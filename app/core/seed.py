"""Load the mock dataset into the database (initial start and 'Reset demo')."""
import csv

from sqlalchemy import func, select

from core import audit, config
from core.db import drop_all, init_db, session_scope
from core.models import Candidate, CandidateDemographic, CandidateStatus, Job

DEMOGRAPHIC_FIELDS = ("gender", "region", "university_tier")


def read_candidates_csv() -> list[dict]:
    with open(config.DATA_DIR / "candidates.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["graduation_year"] = int(r["graduation_year"])
        r["gpa"] = float(r["gpa"])
    return rows


def read_ground_truth() -> dict[str, dict]:
    with open(config.DATA_DIR / "ground_truth.csv", encoding="utf-8") as f:
        return {r["candidate_id"]: r for r in csv.DictReader(f)}


def jd_text() -> str:
    return (config.DATA_DIR / "job_description.md").read_text(encoding="utf-8")


def seed() -> None:
    with session_scope() as s:
        s.add(Job(job_id=config.JOB_ID, title=config.JOB_TITLE, jd_text=jd_text(), seats=config.JOB_SEATS))
        for r in read_candidates_csv():
            app_fields = {k: v for k, v in r.items() if k not in DEMOGRAPHIC_FIELDS}
            s.add(Candidate(job_id=config.JOB_ID, **app_fields))
            s.add(CandidateDemographic(candidate_id=r["candidate_id"], **{k: r[k] for k in DEMOGRAPHIC_FIELDS}))
            s.add(CandidateStatus(candidate_id=r["candidate_id"], status="Applied"))
        audit.log(s, actor_type="system", actor="seed", action="demo_seeded",
                  after={"candidates": len(read_candidates_csv())})


def ensure_seeded() -> None:
    init_db()
    with session_scope() as s:
        n = s.scalar(select(func.count()).select_from(Candidate))
    if not n:
        seed()


def reset() -> None:
    drop_all()
    init_db()
    seed()
