"""Rename pool snapshot block column and add token balance share.

Revision ID: 20260408_0004
Revises: 20260404_0003
Create Date: 2026-04-08 00:04:00
"""

from __future__ import annotations

from alembic import op

revision = "20260408_0004"
down_revision = "20260404_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE `liquidity_pools` "
        "RENAME COLUMN `tvl_block_number` TO `liquidity_snapshot_block_number`"
    )
    op.execute(
        "ALTER TABLE `liquidity_pools` "
        "ADD COLUMN IF NOT EXISTS `token_a_usd_share` Nullable(Float64)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE `liquidity_pools` DROP COLUMN IF EXISTS `token_a_usd_share`")
    op.execute(
        "ALTER TABLE `liquidity_pools` "
        "RENAME COLUMN `liquidity_snapshot_block_number` TO `tvl_block_number`"
    )
