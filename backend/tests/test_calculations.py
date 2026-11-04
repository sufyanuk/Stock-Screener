from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.config import (AnalystCriteria, FloatCriteria, InsiderCriteria, LiquidityCriteria, OptionsCriteria,
                        ScreeningConfig, ShariahConfig, TechnicalCriteria)
from app.screening import analyst, float_liquidity as fl, insider, options, ranking, shariah, technicals
from app.screening.types import CriterionResult, Outcome
from app.tools.expectancy import ExpectancyInput, calculate_expectancy, scenario_grid
from app.tools.pnl import Dividend, Fill, summarize
from app.tools.position import PositionInput, calculate_position


# ---------------------------------------------------------------- options
def _hist(n, call=1000, put=100, last_call=None, last_put=None):
    d0 = date(2026, 1, 1)
    rows = [options.DailyOptionsVolume(d0 + timedelta(days=i), call, put) for i in range(n - 1)]
    rows.append(options.DailyOptionsVolume(d0 + timedelta(days=n - 1), last_call or call, last_put or put))
    return rows


def test_volume_baseline_trailing_avg_uses_window_one_month_earlier():
    h = _hist(41)
    # make the window D-40..D-21 different from the most recent 20 sessions
    for r in h[20:40]:
        r.call_volume = 5000
    h[-1].call_volume, h[-1].put_volume = 4000, 400
    vb = options.volume_baseline(h, "trailing_20d_avg")
    assert vb.baseline == 1100  # sessions 0..19 (D-40..D-21)
    assert vb.current == 4400
    assert vb.change_pct == pytest.approx(300.0)
    assert vb.baseline_sessions == (h[0].session, h[19].session)


def test_volume_baseline_insufficient_history_is_missing():
    vb = options.volume_baseline(_hist(10), "trailing_20d_avg")
    assert vb.change_pct is None


def test_single_session_baseline():
    h = _hist(30)
    h[-22].call_volume = 200
    h[-22].put_volume = 0
    vb = options.volume_baseline(h, "single_session_1m_ago")
    assert vb.baseline == 200


def test_iv_rank():
    s = list(np.linspace(0.2, 0.6, 251)) + [0.3]
    assert options.iv_rank(s) == pytest.approx(25.0)
    assert options.iv_rank(s[:50]) is None  # too few observations


def test_put_call_ratio_and_moneyness():
    assert options.put_call_ratio(10, 100) == 0.1
    assert options.put_call_ratio(10, 0) is None
    assert options.moneyness("call", 50, 100) == "ITM"
    assert options.moneyness("put", 50, 100) == "OTM"
    assert options.moneyness("call", 101, 100) == "ATM"


def test_evaluate_options_pass_fail_missing():
    cfg = OptionsCriteria()
    h = _hist(41, call=900, put=100)
    h[-1].call_volume, h[-1].put_volume = 4800, 200  # 5000 vs 1000 = +400%, P/C .0417
    iv = list(np.linspace(0.2, 0.6, 251)) + [0.4]
    r = options.evaluate_options(options.OptionsSnapshot("X", 10, h, iv), cfg)
    assert r.outcome is Outcome.PASS and r.metrics["volume_change_pct"] == pytest.approx(400)
    h[-1].put_volume = 1500  # P/C 0.31
    assert options.evaluate_options(options.OptionsSnapshot("X", 10, h, iv), cfg).outcome is Outcome.FAIL
    assert options.evaluate_options(options.OptionsSnapshot("X", 10, h, []), cfg).outcome is Outcome.MISSING


def test_exactly_300_pct_passes_but_iv_rank_20_fails():
    cfg = OptionsCriteria()
    h = _hist(41, call=1000, put=0)
    h[-1].call_volume, h[-1].put_volume = 3900, 100
    snap = options.OptionsSnapshot("X", 10, h, [], provider_iv_rank=20.0)
    r = options.evaluate_options(snap, cfg)
    assert r.metrics["volume_change_pct"] == pytest.approx(300)
    assert r.outcome is Outcome.FAIL and "✗ IV rank 20.0" in r.explanation


