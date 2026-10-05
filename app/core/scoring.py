"""Deterministic scoring, evidence verification and shortlist banding. No AI."""
import re

from rapidfuzz import fuzz

QUOTE_MATCH_THRESHOLD = 90

FLAG_LABELS = {
    "unverified_quote": "A quoted piece of evidence was not found in the CV",
    "unsupported_level": "A level above 0 was given without a supporting quote",
    "missing_criterion": "The AI did not return an assessment for every criterion",
    "suspicious_content": "The CV contains text aimed at manipulating the screening",
    "pii_leak_blocked": "Redaction check failed; the CV was not sent to the AI",
    "ai_unavailable": "No AI assessment was available; a human must read this CV",
}


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_found(quote: str, source: str) -> bool:
    q, src = _norm(quote).strip(" .\"'…"), _norm(source)
    if not q:
        return False
    if q in src:
        return True
    return fuzz.partial_ratio(q, src) >= QUOTE_MATCH_THRESHOLD


def enrich_assessments(assessments: list[dict], criteria: list[dict], redacted_cv: str) -> tuple[list[dict], list[str]]:
    """Align AI assessments to the approved criteria, verify quotes, compute points.

    Returns (enriched assessments in criteria order, flags).
    """
    by_id = {a["criterion_id"]: a for a in assessments}
    flags: set[str] = set()
    out = []
    for c in criteria:
        a = by_id.get(c["id"])
        if a is None:
            flags.add("missing_criterion")
            a = {"criterion_id": c["id"], "level": 0, "evidence_quotes": [], "rationale": "No assessment returned.", "gaps": []}
        level = max(0, min(3, int(a.get("level", 0))))
        quotes = [q for q in a.get("evidence_quotes", []) if q and q.strip()]
        verified = [quote_found(q, redacted_cv) for q in quotes]
        if quotes and not all(verified):
            flags.add("unverified_quote")
        if level > 0 and not quotes:
            flags.add("unsupported_level")
        points = round(c["weight"] * level / 3, 2)
        out.append({
            **a,
            "criterion_id": c["id"],
            "criterion_name": c["name"],
            "weight": c["weight"],
            "level": level,
            "evidence_quotes": quotes,
            "quotes_verified": verified,
            "points": points,
        })
    return out, sorted(flags)


def rescore(assessments: list[dict], criteria: list[dict]) -> tuple[list[dict], float]:
    """Apply (possibly changed) weights to existing levels. Used when the HM re-weights."""
    weights = {c["id"]: c["weight"] for c in criteria}
    out, total = [], 0.0
    for a in assessments:
        w = weights.get(a["criterion_id"], 0)
        pts = round(w * a["level"] / 3, 2)
        out.append({**a, "weight": w, "points": pts})
        total += pts
    return out, round(total, 1)


def total_score(enriched: list[dict]) -> float:
    return round(sum(a["points"] for a in enriched), 1)


REVIEW_FLAGS = set(FLAG_LABELS)


def assign_buckets(rows: list[dict], shortlist_size: int, band: float) -> dict[str, tuple[str, int]]:
    """rows: [{candidate_id, eligible, score, flags}] -> {candidate_id: (bucket, rank)}.

    - Ineligible: failed a knock-out.
    - Proposed: top N by score, with no review flags.
    - Needs Review: any review flag, or outside the top N but within `band` points of the cut-off.
    - Not Proposed: everyone else.
    """
    eligible = sorted((r for r in rows if r["eligible"]), key=lambda r: (-r["score"], r["candidate_id"]))
    result: dict[str, tuple[str, int]] = {}
    cutoff = eligible[min(shortlist_size, len(eligible)) - 1]["score"] if eligible else 0.0
    for rank, r in enumerate(eligible, start=1):
        flagged = bool(REVIEW_FLAGS & set(r["flags"]))
        if flagged:
            bucket = "Needs Review"
        elif rank <= shortlist_size:
            bucket = "Proposed"
        elif r["score"] >= cutoff - band:
            bucket = "Needs Review"
        else:
            bucket = "Not Proposed"
        result[r["candidate_id"]] = (bucket, rank)
    for r in rows:
        if not r["eligible"]:
            result[r["candidate_id"]] = ("Ineligible", 0)
    return result
