"""Operator CLI.

  python -m app.cli create-admin EMAIL            # prompts for password
  python -m app.cli verify-providers [TICKER]     # live check of every configured provider + field map
  python -m app.cli scan [--kind manual]          # run one scan synchronously
  python -m app.cli refresh-universe
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import date, timedelta

from app import market_calendar as mc


def create_admin(email: str) -> None:
    from app.auth import hash_password
    from app.db import SessionLocal
    from app.models import User

    pw = getpass.getpass("Password (min 10 chars): ")
    if len(pw) < 10:
        sys.exit("password too short")
    with SessionLocal() as db:
        u = db.query(User).filter(User.email == email.lower()).first() or User(email=email.lower())
        u.password_hash, u.role = hash_password(pw), "admin"
        db.add(u)
        db.commit()
    print(f"admin {email} ready")


async def verify(ticker: str) -> int:
    from app.providers.market import ORATS_FIELDS
    from app.services.datasource import LiveDataSource

    ds = LiveDataSource()
    print("configured:", ds.describe())
    for e in ds.errors:
        print("  !", e)
    failures = 0
    day = mc.previous_sessions(date.today() - timedelta(days=1), 1)[-1]

    async def check(name, coro, validate):
        nonlocal failures
        try:
            res = await coro
            msg = validate(res)
            print(f"[{'OK ' if not msg else 'BAD'}] {name}{': ' + msg if msg else ''}")
            failures += bool(msg)
        except Exception as e:  # report everything, keep going
            failures += 1
            print(f"[ERR] {name}: {e}")

    if ds.sec:
        await check("sec universe", ds.universe(), lambda r: None if len(r) > 3000 else f"only {len(r)} tickers")
        await check("sec form4 index", ds.sec.form4_index(day), lambda r: None if r else "no Form 4 filings")
    if ds.orats:
        raw = await ds.orats._get("/hist/cores", tradeDate=day.isoformat(), ticker=ticker)
        missing = [v for v in ORATS_FIELDS["cores"].values() if raw and v not in raw[0]]
        print(f"[{'OK ' if raw and not missing else 'BAD'}] orats cores fields missing={missing} rows={len(raw)}")
        failures += bool(missing or not raw)
        await check("orats all-ticker day", ds.options_day(day), lambda r: None if len(r[0]) > 1000 else f"{len(r[0])} rows")
    if ds.massive:
        await check("massive bars", ds.bars(ticker, day - timedelta(days=30), day), lambda r: None if len(r[0]) > 10 else "few bars")
        await check("massive quote", ds.quote(ticker), lambda r: None if r[0].get("price") else "no price")
        await check("massive chain", ds.option_contracts(ticker), lambda r: None if r[0] else "no contracts")
    if ds.fmp:
        await check("fmp analyst", ds.analyst(ticker), lambda r: None if r[0].counts else "no consensus")
        await check("fmp float", ds.float_info(ticker), lambda r: None if r[0].get("float_shares") else "no float")
    return failures


async def scan(kind: str) -> None:
    from app.db import SessionLocal
    from app.services.datasource import LiveDataSource
    from app.services.engine import ScanEngine

    with SessionLocal() as db:
        ds = LiveDataSource()
        eng = ScanEngine(db, ds)
        eng.errors.extend(ds.errors)
        run = await eng.run(kind=kind, triggered_by="cli")
        print(run.status, run.funnel, run.errors[:10])


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("email")
    v = sub.add_parser("verify-providers")
    v.add_argument("ticker", nargs="?", default="AAPL")
    s = sub.add_parser("scan")
    s.add_argument("--kind", default="manual")
    sub.add_parser("refresh-universe")
    args = p.parse_args()
    if args.cmd == "create-admin":
        create_admin(args.email)
    elif args.cmd == "verify-providers":
        sys.exit(1 if asyncio.run(verify(args.ticker)) else 0)
    elif args.cmd == "scan":
        asyncio.run(scan(args.kind))
    else:
        from app.tasks import refresh_universe
        print(refresh_universe())


if __name__ == "__main__":
    main()
