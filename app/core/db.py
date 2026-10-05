from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from core.config import database_url
from core.models import AppState, Base


@lru_cache(maxsize=1)
def engine():
    url = database_url()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def _sessionmaker():
    return sessionmaker(bind=engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Session:
    s = _sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db() -> None:
    Base.metadata.create_all(engine())


def drop_all() -> None:
    Base.metadata.drop_all(engine())


def get_state(s: Session, key: str, default=None):
    row = s.get(AppState, key)
    return row.value if row else default


def set_state(s: Session, key: str, value) -> None:
    row = s.get(AppState, key)
    if row:
        row.value = value
    else:
        s.add(AppState(key=key, value=value))


def clear_state(s: Session, key: str) -> None:
    row = s.get(AppState, key)
    if row:
        s.delete(row)


def all_state(s: Session) -> dict:
    return {r.key: r.value for r in s.scalars(select(AppState))}
