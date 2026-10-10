# SPDX-License-Identifier: AGPL-3.0-or-later
"""The local copies a share's bytes are read into once, and the caches that keep them.

A copy is kept while its file still has work queued, and while a reader may be opening it: a cache
that drops either sends the next pass back to the share, or fails it between its resolve and its
read. Neither may grow without bound, so both give way past a ceiling.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel import archives
from sift.kernel.archives import (
    PART_SUFFIX,
    ArchiveRefused,
    CopyChanged,
    OpenArchive,
    copy_settled,
    extract_member,
)
from sift.kernel.content import identity_places
from sift.kernel.content.identity import ContentStore
from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.kernel.media_sources import resolve
from sift.kernel.paths import PathEscape, confine
from sift.kernel.tests.content_helpers import FIXTURES, checked, place
from sift.testing.fixtures import LibraryRoot


@pytest.fixture(autouse=True)
def _nothing_handed_out() -> None:
    identity_places._HANDED_OUT.clear()


@pytest.fixture
def thumbnails(clean_handlers: None) -> str:
    """A job type that reads a file, registered for the queue to accept it."""

    async def nothing(_context: JobContext) -> None:  # pragma: no cover (never run)
        return None

    register_handler("thumbnail", nothing, name="Test job")
    return "thumbnail"


def test_a_copy_is_the_file_whole_and_never_half_of_one(tmp_path: Path) -> None:
    source = tmp_path / "share" / "one.jpg"
    source.parent.mkdir()
    source.write_bytes(b"\xff\xd8" + os.urandom(9 * 1024 * 1024))
    destination = tmp_path / "cache" / "one.jpg"
    destination.parent.mkdir()

    copied = copy_settled(source, destination, size=source.stat().st_size)

    assert copied == source.stat().st_size
    assert destination.read_bytes() == source.read_bytes()
    assert not list(destination.parent.glob(f"*{PART_SUFFIX}"))


def test_a_file_not_the_size_the_walk_saw_is_not_copied(tmp_path: Path) -> None:
    source = tmp_path / "one.jpg"
    source.write_bytes(b"x" * 100)
    destination = tmp_path / "out" / "one.jpg"
    destination.parent.mkdir()

    with pytest.raises(CopyChanged):
        copy_settled(source, destination, size=99)

    assert list(destination.parent.iterdir()) == []


def test_a_file_that_grows_while_it_is_copied_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "one.mp4"
    source.write_bytes(b"x" * (archives.COPY_CHUNK + 10))
    destination = tmp_path / "out" / "one.mp4"
    destination.parent.mkdir()
    real_fstat = os.fstat
    asked = []

    def growing(fd: int) -> os.stat_result:
        asked.append(fd)
        found = real_fstat(fd)
        if len(asked) == 1:
            return found
        values = list(found)
        values[6] += 1  # st_size, as a writer appending would leave it
        return os.stat_result(
            values,
            {
                "st_atime_ns": found.st_atime_ns,
                "st_mtime_ns": found.st_mtime_ns,
                "st_ctime_ns": found.st_ctime_ns,
            },
        )

    monkeypatch.setattr("sift.kernel.archives.os.fstat", growing)
    with pytest.raises(CopyChanged):
        copy_settled(source, destination, size=archives.COPY_CHUNK + 10)

    assert list(destination.parent.iterdir()) == []


def test_a_copy_of_something_that_is_not_a_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        copy_settled(tmp_path, tmp_path / "out", size=0)


def _zip(where: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(where, "w") as writing:
        for name, data in members.items():
            writing.writestr(name, data)
    return where


def test_an_archive_opened_once_gives_every_member_and_stays_open(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "set.zip", {"a.jpg": b"A" * 10, "b.jpg": b"B" * 20})
    cache = tmp_path / "cache"
    opened = OpenArchive(archive)

    first = extract_member(archive, "a.jpg", cache / "1" / "a.jpg", cache_dir=cache, opened=opened)
    second = extract_member(archive, "b.jpg", cache / "2" / "b.jpg", cache_dir=cache, opened=opened)
    held = opened.zip()
    opened.close()
    opened.close()

    assert (first, second) == (10, 20)
    assert (cache / "2" / "b.jpg").read_bytes() == b"B" * 20
    assert held.fp is None, "the archive was left open"


def test_a_picture_written_again_over_a_good_copy_never_leaves_it_half_written(
    tmp_path: Path,
) -> None:
    """Two pulls of one picture meet when a reader asks for it while another is pulling it out: the
    second writes beside the first and renames, so a reader of the first never sees a part."""
    good = b"G" * 64
    archive = tmp_path / "set.zip"
    with zipfile.ZipFile(archive, "w") as writing:
        writing.writestr("a.jpg", b"A" * 64)
    # The index says 64 bytes; the stored bytes are cut short, as a share that drops mid-read does.
    raw = bytearray(archive.read_bytes())
    at = raw.index(b"A" * 64)
    raw[at + 10 : at + 64] = b""
    archive.write_bytes(bytes(raw))
    cache = tmp_path / "cache"
    target = cache / "1" / "a.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(good)

    with pytest.raises(ArchiveRefused):
        extract_member(archive, "a.jpg", target, cache_dir=cache)

    assert target.read_bytes() == good
    assert not list(target.parent.glob(f"*{PART_SUFFIX}"))


def test_a_member_missing_from_an_archive_opened_once_is_refused(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "set.zip", {"a.jpg": b"A"})
    cache = tmp_path / "cache"
    opened = OpenArchive(archive)

    with pytest.raises(ArchiveRefused, match="could not be read out"):
        extract_member(
            archive, "gone.jpg", cache / "1" / "gone.jpg", cache_dir=cache, opened=opened
        )
    opened.close()

    assert not list((cache / "1").iterdir())


def test_a_cache_folder_that_goes_as_it_is_made_is_asked_for_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The keeper removes a picture's folder with its last picture, so a pull of that picture at that
    moment can find its folder going: asked again, not refused as outside the cache."""
    archive = _zip(tmp_path / "set.zip", {"a.jpg": b"A" * 5})
    cache = tmp_path / "cache"
    real = confine
    calls = []

    def going(root: Path, candidate: Path) -> Path:
        calls.append(candidate)
        if len(calls) == 1:
            raise PathEscape("does not resolve") from PermissionError("delete pending")
        return real(root, candidate)

    monkeypatch.setattr("sift.kernel.archives.confine", going)

    assert extract_member(archive, "a.jpg", cache / "x" / "a.jpg", cache_dir=cache) == 5
    assert len(calls) == 2


