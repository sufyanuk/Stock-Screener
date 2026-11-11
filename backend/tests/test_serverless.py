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
    # requests use the pooled endpoint; migrations use the direct one
    assert s.sqlalchemy_url == "postgresql+psycopg://pooled/db"
    assert s.sqlalchemy_url_direct == "postgresql+psycopg://direct/db"
    assert Settings(database_url="postgresql://only/db").sqlalchemy_url_direct == "postgresql+psycopg://only/db"
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


def test_single_user_mode_needs_no_login(db, monkeypatch):
    import app.main as main
    from app.config import get_settings
    from app.models import JournalFill, User
    from tests.conftest import FakeDataSource

    monkeypatch.setattr(main, "make_datasource", lambda: FakeDataSource())
    monkeypatch.setenv("SINGLE_USER_MODE", "true")
    get_settings.cache_clear()
    try:
        c = TestClient(main.app)
        me = c.get("/api/me").json()  # no token, no cookie
        assert me["role"] == "admin" and me["email"] == "owner@local"
        assert c.get("/api/dashboard").status_code == 200
        body = {"ticker": "X", "side": "buy", "quantity": 1, "price": 1, "trade_date": "2026-10-01"}
        assert c.post("/api/journal/fills", json=body).status_code == 403  # cross-site write blocked
        assert c.post("/api/journal/fills", json=body, headers={"X-Requested-With": "stockintel"}).status_code == 200
        assert c.put("/api/admin/settings", json={"config": {}, "note": "ok here"},
                     headers={"X-Requested-With": "stockintel"}).status_code == 200  # owner is admin
        assert db.query(User).count() == 1 and db.query(JournalFill).count() == 1
    finally:
        get_settings.cache_clear()


def test_single_user_mode_reuses_existing_admin(db, monkeypatch):
    import app.main as main
    from app.auth import hash_password
    from app.config import get_settings
    from app.models import User

    db.add(User(email="me@example.com", password_hash=hash_password("correct horse battery"), role="admin"))
    db.commit()
    monkeypatch.setenv("SINGLE_USER_MODE", "true")
    get_settings.cache_clear()
    try:
        assert TestClient(main.app).get("/api/me").json()["email"] == "me@example.com"
    finally:
        get_settings.cache_clear()


def test_owner_user_survives_concurrent_creation(db):
    """Two first requests racing to create the single-user owner must not fail."""
    from app.auth import owner_user
    from app.db import SessionLocal
    from app.models import User

    other = SessionLocal()
    other.add(User(email="owner@local", password_hash="!", role="admin"))
    other.commit()
    other.close()
    import app.auth as auth

    real = auth.select
    calls = {"n": 0}

    def first_misses(*a, **k):  # the first lookup runs "before" the other request committed
        calls["n"] += 1
        q = real(*a, **k)
        return q.where(False) if calls["n"] == 1 else q

    auth.select = first_misses
    try:
        u = owner_user(db)
    finally:
        auth.select = real
    assert u.email == "owner@local" and db.query(User).count() == 1


def test_sec_copy_replaces_an_openinsider_copy_of_the_same_filing(db):
    import asyncio

    from sqlalchemy import func, select

    from app.models import InsiderTransaction
    from app.services.engine import ScanEngine
    from tests.conftest import AS_OF, FakeDataSource

    # an OpenInsider-style row for filing 0001-26-1 (aggregated, hashed owner id) from before the switch
    db.add(InsiderTransaction(accession="0001-26-1", txn_index=0, owner_cik="abcdef0123", issuer_cik="GOOD",
                              ticker="GOOD", owner_name="Jane Doe", officer_title="CEO", is_director=False,
                              is_officer=True, is_ten_pct_owner=False, transaction_date=AS_OF, filing_date=AS_OF,
                              code="P", acquired_disposed="A", shares=100, price=10.0, shares_owned_after=1000,
                              direct_indirect="D", security_title="Common", is_amendment=False, is_derivative=False,
                              footnotes=None))
    db.commit()
    asyncio.run(ScanEngine(db, FakeDataSource()).ingest_insiders([AS_OF]))
    rows = db.scalars(select(InsiderTransaction).where(InsiderTransaction.accession == "0001-26-1")).all()
    assert {r.owner_cik for r in rows} == {"9", "10"}  # only the SEC copy (2 joint owners) remains
    assert db.scalar(select(func.count()).select_from(InsiderTransaction)) == 4


def test_filing_listed_under_two_dates_is_recorded_once(db):
    import asyncio
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.models import ProcessedFiling
    from app.services.engine import ScanEngine
    from tests.conftest import AS_OF, FakeDataSource

    db.add(ProcessedFiling(accession="0001-26-1", filed=AS_OF - timedelta(days=1)))  # read before, other date
    db.commit()
    asyncio.run(ScanEngine(db, FakeDataSource()).ingest_insiders([AS_OF]))  # must not hit the unique key
    assert db.scalar(select(func.count()).select_from(ProcessedFiling)) == 3


