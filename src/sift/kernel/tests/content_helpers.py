# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the content store's tests share: the fixture files, their digests, and placing a copy of one in a library."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# A fingerprint write records an event into the workbench's table, so its schema is registered.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content.identity import (
    ContentStore,
)
from sift.kernel.ingress import (
    Origin,
    verify_ingress,
)
from sift.testing.fixtures import LibraryRoot

FIXTURES = Path(__file__).parent / "fixtures" / "ingress"

# The digest of a checked-in file: if a dependency upgrade changed what BLAKE3 computes, every file
# would re-import as a new asset, leaving its tags and ratings behind.
GOLDEN = {
    "accepted.mp4": "abec64ccf0017b047942fd277b833cdb4c46b5a949023216eb37cf3e3b336792",
    "accepted.jpg": "40d6aaa9caa593d696d48e57bc5b352d0d1ee38b61648ba8bd8a363903fcb3ff",
    "accepted.gif": "ffbf8199fdb4003444f7cc8ddb71e022d045eb351ca7b8787955f2628cd08ba2",
}

#: The identity of the same files, and of a generated file above the sample budget so
#: the sampled path is pinned too. A change here is a migration of every library, not a bug.
GOLDEN_IDENTITY = {
    "accepted.mp4": "96838e0a6d71c0087296e220be95deda9c481cc366446d48c899de828561fe5d",
    "accepted.jpg": "1758e801e063a9b54b2484b00cd5349075da934524f63e2313dc23e465092bee",
    "accepted.gif": "cccf0371fd3c699918365fa69dbdaa5075e202337ea357e8cd25cc9b4b027a51",
    "eight-mebibytes.bin": "591574105bc6e8cbc42852cdacc2ea78fab5b666f76665d8cad1be16e27c702c",
}


def write_eight_mebibytes(target: Path) -> Path:
    """A deterministic 8 MiB file: a seeded 4 KiB pattern repeated, each block stamped with its
    own number so no two blocks are alike and a sample anywhere is distinct from any other."""
    import random

    block = bytes(random.Random(20260909).getrandbits(8) for _ in range(4096))
    body = bytearray(block * (8 * 1024 * 1024 // 4096))
    for at in range(0, len(body), 4096):
        body[at : at + 8] = (at // 4096).to_bytes(8, "little")
    target.write_bytes(bytes(body))
    return target


@pytest.fixture(scope="module", autouse=True)
def corpus_survives() -> Iterator[None]:
    """The fixtures are copied, never used in place. This says so if one ever is."""
    before = {path.name: path.read_bytes() for path in FIXTURES.iterdir()}
    yield
    after = {path.name: path.read_bytes() for path in FIXTURES.iterdir()}
    assert after == before, "a test used a fixture in place; copy it instead"


def place(name: str, root: LibraryRoot, rel_path: str) -> Path:
    """Put a copy of a fixture into a library root, creating the folders it needs."""
    target = root.path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name, target)
    return target


def checked(path: Path, settings: Any, *, origin: Origin = Origin.SCAN) -> Any:
    return verify_ingress(path, origin=origin, settings=settings)


def tree(directory: Path) -> dict[str, bytes]:
    """Every file under a directory, by relative path. Compared before and after to prove that
    indexing a library reads it and does nothing else."""
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


async def _make_legacy(store: ContentStore, asset_id: str, whole: str) -> None:
    """Turn a row into one written before the sample became the identity: the whole-file digest as its identity."""
    await store._db.execute(
        "UPDATE assets SET identity = ?, identity_version = 0, whole_digest = NULL WHERE id = ?",
        (whole, asset_id),
    )
    # The store remembers whether any such row exists, because every file taken in asks; a row
    # made old behind its back is the one write it cannot see.
    store._legacy_remaining = None