def test_a_place_outside_the_cache_is_refused_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _zip(tmp_path / "set.zip", {"a.jpg": b"A"})
    calls = []

    def outside(root: Path, candidate: Path) -> Path:
        calls.append(candidate)
        raise PathEscape("not inside")

    monkeypatch.setattr("sift.kernel.archives.confine", outside)
    with pytest.raises(ArchiveRefused, match="not a place inside the cache"):
        extract_member(archive, "a.jpg", tmp_path / "c" / "a.jpg", cache_dir=tmp_path / "c")
    assert len(calls) == 1


def test_a_folder_that_keeps_going_is_refused_in_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _zip(tmp_path / "set.zip", {"a.jpg": b"A"})

    def going(root: Path, candidate: Path) -> Path:
        raise PathEscape("does not resolve") from PermissionError("delete pending")

    monkeypatch.setattr("sift.kernel.archives.confine", going)
    with pytest.raises(ArchiveRefused, match="not a place inside the cache"):
        extract_member(archive, "a.jpg", tmp_path / "c" / "a.jpg", cache_dir=tmp_path / "c")


# --- the caches' keeper ---------------------------------------------------------------------


def _held(cache: Path, asset: str, size: int, when: int) -> Path:
    path = cache / asset / "001.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (when, when))
    return path


