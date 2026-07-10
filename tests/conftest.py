from __future__ import annotations

import inspect

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.core.container import Container
from app.services.token_metadata_service import TokenMetadataService

from .live_scan_fixture import LIVE_SCAN_CASE, LiveScanCase


@pytest.fixture(autouse=True)
def _reset_settings_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EYWA_REVERSE_HEURISTICS_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def live_scan_case() -> LiveScanCase:
    if not LIVE_SCAN_CASE.is_configured:
        pytest.skip(
            "Fill tests/live_scan_fixture.py with a real block and Shark/Whale addresses "
            "to run live integration tests."
        )

    rpc_endpoint = get_settings().rpc_endpoint.strip()
    if not rpc_endpoint:
        pytest.skip("EYWA_RPC_ENDPOINT must be configured for live integration tests.")

    return LIVE_SCAN_CASE


@pytest.fixture(autouse=True)
def _empty_token_metadata_db(monkeypatch: pytest.MonkeyPatch) -> None:
    def load_empty_metadata(_: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(
        TokenMetadataService,
        "_load_token_metadata_from_db",
        staticmethod(load_empty_metadata),
    )


@pytest_asyncio.fixture
async def orchestrator():
    container = Container()
    try:
        yield container.scan_orchestrator_service()
    finally:
        rpc_gateway_service = container.rpc_gateway_service()
        close_result = rpc_gateway_service.close()
        if inspect.isawaitable(close_result):
            await close_result

        shutdown_result = container.shutdown_resources()
        if inspect.isawaitable(shutdown_result):
            await shutdown_result
        container.unwire()
