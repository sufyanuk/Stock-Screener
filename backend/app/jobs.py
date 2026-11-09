"""Heavy data jobs, run outside Vercel (GitHub Actions: .github/workflows/data-jobs.yml).

    python -m app.jobs nightly      # scan + every data load below
    python -m app.jobs scan         # one scan (after a CSV import)
    python -m app.jobs shariah-weekly     # Sundays: download the SEC figures again
    python -m app.jobs shariah | ratings | insiders | earnings

Each loader is resumable and is called repeatedly until it reports nothing left (or the job's time is up),
so whole-market loads that need several 5-minute Vercel requests finish in one run here.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time

log = logging.getLogger("jobs")
ROUND = 240.0  # seconds per loader call


def _session():
    from app.db import SessionLocal

    return SessionLocal()


def _ds():
    from app.main import make_datasource

    return make_datasource()


async def _until_done(name: str, call, done, deadline: float, rounds: int = 200) -> None:
    for i in range(rounds):
        if time.time() > deadline:
            log.warning("%s: out of time", name)
            return
        r = await call(i)
        log.info("%s round %d: %s", name, i + 1, {k: v for k, v in (r or {}).items() if not isinstance(v, (list, dict))})
        for e in (r or {}).get("errors") or []:
            log.warning("%s: %s", name, e)
        if done(r or {}):
            return


async def scan(kind: str = "manual") -> None:
    from app.main import _do_scan

    log.info("scan: %s", await _do_scan(kind, "github-actions"))


async def shariah(deadline: float, weekly: bool = False) -> None:
    """Weekdays: prices + re-classification only, nothing fetched from SEC. Sundays (``weekly``): the SEC
    figures are downloaded again. SEC is also read on a weekday when nothing has ever been classified (a new database)."""
    from app.services import shariah as shs

    with _session() as db:
        ds = _ds()
        st = shs.status(db)
        first_load = not st["results"]  # nothing classified yet (new database)
        sec_data = weekly or first_load
        refresh = weekly and bool(st["results"])
        log.info("shariah: %s", "weekly SEC refresh" if refresh else "SEC load" if sec_data else "prices only (no SEC)")
        await _until_done("shariah", lambda i: shs.load(db, ds, ROUND, refresh=refresh and i == 0, sec_data=sec_data),
                          lambda r: r.get("complete") or r.get("processed") == 0, deadline)


async def shariah_weekly(deadline: float) -> None:
    await shariah(deadline, weekly=True)


async def ratings(deadline: float) -> None:
    from app.services import ratings as rt

    with _session() as db:
        ds = _ds()
        await _until_done("sentisense", lambda i: rt.load_sentisense(db, ds),
                          lambda r: r.get("remaining", 0) == 0 or r.get("fetched") == 0, deadline, rounds=10)
        await _until_done("analysts", lambda i: rt.load_analysts(db, ds, 200),
                          lambda r: r.get("remaining", 0) == 0 or r.get("fetched") == 0, deadline)


async def insiders(deadline: float) -> None:
    from app.main import insiders_load_shares

    with _session() as db:
        await _until_done("insider shares", lambda i: insiders_load_shares(top=100, db=db, _=None),
                          lambda r: r.get("remaining", 0) == 0 or r.get("fetched") == 0, deadline, rounds=10)


async def earnings(deadline: float) -> None:
    from app.main import earnings_load_analysts

    with _session() as db:
        await _until_done("earnings analysts", lambda i: earnings_load_analysts(days=7, limit=200, db=db, _=None),
                          lambda r: r.get("remaining", 0) == 0 or r.get("fetched") == 0, deadline, rounds=10)


LOADS = {"shariah": shariah, "ratings": ratings, "insiders": insiders, "earnings": earnings}
JOBS = ["nightly", "scan", "shariah-weekly", *LOADS]


async def run(job: str, minutes: float = 100) -> int:
    deadline = time.time() + minutes * 60
    failed = 0
    steps = ([("scan", None), *LOADS.items()] if job == "nightly"
             else [(job, shariah_weekly)] if job == "shariah-weekly" else [(job, LOADS.get(job))])
    for name, fn in steps:
        try:
            if name == "scan":
                await scan("close" if job == "nightly" else "manual")
            else:
                await fn(deadline)
        except Exception:  # noqa: BLE001 - one failing load must not stop the others
            log.exception("%s failed", name)
            failed += 1
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    job = argv[1] if len(argv) > 1 else "nightly"
    if job not in JOBS:
        print(f"usage: python -m app.jobs [{'|'.join(JOBS)}]", file=sys.stderr)
        return 2
    return asyncio.run(run(job))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
