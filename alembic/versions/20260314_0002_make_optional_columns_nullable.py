"""Make optional ClickHouse columns explicitly Nullable.

Revision ID: 20260314_0002
Revises: 20260311_0001
Create Date: 2026-03-14 00:02:00
"""

from __future__ import annotations

from alembic import op

revision = "20260314_0002"
down_revision = "20260311_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE `tokens` MODIFY COLUMN `label` Nullable(String)")
    op.execute("ALTER TABLE `traders` MODIFY COLUMN `label` Nullable(String)")
    op.execute("ALTER TABLE `liquidity_pools` MODIFY COLUMN `fee_tier` Nullable(UInt32)")
    op.execute("ALTER TABLE `transactions` MODIFY COLUMN `bribe` Nullable(UInt256)")
    op.execute("ALTER TABLE `transactions` MODIFY COLUMN `priority_fee` Nullable(UInt256)")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `token_a_address` Nullable(String)")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `token_b_address` Nullable(String)")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `usd_amount` Nullable(Decimal(38, 18))")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `amount_a` Nullable(UInt256)")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `amount_b` Nullable(UInt256)")


def downgrade() -> None:
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `amount_b` UInt256")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `amount_a` UInt256")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `usd_amount` Decimal(38, 18)")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `token_b_address` String")
    op.execute("ALTER TABLE `swaps` MODIFY COLUMN `token_a_address` String")
    op.execute("ALTER TABLE `transactions` MODIFY COLUMN `priority_fee` UInt256")
    op.execute("ALTER TABLE `transactions` MODIFY COLUMN `bribe` UInt256")
    op.execute("ALTER TABLE `liquidity_pools` MODIFY COLUMN `fee_tier` UInt32")
    op.execute("ALTER TABLE `traders` MODIFY COLUMN `label` String")
    op.execute("ALTER TABLE `tokens` MODIFY COLUMN `label` String")
