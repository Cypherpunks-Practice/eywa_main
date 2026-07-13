from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services.scan_orchestrator_service import ScanOrchestratorService


class StubRpcGatewayService:
    def __init__(self, head_block: int) -> None:
        self._head_block = head_block

    async def get_latest_block_number(self) -> int:
        return self._head_block


def build_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
    *,
    head_block: int,
    cursor_block: int | None,
    last_persisted_block: int | None = None,
    max_trace_depth: int = 100,
    start_block: int = 1_000,
) -> ScanOrchestratorService:
    monkeypatch.setenv("EYWA_MAX_TRACE_DEPTH", str(max_trace_depth))
    monkeypatch.setenv("EYWA_START_BLOCK", str(start_block))
    get_settings.cache_clear()

    monkeypatch.setattr(
        ScanOrchestratorService,
        "_get_auto_scan_cursor",
        staticmethod(lambda: cursor_block),
    )
    monkeypatch.setattr(
        ScanOrchestratorService,
        "_get_last_persisted_block",
        staticmethod(lambda: last_persisted_block),
    )

    return ScanOrchestratorService(
        trader_registry_service=None,
        pool_registry_service=None,
        transaction_discovery_service=None,
        receipt_collection_service=None,
        reverse_search_service=None,
        reverse_candidate_scoring_service=None,
        swap_parsing_service=None,
        pool_metadata_service=None,
        pool_tvl_service=None,
        trade_enrichment_service=None,
        trading_persistence_service=None,
        rpc_gateway_service=StubRpcGatewayService(head_block),
        settings=get_settings(),
    )


async def test_window_continues_after_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = build_orchestrator(
        monkeypatch,
        head_block=25_000_000,
        cursor_block=24_999_990,
    )

    assert await orchestrator.resolve_auto_scan_window() == (24_999_991, 25_000_000)


async def test_window_is_clamped_to_traceable_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = build_orchestrator(
        monkeypatch,
        head_block=25_000_000,
        cursor_block=24_000_000,
        max_trace_depth=100,
    )

    assert await orchestrator.resolve_auto_scan_window() == (24_999_900, 25_000_000)


async def test_window_is_none_without_new_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = build_orchestrator(
        monkeypatch,
        head_block=25_000_000,
        cursor_block=25_000_000,
    )

    assert await orchestrator.resolve_auto_scan_window() is None


async def test_window_falls_back_to_last_persisted_block(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = build_orchestrator(
        monkeypatch,
        head_block=25_000_000,
        cursor_block=None,
        last_persisted_block=24_999_950,
    )

    assert await orchestrator.resolve_auto_scan_window() == (24_999_950, 25_000_000)


async def test_empty_database_starts_within_traceable_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = build_orchestrator(
        monkeypatch,
        head_block=25_000_000,
        cursor_block=None,
        last_persisted_block=None,
        start_block=24_078_090,
    )

    assert await orchestrator.resolve_auto_scan_window() == (24_999_900, 25_000_000)
