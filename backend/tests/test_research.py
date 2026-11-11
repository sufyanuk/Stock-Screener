from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.providers.market import parse_av_earnings
from app.services import seasonality as ss
from tests.conftest import FakeDataSource
from tests.test_engine_api import run_scan


@pytest.fixture
def client(db, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "make_datasource", lambda: FakeDataSource())
    return TestClient(main.app)


def _auth(client):
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": "correct horse battery"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_alpha_vantage_calendar_parser_matches_spreadsheet_format():
    rows = parse_av_earnings("symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
                             "AAUC,ALLIED GOLD CORPORATION,2026-11-04,2026-09-30,0.18,USD,post-market\n"
                             "ACET,ADICET BIO,2026-11-04,2026-09-30,-1.56,USD,\n")
    assert rows[0] == {"symbol": "AAUC", "name": "ALLIED GOLD CORPORATION", "report_date": "2026-11-04",
                       "fiscal_date_ending": "2026-09-30", "eps_estimate": 0.18, "time_of_day": "post-market"}
    assert rows[1]["eps_estimate"] == -1.56 and rows[1]["time_of_day"] is None


def test_earnings_screen_columns_and_markers(client, db):
    h = _auth(client)
    run_scan(db)  # stores insider purchases + Shariah statuses for GOOD / BANK
    r = client.get("/api/earnings/upcoming?days=7", headers=h).json()
    syms = [x["symbol"] for x in r["rows"]]
    assert syms == ["GOOD"]  # LATE (10 days out) excluded; BANK is Shariah non-compliant: hidden
    good = r["rows"][0]
    assert good["time_of_day"] == "post-market" and good["shariah_status"] == "Compliant"
    assert good["insider"]["purchases"] == 1 and good["insider"]["shares"] == 2_000_000  # joint filing counted once
    assert good["analysts"] is None and r["analysts_missing"] == 2
    loaded = client.post("/api/earnings/load-analysts?days=7", headers=h).json()
    assert loaded["fetched"] == 2 and loaded["remaining"] == 0
    r = client.get("/api/earnings/upcoming?days=7", headers=h).json()
    assert r["rows"][0]["analysts"]["strong_buy"] == 3
    m = client.get("/api/earnings/markers", headers=h).json()["markers"]
    assert set(m) == {"GOOD", "BANK"} and m["GOOD"]["time_of_day"] == "post-market"


def test_stock_panel_summary(client, db):
    h = _auth(client)
    run_scan(db)
    client.get("/api/earnings/markers", headers=h)
    s = client.get("/api/stocks/GOOD/summary", headers=h).json()
    assert s["price"] == 20.3 and s["pe"] == 18.4 and "float_ratio_pct" not in s and "debt_to_equity" in s
    assert s["analysts"]["recommending_buy"] == 8 and s["analysts"]["counts"]["strong_buy"] == 3
    assert s["earnings"]["time_of_day"] == "post-market" and len(s["bars"]) > 100 and s["news"][0]["title"]
    assert s["insider_90d"]["purchases"] == 1 and s["shariah_status"] == "Compliant"


def test_dashboard_no_longer_has_low_float_or_health(client, db):
    h = _auth(client)
    run_scan(db)
    d = client.get("/api/dashboard", headers=h).json()
    assert "low_float" not in d and "provider_health" not in d


