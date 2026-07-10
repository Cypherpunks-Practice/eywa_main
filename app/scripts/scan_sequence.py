from __future__ import annotations

import argparse
import asyncio
import logging

from ..core.container import Container
from ..schemas import ReverseScanSource, TraceAddressRole
from .common import (
    prepare_cli_runtime,
    print_forward_scan_summary,
    print_reverse_scan_summary,
    require_block_window,
    shutdown_cli_container,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run eywa forward scan followed by reverse scan with configurable pool source.",
    )
    parser.add_argument(
        "--start-block",
        type=int,
        default=None,
        help="First block of the scan window. Must be used together with --end-block.",
    )
    parser.add_argument(
        "--end-block",
        type=int,
        default=None,
        help="Last block of the scan window. Must be used together with --start-block.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of repeated forward+reverse scan cycles for the same block range.",
    )
    parser.add_argument(
        "--trace-address-role",
        choices=[role.value for role in TraceAddressRole],
        default=TraceAddressRole.TO.value,
        help="Address role to use for the forward trace_filter lookup.",
    )
    parser.add_argument(
        "--pool-source",
        "--reverse-pool-source",
        dest="pool_source",
        choices=[source.value for source in ReverseScanSource],
        default=ReverseScanSource.DATABASE.value,
        help="Pool source for reverse scan: forward, database, or mixed.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.runs <= 0:
        raise SystemExit("--runs must be positive")

    start_block, end_block = require_block_window(
        parser,
        start_block=args.start_block,
        end_block=args.end_block,
    )
    prepare_cli_runtime()
    trace_address_role = TraceAddressRole(args.trace_address_role)
    pool_source = ReverseScanSource(args.pool_source)

    asyncio.run(
        _run_scan_sequence(
            start_block=start_block,
            end_block=end_block,
            runs=args.runs,
            trace_address_role=trace_address_role,
            pool_source=pool_source,
        )
    )


async def _run_scan_sequence(
    *,
    start_block: int,
    end_block: int,
    runs: int,
    trace_address_role: TraceAddressRole,
    pool_source: ReverseScanSource,
) -> None:
    container = Container()
    try:
        logger.info("Building scan sequence services")
        orchestrator = container.scan_orchestrator_service()

        for run_index in range(1, runs + 1):
            logger.info(
                "Starting CLI scan sequence run %s/%s for fixed blocks %s-%s",
                run_index,
                runs,
                f"{start_block:,}",
                f"{end_block:,}",
            )
            result = await orchestrator.run_scan_sequence(
                start_block=start_block,
                end_block=end_block,
                persist=True,
                trace_address_role=trace_address_role,
                pool_source=pool_source,
            )
            print(f"Run {run_index}/{runs}")
            print_forward_scan_summary(result.forward_result)
            print_reverse_scan_summary(result.reverse_result)
            if run_index < runs:
                print()
    finally:
        await shutdown_cli_container(container)


if __name__ == "__main__":
    main()
