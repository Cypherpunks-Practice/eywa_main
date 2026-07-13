"""Add scan cursors table.

Revision ID: 20260713_0005
Revises: 20260408_0004
Create Date: 2026-07-13 00:05:00
"""

from __future__ import annotations

from alembic import op

revision = "20260713_0005"
down_revision = "20260408_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS `scan_cursors` ("
        "`name` String, "
        "`last_scanned_block` UInt64, "
        "`updated_at` DateTime"
        ") ENGINE = ReplacingMergeTree(`updated_at`) ORDER BY (`name`)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `scan_cursors`")