def test_insiders_endpoint_and_options_rows(client, db):
    h = _auth(client)
    run_scan(db)
    before = client.get("/api/insiders", headers=h).json()  # default: every purchase in the top 100
    assert {x["ticker"] for x in before["rows"]} == {"GOOD", "PCR"} and before["total_purchases"] == 2  # BANK hidden
    assert "openinsider.com/screener" in before["url"] and "cnt=100" in before["url"] and "fd=730" in before["url"]
    # 3 filings (GOOD reported jointly with Doe Trust in one filing, PCR, BANK): the joint owner is merged
    # inside its filing, BANK hidden
    assert (before["screener_rows"], before["merged_joint"], before["hidden_non_compliant"]) == (3, 0, 1)
    assert client.get("/api/insiders?min_pct=1", headers=h).json()["rows"] == []  # no share counts yet
    ld = client.post("/api/insiders/load-shares", headers=h).json()
    assert ld["fetched"] == 2 and ld["remaining"] == 0
    r = client.get("/api/insiders?min_pct=1", headers=h).json()
    good = [x for x in r["rows"] if x["ticker"] == "GOOD"]
    assert len(good) == 1 and good[0]["co_reporters"] == ["Doe Trust"] and good[0]["shariah_status"] == "Compliant"
    assert good[0]["outstanding_shares"] == 100_000_000 and good[0]["pct_outstanding"] == pytest.approx(2.0)
    assert [x["ticker"] for x in r["rows"]] == ["GOOD"]  # PCR 0.5% hidden; BANK (non-compliant) hidden
    assert r["shares_unavailable"] == 0 and r["shares_missing"] == 0
    assert len(client.get("/api/insiders?min_pct=0.1", headers=h).json()["rows"]) == 2
    from tests.test_options_import import fake_csv
    client.post("/api/admin/options-import", headers=h, json={"filename": "u.csv", "csv": fake_csv()})
    bid = client.get("/api/options-imports", headers=h).json()[0]["id"]
    d = client.get(f"/api/options-imports/{bid}", headers=h).json()
    rows = {x["symbol"]: x for x in d["data"]}
    assert rows["GOOD"]["passes_rules"] and not rows["PCR"]["passes_rules"] and not rows["PRICY"]["passes_rules"]
    assert rows["GOOD"]["in_top5"] and "BANK" not in rows  # non-compliant stocks hidden from scan results
    assert d["thresholds"]["min_volume_change_pct"] == 300


def _month_rows(years=range(2015, 2026)):
    """GOOD gains 10% every November, BANK loses 5% every November, FLAT never moves, PENNY is cheap."""
    rows = []
    for sym, f in {"GOOD": 1.10, "BANK": 0.95, "FLAT": 1.0, "PENNY": 1.0}.items():
        px = 0.5 if sym == "PENNY" else 100.0
        for y in years:
            for m in range(1, 13):
                if m == 11:
                    px *= f
                rows.append([sym, f"{y}-{m:02d}", px])
    return rows


def test_month_last_session():
    assert ss.month_last_session(date(2026, 5, 1)) == date(2026, 5, 29)
    assert ss.month_last_session(date(2026, 10, 1)) == date(2026, 10, 30)


def test_seasonality_from_imported_csv(client, db):
    h = _auth(client)
    rows = _month_rows() + [["abc.w!", "2020-01", 1.0], ["BAD", "2020-13", 1.0], ["NEG", "2020-01", -1]]
    r = client.post("/api/seasonality/import", headers=h, json={"rows": rows, "replace_all": True, "source": "prices.csv"}).json()
    assert r["stored"] == 4 * 11 * 12 and r["skipped"] == 3
    f = client.post("/api/seasonality/import/finish", headers=h, json={"source": "prices.csv", "last_date": "2025-12-31"}).json()
    assert f["partial_month_dropped"] is None and f["coverage"]["symbols"] == 4 and f["coverage"]["months"] == 132
    assert f["coverage"]["provider"] == "prices.csv"

    t = client.get("/api/seasonality?month=11&min_years=5", headers=h).json()
    assert t["mode"] == "bullish" and t["ranked"][0]["symbol"] == "GOOD"
    good = t["ranked"][0]
    assert good["avg"] == pytest.approx(10.0) and good["win_rate"] == 100 and good["years"] == 11
    assert (good["count"], good["pct"]) == (11, 100.0)
    assert good["max"] == pytest.approx(10.0) and good["min"] == pytest.approx(10.0) and good["avg_dir"] == pytest.approx(10.0)
    bear = client.get("/api/seasonality?month=11&min_years=5&mode=bearish", headers=h).json()
    b = bear["ranked"][0]
    assert bear["mode"] == "bearish" and b["symbol"] == "BANK" and (b["count"], b["pct"]) == (11, 100.0)
    assert b["max"] == pytest.approx(-5.0) and b["avg_dir"] == pytest.approx(-5.0)
    assert [x["symbol"] for x in t["ranked"]][-1] in ("BANK", "FLAT", "PENNY")
    t2 = client.get("/api/seasonality?month=11&min_price=1", headers=h).json()
    assert "PENNY" not in [e["symbol"] for e in t2["ranked"]]
    assert [e["symbol"] for e in client.get("/api/seasonality?month=11&q=go", headers=h).json()["ranked"]] == ["GOOD"]
    assert "best_by_month" not in t  # Top-5-per-month card removed
    assert client.get("/api/seasonality?month=0", headers=h).status_code == 422

    # merge mode: re-importing one symbol replaces only that symbol's history
    client.post("/api/seasonality/import", headers=h, json={"rows": [["GOOD", "2025-01", 10.0], ["GOOD", "2025-02", 11.0]]})
    f = client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2025-02-10"}).json()
    assert f["partial_month_dropped"] is None  # Feb 2025 is not the latest month (Dec 2025 is)
    feb = client.get("/api/seasonality?month=2&min_years=1&q=GOOD", headers=h).json()["ranked"]
    assert len(feb) == 1 and feb[0]["years"] == 1 and feb[0]["avg"] == pytest.approx(10.0)  # only the new history
    assert client.get("/api/seasonality?month=11&min_years=1&q=GOOD", headers=h).json()["ranked"] == []


