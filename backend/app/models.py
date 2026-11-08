"""Database schema (PostgreSQL + TimescaleDB; SQLite works for tests).

Immutability: ``recommendations`` rows are never updated in place. Any correction is written
as a ``recommendation_revisions`` row with the before/after payload, reason and author, and the
API returns the revision history with the recommendation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, event)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | admin
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSettings(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict] = mapped_column(Json)
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str | None] = mapped_column(Text)  # each change is a new row = audit trail


class Security(Base):
    __tablename__ = "securities"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    cik: Mapped[str | None] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str | None] = mapped_column(String(32))
    sic: Mapped[str | None] = mapped_column(String(8))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyBar(Base):
    __tablename__ = "daily_bars"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    session: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OptionsDaily(Base):
    __tablename__ = "options_daily"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    session: Mapped[date] = mapped_column(Date, primary_key=True)
    call_volume: Mapped[int | None] = mapped_column(Integer)
    put_volume: Mapped[int | None] = mapped_column(Integer)
    call_oi: Mapped[int | None] = mapped_column(Integer)
    put_oi: Mapped[int | None] = mapped_column(Integer)
    iv30: Mapped[float | None] = mapped_column(Float)
    provider_iv_rank: Mapped[float | None] = mapped_column(Float)
    stock_price: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InsiderTransaction(Base):
    __tablename__ = "insider_transactions"
    __table_args__ = (UniqueConstraint("accession", "txn_index", "owner_cik", name="uq_insider_txn"),
                      Index("ix_insider_ticker_date", "ticker", "transaction_date"))
    id: Mapped[int] = mapped_column(primary_key=True)
    accession: Mapped[str] = mapped_column(String(25))
    txn_index: Mapped[int] = mapped_column(Integer)
    owner_cik: Mapped[str] = mapped_column(String(10))
    issuer_cik: Mapped[str] = mapped_column(String(10))
    ticker: Mapped[str] = mapped_column(String(16))
    owner_name: Mapped[str] = mapped_column(String(255))
    officer_title: Mapped[str] = mapped_column(String(255), default="")
    is_director: Mapped[bool] = mapped_column(Boolean, default=False)
    is_officer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ten_pct_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    transaction_date: Mapped[date] = mapped_column(Date)
    filing_date: Mapped[date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(String(4))
    acquired_disposed: Mapped[str] = mapped_column(String(2))
    shares: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    shares_owned_after: Mapped[float | None] = mapped_column(Float)
    direct_indirect: Mapped[str] = mapped_column(String(2))
    security_title: Mapped[str] = mapped_column(String(255), default="")
    is_amendment: Mapped[bool] = mapped_column(Boolean, default=False)
    is_derivative: Mapped[bool] = mapped_column(Boolean, default=False)
    footnotes: Mapped[str] = mapped_column(Text, default="")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FundamentalsSnapshot(Base):
    __tablename__ = "fundamentals_snapshots"
    __table_args__ = (UniqueConstraint("ticker", "as_of", name="uq_fund_ticker_asof"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    filed: Mapped[date | None] = mapped_column(Date)
    data: Mapped[dict] = mapped_column(Json)
    source: Mapped[str] = mapped_column(String(255))


class OptionsImportBatch(Base):
    """One uploaded unusual-options CSV. Rows are immutable; a newer upload supersedes older ones."""

    __tablename__ = "options_import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    session_date: Mapped[date] = mapped_column(Date, index=True)
    row_count: Mapped[int] = mapped_column(Integer)
    percent_interpretation: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(Json, default=dict)
    imported_by: Mapped[str] = mapped_column(String(255))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rows: Mapped[list["OptionsImportRow"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class OptionsImportRow(Base):
    __tablename__ = "options_import_rows"
    __table_args__ = (UniqueConstraint("batch_id", "symbol", name="uq_options_import_row"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("options_import_batches.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    price: Mapped[float | None] = mapped_column(Float)
    change: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    iv_rank: Mapped[float | None] = mapped_column(Float)
    options_volume: Mapped[int | None] = mapped_column(Integer)
    vol_change_1m_pct: Mapped[float | None] = mapped_column(Float)
    put_volume: Mapped[int | None] = mapped_column(Integer)
    call_volume: Mapped[int | None] = mapped_column(Integer)
    pc_ratio: Mapped[float | None] = mapped_column(Float)
    batch: Mapped[OptionsImportBatch] = relationship(back_populates="rows")


class ProcessedFiling(Base):
    """Form 4 filings already fetched and parsed (including ones with no transactions)."""

    __tablename__ = "processed_filings"
    accession: Mapped[str] = mapped_column(String(25), primary_key=True)
    filed: Mapped[date] = mapped_column(Date, index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShariahScreen(Base):
    __tablename__ = "shariah_screens"
    __table_args__ = (Index("ix_shariah_ticker_date", "ticker", "screened_on"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16))
    screened_on: Mapped[date] = mapped_column(Date)
    methodology: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict] = mapped_column(Json)


class BusinessReviewRow(Base):
    __tablename__ = "business_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(255))
    reviewed_on: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text)
    prohibited_revenue_pct: Mapped[float | None] = mapped_column(Float)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # premarket | open | intraday | close | manual
    session_date: Mapped[date] = mapped_column(Date, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | partial | failed
    config_snapshot: Mapped[dict] = mapped_column(Json)
    relaxed_criteria: Mapped[list] = mapped_column(Json, default=list)
    funnel: Mapped[dict] = mapped_column(Json, default=dict)
    errors: Mapped[list] = mapped_column(Json, default=list)
    triggered_by: Mapped[str] = mapped_column(String(255), default="scheduler")
    results: Mapped[list["ScanResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class ScanResult(Base):
    __tablename__ = "scan_results"
    __table_args__ = (UniqueConstraint("run_id", "ticker", name="uq_scan_result"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(16))
    company: Mapped[str] = mapped_column(String(255))
    eligible: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    shariah_status: Mapped[str] = mapped_column(String(20))
    failures: Mapped[list] = mapped_column(Json)
    criteria: Mapped[dict] = mapped_column(Json)
    extra: Mapped[dict] = mapped_column(Json, default=dict)
    run: Mapped[ScanRun] = relationship(back_populates="results")


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("run_id", "rank", name="uq_rec_run_rank"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    kind: Mapped[str] = mapped_column(String(20))
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)  # closing scan = final daily Top 5
    rank: Mapped[int] = mapped_column(Integer)
    ticker: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    price_at_rec: Mapped[float] = mapped_column(Float)
    entry: Mapped[float | None] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float)
    target1: Mapped[float | None] = mapped_column(Float)
    target2: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecommendationRevision(Base):
    __tablename__ = "recommendation_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    changed_by: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text)
    before: Mapped[dict] = mapped_column(Json)
    after: Mapped[dict] = mapped_column(Json)


class JournalFill(Base):
    __tablename__ = "journal_fills"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0)
    trade_date: Mapped[date] = mapped_column(Date)
    stop: Mapped[float | None] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"))


class JournalDividend(Base):
    __tablename__ = "journal_dividends"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Float)
    pay_date: Mapped[date] = mapped_column(Date)


class ProviderHealth(Base):
    __tablename__ = "provider_health"
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    __table_args__ = (UniqueConstraint("user_id", "session_date", "ticker", "channel", name="uq_notify_once"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    session_date: Mapped[date] = mapped_column(Date)
    ticker: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(Text)


@event.listens_for(Recommendation, "before_update")
def _block_rec_update(mapper, conn, target):  # pragma: no cover - guard
    raise RuntimeError("Recommendations are immutable; write a RecommendationRevision instead")


class EarningsEvent(Base):
    """Upcoming/recent earnings dates from the earnings-calendar provider (refreshed daily)."""

    __tablename__ = "earnings_events"
    __table_args__ = (UniqueConstraint("symbol", "report_date", name="uq_earnings_symbol_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    report_date: Mapped[date] = mapped_column(Date, index=True)
    fiscal_date_ending: Mapped[str | None] = mapped_column(String(10))
    eps_estimate: Mapped[float | None] = mapped_column(Float)
    time_of_day: Mapped[str | None] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalystSnapshot(Base):
    """Cached analyst rating counts per symbol (used by list screens; refreshed when older than a day)."""

    __tablename__ = "analyst_snapshots"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    strong_buy: Mapped[int | None] = mapped_column(Integer)
    buy: Mapped[int | None] = mapped_column(Integer)
    hold: Mapped[int | None] = mapped_column(Integer)
    sell: Mapped[int | None] = mapped_column(Integer)
    strong_sell: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MonthlyClose(Base):
    """Split/dividend-adjusted month-end close, for seasonality analysis."""

    __tablename__ = "monthly_closes"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)  # first day of the month
    close: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SeasonalityStat(Base):
    """Pre-computed per-stock, per-calendar-month return statistics (rebuilt after each price refresh)."""

    __tablename__ = "seasonality_stats"
    __table_args__ = (Index("ix_seasonality_month_avg", "month", "avg"),)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)  # 1..12
    years: Mapped[int] = mapped_column(Integer)
    avg: Mapped[float] = mapped_column(Float)
    median: Mapped[float] = mapped_column(Float)
    win_rate: Mapped[float] = mapped_column(Float)
    best: Mapped[float] = mapped_column(Float)
    best_year: Mapped[int] = mapped_column(Integer)
    worst: Mapped[float] = mapped_column(Float)
    worst_year: Mapped[int] = mapped_column(Integer)
    last_close: Mapped[float | None] = mapped_column(Float)
    # bullish view: years the month closed up; bearish view: years it closed down (changes in %)
    pos_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pos_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    pos_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    pos_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    neg_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    neg_max: Mapped[float | None] = mapped_column(Float, nullable=True)  # largest fall (most negative)
    neg_min: Mapped[float | None] = mapped_column(Float, nullable=True)  # smallest fall
    neg_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketMonth(Base):
    """A month whose whole-market month-end closes have been loaded (one grouped request)."""

    __tablename__ = "market_months"
    month: Mapped[date] = mapped_column(Date, primary_key=True)  # first day of the month
    session: Mapped[date] = mapped_column(Date)  # last trading session used
    symbols: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CustomWatchItem(Base):
    """A stock the user added to their own watchlist."""

    __tablename__ = "custom_watchlist"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_custom_watch_user_symbol"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PriceSnapshot(Base):
    """Recent daily bars and derived levels (52-week low/high, 50 EMA) per stock, computed in the browser
    from the imported OHLCV CSV. Used by the bounce screens and the custom watchlist."""

    __tablename__ = "price_snapshots"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[float] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(Json)
    # Bounce-screen figures computed from ``data`` when it is saved: the screen reads only these (a year of
    # daily bars for every stock is far too much to send from the database on each view).
    metrics: Mapped[dict | None] = mapped_column(Json, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SharesOutstanding(Base):
    """Shares outstanding per ticker (for "% of outstanding acquired" on insider purchases)."""

    __tablename__ = "shares_outstanding"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    shares: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = looked up, not available
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketDay(Base):
    """A trading session whose whole-market daily bars were folded into ``price_snapshots``."""

    __tablename__ = "market_days"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    symbols: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SentiSenseRatingRow(Base):
    """Cached SentiSense Rating per stock (recomputed by SentiSense once per trading day)."""

    __tablename__ = "sentisense_ratings"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    rated: Mapped[bool] = mapped_column(Boolean, default=False)
    letter: Mapped[str | None] = mapped_column(String(2), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    penalty_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    analyst_favorable_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # "screener" | "rating"
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IndexMember(Base):
    """Index constituents (S&P 500, Nasdaq-100, Dow 30) for the Seasonality index filter; refreshed weekly."""

    __tablename__ = "index_members"
    index_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SeasonalitySymbol(Base):
    """Stocks whose monthly history was loaded from the price API (resumable whole-market load)."""

    __tablename__ = "seasonality_symbols"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    months: Mapped[int] = mapped_column(Integer)
    last_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecCompany(Base):
    """SEC registrant profile (industry SIC code) from data.sec.gov submissions."""

    __tablename__ = "sec_companies"
    cik: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    sic: Mapped[str | None] = mapped_column(String(8))
    sic_description: Mapped[str | None] = mapped_column(String(255))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecFact(Base):
    """One XBRL fact per company and tag from the SEC frames API (balance-sheet facts at the company's
    latest balance-sheet date; annual facts from the latest fiscal year)."""

    __tablename__ = "sec_facts"
    cik: Mapped[str] = mapped_column(String(10), primary_key=True)
    tag: Mapped[str] = mapped_column(String(128), primary_key=True)
    end: Mapped[date] = mapped_column(Date)
    start: Mapped[date | None] = mapped_column(Date)
    val: Mapped[float] = mapped_column(Float)
    accn: Mapped[str | None] = mapped_column(String(32))
    frame: Mapped[str | None] = mapped_column(String(16))


class SecFrameLoad(Base):
    """SEC frames already downloaded (resumable loading)."""

    __tablename__ = "sec_frames_loaded"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShariahResultRow(Base):
    """Latest internal Shariah classification per stock, one status column per methodology."""

    __tablename__ = "shariah_results"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    cik: Mapped[str | None] = mapped_column(String(10))
    aaoifi: Mapped[str] = mapped_column(String(16), index=True)
    ftse: Mapped[str] = mapped_column(String(16), index=True)
    data: Mapped[dict] = mapped_column(Json)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShariahRules(Base):
    """Administrator-entered methodology rules (from the official documents). Each save is a new row."""

    __tablename__ = "shariah_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    methodology: Mapped[str] = mapped_column(String(16), index=True)
    rules: Mapped[dict] = mapped_column(Json)
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobClaim(Base):
    """One row per job run that must happen only once (e.g. "daily-scan:2026-11-08"): the database's unique
    key decides which of two simultaneous requests runs it."""

    __tablename__ = "job_claims"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
