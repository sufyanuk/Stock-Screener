"""Run Alembic migrations from inside the app (serverless platforms have no release step).

A PostgreSQL advisory lock makes concurrent cold starts safe: one instance migrates, the
others wait and then find nothing to do.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db import engine

log = logging.getLogger(__name__)
LOCK_ID = 727_001  # arbitrary, app-specific
BACKEND_DIR = Path(__file__).resolve().parent.parent


def upgrade_head() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    eng = engine()
    with eng.connect() as conn:
        pg = conn.dialect.name == "postgresql"
        if pg:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_ID})
        try:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            if pg:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_ID})
                conn.commit()
    log.info("database migrations are up to date")
