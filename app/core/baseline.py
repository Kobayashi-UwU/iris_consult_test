"""A stand-in for today's manual screen: GPA cut-off, then rank by JD keyword hits.

Used only to compare against the evidence-based workflow. Assumption stated in the
slides: many graduate screens shortcut to GPA + keyword matching under volume pressure.
"""
import re

KEYWORDS = [
    "process", "safety", "hse", "hazop", "aspen", "hysys", "python", "simulation",
    "carbon capture", "ccs", "hydrogen", "renewable", "solar", "sustainability", "net zero",
    "offshore", "reservoir", "petroleum", "refinery", "petrochemical", "digital", "data",
    "machine learning", "ai", "leadership", "teamwork", "communication", "energy transition",
]
BASELINE_GPA_MIN = 3.0


def keyword_hits(cv_text: str) -> int:
    low = cv_text.lower()
    return sum(len(re.findall(rf"(?<!\w){re.escape(k)}(?!\w)", low)) for k in KEYWORDS)


def baseline_select(candidates: list[dict], eligible_ids: set[str], n: int) -> dict[str, dict]:
    """Return {candidate_id: {hits, passes_gpa, baseline_rank, baseline_selected}}."""
    out = {}
    for c in candidates:
        out[c["candidate_id"]] = {
            "keyword_hits": keyword_hits(c["cv_text"]),
            "passes_gpa": c["gpa"] >= BASELINE_GPA_MIN,
            "baseline_rank": None,
            "baseline_selected": False,
        }
    pool = [c for c in candidates if c["candidate_id"] in eligible_ids and out[c["candidate_id"]]["passes_gpa"]]
    pool.sort(key=lambda c: (-out[c["candidate_id"]]["keyword_hits"], -c["gpa"], c["candidate_id"]))
    for rank, c in enumerate(pool, start=1):
        out[c["candidate_id"]]["baseline_rank"] = rank
        out[c["candidate_id"]]["baseline_selected"] = rank <= n
    return out
