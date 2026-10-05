"""Build data/candidates.csv and data/ground_truth.csv from the specs and CV texts.

Structured fields come from scripts/candidate_specs.py (fixed values, fully
reproducible). CV texts live in data/cv/<candidate_id>.txt.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))

from candidate_specs import specs  # noqa: E402

APPLICATION_FIELDS = [
    "candidate_id", "full_name", "email", "phone", "gender", "date_of_birth",
    "university", "university_tier", "region", "degree", "major", "graduation_year",
    "gpa", "english_test", "english_score", "willing_offshore", "right_to_work_th",
    "application_date", "source_channel", "cv_text",
]
GROUND_TRUTH_FIELDS = ["candidate_id", "archetype", "expected_outcome", "brief"]


def main() -> None:
    rows = specs()
    missing = []
    for r in rows:
        p = ROOT / "data" / "cv" / f"{r['candidate_id']}.txt"
        if not p.exists():
            missing.append(r["candidate_id"])
            continue
        r["cv_text"] = p.read_text(encoding="utf-8").strip()
    if missing:
        sys.exit(f"Missing CV files: {missing}")

    with open(ROOT / "data" / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=APPLICATION_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(ROOT / "data" / "ground_truth.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=GROUND_TRUTH_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} candidates to data/candidates.csv and data/ground_truth.csv")


if __name__ == "__main__":
    main()
