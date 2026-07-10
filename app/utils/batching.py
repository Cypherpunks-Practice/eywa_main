from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Any


def iter_chunks[T](values: Sequence[T], chunk_size: int) -> Iterator[Sequence[T]]:
    """Yield fixed-size chunks from a sequence."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    for index in range(0, len(values), chunk_size):
        yield values[index: index + chunk_size]


def iter_block_chunks(start_block: int, end_block: int, chunk_size: int) -> Iterator[tuple[int, int]]:
    """Yield inclusive block ranges in fixed-size chunks."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if end_block < start_block:
        raise ValueError("end_block must be greater than or equal to start_block")

    current = start_block
    while current <= end_block:
        chunk_end = min(current + chunk_size - 1, end_block)
        yield current, chunk_end
        current = chunk_end + 1


def align_batch_responses(responses: Iterable[Any], expected_len: int) -> list[Any | None]:
    """
    Align JSON-RPC batch responses by their request id.

    If alignment is ambiguous or malformed, the caller gets `None` placeholders
    instead of potentially mismatched responses.
    """

    if expected_len <= 0:
        return []

    indexed: dict[int, Any] = {}
    response_ids: list[int] = []

    for response in responses:
        if not isinstance(response, dict):
            return [None] * expected_len

        response_id = response.get("id")
        if not isinstance(response_id, int):
            return [None] * expected_len
        if response_id in indexed:
            return [None] * expected_len

        indexed[response_id] = response
        response_ids.append(response_id)

    if not response_ids:
        return [None] * expected_len

    min_id = min(response_ids)
    max_id = max(response_ids)

    if len(response_ids) == expected_len and max_id - min_id + 1 == expected_len:
        return [indexed[min_id + offset] for offset in range(expected_len)]

    lower_bound = max_id - expected_len + 1
    upper_bound = min_id
    candidate_bases: list[int] = []

    for base_id in range(lower_bound, upper_bound + 1):
        if all(base_id <= response_id < base_id + expected_len for response_id in response_ids):
            candidate_bases.append(base_id)

    if len(candidate_bases) != 1:
        return [None] * expected_len

    aligned: list[Any | None] = [None] * expected_len
    base_id = candidate_bases[0]
    for response_id, response in indexed.items():
        aligned[response_id - base_id] = response
    return aligned
