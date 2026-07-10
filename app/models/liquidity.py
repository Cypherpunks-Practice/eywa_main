from __future__ import annotations

from clickhouse_sqlalchemy import engines, types
from sqlalchemy import Column, ForeignKey
from sqlalchemy.orm import relationship

from .base import Base


class Dex(Base):
    __tablename__ = "dexes"

    factory = Column(types.String, primary_key=True)
    label = Column(types.String, nullable=False)

    pools = relationship("LiquidityPool", back_populates="dex")

    __table_args__ = (
        engines.MergeTree(order_by=("factory",)),
    )


class LiquidityPool(Base):
    __tablename__ = "liquidity_pools"

    contract_address = Column(types.String, primary_key=True)
    dex_factory = Column(types.String, ForeignKey("dexes.factory"), nullable=False)
    fee_tier = Column(types.Nullable(types.UInt32), nullable=True)
    approx_tvl_usd = Column(types.Nullable(types.Decimal(38, 18)), nullable=True)
    token_a_usd_share = Column(types.Nullable(types.Float64), nullable=True)
    liquidity_snapshot_block_number = Column(types.Nullable(types.UInt64), nullable=True)

    dex = relationship("Dex", back_populates="pools")
    swaps = relationship("Swap", back_populates="pool")

    __table_args__ = (
        engines.MergeTree(order_by=("contract_address", "dex_factory")),
    )
