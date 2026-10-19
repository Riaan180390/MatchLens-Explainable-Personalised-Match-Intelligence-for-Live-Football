"""Read/write match files and replay them at a chosen speed, as if live."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path

from .schema import Event


def write_jsonl(events: Iterable[Event], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(e.model_dump_json(exclude_none=True) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[Event]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield Event.model_validate_json(line)


def paced(events: Iterable[Event], speed: float) -> Iterator[Event]:
    """Yield events on the match clock divided by `speed` (speed <= 0 means as fast as possible)."""
    start_wall = time.monotonic()
    start_match: float | None = None
    for e in events:
        if speed > 0:
            start_match = e.elapsed_s if start_match is None else start_match
            wait = (e.elapsed_s - start_match) / speed - (time.monotonic() - start_wall)
            if wait > 0:
                time.sleep(wait)
        yield e


async def apaced(events: Iterable[Event], speed: float) -> AsyncIterator[Event]:
    start_wall = time.monotonic()
    start_match: float | None = None
    for e in events:
        if speed > 0:
            start_match = e.elapsed_s if start_match is None else start_match
            wait = (e.elapsed_s - start_match) / speed - (time.monotonic() - start_wall)
            if wait > 0:
                await asyncio.sleep(wait)
        yield e