def test_lots_of_one_filing_are_summed_before_the_one_percent_test(db):
    from datetime import date, timedelta

    from app.main import latest_insider_buys
    from app.models import InsiderTransaction, SharesOutstanding

    today = date.today()
    base = dict(issuer_cik="0000000042", ticker="LOTS", officer_title="CEO", is_director=False, is_officer=True,
                is_ten_pct_owner=False, transaction_date=today - timedelta(days=2), filing_date=today - timedelta(days=1),
                code="P", acquired_disposed="A", direct_indirect="D", security_title="Common", is_amendment=False,
                is_derivative=False, footnotes=None)
    # 1.5% of 1,000,000 shares bought in 3 lots of 5,000, reported jointly by the CEO and her trust
    for owner, cik in (("Jane CEO", "0000000100"), ("Jane Trust", "0000000101")):
        for i, px in enumerate((10.0, 11.0, 12.0)):
            db.add(InsiderTransaction(accession="0009-26-9", txn_index=i, owner_cik=cik, owner_name=owner,
                                      shares=5000, price=px, shares_owned_after=100000 + 5000 * (i + 1), **base))
    # an older purchase filed 60 days ago (outside a 100-row window in practice, inside the 90-day window)
    db.add(InsiderTransaction(accession="0008-26-8", txn_index=0, owner_cik="0000000102", owner_name="Old Buyer",
                              shares=20000, price=5.0, shares_owned_after=50000,
                              **(base | {"filing_date": today - timedelta(days=60), "transaction_date": today - timedelta(days=61)})))
    db.add(SharesOutstanding(ticker="LOTS", shares=1_000_000, source="test"))
    db.commit()
    rows = latest_insider_buys(db, 90, 100, hide_non_compliant=False)
    lots = next(r for r in rows if r["accession"] == "0009-26-9")
    assert lots["shares"] == 15000 and round(lots["price"], 2) == 11.0 and lots["value"] == 165000
    assert lots["co_reporters"] == ["Jane Trust"] and lots["owned_after"] == 115000
    assert round(lots["pct_outstanding"], 2) == 1.5  # passes "> 1%"; each lot alone (0.5%) would not
    assert any(r["accession"] == "0008-26-8" and r["pct_outstanding"] == 2.0 for r in rows)


def test_otc_purchases_are_left_out(db, monkeypatch):
    from datetime import date, timedelta

    import app.main as m
    from app.models import InsiderTransaction

    today = date.today()
    for acc, tk in (("0101-26-1", "LIST"), ("0102-26-1", "OTCX")):
        db.add(InsiderTransaction(accession=acc, txn_index=0, owner_cik="0000000200", owner_name="Buyer", issuer_cik=tk,
                                  ticker=tk, officer_title="CEO", is_director=False, is_officer=True,
                                  is_ten_pct_owner=False, transaction_date=today, filing_date=today, code="P",
                                  acquired_disposed="A", shares=100, price=1.0, shares_owned_after=100,
                                  direct_indirect="D", security_title="Common", is_amendment=False,
                                  is_derivative=False, footnotes=None))
    db.commit()
    monkeypatch.setattr(m, "listed_tickers", lambda db: {"LIST"})  # OTCX is not on a US exchange
    stats: dict = {}
    rows = m.latest_insider_buys(db, 90, 100, hide_non_compliant=False, stats=stats)
    assert [r["ticker"] for r in rows] == ["LIST"] and stats["hidden_otc"] == 1
    monkeypatch.setattr(m, "listed_tickers", lambda db: None)  # exchange list not loaded: nothing filtered
    assert {r["ticker"] for r in m.latest_insider_buys(db, 90, 100, hide_non_compliant=False)} == {"LIST", "OTCX"}


def test_nightly_scan_clears_csvs_uploaded_before_it(db):
    from datetime import date, datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.jobs import clear_options_csv
    from app.models import OptionsImportBatch, OptionsImportRow

    start = datetime(2026, 11, 11, 21, 40, tzinfo=timezone.utc)
    for name, at in (("old.csv", start - timedelta(hours=10)), ("during.csv", start + timedelta(minutes=5))):
        b = OptionsImportBatch(filename=name, session_date=date(2026, 11, 11), row_count=1, percent_interpretation="x",
                               summary={}, imported_by="me", imported_at=at)
        b.rows = [OptionsImportRow(symbol="GOOD", name="Good", price=10.0)]
        db.add(b)
    db.commit()
    assert clear_options_csv(db, start) == 1
    assert [b.filename for b in db.scalars(select(OptionsImportBatch)).all()] == ["during.csv"]
    assert db.scalar(select(func.count()).select_from(OptionsImportRow)) == 1  # the old rows went with it


def test_vercel_never_runs_a_scan_itself(client, db, monkeypatch):
    """On Vercel a scan would use minutes of the monthly CPU allowance: without a GitHub token it is skipped."""
    import app.main as main
    from sqlalchemy import func, select

    from app.models import ScanRun

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(main, "dispatch_job", lambda job: _false())
    app_user = main.app.dependency_overrides
    app_user[main.current_user] = lambda: None
    try:
        r = client.post("/api/scan").json()
        assert r["via"] == "none" and r["status"] == "skipped" and "GITHUB_DISPATCH_TOKEN" in r["reason"]
        assert db.scalar(select(func.count()).select_from(ScanRun)) == 0
        assert client.post("/api/scan/daily").json()["ran"] is False
    finally:
        app_user.clear()


async def _false():
    return False
