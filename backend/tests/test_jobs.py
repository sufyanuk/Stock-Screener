"""Heavy data jobs run on GitHub Actions: Vercel only serves stored data and starts jobs on request."""
import asyncio

import httpx
import respx

from tests.test_research import _auth, client  # noqa: F401  (pytest fixture)
from tests.test_shariah_engine import TODAY, FakeSec

DISPATCH = "https://api.github.com/repos/sufyanuk/Stock-Screener/actions/workflows/data-jobs.yml/dispatches"


def test_vercel_does_not_run_heavy_loads(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("VERCEL", "1")
    h = _auth(client)
    for path, body in [("/api/shariah/load", {}), ("/api/ratings/analysts/load", None),
                       ("/api/ratings/sentisense/load", None), ("/api/earnings/load-analysts", None),
                       ("/api/insiders/load-shares", None)]:
        r = client.post(path, headers=h, json=body)
        assert r.status_code == 200 and r.json()["skipped"] is True, path
    assert client.post("/api/scan/daily", headers=h, json={}).json()["ran"] is False


@respx.mock
def test_scan_request_starts_the_github_job(client, monkeypatch):  # noqa: F811
    from app.config import get_settings

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(get_settings(), "github_dispatch_token", "t0ken")
    route = respx.post(DISPATCH).mock(return_value=httpx.Response(204))
    h = _auth(client)
    assert client.post("/api/scan", headers=h).json() == {"queued": True, "via": "github"}
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer t0ken"
    assert b'"job":"scan"' in sent.content.replace(b" ", b"") and b'"ref":"main"' in sent.content.replace(b" ", b"")

    route.mock(return_value=httpx.Response(403, text="Resource not accessible by personal access token"))
    r = client.post("/api/scan", headers=h)
    assert r.status_code == 502 and "403" in r.json()["detail"]


def test_job_runner_finishes_a_whole_load_in_one_run(db, monkeypatch):
    import app.main as main
    from app import jobs
    from app.models import Security, ShariahResultRow
    from app.services import shariah as shs
    from tests.conftest import FakeDataSource

    fake = FakeSec()

    class DS(FakeDataSource):
        sec = fake

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    monkeypatch.setattr(shs, "_today", lambda: TODAY)
    for c, t in fake.names.items():
        db.add(Security(ticker=t, cik=str(c).zfill(10), name=t, exchange="Nasdaq"))
    db.commit()
    assert asyncio.run(jobs.run("shariah")) == 0
    db.expire_all()
    assert db.query(ShariahResultRow).count() == len(fake.names)
    assert jobs.main(["jobs", "nope"]) == 2


def test_sec_data_is_downloaded_only_by_the_weekly_job(db, monkeypatch):
    import app.main as main
    from app import jobs
    from app.models import PriceSnapshot, Security, ShariahResultRow
    from app.services import shariah as shs
    from tests.conftest import FakeDataSource

    fake = FakeSec()
    priced = []

    class Alp:
        async def multi_snapshots(self, symbols):
            priced.extend(symbols)
            return {s: {"price": 50.0} for s in symbols}

    class DS(FakeDataSource):
        sec = fake
        alpaca = Alp()

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    monkeypatch.setattr(shs, "_today", lambda: TODAY)
    for c, t in fake.names.items():
        db.add(Security(ticker=t, cik=str(c).zfill(10), name=t, exchange="Nasdaq"))
    db.commit()
    assert asyncio.run(jobs.run("shariah")) == 0  # new database: the first load reads SEC
    assert fake.calls and db.query(ShariahResultRow).count() == len(fake.names)

    fake.calls.clear()
    priced.clear()
    db.query(PriceSnapshot).delete()
    db.commit()
    assert asyncio.run(jobs.run("nightly")) == 0  # weekday: prices and re-classification only
    assert fake.calls == [] and priced  # no SEC request, prices fetched

    assert asyncio.run(jobs.run("shariah-weekly")) == 0  # Sunday: SEC figures downloaded again
    assert len(fake.calls) >= len(shs.frame_plan(TODAY))


def test_backup_schedules_run_only_when_the_main_run_was_missed(db, monkeypatch):
    import app.main as main
    from app import jobs
    from app.models import ScanRun, Security
    from app.services import shariah as shs
    from tests.conftest import FakeDataSource

    fake = FakeSec()

    class DS(FakeDataSource):
        sec = fake

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    monkeypatch.setattr(shs, "_today", lambda: TODAY)
    for c, t in fake.names.items():
        db.add(Security(ticker=t, cik=str(c).zfill(10), name=t, exchange="Nasdaq"))
    db.commit()
    scans = lambda: db.query(ScanRun).filter(ScanRun.triggered_by == "github-actions").count()  # noqa: E731

    assert asyncio.run(jobs.run("nightly-backup")) == 0  # main run missed: the backup does the work
    db.expire_all()
    assert scans() == 1
    assert asyncio.run(jobs.run("nightly-backup")) == 0  # already ran today: nothing happens
    db.expire_all()
    assert scans() == 1

    assert asyncio.run(jobs.run("shariah-weekly")) == 0
    fake.calls.clear()
    assert asyncio.run(jobs.run("shariah-weekly-backup")) == 0  # weekly refresh already done
    assert fake.calls == []
    assert jobs.main(["jobs", "nightly-backup-x"]) == 2


def test_basket_is_kept_until_the_next_nightly_scan(client, db, monkeypatch):  # noqa: F811
    from datetime import datetime, timedelta, timezone

    import app.main as main
    from app import jobs
    from app.models import BasketItem, ScanRun
    from tests.conftest import FakeDataSource

    monkeypatch.setattr(main, "make_datasource", lambda: FakeDataSource())
    h = _auth(client)
    assert client.post("/api/basket", headers=h, json={"symbol": "aapl"}).json() == {"added": "AAPL", "count": 1}
    client.post("/api/basket", headers=h, json={"symbol": "MSFT"})
    client.post("/api/basket", headers=h, json={"symbol": "AAPL"})  # again: no duplicate
    assert client.post("/api/basket", headers=h, json={"symbol": "no way!"}).status_code == 422
    b = client.get("/api/basket", headers=h).json()
    assert [i["symbol"] for i in b["items"]] == ["AAPL", "MSFT"]
    assert client.delete("/api/basket/MSFT", headers=h).json()["count"] == 1

    # an older nightly scan does not touch items added after it
    db.add(ScanRun(kind="close", status="ok", triggered_by="github-actions", session_date=TODAY, config_snapshot={},
                   started_at=datetime.now(timezone.utc) - timedelta(days=1)))
    db.commit()
    assert [i["symbol"] for i in client.get("/api/basket", headers=h).json()["items"]] == ["AAPL"]

    asyncio.run(jobs.scan("close"))  # the next nightly scan empties the basket
    db.expire_all()
    assert client.get("/api/basket", headers=h).json()["items"] == [] and db.query(BasketItem).count() == 0
    client.post("/api/basket", headers=h, json={"symbol": "NVDA"})  # added after it: kept
    assert [i["symbol"] for i in client.get("/api/basket", headers=h).json()["items"]] == ["NVDA"]
    assert client.delete("/api/basket", headers=h).json() == {"count": 0}
