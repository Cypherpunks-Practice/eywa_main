from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import or_, select

from ..core.database import session_scope
from ..models.assets import Token
from ..models.liquidity import LiquidityPool
from ..models.trading import Swap, Transaction
from ..schemas import BackfillStats, DexKind, EnrichedSwapEvent, PoolLiquiditySnapshot
from .pool_metadata_service import PoolMetadataService
from .pool_tvl_service import PoolTvlService
from .token_metadata_service import TokenMetadataService
from .trade_enrichment_service import TradeEnrichmentService

logger = logging.getLogger(__name__)
DEFAULT_BACKFILL_CHUNK_SIZE = 250


@dataclass(frozen=True)
class HistoricalPoolContext:
    pool_address: str
    transaction_hash_id: str
    block_number: int
    token_a_address: str
    token_b_address: str
    amount_a_raw: int
    amount_b_raw: int


class DatabaseBackfillService:
    def __init__(
        self,
        token_metadata_service: TokenMetadataService,
        pool_metadata_service: PoolMetadataService,
        pool_tvl_service: PoolTvlService,
        trade_enrichment_service: TradeEnrichmentService,
    ) -> None:
        self._token_metadata_service = token_metadata_service
        self._pool_metadata_service = pool_metadata_service
        self._pool_tvl_service = pool_tvl_service
        self._trade_enrichment_service = trade_enrichment_service

    async def backfill_database(self) -> BackfillStats:
        chunk_size = DEFAULT_BACKFILL_CHUNK_SIZE
        stats = BackfillStats()
        await self._backfill_token_labels(stats, chunk_size=chunk_size)
        await self._backfill_pool_snapshots(stats, chunk_size=chunk_size)
        return stats

    async def _backfill_token_labels(
        self,
        stats: BackfillStats,
        *,
        chunk_size: int,
    ) -> None:
        cursor: str | None = None
        batch_index = 0

        while True:
            addresses = await asyncio.to_thread(
                self._load_token_candidates_batch,
                cursor,
                chunk_size,
            )
            if not addresses:
                break

            batch_index += 1
            cursor = addresses[-1]
            stats.tokens.scanned += len(addresses)

            metadata_by_address = await self._token_metadata_service.resolve_token_metadata(addresses)
            updated, skipped, unresolved = await asyncio.to_thread(
                self._apply_token_backfill_batch,
                metadata_by_address,
            )
            stats.tokens.updated += updated
            stats.tokens.skipped += skipped
            stats.tokens.unresolved += unresolved

            logger.info(
                "Token backfill batch %s processed: scanned=%s, updated=%s, skipped=%s, unresolved=%s",
                batch_index,
                f"{len(addresses):,}",
                f"{updated:,}",
                f"{skipped:,}",
                f"{unresolved:,}",
            )

        logger.info(
            "Token backfill finished: scanned=%s, updated=%s, skipped=%s, unresolved=%s",
            f"{stats.tokens.scanned:,}",
            f"{stats.tokens.updated:,}",
            f"{stats.tokens.skipped:,}",
            f"{stats.tokens.unresolved:,}",
        )

    async def _backfill_pool_snapshots(
        self,
        stats: BackfillStats,
        *,
        chunk_size: int,
    ) -> None:
        cursor: str | None = None
        batch_index = 0

        while True:
            pool_addresses = await asyncio.to_thread(
                self._load_pool_candidates_batch,
                cursor,
                chunk_size,
            )
            if not pool_addresses:
                break

            batch_index += 1
            cursor = pool_addresses[-1]
            stats.pools.scanned += len(pool_addresses)

            contexts_by_pool = await asyncio.to_thread(
                self._load_latest_pool_contexts,
                pool_addresses,
            )
            unresolved = len(pool_addresses) - len(contexts_by_pool)

            events_by_pool, unsupported_count = await self._build_pool_backfill_events(
                contexts_by_pool,
            )
            unresolved += unsupported_count

            snapshots_by_pool, snapshot_unresolved = await self._resolve_pool_snapshots(
                events_by_pool,
            )
            unresolved += snapshot_unresolved

            updated, skipped = await asyncio.to_thread(
                self._apply_pool_snapshot_batch,
                snapshots_by_pool,
            )
            stats.pools.updated += updated
            stats.pools.skipped += skipped
            stats.pools.unresolved += unresolved

            logger.info(
                "Pool backfill batch %s processed: scanned=%s, updated=%s, skipped=%s, unresolved=%s",
                batch_index,
                f"{len(pool_addresses):,}",
                f"{updated:,}",
                f"{skipped:,}",
                f"{unresolved:,}",
            )

        logger.info(
            "Pool backfill finished: scanned=%s, updated=%s, skipped=%s, unresolved=%s",
            f"{stats.pools.scanned:,}",
            f"{stats.pools.updated:,}",
            f"{stats.pools.skipped:,}",
            f"{stats.pools.unresolved:,}",
        )

    @staticmethod
    def _load_token_candidates_batch(
        cursor: str | None,
        chunk_size: int,
    ) -> list[str]:
        condition = or_(
            Token.label.is_(None),
            Token.label == "",
            Token.label.like("unknown_token_%"),
        )

        with session_scope() as session:
            query = (
                select(Token.contract_address)
                .where(condition)
                .order_by(Token.contract_address)
                .limit(chunk_size)
            )
            if cursor is not None:
                query = query.where(Token.contract_address > cursor)

            rows = session.execute(query).all()

        return [
            contract_address
            for (contract_address,) in rows
            if contract_address
        ]

    @classmethod
    def _apply_token_backfill_batch(
        cls,
        metadata_by_address: Mapping[str, object],
    ) -> tuple[int, int, int]:
        addresses = sorted(metadata_by_address)
        if not addresses:
            return 0, 0, 0

        updated = 0
        skipped = 0
        unresolved = 0
        token_table = Token.__table__

        with session_scope() as session:
            rows = session.execute(
                select(
                    Token.contract_address,
                    Token.label,
                    Token.is_stable,
                ).where(Token.contract_address.in_(addresses))
            ).all()

            rows_by_address = {
                contract_address: (label, bool(is_stable))
                for contract_address, label, is_stable in rows
                if contract_address is not None
            }

            for address in addresses:
                row = rows_by_address.get(address)
                if row is None:
                    skipped += 1
                    continue

                current_label, current_is_stable = row
                metadata = metadata_by_address[address]
                values: dict[str, object] = {}

                if TokenMetadataService.label_needs_resolution(current_label):
                    resolved_label = getattr(metadata, "label", None)
                    if not TokenMetadataService.label_needs_resolution(resolved_label):
                        values["label"] = resolved_label

                if getattr(metadata, "is_stable", False) and not current_is_stable:
                    values["is_stable"] = True

                if values:
                    session.execute(
                        token_table.update()
                        .where(token_table.c.contract_address == address)
                        .values(**values)
                    )
                    updated += 1
                elif TokenMetadataService.label_needs_resolution(current_label):
                    unresolved += 1
                else:
                    skipped += 1

        return updated, skipped, unresolved

    @staticmethod
    def _load_pool_candidates_batch(
        cursor: str | None,
        chunk_size: int,
    ) -> list[str]:
        condition = or_(
            LiquidityPool.approx_tvl_usd.is_(None),
            LiquidityPool.token_a_usd_share.is_(None),
            LiquidityPool.liquidity_snapshot_block_number.is_(None),
        )

        with session_scope() as session:
            query = (
                select(LiquidityPool.contract_address)
                .where(condition)
                .order_by(LiquidityPool.contract_address)
                .limit(chunk_size)
            )
            if cursor is not None:
                query = query.where(LiquidityPool.contract_address > cursor)

            rows = session.execute(query).all()

        return [
            contract_address
            for (contract_address,) in rows
            if contract_address
        ]

    @staticmethod
    def _load_latest_pool_contexts(
        pool_addresses: Sequence[str],
    ) -> dict[str, HistoricalPoolContext]:
        if not pool_addresses:
            return {}

        with session_scope() as session:
            rows = session.execute(
                select(
                    Swap.pool_address,
                    Transaction.block_number,
                    Swap.id,
                    Swap.transaction_hash_id,
                    Swap.token_a_address,
                    Swap.token_b_address,
                    Swap.amount_a,
                    Swap.amount_b,
                )
                .select_from(Swap)
                .join(Transaction, Transaction.hash_id == Swap.transaction_hash_id)
                .where(Swap.pool_address.in_(list(pool_addresses)))
                .where(Swap.token_a_address.is_not(None))
                .where(Swap.token_b_address.is_not(None))
                .where(Swap.amount_a.is_not(None))
                .where(Swap.amount_b.is_not(None))
                .order_by(
                    Swap.pool_address,
                    Transaction.block_number.desc(),
                    Swap.id.desc(),
                )
            ).all()

        contexts_by_pool: dict[str, HistoricalPoolContext] = {}
        for (
            pool_address,
            block_number,
            _swap_id,
            transaction_hash_id,
            token_a_address,
            token_b_address,
            amount_a,
            amount_b,
        ) in rows:
            if pool_address is None or pool_address in contexts_by_pool:
                continue
            if (
                block_number is None
                or transaction_hash_id is None
                or token_a_address is None
                or token_b_address is None
                or amount_a is None
                or amount_b is None
            ):
                continue

            contexts_by_pool[pool_address] = HistoricalPoolContext(
                pool_address=pool_address,
                transaction_hash_id=transaction_hash_id,
                block_number=int(block_number),
                token_a_address=token_a_address,
                token_b_address=token_b_address,
                amount_a_raw=int(amount_a),
                amount_b_raw=int(amount_b),
            )

        return contexts_by_pool

    async def _build_pool_backfill_events(
        self,
        contexts_by_pool: Mapping[str, HistoricalPoolContext],
    ) -> tuple[dict[str, EnrichedSwapEvent], int]:
        if not contexts_by_pool:
            return {}, 0

        pool_addresses = sorted(contexts_by_pool)
        fee_tiers = await self._pool_metadata_service.resolve_pool_fee_tiers(pool_addresses)
        token_pairs = await self._pool_metadata_service.resolve_pool_tokens(pool_addresses)

        events_by_pool: dict[str, EnrichedSwapEvent] = {}
        unsupported = 0

        for pool_address in pool_addresses:
            context = contexts_by_pool[pool_address]
            fee_tier = fee_tiers.get(pool_address)
            token_a_address, token_b_address = token_pairs.get(pool_address, (None, None))

            if fee_tier is not None:
                dex_kind = DexKind.UNISWAP_V3
            elif token_a_address is not None and token_b_address is not None:
                dex_kind = DexKind.UNI_V2_OR_SUSHI
            else:
                unsupported += 1
                continue

            events_by_pool[pool_address] = EnrichedSwapEvent(
                tx_hash=context.transaction_hash_id,
                block_number=context.block_number,
                pool_address=context.pool_address,
                dex_kind=dex_kind,
                fee_tier=fee_tier,
                token_a_address=context.token_a_address,
                token_b_address=context.token_b_address,
                amount_a_raw=context.amount_a_raw,
                amount_b_raw=context.amount_b_raw,
            )

        return events_by_pool, unsupported

    async def _resolve_pool_snapshots(
        self,
        events_by_pool: Mapping[str, EnrichedSwapEvent],
    ) -> tuple[dict[str, PoolLiquiditySnapshot], int]:
        if not events_by_pool:
            return {}, 0

        pricing_context = await self._trade_enrichment_service.resolve_pricing_context(
            list(events_by_pool.values()),
        )
        events_by_block: dict[int, list[EnrichedSwapEvent]] = defaultdict(list)
        for event in pricing_context.normalized_events:
            if event.block_number is None or event.block_number <= 0:
                continue
            events_by_block[event.block_number].append(event)

        snapshots_by_pool: dict[str, PoolLiquiditySnapshot] = {}
        for block_number, block_events in sorted(events_by_block.items()):
            resolved = await self._pool_tvl_service.resolve_pool_liquidity_snapshots(
                block_events,
                pricing_context=pricing_context,
                block_number=block_number,
            )
            for pool_address, snapshot in resolved.items():
                if self._has_complete_snapshot(snapshot):
                    snapshots_by_pool[pool_address] = snapshot

        unresolved = len(events_by_pool) - len(snapshots_by_pool)
        return snapshots_by_pool, unresolved

    @classmethod
    def _apply_pool_snapshot_batch(
        cls,
        snapshots_by_pool: Mapping[str, PoolLiquiditySnapshot],
    ) -> tuple[int, int]:
        pool_addresses = sorted(snapshots_by_pool)
        if not pool_addresses:
            return 0, 0

        updated = 0
        skipped = 0
        pool_table = LiquidityPool.__table__

        with session_scope() as session:
            rows = session.execute(
                select(
                    LiquidityPool.contract_address,
                    LiquidityPool.approx_tvl_usd,
                    LiquidityPool.token_a_usd_share,
                    LiquidityPool.liquidity_snapshot_block_number,
                ).where(LiquidityPool.contract_address.in_(pool_addresses))
            ).all()

            existing_rows = {
                contract_address: (
                    approx_tvl_usd,
                    token_a_usd_share,
                    liquidity_snapshot_block_number,
                )
                for (
                    contract_address,
                    approx_tvl_usd,
                    token_a_usd_share,
                    liquidity_snapshot_block_number,
                ) in rows
                if contract_address is not None
            }

            for pool_address in pool_addresses:
                existing_row = existing_rows.get(pool_address)
                if existing_row is None:
                    skipped += 1
                    continue
                if cls._row_has_complete_snapshot(*existing_row):
                    skipped += 1
                    continue

                snapshot = snapshots_by_pool[pool_address]
                session.execute(
                    pool_table.update()
                    .where(pool_table.c.contract_address == pool_address)
                    .values(
                        approx_tvl_usd=snapshot.approx_tvl_usd,
                        token_a_usd_share=snapshot.token_a_usd_share,
                        liquidity_snapshot_block_number=snapshot.liquidity_snapshot_block_number,
                    )
                )
                updated += 1

        return updated, skipped

    @staticmethod
    def _has_complete_snapshot(snapshot: PoolLiquiditySnapshot) -> bool:
        return DatabaseBackfillService._row_has_complete_snapshot(
            snapshot.approx_tvl_usd,
            snapshot.token_a_usd_share,
            snapshot.liquidity_snapshot_block_number,
        )

    @staticmethod
    def _row_has_complete_snapshot(
        approx_tvl_usd: object,
        token_a_usd_share: object,
        liquidity_snapshot_block_number: object,
    ) -> bool:
        return (
            approx_tvl_usd is not None
            and token_a_usd_share is not None
            and liquidity_snapshot_block_number is not None
        )
