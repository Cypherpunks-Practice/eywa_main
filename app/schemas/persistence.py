from __future__ import annotations

from decimal import Decimal

from pydantic import Field, field_validator

from ..models.trading import SwapSide
from ..utils import (
    fits_db_decimal,
    normalize_address,
    normalize_optional_address,
    normalize_required_address,
    normalize_required_tx_hash,
)
from .base import EywaSchema


def _sanitize_approx_tvl_usd(value: Decimal | None) -> Decimal | None:
    """Отсекает TVL, не влезающий в колонку `Decimal(38, 18)`.

    Мусорное значение (напр. `8.38e155` от скам-токена с фейковым балансом) обрушило
    бы вставку блока с `Decimal convert overflow`, поэтому на границе БД трактуем его
    как «неизвестно» (NULL). Логируется это у источника — `PoolTvlService`.
    """

    if value is None or fits_db_decimal(value):
        return value
    return None


class DexWrite(EywaSchema):
    factory: str
    label: str

    @field_validator("factory", mode="before")
    @classmethod
    def _normalize_factory(cls, value: object) -> str:
        if value is None:
            raise ValueError("Dex factory must be set")

        text = str(value).strip()
        if not text:
            raise ValueError("Dex factory must not be empty")

        return normalize_address(text) or text


class TokenWrite(EywaSchema):
    contract_address: str
    label: str | None = None
    decimals: int = 18
    is_stable: bool = False

    @field_validator("contract_address", mode="before")
    @classmethod
    def _normalize_contract_address(cls, value: object) -> str:
        return normalize_required_address(value)


class PoolWrite(EywaSchema):
    contract_address: str
    dex_factory: str
    fee_tier: int | None = None
    approx_tvl_usd: Decimal | None = None
    token_a_usd_share: float | None = Field(default=None, ge=0, le=1)
    liquidity_snapshot_block_number: int | None = None

    @field_validator("approx_tvl_usd")
    @classmethod
    def _drop_overflowing_approx_tvl_usd(cls, value: Decimal | None) -> Decimal | None:
        return _sanitize_approx_tvl_usd(value)

    @field_validator("token_a_usd_share")
    @classmethod
    def _validate_token_a_usd_share(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return min(1.0, max(0.0, value))

    @field_validator("contract_address", mode="before")
    @classmethod
    def _normalize_contract_address(cls, value: object) -> str:
        return normalize_required_address(value)

    @field_validator("dex_factory", mode="before")
    @classmethod
    def _normalize_factory(cls, value: object) -> str:
        if value is None:
            raise ValueError("dex_factory must not be null")

        text = str(value).strip()
        if not text:
            raise ValueError("dex_factory must not be empty")

        return normalize_address(text) or text


class PoolLiquiditySnapshot(EywaSchema):
    approx_tvl_usd: Decimal | None = None
    token_a_usd_share: float | None = Field(default=None, ge=0, le=1)
    liquidity_snapshot_block_number: int | None = None

    @field_validator("approx_tvl_usd")
    @classmethod
    def _drop_overflowing_approx_tvl_usd(cls, value: Decimal | None) -> Decimal | None:
        return _sanitize_approx_tvl_usd(value)

    @field_validator("token_a_usd_share")
    @classmethod
    def _validate_token_a_usd_share(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return min(1.0, max(0.0, value))


class TransactionWrite(EywaSchema):
    hash_id: str
    block_number: int
    trader_address: str
    bribe: int | None = None
    priority_fee: int | None = None

    @field_validator("hash_id", mode="before")
    @classmethod
    def _normalize_hash_id(cls, value: object) -> str:
        return normalize_required_tx_hash(value)

    @field_validator("trader_address", mode="before")
    @classmethod
    def _normalize_trader_address(cls, value: object) -> str:
        return normalize_required_address(value)


class SwapWrite(EywaSchema):
    id: int | None = None
    transaction_hash_id: str
    pool_address: str
    token_a_address: str | None = None
    token_b_address: str | None = None
    side: SwapSide
    usd_amount: Decimal | None = None
    amount_a: int | None = None
    amount_b: int | None = None

    @field_validator("transaction_hash_id", mode="before")
    @classmethod
    def _normalize_transaction_hash_id(cls, value: object) -> str:
        return normalize_required_tx_hash(value)

    @field_validator("pool_address", mode="before")
    @classmethod
    def _normalize_pool_address(cls, value: object) -> str:
        return normalize_required_address(value)

    @field_validator("token_a_address", "token_b_address", mode="before")
    @classmethod
    def _normalize_optional_token_addresses(cls, value: object) -> str | None:
        return normalize_optional_address(value)


class TradePayload(EywaSchema):
    dexes: list[DexWrite] = Field(default_factory=list)
    tokens: list[TokenWrite] = Field(default_factory=list)
    pools: list[PoolWrite] = Field(default_factory=list)
    transactions: list[TransactionWrite] = Field(default_factory=list)
    swaps: list[SwapWrite] = Field(default_factory=list)


class PersistStats(EywaSchema):
    inserted_dexes: int = 0
    inserted_traders: int = 0
    inserted_tokens: int = 0
    inserted_pools: int = 0
    inserted_transactions: int = 0
    inserted_swaps: int = 0
    skipped_existing_transactions: int = 0
    skipped_existing_swaps: int = 0


class BackfillItemStats(EywaSchema):
    scanned: int = 0
    updated: int = 0
    skipped: int = 0
    unresolved: int = 0


class BackfillStats(EywaSchema):
    tokens: BackfillItemStats = Field(default_factory=BackfillItemStats)
    pools: BackfillItemStats = Field(default_factory=BackfillItemStats)
