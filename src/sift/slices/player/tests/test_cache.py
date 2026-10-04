# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cache never exceeds its cap, asserted over generated sequences rather than chosen ones: an
eviction bug fills the disk the library lives on."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sift.slices.player.cache import SegmentCache, size_on_disk

pytestmark = pytest.mark.unit


def _cache(tmp_path: Path, *, max_bytes: int) -> SegmentCache:
    return SegmentCache(tmp_path, max_bytes=max_bytes)


def _write(cache: SegmentCache, key: str, size: int) -> bool:
    """Write a real file of `size` bytes and offer it to the cache, as the service does."""
    path = cache.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    kept = cache.admit(key, size)
    if not kept:
        path.unlink()
    return kept


# --- the invariant --------------------------------------------------------------------------


@given(
    cap=st.integers(min_value=1, max_value=10_000),
    operations=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=40),  # which segment
            st.integers(min_value=0, max_value=3_000),  # how big it is
        ),
        max_size=200,
    ),
)
def test_the_cache_never_exceeds_its_cap(
    tmp_path_factory: pytest.TempPathFactory, cap: int, operations: list[tuple[int, int]]
) -> None:
    """Whatever is asked, the total never crosses the cap; sizes straddle it, where naive policies
    break."""
    cache = _cache(tmp_path_factory.mktemp("cap"), max_bytes=cap)

    for index, size in operations:
        cache.touch(f"seg-{index}")  # a read first, as the real path does
        _write(cache, f"seg-{index}", size)
        assert cache.total_bytes <= cap
        assert cache.total_bytes >= 0

    assert cache.total_bytes <= cap


@given(
    cap=st.integers(min_value=100, max_value=5_000),
    operations=st.lists(
        st.tuples(st.integers(min_value=0, max_value=20), st.integers(min_value=1, max_value=200)),
        max_size=120,
    ),
)
def test_what_the_cache_says_it_holds_is_what_is_on_the_disk(
    tmp_path_factory: pytest.TempPathFactory, cap: int, operations: list[tuple[int, int]]
) -> None:
    """The cache's accounting matches the directory on disk."""
    directory = tmp_path_factory.mktemp("honest")
    cache = _cache(directory, max_bytes=cap)

    for index, size in operations:
        _write(cache, f"seg-{index}", size)

    on_disk = sum(size_on_disk(child) for child in directory.iterdir())
    assert on_disk == cache.total_bytes
    assert on_disk <= cap


# --- the oversized-segment guard --------------------------------------------------------


def test_a_segment_bigger_than_the_whole_cap_is_never_admitted(tmp_path: Path) -> None:
    """A segment bigger than the whole cap is never admitted."""
    cache = _cache(tmp_path, max_bytes=40)

    assert _write(cache, "small", 30) is True
    assert cache.total_bytes == 30

    assert _write(cache, "enormous", 81) is False
    assert cache.total_bytes <= 40


def test_an_oversized_segment_does_not_drain_the_cache_on_its_way_through(tmp_path: Path) -> None:
    """An oversized segment does not evict the cache on its way through."""
    cache = _cache(tmp_path, max_bytes=40)
    _write(cache, "a", 15)
    _write(cache, "b", 15)

    _write(cache, "enormous", 100)

    assert set(cache.keys()) == {"a", "b"}
    assert cache.total_bytes == 30


def test_an_oversized_rewrite_does_not_leave_the_old_segment_in_the_books(tmp_path: Path) -> None:
    """An oversized rewrite drops the old segment from the books, its file already gone."""
    cache = _cache(tmp_path, max_bytes=100)
    _write(cache, "a", 60)
    assert cache.total_bytes == 60

    _write(cache, "a", 150)  # same key, too big to keep

    assert "a" not in cache
    assert cache.total_bytes == 0
    assert sum(size_on_disk(child) for child in tmp_path.iterdir()) == 0


