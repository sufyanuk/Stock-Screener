"""Ratings tab endpoints and the once-a-day automatic scan."""
from app.providers import extra
from tests.conftest import FakeDataSource
from tests.test_research import _auth, client  # noqa: F401  (fixture)
from tests.test_engine_api import run_scan


class SS:
    name = "sentisense"
    screener_calls = 0
    fail = False

    async def screener(self, limit=500):
        SS.screener_calls += 1
        if SS.fail:
            from app.providers.http import ProviderError
            raise ProviderError("sentisense", "screener HTTP 403: PRO only", 403)
        return extra.parse_sentisense_screener({"data": [
            {"ticker": "GOOD", "companyName": "Good Widgets", "ratingLetter": "A", "ratingScore": 91.2, "ratingPercentile": 99.0},
            {"ticker": "BANK", "ratingLetter": "C", "ratingScore": 45.0, "ratingPercentile": 50.0}]})

    async def rating(self, t):
        return {"ticker": t, "rated": t != "PCR", "letter": "B", "score": 75.0, "percentile": 80.0, "as_of": "2026-11-04",
                "reason": None if t != "PCR" else "insufficient_dimensions"}


class DS(FakeDataSource):
    sentisense = SS()


def test_sentisense_screener_and_fallback(client, db, monkeypatch):  # noqa: F811
    import app.main as main

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    h = _auth(client)
    SS.fail, SS.screener_calls = False, 0
    r = client.post("/api/ratings/sentisense/load", headers=h).json()
    assert r["mode"] == "screener" and r["fetched"] == 2
    rows = client.get("/api/ratings/sentisense", headers=h).json()["rows"]
    assert [x["symbol"] for x in rows] == ["GOOD", "BANK"] and rows[0]["letter"] == "A" and rows[0]["name"] == "Good Widgets"
    assert client.post("/api/ratings/sentisense/load", headers=h).json()["fetched"] == 0  # cached for the day
    assert SS.screener_calls == 1


def test_sentisense_per_stock_when_screener_unavailable(client, db, monkeypatch):  # noqa: F811
    import app.main as main

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    h = _auth(client)
    run_scan(db)  # insider purchases + earnings -> universe
    SS.fail = True
    r = client.post("/api/ratings/sentisense/load", headers=h).json()
    assert r["mode"] == "per-stock" and r["fetched"] >= 2 and "PRO only" in r["errors"][0]
    rows = {x["symbol"]: x for x in client.get("/api/ratings/sentisense", headers=h).json()["rows"]}
    assert rows["GOOD"]["letter"] == "B" and rows["PCR"]["rated"] is False
    SS.fail = False


def test_analyst_ratings_tab(client, db):  # noqa: F811
    h = _auth(client)
    run_scan(db)
    before = client.get("/api/ratings/analysts", headers=h).json()
    assert before["missing"] >= 1
    client.post("/api/ratings/analysts/load", headers=h)
    r = client.get("/api/ratings/analysts", headers=h).json()
    assert r["missing"] == 0 and r["rows"] and all(x["strong_buy"] >= 1 for x in r["rows"])
    x = r["rows"][0]
    assert x["strong_buy"] == 3 and x["total"] == 10 and x["buy_pct"] == 80.0
    assert client.get("/api/ratings/analysts?min_strong_buy=4", headers=h).json()["rows"] == []


def test_daily_scan_runs_once_per_day(client, db):  # noqa: F811
    h = _auth(client)
    first = client.post("/api/scan/daily", headers=h).json()
    assert first["ran"] is True
    second = client.post("/api/scan/daily", headers=h).json()
    assert second["ran"] is False and second["reason"] == "already scanned today"
