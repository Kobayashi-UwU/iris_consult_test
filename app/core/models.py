"""ORM models. The database is the single source of truth for workflow state."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    jd_text: Mapped[str] = mapped_column(Text)
    seats: Mapped[int] = mapped_column(Integer)


class SuccessProfileRow(Base):
    __tablename__ = "success_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # approved | superseded
    profile_json: Mapped[dict] = mapped_column(JSON)
    criteria_hash: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Candidate(Base):
    __tablename__ = "candidates"
    candidate_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32))
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32))
    date_of_birth: Mapped[str] = mapped_column(String(10))
    university: Mapped[str] = mapped_column(String(160))
    degree: Mapped[str] = mapped_column(String(16))
    major: Mapped[str] = mapped_column(String(80))
    graduation_year: Mapped[int] = mapped_column(Integer)
    gpa: Mapped[float] = mapped_column(Float)
    english_test: Mapped[str] = mapped_column(String(16))
    english_score: Mapped[str] = mapped_column(String(16))
    willing_offshore: Mapped[str] = mapped_column(String(8))
    right_to_work_th: Mapped[str] = mapped_column(String(8))
    application_date: Mapped[str] = mapped_column(String(10))
    source_channel: Mapped[str] = mapped_column(String(40))
    cv_text: Mapped[str] = mapped_column(Text)


class CandidateDemographic(Base):
    """Kept apart from `candidates`: read only by the fairness monitor, never by the AI pipeline."""
    __tablename__ = "candidate_demographics"
    candidate_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    gender: Mapped[str] = mapped_column(String(16))
    region: Mapped[str] = mapped_column(String(40))
    university_tier: Mapped[str] = mapped_column(String(16))


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(16), index=True)
    profile_version: Mapped[int] = mapped_column(Integer)
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default="")  # live | cache | rules
    knockout_pass: Mapped[bool] = mapped_column(Boolean)
    knockout_reasons: Mapped[list] = mapped_column(JSON, default=list)
    assessments: Mapped[list] = mapped_column(JSON, default=list)  # enriched CriterionAssessment dicts
    summary: Mapped[str] = mapped_column(Text, default="")
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    bucket: Mapped[str] = mapped_column(String(16), default="")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(16), index=True)
    system_bucket: Mapped[str] = mapped_column(String(16))
    human_decision: Mapped[str] = mapped_column(String(16))
    is_override: Mapped[bool] = mapped_column(Boolean)
    reason_code: Mapped[str] = mapped_column(String(64), default="")
    reason_text: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CandidateStatus(Base):
    __tablename__ = "candidate_status"
    candidate_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(24))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Communication(Base):
    __tablename__ = "communications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # interview_kit | invite | regret
    draft_json: Mapped[dict] = mapped_column(JSON)
    edited_text: Mapped[str] = mapped_column(Text, default="")
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    """Append-only: the app never updates or deletes rows (except a full demo reset)."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    actor_type: Mapped[str] = mapped_column(String(8))  # ai | system | human
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(48))
    candidate_id: Mapped[str] = mapped_column(String(16), default="")
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    profile_version: Mapped[int] = mapped_column(Integer, default=0)
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    input_hash: Mapped[str] = mapped_column(String(64), default="")


class AppState(Base):
    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