def test_a_segment_exactly_the_size_of_the_cap_is_admitted(tmp_path: Path) -> None:
    """The boundary is inclusive: it fits in an empty cache, so it is kept."""
    cache = _cache(tmp_path, max_bytes=40)
    assert cache.max_bytes == 40
    _write(cache, "filler", 20)

    assert _write(cache, "exact", 40) is True
    assert cache.total_bytes == 40
    assert set(cache.keys()) == {"exact"}


# --- least-recently-used, rather than first-in-first-out ---------------------------------------


def test_the_least_recently_used_segment_goes_first(tmp_path: Path) -> None:
    """Reading a segment protects it. Without this it is a FIFO queue wearing an LRU's name."""
    cache = _cache(tmp_path, max_bytes=30)
    _write(cache, "a", 10)
    _write(cache, "b", 10)
    _write(cache, "c", 10)

    # Touching `a` makes `b` the oldest, so `b` is what the next admission costs.
    assert cache.touch("a") is not None
    _write(cache, "d", 10)

    assert "a" in cache
    assert "b" not in cache


def test_what_was_written_before_this_boot_is_taken_back_under_the_cap_newest_first(
    tmp_path: Path,
) -> None:
    """Segments from before this boot are taken back under the cap, newest kept; a transcode's
    scratch is left alone."""
    import os

    directory = tmp_path / "segments"
    directory.mkdir()
    ages = {"old": 300, "middle": 200, "new": 100}
    for name, age in ages.items():
        (directory / name).write_bytes(b"x" * 10)
        stamp = 1_700_000_000 - age
        os.utime(directory / name, (stamp, stamp))
    (directory / ".build-half").write_bytes(b"x" * 10)

    cache = SegmentCache(directory, max_bytes=25)
    kept = cache.reload()

    assert kept == 2
    assert cache.keys() == ("middle", "new")
    assert cache.total_bytes == 20
    assert not (directory / "old").exists(), "the oldest was evicted from the disk as well"
    assert (directory / ".build-half").exists(), "a transcode in flight is not touched"


def test_reloading_removes_a_segment_bigger_than_the_whole_cap(tmp_path: Path) -> None:
    """A cap lowered between two boots can be smaller than one segment already on the disk. That
    segment could never be admitted, so it is not left where the books cannot see it."""
    directory = tmp_path / "segments"
    directory.mkdir()
    (directory / "small").write_bytes(b"x" * 10)
    (directory / "huge").write_bytes(b"x" * 100)

    cache = SegmentCache(directory, max_bytes=25)

    assert cache.reload() == 1
    assert cache.keys() == ("small",)
    assert not (directory / "huge").exists()


