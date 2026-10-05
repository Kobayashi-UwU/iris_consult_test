"""Workflow state machine and human-in-the-loop gates.

Every status change goes through `set_status`, which rejects transitions that are
not allowed and writes the audit log. The gates (`can_*`) are what stop the
workflow from continuing without human approval.
"""
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core import audit
from core.db import get_state, set_state
from core.models import CandidateStatus, Decision, Evaluation, SuccessProfileRow

ROLE_HM = "Hiring Manager"
ROLE_RECRUITER = "Recruiter"

SCREEN_BUCKETS = {"Proposed", "Needs Review", "Not Proposed", "Ineligible"}
DECISIONS = {"Approved", "Rejected", "On Hold"}
TRANSITIONS: dict[str, set[str]] = {
    "Applied": SCREEN_BUCKETS,
    "Ineligible": SCREEN_BUCKETS | {"Regret Sent"},
    "Proposed": SCREEN_BUCKETS | DECISIONS,
    "Needs Review": SCREEN_BUCKETS | DECISIONS,
    "Not Proposed": SCREEN_BUCKETS | DECISIONS,
    # Decisions can be revised, or reset by a re-screen, until the shortlist is confirmed (enforced by the gates).
    "Approved": DECISIONS | SCREEN_BUCKETS | {"Invited"},
    "Rejected": DECISIONS | SCREEN_BUCKETS | {"Regret Sent"},
    "On Hold": DECISIONS | SCREEN_BUCKETS,
    "Invited": {"Interview Scheduled"},
    "Interview Scheduled": set(),
    "Regret Sent": set(),
}
STATUS_ORDER = ["Applied", "Ineligible", "Not Proposed", "Needs Review", "Proposed", "On Hold",
                "Rejected", "Regret Sent", "Approved", "Invited", "Interview Scheduled"]

REASON_CODES = [
    "Evidence stronger than the score suggests",
    "Evidence weaker than the score suggests",
    "Unverified or suspicious content",
    "Business need (track or discipline balance)",
    "Accepted system recommendation",
    "Other",
]


class WorkflowError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- status ----------

def statuses(s: Session) -> dict[str, str]:
    return {r.candidate_id: r.status for r in s.scalars(select(CandidateStatus))}


def set_status(s: Session, cid: str, new: str, *, actor_type: str, actor: str, reason: str = "",
               action: str = "status_changed", after_extra: dict | None = None, **audit_kw) -> None:
    row = s.get(CandidateStatus, cid)
    old = row.status if row else "Applied"
    if new != old and new not in TRANSITIONS.get(old, set()):
        raise WorkflowError(f"{cid}: transition {old} → {new} is not allowed")
    if row:
        row.status = new
        row.updated_at = datetime.now(timezone.utc)
    else:
        s.add(CandidateStatus(candidate_id=cid, status=new))
    audit.log(s, actor_type=actor_type, actor=actor, action=action, candidate_id=cid,
              before={"status": old}, after={"status": new, **(after_extra or {})},
              reason=reason, **audit_kw)


# ---------- success profile (HITL #1) ----------

def active_profile(s: Session) -> SuccessProfileRow | None:
    return s.scalars(select(SuccessProfileRow).where(SuccessProfileRow.status == "approved")
                     .order_by(SuccessProfileRow.version.desc())).first()


def validate_profile(profile: dict) -> list[str]:
    errors = []
    crit = profile.get("criteria", [])
    if not 3 <= len(crit) <= 8:
        errors.append("Use between 3 and 8 scored criteria.")
    total = sum(int(c.get("weight", 0)) for c in crit)
    if total != 100:
        errors.append(f"Weights must sum to 100 (currently {total}).")
    for c in crit:
        anchors = c.get("anchors", {})
        if not all(str(anchors.get(f"level_{i}", "")).strip() for i in range(4)):
            errors.append(f"'{c.get('name')}' needs anchors for levels 0–3.")
        if not str(c.get("definition", "")).strip():
            errors.append(f"'{c.get('name')}' needs a definition.")
    if len({c.get("id") for c in crit}) != len(crit):
        errors.append("Criterion ids must be unique.")
    return errors


def approve_profile(s: Session, profile: dict, criteria_hash: str, actor_role: str, source: str) -> SuccessProfileRow:
    if actor_role != ROLE_HM:
        raise WorkflowError("Only the Hiring Manager can approve the success profile.")
    errors = validate_profile(profile)
    if errors:
        raise WorkflowError(" ".join(errors))
    if is_confirmed(s):
        raise WorkflowError("The shortlist is already confirmed. Reset the demo to change the criteria.")
    prev = active_profile(s)
    if prev:
        prev.status = "superseded"
    version = (prev.version + 1) if prev else 1
    row = SuccessProfileRow(job_id=profile.get("job_id", ""), version=version, status="approved",
                            profile_json=profile, criteria_hash=criteria_hash, approved_by=actor_role)
    s.add(row)
    audit.log(s, actor_type="human", actor=actor_role, action="profile_approved",
              before={"version": prev.version if prev else None},
              after={"version": version, "source": source,
                     "weights": {c["id"]: c["weight"] for c in profile["criteria"]},
                     "knockouts": [k["id"] for k in profile["knockouts"] if k.get("enabled", True)]},
              profile_version=version)
    scr = get_state(s, "screening")
    if scr and scr.get("profile_version") != version:
        set_state(s, "screening", {**scr, "stale": True})
    return row


# ---------- gates ----------

def is_confirmed(s: Session) -> bool:
    return bool(get_state(s, "shortlist_confirmed"))


