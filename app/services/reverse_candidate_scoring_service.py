from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..core.config import Settings
from ..schemas import (
    EnrichedSwapEvent,
    ReverseHeuristicsTopLevelField,
    ReversePoolResult,
    ReverseScanResult,
    TransactionContext,
)
from .pool_metadata_service import PoolMetadataService
from .receipt_collection_service import ReceiptCollectionService
from .reverse_search_service import ReverseCompetitionDiscoveryResult
from .swap_parsing_service import SwapParsingService
from .trade_enrichment_service import TradeEnrichmentService

logger = logging.getLogger(__name__)

ZERO_DECIMAL = Decimal("0")
COMPONENT_LABELS = {
    "top_level_match_count": "top-level",
    "raw_tx_count": "raw tx",
    "raw_pool_count": "pools",
    "matched_swap_count": "matched swaps",
    "priced_swap_count": "priced swaps",
    "total_usd_volume": "total usd",
    "max_swap_usd": "max swap usd",
}


@dataclass(frozen=True)
class ReverseCandidateEvidence:
    competitor_address: str
    pool_addresses: tuple[str, ...]
    transaction_hashes: tuple[str, ...]
    matched_pool_addresses_by_tx_hash: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ReverseCandidateMetrics:
    competitor_address: str
    raw_tx_count: int
    raw_pool_count: int
    matched_swap_count: int
    priced_swap_count: int
    total_usd_volume: Decimal
    max_swap_usd: Decimal
    top_level_match_count: int


@dataclass(frozen=True)
class ReverseScoreComponent:
    name: str
    metric_value: int | Decimal
    threshold: int | Decimal | None
    weight: int
    enabled: bool
    passed: bool
    contribution: int


@dataclass(frozen=True)
class ReverseCandidateEvaluation:
    competitor_address: str
    pool_addresses: tuple[str, ...]
    transaction_hashes: tuple[str, ...]
    metrics: ReverseCandidateMetrics
    score_breakdown: tuple[ReverseScoreComponent, ...]
    score: int
    passed: bool


