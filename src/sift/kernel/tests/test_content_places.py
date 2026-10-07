# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a file sits: from an asset to a file on disk, the probe's answers, archives and folders, and the counts a pass divides by."""

from __future__ import annotations

import itertools
import os
import shutil
import zipfile
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.content import (
    identity,
    identity_arrivals,
    identity_places,
    identity_store,
)
from sift.kernel.content.hashing import (
    hash_file,
)
from sift.kernel.content.identity import (
    ContentStore,
    DerivativeKind,
    FolderMedia,
    VerdictProduct,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import (
    ALLOWED_MEDIA,
    CLASSIFIER_VERSION,
)
from sift.kernel.tests.content_helpers import (
    FIXTURES,
    GOLDEN,
    checked,
    corpus_survives,  # noqa: F401  (the corpus check, autouse)
    place,
)
from sift.testing.fixtures import LibraryRoot
from sift.testing.tools import POSIX_ONLY, WINDOWS_ONLY, junction

# --- From an asset to a file on disk --------------------------------------------------


@pytest.mark.integration
async def test_a_location_resolves_to_the_file_it_describes(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The only way from an asset to a path. Everything downstream (probing, thumbnailing,
    playing, saving) needs one, and none of them can work it out for itself: the root's absolute
    path is in a table nothing outside the kernel may read."""
    target = place("accepted.mp4", library_root, "clips/holiday.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clips/holiday.mp4"
    )

    resolved = await content_store.path_of(result.location)

    assert resolved == target
    assert resolved.read_bytes() == (FIXTURES / "accepted.mp4").read_bytes()


@pytest.mark.integration
@POSIX_ONLY
async def test_path_of_returns_the_symlink_followed_path(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The path handed back is the real, symlink-followed one, not the lexical path that was
    validated. A consumer opens that, so a directory component swapped for a symlink between the
    check and the open cannot redirect the read: the returned path has no symlink left to follow."""
    real_dir = library_root.path / "real"
    real_dir.mkdir()
    shutil.copy(FIXTURES / "accepted.mp4", real_dir / "clip.mp4")
    (library_root.path / "via_link").symlink_to(real_dir)  # a benign symlink dir inside the root

    through_link = library_root.path / "via_link" / "clip.mp4"
    result = await content_store.ingest(
        checked(through_link, settings), root_id=library_root.id, rel_path="via_link/clip.mp4"
    )

    resolved = await content_store.path_of(result.location)

    assert resolved == (real_dir / "clip.mp4").resolve()  # canonical, the symlink dir followed
    assert "via_link" not in str(resolved)  # not the lexical path that was checked


@pytest.mark.regression
async def test_a_path_written_before_the_guard_existed_is_still_refused(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Rows outlive the code that wrote them. A database from an older Sift (or a restored
    backup) can hold a `rel_path` that `check_rel_path` never saw, and this is exactly where a
    row that predates a guard meets the code that assumes it.

    So the path is validated again on the way out. Written here with a raw insert, because the
    whole point is that it is a row the front door would not have let in.
    """
    target = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(target, settings)
    asset, _ = await content_store.upsert_asset(
        digest=GOLDEN["accepted.mp4"], media=proof.media, size_bytes=proof.size
    )

    location_id = new_id()
    await temp_db.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename, "
        "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (location_id, asset.id, library_root.id, "../../../etc/passwd", "passwd", 1, 1),
    )

    smuggled = await content_store.location_at(library_root.id, "../../../etc/passwd")
    assert smuggled is not None

    with pytest.raises(ValueError, match="plain path"):
        await content_store.path_of(smuggled)


@pytest.mark.integration
@POSIX_ONLY
async def test_a_symlink_that_leaves_its_root_is_refused(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Any,
    tmp_path: Path,
) -> None:
    """A `..` is not the only way out of a root. A symlink that lives *inside* the root but points
    outside it is a legal relative path with no `..` in it, so the lexical guard waves it through.
    Only resolving the real path and confirming it is still under the root catches it, and it
    must, because serving it is a read of a file the folder ACL was never resolved against."""
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_bytes(b"not yours to serve")

    link = library_root.path / "sneaky.mp4"
    link.symlink_to(secret)

    target = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(target, settings)
    asset, _ = await content_store.upsert_asset(
        digest=GOLDEN["accepted.mp4"], media=proof.media, size_bytes=proof.size
    )
    await temp_db.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename, "
        "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), asset.id, library_root.id, "sneaky.mp4", "sneaky.mp4", 1, 1),
    )
    smuggled = await content_store.location_at(library_root.id, "sneaky.mp4")
    assert smuggled is not None

    with pytest.raises(ValueError, match="outside its library root"):
        await content_store.path_of(smuggled)


@WINDOWS_ONLY
async def test_a_junction_that_leaves_its_root_is_refused(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Any,
    tmp_path: Path,
) -> None:
    """The same escape, by the redirection an ordinary Windows account can actually make.

    Sharper here than the symbolic-link case above, because a junction reports
    `is_symlink() == False`: a guard that noticed links would wave this through, and only resolving
    the real path and finding it outside the root refuses it. Serving what is behind it would be a
    read of a file the folder rules were never resolved against.
    """
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "secret.txt").write_bytes(b"not yours to serve")
    junction(library_root.path / "sneaky", outside)

    target = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(target, settings)
    asset, _ = await content_store.upsert_asset(
        digest=GOLDEN["accepted.mp4"], media=proof.media, size_bytes=proof.size
    )
    await temp_db.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename, "
        "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), asset.id, library_root.id, "sneaky/secret.txt", "secret.txt", 1, 1),
    )
    smuggled = await content_store.location_at(library_root.id, "sneaky/secret.txt")
    assert smuggled is not None

    with pytest.raises(ValueError, match="outside its library root"):
        await content_store.path_of(smuggled)


async def test_a_location_whose_root_is_gone_says_so(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    orphan = replace(result.location, root_id=new_id())

    with pytest.raises(LookupError, match="no longer exists"):
        await content_store.path_of(orphan)


# --- the probe's results --------------------------------------------------------------------
#
# Everything an asset knows about itself past its digest is written here, once something has
# actually opened the file. The feature that does the opening lives elsewhere; this is the row it
# writes to, and these are the rules that hold whoever writes it to the model.


async def test_the_probe_fills_in_what_a_file_turned_out_to_be(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    assert result.asset.probed_at is None

    probed = await content_store.record_probe(
        result.asset.id,
        width=1920,
        height=1080,
        duration_ms=12_500,
        container="mp4",
        vcodec="h264",
        acodec="aac",
        color_transfer="smpte2084",
        phash="1e18fe18fe0681ab",
        videohash="1e18fe18fe0681ab" * 30,
    )

    assert probed is not None
    assert probed.color_transfer == "smpte2084"
    assert (probed.width, probed.height) == (1920, 1080)
    assert probed.duration_ms == 12_500
    assert (probed.container, probed.vcodec, probed.acodec) == ("mp4", "h264", "aac")
    assert probed.probed_at is not None
    # Read back, not just returned: the point is what is in the row.
    stored = await content_store.get(result.asset.id)
    assert stored == probed
    # And the one question every caller actually asks of that column. UNKNOWN IS NOT HDR, which
    # is the direction that matters: mapping an ordinary picture darkens it, so the map is applied
    # only to a file that plainly needs it, and a file nobody has read the transfer of is not
    # one of those.
    assert probed.is_hdr is True
    assert replace(probed, color_transfer="SMPTE2084").is_hdr is True, "the probe's case varies"
    assert replace(probed, color_transfer="arib-std-b67").is_hdr is True
    assert replace(probed, color_transfer="bt709").is_hdr is False
    assert replace(probed, color_transfer=None).is_hdr is False


async def test_the_probe_does_not_re_derive_what_the_gate_already_decided(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Size, type, and the name it was first seen under are set at ingest, from the gate. The
    probe cannot reach them, so a second opinion about what a file is cannot exist."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    probed = await content_store.record_probe(result.asset.id, width=64, height=64)

    assert probed is not None
    assert probed.media_type == result.asset.media_type
    assert probed.size_bytes == result.asset.size_bytes
    assert probed.original_filename == result.asset.original_filename
    assert probed.identity == result.asset.identity


async def test_a_re_probe_writes_what_it_found_and_not_what_it_did_not(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The probe owns these columns, so it is authoritative about them, including about a thing
    no longer being there. A file re-encoded without its audio has no audio codec, and saying so
    means writing the absence rather than leaving yesterday's answer in place."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    await content_store.record_probe(result.asset.id, acodec="aac", width=1920)

    probed = await content_store.record_probe(result.asset.id, acodec=None, width=640)

    assert probed is not None
    assert probed.acodec is None
    assert probed.width == 640


async def test_the_probe_leaves_the_gates_mime_alone_unless_it_disagrees(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The one column the probe may correct rather than own.

    The gate decided what the file is allowed to be, by reading its structure. The probe only
    speaks up when what it decoded disagrees, so passing nothing keeps the gate's answer, which
    is what a probe that found nothing surprising passes.
    """
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    assert result.asset.mime == "video/mp4"

    kept = await content_store.record_probe(result.asset.id, width=64)
    assert kept is not None
    assert kept.mime == "video/mp4"

    corrected = await content_store.record_probe(result.asset.id, mime="video/quicktime")
    assert corrected is not None
    assert corrected.mime == "video/quicktime"


async def test_probing_an_asset_that_is_gone_is_not_an_error(content_store: ContentStore) -> None:
    """A file deleted while it was being probed. Nothing was written, and the caller is a job that
    has already lost: there is nothing here worth raising about."""
    assert await content_store.record_probe(new_id(), width=64) is None


# --- which of these ids are still files -------------------------------------------------------


async def test_existing_ids_answers_which_of_a_list_are_still_in_the_library(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Asked by a feature that keeps its own table of asset ids and cannot carry a foreign key back
    to this one: SQLite takes none on a virtual table, so nothing tells it when a file is deleted
    and its rows are orphaned in silence.

    The answer is the ids that are STILL here rather than the ones that have gone, which is what
    makes it safe: a caller that mixed the two up would be handed the whole list and remove
    everything it had.
    """
    ingested = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
    )
    here = ingested.asset.id

    alive = await content_store.existing_ids([here, "01HX0000000000000000000ZZZ"])

    assert alive == {here}


async def test_existing_ids_asks_nothing_about_an_empty_list(content_store: ContentStore) -> None:
    """Empty in, empty out, without a round trip. A membership test built from no values is a
    syntax error in SQLite, so this is the guard rather than an optimisation."""
    assert await content_store.existing_ids([]) == set()


async def test_a_page_question_about_no_files_asks_nothing(content_store: ContentStore) -> None:
    """The same guard on every question a Build asks about a page of ids it already holds. A
    page can be empty (the last one usually is), and the answer is the empty set, without a
    statement SQLite would refuse."""
    assert await content_store.verdicted_among(VerdictProduct.THUMBNAILS, []) == set()
    assert await content_store.lacking_derivative(DerivativeKind.THUMB, []) == set()
    assert await content_store.unfingerprinted_among([]) == set()


async def test_existing_ids_answers_a_long_list_in_one_statement(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The whole reason it is one bound JSON array rather than a run of placeholders: a sweep over
    a whole index hands it every id the index holds, and SQLite has a hard ceiling on how many
    parameters one statement may carry."""
    ingested = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
    )
    gone = [f"01HX{index:022d}" for index in range(2000)]

    alive = await content_store.existing_ids([*gone, ingested.asset.id])

    assert alive == {ingested.asset.id}


def _gallery(root: LibraryRoot, rel_path: str, members: dict[str, bytes]) -> Path:
    """A real `.zip` sitting in a library root, holding real pictures."""
    target = root.path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as writing:
        for name, body in members.items():
            writing.writestr(name, body)
    return target


async def _picture_in_an_archive(
    store: ContentStore, root: LibraryRoot, settings: Any, tmp_path: Path
) -> tuple[str, Any]:
    """One picture indexed as living inside `galleries/shoot.zip`. Returns its id and location."""
    picture = (FIXTURES / "accepted.jpg").read_bytes()
    _gallery(root, "galleries/shoot.zip", {"001.jpg": picture})
    scratch = tmp_path / "scratch" / "001.jpg"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_bytes(picture)
    ingested = await store.ingest(
        checked(scratch, settings),
        root_id=root.id,
        rel_path="galleries/shoot.zip/001.jpg",
        archive_rel_path="galleries/shoot.zip",
        member_path="001.jpg",
    )
    return ingested.asset.id, ingested.location


async def test_a_picture_inside_an_archive_is_written_out_the_first_time_it_is_read(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any, tmp_path: Path
) -> None:
    """The whole of what makes an archive readable, and the promise it must not break.

    The bytes come back, they are the member's, and the copy is in the CACHE, never beside the
    original, which Sift has only ever promised to read.
    """
    asset_id, location = await _picture_in_an_archive(
        content_store, library_root, settings, tmp_path
    )

    produced = await content_store.path_of(location)

    assert produced.read_bytes() == (FIXTURES / "accepted.jpg").read_bytes()
    assert produced.is_relative_to(settings.cache_dir)
    assert asset_id in str(produced), "filed under the asset's id, so a copy cannot be stale"
    assert sorted(path.name for path in (library_root.path / "galleries").iterdir()) == [
        "shoot.zip"
    ], "something was unpacked next to the archive"


async def test_reading_the_same_picture_again_opens_the_copy_rather_than_the_archive(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any, tmp_path: Path
) -> None:
    """A wall of one shoot asks for the same pictures over and over. Proved by taking the archive
    away: a second read that still answers cannot have opened it."""
    _, location = await _picture_in_an_archive(content_store, library_root, settings, tmp_path)
    first = await content_store.path_of(location)
    (library_root.path / "galleries" / "shoot.zip").unlink()

    again = await content_store.path_of(location)

    assert again == first
    assert again.read_bytes() == (FIXTURES / "accepted.jpg").read_bytes()


async def test_an_ordinary_file_is_not_taken_through_the_archive_path(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The other side of the branch, and the one that must stay free: a file on disk is answered
    with the file on disk, and nothing is written anywhere."""
    target = place("accepted.jpg", library_root, "pictures/one.jpg")
    ingested = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="pictures/one.jpg"
    )

    assert await content_store.path_of(ingested.location) == target
    assert not (settings.cache_dir / ContentStore.ARCHIVE_CACHE).exists()


def test_the_archive_cache_drops_what_has_not_been_read_and_keeps_what_has(
    tmp_path: Path,
) -> None:
    """The cap, at a budget small enough to bite.

    The file that was just asked for is kept whatever the arithmetic says: a cap able to delete
    the thing it was called to produce is an intermittent missing picture rather than a size limit.
    And the directory a picture sat in goes with it, or an archive read once leaves an empty shell
    behind for ever.
    """
    cache = tmp_path / "archives"
    for name, when in (("old", 1_600_000_000), ("new", 1_700_000_000)):
        member = cache / name / "001.jpg"
        member.parent.mkdir(parents=True)
        member.write_bytes(b"x" * 400)
        os.utime(member, (when, when))
    wanted = cache / "wanted" / "001.jpg"
    wanted.parent.mkdir(parents=True)
    wanted.write_bytes(b"x" * 400)
    os.utime(wanted, (1_500_000_000, 1_500_000_000))

    identity._keep_under_budget(cache, 800, wanted)

    assert wanted.is_file(), "the cap deleted the picture it was called to make room for"
    assert not (cache / "old").exists(), "the oldest was kept and its empty directory with it"
    assert (cache / "new" / "001.jpg").is_file()


def test_a_cache_over_budget_with_nothing_it_may_drop_is_left_over_budget(tmp_path: Path) -> None:
    """Everything here is best-effort, and this is the shape that proves it.

    The only picture there is the one that was just asked for, so the cap runs out of things it is
    allowed to remove. Being briefly larger than asked for is the intended answer; deleting the
    picture a caller is about to open is not.
    """
    cache = tmp_path / "archives"
    wanted = cache / "one" / "001.jpg"
    wanted.parent.mkdir(parents=True)
    wanted.write_bytes(b"x" * 400)

    identity._keep_under_budget(cache, 100, wanted)

    assert wanted.is_file()


def test_a_cache_already_inside_its_budget_is_left_alone(tmp_path: Path) -> None:
    """The common case by a long way, and the one where doing anything is a bug."""
    cache = tmp_path / "archives"
    member = cache / "one" / "001.jpg"
    member.parent.mkdir(parents=True)
    member.write_bytes(b"x" * 10)

    identity._keep_under_budget(cache, 1_000, cache / "nothing")

    assert member.is_file()


# --- what a folder holds ------------------------------------------------------------------------


async def test_the_stills_among_a_list_come_back_in_the_order_they_were_asked_for(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A gallery arrives numbered, and a set built in whatever order SQLite answered in would be
    that shoot shuffled. So the answer is re-emitted from the caller's list, not read out of the
    rows, and a video in the list is dropped rather than reordered."""
    picture = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "shoot/2.jpg"), settings),
        root_id=library_root.id,
        rel_path="shoot/2.jpg",
    )
    animation = await content_store.ingest(
        checked(place("accepted.gif", library_root, "shoot/1.gif"), settings),
        root_id=library_root.id,
        rel_path="shoot/1.gif",
    )
    video = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "shoot/3.mp4"), settings),
        root_id=library_root.id,
        rel_path="shoot/3.mp4",
    )

    asked = [picture.asset.id, video.asset.id, animation.asset.id]
    assert await content_store.stills_among(asked) == [picture.asset.id, animation.asset.id]
    assert await content_store.stills_among([]) == [], "an empty list asks the database nothing"


