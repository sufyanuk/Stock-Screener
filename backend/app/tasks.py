"""Celery worker + beat schedule. All schedule times are America/New_York, so DST is handled by
the timezone; each task also checks the NYSE calendar and skips holidays/early closes.

  pre-market 08:30 · post-open 09:45 · intraday every 30 min 10:00–15:30 · closing 16:20 (final Top 5)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from app import market_calendar as mc
from app.config import get_settings

celery = Celery("stockintel", broker=get_settings().redis_url, backend=get_settings().redis_url)
celery.conf.timezone = "America/New_York"
celery.conf.beat_schedule = {
    "premarket": {"task": "app.tasks.scheduled_scan", "schedule": crontab(hour=8, minute=30, day_of_week="mon-fri"), "args": ("premarket",)},
    "open": {"task": "app.tasks.scheduled_scan", "schedule": crontab(hour=9, minute=45, day_of_week="mon-fri"), "args": ("open",)},
    "intraday": {"task": "app.tasks.scheduled_scan", "schedule": crontab(hour="10-15", minute="0,30", day_of_week="mon-fri"), "args": ("intraday",)},
    "close": {"task": "app.tasks.scheduled_scan", "schedule": crontab(hour=16, minute=20, day_of_week="mon-fri"), "args": ("close",)},
    "universe": {"task": "app.tasks.refresh_universe", "schedule": crontab(hour=6, minute=5, day_of_week="mon")},
}


def should_run(kind: str, now: datetime) -> bool:
    d = now.astimezone(mc.NY).date()
    t = mc.session_times(d)
    if not t:
        return False
    if kind == "intraday":
        return t[0] < now.astimezone(mc.NY) < t[1]  # skips intraday runs after an early close
    return True


async def _scan(kind: str, triggered_by: str):
    from app.db import SessionLocal
    from app.models import Recommendation
    from app.services.datasource import LiveDataSource
    from app.services.engine import ScanEngine
    from app.services.notify import notify_new

    with SessionLocal() as db:
        ds = LiveDataSource()
        eng = ScanEngine(db, ds)
        eng.errors.extend(ds.errors)
        now = datetime.now(timezone.utc)
        # Pre-market/intraday scans evaluate the last completed session's EOD options data with live quotes.
        run = await eng.run(kind=kind, as_of=mc.last_completed_session(now), triggered_by=triggered_by)
        prior = set(db.scalars(select(Recommendation.ticker).where(Recommendation.session_date == run.session_date,
                                                                    Recommendation.run_id != run.id)).all())
        new = [r for r in db.scalars(select(Recommendation).where(Recommendation.run_id == run.id)).all() if r.ticker not in prior]
        notify_new(db, new)
        return {"run_id": run.id, "status": run.status, "new": [r.ticker for r in new]}


@celery.task(name="app.tasks.scheduled_scan")
def scheduled_scan(kind: str, triggered_by: str = "scheduler"):
    if triggered_by == "scheduler" and not should_run(kind, datetime.now(timezone.utc)):
        return {"skipped": f"{kind}: market closed"}
    return asyncio.run(_scan(kind, triggered_by))


@celery.task(name="app.tasks.refresh_universe")
def refresh_universe():
    from app.db import SessionLocal
    from app.services.datasource import LiveDataSource
    from app.services.engine import ScanEngine

    async def go():
        with SessionLocal() as db:
            return await ScanEngine(db, LiveDataSource()).refresh_universe()

    return asyncio.run(go())
