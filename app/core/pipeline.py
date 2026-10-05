"""Step 2 (screening) and Step 4 (interview kit and outreach) orchestration."""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai import agents
from ai.client import LLMClient, LLMUnavailable
from core import audit, config, knockout
from core.db import get_state, set_state
from core.guard import detect_injection
from core.models import Candidate, Communication, Evaluation
from core.redaction import pii_leaks, redact
from core.scoring import assign_buckets, enrich_assessments, total_score
from core.templates import fill_placeholders, regret_email
from core.workflow import (ROLE_RECRUITER, WorkflowError, active_profile, can_generate, can_screen,
                           clear_screening, set_status, statuses)

PIPELINE_ACTOR = "screening-pipeline"


def candidate_dicts(s: Session) -> list[dict]:
    rows = s.scalars(select(Candidate).order_by(Candidate.candidate_id))
    return [{c.name: getattr(r, c.name) for c in Candidate.__table__.columns} for r in rows]


def first_name(cand: dict) -> str:
    return cand["full_name"].split()[0]


# ---------------------------------------------------------------- Step 2

def run_screening(s: Session, client: LLMClient, progress=None) -> dict:
    ok, why = can_screen(s)
    if not ok:
        raise WorkflowError(why)
    t_start = time.time()
    prof_row = active_profile(s)
    profile = prof_row.profile_json
    cands = candidate_dicts(s)
    clear_screening(s)

    results: dict[str, dict] = {}
    to_ai: list[tuple[dict, str]] = []
    for c in cands:
        passed, reasons = knockout.check(c, profile["knockouts"])
        if not passed:
            results[c["candidate_id"]] = {"eligible": False, "knockout_reasons": reasons, "assessments": [],
                                          "summary": "", "score": 0.0, "flags": [], "source": "rules", "llm": None}
            continue
        red, _ = redact(c["cv_text"], c)
        if pii_leaks(red, c):  # fail closed: never send a CV that still contains PII
            results[c["candidate_id"]] = {"eligible": True, "knockout_reasons": [], "assessments": [],
                                          "summary": "Redaction check failed; not sent to AI.", "score": 0.0,
                                          "flags": ["pii_leak_blocked"], "source": "rules", "llm": None}
            continue
        to_ai.append((c, red))

    def work(item):
        c, red = item
        try:
            out, res = agents.extract_evidence(client, red, profile)
            return c, red, out, res, None
        except LLMUnavailable as exc:
            return c, red, None, None, str(exc)

    done = 0
    total = len(to_ai)
    if progress:
        progress(0, total, "Starting")
    workers = config.LLM_CONCURRENCY if client.mode == "live" else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, item) for item in to_ai]
        for fut in as_completed(futures):
            c, red, out, res, err = fut.result()
            cid = c["candidate_id"]
            if err:
                results[cid] = {"eligible": True, "knockout_reasons": [], "assessments": [], "summary": err,
                                "score": 0.0, "flags": ["ai_unavailable"], "source": "none", "llm": None}
            else:
                enriched, flags = enrich_assessments(out["assessments"], profile["criteria"], red)
                rule_hits = detect_injection(c["cv_text"])
                if out.get("suspicious_content") or rule_hits:
                    flags = sorted(set(flags) | {"suspicious_content"})
                results[cid] = {
                    "eligible": True, "knockout_reasons": [], "assessments": enriched,
                    "summary": out.get("summary", ""), "score": total_score(enriched), "flags": flags,
                    "source": res.source, "llm": res,
                    "suspicious_note": out.get("suspicious_content_note", "") or "; ".join(rule_hits),
                    "detected_by": [x for x, hit in (("ai", out.get("suspicious_content")), ("rules", rule_hits)) if hit],
                }
            done += 1
            if progress:
                progress(done, total, cid)

    buckets = assign_buckets(
        [{"candidate_id": cid, "eligible": r["eligible"], "score": r["score"], "flags": r["flags"]}
         for cid, r in results.items()],
        config.SHORTLIST_SIZE, config.BORDERLINE_BAND,
    )

    sources: dict[str, int] = {}
    ai_latency = []
    for cid in sorted(results):
        r = results[cid]
        bucket, rank = buckets[cid]
        res = r["llm"]
        sources[r["source"]] = sources.get(r["source"], 0) + 1
        if res:
            ai_latency.append(res.latency_s)
            audit.log(s, actor_type="ai", actor=f"gemini:{res.model}", action="evidence_extracted", candidate_id=cid,
                      after={"levels": {a["criterion_id"]: a["level"] for a in r["assessments"]}, "source": res.source},
                      profile_version=prof_row.version, prompt_version=res.prompt_version, input_hash=res.input_hash)
        flags = list(r["flags"])
        s.add(Evaluation(
            candidate_id=cid, profile_version=prof_row.version,
            prompt_version=res.prompt_version if res else "", model=res.model if res else "",
            input_hash=res.input_hash if res else "", source=r["source"],
            knockout_pass=r["eligible"], knockout_reasons=r["knockout_reasons"], assessments=r["assessments"],
            summary=r["summary"] + (f"\n\nSuspicious content: {r['suspicious_note']} (detected by: {', '.join(r['detected_by'])})"
                                    if "suspicious_content" in flags else ""),
            total_score=r["score"], flags=flags, bucket=bucket, rank=rank,
        ))
        set_status(s, cid, bucket, actor_type="system", actor=PIPELINE_ACTOR, action="screened",
                   reason="; ".join(r["knockout_reasons"]),
                   after_extra={"score": r["score"], "rank": rank, "flags": flags},
                   profile_version=prof_row.version)

    summary = {
        "profile_version": prof_row.version,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": client.mode,
        "sources": sources,
        "ai_calls": len(ai_latency),
        "avg_ai_seconds_per_cv": round(sum(ai_latency) / len(ai_latency), 2) if ai_latency else 0.0,
        "wall_seconds": round(time.time() - t_start, 1),
        "buckets": {b: sum(1 for v in buckets.values() if v[0] == b) for b in
                    ("Proposed", "Needs Review", "Not Proposed", "Ineligible")},
        "stale": False,
    }
    set_state(s, "screening", summary)
    audit.log(s, actor_type="system", actor=PIPELINE_ACTOR, action="screening_run", after=summary,
              profile_version=prof_row.version)
    return summary


