from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field, NonNegativeInt, PositiveFloat, PositiveInt, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..schemas import ReverseHeuristicsTopLevelField, TraceBackend

NonEmptyStr = Annotated[
    str,
    StringConstraints(min_length=1, strip_whitespace=True),
]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_prefix="EYWA_",
        extra="ignore",
    )

    app_name: str = "eywa"
    app_version: str = "0.1.0"
    debug: bool = False

    clickhouse_host: NonEmptyStr = "localhost"
    clickhouse_port: PositiveInt = 19000
    clickhouse_database: NonEmptyStr = "eywa"
    clickhouse_username: NonEmptyStr = "default"
    clickhouse_password: str = "clickhouse"
    clickhouse_driver: NonEmptyStr = "native"
    clickhouse_echo: bool = False
    clickhouse_startup_timeout_seconds: PositiveInt = 60
    clickhouse_retry_interval_seconds: PositiveFloat = 1.0

    rpc_endpoint: NonEmptyStr = "http://host.docker.internal:8545"

    known_stablecoin_hints: set[str] = {
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "0x6b175474e89094c44da98b954eedeac495271d0f",
        "0x4c9edd5852cd905f086c759e8383e09bff1e68b3",
        "0xf939e0a03fb07f59a73314e73794be0e57ac1b4e",
    }

    start_block: PositiveInt = 24_078_090
    chunk_size: PositiveInt = 10_000
    max_workers: PositiveInt = 16
    trace_address_batch_size: PositiveInt = 100

    trace_backend: TraceBackend = TraceBackend.DEBUG
    trace_block_concurrency: PositiveInt = 8
    trace_timeout: NonEmptyStr = "120s"

    receipt_batch_size: PositiveInt = 250
    factory_batch_size: PositiveInt = 250
    pool_token_batch_size: PositiveInt = 250
    block_batch_size: PositiveInt = 200
    token_decimal_batch_size: PositiveInt = 500

    rpc_retries: PositiveInt = 3
    rpc_retry_backoff_seconds: PositiveFloat = 0.5
    progress_log_every: PositiveFloat = 5_000

    default_v2_fee_tier: PositiveInt = 3000
    max_reasonable_token_price: str = "1000000000"
    price_inference_max_rounds: PositiveInt = 4

    scheduler_enabled: bool = False
    scheduler_cron: str | None = None
    scheduler_timezone: NonEmptyStr = "UTC"
    scheduler_runs: PositiveInt = 1

    # Периодическая чистка таблицы traders (удаление малоактивных трейдеров).
    # Отдельный от scan-шедулера планировщик; в ClickHouse нет крона для DELETE.
    traders_cleanup_enabled: bool = False
    traders_cleanup_cron: str | None = None
    traders_cleanup_min_transactions: PositiveInt = 3

    reverse_heuristics_enabled: bool = False
    reverse_heuristics_top_level_field: ReverseHeuristicsTopLevelField = (
        ReverseHeuristicsTopLevelField.TO
    )
    reverse_heuristics_min_pass_score: NonNegativeInt = 4
    reverse_heuristics_top_level_match_min: NonNegativeInt = 1
    reverse_heuristics_top_level_match_weight: NonNegativeInt = 2
    reverse_heuristics_raw_tx_min: NonNegativeInt = 2
    reverse_heuristics_raw_tx_weight: NonNegativeInt = 2
    reverse_heuristics_pool_count_min: NonNegativeInt = 1
    reverse_heuristics_pool_count_weight: NonNegativeInt = 1
    reverse_heuristics_matched_swap_min: NonNegativeInt = 1
    reverse_heuristics_matched_swap_weight: NonNegativeInt = 2
    reverse_heuristics_priced_swap_min: NonNegativeInt = 1
    reverse_heuristics_priced_swap_weight: NonNegativeInt = 1
    reverse_heuristics_total_usd_min: NonNegativeDecimal = Decimal("1000")
    reverse_heuristics_total_usd_weight: NonNegativeInt = 2
    reverse_heuristics_max_swap_usd_min: NonNegativeDecimal = Decimal("500")
    reverse_heuristics_max_swap_usd_weight: NonNegativeInt = 1
    reverse_heuristics_log_sample_limit: NonNegativeInt = 20

    @field_validator("clickhouse_driver")
    @classmethod
    def _normalize_clickhouse_driver(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("known_stablecoin_hints", mode="before")
    @classmethod
    def _normalize_stablecoin_hints(cls, value: object) -> object:
        if value is None:
            return set()
        items = value.split(",") if isinstance(value, str) else value
        return {
            str(item).strip().lower()
            for item in items
            if str(item).strip()
        }

    @field_validator("trace_backend", mode="before")
    @classmethod
    def _normalize_trace_backend(cls, value: object) -> object:
        if value is None or isinstance(value, TraceBackend):
            return value
        return str(value).strip().lower()

    @field_validator("reverse_heuristics_top_level_field", mode="before")
    @classmethod
    def _normalize_reverse_heuristics_top_level_field(cls, value: object) -> object:
        if value is None or isinstance(value, ReverseHeuristicsTopLevelField):
            return value
        return str(value).strip().lower()

    @field_validator("scheduler_cron", "traders_cleanup_cron", mode="before")
    @classmethod
    def _normalize_scheduler_cron(cls, value: object) -> str | None:
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    @field_validator("scheduler_timezone")
    @classmethod
    def _validate_scheduler_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
