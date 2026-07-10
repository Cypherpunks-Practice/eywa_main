from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator

from ..models.trading import SwapSide
from ..utils import (
    normalize_address_list,
    normalize_optional_address,
    normalize_required_address,
    normalize_required_tx_hash,
)
from .base import EywaSchema


class DexKind(StrEnum):
    UNI_V2_OR_SUSHI = "uni_v2_or_sushi"
    UNISWAP_V3 = "uniswap_v3"
    CURVE = "curve"


class TokenMetadata(EywaSchema):
    contract_address: str
    label: str | None = None
    decimals: int = 18
    is_stable: bool = False

    @field_validator("contract_address", mode="before")
    @classmethod
    def _normalize_contract_address(cls, value: object) -> str:
        return normalize_required_address(value)


class PoolMetadata(EywaSchema):
    pool_address: str
    dex_factory: str | None = None
    fee_tier: int | None = None
    token_a_address: str | None = None
    token_b_address: str | None = None

    @field_validator("pool_address", mode="before")
    @classmethod
    def _normalize_pool_address(cls, value: object) -> str:
        return normalize_required_address(value)

    @field_validator("dex_factory", "token_a_address", "token_b_address", mode="before")
    @classmethod
    def _normalize_optional_addresses(cls, value: object) -> str | None:
        return normalize_optional_address(value)


class BlockMetadata(EywaSchema):
    block_number: int
    timestamp: int | None = None
    base_fee_per_gas: int = 0
    miner: str | None = None

    @field_validator("miner", mode="before")
    @classmethod
    def _normalize_miner(cls, value: object) -> str | None:
        return normalize_optional_address(value)


class RawSwapEvent(EywaSchema):
    tx_hash: str
    block_number: int | None = None
    pool_address: str
    dex_kind: DexKind
    side: SwapSide | None = None
    matched_trader_addresses: list[str] = Field(default_factory=list)
    token_a_address: str | None = None
    token_b_address: str | None = None
    amount_a_raw: int = 0
    amount_b_raw: int = 0
    curve_sold_id: int | None = None
    curve_bought_id: int | None = None
    curve_use_underlying: bool = False

    @field_validator("tx_hash", mode="before")
    @classmethod
    def _normalize_tx_hash(cls, value: object) -> str:
        return normalize_required_tx_hash(value)

    @field_validator("pool_address", mode="before")
    @classmethod
    def _normalize_pool_address(cls, value: object) -> str:
        return normalize_required_address(value)

    @field_validator("matched_trader_addresses", mode="before")
    @classmethod
    def _normalize_trader_addresses(cls, value: object) -> list[str]:
        return normalize_address_list(value or [])

    @field_validator("token_a_address", "token_b_address", mode="before")
    @classmethod
    def _normalize_optional_token_addresses(cls, value: object) -> str | None:
        return normalize_optional_address(value)


class EnrichedSwapEvent(RawSwapEvent):
    trader_address: str | None = None
    dex_factory: str | None = None
    fee_tier: int | None = None
    token_a_label: str | None = None
    token_b_label: str | None = None
    token_a_decimals: int | None = None
    token_b_decimals: int | None = None
    token_a_is_stable: bool | None = None
    token_b_is_stable: bool | None = None
    usd_amount: Decimal | None = None
    bribe_wei: int = 0
    priority_fee_wei: int = 0
    timestamp: int | None = None
    miner: str | None = None

    @field_validator("trader_address", "dex_factory", "miner", mode="before")
    @classmethod
    def _normalize_optional_addresses(cls, value: object) -> str | None:
        return normalize_optional_address(value)