async def test_a_folder_reports_its_pictures_in_name_order_and_counts_what_moves(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """What the rule about turning a folder of pictures into a shoot reads.

    Name order because that is the order a shoot was numbered in; `added_at` would order by
    whichever file finished being written first, which for a folder that arrived together is a
    shuffle.
    """
    folder_id = new_id()
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (folder_id, library_root.id, "shoot", "shoot"),
    )
    second = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "shoot/b.jpg"), settings),
        root_id=library_root.id,
        rel_path="shoot/b.jpg",
        folder_id=folder_id,
    )
    first = await content_store.ingest(
        checked(place("accepted.gif", library_root, "shoot/a.gif"), settings),
        root_id=library_root.id,
        rel_path="shoot/a.gif",
        folder_id=folder_id,
    )
    await content_store.ingest(
        checked(place("accepted.mp4", library_root, "shoot/c.mp4"), settings),
        root_id=library_root.id,
        rel_path="shoot/c.mp4",
        folder_id=folder_id,
    )

    held = await content_store.folder_media(folder_id)

    assert held.still_ids == [first.asset.id, second.asset.id]
    assert held.moving == 1
    assert await content_store.folder_media(new_id()) == FolderMedia(still_ids=[], moving=0)


async def test_a_folder_holds_only_its_own_files_that_are_there(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """A picture inside a ZIP carries the folder the ZIP sits in and belongs to the archive's set,
    and a copy marked missing is not in the folder now: neither is counted as the folder's own."""
    folder_id = new_id()
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (folder_id, library_root.id, "shoot", "shoot"),
    )
    own = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "shoot/a.jpg"), settings),
        root_id=library_root.id,
        rel_path="shoot/a.jpg",
        folder_id=folder_id,
    )
    await content_store.ingest(
        checked(place("accepted.png", library_root, "shoot/member.png"), settings),
        root_id=library_root.id,
        rel_path="shoot/gallery.zip/member.png",
        folder_id=folder_id,
        archive_rel_path="shoot/gallery.zip",
        member_path="member.png",
    )
    gone = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "shoot/c.mp4"), settings),
        root_id=library_root.id,
        rel_path="shoot/c.mp4",
        folder_id=folder_id,
    )
    assert await content_store.mark_missing(gone.location.id)

    held = await content_store.folder_media(folder_id)

    assert held == FolderMedia(still_ids=[own.asset.id], moving=0)


