"""Internal Shariah engine: separate methodologies, verified FTSE rules, no assumed thresholds,
missing data → DOUBTFUL, and the SEC frames loader end to end."""
from datetime import date

import pytest

from app.screening import shariah_engine as se
from tests.test_research import _auth, client  # noqa: F401  (pytest fixture)

TODAY = date(2026, 11, 8)
FTSE = se.methodologies()["FTSE"]


def _inp(**kw):
    base = dict(ticker="X", name="Widget Corp", sic="3571", sic_description="Electronic computers",
                period_end=date(2026, 9, 30), total_assets=1000.0, debt=100.0, cash_ib=150.0, receivables=100.0,
                revenue=500.0, interest_income=5.0, market_cap=2000.0)
    base.update(kw)
    return se.Inputs(**base)


def _ratio(r, key):
    return next(x for x in r["ratios"] if x["key"] == key)


# ---------------------------------------------------------------- FTSE (rules from the ground rules v4.6)
def test_ftse_rules_are_the_official_ones():
    assert "v4.6" in FTSE["source"]
    t = {x["key"]: (x["numerator"], x["denominator"], x["op"], x["limit"]) for x in FTSE["tests"]}
    assert t == {"debt": ("debt", "total_assets", "lt", 33.333), "cash": ("cash_ib", "total_assets", "lt", 33.333),
                 "recv_cash": ("receivables_plus_cash", "total_assets", "lt", 50.0),
                 "income": ("nonpermissible_income", "revenue", "le", 5.0)}
    assert {"entertainment", "weapons"} <= set(FTSE["business"]) and set(se.CORE) <= set(FTSE["business"])


def test_ftse_halal_with_ratios():
    r = se.screen("FTSE", FTSE, _inp(), TODAY)
    assert r["status"] == se.HALAL and r["status_label"] == "HALAL / SHARIAH COMPLIANT"
    assert _ratio(r, "debt")["value"] == pytest.approx(10.0)
    assert _ratio(r, "cash")["value"] == pytest.approx(15.0)
    assert _ratio(r, "recv_cash")["value"] == pytest.approx(25.0)
    assert _ratio(r, "income")["value"] == pytest.approx(1.0)


@pytest.mark.parametrize("kw", [dict(debt=340.0), dict(cash_ib=340.0), dict(receivables=360.0, cash_ib=150.0),
                                dict(interest_income=26.0), dict(sic="6022"), dict(sic="2085"), dict(sic="2111"),
                                dict(sic="3484")])
def test_ftse_non_compliant(kw):
    assert se.screen("FTSE", FTSE, _inp(**kw), TODAY)["status"] == se.NON_COMPLIANT


def test_ftse_boundaries_follow_the_wording():
    # debt "less than 33.333%": exactly 33.333% fails; income "should not exceed 5%": exactly 5% passes
    assert se.screen("FTSE", FTSE, _inp(debt=333.33), TODAY)["status"] == se.NON_COMPLIANT
    assert se.screen("FTSE", FTSE, _inp(interest_income=25.0), TODAY)["status"] == se.HALAL


@pytest.mark.parametrize("field", ["total_assets", "debt", "cash_ib", "receivables", "revenue", "interest_income",
                                   "sic", "period_end"])
def test_missing_figure_is_never_halal(field):
    r = se.screen("FTSE", FTSE, _inp(**{field: None}), TODAY)
    assert r["status"] == se.DOUBTFUL and r["reasons"]


def test_stale_and_review_activities_are_doubtful():
    assert se.screen("FTSE", FTSE, _inp(period_end=date(2025, 3, 31)), TODAY)["status"] == se.DOUBTFUL
    for name in ("Lucky Casino Inc", "Pork Producers Co", "Adult Entertainment Holdings"):
        assert se.screen("FTSE", FTSE, _inp(name=name), TODAY)["status"] == se.DOUBTFUL
    assert se.screen("FTSE", FTSE, _inp(sic="7011"), TODAY)["status"] == se.DOUBTFUL  # hotels: needs review
    # word boundaries: "Graham" is not "ham", "Spirit Airlines" is not "spirits"
    assert se.screen("FTSE", FTSE, _inp(name="Graham Corp"), TODAY)["status"] == se.HALAL
    assert se.screen("FTSE", FTSE, _inp(name="Spirit Airlines"), TODAY)["status"] == se.HALAL


