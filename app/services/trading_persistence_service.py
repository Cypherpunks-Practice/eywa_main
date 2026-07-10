from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from ..core.config import Settings
from ..core.database import session_scope
from ..models.assets import Token
from ..models.liquidity import Dex, LiquidityPool
from ..models.participants import Trader
from ..models.trading import Swap, Transaction
from ..schemas import (
    DexKind,
    DexWrite,
    EnrichedSwapEvent,
    PersistStats,
    PoolLiquiditySnapshot,
    PoolWrite,
    SwapWrite,
    TokenWrite,
    TradePayload,
    TransactionWrite,
)

UNKNOWN_DEX_FACTORY = "__unknown__"
UNKNOWN_DEX_LABEL = "unknown_dex"
SwapFingerprint = tuple[str, str, str | None, str | None, int, Decimal | None, int | None, int | None]
logger = logging.getLogger(__name__)


class TradingPersistenceService:
    def __init__(self, settings: Settings):
        self._settings = settings

    def build_payload(
        self,
        events: Sequence[EnrichedSwapEvent],
        *,
        pool_snapshots_by_address: Mapping[str, PoolLiquiditySnapshot] | None = None,
    ) -> TradePayload:
        dexes_by_factory: dict[str, DexWrite] = {}
        tokens_by_address: dict[str, TokenWrite] = {}
        pools_by_address: dict[str, PoolWrite] = {}
        transactions_by_hash: dict[str, TransactionWrite] = {}
        swaps: list[SwapWrite] = []
        resolved_pool_snapshots_by_address = dict(pool_snapshots_by_address or {})

        for event in events:
            dex_factory = event.dex_factory or UNKNOWN_DEX_FACTORY
            if dex_factory == UNKNOWN_DEX_FACTORY:
                dexes_by_factory.setdefault(
                    UNKNOWN_DEX_FACTORY,
                    DexWrite(factory=UNKNOWN_DEX_FACTORY, label=UNKNOWN_DEX_LABEL),
                )
            else:
                dexes_by_factory.setdefault(
                    dex_factory,
                    DexWrite(
                        factory=dex_factory,
                        label=self.build_unknown_dex_label(dex_factory),
                    ),
                )

            if event.token_a_address:
                tokens_by_address[event.token_a_address] = TokenWrite(
                    contract_address=event.token_a_address,
                    label=event.token_a_label or self.build_unknown_token_label(event.token_a_address),
                    decimals=event.token_a_decimals or 18,
                    is_stable=bool(event.token_a_is_stable),
                )
            if event.token_b_address:
                tokens_by_address[event.token_b_address] = TokenWrite(
                    contract_address=event.token_b_address,
                    label=event.token_b_label or self.build_unknown_token_label(event.token_b_address),
                    decimals=event.token_b_decimals or 18,
                    is_stable=bool(event.token_b_is_stable),
                )

            fee_tier = event.fee_tier
            if fee_tier is None and event.dex_kind == DexKind.UNI_V2_OR_SUSHI:
                fee_tier = self._settings.default_v2_fee_tier

            pool_snapshot = resolved_pool_snapshots_by_address.get(event.pool_address)
            candidate_pool = PoolWrite(
                contract_address=event.pool_address,
                dex_factory=dex_factory,
                fee_tier=fee_tier,
                **self._pool_snapshot_values(pool_snapshot),
            )
            existing_pool = pools_by_address.get(event.pool_address)
            if existing_pool is None or (
                existing_pool.dex_factory == UNKNOWN_DEX_FACTORY
                and candidate_pool.dex_factory != UNKNOWN_DEX_FACTORY
            ) or (existing_pool.fee_tier is None and candidate_pool.fee_tier is not None):
                pools_by_address[event.pool_address] = candidate_pool
            elif self._prefer_newer_pool_snapshot(existing_pool, candidate_pool):
                pools_by_address[event.pool_address] = existing_pool.model_copy(
                    update={
                        "approx_tvl_usd": candidate_pool.approx_tvl_usd,
                        "token_a_usd_share": candidate_pool.token_a_usd_share,
                        "liquidity_snapshot_block_number": (
                            candidate_pool.liquidity_snapshot_block_number
                        ),
                    }
                )

            if (
                event.tx_hash not in transactions_by_hash
                and event.block_number is not None
                and event.block_number > 0
                and event.trader_address
            ):
                transactions_by_hash[event.tx_hash] = TransactionWrite(
                    hash_id=event.tx_hash,
                    block_number=event.block_number,
                    trader_address=event.trader_address,
                    bribe=event.bribe_wei,
                    priority_fee=event.priority_fee_wei,
                )
            elif event.tx_hash in transactions_by_hash:
                existing_transaction = transactions_by_hash[event.tx_hash]
                updated_values: dict[str, Any] = {
                    "bribe": max(existing_transaction.bribe or 0, event.bribe_wei),
                    "priority_fee": max(existing_transaction.priority_fee or 0, event.priority_fee_wei),
                }
                if not existing_transaction.trader_address and event.trader_address:
                    updated_values["trader_address"] = event.trader_address
                if (
                    existing_transaction.block_number <= 0
                    and event.block_number is not None
                    and event.block_number > 0
                ):
                    updated_values["block_number"] = event.block_number
                transactions_by_hash[event.tx_hash] = existing_transaction.model_copy(
                    update=updated_values
                )

            if event.side is None:
                continue

            swaps.append(
                SwapWrite(
                    transaction_hash_id=event.tx_hash,
                    pool_address=event.pool_address,
                    token_a_address=event.token_a_address,
                    token_b_address=event.token_b_address,
                    side=event.side,
                    usd_amount=event.usd_amount,
                    amount_a=event.amount_a_raw if event.amount_a_raw > 0 else None,
                    amount_b=event.amount_b_raw if event.amount_b_raw > 0 else None,
                )
            )

        valid_transaction_hashes = set(transactions_by_hash)
        filtered_swaps = [
            swap
            for swap in swaps
            if swap.transaction_hash_id in valid_transaction_hashes
        ]

        return TradePayload(
            dexes=list(dexes_by_factory.values()),
            tokens=list(tokens_by_address.values()),
            pools=list(pools_by_address.values()),
            transactions=list(transactions_by_hash.values()),
            swaps=filtered_swaps,
        )

    async def persist_payload(self, payload: TradePayload) -> PersistStats:
        return await asyncio.to_thread(self._persist_payload_sync, payload)

    def _persist_payload_sync(self, payload: TradePayload) -> PersistStats:
        logger.info(
            "Persisting payload: dexes=%s, tokens=%s, pools=%s, transactions=%s, swaps=%s",
            f"{len(payload.dexes):,}",
            f"{len(payload.tokens):,}",
            f"{len(payload.pools):,}",
            f"{len(payload.transactions):,}",
            f"{len(payload.swaps):,}",
        )
        stats = PersistStats()
        with session_scope() as session:
            existing_dex_factories = self._select_existing_values(
                session,
                Dex.factory,
                [row.factory for row in payload.dexes],
            )
            new_dexes = [
                row.model_dump()
                for row in payload.dexes
                if row.factory not in existing_dex_factories
            ]
            if new_dexes:
                session.execute(Dex.__table__.insert(), new_dexes)
            stats.inserted_dexes = len(new_dexes)

            existing_transaction_hashes = self._select_existing_values(
                session,
                Transaction.hash_id,
                [row.hash_id for row in payload.transactions],
            )
            stats.skipped_existing_transactions = len(existing_transaction_hashes)
            new_transactions = [
                row
                for row in payload.transactions
                if row.hash_id not in existing_transaction_hashes
            ]
            valid_transaction_hashes = existing_transaction_hashes | {
                row.hash_id for row in new_transactions
            }

            trader_addresses = [row.trader_address for row in new_transactions]
            existing_trader_addresses = self._select_existing_values(
                session,
                Trader.contract_address,
                trader_addresses,
            )
            new_traders = [
                {"contract_address": trader_address, "label": "unknown"}
                for trader_address in sorted(set(trader_addresses))
                if trader_address not in existing_trader_addresses
            ]
            if new_traders:
                session.execute(Trader.__table__.insert(), new_traders)
            stats.inserted_traders = len(new_traders)

            existing_token_rows = self._select_existing_token_rows(
                session,
                [row.contract_address for row in payload.tokens],
            )
            existing_token_addresses = set(existing_token_rows)
            new_tokens = [
                row.model_dump()
                for row in payload.tokens
                if row.contract_address not in existing_token_addresses
            ]
            if new_tokens:
                session.execute(Token.__table__.insert(), new_tokens)
            stats.inserted_tokens = len(new_tokens)
            self._update_existing_token_labels(session, payload.tokens, existing_token_rows)

            stable_token_addresses = sorted(
                {
                    row.contract_address
                    for row in payload.tokens
                    if row.is_stable
                }
            )
            if stable_token_addresses:
                token_table = Token.__table__
                session.execute(
                    token_table.update()
                    .where(token_table.c.contract_address.in_(stable_token_addresses))
                    .where(token_table.c.is_stable != True)  # noqa: E712
                    .values(is_stable=True)
                )

            existing_pool_rows = self._select_existing_pool_rows(
                session,
                [row.contract_address for row in payload.pools],
            )
            existing_pool_addresses = set(existing_pool_rows)
            new_pools = [
                row.model_dump()
                for row in payload.pools
                if row.contract_address not in existing_pool_addresses
            ]
            if new_pools:
                session.execute(LiquidityPool.__table__.insert(), new_pools)
            stats.inserted_pools = len(new_pools)
            self._update_existing_pool_liquidity_snapshot(
                session,
                payload.pools,
                existing_pool_rows,
            )
            valid_pool_addresses = existing_pool_addresses | {
                row["contract_address"]
                for row in new_pools
            }

            if new_transactions:
                session.execute(
                    Transaction.__table__.insert(),
                    [row.model_dump() for row in new_transactions],
                )
            stats.inserted_transactions = len(new_transactions)

            existing_swap_fingerprints = self._select_existing_swap_fingerprints(
                session,
                valid_transaction_hashes,
            )

            new_swaps = [
                row
                for row in self._build_new_swaps(
                    payload.swaps,
                    existing_fingerprints=existing_swap_fingerprints,
                    valid_transaction_hashes=valid_transaction_hashes,
                    valid_pool_addresses=valid_pool_addresses,
                )
            ]
            stats.skipped_existing_swaps = len(payload.swaps) - len(new_swaps)
            if new_swaps:
                max_swap_id = session.execute(select(func.max(Swap.id))).scalar() or 0
                next_swap_id = int(max_swap_id) + 1
                for row in new_swaps:
                    row["id"] = next_swap_id
                    next_swap_id += 1
                session.execute(Swap.__table__.insert(), new_swaps)
            stats.inserted_swaps = len(new_swaps)

        logger.info(
            "Persistence summary: inserted dexes=%s, traders=%s, tokens=%s, pools=%s, "
            "transactions=%s, skipped existing transactions=%s, swaps=%s, skipped existing swaps=%s",
            f"{stats.inserted_dexes:,}",
            f"{stats.inserted_traders:,}",
            f"{stats.inserted_tokens:,}",
            f"{stats.inserted_pools:,}",
            f"{stats.inserted_transactions:,}",
            f"{stats.skipped_existing_transactions:,}",
            f"{stats.inserted_swaps:,}",
            f"{stats.skipped_existing_swaps:,}",
        )
        return stats

    @staticmethod
    def build_unknown_dex_label(factory: str) -> str:
        if factory == UNKNOWN_DEX_FACTORY:
            return UNKNOWN_DEX_LABEL
        return f"unknown_dex_{factory[2:10]}"

    @staticmethod
    def build_unknown_token_label(contract_address: str) -> str:
        return f"unknown_token_{contract_address[2:10]}"

    @staticmethod
    def _label_needs_update(label: str | None) -> bool:
        if label is None:
            return True

        normalized_label = label.strip()
        return not normalized_label or normalized_label.startswith("unknown_token_")

    @classmethod
    def _prefer_newer_pool_snapshot(
        cls,
        current_pool: PoolWrite,
        candidate_pool: PoolWrite,
    ) -> bool:
        if not cls._has_complete_pool_snapshot(candidate_pool):
            return False
        if current_pool.liquidity_snapshot_block_number is None:
            return True
        return (
            candidate_pool.liquidity_snapshot_block_number
            >= current_pool.liquidity_snapshot_block_number
        )

    @staticmethod
    def _pool_snapshot_values(
        snapshot: PoolLiquiditySnapshot | None,
    ) -> dict[str, Decimal | float | int | None]:
        if snapshot is None:
            return {}
        if (
            snapshot.approx_tvl_usd is None
            or snapshot.token_a_usd_share is None
            or snapshot.liquidity_snapshot_block_number is None
        ):
            return {}
        return {
            "approx_tvl_usd": snapshot.approx_tvl_usd,
            "token_a_usd_share": snapshot.token_a_usd_share,
            "liquidity_snapshot_block_number": snapshot.liquidity_snapshot_block_number,
        }

    @staticmethod
    def _has_complete_pool_snapshot(pool: PoolWrite) -> bool:
        return (
            pool.approx_tvl_usd is not None
            and pool.token_a_usd_share is not None
            and pool.liquidity_snapshot_block_number is not None
        )

    @classmethod
    def _build_new_swaps(
        cls,
        swaps: Sequence[SwapWrite],
        *,
        existing_fingerprints: Counter[SwapFingerprint],
        valid_transaction_hashes: set[str],
        valid_pool_addresses: set[str],
    ) -> list[dict[str, Any]]:
        remaining_existing = existing_fingerprints.copy()
        new_swaps: list[dict[str, Any]] = []

        for swap in swaps:
            if swap.transaction_hash_id not in valid_transaction_hashes:
                continue
            if swap.pool_address not in valid_pool_addresses:
                continue

            fingerprint = cls._swap_fingerprint_from_write(swap)
            if remaining_existing[fingerprint] > 0:
                remaining_existing[fingerprint] -= 1
                continue

            new_swaps.append(swap.model_dump())

        return new_swaps

    @classmethod
    def _select_existing_swap_fingerprints(
        cls,
        session: Any,
        transaction_hashes: set[str | None],
        *,
        chunk_size: int = 1000,
    ) -> Counter[SwapFingerprint]:
        fingerprints: Counter[SwapFingerprint] = Counter()
        transaction_hash_list = [tx_hash for tx_hash in transaction_hashes if tx_hash is not None]

        for index in range(0, len(transaction_hash_list), chunk_size):
            batch = transaction_hash_list[index: index + chunk_size]
            if not batch:
                continue

            rows = session.execute(
                select(
                    Swap.transaction_hash_id,
                    Swap.pool_address,
                    Swap.token_a_address,
                    Swap.token_b_address,
                    Swap.side,
                    Swap.usd_amount,
                    Swap.amount_a,
                    Swap.amount_b,
                ).where(Swap.transaction_hash_id.in_(batch))
            ).all()
            for row in rows:
                fingerprint = cls._swap_fingerprint(
                    transaction_hash_id=row[0],
                    pool_address=row[1],
                    token_a_address=row[2],
                    token_b_address=row[3],
                    side=row[4],
                    usd_amount=row[5],
                    amount_a=row[6],
                    amount_b=row[7],
                )
                fingerprints[fingerprint] += 1

        return fingerprints

    @classmethod
    def _swap_fingerprint_from_write(cls, swap: SwapWrite) -> SwapFingerprint:
        return cls._swap_fingerprint(
            transaction_hash_id=swap.transaction_hash_id,
            pool_address=swap.pool_address,
            token_a_address=swap.token_a_address,
            token_b_address=swap.token_b_address,
            side=swap.side,
            usd_amount=swap.usd_amount,
            amount_a=swap.amount_a,
            amount_b=swap.amount_b,
        )

    @staticmethod
    def _swap_fingerprint(
        *,
        transaction_hash_id: str,
        pool_address: str,
        token_a_address: str | None,
        token_b_address: str | None,
        side: Any,
        usd_amount: Decimal | None,
        amount_a: int | None,
        amount_b: int | None,
    ) -> SwapFingerprint:
        return (
            transaction_hash_id,
            pool_address,
            token_a_address,
            token_b_address,
            int(side),
            usd_amount,
            amount_a,
            amount_b,
        )

    @staticmethod
    def _select_existing_values(
        session: Any,
        model_field: Any,
        values: Sequence[Any],
        *,
        chunk_size: int = 1000,
    ) -> set[Any]:
        existing: set[Any] = set()
        values_list = [value for value in values if value is not None]
        for index in range(0, len(values_list), chunk_size):
            batch = values_list[index: index + chunk_size]
            if not batch:
                continue
            rows = session.execute(select(model_field).where(model_field.in_(batch))).all()
            existing.update(row[0] for row in rows if row and row[0] is not None)
        return existing

    @classmethod
    def _select_existing_token_rows(
        cls,
        session: Any,
        contract_addresses: Sequence[str],
        *,
        chunk_size: int = 1000,
    ) -> dict[str, str | None]:
        existing_rows: dict[str, str | None] = {}
        address_list = [address for address in contract_addresses if address]

        for index in range(0, len(address_list), chunk_size):
            batch = address_list[index: index + chunk_size]
            if not batch:
                continue
            rows = session.execute(
                select(Token.contract_address, Token.label).where(Token.contract_address.in_(batch))
            ).all()
            existing_rows.update(
                {
                    contract_address: label
                    for contract_address, label in rows
                    if contract_address is not None
                }
            )

        return existing_rows

    @classmethod
    def _update_existing_token_labels(
        cls,
        session: Any,
        tokens: Sequence[TokenWrite],
        existing_token_rows: Mapping[str, str | None],
    ) -> None:
        token_table = Token.__table__
        for token in tokens:
            existing_label = existing_token_rows.get(token.contract_address)
            if token.contract_address not in existing_token_rows:
                continue
            if token.label is None or cls._label_needs_update(token.label):
                continue
            if not cls._label_needs_update(existing_label):
                continue

            session.execute(
                token_table.update()
                .where(token_table.c.contract_address == token.contract_address)
                .values(label=token.label)
            )

    @classmethod
    def _select_existing_pool_rows(
        cls,
        session: Any,
        contract_addresses: Sequence[str],
        *,
        chunk_size: int = 1000,
    ) -> dict[str, int | None]:
        existing_rows: dict[str, int | None] = {}
        address_list = [address for address in contract_addresses if address]

        for index in range(0, len(address_list), chunk_size):
            batch = address_list[index: index + chunk_size]
            if not batch:
                continue
            rows = session.execute(
                select(
                    LiquidityPool.contract_address,
                    LiquidityPool.liquidity_snapshot_block_number,
                )
                .where(LiquidityPool.contract_address.in_(batch))
            ).all()
            existing_rows.update(
                {
                    contract_address: (
                        int(liquidity_snapshot_block_number)
                        if liquidity_snapshot_block_number is not None
                        else None
                    )
                    for contract_address, liquidity_snapshot_block_number in rows
                    if contract_address is not None
                }
            )

        return existing_rows

    @classmethod
    def _update_existing_pool_liquidity_snapshot(
        cls,
        session: Any,
        pools: Sequence[PoolWrite],
        existing_pool_rows: Mapping[str, int | None],
    ) -> None:
        pool_table = LiquidityPool.__table__
        for pool in pools:
            existing_snapshot_block_number = existing_pool_rows.get(pool.contract_address)
            if pool.contract_address not in existing_pool_rows:
                continue
            if not cls._has_complete_pool_snapshot(pool):
                continue
            if (
                existing_snapshot_block_number is not None
                and pool.liquidity_snapshot_block_number < existing_snapshot_block_number
            ):
                continue

            session.execute(
                pool_table.update()
                .where(pool_table.c.contract_address == pool.contract_address)
                .values(
                    approx_tvl_usd=pool.approx_tvl_usd,
                    token_a_usd_share=pool.token_a_usd_share,
                    liquidity_snapshot_block_number=pool.liquidity_snapshot_block_number,
                )
            )