# --- rows a change to the classifier has not reached yet -----------------------------------------
#
# A change to what the ingress classifier answers reaches new files and never the ones already in
# the library: a scan skips a file whose path, size and mtime are unchanged. The row says which
# generation typed it (`classified_version`), and the reclassify pass reads the rows below the one
# in use, such as an animated AVIF an older gate filed as a HEIC photograph.


@pytest.mark.integration
async def test_a_file_taken_in_now_is_stamped_with_the_classifier_that_typed_it(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any, temp_db: Database
) -> None:
    """Bound by the insert, never left to the column's default: the gate that typed this file is
    the classifier in use, and only a row below it is offered to the pass."""
    store = content_store
    current = await store.ingest(
        checked(place("accepted.png", library_root, "holiday.png"), settings),
        root_id=library_root.id,
        rel_path="holiday.png",
    )
    older = await store.ingest(
        checked(place("accepted.jpg", library_root, "motion.avif"), settings),
        root_id=library_root.id,
        rel_path="motion.avif",
    )
    assert current.asset.classified_version == CLASSIFIER_VERSION
    await temp_db.execute(
        "UPDATE assets SET classified_version = ? WHERE id = ?",
        (CLASSIFIER_VERSION - 1, older.asset.id),
    )

    assert await store.unclassified(10) == [older.asset.id]


