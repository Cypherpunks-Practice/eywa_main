"""Add TVL snapshot columns to liquidity pools.

Revision ID: 20260404_0003
Revises: 20260314_0002
Create Date: 2026-04-04 00:03:00
"""

from __future__ import annotations

from alembic import op

revision = "20260404_0003"
down_revision = "20260314_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE `liquidity_pools` "
        "ADD COLUMN IF NOT EXISTS `approx_tvl_usd` Nullable(Decimal(38, 18))"
    )
    op.execute(
        "ALTER TABLE `liquidity_pools` "
        "ADD COLUMN IF NOT EXISTS `tvl_block_number` Nullable(UInt64)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE `liquidity_pools` DROP COLUMN IF EXISTS `tvl_block_number`")
    op.execute("ALTER TABLE `liquidity_pools` DROP COLUMN IF EXISTS `approx_tvl_usd`")
