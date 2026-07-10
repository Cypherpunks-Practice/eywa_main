from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from ..core.config import Settings
from ..core.exceptions import RpcError
from ..schemas import DexKind, EnrichedSwapEvent, PoolLiquiditySnapshot
from ..utils import async_resolve, decode_uint_call_result, iter_chunks, raw_amount_to_decimal
from .rpc_gateway_service import RpcGatewayService
from .trade_enrichment_service import TradePricingContext

BALANCE_OF_SELECTOR = "0x70a08231"
STANDARD_POOL_KINDS = {DexKind.UNI_V2_OR_SUSHI, DexKind.UNISWAP_V3}
PoolTokenKey = tuple[str, str]


class PoolTvlService:
    def __init__(
        self,
        rpc_gateway_service: RpcGatewayService,
        settings: Settings,
    ) -> None:
        self._rpc_gateway_service = rpc_gateway_service
        self._settings = settings

    async def resolve_pool_liquidity_snapshots(
        self,
        events: Sequence[EnrichedSwapEvent],
        *,
        pricing_context: TradePricingContext,
        block_number: int,
    ) -> dict[str, PoolLiquiditySnapshot]:
        pool_tokens = self._collect_pool_tokens(events)
        if not pool_tokens:
            return {}

        balances = await self._resolve_pool_balances(
            pool_tokens,
            block_number=block_number,
        )
        snapshots_by_pool: dict[str, PoolLiquiditySnapshot] = {}

        for pool_address, (token_a_address, token_b_address) in pool_tokens.items():
            token_a_value = self._resolve_token_value_usd(
                token_address=token_a_address,
                balance_raw=balances.get((pool_address, token_a_address)),
                pricing_context=pricing_context,
            )
            token_b_value = self._resolve_token_value_usd(
                token_address=token_b_address,
                balance_raw=balances.get((pool_address, token_b_address)),
                pricing_context=pricing_context,
            )

            snapshot = self._build_pool_liquidity_snapshot(
                token_a_value=token_a_value,
                token_b_value=token_b_value,
                block_number=block_number,
            )
            if snapshot is not None:
                snapshots_by_pool[pool_address] = snapshot

        return snapshots_by_pool

    @staticmethod
    def _collect_pool_tokens(
        events: Sequence[EnrichedSwapEvent],
    ) -> dict[str, tuple[str, str]]:
        pool_tokens: dict[str, tuple[str, str]] = {}
        conflicting_pools: set[str] = set()

        for event in events:
            if (
                event.dex_kind not in STANDARD_POOL_KINDS
                or not event.pool_address
                or not event.token_a_address
                or not event.token_b_address
            ):
                continue

            token_pair = (event.token_a_address, event.token_b_address)
            current_pair = pool_tokens.get(event.pool_address)
            if current_pair is None:
                pool_tokens[event.pool_address] = token_pair
                continue

            if current_pair != token_pair:
                conflicting_pools.add(event.pool_address)

        return {
            pool_address: token_pair
            for pool_address, token_pair in pool_tokens.items()
            if pool_address not in conflicting_pools
        }

    async def _resolve_pool_balances(
        self,
        pool_tokens: dict[str, tuple[str, str]],
        *,
        block_number: int,
    ) -> dict[PoolTokenKey, int | None]:
        ordered_pairs = [
            (pool_address, token_address)
            for pool_address, token_pair in sorted(pool_tokens.items())
            for token_address in token_pair
        ]
        if not ordered_pairs:
            return {}

        block_tag = hex(block_number)
        if self._rpc_gateway_service.supports_batch_requests():
            resolved: dict[PoolTokenKey, int | None] = {}
            for batch in iter_chunks(ordered_pairs, self._settings.pool_token_batch_size):
                results = await self._rpc_gateway_service.eth_call_many(
                    [
                        (token_address, self._build_balance_of_call(pool_address))
                        for pool_address, token_address in batch
                    ],
                    block=block_tag,
                )
                for pair, result in zip(batch, results, strict=False):
                    resolved[pair] = decode_uint_call_result(result)
            return resolved

        async def resolve_one(pair: PoolTokenKey) -> tuple[PoolTokenKey, int | None]:
            pool_address, token_address = pair
            try:
                result = await self._rpc_gateway_service.eth_call(
                    token_address,
                    self._build_balance_of_call(pool_address),
                    block=block_tag,
                )
            except RpcError:
                return pair, None
            return pair, decode_uint_call_result(result)

        return await async_resolve(
            ordered_pairs,
            resolve_one,
            max_concurrency=self._settings.max_workers,
        )

    @staticmethod
    def _build_balance_of_call(holder_address: str) -> str:
        return f"{BALANCE_OF_SELECTOR}{holder_address[2:].rjust(64, '0')}"

    @staticmethod
    def _resolve_token_value_usd(
        *,
        token_address: str,
        balance_raw: int | None,
        pricing_context: TradePricingContext,
    ) -> Decimal | None:
        if balance_raw is None:
            return None

        token_metadata = pricing_context.token_metadata_by_address.get(token_address)
        if token_metadata is None:
            return None

        if token_address in pricing_context.stablecoin_addresses:
            price = Decimal("1")
        else:
            price = pricing_context.token_usd_prices.get(token_address)

        if price is None:
            return None

        return raw_amount_to_decimal(balance_raw, token_metadata.decimals) * price

    @staticmethod
    def _build_pool_liquidity_snapshot(
        *,
        token_a_value: Decimal | None,
        token_b_value: Decimal | None,
        block_number: int,
    ) -> PoolLiquiditySnapshot | None:
        if token_a_value is None or token_b_value is None:
            return None

        approx_tvl_usd = token_a_value + token_b_value
        if approx_tvl_usd == 0:
            token_a_usd_share = 0.5
        else:
            token_a_usd_share = float(token_a_value / approx_tvl_usd)
            token_a_usd_share = min(1.0, max(0.0, token_a_usd_share))

        return PoolLiquiditySnapshot(
            approx_tvl_usd=approx_tvl_usd,
            token_a_usd_share=token_a_usd_share,
            liquidity_snapshot_block_number=block_number,
        )