@pytest.mark.integration
async def test_correcting_what_a_file_IS_leaves_everything_else_it_knows_alone(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any, temp_db: Database
) -> None:
    """The type, the MIME, the container and the generation, and no others. The probe's own
    statement writes a dozen more (the fingerprints, the dimensions, the codecs) and asking it
    for these would blank every one of them, on a file where none of them was in question."""
    store = content_store
    ingested = await store.ingest(
        checked(place("accepted.jpg", library_root, "motion.avif"), settings),
        root_id=library_root.id,
        rel_path="motion.avif",
    )
    await store.record_probe(ingested.asset.id, width=1440, height=1800, phash="abcdef01")
    await temp_db.execute(
        "UPDATE assets SET classified_version = 0 WHERE id = ?", (ingested.asset.id,)
    )
    sequence = next(media for media in ALLOWED_MEDIA if media.name == "avif-sequence")

    await store.reclassify(ingested.asset.id, sequence)

    after = await store.get(ingested.asset.id)
    assert after is not None
    assert (after.media_type, after.mime) == ("gif", "image/avif")
    assert after.classified_version == CLASSIFIER_VERSION
    assert (after.width, after.height) == (1440, 1800), "correcting the type blanked the shape"
    assert after.phash == "abcdef01", "correcting the type blanked the fingerprint"
    row = await temp_db.fetch_one("SELECT container FROM assets WHERE id = ?", (ingested.asset.id,))
    assert row is not None and row["container"] == "avif-sequence"


