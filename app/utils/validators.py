from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .evm import normalize_address, normalize_tx_hash


def normalize_required_address(value: Any) -> str:
    address = normalize_address(value)
    if address is None:
        raise ValueError("Invalid EVM address")
    return address


def normalize_optional_address(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return normalize_required_address(value)


def normalize_required_tx_hash(value: Any) -> str:
    tx_hash = normalize_tx_hash(value)
    if tx_hash is None:
        raise ValueError("Invalid transaction hash")
    return tx_hash


def normalize_address_list(values: Iterable[Any]) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]

    normalized = {
        address
        for value in values
        if (address := normalize_address(value)) is not None
    }
    return sorted(normalized)
