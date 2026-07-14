from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from ..core.config import Settings
from ..schemas import ScanRequest, TraceAddressRole, TransactionContext
from ..utils import iter_block_chunks, normalize_address, normalize_tx_hash
from .trace_provider_service import TraceProviderService

logger = logging.getLogger(__name__)


class TransactionDiscoveryService:
    def __init__(
        self,
        trace_provider_service: TraceProviderService,
        settings: Settings,
    ):
        self._trace_provider_service = trace_provider_service
        self._settings = settings

    async def collect_tx_contexts(self, scan_request: ScanRequest) -> dict[str, TransactionContext]:
        matched_traders_by_tx_hash: dict[str, set[str]] = defaultdict(set)
        trader_set = set(scan_request.trader_addresses)
        failed_ranges: list[tuple[int, int, Exception]] = []
        processed_blocks = 0
        processed_chunks = 0
        total_traces = 0
        progress_log_interval = max(1, math.ceil(self._settings.progress_log_every))
        next_progress_log_at = progress_log_interval

        for chunk_start, chunk_end in iter_block_chunks(
            scan_request.start_block,
            scan_request.end_block,
            self._settings.chunk_size,
        ):
            processed_chunks += 1
            processed_blocks += chunk_end - chunk_start + 1

            try:
                traces = await self._trace_provider_service.collect_traces(
                    from_block=chunk_start,
                    to_block=chunk_end,
                    addresses=scan_request.trader_addresses,
                    address_role=scan_request.trace_address_role,
                )
            except Exception as exc:
                failed_ranges.append((chunk_start, chunk_end, exc))
                traces = []

            total_traces += len(traces)

            for trace in traces:
                tx_hash = normalize_tx_hash(trace.get("transactionHash"))
                if tx_hash is None:
                    continue

                matched_address = self._extract_trace_address(trace, scan_request.trace_address_role)
                matched_traders_by_tx_hash.setdefault(tx_hash, set())
                if matched_address and matched_address in trader_set:
                    matched_traders_by_tx_hash[tx_hash].add(matched_address)

            while processed_blocks >= next_progress_log_at:
                logger.info(
                    "Trace scan progress: %s/%s blocks, %s chunk(s), %s trace(s), %s unique tx(s)",
                    f"{processed_blocks:,}",
                    f"{scan_request.block_count:,}",
                    f"{processed_chunks:,}",
                    f"{total_traces:,}",
                    f"{len(matched_traders_by_tx_hash):,}",
                )
                next_progress_log_at += progress_log_interval

        if failed_ranges:
            failed_ranges_preview = ", ".join(
                f"{start}-{end}: {type(error).__name__}({error})"
                for start, end, error in failed_ranges[:3]
            )
            raise RuntimeError(
                "Failed to collect traces for "
                f"{len(failed_ranges)} block range(s): {failed_ranges_preview}"
            ) from failed_ranges[0][2]

        logger.info(
            "Trace scan completed: %s/%s blocks, %s chunk(s), %s trace(s), %s unique tx(s)",
            f"{processed_blocks:,}",
            f"{scan_request.block_count:,}",
            f"{processed_chunks:,}",
            f"{total_traces:,}",
            f"{len(matched_traders_by_tx_hash):,}",
        )

        return {
            tx_hash: TransactionContext(
                tx_hash=tx_hash,
                matched_trader_addresses=sorted(matched_addresses),
            )
            for tx_hash, matched_addresses in matched_traders_by_tx_hash.items()
        }

    @staticmethod
    def _extract_trace_address(
        trace: dict[str, Any],
        trace_address_role: TraceAddressRole,
    ) -> str | None:
        action = trace.get("action") or {}
        if trace_address_role == TraceAddressRole.TO:
            return normalize_address(action.get("to") or trace.get("toAddress") or trace.get("to"))
        return normalize_address(
            action.get("from") or trace.get("fromAddress") or trace.get("from")
        )
