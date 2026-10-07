# SPDX-License-Identifier: AGPL-3.0-or-later
"""Content identity: the digest, the assets, the locations they sit in.

The tests that matter here are the ones about *sameness*. A file is the same file after it is
renamed, after it is moved to another disk, after the NAS holding it goes offline for a week and
comes back on a different mount point, and it is a different file the moment a byte of it
changes. Everything a user ever records about their library hangs off getting that right.

So the assertions are mostly about what did *not* happen: no second asset, no lost tag, no moved
file, no row written into a library nobody asked Sift to write into.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from blake3 import blake3 as real_blake3
from hypothesis import given
from hypothesis import strategies as st

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content import (
    hashing,
    identity_arrivals,
    identity_places,
    identity_store,
)
from sift.kernel.content.hashing import (
    IDENTITY_END_BYTES,
    IDENTITY_SAMPLES,
    IDENTITY_WHOLE_BELOW,
    FileStillChanging,
    hash_file,
    identity_file,
    identity_ranges,
)
from sift.kernel.content.identity import (
    ContentStore,
    DerivativeKind,
    LocationStatus,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import (
    ALLOWED_MEDIA,
    IngressResult,
    Kind,
    Origin,
)
from sift.kernel.tests.content_helpers import (
    FIXTURES,
    GOLDEN,
    GOLDEN_IDENTITY,
    _make_legacy,
    checked,
    corpus_survives,  # noqa: F401  (the corpus check, autouse)
    place,
    tree,
    write_eight_mebibytes,
)
from sift.testing.fixtures import FakeClock, LibraryRoot

# --- The digest ----------------------------------------------------------------------


@pytest.mark.regression
@pytest.mark.parametrize("name", sorted(GOLDEN))
async def test_the_digest_of_a_known_file_never_changes(
    name: str, tmp_path: Path, settings: Any
) -> None:
    """The alarm on the one failure that would not look like a failure."""
    target = tmp_path / name
    shutil.copy(FIXTURES / name, target)

    assert await hash_file(checked(target, settings)) == GOLDEN[name]


# --- The identity: a sample of the file ---------------------------------------


@pytest.mark.regression
@pytest.mark.parametrize("name", sorted(GOLDEN_IDENTITY))
async def test_the_identity_of_a_known_file_never_changes(
    name: str, tmp_path: Path, settings: Any
) -> None:
    """The same alarm as the digest's, for the layout of the sample as much as the arithmetic."""
    target = tmp_path / name
    if name == "eight-mebibytes.bin":
        write_eight_mebibytes(target)
        proof = _proof(target)
    else:
        shutil.copy(FIXTURES / name, target)
        proof = checked(target, settings)

    assert await identity_file(proof) == GOLDEN_IDENTITY[name]


async def test_an_identity_is_never_a_whole_file_digest(tmp_path: Path, settings: Any) -> None:
    """A file under the budget is read whole for both, and they must still differ: the identity
    is domain-separated, so a column of one can never be mistaken for a column of the other."""
    target = tmp_path / "accepted.mp4"
    shutil.copy(FIXTURES / "accepted.mp4", target)
    proof = checked(target, settings)
    assert await identity_file(proof) != await hash_file(proof)


async def test_a_big_file_is_identified_by_its_sample(tmp_path: Path) -> None:
    """What the sample does and does not see, stated rather than left implied.

    A byte changed inside a sampled range changes the identity; a byte changed between two
    samples does not, which is the price of sampling; a byte appended changes it, because the
    size is in the digest.
    """
    target = write_eight_mebibytes(tmp_path / "big.bin")
    original = await identity_file(_proof(target))
    ranges = identity_ranges(target.stat().st_size)
    assert len(ranges) == IDENTITY_SAMPLES + 2

    body = bytearray(target.read_bytes())
    inside = ranges[3][0] + 17
    body[inside] ^= 0xFF
    target.write_bytes(bytes(body))
    assert await identity_file(_proof(target)) != original, (
        "a sampled byte changed and it saw nothing"
    )

    body[inside] ^= 0xFF
    between = ranges[3][0] + ranges[3][1] + 4096
    assert between < ranges[4][0], "the probe byte must sit between two samples"
    body[between] ^= 0xFF
    target.write_bytes(bytes(body))
    assert await identity_file(_proof(target)) == original, "an unsampled byte is not part of it"

    target.write_bytes(bytes(body) + b"\0")
    assert await identity_file(_proof(target)) != original, "a longer file is a different file"


async def test_a_file_that_grew_since_its_identity_was_checked_is_refused(tmp_path: Path) -> None:
    target = write_eight_mebibytes(tmp_path / "big.bin")
    proof = _proof(target)
    with target.open("ab") as handle:
        handle.write(b"more")
    with pytest.raises(FileStillChanging):
        await identity_file(proof)


