from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.schemas import DexKind, EnrichedSwapEvent, RawSwapEvent, ReverseHeuristicsTopLevelField, ReversePoolResult, ReverseScanRequest
from app.services.reverse_candidate_scoring_service import ReverseCandidateScoringService
from app.services.reverse_search_service import ReverseCompetitionDiscoveryResult, ReverseCompetitionMatch


def _address(hex_digit: str) -> str:
    return "0x" + (hex_digit * 40)


def _tx_hash(hex_digit: str) -> str:
    return "0x" + (hex_digit * 64)


class FakeReceiptCollectionService:
    def __init__(self, receipts: dict[str, dict[str, str]]) -> None:
        self._receipts = receipts

    async def collect_receipts(self, tx_hashes: list[str]) -> dict[str, dict[str, str]]:
        return {
            tx_hash: self._receipts[tx_hash]
            for tx_hash in tx_hashes
            if tx_hash in self._receipts
        }


class FakeSwapParsingService:
    def __init__(self, events_by_tx_hash: dict[str, list[RawSwapEvent]]) -> None:
        self._events_by_tx_hash = events_by_tx_hash

    def extract_swap_events(self, tx_context, receipt) -> list[RawSwapEvent]:
        return [
            event.model_copy(update={"matched_trader_addresses": list(tx_context.matched_trader_addresses)})
            for event in self._events_by_tx_hash.get(tx_context.tx_hash, [])
        ]


class FakePoolMetadataService:
    async def enrich_events(self, events: list[RawSwapEvent]) -> list[EnrichedSwapEvent]:
        return [
            EnrichedSwapEvent.model_validate(event.model_dump())
            for event in events
        ]


class FakeTradeEnrichmentService:
    def __init__(
        self,
        usd_amounts: dict[tuple[str, str, str], Decimal | None],
    ) -> None:
        self._usd_amounts = usd_amounts

    async def resolve_pricing_context(self, events: list[EnrichedSwapEvent]) -> object:
        return {"event_count": len(events)}

    async def enrich_events(
        self,
        events: list[EnrichedSwapEvent],
        receipts,
        *,
        pricing_context,
    ) -> list[EnrichedSwapEvent]:
        enriched_events: list[EnrichedSwapEvent] = []
        for event in events:
            competitor_address = event.matched_trader_addresses[0]
            enriched_events.append(
                event.model_copy(
                    update={
                        "trader_address": competitor_address,
                        "usd_amount": self._usd_amounts.get(
                            (competitor_address, event.tx_hash, event.pool_address)
                        ),
                    }
                )
            )
        return enriched_events


def _build_settings(**overrides) -> Settings:
    settings_kwargs = {
        "reverse_heuristics_enabled": True,
        "reverse_heuristics_min_pass_score": 1,
        "reverse_heuristics_top_level_match_weight": 0,
        "reverse_heuristics_raw_tx_weight": 0,
        "reverse_heuristics_pool_count_weight": 0,
        "reverse_heuristics_matched_swap_weight": 0,
        "reverse_heuristics_priced_swap_weight": 0,
        "reverse_heuristics_total_usd_weight": 0,
        "reverse_heuristics_max_swap_usd_weight": 0,
    }
    settings_kwargs.update(overrides)
    return Settings(_env_file=None, **settings_kwargs)


def _raw_event(tx_hash: str, pool_address: str) -> RawSwapEvent:
    return RawSwapEvent(
        tx_hash=tx_hash,
        pool_address=pool_address,
        dex_kind=DexKind.UNISWAP_V3,
        amount_a_raw=1,
        amount_b_raw=1,
    )


