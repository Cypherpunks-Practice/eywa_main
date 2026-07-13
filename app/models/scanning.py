from __future__ import annotations

from clickhouse_sqlalchemy import engines, types
from sqlalchemy import Column

from .base import Base


class ScanCursor(Base):
    __tablename__ = "scan_cursors"

    name = Column(types.String, primary_key=True)
    last_scanned_block = Column(types.UInt64, nullable=False)
    updated_at = Column(types.DateTime, nullable=False)

    __table_args__ = (
        engines.ReplacingMergeTree(version="updated_at", order_by=("name",)),
    )
