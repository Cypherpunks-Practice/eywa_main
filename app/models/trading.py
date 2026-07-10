from __future__ import annotations

from enum import IntEnum

from clickhouse_sqlalchemy import engines, types
from sqlalchemy import Column, ForeignKey
from sqlalchemy.orm import relationship

from .base import Base


class SwapSide(IntEnum):
    BUY = 1
    SELL = 2


class Transaction(Base):
    __tablename__ = "transactions"

    hash_id = Column(types.String, primary_key=True)
    block_number = Column(types.UInt64, nullable=False)
    trader_address = Column(
        types.String,
        ForeignKey("traders.contract_address"),
        nullable=False,
    )
    bribe = Column(types.Nullable(types.UInt256), nullable=True)
    priority_fee = Column(types.Nullable(types.UInt256), nullable=True)

    trader = relationship("Trader", back_populates="transactions")
    swaps = relationship("Swap", back_populates="transaction")

    __table_args__ = (
        engines.MergeTree(order_by=("block_number", "hash_id")),
    )


class Swap(Base):
    __tablename__ = "swaps"

    id = Column(types.UInt64, primary_key=True)
    transaction_hash_id = Column(
        types.String,
        ForeignKey("transactions.hash_id"),
        nullable=False,
    )
    pool_address = Column(
        types.String,
        ForeignKey("liquidity_pools.contract_address"),
        nullable=False,
    )
    token_a_address = Column(
        types.Nullable(types.String),
        ForeignKey("tokens.contract_address"),
        nullable=True,
    )
    token_b_address = Column(
        types.Nullable(types.String),
        ForeignKey("tokens.contract_address"),
        nullable=True,
    )
    side = Column(
        types.Enum8(SwapSide),
        nullable=False,
    )
    usd_amount = Column(types.Nullable(types.Decimal(38, 18)), nullable=True)
    amount_a = Column(types.Nullable(types.UInt256), nullable=True)
    amount_b = Column(types.Nullable(types.UInt256), nullable=True)

    transaction = relationship("Transaction", back_populates="swaps")
    pool = relationship("LiquidityPool", back_populates="swaps")
    token_a = relationship(
        "Token",
        back_populates="token_a_swaps",
        foreign_keys=[token_a_address],
    )
    token_b = relationship(
        "Token",
        back_populates="token_b_swaps",
        foreign_keys=[token_b_address],
    )

    __table_args__ = (
        engines.MergeTree(order_by=("transaction_hash_id", "id")),
    )
