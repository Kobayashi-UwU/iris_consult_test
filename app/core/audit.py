from sqlalchemy.orm import Session

from core.models import AuditLog


def log(
    s: Session,
    *,
    actor_type: str,
    actor: str,
    action: str,
    candidate_id: str = "",
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
    profile_version: int = 0,
    prompt_version: str = "",
    input_hash: str = "",
) -> None:
    s.add(AuditLog(
        actor_type=actor_type,
        actor=actor,
        action=action,
        candidate_id=candidate_id,
        before=before or {},
        after=after or {},
        reason=reason,
        profile_version=profile_version,
        prompt_version=prompt_version,
        input_hash=input_hash,
    ))
