from __future__ import annotations

import asyncio

from sqlalchemy import select

from ..core.database import session_scope
from ..models.liquidity import LiquidityPool
from ..models.trading import Swap, Transaction
from ..utils import normalize_address


class PoolRegistryService:
    async def load_pool_addresses(self) -> list[str]:
        rows = await asyncio.to_thread(self._load_pool_addresses_sync)
        return sorted(
            {
                address
                for (raw_address,) in rows
                if (address := normalize_address(raw_address)) is not None
            }
        )

    async def load_pool_competitors(self) -> dict[str, list[str]]:
        return await asyncio.to_thread(self._load_pool_competitors_sync)

    @staticmethod
    def _load_pool_addresses_sync() -> list:
        with session_scope() as session:
            return session.execute(select(LiquidityPool.contract_address)).all()

    @staticmethod
    def _load_pool_competitors_sync() -> dict[str, list[str]]:
        with session_scope() as session:
            pool_rows = session.execute(select(LiquidityPool.contract_address)).all()
            competitor_rows = session.execute(
                select(Swap.pool_address, Transaction.trader_address)
                .select_from(Swap)
                .join(Transaction, Transaction.hash_id == Swap.transaction_hash_id)
            ).all()

        competitors_by_pool: dict[str, set[str]] = {
            pool_address: set()
            for (raw_pool_address,) in pool_rows
            if (pool_address := normalize_address(raw_pool_address)) is not None
        }

        for raw_pool_address, raw_competitor_address in competitor_rows:
            pool_address = normalize_address(raw_pool_address)
            competitor_address = normalize_address(raw_competitor_address)
            if pool_address is None:
                continue

            competitors_by_pool.setdefault(pool_address, set())
            if competitor_address is not None:
                competitors_by_pool[pool_address].add(competitor_address)

        return {
            pool_address: sorted(competitor_addresses)
            for pool_address, competitor_addresses in sorted(competitors_by_pool.items())
        }
