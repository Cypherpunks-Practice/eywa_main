"""Регресс на overflow TVL: значение вне Decimal(38, 18) не должно ронять запись.

Инцидент 14.07.2026: пул получил approx_tvl_usd ~8.38e155 (скам-токен с фейковым
балансом), ClickHouse отбил вставку блока `Decimal convert overflow`, шедулер встал.
Здесь проверяем оба слоя защиты — источник (PoolTvlService) и границу БД (схемы) —
без сети и без БД.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas import PoolLiquiditySnapshot
from app.schemas.persistence import PoolWrite
from app.services.pool_tvl_service import PoolTvlService
from app.utils import DB_DECIMAL_38_18_MAX, fits_db_decimal

POOL = "0x" + "1" * 40
FACTORY = "0x" + "2" * 40
OVERFLOW = Decimal("8.382756274993779e155")  # значение из инцидента


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal(0), True),
        (Decimal("12345.678"), True),
        (DB_DECIMAL_38_18_MAX - 1, True),
        (DB_DECIMAL_38_18_MAX, False),
        (OVERFLOW, False),
        (-OVERFLOW, False),
    ],
)
def test_fits_db_decimal_boundaries(value: Decimal, expected: bool) -> None:
    assert fits_db_decimal(value) is expected


def test_build_snapshot_drops_overflowing_tvl() -> None:
    snapshot = PoolTvlService._build_pool_liquidity_snapshot(
        pool_address=POOL,
        token_a_value=OVERFLOW,
        token_b_value=Decimal("1"),
        block_number=25_534_083,
    )
    assert snapshot is None


def test_build_snapshot_keeps_normal_tvl() -> None:
    snapshot = PoolTvlService._build_pool_liquidity_snapshot(
        pool_address=POOL,
        token_a_value=Decimal("600"),
        token_b_value=Decimal("400"),
        block_number=25_534_083,
    )
    assert snapshot is not None
    assert snapshot.approx_tvl_usd == Decimal("1000")
    assert snapshot.token_a_usd_share == pytest.approx(0.6)


def test_build_snapshot_none_inputs() -> None:
    assert PoolTvlService._build_pool_liquidity_snapshot(
        pool_address=POOL,
        token_a_value=None,
        token_b_value=Decimal("1"),
        block_number=1,
    ) is None


def test_pool_write_nulls_overflowing_tvl() -> None:
    pool = PoolWrite(contract_address=POOL, dex_factory=FACTORY, approx_tvl_usd=OVERFLOW)
    assert pool.approx_tvl_usd is None


def test_pool_write_keeps_normal_tvl() -> None:
    pool = PoolWrite(
        contract_address=POOL, dex_factory=FACTORY, approx_tvl_usd=Decimal("1000.5")
    )
    assert pool.approx_tvl_usd == Decimal("1000.5")


def test_snapshot_schema_nulls_overflowing_tvl() -> None:
    snapshot = PoolLiquiditySnapshot(approx_tvl_usd=OVERFLOW)
    assert snapshot.approx_tvl_usd is None


def test_snapshot_schema_keeps_normal_tvl() -> None:
    snapshot = PoolLiquiditySnapshot(approx_tvl_usd=Decimal("1000.5"))
    assert snapshot.approx_tvl_usd == Decimal("1000.5")