def test_partial_final_month_is_dropped(client, db):
    h = _auth(client)
    rows = _month_rows(range(2020, 2023)) + [["GOOD", "2023-01", 500.0]]
    client.post("/api/seasonality/import", headers=h, json={"rows": rows, "replace_all": True})
    f = client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2023-01-13"}).json()
    assert f["partial_month_dropped"] == "2023-01"
    jan = client.get("/api/seasonality?month=1&min_years=1&q=GOOD", headers=h).json()["ranked"][0]
    assert jan["avg"] == pytest.approx(0.0)  # the half-month +400% move was not counted


def test_seasonality_import_requires_admin(client, db):
    _auth(client)
    r2 = client.post("/api/auth/register", json={"email": "b@example.com", "password": "correct horse battery"})
    h2 = {"Authorization": f"Bearer {r2.json()['token']}"}
    assert client.post("/api/seasonality/import", headers=h2, json={"rows": []}).status_code == 403


def test_alpha_vantage_overview_counts():
    from app.providers.market import parse_av_overview

    o = parse_av_overview({"Symbol": "IBM", "AnalystTargetPrice": "250.5", "AnalystRatingStrongBuy": "3",
                           "AnalystRatingBuy": "7", "AnalystRatingHold": "9", "AnalystRatingSell": "1",
                           "AnalystRatingStrongSell": "None"})
    assert o["counts"] == {"strong_buy": 3, "buy": 7, "hold": 9, "sell": 1} and o["target"] == 250.5
    assert parse_av_overview({"Information": "rate limit"}) is None



def test_seasonality_index_filter(client, db, monkeypatch):
    from app.services import indexes as ix

    async def fake_fetch(ds, code):
        return [("GOOD", "Good Widgets Inc"), ("FLAT", "Flat Co")], "wikipedia"

    monkeypatch.setattr(ix, "_fetch", fake_fetch)
    h = _auth(client)
    client.post("/api/seasonality/import", headers=h, json={"rows": _month_rows(), "replace_all": True})
    client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2025-12-31"})
    r = client.get("/api/seasonality?month=11&index=sp500", headers=h).json()
    assert [x["symbol"] for x in r["ranked"]] == ["GOOD", "FLAT"] and r["ranked"][0]["name"] == "Good Widgets Inc"
    assert {i["code"] for i in r["indexes"]} == {"all", "sp500", "ndx", "dji"}


def test_wikipedia_constituent_parser():
    from app.services.indexes import parse_wiki

    html = ("<table><tr><th>Symbol</th><th>Security</th></tr>"
            + "".join(f"<tr><td>T{i}</td><td>Co {i}</td></tr>" for i in range(30))
            + "<tr><td>BRK.B</td><td>Berkshire</td></tr></table>")
    rows = parse_wiki(html, 25)
    assert len(rows) == 31 and rows[-1] == ("BRK.B", "Berkshire")
    assert parse_wiki(html, 100) == []


