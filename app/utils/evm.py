from __future__ import annotations

from typing import Any


def normalize_to_hex(data: Any, *, should_lower: bool = True) -> str | None:
    """Normalize bytes-like or string data to a hex string representation."""

    if data is None:
        return None

    raw = data.hex() if hasattr(data, "hex") else str(data)
    raw = raw.strip()
    if not raw:
        return None

    return raw.lower() if should_lower else raw


def clean_hex_prefix(data: str | None) -> str | None:
    """Remove the `0x` prefix from a hex string."""

    if data is None:
        return None

    result = data[2:] if data.startswith("0x") else data
    return result or None


def normalize_topic0(topic0: Any) -> str | None:
    """Convert topic0 to comparable lowercase hex without `0x`."""

    return clean_hex_prefix(normalize_to_hex(topic0))


def _normalize_fixed_hex(value: Any, *, expected_length: int) -> str | None:
    raw_value = normalize_to_hex(value)
    if raw_value is None:
        return None

    if not raw_value.startswith("0x"):
        raw_value = f"0x{raw_value}"
    if len(raw_value) != expected_length:
        return None

    try:
        int(raw_value[2:], 16)
    except ValueError:
        return None

    return raw_value


def normalize_tx_hash(tx_hash: Any) -> str | None:
    """Normalize a transaction hash to lowercase 0x-prefixed hex."""

    return _normalize_fixed_hex(tx_hash, expected_length=66)


def normalize_address(address: Any) -> str | None:
    """Normalize an EVM address to lowercase 0x-prefixed hex."""

    return _normalize_fixed_hex(address, expected_length=42)


def parse_int(value: Any) -> int | None:
    """Parse an int from decimal or hex RPC values."""

    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value

    text = normalize_to_hex(value)
    if text is None:
        return None

    try:
        return int(text, 16) if text.startswith("0x") else int(text)
    except ValueError:
        return None


def parse_data_words(data: Any) -> list[str]:
    """Split ABI-encoded payload into 32-byte words."""

    raw = clean_hex_prefix(normalize_to_hex(data, should_lower=False))
    if raw is None or len(raw) % 64 != 0:
        return []

    return [raw[index : index + 64] for index in range(0, len(raw), 64)]


def word_to_int(word: str, *, signed: bool = False) -> int:
    """Decode a 32-byte ABI word to an int."""

    value = int(word, 16)
    if signed and value >= (1 << 255):
        return value - (1 << 256)
    return value


def decode_address_call_result(call_result: str | None) -> str | None:
    """Decode an address from an `eth_call` result."""

    raw = clean_hex_prefix(call_result)
    if raw is None or len(raw) < 40:
        return None

    return normalize_address(f"0x{raw[-40:]}")


def decode_uint_call_result(call_result: str | None) -> int | None:
    """Decode an unsigned integer from an `eth_call` result."""

    raw = clean_hex_prefix(call_result)
    if raw is None:
        return None

    try:
        return int(raw, 16)
    except ValueError:
        return None


def _normalize_decoded_text(value: str) -> str | None:
    result = value.replace("\x00", "").strip()
    return result or None


def decode_string_call_result(call_result: str | None) -> str | None:
    """Decode a dynamic ABI string from an `eth_call` result."""

    raw = clean_hex_prefix(call_result)
    if raw is None or len(raw) < 128:
        return None

    try:
        offset = int(raw[:64], 16)
    except ValueError:
        return None

    if offset < 0:
        return None

    offset_start = offset * 2
    if len(raw) < offset_start + 64:
        return None

    try:
        string_length = int(raw[offset_start: offset_start + 64], 16)
    except ValueError:
        return None

    if string_length < 0:
        return None

    payload_start = offset_start + 64
    payload_end = payload_start + string_length * 2
    if payload_end > len(raw):
        return None

    try:
        payload = bytes.fromhex(raw[payload_start:payload_end]).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None

    return _normalize_decoded_text(payload)


def decode_bytes32_string_call_result(call_result: str | None) -> str | None:
    """Decode a bytes32-encoded text value from an `eth_call` result."""

    raw = clean_hex_prefix(call_result)
    if raw is None or len(raw) < 64:
        return None

    try:
        payload = bytes.fromhex(raw[:64]).split(b"\x00", 1)[0].decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None

    return _normalize_decoded_text(payload)