def test_business_review_and_tolerance():
    ok = se.Review("compliant", "admin", date(2026, 10, 1), "hotel without casino or bar", prohibited_revenue_pct=1.0)
    r = se.screen("FTSE", FTSE, _inp(sic="7011"), TODAY, ok)
    assert r["status"] == se.HALAL and _ratio(r, "income")["value"] == pytest.approx(2.0)  # (5 + 1% of 500) / 500
    over = se.Review("compliant", "admin", date(2026, 10, 1), "large bar segment", prohibited_revenue_pct=7.0)
    assert se.screen("FTSE", FTSE, _inp(sic="7011"), TODAY, over)["status"] == se.NON_COMPLIANT


# ---------------------------------------------------------------- unverified methodologies
@pytest.mark.parametrize("m", ["AAOIFI", "DJIM", "SP"])
def test_unconfigured_methodology_assumes_no_thresholds(m):
    rules = se.methodologies()[m]
    assert rules["source"] is None and rules["tests"] == []
    r = se.screen(m, rules, _inp(), TODAY)
    assert r["status"] == se.DOUBTFUL and "not configured" in r["reasons"][0]
    # business screen still applies
    assert se.screen(m, rules, _inp(sic="6022"), TODAY)["status"] == se.NON_COMPLIANT


def test_configured_methodologies_stay_separate():
    custom = {"source": "Official doc v1 (test)", "tests": [
        {"key": "debt", "label": "Debt / market cap", "numerator": "debt", "denominator": "market_cap", "op": "lt", "limit": 30}]}
    ms = se.methodologies({"AAOIFI": custom})
    inp = _inp(debt=340.0)  # 17% of market cap, 34% of total assets
    assert se.screen("AAOIFI", ms["AAOIFI"], inp, TODAY)["status"] == se.HALAL
    assert se.screen("FTSE", ms["FTSE"], inp, TODAY)["status"] == se.NON_COMPLIANT
    assert ms["DJIM"]["tests"] == [] and ms["SP"]["tests"] == []  # nothing borrowed from other methodologies
    # a market-cap test with no market cap is DOUBTFUL, not HALAL
    assert se.screen("AAOIFI", ms["AAOIFI"], _inp(market_cap=None), TODAY)["status"] == se.DOUBTFUL


def test_rules_validation_requires_a_source():
    t = [{"key": "d", "label": "Debt", "numerator": "debt", "denominator": "market_cap", "op": "lt", "limit": 30}]
    assert se.validate_rules({"source": "", "tests": t})
    assert se.validate_rules({"source": "Doc", "tests": [t[0] | {"limit": 0}]})
    assert se.validate_rules({"source": "Doc", "tests": [t[0] | {"denominator": "made_up"}]})
    assert se.validate_rules({"source": "Doc", "business": list(se.CORE), "tests": t}) == []


# ---------------------------------------------------------------- SEC frames loader + API
class FakeSec:
    def __init__(self):
        self.calls = []
        a = {1: "GOOD", 2: "BANK", 3: "DEBTY", 4: "NOINT"}
        self.inst = {  # tag -> {cik: value} at 2026-09-30
            "Assets": {1: 1000, 2: 5000, 3: 1000, 4: 1000},
            "LongTermDebtNoncurrent": {1: 80, 3: 300}, "LongTermDebtCurrent": {1: 20, 3: 50},
            "CashAndCashEquivalentsAtCarryingValue": {1: 100, 2: 900, 3: 50, 4: 100},
            "ShortTermInvestments": {1: 50},
            "AccountsReceivableNetCurrent": {1: 100, 2: 10, 3: 100, 4: 100},
            "CommonStockSharesOutstanding": {1: 100, 3: 100, 4: 100},
        }
        self.annual = {"Revenues": {1: 500, 2: 400, 3: 500, 4: 500}, "InvestmentIncomeInterest": {1: 5, 2: 300, 3: 5}}
        self.names = a

    async def frame(self, taxonomy, tag, unit, period):
        self.calls.append((tag, period))
        if period == "CY2026Q2I" and tag == "Assets":  # an older balance sheet must not win
            return [{"cik": 1, "end": "2026-06-30", "val": 1.0, "accn": "old"}]
        if period == "CY2026Q3I" and tag in self.inst:
            return [{"cik": c, "end": "2026-09-30", "val": v, "accn": f"a{c}"} for c, v in self.inst[tag].items()]
        if period == "CY2025" and tag in self.annual:
            return [{"cik": c, "start": "2025-01-01", "end": "2025-12-31", "val": v, "accn": f"k{c}"}
                    for c, v in self.annual[tag].items()]
        return []

    async def submissions(self, cik):
        c = int(cik)
        return {"name": self.names.get(c, "?"), "sic": "6022" if c == 2 else "3571",
                "sicDescription": "State commercial banks" if c == 2 else "Electronic computers"}