def test_seasonality_backfills_bullish_bearish_columns(client, db):
    from app.models import SeasonalityStat

    h = _auth(client)
    client.post("/api/seasonality/import", headers=h, json={"rows": _month_rows(), "replace_all": True})
    client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2025-12-31"})
    db.query(SeasonalityStat).update({"pos_years": None, "neg_years": None})
    db.commit()
    t = client.get("/api/seasonality?month=11&min_years=5&index=all", headers=h).json()
    assert t["ranked"][0]["symbol"] == "GOOD" and t["ranked"][0]["count"] == 11


def test_seasonality_uses_no_price_api(client, db, monkeypatch):
    """Seasonality works only from the imported CSV: no history load, and the price column uses stored data."""
    from datetime import date as d

    import app.main as main
    from app.models import PriceSnapshot
    from tests.conftest import FakeDataSource

    class Alp:
        async def multi_snapshots(self, symbols):
            raise AssertionError("the price API must not be called")

        async def multi_daily_bars(self, *a, **k):
            raise AssertionError("the price API must not be called")

    class DS(FakeDataSource):
        alpaca = Alp()

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    h = _auth(client)
    assert client.post("/api/seasonality/load", headers=h, json={}).status_code in (404, 405)
    client.post("/api/seasonality/import", headers=h, json={"rows": _month_rows(), "replace_all": True})
    client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2025-12-31"})
    db.add(PriceSnapshot(symbol="GOOD", as_of=d(2026, 11, 6), close=123.45, data={}, source="alpaca-latest"))
    db.add(PriceSnapshot(symbol="FLAT", as_of=d(2026, 11, 6), close=0.0, data={"checked": True}, source="alpaca-latest"))
    db.commit()
    t = client.get("/api/seasonality?month=11&min_years=5", headers=h).json()
    r = {x["symbol"]: x for x in t["ranked"]}
    assert r["GOOD"]["price"] == 123.45 and r["GOOD"]["price_as_of"] == "2026-11-06" and r["GOOD"]["price_live"] is False
    assert r["BANK"]["price"] == r["BANK"]["last_close"] and r["FLAT"]["price"] == r["FLAT"]["last_close"]
    assert "load" not in t and t["coverage"]["provider"] is not None


def test_shares_lookup_uses_sec_and_does_not_cache_timeouts(db):
    import asyncio
    from datetime import date as d

    from app.models import SecFact, Security, SharesOutstanding
    from app.services import research as rs
    from app.providers.http import ProviderError

    db.add_all([Security(ticker="SECX", cik="0000000009", name="Sec X", exchange="Nasdaq"),
                Security(ticker="SLOW", cik="0000000008", name="Slow", exchange="Nasdaq"),
                Security(ticker="NONE", cik="0000000007", name="None", exchange="Nasdaq"),
                SecFact(cik="0000000009", tag="Assets", end=d(2026, 9, 30), val=1e9),
                SecFact(cik="0000000009", tag="CommonStockSharesOutstanding", end=d(2026, 9, 30), val=5e7)])
    db.commit()

    class DS:
        async def shares_outstanding(self, t):
            if t == "SLOW":
                await asyncio.sleep(60)
            raise ProviderError("all", "no data")

    r = asyncio.run(rs.load_shares(db, DS(), ["SECX", "SLOW", "NONE"], max_seconds=0.5))
    got = {x.ticker: (x.shares, x.source) for x in db.query(SharesOutstanding).all()}
    assert got == {"SECX": (5e7, "sec"), "NONE": (None, None)}  # SLOW timed out: not cached, tried next batch
    assert r["fetched"] == 1 and r["remaining"] == 1
    rs._save_shares(db, {"SECX": (6e7, "sec")})  # saving an existing ticker again is fine
    assert db.get(SharesOutstanding, "SECX").shares == 6e7


def test_upsert_tolerates_existing_rows_and_analyst_budget(db):
    import asyncio
    from datetime import datetime, timezone

    from app.db import upsert
    from app.models import AnalystSnapshot
    from app.services import research as rs

    now = datetime.now(timezone.utc)
    row = {"symbol": "AAA", "strong_buy": 1, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0, "provider": "x", "retrieved_at": now}
    upsert(db, AnalystSnapshot, [row])
    upsert(db, AnalystSnapshot, [row | {"strong_buy": 5}])  # same key again: updated, no duplicate-key error
    db.commit()
    assert db.get(AnalystSnapshot, "AAA").strong_buy == 5

    class Prov:
        provider = "fake"

    class Ad:
        counts = {"strong_buy": 2}

    class DS:
        async def analyst(self, s):
            await asyncio.sleep(0.2)
            return Ad(), Prov()

    r = asyncio.run(rs.load_analyst_counts(db, DS(), [f"S{i}" for i in range(50)], limit=50, max_seconds=0.5))
    assert 1 <= r["fetched"] < 50 and r["remaining"] == 50 - r["fetched"]  # stopped on time, progress saved
    assert db.query(AnalystSnapshot).filter(AnalystSnapshot.symbol.like("S%")).count() == r["fetched"]



