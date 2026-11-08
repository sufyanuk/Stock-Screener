from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine = None
_Session: sessionmaker | None = None


def _retrying_creator(url: str):
    """psycopg connect with a few short retries: a database waking up, or a burst of serverless cold
    starts briefly throttled by the provider ("too many connection attempts"), should not fail a request."""
    import time

    import psycopg

    conninfo = url.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect():
        delay = 0.4
        for attempt in range(5):
            try:
                return psycopg.connect(conninfo, prepare_threshold=None, connect_timeout=10)
            except psycopg.OperationalError:
                if attempt == 4:
                    raise
                time.sleep(delay)
                delay *= 2
    return connect


def _make(url: str, pooled: bool = True):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    if not pooled:
        from sqlalchemy.pool import NullPool

        return create_engine("postgresql+psycopg://", creator=_retrying_creator(url), poolclass=NullPool)
    # One instance serves many requests at once (long loaders hold a connection for minutes), so the
    # pool must be roomy; Neon's pooled endpoint (PgBouncer) accepts many client connections.
    # prepare_threshold=None keeps psycopg compatible with transaction-mode poolers.
    return create_engine("postgresql+psycopg://", creator=_retrying_creator(url), pool_pre_ping=True,
                         pool_size=10, max_overflow=10, pool_timeout=20, pool_recycle=300)


def engine():
    global _engine, _Session
    if _engine is None:
        _engine = _make(get_settings().sqlalchemy_url)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def direct_engine():
    """Unpooled engine for migrations (session-level advisory locks)."""
    s = get_settings()
    if s.sqlalchemy_url_direct == s.sqlalchemy_url:
        return engine()
    return _make(s.sqlalchemy_url_direct, pooled=False)


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