@pytest.mark.integration
async def test_a_file_the_classifier_now_refuses_is_stamped_and_nothing_else(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any, temp_db: Database
) -> None:
    """Read, so stamped (or the pass would read it on every start for ever), and otherwise left
    exactly as it was: quarantining somebody's file is not a correction of its type."""
    store = content_store
    ingested = await store.ingest(
        checked(place("accepted.jpg", library_root, "holiday.heic"), settings),
        root_id=library_root.id,
        rel_path="holiday.heic",
    )
    await temp_db.execute(
        "UPDATE assets SET mime = 'image/heic', classified_version = 0 WHERE id = ?",
        (ingested.asset.id,),
    )

    await store.reclassify(ingested.asset.id, None)

    after = await store.get(ingested.asset.id)
    assert after is not None
    assert (after.media_type, after.mime) == ("image", "image/heic")
    assert await store.unclassified(10) == []


# --- the four counters a feature divides by, and nothing counted with ---------------------------
#
# Each is one statement behind a docstring explaining why a screen needs it, and each is a bar's
# denominator: "how many files are left" cannot be answered by the feature that knows how many it
# has done, because a feature may not ask the asset table. Asked here directly, a wrong count is a
# failing number rather than tiles that never stop saying they are importing.


async def test_reading_a_file_is_announced_the_way_an_arrival_is(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A player left waiting on a file nobody has read asks again when it hears the file move,
    and the read is what makes it playable, so the read has to be heard."""
    heard: list[object] = []

    async def hear(connection: object) -> None:
        heard.append(connection)

    for module in (identity_arrivals, identity_places, identity_store):
        monkeypatch.setattr(module, "announce_arrival", hear)
    place("accepted.mp4", library_root, "clips/read-me.mp4")
    taken = await content_store.ingest(
        checked(library_root.path / "clips/read-me.mp4", settings),
        root_id=library_root.id,
        rel_path="clips/read-me.mp4",
    )
    heard.clear()

    await content_store.record_probe(taken.asset.id, width=64, height=48, duration_ms=1000)

    assert len(heard) == 1
    assert await content_store.record_probe("no-such-asset", width=1) is None
    assert len(heard) == 1, "a probe of nothing is not news"


async def test_the_counters_answer_over_a_real_library(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """One file, taken in and not yet read, counted each way.

    Asserted together because they are read together and their DIFFERENCES are the point: a file
    the library holds and has never read is in the library's count and the unread count, and
    leaves the second once it is read.
    """
    place("accepted.mp4", library_root, "clips/holiday.mp4")
    one = await content_store.ingest(
        checked(library_root.path / "clips/holiday.mp4", settings),
        root_id=library_root.id,
        rel_path="clips/holiday.mp4",
    )

    assert await content_store.asset_count() == 1
    # Taken in and not read: the state a stopped queue or a closed application leaves behind, and
    # the one with no visible symptom except a tile that never stops saying it is importing.
    assert await content_store.unread_count() == 1

    await content_store.record_probe(one.asset.id, width=64, height=48, duration_ms=1000)
    assert await content_store.unread_count() == 0


async def test_hashing_a_file_takes_its_storage_lane(
    tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hash reads the whole file end to end (the heaviest read Sift makes of one), and a
    share asked to serve a dozen of those at the same time collapses. The lane is what stops that,
    so a hash that went round it would be exactly the reader the cap cannot see."""
    from contextlib import asynccontextmanager

    from sift.kernel import lanes

    asked: list[Path] = []

    @asynccontextmanager
    async def counting(path: Path) -> AsyncIterator[None]:
        asked.append(path)
        yield

    monkeypatch.setattr(lanes, "reading", counting)
    target = tmp_path / "accepted.jpg"
    shutil.copy(FIXTURES / "accepted.jpg", target)

    await hash_file(checked(target, settings))

    assert asked == [target]


# --- the whole-library reads a migration matches another library against ------------------------


async def _two_files(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> tuple[Any, Any, Any]:
    """A clip at two places and a photograph at one: two files, three locations."""
    clip = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clips/Holiday.mp4"), settings),
        root_id=library_root.id,
        rel_path="clips/Holiday.mp4",
    )
    again = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "copy.mp4"), settings),
        root_id=library_root.id,
        rel_path="copy.mp4",
    )
    photo = await content_store.ingest(
        checked(place("accepted.jpg", library_root, "photo.jpg"), settings),
        root_id=library_root.id,
        rel_path="photo.jpg",
    )
    assert again.asset.id == clip.asset.id
    return clip, again, photo


