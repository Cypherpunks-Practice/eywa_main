from __future__ import annotations

import argparse
import asyncio
import logging

from ..core.container import Container
from .common import prepare_cli_runtime, print_backfill_summary, shutdown_cli_container

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Run historical data backfill for an existing eywa database.",
    )


def main() -> None:
    build_parser().parse_args()
    prepare_cli_runtime()
    asyncio.run(_run_backfill())


async def _run_backfill() -> None:
    container = Container()
    try:
        logger.info("Starting database backfill")
        backfill_service = container.database_backfill_service()
        result = await backfill_service.backfill_database()
        print_backfill_summary(result)
    finally:
        await shutdown_cli_container(container)


if __name__ == "__main__":
    main()
