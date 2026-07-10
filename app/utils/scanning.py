from __future__ import annotations


def validate_block_window(*, start_block: int, end_block: int) -> tuple[int, int]:
    if start_block < 0:
        raise ValueError("start_block must be non-negative")
    if end_block < start_block:
        raise ValueError("end_block must be greater than or equal to start_block")

    return start_block, end_block
