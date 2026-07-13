from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

from ..core.config import Settings
from ..core.exceptions import RpcError
from ..schemas import TraceAddressRole, TraceBackend
from ..utils import (
    async_resolve,
    flatten_call_frames,
    iter_chunks,
    normalize_address,
    normalize_block_trace_entries,
)
from .rpc_gateway_service import RpcGatewayService

logger = logging.getLogger(__name__)

UNSUPPORTED_METHOD_MARKERS = (
    "method not found",
    "-32601",
    "does not exist",
    "not supported",
    "unsupported",
)


class TraceProviderService:
    """
    Collects call traces from either RPC namespace behind one interface.

    `trace_filter` (Erigon/OpenEthereum) filters by address server-side over a whole
    block range, while `debug_traceBlockByNumber` (Geth) traces one block at a time and
    returns everything. Both backends are normalized here into `trace_filter`-shaped
    dicts, so callers keep reading `transactionHash` / `action.from` / `action.to`.
    """

    def __init__(
        self,
        rpc_gateway_service: RpcGatewayService,
        settings: Settings,
    ):
        self._rpc_gateway_service = rpc_gateway_service
        self._settings = settings

    async def collect_traces(
        self,
        *,
        from_block: int,
        to_block: int,
        addresses: Sequence[str],
        address_role: TraceAddressRole,
    ) -> list[dict[str, Any]]:
        if not addresses:
            return []

        if self._settings.trace_backend is TraceBackend.TRACE_FILTER:
            return await self._collect_via_trace_filter(
                from_block=from_block,
                to_block=to_block,
                addresses=addresses,
                address_role=address_role,
            )

        return await self._collect_via_debug(
            from_block=from_block,
            to_block=to_block,
            addresses=addresses,
            address_role=address_role,
        )

    async def trace_transaction(self, tx_hash: str) -> list[dict[str, Any]]:
        if self._settings.trace_backend is TraceBackend.TRACE_FILTER:
            return await self._rpc_gateway_service.trace_transaction(tx_hash)

        frame = await self._rpc_gateway_service.debug_trace_transaction(tx_hash)
        return flatten_call_frames(frame, tx_hash=tx_hash)

    async def trace_transaction_supported(self, sample_tx_hash: str) -> bool:
        try:
            await self.trace_transaction(sample_tx_hash)
        except RpcError as exc:
            message = str(exc).lower()
            return not any(marker in message for marker in UNSUPPORTED_METHOD_MARKERS)
        return True

    async def _collect_via_trace_filter(
        self,
        *,
        from_block: int,
        to_block: int,
        addresses: Sequence[str],
        address_role: TraceAddressRole,
    ) -> list[dict[str, Any]]:
        traces: list[dict[str, Any]] = []
        for address_batch in iter_chunks(
            list(addresses), self._settings.trace_address_batch_size,
        ):
            traces.extend(
                await self._rpc_gateway_service.trace_filter(
                    from_block=from_block,
                    to_block=to_block,
                    addresses=address_batch,
                    address_role=address_role,
                )
            )
        return traces

    async def _collect_via_debug(
        self,
        *,
        from_block: int,
        to_block: int,
        addresses: Sequence[str],
        address_role: TraceAddressRole,
    ) -> list[dict[str, Any]]:
        address_set = {
            address
            for raw_address in addresses
            if (address := normalize_address(raw_address)) is not None
        }
        if not address_set:
            return []

        block_numbers = list(range(from_block, to_block + 1))
        # Trace-namespace filtering happened node-side across the whole range; here every
        # block is a separate request, so progress is reported per block instead of per
        # chunk. Cap the interval so even a short window logs a handful of times.
        progress_interval = max(
            1,
            min(
                math.ceil(self._settings.progress_log_every),
                math.ceil(len(block_numbers) / 10),
            ),
        )
        traced_blocks = 0
        matched_traces = 0

        async def resolve_one(block_number: int) -> tuple[int, list[dict[str, Any]]]:
            nonlocal traced_blocks, matched_traces

            block_traces = await self._collect_block_traces(
                block_number, address_set, address_role,
            )

            traced_blocks += 1
            matched_traces += len(block_traces)
            if traced_blocks % progress_interval == 0:
                logger.info(
                    "Traced %s/%s blocks, %s matching trace(s)",
                    f"{traced_blocks:,}",
                    f"{len(block_numbers):,}",
                    f"{matched_traces:,}",
                )

            return block_number, block_traces

        traces_by_block = await async_resolve(
            block_numbers,
            resolve_one,
            max_concurrency=self._settings.trace_block_concurrency,
        )

        return [
            trace
            for block_number in block_numbers
            for trace in traces_by_block.get(block_number, ())
        ]

    async def _collect_block_traces(
        self,
        block_number: int,
        address_set: set[str],
        address_role: TraceAddressRole,
    ) -> list[dict[str, Any]]:
        entries = await self._rpc_gateway_service.debug_trace_block_by_number(block_number)
        if not entries:
            return []

        normalized_entries = normalize_block_trace_entries(entries)
        if any(tx_hash is None for tx_hash, _ in normalized_entries):
            # Older clients omit `txHash` on each frame; entries are positional, so the
            # block's transaction list restores the mapping.
            block = await self._rpc_gateway_service.get_block(block_number)
            block_tx_hashes = block.get("transactions") or [] if block else []
            normalized_entries = normalize_block_trace_entries(
                entries, fallback_tx_hashes=block_tx_hashes,
            )

        matched: list[dict[str, Any]] = []
        for tx_hash, frame in normalized_entries:
            if tx_hash is None:
                continue
            matched.extend(
                trace
                for trace in flatten_call_frames(
                    frame, tx_hash=tx_hash, block_number=block_number,
                )
                if self._trace_matches(trace, address_set, address_role)
            )
        return matched

    @staticmethod
    def _trace_matches(
        trace: dict[str, Any],
        address_set: set[str],
        address_role: TraceAddressRole,
    ) -> bool:
        action = trace.get("action") or {}
        role_key = "to" if address_role == TraceAddressRole.TO else "from"
        address = normalize_address(action.get(role_key))
        return address is not None and address in address_set


__all__ = ["TraceProviderService"]