@pytest.mark.integration
async def test_every_location_is_every_place_every_file_sits_grouped_by_file(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    clip, again, photo = await _two_files(content_store, library_root, settings)

    every = await content_store.every_location()

    groups = [len(list(group)) for _, group in itertools.groupby(every, lambda one: one.asset_id)]
    assert sorted(groups) == [1, 2], "each file's places together"
    assert {one.id for one in every} == {clip.location.id, again.location.id, photo.location.id}
    assert [one.asset_id for one in every] == sorted(one.asset_id for one in every)


@pytest.mark.integration
async def test_the_carriers_of_an_identity_are_found_by_hash_and_by_place_in_any_case(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """Another library's hashes and paths are matched as this one keeps them, whatever case
    either wrote them in; nothing asked is nothing read."""
    clip, _again, photo = await _two_files(content_store, library_root, settings)
    await temp_db.execute(
        "UPDATE assets SET oshash = 'abcdef0123456789', video_phash = 'feedface' WHERE id = ?",
        (clip.asset.id,),
    )

    assert await content_store.carriers_of() == []
    found = await content_store.carriers_of(
        oshashes=["ABCDEF0123456789", "0000000000000000"],
        phashes=["FEEDFACE"],
        places=[(library_root.id, "PHOTO.JPG"), (library_root.id, "nowhere.jpg")],
    )

    assert sorted((one.kind, one.key, one.rel_path, one.asset_id) for one in found) == [
        ("oshash", "abcdef0123456789", None, clip.asset.id),
        ("phash", "feedface", None, clip.asset.id),
        ("place", None, "photo.jpg", photo.asset.id),
    ]


@pytest.mark.integration
async def test_every_fingerprint_is_every_file_with_either_hash_and_no_other(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    clip, _again, photo = await _two_files(content_store, library_root, settings)
    await temp_db.execute("UPDATE assets SET oshash = NULL, video_phash = NULL")
    await temp_db.execute(
        "UPDATE assets SET video_phash = 'feedface' WHERE id = ?", (clip.asset.id,)
    )

    assert await content_store.every_fingerprint() == [(clip.asset.id, None, "feedface")]
    assert photo.asset.id not in {one for one, _, _ in await content_store.every_fingerprint()}


@pytest.mark.integration
async def test_a_file_its_own_bytes_refute_goes_back_below_the_classifier_line(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The reclassify pass reads rows below the line, so this is how a kind its own bytes disproved is
    read again and its kind and type written together from one answer."""
    _clip, _again, photo = await _two_files(content_store, library_root, settings)

    await content_store.send_to_classifier(photo.asset.id)

    row = await temp_db.fetch_one(
        "SELECT classified_version FROM assets WHERE id = ?", (photo.asset.id,)
    )
    assert row is not None and row["classified_version"] == 0
    assert row["classified_version"] < CLASSIFIER_VERSION


@pytest.mark.integration
async def test_the_moment_a_tiles_still_was_cut_at_is_kept_on_the_file(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The hover clip starts where the still was cut, so the tile does not jump under a pointer."""
    clip, _again, _photo = await _two_files(content_store, library_root, settings)

    await content_store.record_still_moment(clip.asset.id, 4_250)

    record = await content_store.get(clip.asset.id)
    assert record is not None and record.still_at_ms == 4_250
