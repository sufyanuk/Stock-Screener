"""Custom watchlist (live prices and bars from the data APIs)."""
from tests.test_research import _auth, client  # noqa: F401  (fixture)


def test_custom_watchlist(client):  # noqa: F811
    h = _auth(client)
    r = client.post("/api/custom-watchlist", headers=h, json={"symbols": "emab, GOOD; good  XYZ.B", "note": "test"}).json()
    assert r["added"] == ["EMAB", "GOOD", "XYZ.B"]
    assert client.post("/api/custom-watchlist", headers=h, json={"symbols": "GOOD"}).json()["already"] == ["GOOD"]
    assert client.post("/api/custom-watchlist", headers=h, json={"symbols": "BAD$"}).status_code == 422
    rows = {x["symbol"]: x for x in client.get("/api/custom-watchlist", headers=h).json()["rows"]}  # live by default
    assert set(rows) == {"EMAB", "GOOD", "XYZ.B"} and rows["EMAB"]["note"] == "test"
    assert rows["GOOD"]["bars_source"] == "fake-bars" and rows["GOOD"]["price"] == 20.3
    client.delete("/api/custom-watchlist/xyz.b", headers=h)
    assert len(client.get("/api/custom-watchlist", headers=h).json()["rows"]) == 2
    r2 = client.post("/api/auth/register", json={"email": "z@example.com", "password": "correct horse battery"})
    assert client.get("/api/custom-watchlist", headers={"Authorization": f"Bearer {r2.json()['token']}"}).json()["rows"] == []