# ---------------------------------------------------------------- insider
def _tx(**kw):
    base = dict(accession="a1", txn_index=0, issuer_cik="1", ticker="X", owner_name="Jane CEO", owner_cik="o1",
                officer_title="Chief Executive Officer", is_director=False, is_officer=True, is_ten_pct_owner=False,
                transaction_date=date(2026, 10, 20), filing_date=date(2026, 10, 22), code="P",
                acquired_disposed="A", shares=60_000, price=10.0, shares_owned_after=100_000, direct_indirect="D")
    base.update(kw)
    return insider.InsiderTxn(**base)


def test_insider_excludes_non_purchases_and_dedupes_joint_reports():
    txns = [
        _tx(),
        _tx(accession="a1", txn_index=0),  # exact duplicate ingestion
        _tx(accession="a2", owner_name="Family Trust", owner_cik="o2", direct_indirect="I"),  # joint report
        _tx(accession="a3", code="A", price=0),  # grant
        _tx(accession="a4", code="M"),  # option exercise
        _tx(accession="a5", code="G"),  # gift
        _tx(accession="a6", owner_name="Dir B", owner_cik="o3", officer_title="", is_director=True,
            shares=50_000, transaction_date=date(2026, 10, 21)),
    ]
    inp = insider.InsiderInput("X", date(2026, 11, 1), txns, 10_000_000, date(2026, 9, 30))
    r = insider.evaluate_insider(inp, InsiderCriteria())
    assert r.metrics["unique_purchase_count"] == 2
    assert r.metrics["aggregate_shares"] == 110_000
    assert r.metrics["aggregate_pct"] == pytest.approx(1.1)
    assert r.outcome is Outcome.PASS
    assert r.metrics["excluded_count"] == 3
    joint = [p for p in r.metrics["purchases"] if p["joint_report"]][0]
    assert joint["owner"] == "Jane CEO" and joint["co_reporters"] == ["Family Trust"]
    assert joint["owned_before"] == 40_000
    single = insider.evaluate_insider(inp, InsiderCriteria(basis="single_transaction"))
    assert single.outcome is Outcome.FAIL  # 0.6% largest single purchase


def test_insider_amendment_supersedes_original():
    txns = [_tx(accession="orig"), _tx(accession="amend", is_amendment=True)]
    p, _ = insider.dedupe_purchases(txns)
    assert len(p) == 1 and p[0].primary.accession == "amend"


def test_insider_missing_shares_outstanding_and_lookback():
    inp = insider.InsiderInput("X", date(2026, 11, 1), [_tx()], None, None)
    assert insider.evaluate_insider(inp, InsiderCriteria()).outcome is Outcome.MISSING
    inp = insider.InsiderInput("X", date(2026, 11, 1), [_tx(transaction_date=date(2026, 9, 1))], 1e6, None)
    assert insider.evaluate_insider(inp, InsiderCriteria(lookback_days=30)).outcome is Outcome.FAIL


def test_insider_ignores_filings_after_as_of_date():  # look-ahead protection
    inp = insider.InsiderInput("X", date(2026, 10, 21), [_tx(filing_date=date(2026, 10, 22))], 1e6, None)
    assert insider.evaluate_insider(inp, InsiderCriteria()).metrics["unique_purchase_count"] == 0


# ---------------------------------------------------------------- analyst
def test_analyst_consensus_and_unweighted_flag():
    inp = analyst.AnalystInput("X", date(2026, 11, 1), 10.0,
                               {"strong_buy": 4, "buy": 4, "hold": 2, "sell": 0, "strong_sell": 0}, 13.0, 16, 9,
                               actions=[analyst.RatingAction(date(2026, 10, 15), "F", None, "upgrade", "buy")])
    r = analyst.evaluate_analyst(inp, AnalystCriteria())
    assert r.metrics["consensus_score"] == pytest.approx(4.2)
    assert r.metrics["consensus"] == "Buy"
    assert r.metrics["upside_pct"] == pytest.approx(30)
    assert r.metrics["accuracy_weighting"] is False
    assert r.outcome is Outcome.PASS


