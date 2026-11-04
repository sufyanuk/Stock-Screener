"""TimescaleDB hypertables for time-series tables (skipped when the extension is unavailable).

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = {"daily_bars": "session", "options_daily": "session"}


def upgrade() -> None:
    conn = op.get_bind()
    has = conn.exec_driver_sql("select 1 from pg_available_extensions where name='timescaledb'").first()
    if not has:
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    for t, col in TABLES.items():
        op.execute(f"SELECT create_hypertable('{t}', '{col}', chunk_time_interval => INTERVAL '90 days', "
                   f"migrate_data => true, if_not_exists => true)")


def downgrade() -> None:
    pass  # hypertables are kept; dropping them requires a manual data copy
