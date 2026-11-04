from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine = None
_Session: sessionmaker | None = None


def engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().sqlalchemy_url
        if url.startswith("sqlite"):
            kw: dict = {"connect_args": {"check_same_thread": False}}
        else:
            # Small pool for serverless instances; prepare_threshold=None keeps psycopg compatible with
            # transaction-mode poolers (e.g. Neon's pooled endpoint).
            kw = {"pool_pre_ping": True, "pool_size": 3, "max_overflow": 2, "pool_recycle": 300,
                  "connect_args": {"prepare_threshold": None}}
        _engine = create_engine(url, **kw)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def SessionLocal() -> Session:
    engine()
    assert _Session is not None
    return _Session()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def reset_engine_for_tests(url: str) -> None:
    global _engine, _Session
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    _engine = create_engine(url, **kw)
    _Session = sessionmaker(_engine, expire_on_commit=False)