def test_analyst_low_coverage_missing():
    inp = analyst.AnalystInput("X", date(2026, 11, 1), 10.0, {"buy": 1}, None, None, None)
    assert analyst.evaluate_analyst(inp, AnalystCriteria()).outcome is Outcome.MISSING


# ---------------------------------------------------------------- technicals
def _bars(closes, vol=1_000_000):
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c * 0.995, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": vol}, index=idx)


def _oscillating(n=300, last=None):
    # price oscillates between ~20 (support) and ~25 (resistance)
    x = 22.5 + 2.5 * np.sin(np.arange(n) / 8.0)
    if last is not None:
        x[-1] = last
    return x


def test_indicators_basic():
    df = _bars(np.linspace(10, 20, 250))
    assert technicals.sma(df["close"], 20).iloc[-1] == pytest.approx(df["close"].tail(20).mean())
    assert technicals.rsi(df["close"]).iloc[-1] == pytest.approx(100.0)  # monotonic up
    bb = technicals.bollinger(df["close"]).iloc[-1]
    assert bb["lower"] < bb["mid"] < bb["upper"]


def test_support_detection_near_support():
    closes = _oscillating(300)
    closes[-6:] = [21.0, 20.6, 20.3, 20.1, 20.2, 20.3]
    r = technicals.evaluate_technical(_bars(closes), TechnicalCriteria())
    a = r.metrics
    assert a["primary_support"] is not None and a["primary_support"]["touches"] >= 2
    assert a["support_distance_pct"] <= 3
    assert r.outcome is Outcome.PASS
    plan = a["trade_plan"]
    assert plan["stop_loss"] < a["primary_support"]["low"]
    assert plan["target1"] is not None and plan["target1"] > a["price"]


def test_extended_from_support_fails():
    closes = list(_oscillating(300)) + list(np.linspace(25, 40, 30))
    r = technicals.evaluate_technical(_bars(np.array(closes)), TechnicalCriteria())
    assert r.outcome is Outcome.FAIL
    assert r.metrics["classification"] in ("extended", "broken_support", "no_support_found")


def test_breakdown_detected():
    closes = list(_oscillating(300))
    closes[-5:] = [20.5, 20.0, 19.0, 18.0, 17.5]
    r = technicals.evaluate_technical(_bars(np.array(closes)), TechnicalCriteria())
    assert r.metrics["classification"] == "broken_support"
    assert r.outcome is Outcome.FAIL


def test_short_history_missing():
    assert technicals.evaluate_technical(_bars(np.ones(30)), TechnicalCriteria()).outcome is Outcome.MISSING


# ---------------------------------------------------------------- float / liquidity
def test_float_categories():
    cfg = FloatCriteria()
    assert fl.float_category(5, cfg) == "Very Low Float"
    assert fl.float_category(15, cfg) == "Low Float"
    assert fl.float_category(30, cfg) == "Moderate Float"
    assert fl.float_category(41, cfg) == "High Float"
    r = fl.evaluate_float(fl.FloatInput(100e6, 8e6, short_interest_shares=2e6), cfg)
    assert r.metrics["float_ratio_pct"] == pytest.approx(8) and r.metrics["short_pct_float"] == pytest.approx(25)
    assert fl.evaluate_float(fl.FloatInput(None, 8e6), cfg).outcome is Outcome.MISSING


def test_liquidity_flags_pump_and_dump():
    r = fl.evaluate_liquidity(fl.LiquidityInput(500_000, 5.0, 1.00, 1.10, False, 60.0, 5.0), LiquidityCriteria())
    assert r.outcome is Outcome.FAIL
    assert len(r.metrics["flags"]) == 3


# ---------------------------------------------------------------- shariah
def _fund(**kw):
    base = dict(ticker="X", company_name="Widget Corp", sic="3571", period_end=date(2026, 6, 30),
                filed=date(2026, 8, 1), source="test", total_assets=1000.0, debt=100.0, cash_and_securities=150.0,
                receivables=100.0, revenue=500.0, interest_income=5.0, market_cap=2000.0,
                avg_market_cap_24m=1800.0, avg_market_cap_36m=1700.0)
    base.update(kw)
    return shariah.Fundamentals(**base)