def test_insiders_tab_pulls_openinsider_at_most_every_6_hours(client, db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    import app.main as main
    from app.models import ProviderHealth
    from app.services.engine import ScanEngine

    calls = []

    async def fake_ingest(self):
        calls.append(1)
        h = self.db.get(ProviderHealth, "openinsider") or ProviderHealth(provider="openinsider", calls=0, failures=0)
        h.last_success_at = datetime.now(timezone.utc)
        self.db.merge(h)
        self.db.commit()
        return True

    monkeypatch.setattr(ScanEngine, "_ingest_openinsider", fake_ingest)
    monkeypatch.setattr(main.get_settings(), "insider_source", "openinsider")
    h = _auth(client)
    assert client.get("/api/insiders", headers=h).json()["pulled_at"] and len(calls) == 1
    client.get("/api/insiders", headers=h)
    assert len(calls) == 1  # fresh: not pulled again
    row = db.get(ProviderHealth, "openinsider")
    row.last_success_at = datetime.now(timezone.utc) - timedelta(hours=7)
    db.commit()
    client.get("/api/insiders", headers=h)
    assert len(calls) == 2  # older than 6 hours: pulled again


def test_shares_from_cover_page_and_sec_filings(db):
    import asyncio
    from datetime import date as d

    from app.models import SecFact, Security, SharesOutstanding
    from app.services import research as rs

    db.add_all([Security(ticker="COVR", cik="0000000011", name="Cover", exchange="Nasdaq"),
                Security(ticker="FILE", cik="0000000012", name="Filings", exchange="Nasdaq"),
                SecFact(cik="0000000011", tag="dei:EntityCommonStockSharesOutstanding", end=d(2026, 10, 25), val=7e7)])
    db.commit()

    class Sec:
        async def companyfacts(self, cik):  # two share classes on the cover page: added up
            f = [{"end": "2026-10-20", "val": 3e7, "accn": "a", "filed": "2026-10-30"},
                 {"end": "2026-10-20", "val": 2e7, "accn": "a", "filed": "2026-10-30"}]
            return {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": f}}}}}

    class DS:
        sec = Sec()

        async def shares_outstanding(self, t):
            raise AssertionError("SEC sources should have answered first")

    r = asyncio.run(rs.load_shares(db, DS(), ["COVR", "FILE"]))
    got = {x.ticker: (x.shares, x.source) for x in db.query(SharesOutstanding).all()}
    assert got == {"COVR": (7e7, "sec (cover page)"), "FILE": (5e7, "sec (filings)")} and r["fetched"] == 2


def test_concurrent_saves_of_earnings_and_securities_do_not_fail(db):
    from datetime import date as d, datetime, timezone

    from app.db import upsert
    from app.models import EarningsEvent, Security

    row = {"symbol": "AAA", "name": "A", "report_date": d(2026, 11, 10), "fiscal_date_ending": None,
           "eps_estimate": 1.0, "time_of_day": "pre-market", "provider": "x", "retrieved_at": datetime.now(timezone.utc)}
    upsert(db, EarningsEvent, [row], conflict=["symbol", "report_date"])
    upsert(db, EarningsEvent, [row | {"eps_estimate": 2.0}], conflict=["symbol", "report_date"])  # the other request
    db.commit()
    rows = db.query(EarningsEvent).all()
    assert len(rows) == 1 and rows[0].eps_estimate == 2.0
    sec = {"ticker": "ZZZ", "cik": "1", "name": "Z", "exchange": "Nasdaq", "active": True, "updated_at": datetime.now(timezone.utc)}
    upsert(db, Security, [sec], update=False)
    upsert(db, Security, [sec | {"name": "changed"}], update=False)  # already added by another request: kept
    db.commit()
    assert db.get(Security, "ZZZ").name == "Z"


def test_claim_once_lets_exactly_one_request_run(db):
    from app.db import claim_once

    assert claim_once(db, "daily-scan:2026-11-08") is True
    assert claim_once(db, "daily-scan:2026-11-08") is False  # a second tab / instance at the same moment
    assert claim_once(db, "daily-scan:2026-11-09") is True


def test_company_quote_takes_debt_to_equity_from_finnhub():
    import asyncio

    from app.services.datasource import LiveDataSource

    class Fh:
        name = "finnhub"

        def __init__(self, m):
            self.m = m

        async def metrics(self, t):
            return self.m

        async def profile(self, t):
            return {"name": "Apple Inc", "marketCapitalization": 3000000}

        async def quote(self, t):
            return {"price": 200.0, "day_change_pct": 1.0}

    def ds(m):
        d = object.__new__(LiveDataSource)
        d.errors, d.fmp, d.alpaca, d.massive, d.finnhub = [], None, None, None, Fh(m)
        return d

    q, _ = asyncio.run(ds({"totalDebt/totalEquityQuarterly": 1.52, "totalDebt/totalEquityAnnual": 1.4, "peTTM": 30}).company_quote("AAPL"))
    assert q["debt_to_equity"] == 1.52 and q["pe"] == 30 and q["price"] == 200.0
    q, _ = asyncio.run(ds({"totalDebt/totalEquityAnnual": 1.4}).company_quote("AAPL"))
    assert q["debt_to_equity"] == 1.4  # no quarterly figure: latest annual
    q, _ = asyncio.run(ds({}).company_quote("AAPL"))
    assert q["debt_to_equity"] is None


def test_stock_panel_trade_plan(client, db):
    from datetime import timedelta

    import numpy as np

    from app.models import DailyBar
    from app.services import research as rs

    # price oscillates between support ~20 and resistance ~25, ending just above support
    closes = 22.5 + 2.5 * np.sin(np.arange(300) / 8.0)
    closes[-6:] = [21.0, 20.6, 20.3, 20.1, 20.2, 20.3]
    start = rs.today_ny() - timedelta(days=360)
    days = [start + timedelta(days=i) for i in range(400) if (start + timedelta(days=i)).weekday() < 5][:300]
    bars = [DailyBar(ticker="OSC", session=day, open=c * 0.995, high=c * 1.01, low=c * 0.99, close=c, volume=1e6,
                     provider="test") for day, c in zip(days, closes)]
    plan = rs._trade_plan(db, bars)
    assert plan["stop_loss"] < 20.3 and plan["target1"] > 20.3 and plan["as_of"] == days[-1].isoformat()
    assert rs._trade_plan(db, bars[:40]) is None  # too little history: nothing invented
    rising = [DailyBar(ticker="UP", session=day, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1e6, provider="t")
              for day, c in zip(days, np.linspace(10, 40, 300))]
    assert rs._trade_plan(db, rising) is None or rs._trade_plan(db, rising)["stop_loss"] < 40


def test_attach_prices_uses_stored_prices_only(db):
    from datetime import date as d

    from app.models import PriceSnapshot
    from app.services import research as rs

    db.add(PriceSnapshot(symbol="AAA", as_of=d(2026, 11, 10), close=12.5, data={}, source="alpaca-latest"))
    db.add(PriceSnapshot(symbol="ZERO", as_of=d(2026, 11, 10), close=0.0, data={"checked": True}, source="alpaca-latest"))
    db.commit()
    rows = rs.attach_prices(db, [{"symbol": "AAA"}, {"symbol": "ZERO"}, {"symbol": "CSV", "price": 3.0}, {"symbol": "NONE"}])
    assert rows[0]["price"] == 12.5 and rows[0]["price_as_of"] == "2026-11-10"
    assert rows[1]["price"] is None and rows[2]["price"] == 3.0 and rows[3]["price"] is None  # no price invented
    ins = rs.attach_prices(db, [{"ticker": "AAA", "price": 9.0}], "ticker", field="last_price")
    assert ins[0]["price"] == 9.0 and ins[0]["last_price"] == 12.5  # insider purchase price kept apart
