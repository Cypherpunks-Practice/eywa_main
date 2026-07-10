from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ..core.config import Settings
from ..utils import batch_resolve
from .rpc_gateway_service import RpcGatewayService

logger = logging.getLogger(__name__)


class ReceiptCollectionService:
    def __init__(
        self,
        rpc_gateway_service: RpcGatewayService,
        settings: Settings,
    ):
        self._rpc_gateway_service = rpc_gateway_service
        self._settings = settings

    async def collect_receipts(self, tx_hashes: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not tx_hashes:
            return {}

        ordered_tx_hashes = sorted(set(tx_hashes))
        logger.info("Collecting receipts for %s transaction(s)", f"{len(ordered_tx_hashes):,}")

        async def resolve_one(tx_hash: str) -> tuple[str, dict[str, Any] | None]:
            return tx_hash, await self._rpc_gateway_service.get_transaction_receipt(tx_hash)

        results = await batch_resolve(
            ordered_tx_hashes,
            batch_resolver=(
                self._rpc_gateway_service.get_transaction_receipts_batch
                if self._rpc_gateway_service.supports_batch_requests()
                else None
            ),
            individual_resolver=resolve_one,
            batch_size=self._settings.receipt_batch_size,
            max_concurrency=self._settings.max_workers,
        )
        receipts = {k: v for k, v in results.items() if isinstance(v, dict)}

        logger.info(
            "Receipt collection completed: %s/%s receipt(s) resolved",
            f"{len(receipts):,}",
            f"{len(ordered_tx_hashes):,}",
        )
        return receipts
