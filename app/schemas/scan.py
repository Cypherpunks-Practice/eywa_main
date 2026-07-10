from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from ..utils import (
    normalize_address_list,
    normalize_required_address,
    normalize_required_tx_hash,
)
from .base import EywaSchema
from .persistence import PersistStats, TradePayload


class TraceAddressRole(StrEnum):
    TO = "to"
    FROM = "from"


class ReverseScanSource(StrEnum):
    FORWARD = "forward"
    DATABASE = "database"
    MIXED = "mixed"


class ReverseHeuristicsTopLevelField(StrEnum):
    TO = "to"
    FROM = "from"
    OFF = "off"


class TransactionContext(EywaSchema):
    tx_hash: str
    matched_trader_addresses: list[str] = Field(default_factory=list)

    @field_validator("tx_hash", mode="before")
    @classmethod
    def _normalize_tx_hash(cls, value: object) -> str:
        return normalize_required_tx_hash(value)

    @field_validator("matched_trader_addresses", mode="before")
    @classmethod
    def _normalize_trader_addresses(cls, value: object) -> list[str]:
        return normalize_address_list(value or [])


class ScanRequest(EywaSchema):
    start_block: int
    end_block: int
    trader_addresses: list[str] = Field(default_factory=list)
    trace_address_role: TraceAddressRole = TraceAddressRole.TO

    @field_validator("trader_addresses", mode="before")
    @classmethod
    def _normalize_trader_addresses(cls, value: object) -> list[str]:
        return normalize_address_list(value or [])

    @model_validator(mode="after")
    def _validate_range(self) -> ScanRequest:
        if self.start_block < 0:
            raise ValueError("start_block must be non-negative")
        if self.end_block < self.start_block:
            raise ValueError("end_block must be greater than or equal to start_block")
        if not self.trader_addresses:
            raise ValueError("trader_addresses must not be empty")
        return self

    @property
    def block_count(self) -> int:
        return self.end_block - self.start_block + 1


class ScanRunResult(EywaSchema):
    request: ScanRequest
    transaction_count: int = 0
    receipt_count: int = 0
    raw_swap_count: int = 0
    enriched_swap_count: int = 0
    payload: TradePayload | None = None
    persist_stats: PersistStats | None = None


class ReverseScanRequest(EywaSchema):
    start_block: int
    end_block: int
    pool_addresses: list[str] = Field(default_factory=list)
    excluded_competitor_addresses: list[str] = Field(default_factory=list)
    excluded_competitor_addresses_by_pool: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("pool_addresses", "excluded_competitor_addresses", mode="before")
    @classmethod
    def _normalize_address_values(cls, value: object) -> list[str]:
        return normalize_address_list(value or [])

    @field_validator("excluded_competitor_addresses_by_pool", mode="before")
    @classmethod
    def _normalize_competitor_map(cls, value: object) -> dict[str, list[str]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("excluded_competitor_addresses_by_pool must be a mapping")

        return {
            normalize_required_address(pool_address): normalize_address_list(competitor_addresses or [])
            for pool_address, competitor_addresses in value.items()
        }

    @model_validator(mode="after")
    def _validate_range(self) -> ReverseScanRequest:
        if self.start_block < 0:
            raise ValueError("start_block must be non-negative")
        if self.end_block < self.start_block:
            raise ValueError("end_block must be greater than or equal to start_block")
        if not self.pool_addresses:
            raise ValueError("pool_addresses must not be empty")
        return self

    @property
    def block_count(self) -> int:
        return self.end_block - self.start_block + 1

    @property
    def excluded_competitor_pair_count(self) -> int:
        return len(self.excluded_competitor_addresses) + sum(
            len(competitor_addresses)
            for competitor_addresses in self.excluded_competitor_addresses_by_pool.values()
        )


class ReversePoolResult(EywaSchema):
    pool_address: str
    competitor_addresses: list[str] = Field(default_factory=list)
    competitor_count: int = 0
    discovered_competitor_count: int = 0
    rejected_competitor_count: int = 0
    transaction_hashes: list[str] = Field(default_factory=list)
    transaction_count: int = 0

    @field_validator("pool_address", mode="before")
    @classmethod
    def _normalize_pool_address(cls, value: object) -> str:
        return normalize_required_address(value)

    @field_validator("competitor_addresses", mode="before")
    @classmethod
    def _normalize_competitor_addresses(cls, value: object) -> list[str]:
        return normalize_address_list(value or [])

    @field_validator("transaction_hashes", mode="before")
    @classmethod
    def _normalize_transaction_hashes(cls, value: object) -> list[str]:
        return sorted(
            {
                normalize_required_tx_hash(item)
                for item in (value or [])
                if item is not None and str(item).strip()
            }
        )


class ReverseScanResult(EywaSchema):
    request: ReverseScanRequest
    pools: list[ReversePoolResult] = Field(default_factory=list)
    active_pool_count: int = 0
    total_competitor_count: int = 0
    discovered_competitor_count: int = 0
    approved_competitor_count: int = 0
    rejected_competitor_count: int = 0
    total_transaction_count: int = 0


class ScanSequenceRunResult(EywaSchema):
    forward_result: ScanRunResult
    reverse_result: ReverseScanResult | None = None
