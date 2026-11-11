"""Application configuration.

Two layers:
  * ``Settings`` — process/environment configuration (secrets, URLs). Read from env vars only.
    API keys live here and are never sent to the frontend.
  * ``ScreeningConfig`` — business thresholds and weights. Defaults are below; an administrator
    can override them from the admin settings page. Every scan stores a full snapshot of the
    config it used, so a recommendation can always be traced back to the exact thresholds.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://stockintel:stockintel@localhost:5432/stockintel"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me"
    jwt_ttl_minutes: int = 60 * 12
    # Personal use: no login/register; every request acts as the owner (admin). Set to false for multi-user.
    single_user_mode: bool = True
    cors_origins: str = "http://localhost:3000"

    # SEC requires a descriptive User-Agent with contact details: https://www.sec.gov/os/accessing-edgar-data
    sec_user_agent: str = ""
    insider_source: str = "sec"  # "sec" (EDGAR Form 4 daily index, free) or "openinsider" (screener, unreliable)

    orats_api_key: str = ""
    massive_api_key: str = ""  # Massive.com (formerly Polygon.io); Polygon keys keep working
    massive_base_url: str = "https://api.massive.com"
    fmp_api_key: str = ""
    alphavantage_api_key: str = ""  # historical prices (fallback), earnings calendar, analyst fallback
    alpaca_api_key_id: str = ""  # stock prices (first choice)
    alpaca_api_secret_key: str = ""
    alpaca_data_feed: str = "iex"  # "iex" (free plan) or "sip" (paid)
    finnhub_api_key: str = ""  # analyst ratings (first choice), stock prices (fallback)
    tiingo_api_key: str = ""  # historical prices (first choice)
    sentisense_api_key: str = ""  # SentiSense Rating + AI insights (stock panel)
    fred_api_key: str = ""  # FRED (St. Louis Fed) — Dashboard macro backdrop; free key

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    telegram_bot_token: str = ""

    http_cache_ttl_seconds: int = 300

    # Serverless (Vercel) settings
    database_url_unpooled: str = ""  # set by the Neon integration; used for migrations only
    cron_secret: str = ""  # Vercel Cron sends "Authorization: Bearer <CRON_SECRET>"
    backfill_sessions_per_run: int = 0  # 0 = unlimited; cap per invocation so a scan fits maxDuration
    insider_filings_per_run: int = 0  # 0 = unlimited; new Form 4 filings fetched per scan
    auto_migrate: bool = False  # run Alembic migrations at startup (Vercel has no release step)
    # Heavy data jobs run on GitHub Actions (.github/workflows/data-jobs.yml), not on Vercel. A fine-grained
    # token with "Actions: read and write" on the repository lets the app start a job (e.g. a scan after a
    # CSV import) straight away.
    # Codespaces secret names cannot start with "GITHUB", so SCAN_DISPATCH_TOKEN is accepted too.
    github_dispatch_token: str = Field("", validation_alias=AliasChoices("GITHUB_DISPATCH_TOKEN", "SCAN_DISPATCH_TOKEN",
                                                                         "github_dispatch_token"))
    github_repo: str = "sufyanuk/Stock-Screener"
    github_workflow: str = "data-jobs.yml"
    github_ref: str = "main"

    @model_validator(mode="after")
    def _guard_production(self) -> "Settings":
        if self.app_env == "production" and self.jwt_secret in ("", "change-me"):
            raise ValueError("JWT_SECRET must be set to a strong random value in production")
        return self

    @property
    def sqlalchemy_url(self) -> str:
        """URL for normal requests: the pooled endpoint (Neon's PgBouncer) when available, so bursts of
        serverless requests share a few server connections instead of opening many direct ones.
        Normalised (``postgres://`` / ``postgresql://``) to the psycopg 3 driver."""
        return _psycopg(self.database_url or self.database_url_unpooled)

    @property
    def sqlalchemy_url_direct(self) -> str:
        """URL for migrations: the direct endpoint (advisory locks need a session-level connection)."""
        return _psycopg(self.database_url_unpooled or self.database_url)


def _psycopg(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


class Criterion(BaseModel):
    """A configurable criterion. ``hard`` = eligibility filter; otherwise it only affects score."""

    hard: bool = True


class OptionsCriteria(Criterion):
    # "csv": latest uploaded unusual-options CSV (default); "orats": ORATS API with stored history
    source: Literal["csv", "orats"] = "csv"
    min_volume_change_pct: float = 300.0  # current vs baseline, strictly >= this
    baseline_method: Literal["trailing_20d_avg", "single_session_1m_ago"] = "trailing_20d_avg"
    min_baseline_volume: int = 100  # below this the % change is statistically meaningless -> fails
    min_iv_rank: float = 20.0  # strictly greater than
    iv_rank_window_days: int = 252  # 52 weeks of sessions
    min_iv_history_obs: int = 200
    max_put_call_ratio: float = 0.20  # strictly less than
    large_trade_min_premium: float = 100_000.0
    large_trade_min_contracts: int = 500
    atm_band_pct: float = 2.0


class InsiderCriteria(Criterion):
    min_pct_outstanding: float = 1.0  # strictly greater than
    basis: Literal["aggregate_unique", "single_transaction"] = "aggregate_unique"
    lookback_days: Literal[7, 30, 60, 90] = 30


class AnalystCriteria(Criterion):
    hard: bool = False  # ranking factor by default
    min_coverage: int = 3


class TechnicalCriteria(Criterion):
    support_proximity_pct: float = 3.0  # default "near support" eligibility threshold
    strong_pct: float = 1.0
    near_pct: float = 3.0
    approaching_pct: float = 5.0
    swing_window: int = 5
    zone_tolerance_pct: float = 1.5
    min_zone_touches: int = 2
    breakdown_pct: float = 2.0
    history_days: int = 400


class FloatCriteria(Criterion):
    hard: bool = False  # low float improves ranking; it never overrides other criteria
    very_low_pct: float = 10.0
    low_pct: float = 20.0
    moderate_pct: float = 40.0


class LiquidityCriteria(Criterion):
    hard: bool = False
    min_avg_dollar_volume: float = 1_000_000.0
    max_spread_pct: float = 2.0


class Weights(BaseModel):
    options: float = 25
    insider: float = 20
    analyst: float = 15
    technical: float = 20
    float_: float = Field(10, alias="float")
    liquidity: float = 10

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _sum_100(self) -> "Weights":
        total = self.options + self.insider + self.analyst + self.technical + self.float_ + self.liquidity
        if abs(total - 100) > 1e-6:
            raise ValueError(f"weights must sum to 100 (got {total})")
        return self


class ShariahConfig(BaseModel):
    # Methodology used for the Shariah colour across the app (internal engine: AAOIFI, FTSE).
    # FTSE is the default because its rules are built in from the official ground rules; AAOIFI
    # classifies once its rules are entered from the official document. "DJIM", "MSCI" and "SP" are kept
    # only so older saved settings still load (no longer supported: tickers are not coloured).
    methodology: Literal["AAOIFI", "DJIM", "MSCI", "SP", "FTSE"] = "FTSE"
    max_financials_age_days: int = 456  # ~15 months; older -> Needs Review


class ScreeningConfig(BaseModel):
    # Mandatory, non-configurable eligibility (kept here for display, validated in engine)
    max_price: float = 100.0  # strictly less than
    top_n: int = 5
    watchlist_max_failures: int = 2

    options: OptionsCriteria = OptionsCriteria()
    insider: InsiderCriteria = InsiderCriteria()
    analyst: AnalystCriteria = AnalystCriteria()
    technical: TechnicalCriteria = TechnicalCriteria()
    float_: FloatCriteria = Field(FloatCriteria(), alias="float")
    liquidity: LiquidityCriteria = LiquidityCriteria()
    weights: Weights = Weights()
    shariah: ShariahConfig = ShariahConfig()

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _guard(self) -> "ScreeningConfig":
        if self.max_price > 100:
            raise ValueError("max_price above $100 violates a mandatory requirement")
        if self.top_n > 5:
            raise ValueError("top_n may not exceed 5")
        return self

    def relaxed_criteria(self) -> list[str]:
        """Criteria that the spec defines as hard but an admin has turned into preferences."""
        relaxed = []
        for name in ("options", "insider", "technical"):
            if not getattr(self, name).hard:
                relaxed.append(name)
        if self.options.min_volume_change_pct < 300:
            relaxed.append("options.min_volume_change_pct")
        if self.options.min_iv_rank < 20:
            relaxed.append("options.min_iv_rank")
        if self.options.max_put_call_ratio > 0.20:
            relaxed.append("options.max_put_call_ratio")
        if self.insider.min_pct_outstanding < 1.0:
            relaxed.append("insider.min_pct_outstanding")
        return relaxed
