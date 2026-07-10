from __future__ import annotations

import argparse
import asyncio
import logging

from ..core.container import Container
from .common import (
    prepare_cli_runtime,
    print_reverse_scan_summary,
    require_block_window,
    shutdown_cli_container,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run standalone reverse scan for pools discovered in the database.",
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

    asyncio.run(
        _run_reverse_scan(
            start_block=start_block,
            end_block=end_block,
        )
    )


async def _run_reverse_scan(
    *,
    start_block: int,
    end_block: int,
) -> None:
    container = Container()
    try:
        orchestrator = container.scan_orchestrator_service()
        logger.info(
            "Starting standalone reverse scan from database pools for blocks %s-%s",
            f"{start_block:,}",
            f"{end_block:,}",
        )
        result = await orchestrator.run_reverse_scan_from_database(
            start_block=start_block,
            end_block=end_block,
        )
        print_reverse_scan_summary(result)
    finally:
        await shutdown_cli_container(container)


if __name__ == "__main__":
    main()
