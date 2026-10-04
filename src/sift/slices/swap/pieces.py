# SPDX-License-Identifier: AGPL-3.0-or-later
"""The whole-file check at the end, for a receiver whose hello says `once`.

Checked first, every file is read twice: whole for its digest, then again to send it. To a receiver
that says `once`, a header names the file's version (`version_of`) instead, and once the receiver's
stream finds the file whole it asks `{"check": n}`. The sender answers `{"file": n, "pieces": hex}`
(or `"cannot"`): the BLAKE3 over each piece's own BLAKE3 in piece order, however the pieces arrived
across the streams. The receiver makes the same from its staged file and lands only on a match.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Iterable, Mapping
from pathlib import Path

from blake3 import blake3

from sift.slices.swap.device import Device
from sift.slices.swap.transfer import (
    CHUNK_SIZE,
    Prepared,
    chunk_count,
    chunk_digest,
    read_ready_chunk,
)

_VERSION_CONTEXT = b"sift-swap-version-1"


def pieces_digest(digests: Iterable[bytes]) -> str:
    """The digest over the pieces' own digests, in piece order, hex."""
    hasher = blake3()
    for one in digests:
        hasher.update(one)
    return hasher.hexdigest()


def staged_digests(path: Path, size: int) -> tuple[str, str]:
    """A staged file's whole BLAKE3 and its pieces' digest, from one read. Blocking."""
    whole, pieces = blake3(), blake3()
    if size:
        with path.open("rb") as handle:
            while block := handle.read(CHUNK_SIZE):
                whole.update(block)
                pieces.update(chunk_digest(block))
    return whole.hexdigest(), pieces.hexdigest()


def version_of(device: Device, key: str, stamp: tuple[int, int] | None) -> str:
    """The file's size and modification time signed by this device: one message's signature never
    changes, so a receiver matches it across sessions and cannot read the time out of it."""
    size, modified = stamp or (0, 0)
    message = _VERSION_CONTEXT + struct.pack(">Qq", size, modified) + key.encode("utf-8")
    return blake3(device.signer.sign(message)).hexdigest()


async def pieces_of(ready: Prepared, known: Mapping[int, bytes]) -> str:
    """The pieces' digest of a file being sent, reading any piece not `known` (`Changed`)."""
    digests = []
    for index in range(chunk_count(ready.size)):
        piece = known.get(index)
        if piece is None:
            data = await read_ready_chunk(ready, index)
            piece = await asyncio.to_thread(chunk_digest, data)
        digests.append(piece)
    return pieces_digest(digests)
