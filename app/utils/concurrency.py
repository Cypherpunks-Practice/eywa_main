from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from .batching import iter_chunks


def threaded_resolve[InputT, KeyT, ValueT](
    values: Sequence[InputT],
    worker: Callable[[InputT], tuple[KeyT, ValueT]],
    *,
    max_workers: int,
) -> dict[KeyT, ValueT]:
    """Resolve values in a thread pool and collect keyed results."""

    if not values:
        return {}

    resolved: dict[KeyT, ValueT] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, value): value for value in values}
        for future in as_completed(futures):
            key, result = future.result()
            resolved[key] = result
    return resolved


async def async_resolve[InputT, KeyT, ValueT](
    values: Sequence[InputT],
    worker: Callable[[InputT], Awaitable[tuple[KeyT, ValueT]]],
    *,
    max_concurrency: int,
) -> dict[KeyT, ValueT]:
    """Resolve values concurrently with a semaphore."""

    if not values:
        return {}

    semaphore = asyncio.Semaphore(max_concurrency)

    async def bounded(value: InputT) -> tuple[KeyT, ValueT]:
        async with semaphore:
            return await worker(value)

    results = await asyncio.gather(*(bounded(v) for v in values))
    return dict(results)


async def batch_resolve[KeyT, ValueT](
    items: Sequence[KeyT],
    *,
    batch_resolver: Callable[[Sequence[KeyT]], Awaitable[dict[KeyT, ValueT]]] | None,
    individual_resolver: Callable[[KeyT], Awaitable[tuple[KeyT, ValueT]]],
    batch_size: int,
    max_concurrency: int,
    is_resolved: Callable[[ValueT], bool] = lambda v: v is not None,
) -> dict[KeyT, ValueT]:
    """Resolve items using batch-first with individual fallback.

    Tries batch resolution first (if batch_resolver is provided). Items that
    are missing from batch results or fail is_resolved are retried individually.
    """

    if not items:
        return {}

    resolved: dict[KeyT, ValueT] = {}
    unresolved = list(items)

    if batch_resolver is not None:
        unresolved = []
        for batch in iter_chunks(list(items), batch_size):
            batch_results = await batch_resolver(batch)
            resolved.update(batch_results)
            unresolved.extend(
                item for item in batch
                if item not in batch_results or not is_resolved(batch_results[item])
            )

    if unresolved:
        retry_results = await async_resolve(
            unresolved,
            individual_resolver,
            max_concurrency=max_concurrency,
        )
        resolved.update(retry_results)

    return resolved
