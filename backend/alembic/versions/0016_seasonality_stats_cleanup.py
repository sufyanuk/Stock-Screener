"""remove seasonality statistics left without month-end history (written by a job running old code
while 0015 ran)

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-09 12:45:00
"""
from alembic import op


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM monthly_closes WHERE provider = 'alpaca'")
    op.execute("DELETE FROM market_months WHERE month NOT IN (SELECT DISTINCT month FROM monthly_closes)")
    op.execute("DELETE FROM seasonality_stats WHERE symbol NOT IN (SELECT DISTINCT symbol FROM monthly_closes)")


def downgrade() -> None:
    pass
