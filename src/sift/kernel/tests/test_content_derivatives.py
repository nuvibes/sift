# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift builds from a file: where each picture goes, what its bytes are, and which files a rebuild touches."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import changes
from sift.kernel.changes import About, ChangeBus
from sift.kernel.content import hashing, schema
from sift.kernel.content.hashing import (
    digest_cache_file,
    fingerprint,
)
from sift.kernel.content.identity import (
    ContentStore,
    DerivativeKind,
    VerdictProduct,
    check_rel_path,
    derivative_relpath,
    params_key,
)
from sift.kernel.content.schema import CONTENT_COMPONENT, LIBRARY_COMPONENT
from sift.kernel.db import Database, registered_components
from sift.kernel.ids import new_id
from sift.kernel.ingress import (
    ALLOWED_MEDIA,
)
from sift.kernel.tests.content_helpers import (
    GOLDEN,
    checked,
    corpus_survives,  # noqa: F401  (the corpus check, autouse)
    place,
)
from sift.testing.fixtures import LibraryRoot
from sift.testing.tools import POSIX_ONLY, WINDOWS_ONLY, junction

# --- Reading a file Sift built itself ---------------------------------------------------


async def test_a_cache_file_that_is_not_there_is_no_digest_rather_than_a_failure(
    tmp_path: Path,
) -> None:
    """The cache is disposable by design, so a file that has been swept is ordinary. The caller
    records nothing, which reads as "serve this the careful way": the behaviour for any address
    that does not name its contents."""
    assert await digest_cache_file(tmp_path / "never-written.jpg") is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "a named pipe cannot be put in a folder on Windows: its named pipes live under \\\\.\\pipe\\ and are not reachable as a path inside a media folder, so the hazard this guards against does not exist there. See kernel/paths.O_NONBLOCK."
    ),
)
async def test_a_named_pipe_where_a_thumbnail_should_be_is_not_read(tmp_path: Path) -> None:
    """The cache directory sits on somebody's machine and can have anything in it. Opened the
    ordinary way a pipe blocks until something writes to it, and nothing will, so the worker
    would sit there for ever and every job behind it would stop. Checked on the descriptor rather
    than on the path, so a swap between the check and the read cannot slip one through."""
    pipe = tmp_path / "thumb.jpg"
    os.mkfifo(pipe)  # type: ignore[attr-defined, unused-ignore]

    assert await digest_cache_file(pipe) is None


