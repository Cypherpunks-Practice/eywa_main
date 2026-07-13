from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.schemas import TraceAddressRole, TraceBackend
from app.services.trace_provider_service import TraceProviderService
from app.utils import flatten_call_frames, normalize_block_trace_entries


def _address(hex_digit: str) -> str:
    return "0x" + (hex_digit * 40)


def _tx_hash(hex_digit: str) -> str:
    return "0x" + (hex_digit * 64)


TRADER = _address("a")
POOL = _address("b")
ROUTER = _address("c")
OUTSIDER = _address("d")


def _call_frame(**overrides: Any) -> dict[str, Any]:
    frame = {
        "type": "CALL",
        "from": ROUTER,
        "to": POOL,
        "value": "0x0",
        "gas": "0x186a0",
        "gasUsed": "0x5208",
        "input": "0x",
        "output": "0x",
    }
    frame.update(overrides)
    return frame


def test_flatten_walks_nested_frames_in_pre_order() -> None:
    frame = _call_frame(
        calls=[
            _call_frame(type="STATICCALL", to=OUTSIDER),
            _call_frame(
                type="DELEGATECALL",
                to=TRADER,
                calls=[_call_frame(to=POOL)],
            ),
        ],
    )

    traces = flatten_call_frames(frame, tx_hash=_tx_hash("1"), block_number=100)

    assert [trace["traceAddress"] for trace in traces] == [[], [0], [1], [1, 0]]
    assert [trace["action"]["callType"] for trace in traces] == [
        "call",
        "staticcall",
        "delegatecall",
        "call",
    ]
    assert [trace["subtraces"] for trace in traces] == [2, 0, 1, 0]
    assert {trace["transactionHash"] for trace in traces} == {_tx_hash("1")}
    assert {trace["blockNumber"] for trace in traces} == {100}


def test_flatten_maps_create_without_action_to() -> None:
    frame = _call_frame(type="CREATE2", to=TRADER, input="0x60806040")

    (trace,) = flatten_call_frames(frame, tx_hash=_tx_hash("2"), block_number=1)

    # A `to`-role filter must not match a contract deployment: the trace namespace
    # exposes the created address under `result.address`, never under `action.to`.
    assert trace["type"] == "create"
    assert "to" not in trace["action"]
    assert trace["action"]["init"] == "0x60806040"
    assert trace["result"]["address"] == TRADER


def test_flatten_maps_selfdestruct_without_action_to() -> None:
    frame = _call_frame(type="SELFDESTRUCT", to=TRADER, value="0x1")

    (trace,) = flatten_call_frames(frame, tx_hash=_tx_hash("3"), block_number=1)

    assert trace["type"] == "suicide"
    assert "to" not in trace["action"]
    assert trace["action"]["refundAddress"] == TRADER
    assert trace["action"]["balance"] == "0x1"


def test_flatten_keeps_reverted_frames_and_drops_their_result() -> None:
    frame = _call_frame(error="execution reverted")

    (trace,) = flatten_call_frames(frame, tx_hash=_tx_hash("4"), block_number=1)

    assert trace["error"] == "execution reverted"
    assert "result" not in trace
    assert trace["action"]["to"] == POOL


def test_normalize_entries_reads_tx_hash_from_wrapper() -> None:
    entries = [{"txHash": _tx_hash("5"), "result": _call_frame()}]

    assert normalize_block_trace_entries(entries) == [(_tx_hash("5"), _call_frame())]


def test_normalize_entries_falls_back_to_block_transactions_by_position() -> None:
    entries = [
        {"txHash": _tx_hash("6"), "error": "tracing failed"},
        _call_frame(),
    ]

    normalized = normalize_block_trace_entries(
        entries, fallback_tx_hashes=[_tx_hash("6"), _tx_hash("7")],
    )

    # The failed entry is dropped, but positions of the remaining ones must not shift.
    assert normalized == [(_tx_hash("7"), _call_frame())]


class FakeRpcGatewayService:
    def __init__(self, traces_by_block: dict[int, list[dict[str, Any]]]) -> None:
        self._traces_by_block = traces_by_block
        self.traced_blocks: list[int] = []

    async def debug_trace_block_by_number(self, block_number: int) -> list[dict[str, Any]]:
        self.traced_blocks.append(block_number)
        return self._traces_by_block.get(block_number, [])

    async def get_block(self, block_number: int) -> dict[str, Any] | None:
        raise AssertionError("get_block must not be called when txHash is present")


def _settings() -> Settings:
    return Settings(trace_backend=TraceBackend.DEBUG, trace_block_concurrency=2)


@pytest.mark.asyncio
async def test_collect_traces_filters_by_address_role_across_blocks() -> None:
    gateway = FakeRpcGatewayService(
        {
            10: [
                {
                    "txHash": _tx_hash("1"),
                    "result": _call_frame(
                        to=ROUTER,
                        calls=[_call_frame(type="DELEGATECALL", to=TRADER)],
                    ),
                },
                {"txHash": _tx_hash("2"), "result": _call_frame(to=OUTSIDER)},
            ],
            11: [{"txHash": _tx_hash("3"), "result": _call_frame(to=TRADER)}],
        }
    )
    provider = TraceProviderService(gateway, _settings())

    traces = await provider.collect_traces(
        from_block=10,
        to_block=11,
        addresses=[TRADER],
        address_role=TraceAddressRole.TO,
    )

    assert sorted(gateway.traced_blocks) == [10, 11]
    assert [trace["transactionHash"] for trace in traces] == [_tx_hash("1"), _tx_hash("3")]
    assert [trace["blockNumber"] for trace in traces] == [10, 11]
    assert {trace["action"]["to"] for trace in traces} == {TRADER}


@pytest.mark.asyncio
async def test_collect_traces_matches_from_role() -> None:
    gateway = FakeRpcGatewayService(
        {
            10: [
                {"txHash": _tx_hash("1"), "result": _call_frame(**{"from": TRADER})},
                {"txHash": _tx_hash("2"), "result": _call_frame(**{"from": OUTSIDER})},
            ],
        }
    )
    provider = TraceProviderService(gateway, _settings())

    traces = await provider.collect_traces(
        from_block=10,
        to_block=10,
        addresses=[TRADER],
        address_role=TraceAddressRole.FROM,
    )

    assert [trace["transactionHash"] for trace in traces] == [_tx_hash("1")]


@pytest.mark.asyncio
async def test_collect_traces_returns_nothing_without_addresses() -> None:
    gateway = FakeRpcGatewayService({10: [{"txHash": _tx_hash("1"), "result": _call_frame()}]})
    provider = TraceProviderService(gateway, _settings())

    traces = await provider.collect_traces(
        from_block=10,
        to_block=10,
        addresses=[],
        address_role=TraceAddressRole.TO,
    )

    assert traces == []
    assert gateway.traced_blocks == []
