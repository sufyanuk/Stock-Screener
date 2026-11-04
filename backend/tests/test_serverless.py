import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import cron_kind


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_cron_kind_handles_dst_holidays_and_early_close():
    # Winter (EST, UTC-5): 09:30 NY = 14:30 UTC
    assert cron_kind(utc(2026, 1, 5, 13, 30)) == "premarket"
    assert cron_kind(utc(2026, 1, 5, 14, 30)) == "open"
    assert cron_kind(utc(2026, 1, 5, 17, 0)) == "intraday"
    assert cron_kind(utc(2026, 1, 5, 21, 30)) == "close"
    assert cron_kind(utc(2026, 1, 5, 12, 0)) is None  # 07:00 NY
    # Summer (EDT, UTC-4): 09:30 NY = 13:30 UTC
    assert cron_kind(utc(2026, 7, 6, 13, 30)) == "open"
    assert cron_kind(utc(2026, 7, 6, 20, 30)) == "close"
    assert cron_kind(utc(2026, 11, 26, 17, 0)) is None  # Thanksgiving
    assert cron_kind(utc(2026, 11, 27, 18, 30)) == "close"  # 13:30 NY after 13:00 early close
    assert cron_kind(utc(2026, 11, 7, 17, 0)) is None  # Saturday
    # The daily cron (21:15 UTC, may fire anywhere in 21:00-21:59) is a closing scan all year:
    for d in (utc(2026, 1, 5, 21, 0), utc(2026, 1, 5, 21, 59), utc(2026, 7, 6, 21, 0), utc(2026, 7, 6, 21, 59),
              utc(2026, 11, 27, 21, 15)):  # incl. day-after-Thanksgiving early close
        assert cron_kind(d) == "close", d
    assert cron_kind(utc(2026, 7, 7, 0, 30)) is None  # 20:30 NY


def test_database_url_normalisation():
    s = Settings(database_url="postgres://u:p@h/db?sslmode=require")
    assert s.sqlalchemy_url == "postgresql+psycopg://u:p@h/db?sslmode=require"
    s = Settings(database_url="postgresql://pooled/db", database_url_unpooled="postgresql://direct/db")
    assert s.sqlalchemy_url == "postgresql+psycopg://direct/db"
    assert Settings(database_url="sqlite:///x.db").sqlalchemy_url == "sqlite:///x.db"


@pytest.fixture
def client(db, monkeypatch):
    import app.main as main
    from tests.conftest import FakeDataSource

    monkeypatch.setattr(main, "make_datasource", lambda: FakeDataSource())
    return TestClient(main.app)


def test_cron_endpoint_requires_secret(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("CRON_SECRET", "s3cret-value")
    get_settings.cache_clear()
    try:
        assert client.get("/api/cron/scan").status_code == 401
        assert client.get("/api/cron/scan", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = client.get("/api/cron/scan", headers={"Authorization": "Bearer s3cret-value"})
        assert r.status_code == 200  # result depends on the current clock: skipped or a run
    finally:
        get_settings.cache_clear()


def test_insider_ingest_is_incremental_and_capped(db, monkeypatch):
    import asyncio

    from sqlalchemy import func, select

    from app.config import get_settings
    from app.models import InsiderTransaction, ProcessedFiling
    from app.services.engine import ScanEngine
    from tests.conftest import AS_OF, FakeDataSource

    monkeypatch.setenv("INSIDER_FILINGS_PER_RUN", "2")
    get_settings.cache_clear()
    try:
        FakeDataSource.filing_fetches = 0
        eng = ScanEngine(db, FakeDataSource())
        asyncio.run(eng.ingest_insiders([AS_OF]))
        assert FakeDataSource.filing_fetches == 2 and any("not yet fetched" in e for e in eng.errors)
        asyncio.run(ScanEngine(db, FakeDataSource()).ingest_insiders([AS_OF]))
        assert FakeDataSource.filing_fetches == 3  # only the remaining filing was fetched
        asyncio.run(ScanEngine(db, FakeDataSource()).ingest_insiders([AS_OF]))
        assert FakeDataSource.filing_fetches == 3  # nothing re-downloaded
        assert db.scalar(select(func.count()).select_from(ProcessedFiling)) == 3
        assert db.scalar(select(func.count()).select_from(InsiderTransaction)) == 4  # joint filing has 2 owners
    finally:
        get_settings.cache_clear()


def test_options_backfill_capped(db, monkeypatch):
    import asyncio

    from sqlalchemy import func, select

    from app import market_calendar as mc
    from app.config import get_settings
    from app.models import OptionsDaily
    from app.services.engine import ScanEngine
    from tests.conftest import AS_OF, FakeDataSource

    monkeypatch.setenv("BACKFILL_SESSIONS_PER_RUN", "10")
    get_settings.cache_clear()
    try:
        sessions = mc.previous_sessions(AS_OF, 25)
        asyncio.run(ScanEngine(db, FakeDataSource()).ingest_options(sessions))
        stored = db.scalars(select(OptionsDaily.session).distinct()).all()
        assert len(stored) == 10 and max(stored) == AS_OF  # newest first
        asyncio.run(ScanEngine(db, FakeDataSource()).ingest_options(sessions))
        assert db.scalar(select(func.count(func.distinct(OptionsDaily.session)))) == 19  # +9 older + today refresh
    finally:
        get_settings.cache_clear()


def test_register_without_database_gives_clear_503(tmp_path):
    import app.main as main
    from app import db as dbmod

    # Point at an unreachable PostgreSQL server (nothing listens on port 1).
    dbmod.reset_engine_for_tests("postgresql+psycopg://u:p@127.0.0.1:1/none?connect_timeout=2")
    try:
        r = TestClient(main.app).post("/api/auth/register",
                                      json={"email": "a@example.com", "password": "correct horse battery"})
        assert r.status_code == 503 and "database is not connected" in r.json()["detail"]
    finally:
        dbmod.reset_engine_for_tests(f"sqlite:///{tmp_path}/x.db")


def test_health_reports_db_ok(client):
    r = client.get("/api/health").json()
    assert r["db"] == "ok" and r["migration_error"] is None


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"), reason="needs PostgreSQL")
def test_startup_migrations_on_postgres(monkeypatch):
    from sqlalchemy import inspect, text

    from app import db as dbmod
    from app.migrate import upgrade_head

    dbmod.reset_engine_for_tests(os.environ["TEST_DATABASE_URL"])
    with dbmod.engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    upgrade_head()
    upgrade_head()  # idempotent
    assert "recommendations" in inspect(dbmod.engine()).get_table_names()