def test_reloading_steps_over_an_entry_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A segment removed between the directory listing and its `stat` (a viewer's eviction on
    another thread, a tidy-up by hand) is a file that is not there, not a boot that fails."""
    import os
    from types import SimpleNamespace

    directory = tmp_path / "segments"
    directory.mkdir()
    (directory / "kept").write_bytes(b"x" * 10)
    real_scandir = os.scandir

    def vanishing(*, follow_symlinks: bool = True) -> object:
        raise FileNotFoundError("gone between the listing and the stat")

    def listing(path: str | os.PathLike[str]) -> list[object]:
        entries: list[object] = list(real_scandir(path))
        entries.append(
            SimpleNamespace(name="ghost", is_file=lambda follow_symlinks=True: True, stat=vanishing)
        )
        return entries

    monkeypatch.setattr(os, "scandir", listing)

    cache = SegmentCache(directory, max_bytes=25)

    assert cache.reload() == 1
    assert cache.keys() == ("kept",)


def test_reloading_an_empty_or_missing_directory_holds_nothing(tmp_path: Path) -> None:
    assert SegmentCache(tmp_path / "missing", max_bytes=10).reload() == 0
    assert SegmentCache(tmp_path, max_bytes=10).reload() == 0


def test_a_touch_on_a_segment_that_is_not_held_is_a_miss(tmp_path: Path) -> None:
    cache = _cache(tmp_path, max_bytes=30)
    assert cache.touch("never-made") is None


def test_a_segment_whose_file_vanished_reads_as_a_miss(tmp_path: Path) -> None:
    """A segment whose file vanished reads as a miss, and its row is dropped."""
    cache = _cache(tmp_path, max_bytes=100)
    _write(cache, "a", 10)

    cache.path_for("a").unlink()

    assert cache.touch("a") is None
    assert "a" not in cache
    assert cache.total_bytes == 0


def test_rewriting_a_segment_replaces_its_accounting(tmp_path: Path) -> None:
    """A second write of the same key at a different size must not double-count.

    It happens when a transcode is retried and the encoder makes a marginally different file.
    """
    cache = _cache(tmp_path, max_bytes=100)
    _write(cache, "a", 10)
    _write(cache, "a", 25)

    assert cache.total_bytes == 25
    assert len(cache) == 1


def test_discarding_a_segment_removes_it_from_disk_and_from_the_books(tmp_path: Path) -> None:
    cache = _cache(tmp_path, max_bytes=100)
    _write(cache, "a", 10)

    cache.discard("a")

    assert "a" not in cache
    assert cache.total_bytes == 0
    assert not cache.path_for("a").exists()

    cache.discard("a")  # again, on something already gone


def test_every_piece_of_one_file_goes_when_that_file_ends(tmp_path: Path) -> None:
    """Every piece of a deleted file goes, numbered segments and init header alike."""
    cache = _cache(tmp_path, max_bytes=500)
    _write(cache, "01H4-h0-0.m4s", 10)
    _write(cache, "01H4-h0-1.m4s", 10)
    _write(cache, "01H4-h720-init.mp4", 10)
    _write(cache, "01H9-h0-0.m4s", 10)

    assert cache.discard_asset("01H4") == 3

    assert cache.keys() == ("01H9-h0-0.m4s",)
    assert cache.total_bytes == 10
    assert not cache.path_for("01H4-h0-0.m4s").exists()
    assert cache.path_for("01H9-h0-0.m4s").exists()


def test_a_file_with_nothing_cached_is_an_ordinary_answer(tmp_path: Path) -> None:
    """The common case by far. Most deleted files were never transcoded at all."""
    cache = _cache(tmp_path, max_bytes=100)
    _write(cache, "01H9-h0-0.m4s", 10)

    assert cache.discard_asset("01H4") == 0
    assert cache.total_bytes == 10


def test_the_pieces_of_another_file_are_not_taken_by_a_near_match(tmp_path: Path) -> None:
    """The match is on the id and a dash, so a near-match file keeps its pieces."""
    cache = _cache(tmp_path, max_bytes=200)
    _write(cache, "01H4-h0-0.m4s", 10)
    _write(cache, "01H4EXTRA-h0-0.m4s", 10)

    assert cache.discard_asset("01H4") == 1

    assert cache.keys() == ("01H4EXTRA-h0-0.m4s",)


def test_a_half_written_segment_is_left_where_it_is(tmp_path: Path) -> None:
    """The scratch file belongs to a transcode running right now, for somebody watching something
    else. Taking it out from under one turns a delete into a failed video."""
    cache = _cache(tmp_path, max_bytes=200)
    _write(cache, ".build-01H4-h0-0.m4s", 10)

    assert cache.discard_asset("01H4") == 0

    assert cache.keys() == (".build-01H4-h0-0.m4s",)


def test_evicting_tolerates_a_file_that_is_already_gone(tmp_path: Path) -> None:
    """Eviction deletes files, and a file it is about to delete can already be deleted."""
    cache = _cache(tmp_path, max_bytes=20)
    _write(cache, "a", 10)
    cache.path_for("a").unlink()

    _write(cache, "b", 15)

    assert "a" not in cache
    assert cache.total_bytes == 15


def test_a_cache_with_no_room_at_all_is_refused(tmp_path: Path) -> None:
    """A cap of zero is a configuration mistake, and it is caught where it is made."""
    with pytest.raises(ValueError, match="positive"):
        SegmentCache(tmp_path, max_bytes=0)


def test_a_negative_segment_size_is_refused(tmp_path: Path) -> None:
    cache = _cache(tmp_path, max_bytes=100)
    with pytest.raises(ValueError, match="negative"):
        cache.admit("a", -1)


def test_size_on_disk_of_a_file_that_is_not_there_is_zero(tmp_path: Path) -> None:
    assert size_on_disk(tmp_path / "nothing") == 0


def test_a_segment_that_cannot_be_unlinked_is_logged_and_stepped_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stubborn temporary file must not take the playback down with it.

    The accounting drops it either way, so the cap still holds; what is lost is one file's worth of
    disk until something else cleans it up.
    """
    cache = _cache(tmp_path, max_bytes=20)
    _write(cache, "a", 10)

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise OSError("device or resource busy")

    monkeypatch.setattr(Path, "unlink", refuse)

    _write(cache, "b", 15)

    assert "a" not in cache
    assert cache.total_bytes == 15


