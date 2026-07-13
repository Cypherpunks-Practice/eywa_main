from __future__ import annotations

from collections.abc import Sequence
from typing import Any

CREATE_FRAME_TYPES = frozenset({"CREATE", "CREATE2"})
SELFDESTRUCT_FRAME_TYPES = frozenset({"SELFDESTRUCT", "SUICIDE"})


def normalize_block_trace_entries(
    entries: Sequence[Any],
    *,
    fallback_tx_hashes: Sequence[Any] = (),
) -> list[tuple[Any, dict[str, Any]]]:
    """
    Pair every `debug_traceBlockByNumber` entry with its transaction hash.

    Modern clients wrap each call frame as `{"txHash": ..., "result": {...}}`, while
    older ones return bare frames. `fallback_tx_hashes` (the block's transaction list)
    covers the latter, since entries are positional. Transactions whose trace failed
    are dropped without shifting the positions of the remaining ones.
    """

    normalized: list[tuple[Any, dict[str, Any]]] = []

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue

        if "result" in entry or "txHash" in entry:
            frame = entry.get("result")
            if not isinstance(frame, dict):
                continue
            tx_hash = entry.get("txHash") or entry.get("transactionHash")
        else:
            frame = entry
            tx_hash = None

        if tx_hash is None and index < len(fallback_tx_hashes):
            tx_hash = fallback_tx_hashes[index]

        normalized.append((tx_hash, frame))

    return normalized


def flatten_call_frames(
    frame: Any,
    *,
    tx_hash: Any,
    block_number: int | None = None,
) -> list[dict[str, Any]]:
    """
    Flatten a `callTracer` frame tree into `trace_filter`-shaped trace dicts.

    Frames are walked pre-order so `traceAddress` matches the trace-namespace layout.
    """

    if not isinstance(frame, dict):
        return []

    traces: list[dict[str, Any]] = []
    stack: list[tuple[dict[str, Any], tuple[int, ...]]] = [(frame, ())]

    while stack:
        current, trace_address = stack.pop()
        traces.append(
            _build_trace(
                current,
                tx_hash=tx_hash,
                block_number=block_number,
                trace_address=trace_address,
            )
        )

        subcalls = current.get("calls")
        if not isinstance(subcalls, list):
            continue

        for index in reversed(range(len(subcalls))):
            subcall = subcalls[index]
            if isinstance(subcall, dict):
                stack.append((subcall, (*trace_address, index)))

    return traces


def _build_trace(
    frame: dict[str, Any],
    *,
    tx_hash: Any,
    block_number: int | None,
    trace_address: tuple[int, ...],
) -> dict[str, Any]:
    frame_type = str(frame.get("type") or "").upper()
    subcalls = frame.get("calls")

    # CREATE and SELFDESTRUCT frames deliberately carry no `action.to`: the trace
    # namespace does not expose one either, so a `to`-role filter must not match a
    # contract deployment or a self-destruct refund target.
    if frame_type in CREATE_FRAME_TYPES:
        trace_type = "create"
        action: dict[str, Any] = {
            "from": frame.get("from"),
            "value": frame.get("value"),
            "gas": frame.get("gas"),
            "init": frame.get("input"),
        }
        result: dict[str, Any] | None = {
            "address": frame.get("to"),
            "code": frame.get("output"),
            "gasUsed": frame.get("gasUsed"),
        }
    elif frame_type in SELFDESTRUCT_FRAME_TYPES:
        trace_type = "suicide"
        action = {
            "address": frame.get("from"),
            "refundAddress": frame.get("to"),
            "balance": frame.get("value"),
        }
        result = None
    else:
        trace_type = "call"
        action = {
            "callType": frame_type.lower() or "call",
            "from": frame.get("from"),
            "to": frame.get("to"),
            "value": frame.get("value"),
            "gas": frame.get("gas"),
            "input": frame.get("input"),
        }
        result = {
            "gasUsed": frame.get("gasUsed"),
            "output": frame.get("output"),
        }

    trace: dict[str, Any] = {
        "transactionHash": tx_hash,
        "blockNumber": block_number,
        "traceAddress": list(trace_address),
        "subtraces": len(subcalls) if isinstance(subcalls, list) else 0,
        "type": trace_type,
        "action": action,
    }

    error = frame.get("error")
    if error:
        trace["error"] = error
    elif result is not None:
        trace["result"] = result

    return trace


__all__ = ["flatten_call_frames", "normalize_block_trace_entries"]
