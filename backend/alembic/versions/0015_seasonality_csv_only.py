"""seasonality from imported CSV only: drop the price-API history

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-09 12:40:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Month-end closes loaded from the price API go; closes from imported CSV files stay.
    op.execute("DELETE FROM monthly_closes WHERE provider = 'alpaca'")
    op.execute("DELETE FROM market_months WHERE month NOT IN (SELECT DISTINCT month FROM monthly_closes)")
    op.execute("DELETE FROM seasonality_stats WHERE symbol NOT IN (SELECT DISTINCT symbol FROM monthly_closes)")
    op.drop_table('seasonality_symbols')


def downgrade() -> None:
    op.create_table('seasonality_symbols',
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('months', sa.Integer(), nullable=False),
    sa.Column('last_month', sa.Date(), nullable=True),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('symbol')
    )
