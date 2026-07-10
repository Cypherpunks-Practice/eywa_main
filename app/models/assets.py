from __future__ import annotations

from clickhouse_sqlalchemy import engines, types
from sqlalchemy import Column, text
from sqlalchemy.orm import relationship

from .base import Base


class Token(Base):
    __tablename__ = "tokens"

    contract_address = Column(types.String, primary_key=True)
    label = Column(types.Nullable(types.String), nullable=True)
    decimals = Column(types.UInt8, nullable=False)
    is_stable = Column(
        types.Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    token_a_swaps = relationship(
        "Swap",
        back_populates="token_a",
        foreign_keys="Swap.token_a_address",
    )
    token_b_swaps = relationship(
        "Swap",
        back_populates="token_b",
        foreign_keys="Swap.token_b_address",
    )

    __table_args__ = (
        engines.MergeTree(order_by=("contract_address",)),
    )