def test_loader_and_api(client, db, monkeypatch):  # noqa: F811
    import app.main as main
    from app.models import Security
    from app.services import shariah as shs
    from tests.conftest import FakeDataSource

    fake = FakeSec()

    class DS(FakeDataSource):
        sec = fake

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    monkeypatch.setattr(shs, "_today", lambda: TODAY)
    for c, t in fake.names.items():
        db.add(Security(ticker=t, cik=str(c).zfill(10), name=t.title() + " Inc", exchange="Nasdaq"))
    db.add(Security(ticker="NOCIK", cik=None, name="No Cik Co", exchange="Nasdaq"))
    db.commit()
    h = _auth(client)

    r = client.post("/api/shariah/load", headers=h, json={"max_seconds": 120}).json()
    assert r["complete"] and r["frames_done"] == r["frames_total"] and r["companies_done"] == 4 and r["results"] == 5
    assert fake.calls[0][0] == "Assets"

    lst = client.get("/api/shariah?methodology=FTSE", headers=h).json()
    st = {x["symbol"]: x["status"] for x in lst["rows"]}
    assert st == {"GOOD": "HALAL", "BANK": "NON_COMPLIANT", "DEBTY": "NON_COMPLIANT", "NOINT": "DOUBTFUL",
                  "NOCIK": "DOUBTFUL"}
    assert lst["counts"] == {"HALAL": 1, "NON_COMPLIANT": 2, "DOUBTFUL": 2}
    good = next(x for x in lst["rows"] if x["symbol"] == "GOOD")
    assert {x["key"]: x["value"] for x in good["ratios"]} == {"debt": 10.0, "cash": 15.0, "recv_cash": 25.0, "income": 1.0}
    assert client.get("/api/shariah?methodology=FTSE&status=HALAL", headers=h).json()["total"] == 1

    # AAOIFI / DJIM / S&P have no rules yet: everything DOUBTFUL except the bank (business screen)
    aa = {x["symbol"]: x["status"] for x in client.get("/api/shariah?methodology=AAOIFI", headers=h).json()["rows"]}
    assert aa["GOOD"] == "DOUBTFUL" and aa["BANK"] == "NON_COMPLIANT"

    d = client.get("/api/shariah/GOOD", headers=h).json()
    assert d["sic"] == "3571" and d["period_end"] == "2026-09-30"
    assert d["sources"]["debt"][0]["tag"] == "LongTermDebtNoncurrent" and d["methods"]["FTSE"]["status"] == "HALAL"
    assert d["methods"]["FTSE"]["inputs"]["debt"] == 100 and d["methods"]["FTSE"]["inputs"]["market_cap"] is None

    # colours across the app follow the methodology chosen in Settings (FTSE by default)
    from app.services import research as rs
    assert rs.shariah_map(db, {"GOOD", "BANK", "NOINT"}) == {"GOOD": "Compliant", "BANK": "Non-Compliant",
                                                             "NOINT": "Needs Review"}

    # an administrator enters AAOIFI rules from the official document → AAOIFI classifies
    body = {"source": "AAOIFI Shari'ah Standard No. 21 (test copy)", "business": list(se.CORE),
            "business_tolerance_pct": None,
            "tests": [{"key": "debt", "label": "Debt / total assets", "numerator": "debt",
                       "denominator": "total_assets", "op": "lt", "limit": 30}]}
    assert client.put("/api/shariah/rules/AAOIFI", headers=h, json=body | {"source": ""}).status_code == 422
    assert client.put("/api/shariah/rules/AAOIFI", headers=h, json=body).status_code == 200
    aa = {x["symbol"]: x["status"] for x in client.get("/api/shariah?methodology=AAOIFI", headers=h).json()["rows"]}
    assert aa["GOOD"] == "HALAL" and aa["DEBTY"] == "NON_COMPLIANT"
    rules = client.get("/api/shariah/rules", headers=h).json()
    assert next(m for m in rules["methodologies"] if m["key"] == "AAOIFI")["configured"] is True
    assert next(m for m in rules["methodologies"] if m["key"] == "SP")["configured"] is False

    # ticker colours can follow another methodology
    assert client.put("/api/shariah/primary", headers=h, json={"methodology": "nope"}).status_code == 422
    assert client.put("/api/shariah/primary", headers=h, json={"methodology": "AAOIFI"}).json() == {"primary": "AAOIFI"}
    assert rs.shariah_map(db, {"DEBTY"}) == {"DEBTY": "Non-Compliant"}
    assert client.get("/api/shariah", headers=h).json()["primary"] == "AAOIFI"
