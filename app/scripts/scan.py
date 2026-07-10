from __future__ import annotations

import argparse
import asyncio
import logging

from ..core.container import Container
from ..schemas import TraceAddressRole
from .common import (
    prepare_cli_runtime,
    print_forward_scan_summary,
    require_block_window,
    shutdown_cli_container,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run eywa forward scan.",
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
        "--trace-address-role",
        choices=[role.value for role in TraceAddressRole],
        default=TraceAddressRole.TO.value,
        help="Address role to use for the forward trace_filter lookup.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    start_block, end_block = require_block_window(
        parser,
        start_block=args.start_block,
        end_block=args.end_block,
    )
    prepare_cli_runtime()
    trace_address_role = TraceAddressRole(args.trace_address_role)

    asyncio.run(
        _run_scan(start_block=start_block, end_block=end_block, trace_address_role=trace_address_role)
    )


async def _run_scan(
    *,
    start_block: int,
    end_block: int,
    trace_address_role: TraceAddressRole,
) -> None:
    container = Container()
    try:
        logger.info("Building forward scan services")
        orchestrator = container.scan_orchestrator_service()
        logger.info(
            "Starting CLI forward scan for blocks %s-%s",
            f"{start_block:,}",
            f"{end_block:,}",
        )
        result = await orchestrator.run_forward_scan(
            start_block=start_block,
            end_block=end_block,
            persist=True,
            trace_address_role=trace_address_role,
        )
        print_forward_scan_summary(result)
    finally:
        await shutdown_cli_container(container)


if __name__ == "__main__":
    main()