class ReverseCandidateScoringService:
    def __init__(
        self,
        receipt_collection_service: ReceiptCollectionService,
        swap_parsing_service: SwapParsingService,
        pool_metadata_service: PoolMetadataService,
        trade_enrichment_service: TradeEnrichmentService,
        settings: Settings,
    ) -> None:
        self._receipt_collection_service = receipt_collection_service
        self._swap_parsing_service = swap_parsing_service
        self._pool_metadata_service = pool_metadata_service
        self._trade_enrichment_service = trade_enrichment_service
        self._settings = settings

    async def apply_heuristics(
        self,
        discovery: ReverseCompetitionDiscoveryResult,
    ) -> ReverseScanResult:
        if not discovery.matches:
            return self._build_result(discovery, approved_candidates=set())

        if not self._settings.reverse_heuristics_enabled:
            return self._build_result(
                discovery,
                approved_candidates=set(discovery.competitor_addresses),
            )

        evidence_by_candidate = self._build_candidate_evidence(discovery)
        receipts = await self._receipt_collection_service.collect_receipts(
            sorted({tx_hash for evidence in evidence_by_candidate.values() for tx_hash in evidence.transaction_hashes})
        )
        events_by_candidate = await self._collect_candidate_events(evidence_by_candidate, receipts)
        evaluations = {
            candidate_address: self._evaluate_candidate(
                evidence,
                receipts,
                events_by_candidate.get(candidate_address, []),
            )
            for candidate_address, evidence in sorted(evidence_by_candidate.items())
        }

        approved_candidates = {
            evaluation.competitor_address
            for evaluation in evaluations.values()
            if evaluation.passed
        }
        self._log_evaluations(evaluations.values())
        return self._build_result(
            discovery,
            approved_candidates=approved_candidates,
        )

    def _build_candidate_evidence(
        self,
        discovery: ReverseCompetitionDiscoveryResult,
    ) -> dict[str, ReverseCandidateEvidence]:
        pool_addresses_by_candidate: dict[str, set[str]] = defaultdict(set)
        tx_hashes_by_candidate: dict[str, set[str]] = defaultdict(set)
        matched_pool_addresses_by_candidate_tx: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )

        for match in discovery.matches:
            pool_addresses_by_candidate[match.competitor_address].add(match.pool_address)
            tx_hashes_by_candidate[match.competitor_address].add(match.tx_hash)
            matched_pool_addresses_by_candidate_tx[match.competitor_address][match.tx_hash].add(
                match.pool_address
            )

        return {
            competitor_address: ReverseCandidateEvidence(
                competitor_address=competitor_address,
                pool_addresses=tuple(sorted(pool_addresses_by_candidate[competitor_address])),
                transaction_hashes=tuple(sorted(tx_hashes_by_candidate[competitor_address])),
                matched_pool_addresses_by_tx_hash={
                    tx_hash: tuple(sorted(pool_addresses))
                    for tx_hash, pool_addresses in sorted(
                        matched_pool_addresses_by_candidate_tx[competitor_address].items()
                    )
                },
            )
            for competitor_address in sorted(pool_addresses_by_candidate)
        }

    async def _collect_candidate_events(
        self,
        evidence_by_candidate: Mapping[str, ReverseCandidateEvidence],
        receipts: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, list[EnrichedSwapEvent]]:
        raw_events = []
        for evidence in evidence_by_candidate.values():
            for tx_hash in evidence.transaction_hashes:
                receipt = receipts.get(tx_hash)
                if receipt is None:
                    continue

                matched_pool_addresses = set(
                    evidence.matched_pool_addresses_by_tx_hash.get(tx_hash, ())
                )
                if not matched_pool_addresses:
                    continue

                tx_context = TransactionContext(
                    tx_hash=tx_hash,
                    matched_trader_addresses=[evidence.competitor_address],
                )
                raw_events.extend(
                    event
                    for event in self._swap_parsing_service.extract_swap_events(tx_context, receipt)
                    if event.pool_address in matched_pool_addresses
                )

        if not raw_events:
            return {}

        pool_enriched_events = await self._pool_metadata_service.enrich_events(raw_events)
        pricing_context = await self._trade_enrichment_service.resolve_pricing_context(
            pool_enriched_events,
        )
        enriched_events = await self._trade_enrichment_service.enrich_events(
            pool_enriched_events,
            receipts,
            pricing_context=pricing_context,
        )

        events_by_candidate: dict[str, list[EnrichedSwapEvent]] = defaultdict(list)
        for event in enriched_events:
            competitor_address = None
            if event.matched_trader_addresses:
                competitor_address = event.matched_trader_addresses[0]
            elif event.trader_address:
                competitor_address = event.trader_address

            if competitor_address is None:
                continue
            events_by_candidate[competitor_address].append(event)

        return dict(events_by_candidate)

    def _evaluate_candidate(
        self,
        evidence: ReverseCandidateEvidence,
        receipts: Mapping[str, Mapping[str, Any]],
        candidate_events: list[EnrichedSwapEvent],
    ) -> ReverseCandidateEvaluation:
        metrics = ReverseCandidateMetrics(
            competitor_address=evidence.competitor_address,
            raw_tx_count=len(evidence.transaction_hashes),
            raw_pool_count=len(evidence.pool_addresses),
            matched_swap_count=len(candidate_events),
            priced_swap_count=sum(1 for event in candidate_events if event.usd_amount is not None),
            total_usd_volume=sum(
                ((event.usd_amount or ZERO_DECIMAL) for event in candidate_events),
                start=ZERO_DECIMAL,
            ),
            max_swap_usd=max(
                (event.usd_amount or ZERO_DECIMAL)
                for event in candidate_events
            ) if candidate_events else ZERO_DECIMAL,
            top_level_match_count=self._count_top_level_matches(evidence, receipts),
        )
        score_breakdown = self._build_score_breakdown(metrics)
        score = sum(component.contribution for component in score_breakdown)
        return ReverseCandidateEvaluation(
            competitor_address=evidence.competitor_address,
            pool_addresses=evidence.pool_addresses,
            transaction_hashes=evidence.transaction_hashes,
            metrics=metrics,
            score_breakdown=tuple(score_breakdown),
            score=score,
            passed=score >= self._settings.reverse_heuristics_min_pass_score,
        )

    def _build_score_breakdown(
        self,
        metrics: ReverseCandidateMetrics,
    ) -> list[ReverseScoreComponent]:
        top_level_enabled = (
            self._settings.reverse_heuristics_top_level_field != ReverseHeuristicsTopLevelField.OFF
            and self._settings.reverse_heuristics_top_level_match_weight > 0
        )
        return [
            self._build_component(
                name="top_level_match_count",
                metric_value=metrics.top_level_match_count,
                threshold=self._settings.reverse_heuristics_top_level_match_min,
                weight=self._settings.reverse_heuristics_top_level_match_weight,
                enabled=top_level_enabled,
            ),
            self._build_component(
                name="raw_tx_count",
                metric_value=metrics.raw_tx_count,
                threshold=self._settings.reverse_heuristics_raw_tx_min,
                weight=self._settings.reverse_heuristics_raw_tx_weight,
            ),
            self._build_component(
                name="raw_pool_count",
                metric_value=metrics.raw_pool_count,
                threshold=self._settings.reverse_heuristics_pool_count_min,
                weight=self._settings.reverse_heuristics_pool_count_weight,
            ),
            self._build_component(
                name="matched_swap_count",
                metric_value=metrics.matched_swap_count,
                threshold=self._settings.reverse_heuristics_matched_swap_min,
                weight=self._settings.reverse_heuristics_matched_swap_weight,
            ),
            self._build_component(
                name="priced_swap_count",
                metric_value=metrics.priced_swap_count,
                threshold=self._settings.reverse_heuristics_priced_swap_min,
                weight=self._settings.reverse_heuristics_priced_swap_weight,
            ),
            self._build_component(
                name="total_usd_volume",
                metric_value=metrics.total_usd_volume,
                threshold=self._settings.reverse_heuristics_total_usd_min,
                weight=self._settings.reverse_heuristics_total_usd_weight,
            ),
            self._build_component(
                name="max_swap_usd",
                metric_value=metrics.max_swap_usd,
                threshold=self._settings.reverse_heuristics_max_swap_usd_min,
                weight=self._settings.reverse_heuristics_max_swap_usd_weight,
            ),
        ]

    @staticmethod
    def _build_component(
        *,
        name: str,
        metric_value: int | Decimal,
        threshold: int | Decimal,
        weight: int,
        enabled: bool = True,
    ) -> ReverseScoreComponent:
        if not enabled or weight <= 0:
            return ReverseScoreComponent(
                name=name,
                metric_value=metric_value,
                threshold=threshold,
                weight=weight,
                enabled=False,
                passed=False,
                contribution=0,
            )

        passed = metric_value >= threshold
        return ReverseScoreComponent(
            name=name,
            metric_value=metric_value,
            threshold=threshold,
            weight=weight,
            enabled=True,
            passed=passed,
            contribution=weight if passed else 0,
        )

    def _count_top_level_matches(
        self,
        evidence: ReverseCandidateEvidence,
        receipts: Mapping[str, Mapping[str, Any]],
    ) -> int:
        top_level_field = self._settings.reverse_heuristics_top_level_field
        if top_level_field == ReverseHeuristicsTopLevelField.OFF:
            return 0

        return sum(
            1
            for tx_hash in evidence.transaction_hashes
            if self._extract_top_level_address(receipts.get(tx_hash), top_level_field)
            == evidence.competitor_address
        )

    @staticmethod
    def _extract_top_level_address(
        receipt: Mapping[str, Any] | None,
        top_level_field: ReverseHeuristicsTopLevelField,
    ) -> str | None:
        if receipt is None:
            return None
        value = receipt.get(top_level_field.value)
        return str(value).strip().lower() if value else None

    def _build_result(
        self,
        discovery: ReverseCompetitionDiscoveryResult,
        *,
        approved_candidates: set[str],
    ) -> ReverseScanResult:
        pools: list[ReversePoolResult] = []
        for pool_result in discovery.pools:
            discovered_competitors = tuple(pool_result.competitor_addresses)
            approved_competitors = (
                discovered_competitors
                if not self._settings.reverse_heuristics_enabled
                else tuple(
                    competitor_address
                    for competitor_address in discovered_competitors
                    if competitor_address in approved_candidates
                )
            )
            pools.append(
                pool_result.model_copy(
                    update={
                        "competitor_addresses": list(approved_competitors),
                        "competitor_count": len(approved_competitors),
                        "discovered_competitor_count": len(discovered_competitors),
                        "rejected_competitor_count": (
                            len(discovered_competitors) - len(approved_competitors)
                        ),
                    }
                )
            )

        discovered_competitor_count = len(discovery.competitor_addresses)
        approved_competitor_count = (
            discovered_competitor_count
            if not self._settings.reverse_heuristics_enabled
            else len(approved_candidates)
        )

        return ReverseScanResult(
            request=discovery.request,
            pools=pools,
            active_pool_count=discovery.active_pool_count,
            total_competitor_count=approved_competitor_count,
            discovered_competitor_count=discovered_competitor_count,
            approved_competitor_count=approved_competitor_count,
            rejected_competitor_count=max(
                discovered_competitor_count - approved_competitor_count,
                0,
            ),
            total_transaction_count=discovery.total_transaction_count,
        )

    def _log_evaluations(
        self,
        evaluations: Iterable[ReverseCandidateEvaluation],
    ) -> None:
        evaluation_list = sorted(
            evaluations,
            key=lambda evaluation: (evaluation.score, evaluation.competitor_address),
        )
        approved_count = sum(1 for evaluation in evaluation_list if evaluation.passed)
        rejected = [
            evaluation
            for evaluation in evaluation_list
            if not evaluation.passed
        ]
        logger.info(
            "Reverse heuristics evaluated %s candidate(s): approved=%s, rejected=%s, pass_score=%s",
            f"{len(evaluation_list):,}",
            f"{approved_count:,}",
            f"{len(rejected):,}",
            self._settings.reverse_heuristics_min_pass_score,
        )

        sample_limit = self._settings.reverse_heuristics_log_sample_limit
        if sample_limit <= 0 or not rejected:
            return

        shown_rejected = rejected[:sample_limit]
        logger.info(
            "Reverse heuristics rejected sample: showing %s of %s candidate(s)",
            f"{len(shown_rejected):,}",
            f"{len(rejected):,}",
        )

        for index, evaluation in enumerate(shown_rejected, start=1):
            logger.info(
                "Rejected %s/%s %s score=%s/%s | %s | failed: %s",
                index,
                len(shown_rejected),
                evaluation.competitor_address,
                evaluation.score,
                self._settings.reverse_heuristics_min_pass_score,
                self._format_metrics_summary(evaluation),
                self._format_failed_components(
                    evaluation,
                    min_pass_score=self._settings.reverse_heuristics_min_pass_score,
                ),
            )

    @classmethod
    def _format_failed_components(
        cls,
        evaluation: ReverseCandidateEvaluation,
        *,
        min_pass_score: int,
    ) -> str:
        failed_components = [
            component
            for component in evaluation.score_breakdown
            if component.enabled and not component.passed
        ]
        if not failed_components:
            return f"score {evaluation.score} < {min_pass_score}"

        return ", ".join(
            cls._format_failed_component(component)
            for component in failed_components
        )

    @classmethod
    def _format_failed_component(
        cls,
        component: ReverseScoreComponent,
    ) -> str:
        label = COMPONENT_LABELS.get(component.name, component.name)
        if component.threshold is None:
            return f"{label} not met (+{component.weight})"
        return (
            f"{label} {cls._format_metric_value(component.metric_value)}"
            f" < {cls._format_metric_value(component.threshold)}"
            f" (+{component.weight})"
        )

    @classmethod
    def _format_metrics_summary(
        cls,
        evaluation: ReverseCandidateEvaluation,
    ) -> str:
        metrics = evaluation.metrics
        return (
            f"pools={metrics.raw_pool_count}, "
            f"tx={metrics.raw_tx_count}, "
            f"swaps={metrics.matched_swap_count}, "
            f"priced={metrics.priced_swap_count}, "
            f"volume={cls._format_usd(metrics.total_usd_volume)}, "
            f"max_swap={cls._format_usd(metrics.max_swap_usd)}"
        )

    @classmethod
    def _format_metric_value(
        cls,
        value: int | Decimal,
    ) -> str:
        if isinstance(value, Decimal):
            return cls._format_usd(value)
        return f"{value:,}"

    @staticmethod
    def _format_usd(value: Decimal) -> str:
        quantized = value.quantize(Decimal("0.01"))
        formatted = f"{quantized:,.2f}"
        if formatted.endswith(".00"):
            formatted = formatted[:-3]
        return f"${formatted}"


__all__ = [
    "ReverseCandidateEvaluation",
    "ReverseCandidateEvidence",
    "ReverseCandidateMetrics",
    "ReverseCandidateScoringService",
    "ReverseScoreComponent",
]
