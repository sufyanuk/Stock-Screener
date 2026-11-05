from datetime import date, timedelta

import pytest

from app.services import bounces as bn
from tests.test_research import _auth, client  # noqa: F401  (fixture)


def _snap(sym, closes, lows=None, ema=None, low52=None, low52_ago=None, n=260, vol=1e6):
    k = len(closes)
    days = [(date(2026, 10, 30) - timedelta(days=k - 1 - i)).isoformat() for i in range(k)]
    return {"symbol": sym, "n": n, "d": days, "o": closes, "h": [c * 1.01 for c in closes],
            "l": lows or [c * 0.99 for c in closes], "c": closes, "v": [vol] * k, "ema50": ema,
            "low52": low52, "low52_date": days[-1 - low52_ago] if low52_ago is not None else None,
            "low52_ago": low52_ago, "high52": 40.0, "high52_date": "2026-01-02", "avg_vol50": vol}


def _fixtures():
    flat_ema = [20.0 + 0.02 * i for i in range(15)]  # rising
    return [
        # bounced 8% off a 52w low set 4 sessions ago, up today
        _snap("LOWB", [21, 20.5, 20, 19.5, 19, 18.8, 18.6, 18.5, 18.4, 18.3, 18.2, 18.9, 19.3, 19.6, 19.87],
              low52=18.4, low52_ago=4),
        # low set today -> not yet bouncing
        _snap("FALL", [21] * 14 + [18.0], low52=17.9, low52_ago=0),
        # low too long ago
        _snap("OLDLOW", [19.5] * 14 + [20.0], low52=18.0, low52_ago=14),
        # pulled back to a rising 50 EMA 1 session ago and closed up above it
        _snap("EMAB", [22, 21.8, 21.6, 21.4, 21.2, 21.0, 20.9, 20.8, 20.7, 20.6, 20.6, 20.5, 20.45, 20.35, 20.7],
              lows=[21.5, 21.5, 21.3, 21.1, 21.0, 20.8, 20.7, 20.6, 20.6, 20.5, 20.5, 20.45, 20.3, 20.2, 20.4], ema=flat_ema),
        # broke well below the EMA -> not a bounce
        _snap("BROKE", [22] * 10 + [21, 20, 19, 19.5, 20.6], ema=flat_ema),
        # EMA falling
        _snap("FALLEMA", [20.5] * 13 + [20.2, 20.4], lows=[20.3] * 13 + [20.0, 20.1], ema=[21 - 0.05 * i for i in range(15)]),
        # cheap stock filtered by price
        _snap("PENNY", [2.1] * 14 + [2.2], low52=2.0, low52_ago=3),
    ]


def test_bounce_screens(client, db):  # noqa: F811
    h = _auth(client)
    r = client.post("/api/prices/snapshots", headers=h,
                    json={"items": _fixtures() + [{"symbol": "BAD"}], "replace_all": True, "source": "prices.csv"}).json()
    assert r == {"stored": 7, "skipped": 1}
    low = client.get("/api/bounces?kind=low52", headers=h).json()
    assert [x["symbol"] for x in low["rows"]] == ["LOWB"]
    assert low["rows"][0]["bounce_pct"] == pytest.approx(7.99, abs=0.01) and low["rows"][0]["sessions_since_low"] == 4
    assert low["data"]["symbols"] == 7 and low["data"]["as_of"] == "2026-10-30"
    assert "PENNY" in [x["symbol"] for x in client.get("/api/bounces?kind=low52&min_price=0", headers=h).json()["rows"]]
    ema = client.get("/api/bounces?kind=ema50", headers=h).json()
    assert [x["symbol"] for x in ema["rows"]] == ["EMAB"]
    assert 0 < ema["rows"][0]["above_ema_pct"] < 5
    assert client.get("/api/bounces?kind=nope", headers=h).status_code == 422
    # merge import replaces only the given symbol
    client.post("/api/prices/snapshots", headers=h, json={"items": [_snap("LOWB", [30.0] * 15, low52=18.4, low52_ago=4)]})
    assert client.get("/api/bounces?kind=low52", headers=h).json()["rows"] == []
    assert bn.snapshot_info(db)["symbols"] == 7


def test_custom_watchlist(client, db):  # noqa: F811
    h = _auth(client)
    client.post("/api/prices/snapshots", headers=h, json={"items": _fixtures(), "replace_all": True})
    r = client.post("/api/custom-watchlist", headers=h, json={"symbols": "emab, GOOD; good  XYZ.B", "note": "test"}).json()
    assert r["added"] == ["EMAB", "GOOD", "XYZ.B"]
    assert client.post("/api/custom-watchlist", headers=h, json={"symbols": "GOOD"}).json()["already"] == ["GOOD"]
    assert client.post("/api/custom-watchlist", headers=h, json={"symbols": "BAD$"}).status_code == 422
    rows = {x["symbol"]: x for x in client.get("/api/custom-watchlist?live=true", headers=h).json()["rows"]}
    assert set(rows) == {"EMAB", "GOOD", "XYZ.B"}
    assert rows["GOOD"]["price"] == 20.3 and rows["GOOD"]["pe"] == 18.4  # live quote (fake provider)
    assert rows["EMAB"]["vs_ema50_pct"] is not None and rows["EMAB"]["note"] == "test"
    client.delete("/api/custom-watchlist/xyz.b", headers=h)
    assert len(client.get("/api/custom-watchlist", headers=h).json()["rows"]) == 2
    # another user does not see it
    r2 = client.post("/api/auth/register", json={"email": "z@example.com", "password": "correct horse battery"})
    assert client.get("/api/custom-watchlist", headers={"Authorization": f"Bearer {r2.json()['token']}"}).json()["rows"] == []
