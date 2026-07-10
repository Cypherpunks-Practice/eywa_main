from __future__ import annotations

import asyncio
from collections.abc import Sequence

from sqlalchemy import select

from ..core.database import session_scope
from ..models.participants import Trader
from ..schemas.scan import ScanRequest, TraceAddressRole
from ..utils.scanning import validate_block_window
from ..utils import normalize_address


class TraderRegistryService:
    @staticmethod
    def normalize_trader_addresses(addresses: Sequence[str | None]) -> list[str]:
        normalized = {
            address
            for raw_address in addresses
            if (address := normalize_address(raw_address)) is not None
        }
        return sorted(normalized)

    async def load_tracked_traders(self) -> list[str]:
        rows = await asyncio.to_thread(self._load_tracked_traders_sync)
        return self.normalize_trader_addresses(address for (address,) in rows)

    async def persist_traders(
        self,
        trader_addresses: Sequence[str | None],
        *,
        label: str | None = "unknown",
    ) -> int:
        normalized_traders = self.normalize_trader_addresses(trader_addresses)
        if not normalized_traders:
            return 0
        return await asyncio.to_thread(
            self._persist_traders_sync,
            normalized_traders,
            label,
        )

    @staticmethod
    def _load_tracked_traders_sync() -> list:
        with session_scope() as session:
            return session.execute(select(Trader.contract_address)).all()

    @staticmethod
    def _persist_traders_sync(
        trader_addresses: list[str],
        label: str | None,
        *,
        chunk_size: int = 1000,
    ) -> int:
        with session_scope() as session:
            existing_addresses: set[str] = set()
            for index in range(0, len(trader_addresses), chunk_size):
                batch = trader_addresses[index: index + chunk_size]
                if not batch:
                    continue
                rows = session.execute(
                    select(Trader.contract_address).where(Trader.contract_address.in_(batch))
                ).all()
                existing_addresses.update(
                    contract_address
                    for (contract_address,) in rows
                    if contract_address is not None
                )

            new_traders = [
                {"contract_address": trader_address, "label": label}
                for trader_address in trader_addresses
                if trader_address not in existing_addresses
            ]
            if new_traders:
                session.execute(Trader.__table__.insert(), new_traders)

        return len(new_traders)

    async def build_scan_request(
        self,
        *,
        start_block: int,
        end_block: int,
        trader_addresses: Sequence[str] | None = None,
        trace_address_role: TraceAddressRole = TraceAddressRole.TO,
    ) -> ScanRequest:
        validated_start_block, validated_end_block = validate_block_window(
            start_block=start_block,
            end_block=end_block,
        )

        resolved_traders = (
            await self.load_tracked_traders()
            if trader_addresses is None
            else self.normalize_trader_addresses(trader_addresses)
        )
        if not resolved_traders:
            raise RuntimeError(
                "No valid trader addresses were provided or found in the traders table"
            )

        return ScanRequest(
            start_block=validated_start_block,
            end_block=validated_end_block,
            trader_addresses=resolved_traders,
            trace_address_role=trace_address_role,
        )
