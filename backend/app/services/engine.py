"""Scan engine: ingest → funnel → evaluate → rank → persist → notify.

Funnel (keeps market-wide scanning within API budgets):
  Stage 1 (all optionable US tickers, 1–2 provider requests total): price < $100 and the
      options criteria from stored daily options history.
  Stage 2 (survivors only): insider (from stored SEC Form 4), Shariah (SEC XBRL), technicals
      (daily bars), analyst, float, liquidity.
If options is configured as a preference (not hard), stage 1 keeps the best
``STAGE2_MAX`` tickers by options score instead of only passes; this is recorded in the run.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import market_calendar as mc
from app.config import ScreeningConfig, get_settings
from app.models import (AppSettings, BusinessReviewRow, DailyBar, InsiderTransaction, OptionsDaily, ProviderHealth,
                        Recommendation, ScanResult, ScanRun, Security, ShariahScreen)
from app.providers.http import ProviderError
from app.screening import analyst as an, float_liquidity as fl, insider as ins, options as op, shariah as sh
from app.screening import technicals as tech
from app.screening.ranking import Candidate, rank
from app.screening.types import CriterionResult, Outcome
from app.services.datasource import DataSource

log = logging.getLogger(__name__)
STAGE2_MAX = 150
HISTORY_SESSIONS = 260  # options history kept for IV rank (252) + baseline (41)


def load_config(db: Session) -> ScreeningConfig:
    row = db.scalars(select(AppSettings).order_by(AppSettings.id.desc()).limit(1)).first()
    return ScreeningConfig.model_validate(row.config) if row else ScreeningConfig()


def _health(db: Session, provider: str, ok: bool, err: str | None = None):
    h = db.get(ProviderHealth, provider) or ProviderHealth(provider=provider, calls=0, failures=0)
    h.calls = (h.calls or 0) + 1
    now = datetime.now(timezone.utc)
    if ok:
        h.last_success_at = now
    else:
        h.failures = (h.failures or 0) + 1
        h.last_error_at, h.last_error = now, (err or "")[:2000]
    db.merge(h)


class ScanEngine:
    def __init__(self, db: Session, ds: DataSource, cfg: ScreeningConfig | None = None):
        self.db, self.ds = db, ds
        self.cfg = cfg or load_config(db)
        self.errors: list[str] = []
        self.persist = True  # False in backtests: no side-effect rows

    async def _call(self, provider: str, coro):
        try:
            out = await coro
            _health(self.db, provider, True)
            return out
        except ProviderError as e:
            self.errors.append(str(e))
            _health(self.db, provider, False, str(e))
            return None

    # ------------------------------------------------------------ ingestion
    async def refresh_universe(self) -> int:
        rows = await self._call("sec_edgar", self.ds.universe())
        if not rows:
            return 0
        for r in rows:
            self.db.merge(Security(ticker=r["ticker"], cik=r["cik"], name=r["name"], exchange=r["exchange"]))
        self.db.commit()
        return len(rows)

    async def ingest_options(self, sessions: list[date]) -> None:
        """Fetch missing sessions newest-first. ``BACKFILL_SESSIONS_PER_RUN`` caps how many are fetched
        in one run so a serverless invocation finishes in time; later runs continue the backfill."""
        have = set(self.db.scalars(select(OptionsDaily.session).distinct()).all())
        cap = get_settings().backfill_sessions_per_run or len(sessions)
        fetched = 0
        for d in sorted(sessions, reverse=True):
            if d in have and d != sessions[-1]:
                continue
            if fetched >= cap:
                self.errors.append(f"options history backfill incomplete: continuing next run ({len(have)} sessions stored)")
                break
            fetched += 1
            res = await self._call("orats", self.ds.options_day(d))
            if not res:
                continue
            rows, prov = res
            self.db.execute(delete(OptionsDaily).where(OptionsDaily.session == d))
            self.db.add_all(OptionsDaily(ticker=r.ticker, session=d, call_volume=r.call_volume, put_volume=r.put_volume,
                                         call_oi=r.call_oi, put_oi=r.put_oi, iv30=r.iv30, provider_iv_rank=r.provider_iv_rank,
                                         stock_price=r.stock_price, provider=prov.provider) for r in rows)
            self.db.commit()

    async def ingest_insiders(self, days: list[date]) -> None:
        for d in days:
            res = await self._call("sec_edgar", self.ds.insider_filings(d))
            if not res:
                continue
            txns, _ = res
            existing = set(self.db.execute(select(InsiderTransaction.accession, InsiderTransaction.txn_index,
                                                  InsiderTransaction.owner_cik)
                                           .where(InsiderTransaction.filing_date == d)).all())
            for t in txns:
                if (t.accession, t.txn_index, t.owner_cik) in existing:
                    continue  # duplicate detection on re-ingestion
                existing.add((t.accession, t.txn_index, t.owner_cik))
                self.db.add(InsiderTransaction(**{k: getattr(t, k) for k in (
                    "accession", "txn_index", "owner_cik", "issuer_cik", "ticker", "owner_name", "officer_title",
                    "is_director", "is_officer", "is_ten_pct_owner", "transaction_date", "filing_date", "code",
                    "acquired_disposed", "shares", "price", "shares_owned_after", "direct_indirect", "security_title",
                    "is_amendment", "is_derivative", "footnotes")}))
            self.db.commit()

    async def load_bars(self, ticker: str, as_of: date) -> pd.DataFrame | None:
        start = as_of - timedelta(days=int(self.cfg.technical.history_days * 1.5))
        stored = self.db.scalars(select(DailyBar).where(DailyBar.ticker == ticker, DailyBar.session >= start,
                                                        DailyBar.session <= as_of).order_by(DailyBar.session)).all()
        if not stored or stored[-1].session < mc.previous_sessions(as_of, 1)[-1]:
            res = await self._call("massive", self.ds.bars(ticker, start, as_of))
            if res and len(res[0]):
                df, prov = res
                for ts, r in df.iterrows():
                    self.db.merge(DailyBar(ticker=ticker, session=ts.date(), open=r.open, high=r.high, low=r.low,
                                           close=r.close, volume=r.volume, provider=prov.provider))
                self.db.commit()
                return df[df.index <= pd.Timestamp(as_of)]
        if not stored:
            return None
        return pd.DataFrame([{"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
                             for b in stored], index=pd.DatetimeIndex([pd.Timestamp(b.session) for b in stored]))

    # ------------------------------------------------------------ evaluation
    def options_snapshot(self, ticker: str, as_of: date) -> op.OptionsSnapshot:
        rows = self.db.scalars(select(OptionsDaily).where(OptionsDaily.ticker == ticker, OptionsDaily.session <= as_of)
                               .order_by(OptionsDaily.session.desc()).limit(HISTORY_SESSIONS)).all()[::-1]
        hist = [op.DailyOptionsVolume(r.session, r.call_volume, r.put_volume) for r in rows
                if r.call_volume is not None and r.put_volume is not None]
        if hist and hist[-1].session != as_of:
            hist = []  # no data for the evaluation session -> missing, never stale-pass
        last = rows[-1] if rows else None
        return op.OptionsSnapshot(ticker, last.stock_price if last else None, hist,
                                  [r.iv30 for r in rows if r.iv30], last.provider_iv_rank if last else None)

    def insider_input(self, ticker: str, as_of: date, so, so_date) -> ins.InsiderInput:
        start = as_of - timedelta(days=self.cfg.insider.lookback_days)
        rows = self.db.scalars(select(InsiderTransaction).where(InsiderTransaction.ticker == ticker,
                                                                InsiderTransaction.transaction_date >= start)).all()
        txns = [ins.InsiderTxn(**{c: getattr(r, c) for c in ins.InsiderTxn.__dataclass_fields__}) for r in rows]
        return ins.InsiderInput(ticker, as_of, txns, so, so_date)

    def business_review(self, ticker: str, as_of: date) -> sh.BusinessReview | None:
        r = self.db.scalars(select(BusinessReviewRow).where(BusinessReviewRow.ticker == ticker,
                                                            BusinessReviewRow.reviewed_on <= as_of,
                                                            BusinessReviewRow.superseded.is_(False))
                            .order_by(BusinessReviewRow.id.desc())).first()
        return sh.BusinessReview(r.status, r.reviewer, r.reviewed_on, r.notes, r.prohibited_revenue_pct) if r else None

    async def evaluate(self, sec: Security, as_of: date, opt_result: CriterionResult, snap: op.OptionsSnapshot,
                       live: bool) -> Candidate:
        cfg = self.cfg
        t = sec.ticker
        provenance: dict = {}

        quote = None
        if live:
            res = await self._call("massive", self.ds.quote(t))
            if res:
                quote, provenance["quote"] = res[0], res[1].to_dict()
        bars = await self.load_bars(t, as_of)
        price = (quote or {}).get("price") or (float(bars["close"].iloc[-1]) if bars is not None and len(bars) else None)
        day_chg = (quote or {}).get("day_change_pct")
        if day_chg is None and bars is not None and len(bars) > 1:
            day_chg = (bars["close"].iloc[-1] / bars["close"].iloc[-2] - 1) * 100

        # Shariah + shares outstanding (SEC)
        mcap = avg24 = avg36 = None
        so = so_date = None
        shariah_res: sh.ShariahResult | None = None
        if sec.cik:
            fres = await self._call("sec_edgar", self.ds.fundamentals(t, sec.cik, as_of, None, None, None))
            if fres:
                f, so, prov = fres
                provenance["fundamentals"] = prov.to_dict()
                if so and price:
                    mcap = so * price
                    if bars is not None and len(bars):
                        m = bars["close"].resample("ME").last()
                        avg24 = float(m.tail(24).mean() * so) if len(m) >= 24 else None
                        avg36 = float(m.tail(36).mean() * so) if len(m) >= 36 else None
                f.market_cap, f.avg_market_cap_24m, f.avg_market_cap_36m = mcap, avg24, avg36
                so_date = f.shares_outstanding_date  # SEC report date, not the scan date
                shariah_res = sh.screen(f, cfg.shariah, as_of, self.business_review(t, as_of))
        if shariah_res is None:
            shariah_res = sh.ShariahResult("Needs Review", cfg.shariah.methodology,
                                           sh.METHODOLOGIES[cfg.shariah.methodology]["label"], as_of, {}, [],
                                           ["Financial data unavailable"], "unavailable", None)
        if self.persist:
            self.db.add(ShariahScreen(ticker=t, screened_on=as_of, methodology=cfg.shariah.methodology,
                                      status=shariah_res.status, result=_jsonable(shariah_res.to_dict())))

        crit: dict[str, CriterionResult] = {"options": opt_result}
        crit["insider"] = ins.evaluate_insider(self.insider_input(t, as_of, so, so_date), cfg.insider)

        a_res = await self._call("fmp", self.ds.analyst(t))
        if a_res:
            ad, prov = a_res
            provenance["analyst"] = prov.to_dict()
            tg = ad.targets or {}
            crit["analyst"] = an.evaluate_analyst(an.AnalystInput(t, as_of, price, ad.counts, tg.get("avg"), tg.get("high"),
                                                                  tg.get("low"), tg.get("median"), ad.actions), cfg.analyst)
        else:
            crit["analyst"] = CriterionResult("analyst", Outcome.MISSING, cfg.analyst.hard, 0, "Analyst data unavailable.")

        crit["technical"] = tech.evaluate_technical(bars, cfg.technical)

        fres = await self._call("fmp", self.ds.float_info(t))
        finfo = fres[0] if fres else {}
        if fres:
            provenance["float"] = fres[1].to_dict()
        tm = crit["technical"].metrics
        crit["float"] = fl.evaluate_float(fl.FloatInput(
            finfo.get("outstanding_shares") or so, finfo.get("float_shares"), None,
            None, finfo.get("short_interest"), finfo.get("days_to_cover"),
            float(bars["volume"].tail(20).mean()) if bars is not None and len(bars) else None), cfg.float_)
        crit["liquidity"] = fl.evaluate_liquidity(fl.LiquidityInput(
            tm.get("avg_dollar_volume20"), tm.get("relative_volume"), (quote or {}).get("bid"), (quote or {}).get("ask"),
            None, day_chg, crit["float"].metrics.get("float_ratio_pct")), cfg.liquidity)

        extra = {"shariah": shariah_res.to_dict(), "provenance": provenance, "exchange": sec.exchange,
                 "trade_plan": tm.get("trade_plan"), "market_cap": mcap,
                 "volume": float(bars["volume"].iloc[-1]) if bars is not None and len(bars) else None}
        return Candidate(t, sec.name, price, day_chg, shariah_res.status, crit, extra)

    async def screen(self, as_of: date, live: bool, include_inactive: bool = False):
        """Point-in-time screen for ``as_of``: returns (candidates, top, watchlist, funnel)."""
        cfg = self.cfg
        q = select(Security) if include_inactive else select(Security).where(Security.active.is_(True))
        universe = {s.ticker: s for s in self.db.scalars(q).all()}
        day_rows = self.db.scalars(select(OptionsDaily).where(OptionsDaily.session == as_of)).all()
        stage1, priced_out = [], 0
        for r in day_rows:
            sec = universe.get(r.ticker)
            if not sec:
                continue
            if r.stock_price is None or not r.stock_price < cfg.max_price:
                priced_out += 1
                continue
            snap = self.options_snapshot(r.ticker, as_of)
            stage1.append((sec, op.evaluate_options(snap, cfg.options), snap))
        if cfg.options.hard:
            survivors = [s for s in stage1 if s[1].passed]
        else:
            survivors = sorted(stage1, key=lambda s: -s[1].score)[:STAGE2_MAX]
        funnel = {"universe": len(universe), "options_rows": len(day_rows), "price_filtered_out": priced_out,
                      "stage1_evaluated": len(stage1), "stage1_options_pass": sum(s[1].passed for s in stage1),
                      "stage2": len(survivors)}

        cands: list[Candidate] = []
        for sec, ores, snap in survivors:
            c = await self.evaluate(sec, as_of, ores, snap, live)
            if cfg.options.hard and live:
                cres = await self._call("massive", self.ds.option_contracts(sec.ticker))
                if cres:
                    contracts, spot, _ = cres
                    snap.contracts, snap.spot = contracts, spot or snap.spot
                    c.criteria["options"] = op.evaluate_options(snap, cfg.options)
            cands.append(c)
        top, watch = rank(cands, cfg)
        return cands, top, watch, funnel

    # ------------------------------------------------------------ run
    async def run(self, kind: str = "manual", as_of: date | None = None, triggered_by: str = "scheduler",
                  live: bool = True, ingest: bool = True) -> ScanRun:
        now = datetime.now(timezone.utc)
        as_of = as_of or mc.last_completed_session(now)
        cfg = self.cfg
        run = ScanRun(kind=kind, session_date=as_of, config_snapshot=cfg.model_dump(by_alias=True),
                      relaxed_criteria=cfg.relaxed_criteria(), triggered_by=triggered_by, funnel={}, errors=[])
        self.db.add(run)
        self.db.commit()
        try:
            if ingest:
                if not self.db.scalars(select(Security).limit(1)).first():
                    await self.refresh_universe()
                await self.ingest_options(mc.previous_sessions(as_of, HISTORY_SESSIONS if not self._has_history() else 3))
                await self.ingest_insiders([as_of - timedelta(days=i) for i in range(0, 4)])

            cands, top, watch, funnel = await self.screen(as_of, live)
            run.funnel = funnel
            watch_set = {c.ticker for c in watch}
            for c in cands:
                self.db.add(ScanResult(run_id=run.id, ticker=c.ticker, company=c.company, eligible=c.eligible,
                                       score=c.score, price=c.price, shariah_status=c.shariah_status,
                                       failures=c.failures,
                                       criteria=_jsonable({k: v.to_dict() for k, v in c.criteria.items()}),
                                       extra=_jsonable(c.extra | {"day_change_pct": c.day_change_pct,
                                                                  "watchlist": c.ticker in watch_set})))
            for i, c in enumerate(top, 1):
                plan = c.extra.get("trade_plan") or {}
                self.db.add(Recommendation(session_date=as_of, run_id=run.id, kind=kind, is_final=(kind == "close"),
                                           rank=i, ticker=c.ticker, score=c.score, price_at_rec=c.price,
                                           entry=plan.get("entry"), stop=plan.get("stop_loss"),
                                           target1=plan.get("target1"), target2=plan.get("target2"),
                                           payload=_jsonable({"company": c.company, "criteria": {k: v.to_dict() for k, v in c.criteria.items()},
                                                              "extra": c.extra, "day_change_pct": c.day_change_pct})))
            run.funnel = run.funnel | {"evaluated": len(cands), "eligible": sum(c.eligible for c in cands),
                                       "top": [c.ticker for c in top], "watchlist": [c.ticker for c in watch]}
            run.status = "partial" if self.errors else "ok"
        except Exception as e:  # record and re-raise; a failed run never produces recommendations
            log.exception("scan failed")
            self.db.rollback()
            run.status = "failed"
            self.errors.append(repr(e))
            self.db.execute(delete(Recommendation).where(Recommendation.run_id == run.id))
        run.errors = self.errors[:200]
        run.finished_at = datetime.now(timezone.utc)
        self.db.merge(run)
        self.db.commit()
        return run

    def _has_history(self) -> bool:
        # Need ~252 sessions for a 52-week IV rank, not just the 41 used by the volume baseline;
        # otherwise a partial first backfill would never be completed.
        n = self.db.query(OptionsDaily.session).distinct().count()
        return n >= HISTORY_SESSIONS - 5


def _jsonable(o):
    import math

    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    return o