async def test_a_file_that_cannot_be_read_is_no_digest_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk going bad part-way through. Recording no digest costs a picture that keeps being
    re-checked; letting it out would fail the job that was building the picture."""
    target = tmp_path / "thumb.jpg"
    target.write_bytes(b"a thumbnail")

    def refuses(fd: int, *args: Any, **kwargs: Any) -> Any:
        os.close(fd)
        raise OSError("the disk gave up")

    monkeypatch.setattr("sift.kernel.content.hashing.os.fdopen", refuses)

    assert await digest_cache_file(target) is None


# --- What a derivative's bytes are -----------------------------------------------------


async def _derivative(
    content_store: ContentStore,
    asset_id: str,
    kind: DerivativeKind,
    body: bytes,
    *,
    extension: str = "jpg",
) -> Any:
    """Write a derivative's file where the store would put it, then record it."""
    path = content_store.derivative_path(asset_id, kind, extension=extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return await content_store.add_derivative(
        asset_id, kind, extension=extension, size_bytes=len(body)
    )


@pytest.mark.integration
async def test_a_picture_is_recorded_with_a_digest_of_its_own_bytes(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """What makes an address name the picture rather than the asset.

    Read here rather than passed in by the caller: every job that builds one of these has just
    written a file, and a digest each of them had to remember to compute is a digest one of them
    would not.
    """
    target = place("accepted.mp4", library_root, "clips/holiday.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clips/holiday.mp4"
    )

    first = await _derivative(content_store, result.asset.id, DerivativeKind.THUMB, b"a thumbnail")
    assert first.content_hash is not None

    # Rebuilt identically: the same address, so nothing that already holds it has to fetch again.
    same = await _derivative(content_store, result.asset.id, DerivativeKind.THUMB, b"a thumbnail")
    assert same.content_hash == first.content_hash

    # Rebuilt differently: a new address, which is the whole point of the digest being of bytes.
    changed = await _derivative(
        content_store, result.asset.id, DerivativeKind.THUMB, b"a different thumbnail"
    )
    assert changed.content_hash != first.content_hash


@pytest.mark.integration
async def test_a_whole_file_copy_is_never_read_to_be_digested(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A repaired copy is a whole video, streamed a byte range at a time and never given a keepable
    address. Reading one to record something nothing uses is gigabytes of disk for nothing."""
    target = place("accepted.mp4", library_root, "clips/holiday.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clips/holiday.mp4"
    )

    repaired = await _derivative(
        content_store, result.asset.id, DerivativeKind.REMUX, b"a whole video", extension="mp4"
    )

    assert repaired.content_hash is None


@pytest.mark.parametrize(
    ("rel_path", "because"),
    [
        ("/etc/passwd", "absolute"),
        ("../../../etc/passwd", "plain path"),
        ("clips/../../secrets/key.mp4", "plain path"),
        ("..", "plain path"),
        ("", "needs a path"),
        (".", "plain path"),
        ("clips//holiday.mp4", "plain path"),
        ("clips/./holiday.mp4", "plain path"),
        ("clip\x00.mp4", "null byte"),
        # The same attacks spelled the other way. None of these has a `..` segment at all if you
        # only split on `/`, and a drive-qualified path does not extend the root when it is joined
        # on Windows: it replaces it.
        ("..\\..\\etc\\passwd", "plain path"),
        ("clips\\..\\..\\secrets\\key.mp4", "plain path"),
        ("\\etc\\passwd", "absolute"),
        ("C:\\Windows\\System32\\x.mp4", "drive"),
        ("C:x.mp4", "drive"),
        ("\\\\host\\share\\x.mp4", "absolute"),
    ],
)
def test_a_path_that_leaves_its_root_is_refused(rel_path: str, because: str) -> None:
    """Every file Sift serves is read from `root.abs_path / rel_path`. A `..` in that string is a
    request for any file on the machine, wearing an asset id.

    The exact reason is asserted, not merely that something was raised. Several of these checks
    would catch several of these paths, and a test that accepts any refusal passes happily with
    one of them deleted: the survivor covering for the corpse.
    """
    with pytest.raises(ValueError, match=because):
        check_rel_path(rel_path)


@pytest.mark.parametrize(
    "rel_path",
    [
        "clip.mp4",
        "clips/holiday.mp4",
        "a/b/c/d/clip.mp4",
        "..holiday.mp4",
        "clips/..holiday..mp4",
        "not a virus.mp4",
        "clips/2019/summer holiday (1).mp4",
        # A backslash is a legal character in a filename on Linux, and this is a Linux server.
        # Refusing it would refuse real files; only a `..` component and a drive are refused.
        "my\\file.mp4",
        "clips/AC\\DC - live.mp4",
    ],
)
def test_an_ordinary_path_is_allowed(rel_path: str) -> None:
    """The other half. A guard that refuses real filenames is a guard that gets removed, and
    a leading `..` in a *name* is not a `..` segment."""
    assert check_rel_path(rel_path) == rel_path


async def test_a_traversal_cannot_be_stored_as_a_location(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The guard is on the way in, so no caller can write one: not through `ingest`, and not
    through `add_location` either."""
    target = place("accepted.mp4", library_root, "clip.mp4")

    with pytest.raises(ValueError, match="plain path"):
        await content_store.ingest(
            checked(target, settings),
            root_id=library_root.id,
            rel_path="../../../etc/passwd",
        )

    asset, _ = await content_store.upsert_asset(
        digest=GOLDEN["accepted.mp4"],
        media=checked(target, settings).media,
        size_bytes=1781,
    )
    with pytest.raises(ValueError, match="plain path"):
        await content_store.add_location(
            asset_id=asset.id, root_id=library_root.id, rel_path="../../../etc/passwd"
        )


# --- Where a derivative goes ---------------------------------------------------------


@pytest.mark.regression
def test_the_cache_path_convention_is_stable() -> None:
    """A golden path. Change the convention and every thumbnail, preview and sprite already on
    disk becomes unreachable: present, correct, and looked for somewhere else."""
    asset_id = "01JZQK8XW9GH4NPTVBC2R7MDEF"

    assert (
        derivative_relpath(asset_id, DerivativeKind.THUMB, extension="jpg")
        == "MD/EF/01JZQK8XW9GH4NPTVBC2R7MDEF/thumb.jpg"
    )
    assert (
        derivative_relpath(
            asset_id, DerivativeKind.RENDITION, extension="m3u8", params={"height": 720}
        )
        == f"MD/EF/01JZQK8XW9GH4NPTVBC2R7MDEF/rendition-{fingerprint('{"height":720}')}.m3u8"
    )


def test_the_settings_are_compared_by_meaning_not_by_spelling() -> None:
    """Two dicts that say the same thing must produce the same file. Left to dict ordering they
    would be two files on disk for one thumbnail, and no way to tell which one is current."""
    one = derivative_relpath(
        new_id(), DerivativeKind.SPRITE, extension="jpg", params={"width": 320, "rows": 5}
    )
    other_order = {"rows": 5, "width": 320}
    assert params_key(other_order) == params_key({"width": 320, "rows": 5})

    same_id = one.split("/")[2]
    assert (
        derivative_relpath(same_id, DerivativeKind.SPRITE, extension="jpg", params=other_order)
        == one
    )


def test_different_settings_are_different_files() -> None:
    asset_id = new_id()
    small = derivative_relpath(
        asset_id, DerivativeKind.RENDITION, extension="m3u8", params={"height": 480}
    )
    large = derivative_relpath(
        asset_id, DerivativeKind.RENDITION, extension="m3u8", params={"height": 1080}
    )
    plain = derivative_relpath(asset_id, DerivativeKind.RENDITION, extension="m3u8")

    assert len({small, large, plain}) == 3


def test_no_settings_means_no_suffix() -> None:
    asset_id = new_id()
    assert derivative_relpath(
        asset_id, DerivativeKind.THUMB, extension="jpg"
    ) == derivative_relpath(asset_id, DerivativeKind.THUMB, extension="jpg", params={})
    assert params_key(None) == "{}"


# A fixed valid id, not new_id(): these cases are about rejecting the extension, so the id only has
# to be well formed, and generating one here gives each parallel worker a different parametrize id,
# which a cross-worker run reads as "different tests were collected" and refuses to run. The
# value that goes into a test id has to be the same everywhere the id is computed.
_A_VALID_ID = "01BX5ZZKBKACTAV9WEVGEMMVRY"


@pytest.mark.parametrize(
    ("asset_id", "extension"),
    [
        ("../../etc/passwd", "jpg"),
        ("not an id", "jpg"),
        ("", "jpg"),
        (_A_VALID_ID, "../../../etc/cron.d/x"),
        (_A_VALID_ID, "jpg/../../.."),
        (_A_VALID_ID, ""),
        (_A_VALID_ID, "JPG"),
        (_A_VALID_ID, "j p g"),
    ],
)
def test_a_derivative_path_cannot_be_talked_out_of_the_cache(asset_id: str, extension: str) -> None:
    """Nothing but a real asset id and a plain extension gets into that string, which is what
    makes "derivatives never land beside the originals" true by construction."""
    with pytest.raises(ValueError, match=r"asset id|extension"):
        derivative_relpath(asset_id, DerivativeKind.THUMB, extension=extension)


@given(
    kind=st.sampled_from(list(DerivativeKind)),
    extension=st.from_regex(r"\A[a-z0-9]{1,8}\Z"),
    params=st.dictionaries(
        st.text(min_size=1, max_size=8),
        st.integers() | st.text(max_size=8) | st.booleans(),
        max_size=4,
    ),
)
def test_a_derivative_always_lands_under_the_cache_directory(
    kind: DerivativeKind, extension: str, params: dict[str, Any]
) -> None:
    # Resolved on both sides, because that is the comparison the claim is about and because a
    # leading slash is not an absolute path on every site: `resolve` fills in the drive on
    # one of them, and the unresolved base would then not be a prefix of anything.
    cache = Path("/cache").resolve()
    relative = derivative_relpath(new_id(), kind, extension=extension, params=params)
    resolved = (cache / relative).resolve()

    assert resolved.is_relative_to(cache)
    assert ".." not in relative


async def test_rebuilding_a_derivative_replaces_it(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Same asset, same kind, same settings is the same derivative. Two rows would be two files
    on disk, one of them stale and neither of them obviously so."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    first = await content_store.add_derivative(
        result.asset.id, DerivativeKind.THUMB, extension="jpg", size_bytes=100
    )
    second = await content_store.add_derivative(
        result.asset.id, DerivativeKind.THUMB, extension="jpg", size_bytes=200
    )

    stored = await content_store.derivatives(result.asset.id)
    assert len(stored) == 1
    assert stored[0].id == first.id == second.id
    assert stored[0].size_bytes == 200


async def test_a_picture_added_or_taken_away_tells_whoever_may_be_drawing_the_file(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A clip built on request is on disk the moment this returns, and a wall already open is
    drawing the file as having none. Its row does not move, so without being told the wall keeps
    that answer until the page is reloaded. Told as an arrival, which is what a wall re-reads on.

    Both directions, and the case that must stay quiet: a drop that matched nothing moved nothing.
    """
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    bus = ChangeBus()
    changes.listens(bus)
    watching = bus.subscribe("admin")
    try:
        watching.take(as_admin=True)
        await content_store.add_derivative(
            result.asset.id, DerivativeKind.PREVIEW, extension="mp4", size_bytes=10
        )
        added = watching.take(as_admin=True).about

        await content_store.drop_derivatives(DerivativeKind.PREVIEW)
        dropped = watching.take(as_admin=True).about

        await content_store.drop_derivatives(DerivativeKind.SPRITE)
        nothing = watching.take(as_admin=True).about
    finally:
        changes.listens(None)

    assert added == (About.ARRIVALS,), "a picture was added and nobody drawing the file was told"
    assert dropped == (About.ARRIVALS,), "a picture was taken away and nobody was told"
    assert nothing == (), "a drop that removed nothing was announced"


async def test_derivatives_with_different_settings_coexist(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    await content_store.add_derivative(
        result.asset.id, DerivativeKind.RENDITION, extension="m3u8", params={"height": 480}
    )
    await content_store.add_derivative(
        result.asset.id, DerivativeKind.RENDITION, extension="m3u8", params={"height": 1080}
    )

    stored = await content_store.derivatives(result.asset.id)
    assert len(stored) == 2
    assert len({derivative.rel_cache_path for derivative in stored}) == 2


# --- From a derivative row to a file in the cache --------------------------------------


@pytest.mark.integration
async def test_a_derivative_row_resolves_to_the_file_it_names(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.jpg", library_root, "photo.jpg")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="photo.jpg"
    )
    derivative = await content_store.add_derivative(
        result.asset.id, DerivativeKind.THUMB, extension="jpg"
    )
    written = settings.cache_dir / derivative.rel_cache_path
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_bytes(b"a thumbnail")

    found = await content_store.derivative_at(derivative.rel_cache_path)
    assert found is not None
    assert found.read_bytes() == b"a thumbnail"


@pytest.mark.integration
async def test_a_derivative_whose_file_is_gone_is_not_an_error(
    content_store: ContentStore,
) -> None:
    """The cache is disposable. A row pointing at something a sweep removed is ordinary, and the
    caller wants "no thumbnail" rather than an exception to handle on every tile."""
    assert await content_store.derivative_at("ab/cd/" + new_id() + "/thumb.jpg") is None


@pytest.mark.regression
@pytest.mark.parametrize(
    "rel_cache_path",
    [
        "../../etc/shadow",
        "/etc/shadow",
        "ab/cd/../../../../etc/shadow",
        "C:/Windows/System32/config/SAM",
        "ab/cd/\x00/thumb.jpg",
    ],
)
async def test_a_derivative_row_cannot_be_walked_out_of_the_cache(
    content_store: ContentStore, rel_cache_path: str
) -> None:
    """The row was checked when it was written. This is the check on the way back out, for a
    database restored from a backup that predates the guard."""
    with pytest.raises(ValueError):
        await content_store.derivative_at(rel_cache_path)


@pytest.mark.regression
@pytest.mark.integration
@POSIX_ONLY
async def test_a_symlink_in_the_cache_cannot_point_out_of_it(
    content_store: ContentStore, settings: Any, tmp_path: Path
) -> None:
    """A lexically clean path with no `..` in it, that still leaves the cache. Only resolving the
    symlink catches this, which is why the confinement follows links rather than reading text."""
    secret = tmp_path / "outside" / "private.key"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"not a thumbnail")

    planted = settings.cache_dir / "ab" / "cd" / "thumb.jpg"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.symlink_to(secret)

    with pytest.raises(ValueError, match="outside the cache"):
        await content_store.derivative_at("ab/cd/thumb.jpg")


@WINDOWS_ONLY
async def test_a_junction_in_the_cache_cannot_point_out_of_it(
    content_store: ContentStore, settings: Any, tmp_path: Path
) -> None:
    """The cache half of the same escape, by the mechanism this site has.

    The cache is written by Sift and read back by an address a request supplies, so a redirection
    planted in it points a request at a file somewhere else on the disk. A junction is not a link,
    which is exactly why the confinement has to resolve rather than read text.
    """
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "private.key").write_bytes(b"not a thumbnail")

    planted = settings.cache_dir / "ab" / "cd"
    planted.parent.mkdir(parents=True, exist_ok=True)
    junction(planted, outside)

    with pytest.raises(ValueError, match="outside the cache"):
        await content_store.derivative_at("ab/cd/private.key")


@pytest.mark.regression
async def test_the_content_schema_applies_without_the_layer_above_it(tmp_path: Path) -> None:
    """A process that uses the content tables alone must be able to build them alone.

    Not hypothetical: a worker subprocess imports the job queue and the content store and nothing
    else, and it applies the schema at startup. A component here that declared a dependency on a
    table the access layer owns would make that subprocess fail at boot, with an error about a
    missing component rather than about anything it was doing, and only in the one place that
    does not import the whole application.
    """
    registered = registered_components()
    for name in (LIBRARY_COMPONENT, CONTENT_COMPONENT, schema.USER_STATE_COMPONENT):
        component = registered[name]
        missing = [
            dep
            for dep in component.depends_on
            if dep not in {LIBRARY_COMPONENT, CONTENT_COMPONENT, schema.USER_STATE_COMPONENT}
        ]
        assert not missing, (
            f"{name} depends on {missing}, which the content layer cannot build on its own"
        )


# --- the files a rebuild would touch ------------------------------------------------------------
#
# `probed_at IS NOT NULL` and not the derivative table, deliberately: what is wanted is every file
# a picture COULD be made of, including the ones whose picture is wrong or missing, which is the
# whole reason somebody presses rebuild.


async def test_only_a_file_something_has_read_can_have_a_picture_made_of_it(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A row nothing has probed has nothing to make a picture from, so it is neither counted nor
    queued: a number on the screen that the run cannot reach is worse than no number."""
    unread = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )

    assert await content_store.thumbnailable() == []
    assert await content_store.thumbnailable_count() == 0

    await content_store.record_probe(unread.asset.id, width=1920, height=1080, duration_ms=1000)

    assert await content_store.thumbnailable() == [unread.asset.id]
    assert await content_store.thumbnailable_count() == 1


async def test_the_files_come_back_oldest_first_so_a_run_cut_short_has_made_progress(
    content_store: ContentStore, library_root: LibraryRoot, temp_db: Database, settings: Any
) -> None:
    """One job is queued per file in this order. A run stopped halfway has done the oldest half
    rather than an arbitrary one, which is what makes stopping it a decision rather than a loss."""
    ids = []
    # Three different files, not three copies of one. Identical bytes are one asset in three
    # places (the central rule of this module), so copies would test the ordering of a list
    # with one entry in it.
    for position, (fixture, name) in enumerate(
        (("accepted.mp4", "one.mp4"), ("accepted.mkv", "two.mkv"), ("accepted.webm", "three.webm"))
    ):
        made = await content_store.ingest(
            checked(place(fixture, library_root, name), settings),
            root_id=library_root.id,
            rel_path=name,
        )
        await content_store.record_probe(made.asset.id, width=16, height=16, duration_ms=1)
        await temp_db.execute(
            "UPDATE assets SET added_at = ? WHERE id = ?", (100 - position, made.asset.id)
        )
        ids.append(made.asset.id)

    assert await content_store.thumbnailable() == list(reversed(ids))
    assert await content_store.thumbnailable_count() == 3


async def _taken_in(
    store: ContentStore, root: LibraryRoot, settings: Any, fixture: str, name: str, *, added_at: int
) -> Any:
    """A file in the library, taken in at a stated moment, so an oldest-first order is decided by
    the test rather than by the clock."""
    taken = await store.ingest(
        checked(place(fixture, root, name), settings), root_id=root.id, rel_path=name
    )
    await store._db.execute(
        "UPDATE assets SET added_at = ? WHERE id = ?", (added_at, taken.asset.id)
    )
    return taken


async def test_a_pass_over_the_library_walks_the_files_read_oldest_first_a_page_at_a_time(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """What a pass that must visit every file walks: only files that have been read, since a file
    with no dimensions has nothing to build from, in the order they were taken in, and the same
    order on every page so a pass cut short can pick up where it stopped."""
    store = content_store
    first = await _taken_in(store, library_root, settings, "accepted.mp4", "one.mp4", added_at=1)
    second = await _taken_in(store, library_root, settings, "accepted.mkv", "two.mkv", added_at=2)
    third = await _taken_in(
        store, library_root, settings, "accepted.webm", "three.webm", added_at=3
    )
    unread = await _taken_in(store, library_root, settings, "accepted.jpg", "four.jpg", added_at=0)
    for taken in (third, first, second):
        await store.record_probe(taken.asset.id, width=16, height=16, duration_ms=1)

    page = await store.asset_ids_page(limit=2)
    assert page.ids == [first.asset.id, second.asset.id]
    assert (await store.asset_ids_page(after=page.last, limit=2)).ids == [third.asset.id]
    assert unread.asset.id not in (await store.asset_ids_page(limit=10)).ids


async def test_a_walk_by_key_neither_skips_nor_repeats_while_files_are_read_and_lost_behind_it(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A Generate walks the library while a scan is still reading it. A file read behind the walk,
    and a file whose copy goes missing behind it, move every later file's place; the next page
    starts after the last file handed out, so each file ahead is handed out exactly once."""
    store = content_store
    first = await _taken_in(store, library_root, settings, "accepted.mp4", "one.mp4", added_at=1)
    second = await _taken_in(store, library_root, settings, "accepted.mkv", "two.mkv", added_at=2)
    third = await _taken_in(
        store, library_root, settings, "accepted.webm", "three.webm", added_at=3
    )
    fourth = await _taken_in(store, library_root, settings, "accepted.mov", "four.mov", added_at=4)
    early = await _taken_in(store, library_root, settings, "accepted.jpg", "zero.jpg", added_at=0)
    for taken in (first, second, third, fourth):
        await store.record_probe(taken.asset.id, width=16, height=16, duration_ms=1)

    page = await store.asset_ids_page(limit=2)
    assert page.ids == [first.asset.id, second.asset.id]
    # Behind the walk: one file is read for the first time, and another loses its only copy.
    await store.record_probe(early.asset.id, width=16, height=16, duration_ms=None)
    await store.mark_missing(first.location.id)

    rest = await store.asset_ids_page(after=page.last, limit=2)
    assert rest.ids == [third.asset.id, fourth.asset.id]
    assert (await store.asset_ids_page(after=rest.last, limit=2)).ids == []


async def test_the_files_never_read_come_back_oldest_first_and_only_where_a_copy_can_be_read(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The pass at start that hands out the reads a cut-off scan never did. Four things are left
    out, each for a reason: a file already read, a file whose only copy is missing, a file the
    read itself gave up on, and a row with no copy anywhere. A verdict that was about a moment
    does not take a file out."""
    store = content_store
    waiting = await _taken_in(store, library_root, settings, "accepted.mp4", "one.mp4", added_at=2)
    also = await _taken_in(store, library_root, settings, "accepted.mkv", "two.mkv", added_at=1)
    read = await _taken_in(store, library_root, settings, "accepted.webm", "three.webm", added_at=0)
    away = await _taken_in(store, library_root, settings, "accepted.jpg", "four.jpg", added_at=0)
    given_up = await _taken_in(
        store, library_root, settings, "accepted.gif", "five.gif", added_at=0
    )
    await store.record_probe(read.asset.id, width=16, height=16, duration_ms=1)
    await store.mark_missing(away.location.id)
    await store.record_verdict(
        given_up.asset.id, VerdictProduct.PROBE, code="unreadable", reason="Unreadable."
    )
    await store.record_verdict(
        also.asset.id, VerdictProduct.PROBE, code="share_away", reason="Away.", transient=True
    )
    await store.upsert_asset(digest="nowhere", media=ALLOWED_MEDIA[0], size_bytes=1)

    assert await store.unread_page(offset=0, limit=10) == [also.asset.id, waiting.asset.id]
    assert await store.unread_page(offset=1, limit=1) == [waiting.asset.id]


async def test_an_empty_library_answers_zero_rather_than_nothing(
    content_store: ContentStore,
) -> None:
    """The count is a bare aggregate, so it answers on an empty table as readily as on a full one.
    Read as "no row" it would be a zero arrived at by accident rather than by counting."""
    assert await content_store.thumbnailable_count() == 0


# --- hover clips built some other way ----------------------------------------------------------
#
# A hover clip is cut to a recipe (how many pieces, how long each one runs) and the recipe is a
# setting. Changing it does not change the clips already on disk, so the library holds a mixture
# until somebody rebuilds. These three questions are what the Performance screen and the rebuild
# sweep are made of: how many are out of date, which ones, and what to delete once the replacement
# is in place.
#
# Compared on the WHOLE recipe rather than any one field of it. Two clips built with the same number
# of pieces at different lengths are as different as two built with different numbers.


async def _preview(
    content_store: ContentStore, asset_id: str, params: dict[str, int], body: bytes = b"clip"
) -> Any:
    """A hover clip recorded against a recipe, with its file where the store would put it."""
    path = content_store.derivative_path(
        asset_id, DerivativeKind.PREVIEW, extension="mp4", params=params
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return await content_store.add_derivative(
        asset_id, DerivativeKind.PREVIEW, extension="mp4", params=params, size_bytes=len(body)
    )


async def test_a_clip_cut_to_the_recipe_in_force_is_not_offered_for_rebuilding(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The zero case is the one worth writing: it is how the screen can say the library is already
    up to date rather than offering a button whose effect would be nothing."""
    now = {"pieces": 6, "each_ms": 1500}
    made = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )
    await _preview(content_store, made.asset.id, now)

    assert await content_store.previews_of_another_recipe(now) == []
    assert await content_store.previews_of_another_recipe_count(now) == 0


async def test_a_clip_cut_to_any_other_recipe_is_counted_and_named(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Same file, same number of pieces, a different length each, and that is enough. The recipe
    is compared whole, because a clip that cuts at the right moments for the wrong length is not
    the clip the setting asks for."""
    made = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )
    await _preview(content_store, made.asset.id, {"pieces": 6, "each_ms": 1000})

    now = {"pieces": 6, "each_ms": 1500}
    assert await content_store.previews_of_another_recipe(now) == [made.asset.id]
    assert await content_store.previews_of_another_recipe_count(now) == 1


async def test_a_file_with_clips_from_two_recipes_is_one_file_to_rebuild(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """DISTINCT, and it is load-bearing: a library carried through three recipes holds three rows
    per file, and the number on the confirm dialog is files rather than rows."""
    made = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )
    await _preview(content_store, made.asset.id, {"pieces": 5, "each_ms": 2000})
    await _preview(content_store, made.asset.id, {"pieces": 10, "each_ms": 1000})

    now = {"pieces": 6, "each_ms": 1500}
    assert await content_store.previews_of_another_recipe(now) == [made.asset.id]
    assert await content_store.previews_of_another_recipe_count(now) == 1


async def test_forgetting_the_superseded_clips_names_their_files_and_leaves_the_new_one(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Called once the replacement is recorded, so the row that matches the recipe in force has to
    survive: forgetting that one would delete the clip every tile is playing.

    The paths come back rather than the files being deleted here: the caller deletes, in that
    order, because a row that outlives its file is a broken picture and a file that outlives its
    row is a few hundred kilobytes the tidy sweep already knows how to find.
    """
    made = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )
    old = await _preview(content_store, made.asset.id, {"pieces": 5, "each_ms": 2000})
    now = {"pieces": 6, "each_ms": 1500}
    kept = await _preview(content_store, made.asset.id, now)

    assert await content_store.forget_superseded_previews(made.asset.id, now) == [
        str(old.rel_cache_path)
    ]
    assert await content_store.derivatives(made.asset.id) == [kept]
    assert await content_store.previews_of_another_recipe_count(now) == 0


async def test_forgetting_touches_no_other_file(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """One asset at a time. The sweep queues one job per file and each job forgets its own; a call
    that reached past its own asset would delete a neighbour's only clip."""
    old = {"pieces": 5, "each_ms": 2000}
    now = {"pieces": 6, "each_ms": 1500}
    ids = []
    for fixture, name in (("accepted.mp4", "one.mp4"), ("accepted.mkv", "two.mkv")):
        made = await content_store.ingest(
            checked(place(fixture, library_root, name), settings),
            root_id=library_root.id,
            rel_path=name,
        )
        await _preview(content_store, made.asset.id, old)
        ids.append(made.asset.id)

    assert await content_store.forget_superseded_previews(ids[0], now) != []

    assert await content_store.previews_of_another_recipe(now) == [ids[1]]


async def test_an_empty_library_answers_zero_clips_to_rebuild(content_store: ContentStore) -> None:
    """A bare aggregate answers on an empty table as readily as on a full one, and read as "no row"
    it would be a zero arrived at by accident."""
    assert await content_store.previews_of_another_recipe_count({"pieces": 6, "each_ms": 1500}) == 0


def test_a_settling_read_refuses_a_descriptor_that_is_not_a_regular_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same guard as the gate's, one layer in, and asked the same way.

    A named pipe read like a file blocks until somebody writes to it, and nobody is going to. The
    situation cannot be arranged on Windows; the question the code asks can be, so it is.
    """
    ordinary = tmp_path / "clip.mp4"
    ordinary.write_bytes(b"\x00" * 64)
    monkeypatch.setattr(os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFIFO, st_size=64))

    with pytest.raises(hashing.FileStillChanging, match="not a regular file"):
        hashing._digest_settled(ordinary, 64)


def test_an_identity_read_refuses_a_descriptor_that_is_not_a_regular_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sampled read asks the same question of its descriptor as the whole-file read, and for
    the same reason: a seek into a pipe is not a seek, and a read of one never returns."""
    ordinary = tmp_path / "clip.mp4"
    ordinary.write_bytes(b"\x00" * 64)
    monkeypatch.setattr(os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFIFO, st_size=64))

    with pytest.raises(hashing.FileStillChanging, match="not a regular file"):
        hashing._identity_settled(ordinary, 64)


def test_the_cache_digest_of_something_that_is_not_a_regular_file_is_no_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No digest reads as "serve this the careful way", which is the safe answer for anything the
    reader cannot treat as a file."""
    ordinary = tmp_path / "thumb.jpg"
    ordinary.write_bytes(b"\x00" * 16)
    monkeypatch.setattr(os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFIFO))

    assert hashing._digest_cache_file(ordinary) is None


@pytest.mark.integration
async def test_every_heif_still_is_listed_with_when_its_whole_picture_was_made(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A result older than its whole-picture copy was read from one tile, so the passes that
    read it again need the copy's time, and None for a still that has no copy yet."""
    heic = await content_store.ingest(
        checked(place("accepted.heic", library_root, "phone.heic"), settings),
        root_id=library_root.id,
        rel_path="phone.heic",
    )
    await content_store.ingest(
        checked(place("accepted.jpg", library_root, "photo.jpg"), settings),
        root_id=library_root.id,
        rel_path="photo.jpg",
    )

    assert await content_store.heif_stills() == [(heic.asset.id, None)]

    made = await _derivative(
        content_store, heic.asset.id, DerivativeKind.RENDITION, b"whole", extension="png"
    )
    assert await content_store.heif_stills() == [(heic.asset.id, made.created_at)]
    assert await content_store.heif_stills(after=heic.asset.id) == [], "a page after the last"


@pytest.mark.integration
async def test_the_totals_of_a_kind_are_its_count_and_its_bytes_and_nothing_else(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """What the Maintenance card says a clear would free, before anything is cleared."""
    one = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "one.mp4"), settings),
        root_id=library_root.id,
        rel_path="one.mp4",
    )
    two = await content_store.ingest(
        checked(place("accepted.webm", library_root, "two.webm"), settings),
        root_id=library_root.id,
        rel_path="two.webm",
    )
    await _derivative(content_store, one.asset.id, DerivativeKind.REMUX, b"abc", extension="mp4")
    await _derivative(content_store, two.asset.id, DerivativeKind.REMUX, b"abcde", extension="mp4")
    await _derivative(content_store, two.asset.id, DerivativeKind.THUMB, b"a thumbnail")

    assert await content_store.derivative_totals(DerivativeKind.REMUX) == (2, 8)
    assert await content_store.derivative_totals(DerivativeKind.PREVIEW) == (0, 0)
