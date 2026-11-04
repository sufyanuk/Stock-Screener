"""Shared value types: data provenance and criterion outcomes.

Every value shown to a user carries a ``Provenance``. A missing value is represented
explicitly (``value is None`` + ``status="missing"``) and can never pass a criterion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Freshness(str, Enum):
    REALTIME = "realtime"
    DELAYED = "delayed"
    EOD = "end_of_day"
    FILING = "filing"  # point-in-time regulatory filing (SEC)
    DERIVED = "derived"  # computed by this app from sourced inputs


@dataclass(frozen=True)
class Provenance:
    provider: str
    freshness: Freshness
    retrieved_at: datetime
    market_ts: datetime | None = None  # time the value refers to in the market
    provider_updated_at: datetime | None = None
    endpoint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["freshness"] = self.freshness.value
        for k in ("retrieved_at", "market_ts", "provider_updated_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def derived(*inputs: Provenance | None) -> Provenance:
    """Provenance for a value computed from other values: market_ts is the *oldest* input."""
    ts = [p.market_ts for p in inputs if p and p.market_ts]
    return Provenance(
        provider="+".join(sorted({p.provider for p in inputs if p})) or "app",
        freshness=Freshness.DERIVED,
        retrieved_at=now_utc(),
        market_ts=min(ts) if ts else None,
    )


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    MISSING = "missing"  # insufficient data — treated as a failure for eligibility


@dataclass
class CriterionResult:
    name: str
    outcome: Outcome
    hard: bool
    score: float  # 0..100 sub-score used for ranking (0 when missing)
    explanation: str
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome.value,
            "hard": self.hard,
            "score": round(self.score, 2),
            "explanation": self.explanation,
            "metrics": self.metrics,
        }


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def lerp_score(x: float, x0: float, x1: float, s0: float = 0.0, s1: float = 100.0) -> float:
    """Linear map x in [x0, x1] -> [s0, s1], clamped."""
    if x1 == x0:
        return s1 if x >= x1 else s0
    t = (x - x0) / (x1 - x0)
    return clamp(s0 + t * (s1 - s0), min(s0, s1), max(s0, s1))
