"""Small pure helpers shared by the store modules."""

from __future__ import annotations

import struct
import time
from collections.abc import Collection, Sequence
from typing import Any


def resolve_now(now: int | None) -> int:
    """``now`` when given (tests pass a fixed clock), else the wall clock."""
    return int(time.time()) if now is None else now


def pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def unpack_vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def placeholders(values: Collection[Any]) -> str:
    """``?,?,?`` for an ``IN (...)`` list of this many values."""
    return ",".join("?" * len(values))