def can_screen(s: Session) -> tuple[bool, str]:
    if not active_profile(s):
        return False, "Waiting for the Hiring Manager to approve the success profile (Step 1)."
    if is_confirmed(s):
        return False, "The shortlist is confirmed; screening is locked. Reset the demo to run again."
    return True, ""


def can_review(s: Session) -> tuple[bool, str]:
    scr = get_state(s, "screening")
    if not scr:
        return False, "Run screening first (Step 2)."
    if scr.get("stale"):
        return False, "The success profile changed after screening. Re-run screening (Step 2)."
    return True, ""


def can_generate(s: Session) -> tuple[bool, str]:
    if not is_confirmed(s):
        return False, "Waiting for the Recruiter to confirm the shortlist (Step 3)."
    return True, ""


# ---------- screening results ----------

def evaluations(s: Session) -> dict[str, Evaluation]:
    return {e.candidate_id: e for e in s.scalars(select(Evaluation))}


def clear_screening(s: Session) -> None:
    s.execute(delete(Evaluation))
    s.execute(delete(Decision))


# ---------- decisions (HITL #2) ----------

def current_decisions(s: Session) -> dict[str, Decision]:
    out: dict[str, Decision] = {}
    for d in s.scalars(select(Decision).order_by(Decision.decided_at, Decision.id)):
        out[d.candidate_id] = d
    return out


def is_override(bucket: str, decision: str) -> bool:
    return (bucket == "Proposed" and decision == "Rejected") or (bucket == "Not Proposed" and decision == "Approved")


def record_decision(s: Session, cid: str, decision: str, reason_code: str, reason_text: str, actor_role: str) -> Decision:
    if actor_role != ROLE_RECRUITER:
        raise WorkflowError("Only the Recruiter can make shortlist decisions.")
    if is_confirmed(s):
        raise WorkflowError("The shortlist is already confirmed.")
    ok, why = can_review(s)
    if not ok:
        raise WorkflowError(why)
    if decision not in DECISIONS:
        raise WorkflowError(f"Unknown decision {decision}")
    ev = s.scalars(select(Evaluation).where(Evaluation.candidate_id == cid)).first()
    if not ev or ev.bucket == "Ineligible":
        raise WorkflowError("This candidate is not in the review pool.")
    override = is_override(ev.bucket, decision)
    needs_reason = override or ev.bucket == "Needs Review"
    if needs_reason and (not reason_code or len(reason_text.strip()) < 10):
        raise WorkflowError("A reason code and a written reason (at least 10 characters) are required "
                            "for overrides and for every 'Needs Review' decision.")
    d = Decision(candidate_id=cid, system_bucket=ev.bucket, human_decision=decision, is_override=override,
                 reason_code=reason_code, reason_text=reason_text.strip(), decided_by=actor_role)
    s.add(d)
    set_status(s, cid, decision, actor_type="human", actor=actor_role,
               action="override" if override else "decision", reason=reason_text.strip() or reason_code,
               after_extra={"system_bucket": ev.bucket, "reason_code": reason_code, "score": ev.total_score})
    return d


def bulk_approve_proposed(s: Session, actor_role: str) -> int:
    decided = current_decisions(s)
    n = 0
    for cid, ev in evaluations(s).items():
        if ev.bucket == "Proposed" and cid not in decided:
            record_decision(s, cid, "Approved", "Accepted system recommendation", "", actor_role)
            n += 1
    return n


def confirm_blockers(s: Session) -> list[str]:
    decided = current_decisions(s)
    evs = evaluations(s)
    blockers = []
    pending_review = [cid for cid, ev in evs.items() if ev.bucket == "Needs Review" and cid not in decided]
    if pending_review:
        blockers.append(f"{len(pending_review)} 'Needs Review' candidate(s) still need a decision: {', '.join(sorted(pending_review))}")
    pending_prop = [cid for cid, ev in evs.items() if ev.bucket == "Proposed" and cid not in decided]
    if pending_prop:
        blockers.append(f"{len(pending_prop)} 'Proposed' candidate(s) still need a decision (use 'Approve all Proposed').")
    if not any(d.human_decision == "Approved" for d in decided.values()):
        blockers.append("Approve at least one candidate for interview.")
    return blockers


def confirm_shortlist(s: Session, actor_role: str) -> dict:
    if actor_role != ROLE_RECRUITER:
        raise WorkflowError("Only the Recruiter can confirm the shortlist.")
    ok, why = can_review(s)
    if not ok:
        raise WorkflowError(why)
    blockers = confirm_blockers(s)
    if blockers:
        raise WorkflowError(" ".join(blockers))
    decided = current_decisions(s)
    auto = 0
    for cid, ev in evaluations(s).items():
        if ev.bucket == "Not Proposed" and cid not in decided:
            record_decision(s, cid, "Rejected", "Accepted system recommendation", "", actor_role)
            auto += 1
    decided = current_decisions(s)
    summary = {
        "at": _now(), "by": actor_role,
        "approved": sum(d.human_decision == "Approved" for d in decided.values()),
        "rejected": sum(d.human_decision == "Rejected" for d in decided.values()),
        "on_hold": sum(d.human_decision == "On Hold" for d in decided.values()),
        "overrides": sum(d.is_override for d in decided.values()),
        "auto_rejected_not_proposed": auto,
    }
    set_state(s, "shortlist_confirmed", summary)
    audit.log(s, actor_type="human", actor=actor_role, action="shortlist_confirmed", after=summary,
              profile_version=(active_profile(s).version if active_profile(s) else 0))
    return summary
