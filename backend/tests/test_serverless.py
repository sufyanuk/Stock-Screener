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
