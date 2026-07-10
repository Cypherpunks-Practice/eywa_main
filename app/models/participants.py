from __future__ import annotations

from clickhouse_sqlalchemy import engines, types
from sqlalchemy import Column
from sqlalchemy.orm import relationship

from .base import Base


class Trader(Base):
    __tablename__ = "traders"

    contract_address = Column(types.String, primary_key=True)
    label = Column(types.Nullable(types.String), nullable=True)

    transactions = relationship("Transaction", back_populates="trader")

    __table_args__ = (
        engines.MergeTree(order_by=("contract_address",)),
    )
