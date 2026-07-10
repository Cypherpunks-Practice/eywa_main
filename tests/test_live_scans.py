from __future__ import annotations

import pytest

from app.schemas import ReverseScanRequest

from .live_scan_fixture import LiveScanCase


def _find_expected_swap(result, *, expected_tx_hash: str, expected_pool_address: str):
    assert result.payload is not None
    for swap in result.payload.swaps:
        if (
            swap.transaction_hash_id == expected_tx_hash
            and swap.pool_address == expected_pool_address
        ):
            return swap
    return None


def _find_expected_pool(result, *, expected_pool_address: str):
    assert result.payload is not None
    for pool in result.payload.pools:
        if pool.contract_address == expected_pool_address:
            return pool
    return None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trader_address_field", "expected_tx_hash_field"),
    [
        ("shark_address", "expected_shark_tx_hash"),
        ("whale_address", "expected_whale_tx_hash"),
    ],
)
async def test_forward_scan_returns_expected_live_data(
    orchestrator,
    live_scan_case: LiveScanCase,
    trader_address_field: str,
    expected_tx_hash_field: str,
) -> None:
    trader_address = getattr(live_scan_case, trader_address_field)
    expected_tx_hash = getattr(live_scan_case, expected_tx_hash_field)

    result = await orchestrator.run_forward_scan(
        start_block=live_scan_case.block_number,
        end_block=live_scan_case.block_number,
        trader_addresses=[trader_address],
        persist=False,
        trace_address_role=live_scan_case.trace_address_role,
    )

    assert result.transaction_count > 0
    assert result.receipt_count > 0
    assert result.raw_swap_count > 0
    assert result.enriched_swap_count > 0
    assert result.payload is not None
    assert result.payload.swaps
    assert result.payload.tokens
    assert result.payload.pools

    expected_swap = _find_expected_swap(
        result,
        expected_tx_hash=expected_tx_hash,
        expected_pool_address=live_scan_case.expected_pool_address,
    )
    assert expected_swap is not None
    assert expected_swap.token_a_address == live_scan_case.expected_token_a_address
    assert expected_swap.token_b_address == live_scan_case.expected_token_b_address

    tokens_by_address = {
        token.contract_address: token
        for token in result.payload.tokens
    }
    assert live_scan_case.expected_token_a_address in tokens_by_address
    assert live_scan_case.expected_token_b_address in tokens_by_address
    assert (
        tokens_by_address[live_scan_case.expected_token_a_address].label
        == live_scan_case.expected_token_a_symbol
    )
    assert (
        tokens_by_address[live_scan_case.expected_token_b_address].label
        == live_scan_case.expected_token_b_symbol
    )

    expected_pool = _find_expected_pool(
        result,
        expected_pool_address=live_scan_case.expected_pool_address,
    )
    assert expected_pool is not None
    assert expected_pool.approx_tvl_usd is not None
    assert expected_pool.liquidity_snapshot_block_number == live_scan_case.block_number
    assert expected_pool.token_a_usd_share is not None
    assert 0 <= expected_pool.token_a_usd_share <= 1


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("excluded_address_field", "expected_competitor_field", "expected_tx_hash_field"),
    [
        ("shark_address", "whale_address", "expected_whale_tx_hash"),
        ("whale_address", "shark_address", "expected_shark_tx_hash"),
    ],
)
async def test_reverse_scan_returns_expected_live_data(
    orchestrator,
    live_scan_case: LiveScanCase,
    excluded_address_field: str,
    expected_competitor_field: str,
    expected_tx_hash_field: str,
) -> None:
    reverse_request = ReverseScanRequest(
        start_block=live_scan_case.block_number,
        end_block=live_scan_case.block_number,
        pool_addresses=[live_scan_case.expected_pool_address],
        excluded_competitor_addresses=[getattr(live_scan_case, excluded_address_field)],
    )

    result = await orchestrator.run_reverse_scan(reverse_request, persist=False)

    assert result.active_pool_count > 0
    assert result.total_transaction_count > 0
    assert result.pools

    pool_result = result.pools[0]
    assert pool_result.pool_address == live_scan_case.expected_pool_address
    assert getattr(live_scan_case, expected_competitor_field) in pool_result.competitor_addresses
    assert getattr(live_scan_case, expected_tx_hash_field) in pool_result.transaction_hashes