def test_shariah_compliant_and_ratios():
    r = shariah.screen(_fund(), ShariahConfig(), date(2026, 11, 1))
    assert r.status == "Compliant"
    debt = next(x for x in r.ratios if x["name"] == "Interest-bearing debt")
    assert debt["value"] == pytest.approx(5.0) and debt["denominator"] == "market cap"


def test_shariah_fail_bank_and_debt():
    assert shariah.screen(_fund(sic="6022"), ShariahConfig(), date(2026, 11, 1)).status == "Non-Compliant"
    assert shariah.screen(_fund(debt=700.0), ShariahConfig(), date(2026, 11, 1)).status == "Non-Compliant"
    # MSCI uses total assets: debt 340/1000 = 34% fails, while AAOIFI 340/2000 = 17% passes
    f = _fund(debt=340.0)
    assert shariah.screen(f, ShariahConfig(methodology="AAOIFI"), date(2026, 11, 1)).status == "Compliant"
    assert shariah.screen(f, ShariahConfig(methodology="MSCI"), date(2026, 11, 1)).status == "Non-Compliant"


def test_shariah_needs_review_on_missing_or_stale():
    assert shariah.screen(_fund(interest_income=None), ShariahConfig(), date(2026, 11, 1)).status == "Needs Review"
    assert shariah.screen(_fund(filed=date(2025, 1, 1)), ShariahConfig(), date(2026, 11, 1)).status == "Needs Review"
    assert shariah.screen(_fund(sic=None), ShariahConfig(), date(2026, 11, 1)).status == "Needs Review"
    assert shariah.screen(_fund(company_name="Lucky Casino Inc"), ShariahConfig(), date(2026, 11, 1)).status == "Needs Review"


def test_shariah_mixed_revenue_review_and_purification():
    rv = shariah.BusinessReview("compliant", "admin", date(2026, 10, 1), "hotel without casino", prohibited_revenue_pct=4.5)
    r = shariah.screen(_fund(sic="7011"), ShariahConfig(), date(2026, 11, 1), rv)
    inc = next(x for x in r.ratios if x["name"] == "Non-compliant income")
    assert inc["value"] == pytest.approx(5.5) and r.status == "Non-Compliant"
    assert shariah.purification(1000, 3.2)["purification_amount"] == 32.0


# ---------------------------------------------------------------- ranking
def _crit(name, ok=True, score=50, hard=True):
    return CriterionResult(name, Outcome.PASS if ok else Outcome.FAIL, hard, score, "")


def _cand(t, score=50, shariah_status="Compliant", price=10.0, **fails):
    crit = {k: _crit(k, not fails.get(k), score, hard=k in ("options", "insider", "technical"))
            for k in ("options", "insider", "analyst", "technical", "float", "liquidity")}
    return ranking.Candidate(t, t, price, 1.0, shariah_status, crit)


def test_ranking_never_manufactures_and_shariah_is_display_only():
    cands = [_cand("A", 90), _cand("B", 80), _cand("C", 70, options=True), _cand("D", 99, shariah_status="Needs Review"),
             _cand("E", 95, price=100.0), _cand("F", 60, shariah_status="Non-Compliant"), _cand("G", 10, price=None),
             _cand("H", 50)]
    top, watch = ranking.rank(cands, ScreeningConfig())
    # Shariah status no longer filters; price < $100 and hard criteria still do; max 5
    assert [c.ticker for c in top] == ["D", "A", "B", "F", "H"]
    assert next(c for c in top if c.ticker == "F").shariah_status == "Non-Compliant"  # kept for the red highlight
    assert top[1].score == pytest.approx(90)
    assert {c.ticker for c in watch} == {"C"}
    top3, _ = ranking.rank(cands[:3], ScreeningConfig())
    assert [c.ticker for c in top3] == ["A", "B"]  # fewer qualify -> fewer returned, never padded