# ---------------------------------------------------------------- Step 4

def _comm(s: Session, cid: str, kind: str) -> Communication | None:
    return s.scalars(select(Communication).where(Communication.candidate_id == cid, Communication.kind == kind)
                     .order_by(Communication.id.desc())).first()


def get_comm(s: Session, cid: str, kind: str) -> Communication | None:
    return _comm(s, cid, kind)


def _eval_and_cand(s: Session, cid: str) -> tuple[Evaluation, dict]:
    ev = s.scalars(select(Evaluation).where(Evaluation.candidate_id == cid)).first()
    cand = next(c for c in candidate_dicts(s) if c["candidate_id"] == cid)
    return ev, cand


def ensure_interview_kit(s: Session, client: LLMClient, cid: str) -> Communication:
    ok, why = can_generate(s)
    if not ok:
        raise WorkflowError(why)
    existing = _comm(s, cid, "interview_kit")
    if existing:
        return existing
    ev, cand = _eval_and_cand(s, cid)
    red, _ = redact(cand["cv_text"], cand)
    profile = active_profile(s).profile_json
    out, res = agents.interview_kit(client, red, profile, ev.assessments)
    row = Communication(candidate_id=cid, kind="interview_kit", draft_json=out, source=res.source)
    s.add(row)
    audit.log(s, actor_type="ai", actor=f"gemini:{res.model}", action="interview_kit_drafted", candidate_id=cid,
              after={"questions": len(out.get("questions", [])), "source": res.source},
              prompt_version=res.prompt_version, input_hash=res.input_hash)
    return row


def ensure_invite(s: Session, client: LLMClient, cid: str) -> Communication:
    ok, why = can_generate(s)
    if not ok:
        raise WorkflowError(why)
    existing = _comm(s, cid, "invite")
    if existing:
        return existing
    ev, cand = _eval_and_cand(s, cid)
    out, res = agents.invite_email(client, agents.strengths_for_email(ev.assessments))
    draft = {"subject": out["subject"], "body": fill_placeholders(out["body"], first_name(cand))}
    row = Communication(candidate_id=cid, kind="invite", draft_json=draft, edited_text=draft["body"], source=res.source)
    s.add(row)
    audit.log(s, actor_type="ai", actor=f"gemini:{res.model}", action="invite_drafted", candidate_id=cid,
              after={"source": res.source}, prompt_version=res.prompt_version, input_hash=res.input_hash)
    return row


