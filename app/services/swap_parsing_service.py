from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models.trading import SwapSide
from ..schemas import DexKind, RawSwapEvent, TransactionContext
from ..utils import normalize_topic0, parse_data_words, parse_int, word_to_int

UNISWAP_V2_SWAP_TOPIC0 = "d78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
UNISWAP_V3_SWAP_TOPIC0 = "c42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
CURVE_TOKEN_EXCHANGE_TOPIC0 = "8b3e96f2b889fa771c53c981b40daf005f63f637f1869f707052d15a3dd97140"
CURVE_TOKEN_EXCHANGE_UNDERLYING_TOPIC0 = "d013ca23e77a65003c2c659c5442c00c805371b7fc1ebd4c206c41d1536bd90b"

SWAP_SIGNATURES: dict[str, DexKind] = {
    UNISWAP_V2_SWAP_TOPIC0: DexKind.UNI_V2_OR_SUSHI,
    UNISWAP_V3_SWAP_TOPIC0: DexKind.UNISWAP_V3,
    CURVE_TOKEN_EXCHANGE_TOPIC0: DexKind.CURVE,
    CURVE_TOKEN_EXCHANGE_UNDERLYING_TOPIC0: DexKind.CURVE,
}


class SwapParsingService:
    def extract_swap_events(
        self,
        tx_context: TransactionContext,
        receipt: Mapping[str, Any],
    ) -> list[RawSwapEvent]:
        events: list[RawSwapEvent] = []
        block_number = parse_int(receipt.get("blockNumber"))

        for log in receipt.get("logs", []) or []:
            if not isinstance(log, Mapping):
                continue

            event = self._parse_swap_log(
                tx_context=tx_context,
                block_number=block_number,
                log=log,
            )
            if event is not None:
                events.append(event)

        return events

    def collect_swap_events(
        self,
        receipts: Mapping[str, Mapping[str, Any]],
        tx_contexts: Mapping[str, TransactionContext],
    ) -> list[RawSwapEvent]:
        events: list[RawSwapEvent] = []
        for tx_hash, receipt in receipts.items():
            tx_context = tx_contexts.get(tx_hash)
            if tx_context is None:
                continue
            events.extend(self.extract_swap_events(tx_context, receipt))
        return events

    def _parse_swap_log(
        self,
        *,
        tx_context: TransactionContext,
        block_number: int | None,
        log: Mapping[str, Any],
    ) -> RawSwapEvent | None:
        topics = log.get("topics") or []
        if not topics:
            return None

        topic0 = normalize_topic0(topics[0])
        dex_kind = SWAP_SIGNATURES.get(topic0 or "")
        if dex_kind is None:
            return None

        words = parse_data_words(log.get("data"))
        if not words:
            return None

        base_event_kwargs = {
            "tx_hash": tx_context.tx_hash,
            "matched_trader_addresses": tx_context.matched_trader_addresses,
            "block_number": block_number,
            "pool_address": log.get("address"),
            "dex_kind": dex_kind,
        }

        if topic0 == UNISWAP_V2_SWAP_TOPIC0:
            return self._parse_uniswap_v2_event(words, base_event_kwargs)
        if topic0 == UNISWAP_V3_SWAP_TOPIC0:
            return self._parse_uniswap_v3_event(words, base_event_kwargs)
        if topic0 in {CURVE_TOKEN_EXCHANGE_TOPIC0, CURVE_TOKEN_EXCHANGE_UNDERLYING_TOPIC0}:
            return self._parse_curve_event(
                words,
                use_underlying=topic0 == CURVE_TOKEN_EXCHANGE_UNDERLYING_TOPIC0,
                base_event_kwargs=base_event_kwargs,
            )
        return None

    @staticmethod
    def _parse_uniswap_v2_event(
        words: list[str],
        base_event_kwargs: dict[str, Any],
    ) -> RawSwapEvent | None:
        if len(words) < 4:
            return None

        amount0_in = word_to_int(words[0])
        amount1_in = word_to_int(words[1])
        amount0_out = word_to_int(words[2])
        amount1_out = word_to_int(words[3])

        side: SwapSide | None = None
        if amount0_in > 0 and amount1_out > 0:
            side = SwapSide.BUY
        elif amount1_in > 0 and amount0_out > 0:
            side = SwapSide.SELL

        return RawSwapEvent(
            **base_event_kwargs,
            side=side,
            amount_a_raw=max(amount0_in, amount0_out),
            amount_b_raw=max(amount1_in, amount1_out),
        )

    @staticmethod
    def _parse_uniswap_v3_event(
        words: list[str],
        base_event_kwargs: dict[str, Any],
    ) -> RawSwapEvent | None:
        if len(words) < 2:
            return None

        amount0_signed = word_to_int(words[0], signed=True)
        amount1_signed = word_to_int(words[1], signed=True)

        side: SwapSide | None = None
        if amount0_signed > 0 and amount1_signed < 0:
            side = SwapSide.BUY
        elif amount1_signed > 0 and amount0_signed < 0:
            side = SwapSide.SELL

        return RawSwapEvent(
            **base_event_kwargs,
            side=side,
            amount_a_raw=abs(amount0_signed),
            amount_b_raw=abs(amount1_signed),
        )

    @staticmethod
    def _parse_curve_event(
        words: list[str],
        *,
        use_underlying: bool,
        base_event_kwargs: dict[str, Any],
    ) -> RawSwapEvent | None:
        if len(words) < 4:
            return None

        sold_id = word_to_int(words[0], signed=True)
        bought_id = word_to_int(words[2], signed=True)
        if sold_id < 0 or bought_id < 0:
            return None

        return RawSwapEvent(
            **base_event_kwargs,
            side=SwapSide.BUY,
            amount_a_raw=word_to_int(words[1]),
            amount_b_raw=word_to_int(words[3]),
            curve_sold_id=sold_id,
            curve_bought_id=bought_id,
            curve_use_underlying=use_underlying,
        )