def test_missing_never_passes():
    c = _cand("A")
    c.criteria["options"] = CriterionResult("options", Outcome.MISSING, True, 0, "")
    top, _ = ranking.rank([c], ScreeningConfig())
    assert top == []


def test_config_guards():
    with pytest.raises(ValueError):
        ScreeningConfig(max_price=150)
    with pytest.raises(ValueError):
        ScreeningConfig(top_n=6)
    cfg = ScreeningConfig.model_validate({"options": {"max_put_call_ratio": 0.3, "hard": False}})
    assert set(cfg.relaxed_criteria()) == {"options", "options.max_put_call_ratio"}


# ---------------------------------------------------------------- position sizing
def test_position_long_risk_limited():
    r = calculate_position(PositionInput(account_balance=3000, risk_pct=2, entry=100, stop=95))
    # spreadsheet example: 3000 × 2% / 5 = 12 shares
    assert r["shares"] == 12 and r["max_loss"] == 60 and r["limited_by"] == "risk"


def test_position_allocation_limit_fees_short_fractional():
    r = calculate_position(PositionInput(account_balance=10_000, risk_pct=1, entry=50, stop=49.5, max_allocation_pct=20,
                                         target1=52))
    assert r["limited_by"] == "allocation" and r["shares"] == 40
    assert r["profit_target1"] == 80 and r["rr_target1"] == 4.0
    r = calculate_position(PositionInput(account_balance=10_000, risk_pct=1, entry=50, stop=52, direction="short",
                                         commission_per_trade=1, slippage_per_share=0.05, fractional=True, target1=46))
    assert r["risk_per_share"] == pytest.approx(2.10)
    assert r["shares"] == pytest.approx((100 - 2) / 2.10, rel=1e-5)
    assert r["max_loss"] == pytest.approx(100, abs=0.01)
    with pytest.raises(ValueError):
        PositionInput(account_balance=1000, risk_pct=1, entry=50, stop=55)


# ---------------------------------------------------------------- expectancy
def test_expectancy_formulas():
    r = calculate_expectancy(ExpectancyInput(win_rate_pct=40, avg_win=300, avg_loss=100, num_trades=50,
                                             risk_per_trade=100, starting_capital=5000))
    assert r["expectancy_per_trade"] == pytest.approx(60)
    assert r["expectancy_r"] == pytest.approx(0.6)
    assert r["profit_factor"] == pytest.approx(2.0)
    assert r["break_even_win_rate_pct"] == pytest.approx(25)
    assert 0 <= r["risk_of_ruin_pct"] < 5
    g = scenario_grid(100)
    assert next(x for x in g if x["win_rate_pct"] == 50 and x["rr"] == 1)["expectancy"] == 0


# ---------------------------------------------------------------- pnl
def test_pnl_fifo_long_short_dividends():
    fills = [Fill(1, "A", "buy", 10, 10, 1, date(2026, 1, 2)), Fill(2, "A", "buy", 10, 12, 1, date(2026, 1, 3)),
             Fill(3, "A", "sell", 15, 15, 1.5, date(2026, 2, 1)), Fill(4, "B", "sell", 5, 20, 0, date(2026, 2, 2)),
             Fill(5, "B", "buy", 5, 18, 0, date(2026, 2, 3))]
    s = summarize(fills, [Dividend("A", 2.5, date(2026, 1, 20))], {"A": 14})
    # A: 10×5 + 5×3 = 65 gross; fees: lot1 1.0 + half of lot2 0.5 + sell 15×0.1 = 3.0 ; B short +10
    assert s["realized_pnl"] == pytest.approx(65 - 3.0 + 10)
    op = s["open_positions"][0]
    assert op["quantity"] == 5 and op["unrealized"] == pytest.approx((14 - 12) * 5 - 0.5)
    assert s["win_rate_pct"] == 100 and s["dividends"] == 2.5
    assert s["periods"]["monthly"][0]["period"] == "2026-02"
