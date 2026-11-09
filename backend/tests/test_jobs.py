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