# --- the headline: a long session stays bounded ------------------------------------------------


def test_a_long_browsing_session_stays_within_the_cap(tmp_path: Path) -> None:
    """Many clips with a few segments each, none revisited, stay within the cap."""
    cap = 8_000
    cache = _cache(tmp_path, max_bytes=cap)
    peak = 0

    for clip in range(400):
        for segment in range(3):
            _write(cache, f"clip{clip}-{segment}", 120)
            peak = max(peak, cache.total_bytes)
            assert cache.total_bytes <= cap

    assert peak > cap // 2, "the cache should actually fill up, or this proves nothing"
    on_disk = sum(size_on_disk(child) for child in tmp_path.iterdir())
    assert on_disk <= cap


# --- moving the cap while the application is running -------------------------------------------


def test_lowering_the_cap_evicts_at_once_rather_than_at_the_next_thing_played(
    tmp_path: Path,
) -> None:
    """Lowering the cap evicts at once, not at the next thing played."""
    cache = _cache(tmp_path, max_bytes=100)
    _write(cache, "a", 30)
    _write(cache, "b", 30)
    _write(cache, "c", 30)
    assert cache.total_bytes == 90

    assert cache.resize(40) is True

    assert cache.max_bytes == 40
    assert cache.total_bytes <= 40
    # And the oldest went first, which is the same rule an admission follows.
    assert "c" in cache


def test_raising_the_cap_keeps_everything_and_says_the_cap_moved(tmp_path: Path) -> None:
    cache = _cache(tmp_path, max_bytes=40)
    _write(cache, "a", 30)

    assert cache.resize(400) is True

    assert cache.max_bytes == 400
    assert set(cache.keys()) == {"a"}


def test_setting_the_cap_to_what_it_already_is_changes_nothing_and_says_so(tmp_path: Path) -> None:
    """The answer is what the caller logs on, so a settings beat that re-reads the same number must
    not announce a resize that did not happen, and must not walk the eviction loop either."""
    cache = _cache(tmp_path, max_bytes=40)
    _write(cache, "a", 30)

    assert cache.resize(40) is False

    assert cache.max_bytes == 40
    assert set(cache.keys()) == {"a"}


@pytest.mark.parametrize("cap", [0, -1, -4096])
def test_a_cap_of_nothing_is_refused_here_too(tmp_path: Path, cap: int) -> None:
    """The same refusal the constructor makes, at the other door into the same field. A zero here
    would come from a stored setting rather than from a mistake in the code, and accepting it would
    empty the cache on the next beat and never let anything back in."""
    cache = _cache(tmp_path, max_bytes=40)
    _write(cache, "a", 30)

    with pytest.raises(ValueError, match="positive"):
        cache.resize(cap)

    assert cache.max_bytes == 40
    assert set(cache.keys()) == {"a"}
