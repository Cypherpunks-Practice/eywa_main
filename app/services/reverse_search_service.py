from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..core.config import Settings
from ..schemas import ReversePoolResult, ReverseScanRequest, TraceAddressRole
from ..utils import iter_block_chunks, normalize_address, normalize_tx_hash
from .trace_provider_service import TraceProviderService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReverseCompetitionMatch:
    pool_address: str
    competitor_address: str
    tx_hash: str


@dataclass(frozen=True)
class ReverseCompetitionDiscoveryResult:
    request: ReverseScanRequest
    pools: list[ReversePoolResult]
    matches: tuple[ReverseCompetitionMatch, ...]
    competitor_addresses: tuple[str, ...]
    active_pool_count: int
    total_transaction_count: int


class ReverseSearchService:
    def __init__(
        self,
        trace_provider_service: TraceProviderService,
        settings: Settings,
    ):
        self._trace_provider_service = trace_provider_service
        self._settings = settings

    async def collect_pool_competition(
        self,
        reverse_scan_request: ReverseScanRequest,
    ) -> ReverseCompetitionDiscoveryResult:
        pool_addresses = sorted(set(reverse_scan_request.pool_addresses))
        excluded_competitor_addresses = set(reverse_scan_request.excluded_competitor_addresses)
        excluded_competitor_addresses_by_pool = {
            pool_address: set(competitor_addresses)
            for pool_address, competitor_addresses in (
                reverse_scan_request.excluded_competitor_addresses_by_pool.items()
            )
        }
        competitors_by_pool: dict[str, set[str]] = defaultdict(set)
        transaction_hashes_by_pool: dict[str, set[str]] = defaultdict(set)
        pool_address_set = set(pool_addresses)
        competitor_addresses: set[str] = set()
        match_keys: set[tuple[str, str, str]] = set()
        matches: list[ReverseCompetitionMatch] = []
        failed_ranges: list[tuple[int, int, Exception]] = []
        processed_blocks = 0
        processed_chunks = 0
        total_traces = 0
        progress_log_interval = max(1, math.ceil(self._settings.progress_log_every))
        next_progress_log_at = progress_log_interval

        for chunk_start, chunk_end in iter_block_chunks(
            reverse_scan_request.start_block,
            reverse_scan_request.end_block,
            self._settings.chunk_size,
        ):
            processed_chunks += 1
            processed_blocks += chunk_end - chunk_start + 1

            try:
                traces = await self._trace_provider_service.collect_traces(
                    from_block=chunk_start,
                    to_block=chunk_end,
                    addresses=pool_addresses,
                    address_role=TraceAddressRole.TO,
                )
            except Exception as exc:
                failed_ranges.append((chunk_start, chunk_end, exc))
                traces = []

            total_traces += len(traces)

            for trace in traces:
                tx_hash = normalize_tx_hash(trace.get("transactionHash"))
                if tx_hash is None:
                    continue

                pool_address = self._extract_pool_address(trace)
                if pool_address is None or pool_address not in pool_address_set:
                    continue

                competitor_address = self._extract_competitor_address(trace)
                if (
                    competitor_address is None
                    or competitor_address == pool_address
                    or competitor_address in excluded_competitor_addresses
                    or competitor_address in excluded_competitor_addresses_by_pool.get(pool_address, set())
                ):
                    continue

                match_key = (pool_address, competitor_address, tx_hash)
                if match_key not in match_keys:
                    match_keys.add(match_key)
                    matches.append(
                        ReverseCompetitionMatch(
                            pool_address=pool_address,
                            competitor_address=competitor_address,
                            tx_hash=tx_hash,
                        )
                    )
                transaction_hashes_by_pool[pool_address].add(tx_hash)
                competitors_by_pool[pool_address].add(competitor_address)
                competitor_addresses.add(competitor_address)

            while processed_blocks >= next_progress_log_at:
                logger.info(
                    "Reverse scan progress: %s/%s blocks, %s chunk(s), %s trace(s), %s active pool(s)",
                    f"{processed_blocks:,}",
                    f"{reverse_scan_request.block_count:,}",
                    f"{processed_chunks:,}",
                    f"{total_traces:,}",
                    f"{sum(1 for tx_hashes in transaction_hashes_by_pool.values() if tx_hashes):,}",
                )
                next_progress_log_at += progress_log_interval

        if failed_ranges:
            failed_ranges_preview = ", ".join(
                f"{start}-{end}: {type(error).__name__}({error})"
                for start, end, error in failed_ranges[:3]
            )
            raise RuntimeError(
                "Failed to collect reverse traces for "
                f"{len(failed_ranges)} block range(s): {failed_ranges_preview}"
            ) from failed_ranges[0][2]

        logger.info(
            "Reverse scan completed: %s/%s blocks, %s chunk(s), %s trace(s), %s active pool(s)",
            f"{processed_blocks:,}",
            f"{reverse_scan_request.block_count:,}",
            f"{processed_chunks:,}",
            f"{total_traces:,}",
            f"{sum(1 for tx_hashes in transaction_hashes_by_pool.values() if tx_hashes):,}",
        )

        pool_results = [
            ReversePoolResult(
                pool_address=pool_address,
                competitor_addresses=sorted(competitors_by_pool.get(pool_address, set())),
                competitor_count=len(competitors_by_pool.get(pool_address, set())),
                discovered_competitor_count=len(competitors_by_pool.get(pool_address, set())),
                rejected_competitor_count=0,
                transaction_hashes=sorted(transaction_hashes_by_pool.get(pool_address, set())),
                transaction_count=len(transaction_hashes_by_pool.get(pool_address, set())),
            )
            for pool_address in pool_addresses
        ]

        return ReverseCompetitionDiscoveryResult(
            request=reverse_scan_request,
            pools=pool_results,
            matches=tuple(matches),
            competitor_addresses=tuple(sorted(competitor_addresses)),
            active_pool_count=sum(1 for pool in pool_results if pool.transaction_count > 0),
            total_transaction_count=sum(pool.transaction_count for pool in pool_results),
        )

    @staticmethod
    def _extract_pool_address(trace: dict[str, Any]) -> str | None:
        action = trace.get("action") or {}
        return normalize_address(action.get("to") or trace.get("toAddress") or trace.get("to"))

    @staticmethod
    def _extract_competitor_address(trace: dict[str, Any]) -> str | None:
        action = trace.get("action") or {}
        return normalize_address(
            action.get("from") or trace.get("fromAddress") or trace.get("from")
        )


__all__ = [
    "ReverseCompetitionDiscoveryResult",
    "ReverseCompetitionMatch",
    "ReverseSearchService",
]