async def test_a_copy_whose_file_still_has_work_queued_is_kept_over_the_budget(
    content_store: ContentStore,
    job_queue: JobQueue,
    thumbnails: str,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache at its cap that drops a copy with work still owed sends that work back to the share
    for the same bytes: such a copy is the one worth keeping."""
    monkeypatch.setattr(ContentStore, "ARCHIVE_CACHE_BUDGET", 1_000)
    cache = settings.cache_dir / ContentStore.ARCHIVE_CACHE
    owed = _held(cache, "owed", 400, 1_500_000_000)
    idle = _held(cache, "idle", 400, 1_600_000_000)
    new = _held(cache, "new", 400, 1_700_000_000)
    await job_queue.enqueue(thumbnails, {"asset_id": "owed"})

    await content_store._keep(ContentStore.ARCHIVE_CACHE, 1_000, new)

    assert owed.is_file(), "a copy with work queued was dropped"
    assert not idle.exists() and not idle.parent.exists()
    assert new.is_file()


async def test_past_its_ceiling_even_a_copy_with_work_owed_goes_and_it_is_said(
    content_store: ContentStore,
    job_queue: JobQueue,
    thumbnails: str,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(identity_places, "OWED_CEILING_TIMES", 1)
    cache = settings.cache_dir / ContentStore.ARCHIVE_CACHE
    owed = _held(cache, "owed", 400, 1_500_000_000)
    new = _held(cache, "new", 400, 1_700_000_000)
    await job_queue.enqueue(thumbnails, {"asset_id": "owed"})

    with capture_logs() as logs:
        await content_store._keep(ContentStore.ARCHIVE_CACHE, 500, new)

    assert not owed.exists()
    assert new.is_file()
    said = [one for one in logs if one["event"] == "content.cache_dropped_owed"]
    assert said and said[0]["owed"] == 1


async def test_which_copies_are_owed_is_asked_of_the_queue_at_most_twice_a_minute(
    content_store: ContentStore, job_queue: JobQueue, thumbnails: str
) -> None:
    await job_queue.enqueue(thumbnails, {"asset_id": "a"})
    assert await content_store._work_owed(["a", "b"]) == frozenset({"a"})
    await job_queue.enqueue(thumbnails, {"asset_id": "b"})

    # The answer stands; an asset it was not asked about counts as owed until it is.
    assert await content_store._work_owed(["a", "b", "c"]) == frozenset({"a", "c"})
    content_store._owed_answer = (float("-inf"), frozenset(), frozenset())
    assert await content_store._work_owed(["a", "b", "c"]) == frozenset({"a", "b"})


def test_a_copy_handed_to_a_reader_lately_is_not_dropped_under_it(tmp_path: Path) -> None:
    cache = tmp_path / "archives"
    read = _held(cache, "read", 400, 1_500_000_000)
    other = _held(cache, "other", 400, 1_600_000_000)
    new = _held(cache, "new", 400, 1_700_000_000)
    identity_places._handed_out(read)

    dropped = identity_places._drop(cache, identity_places._cached(cache), 800, new, ceiling=1_200)

    assert dropped == (1, 0)
    assert read.is_file() and not other.exists()


def test_a_note_of_a_copy_handed_out_long_ago_is_let_go(monkeypatch: pytest.MonkeyPatch) -> None:
    identity_places._HANDED_OUT[Path("old")] = -identity_places.HANDED_OUT_SECONDS * 2
    monkeypatch.setattr(identity_places, "_HANDED_OUT_NOTES", 1)

    identity_places._handed_out(Path("new"))

    assert set(identity_places._HANDED_OUT) == {Path("new")}


def test_a_part_written_now_is_never_counted_or_dropped(tmp_path: Path) -> None:
    cache = tmp_path / "archives"
    part = cache / "a" / f"001.jpg.x{PART_SUFFIX}"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"x" * 1_000)

    identity_places._keep_under_budget(cache, 1, cache / "nothing")

    assert part.is_file()


def test_the_ceiling_follows_the_free_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Usage:
        free = 4_000

    monkeypatch.setattr(
        "sift.kernel.content.identity_places.shutil.disk_usage", lambda _path: Usage()
    )
    assert identity_places._owed_ceiling(tmp_path, 100) == 800
    assert identity_places._owed_ceiling(tmp_path, 10_000) == 10_000


# --- the copy kept, and read in place of the share ------------------------------------------


async def test_a_kept_copy_is_what_every_pass_resolves_to(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any, tmp_path: Path
) -> None:
    """Both of a Source's paths: the probe re-verifies `original` and decodes `path`, and neither
    may go back to the share once the take-in has the bytes."""
    target = place("accepted.jpg", library_root, "pictures/one.jpg")
    ingested = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="pictures/one.jpg"
    )
    scratch = tmp_path / "scratch" / "one.jpg"
    scratch.parent.mkdir()
    scratch.write_bytes(target.read_bytes())

    kept = await content_store.keep_local_copy(ingested.asset.id, scratch)
    target.unlink()
    source = await resolve(content_store, ingested.asset.id)

    assert not scratch.exists()
    assert source.path == source.original == kept
    assert kept.read_bytes() == (FIXTURES / "accepted.jpg").read_bytes()
    assert settings.cache_dir / ContentStore.LOCAL_COPIES in kept.parents


async def test_a_second_copy_of_the_same_bytes_leaves_the_first(
    content_store: ContentStore, settings: Any, tmp_path: Path
) -> None:
    scratch = tmp_path / "s" / "one.jpg"
    scratch.parent.mkdir()
    scratch.write_bytes(b"first")
    kept = await content_store.keep_local_copy("asset", scratch)
    scratch.write_bytes(b"second")

    again = await content_store.keep_local_copy("asset", scratch)

    assert again == kept and kept.read_bytes() == b"first"
    assert not scratch.exists()


async def test_a_copy_that_cannot_be_placed_takes_its_scratch_with_it(
    content_store: ContentStore, settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "s" / "one.jpg"
    scratch.parent.mkdir()
    scratch.write_bytes(b"bytes")

    def full(_from: Any, _to: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("sift.kernel.content.identity_places.os.replace", full)
    with pytest.raises(OSError):
        await content_store.keep_local_copy("asset", scratch)
    assert not scratch.exists()


async def test_a_picture_out_of_an_archive_is_kept_where_the_passes_look(
    content_store: ContentStore, settings: Any, tmp_path: Path
) -> None:
    scratch = tmp_path / "s" / "001.jpg"
    scratch.parent.mkdir()
    scratch.write_bytes(b"bytes")

    kept = await content_store.keep_local_copy("asset", scratch, from_archive=True)

    assert kept == settings.cache_dir / ContentStore.ARCHIVE_CACHE / "asset" / "001.jpg"
    assert await content_store.local_copy("asset") == kept


def test_no_copy_is_found_in_a_folder_of_parts_alone(tmp_path: Path) -> None:
    (tmp_path / f"a.jpg.1{PART_SUFFIX}").write_bytes(b"x")
    assert identity_places._the_copy_in(tmp_path) is None
    assert identity_places._the_copy_in(tmp_path / "missing") is None


async def test_a_starts_tidy_removes_the_take_ins_scratch_and_keeps_the_copies(
    content_store: ContentStore,
) -> None:
    """A process stopped during a copy or an archive's take-in leaves its scratch folder; the
    next start removes it, and the kept copies the passes read stay."""
    incoming = content_store._settings.cache_dir / "incoming"
    for name in ("copy-abc", "archive-def"):
        (incoming / name).mkdir(parents=True)
        (incoming / name / "half.jpg").write_bytes(b"part")
    kept = content_store._settings.cache_dir / ContentStore.LOCAL_COPIES / "A" / "a.jpg"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"whole")

    assert await content_store.tidy_incoming() == 2
    assert sorted(one.name for one in incoming.iterdir()) == ["copies"]
    assert kept.read_bytes() == b"whole"
    assert await content_store.tidy_incoming() == 0


async def test_a_start_with_no_scratch_yet_tidies_nothing(tmp_path: Path) -> None:
    assert identity_places._tidy_scratch(tmp_path / "never-made") == 0
