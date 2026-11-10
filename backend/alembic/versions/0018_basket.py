"""basket_items: tickers the user put in the basket (kept until the next nightly scan)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-10 08:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('basket_items',
    sa.Column('ticker', sa.String(length=16), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('ticker')
    )


def downgrade() -> None:
    op.drop_table('basket_items')
