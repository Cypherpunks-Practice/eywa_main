"""Initial ClickHouse schema for eywa.

Revision ID: 20260311_0001
Revises:
Create Date: 2026-03-11 00:01:00
"""

from __future__ import annotations

from enum import IntEnum

from clickhouse_sqlalchemy import Table, engines, types
from sqlalchemy import Column, ForeignKey, MetaData, text

from alembic import op

revision = "20260311_0001"
down_revision = None
branch_labels = None
depends_on = None


class SwapSide(IntEnum):
    BUY = 1
    SELL = 2


metadata = MetaData()

tokens = Table(
    "tokens",
    metadata,
    Column("contract_address", types.String, primary_key=True),
    Column("label", types.Nullable(types.String), nullable=True),
    Column("decimals", types.UInt8, nullable=False),
    Column("is_stable", types.Boolean, nullable=False, server_default=text("false")),
    engines.MergeTree(order_by=("contract_address",)),
)

dexes = Table(
    "dexes",
    metadata,
    Column("factory", types.String, primary_key=True),
    Column("label", types.String, nullable=False),
    engines.MergeTree(order_by=("factory",)),
)

traders = Table(
    "traders",
    metadata,
    Column("contract_address", types.String, primary_key=True),
    Column("label", types.Nullable(types.String), nullable=True),
    engines.MergeTree(order_by=("contract_address",)),
)

liquidity_pools = Table(
    "liquidity_pools",
    metadata,
    Column("contract_address", types.String, primary_key=True),
    Column("dex_factory", types.String, ForeignKey("dexes.factory"), nullable=False),
    Column("fee_tier", types.Nullable(types.UInt32), nullable=True),
    engines.MergeTree(order_by=("contract_address", "dex_factory")),
)

transactions = Table(
    "transactions",
    metadata,
    Column("hash_id", types.String, primary_key=True),
    Column("block_number", types.UInt64, nullable=False),
    Column("trader_address", types.String, ForeignKey("traders.contract_address"), nullable=False),
    Column("bribe", types.Nullable(types.UInt256), nullable=True),
    Column("priority_fee", types.Nullable(types.UInt256), nullable=True),
    engines.MergeTree(order_by=("block_number", "hash_id")),
)

swaps = Table(
    "swaps",
    metadata,
    Column("id", types.UInt64, primary_key=True),
    Column("transaction_hash_id", types.String, ForeignKey("transactions.hash_id"), nullable=False),
    Column("pool_address", types.String, ForeignKey("liquidity_pools.contract_address"), nullable=False),
    Column("token_a_address", types.Nullable(types.String), ForeignKey("tokens.contract_address"), nullable=True),
    Column("token_b_address", types.Nullable(types.String), ForeignKey("tokens.contract_address"), nullable=True),
    Column("side", types.Enum8(SwapSide), nullable=False),
    Column("usd_amount", types.Nullable(types.Decimal(38, 18)), nullable=True),
    Column("amount_a", types.Nullable(types.UInt256), nullable=True),
    Column("amount_b", types.Nullable(types.UInt256), nullable=True),
    engines.MergeTree(order_by=("transaction_hash_id", "id")),
)

STABLECOIN_HINTS = (
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "0x6b175474e89094c44da98b954eedeac495271d0f",
    "0x4c9edd5852cd905f086c759e8383e09bff1e68b3",
    "0xf939e0a03fb07f59a73314e73794be0e57ac1b4e",
)


def _backfill_stable_tokens() -> None:
    quoted = ", ".join(f"'{address}'" for address in STABLECOIN_HINTS)
    op.execute(
        "ALTER TABLE `tokens` "
        "UPDATE `is_stable` = true "
        f"WHERE `contract_address` IN ({quoted}) AND `is_stable` != true"
    )


def upgrade() -> None:
    bind = op.get_bind()

    for table in (tokens, dexes, traders, liquidity_pools, transactions, swaps):
        table.create(bind=bind, checkfirst=True)

    _backfill_stable_tokens()


def downgrade() -> None:
    bind = op.get_bind()

    for table in (swaps, transactions, liquidity_pools, traders, dexes, tokens):
        table.drop(bind=bind, checkfirst=True)
