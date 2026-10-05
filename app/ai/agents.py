"""The four AI agents. Each is a thin, typed wrapper around one versioned prompt."""
import json
import re

from ai.client import LLMClient, LLMResult
from core.schemas import CandidateEvaluation, EmailDraft, InterviewKit, SuccessProfile


def criteria_core(profile: dict) -> list[dict]:
    """What the evidence extractor sees: definitions and anchors, never weights."""
    return [{"id": c["id"], "name": c["name"], "definition": c["definition"], "anchors": c["anchors"]}
            for c in profile["criteria"]]


def normalize_profile(p: dict) -> dict:
    """Make an AI-drafted profile safe to edit: unique ids, integer weights summing to 100."""
    seen = set()
    for c in p["criteria"]:
        cid = re.sub(r"[^a-z0-9_]", "_", c["id"].lower()).strip("_") or "criterion"
        while cid in seen:
            cid += "_2"
        seen.add(cid)
        c["id"] = cid
        c["weight"] = max(0, int(c["weight"]))
    total = sum(c["weight"] for c in p["criteria"])
    if total != 100 and p["criteria"]:
        if total == 0:
            for c in p["criteria"]:
                c["weight"] = 100 // len(p["criteria"])
        else:
            for c in p["criteria"]:
                c["weight"] = round(c["weight"] * 100 / total)
        diff = 100 - sum(c["weight"] for c in p["criteria"])
        max(p["criteria"], key=lambda c: c["weight"])["weight"] += diff
    for k in p["knockouts"]:
        k.setdefault("enabled", True)
    return p


def build_profile(client: LLMClient, jd_text: str) -> tuple[dict, LLMResult]:
    res = client.run("profile_builder.v1", {"jd": jd_text}, SuccessProfile, (jd_text.strip(),))
    return normalize_profile(json.loads(json.dumps(res.output))), res


def extract_evidence(client: LLMClient, redacted_cv: str, profile: dict) -> tuple[dict, LLMResult]:
    core = criteria_core(profile)
    res = client.run(
        "evidence_extractor.v1",
        {"criteria": json.dumps(core, indent=1), "cv": redacted_cv},
        CandidateEvaluation,
        (core, redacted_cv),
    )
    return res.output, res


def interview_kit(client: LLMClient, redacted_cv: str, profile: dict, assessments: list[dict]) -> tuple[dict, LLMResult]:
    crit = [{"id": c["id"], "name": c["name"], "weight": c["weight"]} for c in profile["criteria"]]
    slim = [{"criterion_id": a["criterion_id"], "level": a["level"], "rationale": a["rationale"], "gaps": a.get("gaps", [])}
            for a in assessments]
    res = client.run(
        "interview_kit.v1",
        {"criteria": json.dumps(crit, indent=1), "assessment": json.dumps(slim, indent=1), "cv": redacted_cv},
        InterviewKit,
        (criteria_core(profile), redacted_cv),
    )
    return res.output, res


def strengths_for_email(assessments: list[dict]) -> list[str]:
    # Ordered by level then criterion order (not weight), so re-weighting doesn't change the email input.
    ranked = sorted(enumerate(assessments), key=lambda ia: (-ia[1]["level"], ia[0]))
    top = [a for _, a in ranked[:2]]
    return [f"{a['criterion_name']}: {a['rationale']}" for a in top if a["level"] >= 2]


def invite_email(client: LLMClient, strengths: list[str]) -> tuple[dict, LLMResult]:
    text = "\n".join(f"- {s}" for s in strengths) or "- A strong overall application."
    res = client.run("invite_email.v1", {"strengths": text}, EmailDraft, (text,))
    return res.output, res