@pytest.mark.regression
async def test_an_identity_read_of_a_file_still_being_written_is_refused(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sample is refused a moving file the way the whole-file digest is. The size was right
    when the read started and the writer kept going, so only the second stat catches it."""
    target = tmp_path / "download.mp4"
    shutil.copy(FIXTURES / "accepted.mp4", target)
    proof = checked(target, settings)

    class StillWriting:
        def __init__(self, **kwargs: Any) -> None:
            self._inner = real_blake3(**kwargs)

        def update(self, chunk: bytes) -> None:
            with target.open("ab") as handle:
                handle.write(b"more")
            self._inner.update(chunk)

        def hexdigest(self) -> str:
            return str(self._inner.hexdigest())

    monkeypatch.setattr("sift.kernel.content.hashing.blake3", StillWriting)

    with pytest.raises(FileStillChanging):
        await identity_file(proof)


@pytest.mark.regression
async def test_an_identity_read_that_comes_up_short_is_refused(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each range is read exactly, so a file that shrank under the read hands back fewer bytes
    than the range asked for. That is refused rather than digested as a prefix, and the check on
    the size alone cannot see it: the file was the right length when the read started."""
    monkeypatch.setattr(hashing, "CHUNK_BYTES", 64)

    target = tmp_path / "replaced.mp4"
    target.write_bytes((FIXTURES / "accepted.mp4").read_bytes() + bytes(200_000))
    proof = checked(target, settings)

    class TruncatesTheFile:
        def __init__(self, **kwargs: Any) -> None:
            self._inner = real_blake3(**kwargs)

        def update(self, chunk: bytes) -> None:
            with target.open("r+b") as handle:
                handle.truncate(100)
            self._inner.update(chunk)

        def hexdigest(self) -> str:
            return str(self._inner.hexdigest())

    monkeypatch.setattr("sift.kernel.content.hashing.blake3", TruncatesTheFile)

    with pytest.raises(FileStillChanging):
        await identity_file(proof)


@given(size=st.integers(min_value=0, max_value=1 << 40))
def test_identity_ranges_cover_a_file_in_order_and_within_it(size: int) -> None:
    ranges = identity_ranges(size)
    assert ranges == sorted(ranges)
    assert all(at >= 0 and at + length <= size for at, length in ranges)
    if size <= IDENTITY_WHOLE_BELOW:
        assert ranges == [(0, size)]
    else:
        assert len(ranges) == IDENTITY_SAMPLES + 2
        assert ranges[0] == (0, IDENTITY_END_BYTES)
        assert ranges[-1] == (size - IDENTITY_END_BYTES, IDENTITY_END_BYTES)
        assert ranges[1][0] == IDENTITY_END_BYTES
        assert ranges[-2][0] + ranges[-2][1] == size - IDENTITY_END_BYTES


def _proof(target: Path) -> Any:
    """An ingress proof for a file that is not media: the identity reads bytes, not pictures."""
    from types import SimpleNamespace

    return SimpleNamespace(
        path=target, size=target.stat().st_size, media=SimpleNamespace(name="bin")
    )


async def test_a_fresh_library_never_digests_a_file_twice(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole-file read is paid only while a row from before the sample remains."""
    target = place("accepted.mp4", library_root, "clip.mp4")

    async def never(_checked: Any) -> str:
        raise AssertionError("the whole file was read on a library with nothing to bring forward")

    monkeypatch.setattr(identity_arrivals, "hash_file", never)
    taken = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    assert taken.asset_is_new
    assert taken.asset.identity_version == 1 and taken.asset.whole_digest is None
    assert not await content_store.legacy_identities_remain()


async def test_a_file_no_old_row_shares_a_size_with_is_not_read_twice(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One old row whose copies cannot be read stays waiting, and would have every file taken in
    read whole for as long as it stood, a second full read from a network share per file. Bytes of
    another length cannot have that row's digest, so a file of another size is read once."""
    photo = place("accepted.jpg", library_root, "photo.jpg")
    old = await content_store.ingest(
        checked(photo, settings), root_id=library_root.id, rel_path="photo.jpg"
    )
    await _make_legacy(content_store, old.asset.id, await hash_file(checked(photo, settings)))
    assert await content_store.legacy_identities_remain()

    clip = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(clip, settings)
    assert proof.size != old.asset.size_bytes

    async def never(_checked: Any) -> str:
        raise AssertionError("a file no waiting row could be was read whole")

    monkeypatch.setattr(identity_arrivals, "hash_file", never)
    taken = await content_store.ingest(proof, root_id=library_root.id, rel_path="clip.mp4")
    assert taken.asset_is_new and taken.asset.id != old.asset.id
    assert await content_store.legacy_identities_remain(), "the old row still waits for the pass"


async def test_an_old_row_with_no_size_could_be_any_file(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row with no size recorded cannot be ruled out by one, so a copy of its bytes is still
    found whatever size it arrives at."""
    first = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(first, settings)
    taken = await content_store.ingest(proof, root_id=library_root.id, rel_path="clip.mp4")
    await _make_legacy(content_store, taken.asset.id, await hash_file(proof))
    await content_store._db.execute(
        "UPDATE assets SET size_bytes = NULL WHERE id = ?", (taken.asset.id,)
    )

    again = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "again.mp4"), settings),
        root_id=library_root.id,
        rel_path="again.mp4",
    )
    assert again.asset.id == taken.asset.id and not again.asset_is_new
    assert again.asset.identity_version == 1


async def test_a_copy_of_a_file_from_before_the_sample_finds_its_asset(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The one case the two generations meet: an old row, a new copy of its bytes arriving."""
    first = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(first, settings)
    taken = await content_store.ingest(proof, root_id=library_root.id, rel_path="clip.mp4")
    whole = await hash_file(proof)
    await _make_legacy(content_store, taken.asset.id, whole)
    assert await content_store.legacy_identities_remain()

    second = place("accepted.mp4", library_root, "again.mp4")
    again = await content_store.ingest(
        checked(second, settings), root_id=library_root.id, rel_path="again.mp4"
    )

    assert again.asset.id == taken.asset.id and not again.asset_is_new
    assert again.asset.identity_version == 1
    assert again.asset.identity == GOLDEN_IDENTITY["accepted.mp4"]
    assert again.asset.whole_digest == whole, "the digest that cost hours is kept, not dropped"
    assert not await content_store.legacy_identities_remain()


async def test_the_pass_brings_a_row_forward_once(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(target, settings)
    taken = await content_store.ingest(proof, root_id=library_root.id, rel_path="clip.mp4")
    await _make_legacy(content_store, taken.asset.id, await hash_file(proof))
    assert await content_store.legacy_identity_count() == 1
    assert await content_store.legacy_identity_page(10) == [taken.asset.id]

    assert await content_store.adopt_identity(taken.asset.id, GOLDEN_IDENTITY["accepted.mp4"])
    brought = await content_store.get(taken.asset.id)
    assert brought is not None and brought.identity_version == 1
    assert brought.identity == GOLDEN_IDENTITY["accepted.mp4"]
    assert await content_store.legacy_identity_count() == 0
    # A second time is not an error and not a change: the WHERE on the version sees to it.
    assert not await content_store.adopt_identity(taken.asset.id, GOLDEN_IDENTITY["accepted.mp4"])


async def test_an_identity_already_held_elsewhere_leaves_the_old_row_where_it_is(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The constructed collision, or a file imported fresh while its old row had no copy: the
    old row is left at version 0 and counted, never merged into the other."""
    clip = place("accepted.mp4", library_root, "clip.mp4")
    fresh = await content_store.ingest(
        checked(clip, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    photo = place("accepted.jpg", library_root, "photo.jpg")
    old = await content_store.ingest(
        checked(photo, settings), root_id=library_root.id, rel_path="photo.jpg"
    )
    await _make_legacy(content_store, old.asset.id, "an-old-whole-file-digest")

    assert not await content_store.adopt_identity(old.asset.id, fresh.asset.identity)
    left = await content_store.get(old.asset.id)
    assert left is not None and left.identity_version == 0
    assert await content_store.legacy_identity_count() == 1


async def test_a_whole_file_digest_no_old_row_was_written_with_is_an_ordinary_import(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An arriving file the size of an old row is digested both ways, and it need not be the old
    row's bytes: nothing is adopted and the import goes on exactly as it would have."""
    photo = place("accepted.jpg", library_root, "photo.jpg")
    old = await content_store.ingest(
        checked(photo, settings), root_id=library_root.id, rel_path="photo.jpg"
    )
    await _make_legacy(content_store, old.asset.id, "the-whole-file-digest-of-the-photo")

    clip = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(clip, settings)
    await content_store._db.execute(
        "UPDATE assets SET size_bytes = ? WHERE id = ?", (proof.size, old.asset.id)
    )
    read_whole: list[int] = []
    real_hash_file = hash_file

    async def counted(one: Any) -> str:
        read_whole.append(one.size)
        return await real_hash_file(one)

    monkeypatch.setattr(identity_arrivals, "hash_file", counted)
    fresh = await content_store.ingest(proof, root_id=library_root.id, rel_path="clip.mp4")
    assert read_whole == [proof.size], "a file the size of an old row was not read whole"

    assert fresh.asset_is_new and fresh.asset.identity_version == 1
    assert fresh.asset.id != old.asset.id
    left = await content_store.get(old.asset.id)
    assert left is not None and left.identity_version == 0, "an unrelated row was brought forward"
    assert await content_store.legacy_identities_remain()


async def test_a_copy_for_an_old_row_whose_identity_is_already_held_joins_the_newer_row(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The collision met at import rather than by the pass: the same file was imported fresh while
    its old row had no readable copy, so its sampled identity is already on the newer row. The
    arriving copy belongs with that row; the old one is left at version 0 for the pass to report."""
    clip = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(clip, settings)
    fresh = await content_store.ingest(proof, root_id=library_root.id, rel_path="clip.mp4")
    photo = place("accepted.jpg", library_root, "photo.jpg")
    old = await content_store.ingest(
        checked(photo, settings), root_id=library_root.id, rel_path="photo.jpg"
    )
    await _make_legacy(content_store, old.asset.id, await hash_file(proof))
    # The old row was written for these bytes, so it has their size, which is what has the
    # arriving copy read whole and the collision met here rather than skipped by the size.
    await content_store._db.execute(
        "UPDATE assets SET size_bytes = ? WHERE id = ?", (proof.size, old.asset.id)
    )

    again = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "again.mp4"), settings),
        root_id=library_root.id,
        rel_path="again.mp4",
    )

    assert again.asset.id == fresh.asset.id and not again.asset_is_new
    assert {one.rel_path for one in await content_store.locations(fresh.asset.id)} == {
        "clip.mp4",
        "again.mp4",
    }
    left = await content_store.get(old.asset.id)
    assert left is not None and left.identity_version == 0
    assert await content_store.legacy_identities_remain(), "the old row still waits for the pass"


async def test_the_same_bytes_hash_the_same_wherever_they_are(
    tmp_path: Path, settings: Any
) -> None:
    """The whole model rests on this: the digest cannot depend on the path."""
    here = tmp_path / "here" / "clip.mp4"
    there = tmp_path / "elsewhere" / "renamed.mp4"
    for target in (here, there):
        target.parent.mkdir(parents=True)
        shutil.copy(FIXTURES / "accepted.mp4", target)

    assert await hash_file(checked(here, settings)) == await hash_file(checked(there, settings))


async def test_different_bytes_hash_differently(tmp_path: Path, settings: Any) -> None:
    one = tmp_path / "one.mp4"
    two = tmp_path / "two.jpg"
    shutil.copy(FIXTURES / "accepted.mp4", one)
    shutil.copy(FIXTURES / "accepted.jpg", two)

    assert await hash_file(checked(one, settings)) != await hash_file(checked(two, settings))


async def test_a_file_bigger_than_one_chunk_hashes_correctly(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read loop runs more than once. A hasher fed only its first chunk would pass every
    other test in this file, because every fixture fits in one."""
    monkeypatch.setattr(hashing, "CHUNK_BYTES", 64)

    target = tmp_path / "big.mp4"
    shutil.copy(FIXTURES / "accepted.mp4", target)

    assert await hash_file(checked(target, settings)) == GOLDEN["accepted.mp4"]


# --- The half-file guard -------------------------------------------------------------


@pytest.mark.regression
async def test_a_file_that_grew_since_it_was_checked_is_refused(
    tmp_path: Path, settings: Any
) -> None:
    """A download still in flight. Hashing it would mint an asset for a file that does not exist
    yet, and the digest would be wrong the moment the writer finished."""
    target = tmp_path / "download.mp4"
    shutil.copy(FIXTURES / "accepted.mp4", target)
    proof = checked(target, settings)

    with target.open("ab") as handle:
        handle.write(b"the rest of the file")

    with pytest.raises(FileStillChanging):
        await hash_file(proof)


@pytest.mark.regression
async def test_a_file_still_being_written_while_it_is_read_is_refused(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same thing, but the writer is still going *during* the read, so the size was right
    when the read started and wrong by the time it ended. Only the second stat catches this."""
    target = tmp_path / "download.mp4"
    shutil.copy(FIXTURES / "accepted.mp4", target)
    proof = checked(target, settings)

    class StillWriting:
        """A hasher that appends to the file as it is being fed it."""

        def __init__(self, **kwargs: Any) -> None:
            self._inner = real_blake3(**kwargs)

        def update(self, chunk: bytes) -> None:
            with target.open("ab") as handle:
                handle.write(b"more")
            self._inner.update(chunk)

        def hexdigest(self) -> str:
            return str(self._inner.hexdigest())

    monkeypatch.setattr("sift.kernel.content.hashing.blake3", StillWriting)

    with pytest.raises(FileStillChanging):
        await hash_file(proof)


@pytest.mark.regression
async def test_a_file_truncated_while_it_is_read_is_refused(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file replaced underneath a running scan. It ends early, so the digest is of a prefix,
    and a prefix hashes to something perfectly valid that means nothing.

    The file has to be bigger than the reader's own buffer for the read to actually come up
    short. Below that, Python has the whole file in memory after the first read and hands out
    the bytes of a file that is no longer there, which is a different and much quieter bug.
    """
    monkeypatch.setattr(hashing, "CHUNK_BYTES", 64)

    target = tmp_path / "replaced.mp4"
    target.write_bytes((FIXTURES / "accepted.mp4").read_bytes() + bytes(200_000))
    proof = checked(target, settings)

    class TruncatesTheFile:
        def __init__(self, **kwargs: Any) -> None:
            self._inner = real_blake3(**kwargs)

        def update(self, chunk: bytes) -> None:
            with target.open("r+b") as handle:
                handle.truncate(100)
            self._inner.update(chunk)

        def hexdigest(self) -> str:
            return str(self._inner.hexdigest())

    monkeypatch.setattr("sift.kernel.content.hashing.blake3", TruncatesTheFile)

    with pytest.raises(FileStillChanging):
        await hash_file(proof)


@pytest.mark.regression
async def test_a_file_rewritten_in_place_is_refused(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rewritten with the same number of bytes, so it is exactly as long as it was and exactly as
    long as was read. Nothing about the size says anything is wrong, and the digest is of a mixture
    of the old file and the new one, which is a file that has never existed.

    Only the modification time catches this, which is why it is compared in nanoseconds and why
    this test exists separately from the ones about a file changing length.
    """
    monkeypatch.setattr(hashing, "CHUNK_BYTES", 64)

    target = tmp_path / "rewritten.mp4"
    target.write_bytes((FIXTURES / "accepted.mp4").read_bytes() + bytes(200_000))
    proof = checked(target, settings)
    original = target.stat()

    class RewritesTheFile:
        def __init__(self, **kwargs: Any) -> None:
            self._inner = real_blake3(**kwargs)
            self._done = False

        def update(self, chunk: bytes) -> None:
            if not self._done:
                self._done = True
                with target.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(b"\xff" * 512)
                # Set explicitly rather than left to the filesystem: the check is in nanoseconds,
                # and a test that depends on the clock ticking between two writes is a test that
                # fails on somebody else's machine.
                os.utime(
                    target,
                    ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000),
                )
            self._inner.update(chunk)

        def hexdigest(self) -> str:
            return str(self._inner.hexdigest())

    monkeypatch.setattr("sift.kernel.content.hashing.blake3", RewritesTheFile)

    assert target.stat().st_size == original.st_size
    with pytest.raises(FileStillChanging):
        await hash_file(proof)


@pytest.mark.regression
async def test_a_read_that_quietly_comes_up_short_is_refused(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A network share can end a read early and report no error at all: the file is still there,
    still the right size, still the same modification time, and Sift has only some of it.

    Neither stat notices. Counting the bytes is the only thing that does.
    """
    target = tmp_path / "nas.mp4"
    target.write_bytes((FIXTURES / "accepted.mp4").read_bytes() + bytes(200_000))
    proof = checked(target, settings)

    real_fdopen = os.fdopen

    class GivesUpEarly:
        """A handle that stops returning bytes partway through, and says nothing about it.

        It has to cap the chunk it returns, not just refuse a later call: the reader asks for the
        whole file in one go, so a wrapper that only starts refusing on the second call hands over
        every byte on the first and simulates nothing.
        """

        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self._served = 0

        def read(self, size: int = -1) -> bytes:
            remaining = 4096 - self._served
            if remaining <= 0:
                return b""
            wanted = remaining if size < 0 else min(size, remaining)
            chunk: bytes = self._inner.read(wanted)
            self._served += len(chunk)
            return chunk

        def __enter__(self) -> GivesUpEarly:
            return self

        def __exit__(self, *exc: Any) -> None:
            self._inner.close()

    def short_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        return GivesUpEarly(real_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr("sift.kernel.content.hashing.os.fdopen", short_fdopen)

    with pytest.raises(FileStillChanging):
        await hash_file(proof)

    assert target.stat().st_size == proof.size


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "a named pipe cannot be put in a folder on Windows: its named pipes live under \\\\.\\pipe\\ and are not reachable as a path inside a media folder, so the hazard this guards against does not exist there. See kernel/paths.O_NONBLOCK."
    ),
)
async def test_a_named_pipe_is_not_read(tmp_path: Path) -> None:
    """A library is a directory somebody else assembled, and it can have anything in it. Reading
    a named pipe blocks until something writes to it, and nothing will, so the worker would sit
    there until Sift was restarted, and the queue behind it would never move again."""
    pipe = tmp_path / "clip.mp4"
    os.mkfifo(pipe)  # type: ignore[attr-defined, unused-ignore]

    # Built by hand: the gate would block on this too, and what is under test is that nothing
    # reads the whole of it even if one somehow arrives.
    proof = IngressResult(path=pipe, media=ALLOWED_MEDIA[0], size=0, origin=Origin.SCAN)

    with pytest.raises(FileStillChanging, match="regular file"):
        await hash_file(proof)


async def test_a_settled_file_is_not_refused(tmp_path: Path, settings: Any) -> None:
    """The other half of the guard: it must not refuse the ordinary case, or every import fails."""
    target = tmp_path / "settled.mp4"
    shutil.copy(FIXTURES / "accepted.mp4", target)

    assert await hash_file(checked(target, settings)) == GOLDEN["accepted.mp4"]


# --- Identity ------------------------------------------------------------------------


async def test_the_same_bytes_twice_are_one_asset_in_two_places(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The central rule. Importing a copy adds a location; it never adds a second asset."""
    first = place("accepted.mp4", library_root, "clips/holiday.mp4")
    second = place("accepted.mp4", library_root, "backup/holiday copy.mp4")

    one = await content_store.ingest(
        checked(first, settings), root_id=library_root.id, rel_path="clips/holiday.mp4"
    )
    two = await content_store.ingest(
        checked(second, settings), root_id=library_root.id, rel_path="backup/holiday copy.mp4"
    )

    assert one.asset.id == two.asset.id
    assert one.asset_is_new is True
    assert two.asset_is_new is False

    locations = await content_store.locations(one.asset.id)
    assert [location.rel_path for location in locations] == [
        "clips/holiday.mp4",
        "backup/holiday copy.mp4",
    ]
    assert {location.status for location in locations} == {LocationStatus.PRESENT}


async def test_the_same_content_in_two_roots_is_one_asset(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """One tile in the grid, two places on disk. This is the row shape the access rules are
    resolved over: an asset is only as visible as the least visible place it sits."""
    other_id = new_id()
    other_path = library_root.path.parent / "second-library"
    other_path.mkdir()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (other_id, "second", str(other_path), 1_700_000_000),
    )

    here = place("accepted.mp4", library_root, "clip.mp4")
    there = other_path / "clip.mp4"
    shutil.copy(FIXTURES / "accepted.mp4", there)

    one = await content_store.ingest(
        checked(here, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    two = await content_store.ingest(
        checked(there, settings), root_id=other_id, rel_path="clip.mp4"
    )

    assert one.asset.id == two.asset.id
    locations = await content_store.locations(one.asset.id)
    assert {location.root_id for location in locations} == {library_root.id, other_id}


@pytest.mark.integration
async def test_a_renamed_file_keeps_its_identity_and_its_tags(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The point of the whole design. Rename a file and the tags follow the content.

    Asserting that the *asset id* survived proves nothing on its own. What a user loses when
    identity breaks is everything hanging off it, so this hangs something off it first.
    """
    tag_id = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag_id, "summer", 1)
    )

    original = place("accepted.mp4", library_root, "clips/holiday.mp4")
    first = await content_store.ingest(
        checked(original, settings), root_id=library_root.id, rel_path="clips/holiday.mp4"
    )
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (first.asset.id, tag_id)
    )

    # The user renames it and moves it to another folder.
    renamed = library_root.path / "archive" / "2019 holiday.mp4"
    renamed.parent.mkdir()
    original.rename(renamed)

    # The scan finds it in its new place, and finds the old one gone.
    second = await content_store.ingest(
        checked(renamed, settings), root_id=library_root.id, rel_path="archive/2019 holiday.mp4"
    )
    await content_store.mark_missing(first.location.id)

    assert second.asset.id == first.asset.id
    assert second.asset_is_new is False

    tags = await temp_db.fetch_all(
        "SELECT t.name FROM asset_tags a JOIN tags t ON t.id = a.tag_id WHERE a.asset_id = ?",
        (first.asset.id,),
    )
    assert [row["name"] for row in tags] == ["summer"]

    by_status = {
        location.rel_path: location.status
        for location in await content_store.locations(first.asset.id)
    }
    assert by_status == {
        "clips/holiday.mp4": LocationStatus.MISSING,
        "archive/2019 holiday.mp4": LocationStatus.PRESENT,
    }


@pytest.mark.integration
async def test_a_vanished_file_keeps_its_asset_and_reconnects_elsewhere(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A NAS going offline is a non-event, and a NAS coming back on a different mount point is
    the same non-event. The asset is the content; the mount is a location."""
    original = place("accepted.mp4", library_root, "nas/clip.mp4")
    first = await content_store.ingest(
        checked(original, settings), root_id=library_root.id, rel_path="nas/clip.mp4"
    )

    original.unlink()
    assert await content_store.mark_missing(first.location.id) is True

    # It actually says so. Asserting only that the call returned True proves the row was found,
    # not that it was changed, and those are different things.
    gone = await content_store.locations(first.asset.id)
    assert [location.status for location in gone] == [LocationStatus.MISSING]

    # The asset survives its only location vanishing.
    assert await content_store.get(first.asset.id) is not None

    # The same bytes turn up in a different root.
    other_id = new_id()
    other_path = library_root.path.parent / "remounted"
    other_path.mkdir()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (other_id, "remounted", str(other_path), 1_700_000_000),
    )
    reappeared = other_path / "clip.mp4"
    shutil.copy(FIXTURES / "accepted.mp4", reappeared)

    second = await content_store.ingest(
        checked(reappeared, settings), root_id=other_id, rel_path="clip.mp4"
    )
    assert second.asset.id == first.asset.id


async def test_marking_a_location_missing_twice_is_not_an_error(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A scan that finds the same file gone on two consecutive passes has found nothing new."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    assert await content_store.mark_missing(result.location.id) is True
    assert await content_store.mark_missing(result.location.id) is False
    assert await content_store.mark_missing(new_id()) is False


async def test_removing_a_location_leaves_the_asset_and_everything_else_alone(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Losing one of two copies changes nothing about what the file is.

    This is the subtle half of deleting, and it is where a naive implementation destroys somebody's
    metadata: the asset is the content, the content is still on the disk somewhere else, and
    everything anyone ever recorded about it hangs off the row that must not go.
    """
    first = place("accepted.mp4", library_root, "one.mp4")
    second = place("accepted.mp4", library_root, "two.mp4")
    kept = await content_store.ingest(
        checked(first, settings), root_id=library_root.id, rel_path="one.mp4"
    )
    await content_store.ingest(
        checked(second, settings), root_id=library_root.id, rel_path="two.mp4"
    )
    # The same bytes twice, so this is one asset with two places to be.
    assert len(await content_store.locations(kept.asset.id)) == 2

    assert await content_store.remove_location(kept.location.id) is True

    assert await content_store.get(kept.asset.id) is not None
    assert [location.rel_path for location in await content_store.locations(kept.asset.id)] == [
        "two.mp4"
    ]
    # And the asset is not swept up by the emptiness check, because it is not empty.
    assert await content_store.remove_asset_if_unplaced(kept.asset.id) is False


async def test_removing_a_location_that_is_not_there_is_not_an_error(
    content_store: ContentStore,
) -> None:
    """Two things asking for the same file to be forgotten is a race with a correct outcome."""
    assert await content_store.remove_location(new_id()) is False


async def test_forgetting_a_selection_of_copies_ends_only_the_files_with_none_left(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bulk form of the two single calls, written once: the copies go, a file left with no
    copy ends and takes its pictures with it, and a file with a copy left keeps everything
    recorded about it. One announcement for the whole selection, and none for one that touched
    nothing."""
    heard: list[object] = []

    async def hear(connection: object) -> None:
        heard.append(connection)

    for module in (identity_arrivals, identity_places, identity_store):
        monkeypatch.setattr(module, "announce_arrival", hear)
    twice = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )
    other_copy = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "two.mp4"), settings),
        root_id=library_root.id,
        rel_path="two.mp4",
    )
    once = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "photo.jpg"), settings),
        root_id=library_root.id,
        rel_path="photo.jpg",
    )
    picture = await content_store.add_derivative(
        once.asset.id, DerivativeKind.THUMB, extension="jpg"
    )
    assert other_copy.asset.id == twice.asset.id
    heard.clear()

    assert await content_store.forget_locations([]) == []
    assert await content_store.forget_locations([new_id()]) == []
    assert heard == [], "a selection that touched nothing is not news"

    ended = await content_store.forget_locations(
        [twice.location.id, once.location.id, twice.location.id, new_id()]
    )

    assert ended == [once.asset.id]
    assert len(heard) == 1, "one announcement for the whole selection"
    assert await content_store.get(once.asset.id) is None
    assert picture.rel_cache_path not in await content_store.derivative_paths()
    assert await content_store.get(twice.asset.id) is not None
    assert [one.rel_path for one in await content_store.locations(twice.asset.id)] == ["two.mp4"]


async def test_where_a_page_of_files_sits_and_their_pictures_come_back_in_one_read(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """What a delete over a selection reads before it touches a disk: every copy and every picture
    of every file asked about, keyed by file, with a file that has none of either simply absent
    and an id named twice read once."""
    twice = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )
    await content_store.ingest(
        checked(place("accepted.mp4", library_root, "two.mp4"), settings),
        root_id=library_root.id,
        rel_path="two.mp4",
    )
    once = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "photo.jpg"), settings),
        root_id=library_root.id,
        rel_path="photo.jpg",
    )
    thumb = await content_store.add_derivative(once.asset.id, DerivativeKind.THUMB, extension="jpg")
    bare, _ = await content_store.upsert_asset(
        digest="nowhere", media=ALLOWED_MEDIA[0], size_bytes=1
    )
    asked = [twice.asset.id, once.asset.id, twice.asset.id, bare.id, "nobody"]

    places = await content_store.locations_of(asked)
    pictures = await content_store.derivatives_of(asked)

    assert {
        asset_id: sorted(one.rel_path for one in found) for asset_id, found in places.items()
    } == {
        twice.asset.id: ["one.mp4", "two.mp4"],
        once.asset.id: ["photo.jpg"],
    }
    assert {asset_id: [one.id for one in found] for asset_id, found in pictures.items()} == {
        once.asset.id: [thumb.id]
    }


async def test_a_location_can_be_pointed_at_a_new_path(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The same row at a different address, which is what makes a rename keep its file's identity.

    The location id is unchanged, and so is the asset. A delete-and-reinsert would look the same
    from the outside and would take everything recorded against either id with it.
    """
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    moved = await content_store.relocate(
        result.location.id,
        root_id=library_root.id,
        rel_path="sorted/holiday.mp4",
        folder_id=None,
    )

    assert moved is not None
    assert moved.id == result.location.id
    assert moved.rel_path == "sorted/holiday.mp4"
    # The filename column follows the path rather than being left describing the old one.
    assert moved.filename == "holiday.mp4"
    assert moved.asset_id == result.asset.id
    asset = await content_store.get(result.asset.id)
    assert asset is not None
    assert asset.identity == result.asset.identity


async def test_relocating_brings_a_location_back_from_missing(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A scan marked it missing; it has just been found somewhere else in the same root.

    Left missing, a file that is demonstrably there would stay hidden until the next full scan.
    """
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    await content_store.mark_missing(result.location.id)

    moved = await content_store.relocate(
        result.location.id, root_id=library_root.id, rel_path="found.mp4", folder_id=None
    )

    assert moved is not None
    assert moved.status is LocationStatus.PRESENT


async def test_relocating_a_path_that_walks_out_of_its_root_is_refused(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The same check the way in gets. A row is only as safe as the last thing that wrote it."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    with pytest.raises(ValueError):
        await content_store.relocate(
            result.location.id, root_id=library_root.id, rel_path="../escape.mp4", folder_id=None
        )


async def test_relocating_a_location_that_is_not_there_says_so(
    content_store: ContentStore, library_root: LibraryRoot
) -> None:
    assert (
        await content_store.relocate(
            new_id(), root_id=library_root.id, rel_path="anywhere.mp4", folder_id=None
        )
        is None
    )


async def test_a_location_can_be_looked_up_by_its_id(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    found = await content_store.location(result.location.id)

    assert found is not None
    assert found.id == result.location.id
    assert found.rel_path == "clip.mp4"


async def test_looking_up_a_location_that_is_not_there_says_so(
    content_store: ContentStore,
) -> None:
    assert await content_store.location(new_id()) is None


async def test_the_last_location_going_is_what_ends_an_asset(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """An asset with nowhere left to be is over, and the check is what decides that.

    Deliberately two calls: removing the location does not remove the asset, and nothing works out
    that it was the last one except the call written to ask. Fusing the two would mean every caller
    that removes a location has to be right about which one was last.
    """
    target = place("accepted.mp4", library_root, "only.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="only.mp4"
    )

    await content_store.remove_location(result.location.id)
    assert await content_store.get(result.asset.id) is not None

    assert await content_store.remove_asset_if_unplaced(result.asset.id) is True
    assert await content_store.get(result.asset.id) is None

    # And again, on an id that now resolves to nothing.
    assert await content_store.remove_asset_if_unplaced(result.asset.id) is False


async def test_an_asset_with_no_location_is_reported_as_stranded(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """What removing a library folder leaves behind, and what the maintenance surface reads.

    Both halves are asserted. An asset that still sits somewhere must not be listed: offering it
    for removal would destroy the tags and ratings of a file that is right there.
    """
    placed = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "here.mp4"), settings),
        root_id=library_root.id,
        rel_path="here.mp4",
    )
    stranded = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "gone.jpg"), settings),
        root_id=library_root.id,
        rel_path="gone.jpg",
    )
    await content_store.remove_location(stranded.location.id)

    # Stranded some way other than a removed folder: no moment was kept, so it is offered immediately.
    ids = await content_store.stranded_asset_ids(int(time.time()))

    assert ids == [stranded.asset.id]
    assert placed.asset.id not in ids


async def test_a_record_whose_folder_just_went_is_left_alone_and_a_place_again_ends_the_strand(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The strand is dated, so the Maintenance card can leave a fresh one alone; and a location
    added back (the folder returned, or the bytes turned up elsewhere) ends it outright."""
    placed = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "back.jpg"), settings),
        root_id=library_root.id,
        rel_path="back.jpg",
    )
    await content_store.remove_location(placed.location.id)
    await content_store._db.execute(
        "UPDATE assets SET stranded_at = ? WHERE id = ?", (1_000, placed.asset.id)
    )

    assert await content_store.stranded_asset_ids(999) == []
    assert await content_store.stranded_asset_ids(1_000) == [placed.asset.id]

    again = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "back.jpg"), settings),
        root_id=library_root.id,
        rel_path="back.jpg",
    )
    assert again.asset.id == placed.asset.id
    stored = await content_store.get(placed.asset.id)
    assert stored is not None
    assert await content_store.stranded_asset_ids(int(time.time())) == []
    (row,) = await content_store._db.fetch_all(
        "SELECT stranded_at FROM assets WHERE id = ?", (placed.asset.id,)
    )
    assert row["stranded_at"] is None


async def test_the_paths_of_every_derivative_come_back_together(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The set a cache sweep checks each file against. One missing name is a file deleted while in
    use, so this answers for every asset in one go rather than per asset."""
    first = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )
    second = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "two.jpg"), settings),
        root_id=library_root.id,
        rel_path="two.jpg",
    )
    thumb = await content_store.add_derivative(
        first.asset.id, DerivativeKind.THUMB, extension="jpg"
    )
    preview = await content_store.add_derivative(
        second.asset.id, DerivativeKind.PREVIEW, extension="mp4"
    )

    assert await content_store.derivative_paths() == {
        thumb.rel_cache_path,
        preview.rel_cache_path,
    }


async def test_a_file_that_comes_back_is_present_again(
    temp_db: Database, library_root: LibraryRoot, settings: Any, fake_clock: FakeClock
) -> None:
    """A NAS that was offline for a week has not just appeared. `first_seen_at` records when Sift
    first saw that path, and coming back is not being discovered.

    On a fake clock, deliberately. Both scans would otherwise land in the same wall-clock second,
    and a test that cannot tell the two timestamps apart cannot tell whether one was overwritten.
    """
    store = ContentStore(temp_db, settings, clock=fake_clock.now)

    target = place("accepted.mp4", library_root, "clip.mp4")
    first = await store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    await store.mark_missing(first.location.id)

    fake_clock.advance(7 * 24 * 3600)
    second = await store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    assert second.location.id == first.location.id
    assert second.location.status is LocationStatus.PRESENT
    assert second.location.first_seen_at == first.location.first_seen_at
    assert second.location.last_seen_at == first.location.last_seen_at + 7 * 24 * 3600


async def test_a_missing_copy_found_again_unchanged_is_marked_present_without_a_read(
    temp_db: Database, library_root: LibraryRoot, settings: Any, fake_clock: FakeClock
) -> None:
    """What a scan does with a path it recorded, lost, and finds again with the same size and
    age: the row comes back to present and says when, and the file is not put through the gate
    or read. A row that was not missing is left alone and the call says so."""
    store = ContentStore(temp_db, settings, clock=fake_clock.now)
    target = place("accepted.mp4", library_root, "clip.mp4")
    taken = await store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    assert await store.mark_present(taken.location.id) is False, "it was never missing"
    assert await store.mark_missing(taken.location.id) is True
    fake_clock.advance(3600)

    assert await store.mark_present(taken.location.id) is True
    back = await store.location(taken.location.id)
    assert back is not None and back.status is LocationStatus.PRESENT
    assert back.last_seen_at == taken.location.last_seen_at + 3600
    assert back.first_seen_at == taken.location.first_seen_at
    assert await store.mark_present(taken.location.id) is False, "already present"
    assert await store.mark_present(new_id()) is False


async def test_replacing_the_file_at_a_path_repoints_it(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A path holds one file. Overwrite it with different content and the path belongs to the new
    asset: the old one keeps everything anyone recorded about it, wherever else it may sit.

    Both files are the same container family, because they have to be: swap an mp4 for a jpeg
    under an `.mp4` name and the ingress gate refuses it long before this code is reached, which
    is a different test and a correct answer to a different question.
    """
    target = place("accepted.mov", library_root, "clip.mov")
    first = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mov"
    )

    shutil.copy(FIXTURES / "accepted.mp4", target)
    second = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mov"
    )

    assert second.asset.id != first.asset.id
    assert second.location.id == first.location.id
    assert await content_store.locations(first.asset.id) == []
    assert await content_store.get(first.asset.id) is not None


async def test_a_second_copy_does_not_overwrite_what_is_known_about_an_asset(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Re-importing must be inert. An asset that has been probed and named must not have any of it
    undone by someone dragging in a second copy of the file."""
    first = place("accepted.mp4", library_root, "original.mp4")
    result = await content_store.ingest(
        checked(first, settings), root_id=library_root.id, rel_path="original.mp4"
    )
    await temp_db.execute(
        "UPDATE assets SET width = ?, height = ?, probed_at = ? WHERE id = ?",
        (1920, 1080, 1_700_000_500, result.asset.id),
    )

    second = place("accepted.mp4", library_root, "copy.mp4")
    await content_store.ingest(
        checked(second, settings), root_id=library_root.id, rel_path="copy.mp4"
    )

    asset = await content_store.get(result.asset.id)
    assert asset is not None
    assert (asset.width, asset.height) == (1920, 1080)
    assert asset.probed_at == 1_700_000_500
    assert asset.original_filename == "original.mp4"


async def test_an_asset_records_what_the_gate_decided(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The media type and mime come from the ingress gate, not from the file's extension, which
    is a claim, and not evidence."""
    target = place("accepted.gif", library_root, "misnamed.gif")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="misnamed.gif"
    )

    assert result.asset.media_type == Kind.GIF.value
    assert result.asset.mime == "image/gif"
    assert result.asset.size_bytes == (FIXTURES / "accepted.gif").stat().st_size
    assert result.asset.identity == GOLDEN_IDENTITY["accepted.gif"]
    assert result.location.filename == "misnamed.gif"
    assert result.location.mtime is not None


async def test_resolve_by_identity_finds_content_sift_has_seen(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    found = await content_store.resolve_by_identity(GOLDEN_IDENTITY["accepted.mp4"])
    assert found is not None
    assert found.id == result.asset.id

    assert await content_store.resolve_by_identity(GOLDEN_IDENTITY["accepted.jpg"]) is None
    assert await content_store.get(new_id()) is None


async def test_a_location_can_be_looked_up_by_its_path(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.mp4", library_root, "clips/holiday.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clips/holiday.mp4"
    )

    found = await content_store.location_at(library_root.id, "clips/holiday.mp4")
    assert found is not None
    assert found.id == result.location.id
    assert await content_store.location_at(library_root.id, "nothing/here.mp4") is None


async def test_the_primitives_work_on_their_own(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """`upsert_asset` and `add_location` are usable outside `ingest`, and each opens its own
    transaction when it is."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(target, settings)
    digest = await hash_file(proof)

    asset, created = await content_store.upsert_asset(
        digest=digest, media=proof.media, size_bytes=proof.size, original_filename="clip.mp4"
    )
    assert created is True

    again, created_again = await content_store.upsert_asset(
        digest=digest, media=proof.media, size_bytes=proof.size
    )
    assert created_again is False
    assert again.id == asset.id

    location = await content_store.add_location(
        asset_id=asset.id, root_id=library_root.id, rel_path="clip.mp4"
    )
    assert location.filename == "clip.mp4"
    assert location.size_bytes is None


# --- Sift does not touch the library -------------------------------------------------


@pytest.mark.integration
async def test_indexing_a_library_changes_nothing_in_it(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """These are the user's files. Sift reads them and does nothing else: no move, no rename,
    no sidecar written next to them, nothing."""
    for index, name in enumerate(["accepted.mp4", "accepted.jpg", "accepted.gif"]):
        place(name, library_root, f"folder{index}/{name}")

    before = tree(library_root.path)

    for index, name in enumerate(["accepted.mp4", "accepted.jpg", "accepted.gif"]):
        rel_path = f"folder{index}/{name}"
        await content_store.ingest(
            checked(library_root.path / rel_path, settings),
            root_id=library_root.id,
            rel_path=rel_path,
        )

    assert tree(library_root.path) == before


@pytest.mark.integration
async def test_derivatives_land_in_the_cache_and_never_beside_the_original(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.mp4", library_root, "clips/holiday.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clips/holiday.mp4"
    )
    before = tree(library_root.path)

    path = content_store.derivative_path(result.asset.id, DerivativeKind.THUMB, extension="jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a thumbnail")
    derivative = await content_store.add_derivative(
        result.asset.id, DerivativeKind.THUMB, extension="jpg", size_bytes=path.stat().st_size
    )

    assert path.is_relative_to(settings.cache_dir)
    assert not path.is_relative_to(library_root.path)
    assert tree(library_root.path) == before

    stored = await content_store.derivatives(result.asset.id)
    assert stored == [derivative]
    assert settings.cache_dir / derivative.rel_cache_path == path
