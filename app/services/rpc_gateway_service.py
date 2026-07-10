from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from aiohttp import ClientResponseError
from eth_typing import HexStr
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.types import RPCEndpoint

from ..core.config import Settings
from ..core.exceptions import RpcError, RpcTransientError
from ..schemas.scan import TraceAddressRole
from ..utils import align_batch_responses, provider_supports_batch_requests


class RpcGatewayService:
    def __init__(self, settings: Settings):
        self._settings = settings
        provider = AsyncHTTPProvider(self._settings.rpc_endpoint)
        self._web3 = AsyncWeb3(provider)

    async def close(self) -> None:
        await self._web3.provider.disconnect()

    @property
    def web3(self) -> AsyncWeb3:
        return self._web3

    @property
    def provider(self) -> object:
        return self._web3.provider

    def supports_batch_requests(self) -> bool:
        return provider_supports_batch_requests(self.provider)

    async def make_request(
        self,
        method: str,
        params: Sequence[Any],
        *,
        allow_error: bool = False,
    ) -> dict[str, Any]:
        retry_count = self._settings.rpc_retries
        for attempt in range(retry_count):
            try:
                response = await self._web3.provider.make_request(
                    RPCEndpoint(method),
                    list(params),
                )
                break
            except ClientResponseError as exc:
                if self._is_transient_http_status(exc.status):
                    if attempt < retry_count - 1:
                        await asyncio.sleep(self._get_retry_delay_seconds(exc, attempt))
                        continue
                    raise RpcTransientError(f"{method} request failed: HTTP {exc.status}") from exc
                raise RpcError(f"{method} request failed: HTTP {exc.status}") from exc
            except (OSError, TimeoutError) as exc:
                if attempt < retry_count - 1:
                    await asyncio.sleep(self._get_retry_delay_seconds(exc, attempt))
                    continue
                raise RpcTransientError(f"{method} request failed: {exc}") from exc

        if not isinstance(response, dict):
            raise RpcError(f"Unexpected RPC response type for {method}: {type(response).__name__}")

        if not allow_error and "error" in response:
            raise RpcError(f"{method} error: {response['error']}")

        return response

    async def make_batch_request(
        self,
        requests: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[Any | None]:
        if not self.supports_batch_requests():
            raise RpcError("Batch requests are not supported by the configured RPC provider")

        batch_requests = [(method, list(params)) for method, params in requests]
        return await self._make_batch_request_with_resilience(batch_requests)

    async def _make_batch_request_with_resilience(
        self,
        batch_requests: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[Any | None]:
        if not batch_requests:
            return []

        retry_count = self._settings.rpc_retries

        rpc_batch = [
            (RPCEndpoint(method), params)
            for method, params in batch_requests
        ]

        for attempt in range(retry_count):
            try:
                responses = await self._web3.provider.make_batch_request(rpc_batch)
            except ClientResponseError as exc:
                if not self._is_transient_http_status(exc.status):
                    raise RpcError(f"Batch RPC request failed: HTTP {exc.status}") from exc
                if attempt < retry_count - 1:
                    await asyncio.sleep(
                        self._get_retry_delay_seconds(exc, attempt),
                    )
                    continue
                break
            except (OSError, TimeoutError) as exc:
                if attempt < retry_count - 1:
                    await asyncio.sleep(
                        self._get_batch_retry_delay_seconds(exc, attempt),
                    )
                    continue
                break

            if not isinstance(responses, list):
                raise RpcError(
                    f"Unexpected batch RPC response type: {type(responses).__name__}"
                )
            return align_batch_responses(responses, len(batch_requests))

        if len(batch_requests) == 1:
            return [None]

        midpoint = len(batch_requests) // 2
        left_responses = await self._make_batch_request_with_resilience(
            batch_requests[:midpoint],
        )
        right_responses = await self._make_batch_request_with_resilience(
            batch_requests[midpoint:],
        )
        return [*left_responses, *right_responses]

    def _get_batch_retry_delay_seconds(
        self,
        exception: OSError | TimeoutError | ClientResponseError,
        attempt: int,
    ) -> float:
        retry_after_seconds = self._extract_retry_after_seconds(exception)
        if retry_after_seconds is not None:
            return retry_after_seconds
        return self._settings.rpc_retry_backoff_seconds * (2 ** attempt)

    def _get_retry_delay_seconds(
        self,
        exception: OSError | TimeoutError | ClientResponseError,
        attempt: int,
    ) -> float:
        retry_after_seconds = self._extract_retry_after_seconds(exception)
        if retry_after_seconds is not None:
            return retry_after_seconds
        return self._settings.rpc_retry_backoff_seconds * (2 ** attempt)

    @staticmethod
    def _extract_retry_after_seconds(
        exception: OSError | TimeoutError | ClientResponseError,
    ) -> float | None:
        headers = getattr(exception, "headers", None)
        if headers is None:
            response = getattr(exception, "response", None)
            if response is None:
                return None
            headers = getattr(response, "headers", None)
            if headers is None:
                return None

        retry_after = headers.get("Retry-After")
        if retry_after is None:
            return None

        try:
            retry_after_seconds = float(retry_after)
        except (TypeError, ValueError):
            return None

        return retry_after_seconds if retry_after_seconds > 0 else None

    @staticmethod
    def _is_transient_http_status(status: int) -> bool:
        return status in {408, 425, 429, 500, 502, 503, 504}

    async def trace_filter(
        self,
        *,
        from_block: int,
        to_block: int,
        addresses: Sequence[str],
        address_role: TraceAddressRole,
    ) -> list[dict[str, Any]]:
        if not addresses:
            return []

        params: dict[str, Any] = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        if address_role == TraceAddressRole.TO:
            params["toAddress"] = list(addresses)
        else:
            params["fromAddress"] = list(addresses)

        response = await self.make_request(
            "trace_filter",
            [params],
        )
        result = response.get("result", [])
        return result if isinstance(result, list) else []

    async def get_transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        try:
            response = await self.make_request(
                "eth_getTransactionReceipt",
                [HexStr(tx_hash)],
            )
        except (RpcError, ValueError):
            return None

        receipt = response.get("result")
        return dict(receipt) if receipt else None

    async def get_transaction_receipts_batch(
        self,
        tx_hashes: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        if not tx_hashes:
            return {}

        requests = [("eth_getTransactionReceipt", [tx_hash]) for tx_hash in tx_hashes]
        responses = await self.make_batch_request(requests)

        receipts: dict[str, dict[str, Any]] = {}
        for tx_hash, response in zip(tx_hashes, responses, strict=False):
            if not isinstance(response, dict) or "error" in response:
                continue
            receipt = response.get("result")
            if isinstance(receipt, dict):
                receipts[tx_hash] = receipt
        return receipts

    async def get_latest_block_number(self) -> int:
        response = await self.make_request("eth_blockNumber", [])
        result = response.get("result")
        if not isinstance(result, str):
            raise RpcError("eth_blockNumber returned an unexpected response payload")

        try:
            return int(result, 16)
        except ValueError as exc:
            raise RpcError(f"Invalid eth_blockNumber response: {result!r}") from exc

    async def eth_call(
        self, address: str, data: str, *, block: str = "latest",
    ) -> str | None:
        response = await self.make_request(
            "eth_call",
            [{"to": AsyncWeb3.to_checksum_address(address), "data": data}, block],
        )
        result = response.get("result")
        return result if isinstance(result, str) else None

    async def eth_call_many(
        self,
        calls: Sequence[tuple[str, str]],
        *,
        block: str = "latest",
    ) -> list[str | None]:
        if not calls:
            return []

        requests = [
            (
                "eth_call",
                [{"to": AsyncWeb3.to_checksum_address(address), "data": data}, block],
            )
            for address, data in calls
        ]
        responses = await self.make_batch_request(requests)

        results: list[str | None] = []
        for response in responses:
            if not isinstance(response, dict) or "error" in response:
                results.append(None)
                continue
            result = response.get("result")
            results.append(result if isinstance(result, str) else None)
        return results

    async def get_block(self, block_number: int) -> dict[str, Any] | None:
        try:
            response = await self.make_request(
                "eth_getBlockByNumber",
                [hex(block_number), False],
            )
        except (RpcError, ValueError):
            return None

        block = response.get("result")
        return dict(block) if block else None

    async def get_blocks_batch(
        self, block_numbers: Sequence[int],
    ) -> dict[int, dict[str, Any]]:
        if not block_numbers:
            return {}

        requests = [
            ("eth_getBlockByNumber", [hex(block_number), False])
            for block_number in block_numbers
        ]
        responses = await self.make_batch_request(requests)

        blocks: dict[int, dict[str, Any]] = {}
        for block_number, response in zip(block_numbers, responses, strict=False):
            if not isinstance(response, dict) or "error" in response:
                continue
            block = response.get("result")
            if isinstance(block, dict):
                blocks[block_number] = block
        return blocks

    async def trace_transaction(
        self, tx_hash: str, *, allow_error: bool = False,
    ) -> list[dict[str, Any]]:
        response = await self.make_request(
            "trace_transaction",
            [tx_hash],
            allow_error=allow_error,
        )
        result = response.get("result", [])
        return result if isinstance(result, list) else []
