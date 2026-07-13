from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select

from ..core.config import Settings
from ..core.database import session_scope
from ..models.scanning import ScanCursor
from ..models.trading import Transaction
from ..schemas import (
    ReverseScanRequest,
    ReverseScanResult,
    ReverseScanSource,
    ScanRequest,
    ScanRunResult,
    ScanSequenceRunResult,
    TraceAddressRole,
    TradePayload,
)
from ..utils.scanning import validate_block_window
from .pool_metadata_service import PoolMetadataService
from .pool_registry_service import PoolRegistryService
from .pool_tvl_service import PoolTvlService
from .receipt_collection_service import ReceiptCollectionService
from .reverse_candidate_scoring_service import ReverseCandidateScoringService
from .reverse_search_service import ReverseSearchService
from .rpc_gateway_service import RpcGatewayService
from .swap_parsing_service import SwapParsingService
from .trade_enrichment_service import TradeEnrichmentService
from .trader_registry_service import TraderRegistryService
from .trading_persistence_service import TradingPersistenceService
from .transaction_discovery_service import TransactionDiscoveryService

logger = logging.getLogger(__name__)

AUTO_SCAN_CURSOR_NAME = "auto"


class ScanOrchestratorService:
    def __init__(
        self,
        trader_registry_service: TraderRegistryService,
        pool_registry_service: PoolRegistryService,
        transaction_discovery_service: TransactionDiscoveryService,
        receipt_collection_service: ReceiptCollectionService,
        reverse_search_service: ReverseSearchService,
        reverse_candidate_scoring_service: ReverseCandidateScoringService,
        swap_parsing_service: SwapParsingService,
        pool_metadata_service: PoolMetadataService,
        pool_tvl_service: PoolTvlService,
        trade_enrichment_service: TradeEnrichmentService,
        trading_persistence_service: TradingPersistenceService,
        rpc_gateway_service: RpcGatewayService,
        settings: Settings,
    ) -> None:
        self._trader_registry_service = trader_registry_service
        self._pool_registry_service = pool_registry_service
        self._transaction_discovery_service = transaction_discovery_service
        self._receipt_collection_service = receipt_collection_service
        self._reverse_search_service = reverse_search_service
        self._reverse_candidate_scoring_service = reverse_candidate_scoring_service
        self._swap_parsing_service = swap_parsing_service
        self._pool_metadata_service = pool_metadata_service
        self._pool_tvl_service = pool_tvl_service
        self._trade_enrichment_service = trade_enrichment_service
        self._trading_persistence_service = trading_persistence_service
        self._rpc_gateway_service = rpc_gateway_service
        self._settings = settings

    async def run_scan_sequence(
        self,
        *,
        start_block: int,
        end_block: int,
        trader_addresses: list[str] | None = None,
        persist: bool = True,
        trace_address_role: TraceAddressRole = TraceAddressRole.TO,
        pool_source: ReverseScanSource = ReverseScanSource.DATABASE,
    ) -> ScanSequenceRunResult:
        scan_request = await self._trader_registry_service.build_scan_request(
            start_block=start_block,
            end_block=end_block,
            trader_addresses=trader_addresses,
            trace_address_role=trace_address_role,
        )
        return await self.run_scan_sequence_for_request(
            scan_request,
            persist=persist,
            pool_source=pool_source,
        )

    async def run_forward_scan(
        self,
        *,
        start_block: int,
        end_block: int,
        trader_addresses: list[str] | None = None,
        persist: bool = True,
        trace_address_role: TraceAddressRole = TraceAddressRole.TO,
    ) -> ScanRunResult:
        scan_request = await self._trader_registry_service.build_scan_request(
            start_block=start_block,
            end_block=end_block,
            trader_addresses=trader_addresses,
            trace_address_role=trace_address_role,
        )
        return await self.run_scan(scan_request, persist=persist)

    async def resolve_auto_scan_window(self) -> tuple[int, int] | None:
        start_block = await asyncio.to_thread(self._resolve_auto_start_block)
        end_block = await self._rpc_gateway_service.get_latest_block_number()

        oldest_traceable_block = max(end_block - self._settings.max_trace_depth, 0)
        if start_block < oldest_traceable_block:
            logger.warning(
                "Blocks %s-%s are out of reach and will be skipped: the node only keeps "
                "state for the last %s block(s)",
                f"{start_block:,}",
                f"{oldest_traceable_block - 1:,}",
                f"{self._settings.max_trace_depth:,}",
            )
            start_block = oldest_traceable_block

        if start_block > end_block:
            logger.info(
                "Auto scan skipped: no new blocks after %s (head is %s)",
                f"{start_block - 1:,}",
                f"{end_block:,}",
            )
            return None

        try:
            validated_start_block, validated_end_block = validate_block_window(
                start_block=start_block,
                end_block=end_block,
            )
        except ValueError as exc:
            raise RuntimeError(
                "Unable to build auto scan range "
                f"from start_block={start_block} and end_block={end_block}"
            ) from exc

        logger.info(
            "Auto scan range resolved: %s-%s",
            f"{validated_start_block:,}",
            f"{validated_end_block:,}",
        )
        return validated_start_block, validated_end_block

    async def commit_auto_scan_progress(self, last_scanned_block: int) -> None:
        await asyncio.to_thread(self._persist_auto_scan_cursor, last_scanned_block)
        logger.info("Auto scan cursor advanced to block %s", f"{last_scanned_block:,}")

    async def run_scan_sequence_for_request(
        self,
        scan_request: ScanRequest,
        *,
        persist: bool = True,
        pool_source: ReverseScanSource = ReverseScanSource.DATABASE,
    ) -> ScanSequenceRunResult:
        forward_result = await self.run_scan(scan_request, persist=persist)
        reverse_result = await self._run_reverse_scan(
            forward_result,
            persist=persist,
            pool_source=pool_source,
        )
        return ScanSequenceRunResult(
            forward_result=forward_result,
            reverse_result=reverse_result,
        )

    async def run_scan(
        self,
        scan_request: ScanRequest,
        *,
        persist: bool = True,
    ) -> ScanRunResult:
        logger.info(
            "Starting scan for blocks %s-%s (%s blocks), traders=%s, trace_role=%s, persist=%s",
            f"{scan_request.start_block:,}",
            f"{scan_request.end_block:,}",
            f"{scan_request.block_count:,}",
            f"{len(scan_request.trader_addresses):,}",
            scan_request.trace_address_role.value,
            persist,
        )

        tx_contexts = await self._transaction_discovery_service.collect_tx_contexts(scan_request)
        logger.info("Transaction discovery finished: %s transaction(s) matched", f"{len(tx_contexts):,}")
        if not tx_contexts:
            logger.info("Scan finished early: no matching transactions were found")
            return ScanRunResult(request=scan_request, payload=TradePayload())

        receipts = await self._receipt_collection_service.collect_receipts(tx_contexts.keys())
        logger.info("Receipt collection finished: %s receipt(s) loaded", f"{len(receipts):,}")
        raw_swap_events = self._swap_parsing_service.collect_swap_events(receipts, tx_contexts)
        logger.info("Swap parsing finished: %s raw swap event(s) found", f"{len(raw_swap_events):,}")
        if not raw_swap_events:
            logger.info("Scan finished early: no swap events were found in collected receipts")
            return ScanRunResult(
                request=scan_request,
                transaction_count=len(tx_contexts),
                receipt_count=len(receipts),
                payload=TradePayload(),
            )

        pool_enriched_events = await self._pool_metadata_service.enrich_events(raw_swap_events)
        logger.info(
            "Pool metadata enrichment finished: %s event(s) prepared",
            f"{len(pool_enriched_events):,}",
        )
        pricing_context = await self._trade_enrichment_service.resolve_pricing_context(
            pool_enriched_events,
        )
        pool_snapshots_by_address = await self._pool_tvl_service.resolve_pool_liquidity_snapshots(
            pricing_context.normalized_events,
            pricing_context=pricing_context,
            block_number=scan_request.end_block,
        )
        logger.info(
            "Pool TVL enrichment finished: %s pool(s) evaluated",
            f"{len(pool_snapshots_by_address):,}",
        )
        enriched_events = await self._trade_enrichment_service.enrich_events(
            pool_enriched_events,
            receipts,
            pricing_context=pricing_context,
        )
        logger.info(
            "Trade enrichment finished: %s enriched swap event(s) ready",
            f"{len(enriched_events):,}",
        )
        payload = self._trading_persistence_service.build_payload(
            enriched_events,
            pool_snapshots_by_address=pool_snapshots_by_address,
        )
        logger.info(
            "Payload prepared: dexes=%s, tokens=%s, pools=%s, transactions=%s, swaps=%s",
            f"{len(payload.dexes):,}",
            f"{len(payload.tokens):,}",
            f"{len(payload.pools):,}",
            f"{len(payload.transactions):,}",
            f"{len(payload.swaps):,}",
        )
        persist_stats = (
            await self._trading_persistence_service.persist_payload(payload)
            if persist
            else None
        )
        if persist_stats is not None:
            logger.info(
                "Persistence finished: transactions=%s inserted, %s skipped; swaps=%s inserted, %s skipped; "
                "dexes=%s, traders=%s, tokens=%s, pools=%s",
                f"{persist_stats.inserted_transactions:,}",
                f"{persist_stats.skipped_existing_transactions:,}",
                f"{persist_stats.inserted_swaps:,}",
                f"{persist_stats.skipped_existing_swaps:,}",
                f"{persist_stats.inserted_dexes:,}",
                f"{persist_stats.inserted_traders:,}",
                f"{persist_stats.inserted_tokens:,}",
                f"{persist_stats.inserted_pools:,}",
            )

        return ScanRunResult(
            request=scan_request,
            transaction_count=len(tx_contexts),
            receipt_count=len(receipts),
            raw_swap_count=len(raw_swap_events),
            enriched_swap_count=len(enriched_events),
            payload=payload,
            persist_stats=persist_stats,
        )

    def _resolve_auto_start_block(self) -> int:
        # The cursor moves after every completed scan, while `transactions` only grows when a
        # tracked trader actually traded. Without the cursor a quiet market would pin the scan
        # window to the last trade and drag it out of the node's state window.
        last_scanned_block = self._get_auto_scan_cursor()
        if last_scanned_block is not None:
            logger.info(
                "Auto scan will continue from cursor block %s",
                f"{last_scanned_block + 1:,}",
            )
            return last_scanned_block + 1

        last_persisted_block = self._get_last_persisted_block()
        if last_persisted_block is None:
            logger.info(
                "No persisted transactions found; auto scan will start from configured block %s",
                f"{self._settings.start_block:,}",
            )
            return self._settings.start_block

        logger.info(
            "Auto scan will start from last persisted block %s",
            f"{last_persisted_block:,}",
        )
        return last_persisted_block

    @staticmethod
    def _get_last_persisted_block() -> int | None:
        with session_scope() as session:
            last_persisted_block = session.execute(
                select(func.max(Transaction.block_number))
            ).scalar()

        if last_persisted_block is None:
            return None

        return int(last_persisted_block)

    @staticmethod
    def _get_auto_scan_cursor() -> int | None:
        with session_scope() as session:
            last_scanned_block = session.execute(
                select(func.max(ScanCursor.last_scanned_block)).where(
                    ScanCursor.name == AUTO_SCAN_CURSOR_NAME,
                )
            ).scalar()

        if last_scanned_block is None:
            return None

        return int(last_scanned_block)

    @staticmethod
    def _persist_auto_scan_cursor(last_scanned_block: int) -> None:
        with session_scope() as session:
            session.execute(
                ScanCursor.__table__.insert(),
                [
                    {
                        "name": AUTO_SCAN_CURSOR_NAME,
                        "last_scanned_block": last_scanned_block,
                        "updated_at": datetime.now(UTC).replace(tzinfo=None),
                    }
                ],
            )

    async def run_reverse_scan(
        self,
        reverse_scan_request: ReverseScanRequest,
        *,
        persist: bool = True,
    ) -> ReverseScanResult:
        logger.info(
            "Starting reverse scan for blocks %s-%s (%s blocks), pools=%s, excluded_competitors=%s",
            f"{reverse_scan_request.start_block:,}",
            f"{reverse_scan_request.end_block:,}",
            f"{reverse_scan_request.block_count:,}",
            f"{len(reverse_scan_request.pool_addresses):,}",
            f"{reverse_scan_request.excluded_competitor_pair_count:,}",
        )
        discovery = await self._reverse_search_service.collect_pool_competition(
            reverse_scan_request,
        )
        reverse_result = await self._reverse_candidate_scoring_service.apply_heuristics(
            discovery,
        )
        logger.info(
            "Reverse scan finished: pools=%s, active_pools=%s, discovered=%s, approved=%s, "
            "rejected=%s, transactions=%s",
            f"{len(reverse_result.pools):,}",
            f"{reverse_result.active_pool_count:,}",
            f"{reverse_result.discovered_competitor_count:,}",
            f"{reverse_result.approved_competitor_count:,}",
            f"{reverse_result.rejected_competitor_count:,}",
            f"{reverse_result.total_transaction_count:,}",
        )
        if persist:
            inserted_traders = await self._persist_reverse_competitors(reverse_result)
            logger.info(
                "Reverse scan persistence finished: inserted_traders=%s",
                f"{inserted_traders:,}",
            )
        return reverse_result

    async def run_reverse_scan_from_database(
        self,
        *,
        start_block: int,
        end_block: int,
        persist: bool = True,
    ) -> ReverseScanResult | None:
        reverse_scan_request = await self._build_database_reverse_scan_request(
            start_block=start_block,
            end_block=end_block,
        )
        if reverse_scan_request is None:
            logger.info("Reverse scan skipped: no pool addresses were resolved from the database")
            return None

        return await self.run_reverse_scan(reverse_scan_request, persist=persist)

    async def _run_reverse_scan(
        self,
        forward_result: ScanRunResult,
        *,
        persist: bool,
        pool_source: ReverseScanSource,
    ) -> ReverseScanResult | None:
        reverse_scan_request = await self._build_reverse_scan_request(
            forward_result,
            pool_source=pool_source,
        )
        if reverse_scan_request is None:
            logger.info(
                "Reverse scan skipped: no pool addresses were resolved for source '%s'",
                pool_source.value,
            )
            return None
        return await self.run_reverse_scan(reverse_scan_request, persist=persist)

    async def _build_reverse_scan_request(
        self,
        forward_result: ScanRunResult,
        *,
        pool_source: ReverseScanSource,
    ) -> ReverseScanRequest | None:
        pool_addresses = await self._resolve_reverse_pool_addresses(
            forward_result,
            pool_source=pool_source,
        )
        if not pool_addresses:
            return None

        return ReverseScanRequest(
            start_block=forward_result.request.start_block,
            end_block=forward_result.request.end_block,
            pool_addresses=pool_addresses,
            excluded_competitor_addresses=forward_result.request.trader_addresses,
        )

    async def _resolve_reverse_pool_addresses(
        self,
        forward_result: ScanRunResult,
        *,
        pool_source: ReverseScanSource,
    ) -> list[str]:
        direct_pool_addresses = sorted(
            {
                pool.contract_address
                for pool in (forward_result.payload.pools if forward_result.payload is not None else [])
                if pool.contract_address
            }
        )
        database_pool_addresses = (
            await self._pool_registry_service.load_pool_addresses()
            if pool_source in {ReverseScanSource.DATABASE, ReverseScanSource.MIXED}
            else []
        )

        logger.info(
            "Resolved reverse pool source '%s': direct=%s, database=%s",
            pool_source.value,
            f"{len(direct_pool_addresses):,}",
            f"{len(database_pool_addresses):,}",
        )

        if pool_source == ReverseScanSource.FORWARD:
            return direct_pool_addresses
        if pool_source == ReverseScanSource.DATABASE:
            return database_pool_addresses

        return sorted(set(direct_pool_addresses) | set(database_pool_addresses))

    async def _build_database_reverse_scan_request(
        self,
        *,
        start_block: int,
        end_block: int,
    ) -> ReverseScanRequest | None:
        pool_competitors = await self._pool_registry_service.load_pool_competitors()
        if not pool_competitors:
            return None

        return ReverseScanRequest(
            start_block=start_block,
            end_block=end_block,
            pool_addresses=sorted(pool_competitors),
            excluded_competitor_addresses_by_pool=pool_competitors,
        )

    async def _persist_reverse_competitors(
        self,
        reverse_result: ReverseScanResult,
    ) -> int:
        competitor_addresses = [
            competitor_address
            for pool in reverse_result.pools
            for competitor_address in pool.competitor_addresses
            if competitor_address
        ]
        if not competitor_addresses:
            logger.info("Reverse scan persistence skipped: no competitor addresses were discovered")
            return 0

        return await self._trader_registry_service.persist_traders(
            competitor_addresses,
            label="unknown",
        )
