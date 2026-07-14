from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..core.config import Settings
from ..core.exceptions import RpcError
from ..models.trading import SwapSide
from ..schemas import BlockMetadata, EnrichedSwapEvent, TokenMetadata
from ..utils import (
    async_resolve,
    batch_resolve,
    median_decimal,
    normalize_address,
    parse_int,
    raw_amount_to_decimal,
)
from .rpc_gateway_service import RpcGatewayService
from .token_metadata_service import TokenMetadataService
from .trace_provider_service import TraceProviderService


@dataclass(frozen=True)
class TradePricingContext:
    normalized_events: list[EnrichedSwapEvent]
    token_metadata_by_address: dict[str, TokenMetadata]
    stablecoin_addresses: set[str]
    token_usd_prices: dict[str, Decimal]


class TradeEnrichmentService:
    def __init__(
        self,
        rpc_gateway_service: RpcGatewayService,
        trace_provider_service: TraceProviderService,
        token_metadata_service: TokenMetadataService,
        settings: Settings,
    ):
        self._rpc_gateway_service = rpc_gateway_service
        self._trace_provider_service = trace_provider_service
        self._token_metadata_service = token_metadata_service
        self._settings = settings

    async def enrich_events(
        self,
        events: Sequence[EnrichedSwapEvent],
        receipts: Mapping[str, Mapping[str, Any]],
        *,
        pricing_context: TradePricingContext | None = None,
    ) -> list[EnrichedSwapEvent]:
        if not events:
            return []

        context = pricing_context or await self.resolve_pricing_context(events)
        normalized_events = context.normalized_events
        token_metadata_by_address = context.token_metadata_by_address
        stablecoin_addresses = context.stablecoin_addresses

        block_numbers = sorted(
            {
                event.block_number
                for event in normalized_events
                if event.block_number is not None
            }
        )
        block_metadata_by_number = await self.resolve_block_metadata(block_numbers)
        bribes_by_tx_hash = await self.collect_bribes(
            [event.tx_hash for event in normalized_events],
            receipts,
            block_metadata_by_number,
        )
        token_usd_prices = context.token_usd_prices

        enriched_events: list[EnrichedSwapEvent] = []
        for event in normalized_events:
            receipt = receipts.get(event.tx_hash, {})
            block_number = event.block_number or parse_int(receipt.get("blockNumber"))
            block_metadata = (
                block_metadata_by_number.get(block_number)
                if block_number is not None
                else None
            )
            token_a_metadata = (
                token_metadata_by_address.get(event.token_a_address)
                if event.token_a_address
                else None
            )
            token_b_metadata = (
                token_metadata_by_address.get(event.token_b_address)
                if event.token_b_address
                else None
            )

            effective_gas_price = parse_int(receipt.get("effectiveGasPrice"))
            if effective_gas_price is None:
                effective_gas_price = parse_int(receipt.get("gasPrice")) or 0
            gas_used = parse_int(receipt.get("gasUsed")) or 0
            base_fee = block_metadata.base_fee_per_gas if block_metadata is not None else 0
            priority_fee_wei = max(effective_gas_price - base_fee, 0) * gas_used

            enriched_events.append(
                event.model_copy(
                    update={
                        "block_number": block_number,
                        "trader_address": self._extract_preferred_trader(event, receipt),
                        "token_a_label": token_a_metadata.label if token_a_metadata else None,
                        "token_b_label": token_b_metadata.label if token_b_metadata else None,
                        "token_a_decimals": token_a_metadata.decimals if token_a_metadata else None,
                        "token_b_decimals": token_b_metadata.decimals if token_b_metadata else None,
                        "token_a_is_stable": token_a_metadata.is_stable if token_a_metadata else None,
                        "token_b_is_stable": token_b_metadata.is_stable if token_b_metadata else None,
                        "usd_amount": self.compute_trade_amount_usd(
                            event,
                            token_metadata_by_address,
                            stablecoin_addresses,
                            token_usd_prices,
                        ),
                        "bribe_wei": bribes_by_tx_hash.get(event.tx_hash, 0),
                        "priority_fee_wei": priority_fee_wei,
                        "timestamp": block_metadata.timestamp if block_metadata is not None else None,
                        "miner": block_metadata.miner if block_metadata is not None else None,
                    }
                )
            )

        return enriched_events

    async def resolve_pricing_context(
        self,
        events: Sequence[EnrichedSwapEvent],
    ) -> TradePricingContext:
        if not events:
            return TradePricingContext(
                normalized_events=[],
                token_metadata_by_address={},
                stablecoin_addresses=set(),
                token_usd_prices={},
            )

        normalized_events = self.normalize_trade_token_order(events)
        token_addresses = sorted(
            {
                token_address
                for event in normalized_events
                for token_address in (event.token_a_address, event.token_b_address)
                if token_address
            }
        )
        token_metadata_by_address = await self._token_metadata_service.resolve_token_metadata(
            token_addresses,
        )
        stablecoin_addresses = {
            token_address
            for token_address, metadata in token_metadata_by_address.items()
            if metadata.is_stable
        }
        token_usd_prices = self.infer_token_usd_prices(
            normalized_events,
            token_metadata_by_address,
            stablecoin_addresses,
        )

        return TradePricingContext(
            normalized_events=normalized_events,
            token_metadata_by_address=token_metadata_by_address,
            stablecoin_addresses=stablecoin_addresses,
            token_usd_prices=token_usd_prices,
        )

    async def resolve_block_metadata(
        self,
        block_numbers: Sequence[int],
    ) -> dict[int, BlockMetadata]:
        normalized_block_numbers = sorted(set(block_numbers))
        if not normalized_block_numbers:
            return {}

        async def resolve_one(block_number: int) -> tuple[int, dict[str, Any] | None]:
            return block_number, await self._rpc_gateway_service.get_block(block_number)

        raw_blocks = await batch_resolve(
            normalized_block_numbers,
            batch_resolver=(
                self._rpc_gateway_service.get_blocks_batch
                if self._rpc_gateway_service.supports_batch_requests()
                else None
            ),
            individual_resolver=resolve_one,
            batch_size=self._settings.block_batch_size,
            max_concurrency=self._settings.max_workers,
        )

        return {
            block_number: self._parse_block_metadata(block_number, raw_block)
            for block_number, raw_block in raw_blocks.items()
            if raw_block is not None
        }

    async def trace_transaction_supported(self, sample_tx_hash: str) -> bool:
        return await self._trace_provider_service.trace_transaction_supported(sample_tx_hash)

    async def get_tx_bribe_wei(self, tx_hash: str, miner: str | None) -> int:
        if not miner:
            return 0

        try:
            traces = await self._trace_provider_service.trace_transaction(tx_hash)
        except RpcError:
            return 0

        total = 0
        for trace in traces:
            action = trace.get("action") or {}
            to_address = normalize_address(action.get("to"))
            if to_address != miner:
                continue
            value = parse_int(action.get("value")) or 0
            if value > 0:
                total += value
        return total

    async def collect_bribes(
        self,
        tx_hashes: Sequence[str],
        receipts: Mapping[str, Mapping[str, Any]],
        block_metadata_by_number: Mapping[int, BlockMetadata],
    ) -> dict[str, int]:
        ordered_tx_hashes = sorted(set(tx_hashes))
        if not ordered_tx_hashes:
            return {}
        if not await self.trace_transaction_supported(ordered_tx_hashes[0]):
            return {tx_hash: 0 for tx_hash in ordered_tx_hashes}

        miners_by_tx_hash: dict[str, str | None] = {}
        for tx_hash in ordered_tx_hashes:
            receipt = receipts.get(tx_hash, {})
            block_number = parse_int(receipt.get("blockNumber"))
            miners_by_tx_hash[tx_hash] = (
                block_metadata_by_number.get(block_number).miner
                if block_number in block_metadata_by_number
                else None
            )

        async def resolve_one(tx_hash: str) -> tuple[str, int]:
            return tx_hash, await self.get_tx_bribe_wei(
                tx_hash, miners_by_tx_hash.get(tx_hash),
            )

        bribes_by_tx_hash = await async_resolve(
            ordered_tx_hashes,
            resolve_one,
            max_concurrency=max(1, min(self._settings.max_workers, 4)),
        )
        for tx_hash in ordered_tx_hashes:
            bribes_by_tx_hash.setdefault(tx_hash, 0)
        return bribes_by_tx_hash

    def normalize_trade_token_order(
        self,
        events: Sequence[EnrichedSwapEvent],
    ) -> list[EnrichedSwapEvent]:
        normalized_events: list[EnrichedSwapEvent] = []
        for event in events:
            token_a_address = normalize_address(event.token_a_address)
            token_b_address = normalize_address(event.token_b_address)
            if (
                token_a_address is None
                or token_b_address is None
                or token_a_address <= token_b_address
            ):
                normalized_events.append(
                    event.model_copy(
                        update={
                            "token_a_address": token_a_address,
                            "token_b_address": token_b_address,
                        }
                    )
                )
                continue

            normalized_events.append(
                event.model_copy(
                    update={
                        "token_a_address": token_b_address,
                        "token_b_address": token_a_address,
                        "token_a_label": event.token_b_label,
                        "token_b_label": event.token_a_label,
                        "token_a_decimals": event.token_b_decimals,
                        "token_b_decimals": event.token_a_decimals,
                        "token_a_is_stable": event.token_b_is_stable,
                        "token_b_is_stable": event.token_a_is_stable,
                        "amount_a_raw": event.amount_b_raw,
                        "amount_b_raw": event.amount_a_raw,
                        "side": self._invert_swap_side(event.side),
                    }
                )
            )
        return normalized_events

    def infer_token_usd_prices(
        self,
        events: Sequence[EnrichedSwapEvent],
        token_metadata_by_address: Mapping[str, TokenMetadata],
        stablecoin_addresses: set[str],
    ) -> dict[str, Decimal]:
        known_prices: dict[str, Decimal] = {}
        max_rounds = self._settings.price_inference_max_rounds
        max_reasonable_price = Decimal(self._settings.max_reasonable_token_price)

        for _ in range(max_rounds):
            samples: dict[str, list[Decimal]] = defaultdict(list)

            for event in events:
                token_amounts = self._get_event_token_amounts(event, token_metadata_by_address)
                if not token_amounts:
                    continue

                priced_usd_values: list[Decimal] = []
                for token_address, amount in token_amounts:
                    if token_address in stablecoin_addresses:
                        priced_usd_values.append(amount)
                    elif token_address in known_prices:
                        priced_usd_values.append(amount * known_prices[token_address])

                if not priced_usd_values:
                    continue

                reference_usd = max(priced_usd_values)
                if reference_usd <= 0:
                    continue

                for token_address, amount in token_amounts:
                    if token_address in stablecoin_addresses or token_address in known_prices:
                        continue
                    if amount <= 0:
                        continue

                    implied_price = reference_usd / amount
                    if 0 < implied_price <= max_reasonable_price:
                        samples[token_address].append(implied_price)

            new_prices: dict[str, Decimal] = {}
            for token_address, price_samples in samples.items():
                if price_samples:
                    new_prices[token_address] = median_decimal(price_samples)

            if not new_prices:
                break

            known_prices.update(new_prices)

        return known_prices

    def compute_trade_amount_usd(
        self,
        event: EnrichedSwapEvent,
        token_metadata_by_address: Mapping[str, TokenMetadata],
        stablecoin_addresses: set[str],
        token_usd_prices: Mapping[str, Decimal],
    ) -> Decimal | None:
        stable_amounts: list[Decimal] = []

        if event.token_a_address in stablecoin_addresses and event.amount_a_raw > 0:
            token_a_metadata = token_metadata_by_address.get(event.token_a_address)
            stable_amounts.append(
                raw_amount_to_decimal(
                    event.amount_a_raw,
                    token_a_metadata.decimals if token_a_metadata is not None else 18,
                )
            )
        if event.token_b_address in stablecoin_addresses and event.amount_b_raw > 0:
            token_b_metadata = token_metadata_by_address.get(event.token_b_address)
            stable_amounts.append(
                raw_amount_to_decimal(
                    event.amount_b_raw,
                    token_b_metadata.decimals if token_b_metadata is not None else 18,
                )
            )

        if stable_amounts:
            return max(stable_amounts)

        inferred_amounts: list[Decimal] = []
        if event.token_a_address in token_usd_prices and event.amount_a_raw > 0:
            token_a_metadata = token_metadata_by_address.get(event.token_a_address)
            inferred_amounts.append(
                raw_amount_to_decimal(
                    event.amount_a_raw,
                    token_a_metadata.decimals if token_a_metadata is not None else 18,
                )
                * token_usd_prices[event.token_a_address]
            )
        if event.token_b_address in token_usd_prices and event.amount_b_raw > 0:
            token_b_metadata = token_metadata_by_address.get(event.token_b_address)
            inferred_amounts.append(
                raw_amount_to_decimal(
                    event.amount_b_raw,
                    token_b_metadata.decimals if token_b_metadata is not None else 18,
                )
                * token_usd_prices[event.token_b_address]
            )

        if not inferred_amounts:
            return None
        return max(inferred_amounts)

    @staticmethod
    def _parse_block_metadata(block_number: int, raw_block: Mapping[str, Any]) -> BlockMetadata:
        return BlockMetadata(
            block_number=block_number,
            timestamp=parse_int(raw_block.get("timestamp")),
            base_fee_per_gas=parse_int(raw_block.get("baseFeePerGas")) or 0,
            miner=normalize_address(raw_block.get("miner") or raw_block.get("author")),
        )

    @staticmethod
    def _extract_preferred_trader(
        event: EnrichedSwapEvent,
        receipt: Mapping[str, Any],
    ) -> str | None:
        if event.matched_trader_addresses:
            return event.matched_trader_addresses[0]
        return normalize_address(receipt.get("to"))

    @staticmethod
    def _invert_swap_side(side: SwapSide | None) -> SwapSide | None:
        if side == SwapSide.BUY:
            return SwapSide.SELL
        if side == SwapSide.SELL:
            return SwapSide.BUY
        return None

    @staticmethod
    def _get_event_token_amounts(
        event: EnrichedSwapEvent,
        token_metadata_by_address: Mapping[str, TokenMetadata],
    ) -> list[tuple[str, Decimal]]:
        token_amounts: list[tuple[str, Decimal]] = []

        if event.token_a_address and event.amount_a_raw > 0:
            token_a_metadata = token_metadata_by_address.get(event.token_a_address)
            token_amounts.append(
                (
                    event.token_a_address,
                    raw_amount_to_decimal(
                        event.amount_a_raw,
                        token_a_metadata.decimals if token_a_metadata is not None else 18,
                    ),
                )
            )
        if event.token_b_address and event.amount_b_raw > 0:
            token_b_metadata = token_metadata_by_address.get(event.token_b_address)
            token_amounts.append(
                (
                    event.token_b_address,
                    raw_amount_to_decimal(
                        event.amount_b_raw,
                        token_b_metadata.decimals if token_b_metadata is not None else 18,
                    ),
                )
            )

        return token_amounts