def _build_discovery(
    match_rows: list[tuple[str, str, str]],
) -> ReverseCompetitionDiscoveryResult:
    pool_addresses = sorted({pool_address for pool_address, _, _ in match_rows})
    request = ReverseScanRequest(
        start_block=1,
        end_block=1,
        pool_addresses=pool_addresses,
    )

    competitors_by_pool: dict[str, set[str]] = defaultdict(set)
    tx_hashes_by_pool: dict[str, set[str]] = defaultdict(set)
    competitor_addresses: set[str] = set()
    matches = [
        ReverseCompetitionMatch(
            pool_address=pool_address,
            competitor_address=competitor_address,
            tx_hash=tx_hash,
        )
        for pool_address, competitor_address, tx_hash in match_rows
    ]
    for pool_address, competitor_address, tx_hash in match_rows:
        competitors_by_pool[pool_address].add(competitor_address)
        tx_hashes_by_pool[pool_address].add(tx_hash)
        competitor_addresses.add(competitor_address)

    pools = [
        ReversePoolResult(
            pool_address=pool_address,
            competitor_addresses=sorted(competitors_by_pool[pool_address]),
            competitor_count=len(competitors_by_pool[pool_address]),
            discovered_competitor_count=len(competitors_by_pool[pool_address]),
            rejected_competitor_count=0,
            transaction_hashes=sorted(tx_hashes_by_pool[pool_address]),
            transaction_count=len(tx_hashes_by_pool[pool_address]),
        )
        for pool_address in pool_addresses
    ]
    return ReverseCompetitionDiscoveryResult(
        request=request,
        pools=pools,
        matches=tuple(matches),
        competitor_addresses=tuple(sorted(competitor_addresses)),
        active_pool_count=sum(1 for pool in pools if pool.transaction_count > 0),
        total_transaction_count=sum(pool.transaction_count for pool in pools),
    )