def ensure_regret(s: Session, cid: str) -> Communication:
    existing = _comm(s, cid, "regret")
    if existing:
        return existing
    _, cand = _eval_and_cand(s, cid)
    draft = regret_email(first_name(cand))
    row = Communication(candidate_id=cid, kind="regret", draft_json=draft, edited_text=draft["body"], source="template")
    s.add(row)
    return row


def send_invite(s: Session, cid: str, subject: str, body: str, slot: str, actor_role: str) -> None:
    if actor_role != ROLE_RECRUITER:
        raise WorkflowError("Only the Recruiter can send invitations.")
    if "{{interview_slot}}" in body and not slot:
        raise WorkflowError("Choose an interview slot first.")
    row = _comm(s, cid, "invite")
    row.edited_text = fill_placeholders(body, "", slot)
    row.draft_json = {**row.draft_json, "subject_final": subject, "slot": slot}
    row.sent = True
    row.updated_at = datetime.now(timezone.utc)
    set_status(s, cid, "Invited", actor_type="human", actor=actor_role, action="invite_sent",
               after_extra={"slot": slot, "edited": row.edited_text.strip() != fill_placeholders(row.draft_json["body"], "", slot).strip()})


def confirm_interview(s: Session, cid: str, actor_role: str) -> None:
    row = _comm(s, cid, "invite")
    set_status(s, cid, "Interview Scheduled", actor_type="human", actor=actor_role, action="interview_scheduled",
               after_extra={"slot": (row.draft_json or {}).get("slot", "") if row else ""})


def send_regrets(s: Session, actor_role: str) -> int:
    if actor_role != ROLE_RECRUITER:
        raise WorkflowError("Only the Recruiter can send emails.")
    n = 0
    for cid, st in statuses(s).items():
        if st in ("Rejected", "Ineligible"):
            row = ensure_regret(s, cid)
            row.sent = True
            set_status(s, cid, "Regret Sent", actor_type="human", actor=actor_role, action="regret_sent")
            n += 1
    return n


def _slots() -> list[str]:
    """Mock calendar: two weeks of 30-minute video interviews, six per day."""
    from datetime import date, timedelta
    times = ["09:30–10:00", "10:30–11:00", "11:30–12:00", "13:30–14:00", "14:30–15:00", "15:30–16:00"]
    out, d = [], date(2026, 11, 9)
    while len(out) < 60:
        if d.weekday() < 5:
            out += [f"{d.strftime('%a')} {d.day} {d.strftime('%b %Y')}, {t} (video)" for t in times]
        d += timedelta(days=1)
    return out


INTERVIEW_SLOTS = _slots()


def booked_slots(s: Session) -> set[str]:
    rows = s.scalars(select(Communication).where(Communication.kind == "invite", Communication.sent.is_(True)))
    return {r.draft_json.get("slot") for r in rows if r.draft_json.get("slot")}


def send_all_invites(s: Session, client: LLMClient, actor_role: str) -> tuple[int, list[str], list[str]]:
    """Send every pending invitation with its AI draft as written and the next free slot."""
    ok, why = can_generate(s)
    if not ok:
        raise WorkflowError(why)
    sent, failed, no_slot = 0, [], []
    taken = booked_slots(s)
    for cid, st in sorted(statuses(s).items()):
        if st != "Approved":
            continue
        try:
            ensure_interview_kit(s, client, cid)
            inv = ensure_invite(s, client, cid)
        except LLMUnavailable:
            failed.append(cid)
            continue
        slot = next((x for x in INTERVIEW_SLOTS if x not in taken), "")
        if not slot:
            no_slot.append(cid)
            continue
        taken.add(slot)
        send_invite(s, cid, inv.draft_json["subject"], inv.edited_text, slot, actor_role)
        sent += 1
    return sent, failed, no_slot
