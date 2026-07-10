from __future__ import annotations

from collections.abc import Callable, Sequence

from ..core.config import Settings
from ..core.exceptions import RpcError
from ..schemas import DexKind, EnrichedSwapEvent, PoolMetadata, RawSwapEvent
from ..utils import (
    async_resolve,
    batch_resolve,
    decode_address_call_result,
    decode_uint_call_result,
    iter_chunks,
)
from .rpc_gateway_service import RpcGatewayService

FACTORY_SELECTOR = "0xc45a0155"
FEE_SELECTOR = "0xddca3f43"
TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
COINS_SELECTOR = "0xc6610657"
COINS_INT128_SELECTOR = "0x23746eb8"
UNDERLYING_COINS_SELECTOR = "0xb9947eb0"
UNDERLYING_COINS_INT128_SELECTOR = "0xb739953e"


class PoolMetadataService:
    def __init__(
        self,
        rpc_gateway_service: RpcGatewayService,
        settings: Settings,
    ):
        self._rpc_gateway_service = rpc_gateway_service
        self._settings = settings

    async def enrich_events(self, events: Sequence[RawSwapEvent]) -> list[EnrichedSwapEvent]:
        if not events:
            return []

        pool_metadata = await self.resolve_pool_metadata(events)
        curve_cache: dict[tuple[str, int, bool], str | None] = {}
        enriched_events: list[EnrichedSwapEvent] = []

        for event in events:
            metadata = pool_metadata.get(event.pool_address) or PoolMetadata(
                pool_address=event.pool_address,
            )
            token_a_address = metadata.token_a_address
            token_b_address = metadata.token_b_address

            if event.dex_kind == DexKind.CURVE:
                if event.curve_sold_id is not None:
                    token_a_address = await self.resolve_curve_coin_address(
                        pool_address=event.pool_address,
                        index=event.curve_sold_id,
                        cache=curve_cache,
                        prefer_underlying=event.curve_use_underlying,
                    )
                if event.curve_bought_id is not None:
                    token_b_address = await self.resolve_curve_coin_address(
                        pool_address=event.pool_address,
                        index=event.curve_bought_id,
                        cache=curve_cache,
                        prefer_underlying=event.curve_use_underlying,
                    )

            event_payload = event.model_dump()
            event_payload.update(
                token_a_address=token_a_address,
                token_b_address=token_b_address,
                dex_factory=metadata.dex_factory,
                fee_tier=metadata.fee_tier,
            )
            enriched_events.append(
                EnrichedSwapEvent.model_validate(event_payload)
            )

        return enriched_events

    async def resolve_pool_metadata(
        self,
        events: Sequence[RawSwapEvent],
    ) -> dict[str, PoolMetadata]:
        pool_addresses = sorted({event.pool_address for event in events})
        if not pool_addresses:
            return {}

        factories = await self.resolve_pool_factories(pool_addresses)
        v3_pool_addresses = sorted(
            {
                event.pool_address
                for event in events
                if event.dex_kind == DexKind.UNISWAP_V3
            }
        )
        fee_tiers = await self.resolve_pool_fee_tiers(v3_pool_addresses)

        standard_pool_addresses = sorted(
            {
                event.pool_address
                for event in events
                if event.dex_kind in {DexKind.UNI_V2_OR_SUSHI, DexKind.UNISWAP_V3}
            }
        )
        token_pairs = await self.resolve_pool_tokens(standard_pool_addresses)

        metadata_by_pool: dict[str, PoolMetadata] = {}
        for pool_address in pool_addresses:
            token_a_address, token_b_address = token_pairs.get(pool_address, (None, None))
            metadata_by_pool[pool_address] = PoolMetadata(
                pool_address=pool_address,
                dex_factory=factories.get(pool_address),
                fee_tier=fee_tiers.get(pool_address),
                token_a_address=token_a_address,
                token_b_address=token_b_address,
            )

        return metadata_by_pool

    async def resolve_pool_factories(
        self,
        pool_addresses: Sequence[str],
    ) -> dict[str, str | None]:
        unique_pools = sorted(set(pool_addresses))
        if not unique_pools:
            return {}

        async def resolve_one(pool_address: str) -> tuple[str, str | None]:
            return (
                pool_address,
                await self._decode_pool_call(pool_address, FACTORY_SELECTOR, decode_address_call_result),
            )

        resolved = await batch_resolve(
            unique_pools,
            batch_resolver=(
                self._resolve_pool_factories_batch
                if self._rpc_gateway_service.supports_batch_requests()
                else None
            ),
            individual_resolver=resolve_one,
            batch_size=self._settings.factory_batch_size,
            max_concurrency=self._settings.max_workers,
        )
        return {pool_address: resolved.get(pool_address) for pool_address in unique_pools}

    async def resolve_pool_fee_tiers(
        self,
        pool_addresses: Sequence[str],
    ) -> dict[str, int | None]:
        unique_pools = sorted(set(pool_addresses))
        if not unique_pools:
            return {}

        async def resolve_one(pool_address: str) -> tuple[str, int | None]:
            return (
                pool_address,
                await self._decode_pool_call(pool_address, FEE_SELECTOR, decode_uint_call_result),
            )

        resolved = await batch_resolve(
            unique_pools,
            batch_resolver=(
                self._resolve_pool_fee_tiers_batch
                if self._rpc_gateway_service.supports_batch_requests()
                else None
            ),
            individual_resolver=resolve_one,
            batch_size=self._settings.factory_batch_size,
            max_concurrency=self._settings.max_workers,
        )
        return {pool_address: resolved.get(pool_address) for pool_address in unique_pools}

    async def resolve_pool_tokens(
        self,
        pool_addresses: Sequence[str],
    ) -> dict[str, tuple[str | None, str | None]]:
        unique_pools = sorted(set(pool_addresses))
        if not unique_pools:
            return {}

        resolved: dict[str, tuple[str | None, str | None]] = {}
        unresolved: set[str] = set(unique_pools)

        if self._rpc_gateway_service.supports_batch_requests():
            unresolved.clear()
            for batch in iter_chunks(unique_pools, self._settings.pool_token_batch_size):
                batch_results = await self._resolve_pool_tokens_batch(batch)
                resolved.update(batch_results)
                unresolved.update(
                    pool_address
                    for pool_address, token_pair in batch_results.items()
                    if self._token_pair_is_incomplete(token_pair)
                )

        if unresolved:
            retry_results = await self._resolve_pool_tokens_individually(sorted(unresolved))
            for pool_address, retry_pair in retry_results.items():
                current_pair = resolved.get(pool_address)
                resolved[pool_address] = self._merge_token_pairs(current_pair, retry_pair)

        return {
            pool_address: resolved.get(pool_address, (None, None))
            for pool_address in unique_pools
        }

    async def resolve_curve_coin_address(
        self,
        *,
        pool_address: str,
        index: int,
        cache: dict[tuple[str, int, bool], str | None],
        prefer_underlying: bool = False,
    ) -> str | None:
        cache_key = (pool_address, index, prefer_underlying)
        if cache_key in cache:
            return cache[cache_key]

        selectors = [
            COINS_SELECTOR,
            COINS_INT128_SELECTOR,
            UNDERLYING_COINS_SELECTOR,
            UNDERLYING_COINS_INT128_SELECTOR,
        ]
        if prefer_underlying:
            selectors = [
                UNDERLYING_COINS_SELECTOR,
                UNDERLYING_COINS_INT128_SELECTOR,
                COINS_SELECTOR,
                COINS_INT128_SELECTOR,
            ]

        for selector in selectors:
            data = f"{selector}{index:064x}"
            try:
                result = await self._rpc_gateway_service.eth_call(pool_address, data)
            except RpcError:
                continue

            token_address = decode_address_call_result(result)
            if token_address is not None:
                cache[cache_key] = token_address
                return token_address

        cache[cache_key] = None
        return None

    @staticmethod
    def _token_pair_is_incomplete(token_pair: tuple[str | None, ...]) -> bool:
        return not all(token_pair)

    async def _resolve_pool_factories_batch(
        self,
        pool_addresses: Sequence[str],
    ) -> dict[str, str | None]:
        results = await self._rpc_gateway_service.eth_call_many(
            [(pool_address, FACTORY_SELECTOR) for pool_address in pool_addresses]
        )
        return {
            pool_address: decode_address_call_result(result)
            for pool_address, result in zip(pool_addresses, results, strict=False)
        }

    async def _resolve_pool_fee_tiers_batch(
        self,
        pool_addresses: Sequence[str],
    ) -> dict[str, int | None]:
        results = await self._rpc_gateway_service.eth_call_many(
            [(pool_address, FEE_SELECTOR) for pool_address in pool_addresses]
        )
        return {
            pool_address: decode_uint_call_result(result)
            for pool_address, result in zip(pool_addresses, results, strict=False)
        }

    async def _resolve_pool_tokens_batch(
        self,
        pool_addresses: Sequence[str],
    ) -> dict[str, tuple[str | None, str | None]]:
        calls = [
            (pool_address, selector)
            for pool_address in pool_addresses
            for selector in (TOKEN0_SELECTOR, TOKEN1_SELECTOR)
        ]
        results = await self._rpc_gateway_service.eth_call_many(calls)
        token_pairs: dict[str, tuple[str | None, str | None]] = {}
        for index, pool_address in enumerate(pool_addresses):
            token_pairs[pool_address] = (
                decode_address_call_result(results[index * 2]),
                decode_address_call_result(results[index * 2 + 1]),
            )
        return token_pairs

    async def _resolve_pool_tokens_individually(
        self,
        pool_addresses: Sequence[str],
    ) -> dict[str, tuple[str | None, str | None]]:
        async def resolve_one(
            pool_address: str,
        ) -> tuple[str, tuple[str | None, str | None]]:
            return (
                pool_address,
                (
                    await self._decode_pool_call(
                        pool_address, TOKEN0_SELECTOR, decode_address_call_result,
                    ),
                    await self._decode_pool_call(
                        pool_address, TOKEN1_SELECTOR, decode_address_call_result,
                    ),
                ),
            )

        return await async_resolve(
            list(pool_addresses),
            resolve_one,
            max_concurrency=self._settings.max_workers,
        )

    async def _decode_pool_call[OutputT](
        self,
        pool_address: str,
        selector: str,
        decoder: Callable[[str | None], OutputT],
    ) -> OutputT:
        try:
            return decoder(await self._rpc_gateway_service.eth_call(pool_address, selector))
        except RpcError:
            return decoder(None)

    @staticmethod
    def _merge_token_pairs(
        current_pair: tuple[str | None, ...] | None,
        retry_pair: tuple[str | None, ...],
    ) -> tuple[str | None, ...]:
        if current_pair is None:
            return retry_pair

        return tuple(
            current_value or retry_value
            for current_value, retry_value in zip(current_pair, retry_pair, strict=False)
        )