def _build_service(
    *,
    settings: Settings,
    receipts: dict[str, dict[str, str]],
    events_by_tx_hash: dict[str, list[RawSwapEvent]],
    usd_amounts: dict[tuple[str, str, str], Decimal | None],
) -> ReverseCandidateScoringService:
    return ReverseCandidateScoringService(
        receipt_collection_service=FakeReceiptCollectionService(receipts),
        swap_parsing_service=FakeSwapParsingService(events_by_tx_hash),
        pool_metadata_service=FakePoolMetadataService(),
        trade_enrichment_service=FakeTradeEnrichmentService(usd_amounts),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_candidate_passes_when_weighted_score_reaches_threshold() -> None:
    competitor = _address("a")
    pool = _address("1")
    tx_hash_1 = _tx_hash("1")
    tx_hash_2 = _tx_hash("2")
    discovery = _build_discovery(
        [
            (pool, competitor, tx_hash_1),
            (pool, competitor, tx_hash_2),
        ]
    )
    service = _build_service(
        settings=_build_settings(
            reverse_heuristics_min_pass_score=4,
            reverse_heuristics_raw_tx_min=2,
            reverse_heuristics_raw_tx_weight=2,
            reverse_heuristics_matched_swap_min=2,
            reverse_heuristics_matched_swap_weight=2,
            reverse_heuristics_total_usd_min=Decimal("1000"),
            reverse_heuristics_total_usd_weight=2,
        ),
        receipts={
            tx_hash_1: {"to": competitor},
            tx_hash_2: {"to": _address("f")},
        },
        events_by_tx_hash={
            tx_hash_1: [_raw_event(tx_hash_1, pool)],
            tx_hash_2: [_raw_event(tx_hash_2, pool)],
        },
        usd_amounts={
            (competitor, tx_hash_1, pool): Decimal("900"),
            (competitor, tx_hash_2, pool): Decimal("700"),
        },
    )

    result = await service.apply_heuristics(discovery)

    assert result.discovered_competitor_count == 1
    assert result.approved_competitor_count == 1
    assert result.rejected_competitor_count == 0
    assert result.pools[0].competitor_addresses == [competitor]


@pytest.mark.asyncio
async def test_candidate_fails_when_score_is_below_threshold() -> None:
    competitor = _address("b")
    pool = _address("2")
    tx_hash = _tx_hash("3")
    discovery = _build_discovery([(pool, competitor, tx_hash)])
    service = _build_service(
        settings=_build_settings(
            reverse_heuristics_min_pass_score=4,
            reverse_heuristics_raw_tx_min=1,
            reverse_heuristics_raw_tx_weight=2,
        ),
        receipts={tx_hash: {"to": _address("f")}},
        events_by_tx_hash={},
        usd_amounts={},
    )

    result = await service.apply_heuristics(discovery)

    assert result.approved_competitor_count == 0
    assert result.rejected_competitor_count == 1
    assert result.pools[0].competitor_addresses == []


@pytest.mark.asyncio
async def test_weight_zero_disables_signal_contribution() -> None:
    competitor = _address("c")
    pool = _address("3")
    tx_hash = _tx_hash("4")
    discovery = _build_discovery([(pool, competitor, tx_hash)])
    service = _build_service(
        settings=_build_settings(
            reverse_heuristics_min_pass_score=1,
            reverse_heuristics_top_level_match_min=1,
            reverse_heuristics_top_level_match_weight=0,
        ),
        receipts={tx_hash: {"to": competitor}},
        events_by_tx_hash={},
        usd_amounts={},
    )

    result = await service.apply_heuristics(discovery)

    assert result.approved_competitor_count == 0
    assert result.rejected_competitor_count == 1


@pytest.mark.asyncio
async def test_top_level_field_off_disables_that_signal() -> None:
    competitor = _address("d")
    pool = _address("4")
    tx_hash = _tx_hash("5")
    discovery = _build_discovery([(pool, competitor, tx_hash)])
    service = _build_service(
        settings=_build_settings(
            reverse_heuristics_min_pass_score=3,
            reverse_heuristics_top_level_field=ReverseHeuristicsTopLevelField.OFF,
            reverse_heuristics_top_level_match_min=1,
            reverse_heuristics_top_level_match_weight=3,
        ),
        receipts={tx_hash: {"to": competitor}},
        events_by_tx_hash={},
        usd_amounts={},
    )

    result = await service.apply_heuristics(discovery)

    assert result.approved_competitor_count == 0
    assert result.rejected_competitor_count == 1


@pytest.mark.asyncio
async def test_shared_transaction_hashes_do_not_leak_swap_evidence_between_candidates() -> None:
    competitor_a = _address("e")
    competitor_b = _address("f")
    pool_1 = _address("5")
    pool_2 = _address("6")
    tx_hash_1 = _tx_hash("6")
    tx_hash_2 = _tx_hash("7")
    discovery = _build_discovery(
        [
            (pool_1, competitor_a, tx_hash_1),
            (pool_2, competitor_a, tx_hash_2),
            (pool_2, competitor_b, tx_hash_1),
        ]
    )
    service = _build_service(
        settings=_build_settings(
            reverse_heuristics_min_pass_score=5,
            reverse_heuristics_matched_swap_min=2,
            reverse_heuristics_matched_swap_weight=5,
        ),
        receipts={
            tx_hash_1: {"to": competitor_a},
            tx_hash_2: {"to": competitor_a},
        },
        events_by_tx_hash={
            tx_hash_1: [
                _raw_event(tx_hash_1, pool_1),
                _raw_event(tx_hash_1, pool_2),
            ],
            tx_hash_2: [],
        },
        usd_amounts={
            (competitor_a, tx_hash_1, pool_1): Decimal("50"),
            (competitor_a, tx_hash_1, pool_2): Decimal("75"),
            (competitor_b, tx_hash_1, pool_2): Decimal("75"),
        },
    )

    result = await service.apply_heuristics(discovery)

    assert result.discovered_competitor_count == 2
    assert result.approved_competitor_count == 0
    assert result.rejected_competitor_count == 2
    assert result.pools[0].competitor_addresses == []
    assert result.pools[1].competitor_addresses == []
