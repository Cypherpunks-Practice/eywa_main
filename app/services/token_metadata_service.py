from __future__ import annotations

import asyncio
from collections.abc import Sequence

from sqlalchemy import select

from ..core.config import Settings
from ..core.database import session_scope
from ..core.exceptions import RpcError
from ..models.assets import Token
from ..schemas import TokenMetadata
from ..utils import (
    batch_resolve,
    decode_bytes32_string_call_result,
    decode_string_call_result,
    decode_uint_call_result,
    normalize_address,
)
from .rpc_gateway_service import RpcGatewayService

DECIMALS_SELECTOR = "0x313ce567"
SYMBOL_SELECTOR = "0x95d89b41"


class TokenMetadataService:
    def __init__(
        self,
        rpc_gateway_service: RpcGatewayService,
        settings: Settings,
    ):
        self._rpc_gateway_service = rpc_gateway_service
        self._settings = settings

    async def resolve_token_metadata(
        self,
        token_addresses: Sequence[str],
    ) -> dict[str, TokenMetadata]:
        normalized_addresses = sorted(
            {
                address
                for token_address in token_addresses
                if (address := normalize_address(token_address)) is not None
            }
        )
        if not normalized_addresses:
            return {}

        metadata_by_address = await asyncio.to_thread(
            self._load_token_metadata_from_db, normalized_addresses,
        )
        unresolved_decimals_addresses = [
            token_address
            for token_address in normalized_addresses
            if token_address not in metadata_by_address
        ]
        unresolved_symbol_addresses = [
            token_address
            for token_address in normalized_addresses
            if (
                token_address not in metadata_by_address
                or self.label_needs_resolution(metadata_by_address[token_address].label)
            )
        ]
        resolved_decimals = await self._resolve_token_decimals_rpc(unresolved_decimals_addresses)
        resolved_symbols = await self._resolve_token_symbols_rpc(unresolved_symbol_addresses)

        for token_address in normalized_addresses:
            if token_address in metadata_by_address:
                existing_metadata = metadata_by_address[token_address]
                resolved_label = existing_metadata.label
                if self.label_needs_resolution(existing_metadata.label):
                    resolved_label = resolved_symbols.get(token_address) or existing_metadata.label

                metadata_by_address[token_address] = existing_metadata.model_copy(
                    update={
                        "label": resolved_label,
                        "is_stable": existing_metadata.is_stable
                        or token_address in self._settings.known_stablecoin_hints,
                    }
                )
                continue

            metadata_by_address[token_address] = TokenMetadata(
                contract_address=token_address,
                label=resolved_symbols.get(token_address),
                decimals=resolved_decimals.get(token_address, 18),
                is_stable=token_address in self._settings.known_stablecoin_hints,
            )

        return metadata_by_address

    async def resolve_token_decimals(self, token_addresses: Sequence[str]) -> dict[str, int]:
        return {
            token_address: metadata.decimals
            for token_address, metadata in (
                await self.resolve_token_metadata(token_addresses)
            ).items()
        }

    async def resolve_stablecoin_addresses(self, token_addresses: Sequence[str]) -> set[str]:
        return {
            token_address
            for token_address, metadata in (
                await self.resolve_token_metadata(token_addresses)
            ).items()
            if metadata.is_stable
        }

    @staticmethod
    def label_needs_resolution(label: str | None) -> bool:
        if label is None:
            return True

        normalized_label = label.strip()
        return not normalized_label or normalized_label.startswith("unknown_token_")

    @staticmethod
    def _load_token_metadata_from_db(
        token_addresses: Sequence[str],
    ) -> dict[str, TokenMetadata]:
        with session_scope() as session:
            rows = session.execute(
                select(
                    Token.contract_address,
                    Token.label,
                    Token.decimals,
                    Token.is_stable,
                ).where(Token.contract_address.in_(list(token_addresses)))
            ).all()

        metadata_by_address: dict[str, TokenMetadata] = {}
        for contract_address, label, decimals, is_stable in rows:
            normalized_address = normalize_address(contract_address)
            if normalized_address is None:
                continue

            metadata_by_address[normalized_address] = TokenMetadata(
                contract_address=normalized_address,
                label=label,
                decimals=int(decimals) if decimals is not None else 18,
                is_stable=bool(is_stable),
            )

        return metadata_by_address

    async def _resolve_token_decimals_rpc(
        self,
        token_addresses: Sequence[str],
    ) -> dict[str, int]:
        if not token_addresses:
            return {}

        async def resolve_one(token_address: str) -> tuple[str, int]:
            return token_address, await self._resolve_token_decimals_single(token_address)

        resolved = await batch_resolve(
            list(token_addresses),
            batch_resolver=(
                self._resolve_token_decimals_batch
                if self._rpc_gateway_service.supports_batch_requests()
                else None
            ),
            individual_resolver=resolve_one,
            batch_size=self._settings.token_decimal_batch_size,
            max_concurrency=self._settings.max_workers,
        )

        for token_address in token_addresses:
            resolved.setdefault(token_address, 18)
        return resolved

    async def _resolve_token_symbols_rpc(
        self,
        token_addresses: Sequence[str],
    ) -> dict[str, str | None]:
        if not token_addresses:
            return {}

        async def resolve_one(token_address: str) -> tuple[str, str | None]:
            return token_address, await self._resolve_token_symbol_single(token_address)

        return await batch_resolve(
            list(token_addresses),
            batch_resolver=(
                self._resolve_token_symbols_batch
                if self._rpc_gateway_service.supports_batch_requests()
                else None
            ),
            individual_resolver=resolve_one,
            batch_size=self._settings.token_decimal_batch_size,
            max_concurrency=self._settings.max_workers,
        )

    async def _resolve_token_decimals_batch(
        self,
        token_addresses: Sequence[str],
    ) -> dict[str, int | None]:
        results = await self._rpc_gateway_service.eth_call_many(
            [(token_address, DECIMALS_SELECTOR) for token_address in token_addresses]
        )
        return {
            token_address: decode_uint_call_result(result)
            for token_address, result in zip(token_addresses, results, strict=False)
        }

    async def _resolve_token_decimals_single(self, token_address: str) -> int:
        try:
            result = await self._rpc_gateway_service.eth_call(token_address, DECIMALS_SELECTOR)
        except RpcError:
            return 18

        decimals = decode_uint_call_result(result)
        return 18 if decimals is None else decimals

    async def _resolve_token_symbols_batch(
        self,
        token_addresses: Sequence[str],
    ) -> dict[str, str | None]:
        results = await self._rpc_gateway_service.eth_call_many(
            [(token_address, SYMBOL_SELECTOR) for token_address in token_addresses]
        )
        return {
            token_address: self._decode_token_symbol_result(result)
            for token_address, result in zip(token_addresses, results, strict=False)
        }

    async def _resolve_token_symbol_single(self, token_address: str) -> str | None:
        try:
            result = await self._rpc_gateway_service.eth_call(token_address, SYMBOL_SELECTOR)
        except RpcError:
            return None

        return self._decode_token_symbol_result(result)

    @staticmethod
    def _decode_token_symbol_result(call_result: str | None) -> str | None:
        return (
            decode_string_call_result(call_result)
            or decode_bytes32_string_call_result(call_result)
        )
