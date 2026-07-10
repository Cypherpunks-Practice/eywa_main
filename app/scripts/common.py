from __future__ import annotations

import argparse
import logging
from inspect import isawaitable

from ..core.container import Container
from ..core.config import Settings, get_settings
from ..core.logging import configure_logging
from ..schemas import BackfillStats, ReverseScanResult, ScanRunResult
from ..utils.scanning import validate_block_window
from .migrate import apply_migrations

logger = logging.getLogger(__name__)


async def shutdown_cli_container(container: Container) -> None:
    try:
        rpc_gateway_service = container.rpc_gateway_service()
    except Exception:
        rpc_gateway_service = None

    if rpc_gateway_service is not None:
        close = getattr(rpc_gateway_service, "close", None)
        if callable(close):
            result = close()
            if isawaitable(result):
                await result

    shutdown_result = container.shutdown_resources()
    if isawaitable(shutdown_result):
        await shutdown_result

    container.unwire()


def prepare_cli_runtime() -> Settings:
    settings = get_settings()
    configure_logging(settings)
    logger.info("Applying database migrations")
    apply_migrations()
    logger.info("Migrations completed")
    return settings


def require_block_window(
    parser: argparse.ArgumentParser,
    *,
    start_block: int | None,
    end_block: int | None,
) -> tuple[int, int]:
    if start_block is None or end_block is None:
        parser.error("--start-block and --end-block must be provided together")

    validated_start_block = start_block
    validated_end_block = end_block
    assert validated_start_block is not None
    assert validated_end_block is not None

    try:
        return validate_block_window(
            start_block=validated_start_block,
            end_block=validated_end_block,
        )
    except ValueError as exc:
        parser.error(str(exc))


def print_forward_scan_summary(result: ScanRunResult) -> None:
    print(
        f"Forward scan blocks {result.request.start_block:,}-{result.request.end_block:,} "
        f"({result.request.block_count:,} blocks)"
    )
    print(f"Transactions: {result.transaction_count:,}")
    print(f"Receipts: {result.receipt_count:,}")
    print(f"Raw swaps: {result.raw_swap_count:,}")
    print(f"Enriched swaps: {result.enriched_swap_count:,}")
    if result.persist_stats is not None:
        print(f"Inserted transactions: {result.persist_stats.inserted_transactions:,}")
        print(
            "Skipped existing transactions: "
            f"{result.persist_stats.skipped_existing_transactions:,}"
        )
        print(f"Inserted swaps: {result.persist_stats.inserted_swaps:,}")
        print(f"Skipped existing swaps: {result.persist_stats.skipped_existing_swaps:,}")


def print_reverse_scan_summary(result: ReverseScanResult | None) -> None:
    if result is None:
        print("Reverse scan: skipped (no pools resolved from the database)")
        return

    print(f"Reverse pools: {len(result.pools):,}")
    print(f"Reverse active pools: {result.active_pool_count:,}")
    print(f"Reverse competitors discovered: {result.discovered_competitor_count:,}")
    print(f"Reverse competitors approved: {result.approved_competitor_count:,}")
    print(f"Reverse competitors rejected: {result.rejected_competitor_count:,}")
    print(f"Reverse transactions: {result.total_transaction_count:,}")


def print_backfill_summary(result: BackfillStats) -> None:
    print("Backfill summary")
    print(
        "Tokens: "
        f"scanned={result.tokens.scanned:,}, "
        f"updated={result.tokens.updated:,}, "
        f"skipped={result.tokens.skipped:,}, "
        f"unresolved={result.tokens.unresolved:,}"
    )
    print(
        "Pools: "
        f"scanned={result.pools.scanned:,}, "
        f"updated={result.pools.updated:,}, "
        f"skipped={result.pools.skipped:,}, "
        f"unresolved={result.pools.unresolved:,}"
    )
