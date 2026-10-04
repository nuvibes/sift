# SPDX-License-Identifier: AGPL-3.0-or-later
"""A ZIP of pictures, found by a scan and indexed where it lies.

The unit tests for the archive gate are in `kernel/tests/test_archives.py`: refusals, caps and
zip-slip. What is asserted here is the thing only a real scan can settle: that a `.zip` produces
ASSETS and no second copy on disk, that the archive itself never becomes one, and that a picture
inside it can be got back afterwards through the one function everything reads files with.
"""

from __future__ import annotations

import asyncio
import os
import time
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from sift.kernel.archives import ArchiveRefused, extract_member
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LocationStatus, Root
from sift.kernel.jobs import JobContext, JobQueue
from sift.slices.library_roots import jobs, taking_in
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer, draw
from sift.testing.logs import uncached_log

Context = Callable[[str, dict[str, object]], Awaitable[JobContext]]


def gallery(where: Path, names: list[str], scratch: Path) -> Path:
    """An archive of real, decodable pictures.

    Real ones rather than made-up bytes, because every member goes through the same ingress gate a
    downloaded file does (head, tail and ffmpeg), so an archive of plausible-looking rubbish
    would test the refusal path and quietly prove nothing about the accepting one.
    """
    where.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(where, "w", compression=zipfile.ZIP_DEFLATED) as writing:
        for index, name in enumerate(names):
            made = scratch / f"{index}-{name}"
            # A different SIZE per picture, in steps of eight. Adjacent odd sizes can come out
            # byte-identical, which Sift correctly indexes as one asset in two places, so the test
            # would count two pictures and look like a bug in the archive code. Identity here is
            # the content, and a fixture has to respect that.
            draw(made, f"testsrc2=size={64 + index * 8}x48:rate=1")
            writing.write(made, name)
            made.unlink()
    return where


async def _scan(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    *,
    archive_settled: jobs.ArchiveSettled | None = None,
) -> None:
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(
        context,
        settings=settings,
        service=service,
        reindexer=reindexer,
        archive_settled=archive_settled,
    )


async def test_a_zip_of_pictures_becomes_pictures_and_never_an_asset_of_its_own(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """The whole feature in one assertion pair.

    Three pictures come out of the archive as three assets. The archive is not a fourth: a tile of a
    `.zip` is a row nobody can open, sitting between rows they can, and
    the right one.
    """
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)

    locations = []
    for asset_id in reindexer.told:
        locations.extend(await content_store.locations(asset_id))
    assert len(reindexer.told) == 3
    assert {Path(one.rel_path).name for one in locations} == {"01.png", "02.png", "03.png"}
    assert all(one.inside_an_archive for one in locations)
    assert all(one.archive_rel_path == "shoot.zip" for one in locations)
    assert await content_store.location_at(root.id, "shoot.zip") is None, (
        "the archive itself was indexed as a file"
    )


async def test_nothing_is_unpacked_and_the_library_is_untouched(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The promise this design is built on: no second copy of thousands of pictures.

    Two halves. The library is byte-for-byte what it was, which is the promise every scan makes;
    and the CACHE holds no full-size pictures either, because each member is pulled out to a
    scratch file, verified, and deleted again. A version that kept them would pass the first half
    and fail this.
    """
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    before = {
        path.relative_to(root_path): path.read_bytes()
        for path in sorted(root_path.rglob("*"))
        if path.is_file()
    }
    await _scan(context_for, root, settings, service, reindexer)

    after = {
        path.relative_to(root_path): path.read_bytes()
        for path in sorted(root_path.rglob("*"))
        if path.is_file()
    }
    assert after == before

    kept = list((settings.cache_dir / "incoming").rglob("*")) if settings.cache_dir.exists() else []
    assert [one for one in kept if one.is_file()] == [], "a member was left behind in the cache"
    materialised = settings.cache_dir / ContentStore.ARCHIVE_CACHE
    assert not materialised.exists(), "pictures were materialised before anybody looked at one"


async def test_a_picture_is_pulled_back_out_on_demand_and_then_kept(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """`path_of` is the one way anything reads a file, so this is what makes probing, thumbnailing
    and serving work for a picture inside an archive without any of them knowing what a `.zip` is.

    Asked twice on purpose. The second answer must be the same path and must not have gone back to
    the archive: the cached copy is filed under the asset's id, and an asset id in Sift is minted
    per set of bytes, so a copy under that name cannot be stale.
    """
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)

    location = (await content_store.locations(reindexer.told[0]))[0]
    first = await content_store.path_of(location)
    assert first.is_file()
    assert first.read_bytes()[:4] == b"\x89PNG"
    assert settings.cache_dir in first.parents

    marker = b"\x89PNG" + b"already-here"
    first.write_bytes(marker)
    again = await content_store.path_of(location)
    assert again == first
    assert again.read_bytes() == marker, "it went back to the archive for something already cached"


async def test_the_pictures_pulled_out_of_archives_cannot_fill_the_cache(
    monkeypatch: pytest.MonkeyPatch,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """The cap that makes "indexed in place" true rather than nearly true.

    Nothing is materialised by the scan itself, but PROBING is the first thing to read a
    picture, and it reads every one. Uncapped, a library of archives ends up holding a full copy of
    every picture inside them, in the cache instead of the library, which is the second copy the
    whole design refuses. It would have looked fine in every other test here.

    The budget is made tiny so three small pictures cross it. What must hold is that the total comes
    down and that the picture just asked for is still there: a cap that can delete the file it was
    called to produce is an intermittent missing picture rather than a size limit.
    """
    # A budget no single picture can fit in, so what is left is exactly what the rule protects: the
    # one that was just asked for, and nothing else. A budget of "about two pictures" would depend
    # on how large the fixtures happen to compress to, which is a test that passes by coincidence.
    monkeypatch.setattr(ContentStore, "ARCHIVE_CACHE_BUDGET", 1)
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)

    last = None
    for asset_id in reindexer.told:
        last = await content_store.path_of((await content_store.locations(asset_id))[0])

    assert last is not None and last.is_file(), "the cap deleted the picture it was asked for"
    held = [
        one for one in (settings.cache_dir / ContentStore.ARCHIVE_CACHE).rglob("*") if one.is_file()
    ]
    assert held == [last], "everything but the picture just asked for should have been dropped"


async def test_the_archive_is_offered_for_grouping_once_with_its_pictures_in_order(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """Once per archive, never per picture: a set built as the walk went would briefly be a set of
    one. The order is the archive's own, because a shoot shown shuffled is a different shoot."""
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    settled: list[tuple[str, str, str, list[str]]] = []

    async def remember(root_id: str, rel_path: str, name: str, asset_ids: list[str]) -> None:
        settled.append((root_id, rel_path, name, asset_ids))

    await _scan(context_for, root, settings, service, reindexer, archive_settled=remember)

    assert len(settled) == 1
    root_id, rel_path, name, asset_ids = settled[0]
    assert (rel_path, name) == ("shoot.zip", "shoot")
    # Which library, as well as where in it. A path alone does not identify an archive (two
    # libraries can each hold a `galleries/482615.zip`), and the listener behind this keys a photo
    # set on the pair. Told only the path, it would have nothing to key on at all and would make a
    # new set on every scan.
    assert root_id == root.id
    assert asset_ids == reindexer.told


async def test_a_grouping_that_fails_does_not_take_the_scan_down(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """A photo set is a convenience; an indexed library is not. A scan that walked a whole library
    and then died on the way out would be re-run, re-walking all of it, for a grouping."""
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)

    async def refuses(root_id: str, rel_path: str, name: str, asset_ids: list[str]) -> None:
        raise RuntimeError("no")

    await _scan(context_for, root, settings, service, reindexer, archive_settled=refuses)
    assert len(reindexer.told) == 3


async def test_a_grouping_that_fails_says_why_in_the_log(
    monkeypatch: pytest.MonkeyPatch,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The warning carries the refusal's own words: a line that only says it failed sends the
    next reader to reproduce it."""
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)

    async def refuses(root_id: str, rel_path: str, name: str, asset_ids: list[str]) -> None:
        raise RuntimeError("the set could not be made")

    uncached_log(monkeypatch, taking_in)
    with capture_logs() as logs:
        await _scan(context_for, root, settings, service, reindexer, archive_settled=refuses)

    (warning,) = [one for one in logs if one["event"] == "library.archive_settle_failed"]
    assert warning["error"] == "the set could not be made"
    assert warning["error_kind"] == "RuntimeError"


async def test_two_scans_meeting_one_archive_take_it_in_once(
    monkeypatch: pytest.MonkeyPatch,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The watcher's scan and a pressed Scan can meet the same ZIP at the same moment. Each picture
    comes out of it once, and the grouping is never asked twice at once: two at once would each
    find no set and each make one."""
    where = gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    stale = time.time() - 3600
    os.utime(where, (stale, stale))
    pulled: list[str] = []
    original = extract_member

    def counting(archive: Path, member: str, *args: object, **kwargs: object) -> object:
        pulled.append(member)
        return original(archive, member, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(taking_in, "extract_member", counting)
    grouping = 0
    groupings: list[list[str]] = []

    async def remember(root_id: str, rel_path: str, name: str, asset_ids: list[str]) -> None:
        nonlocal grouping
        grouping += 1
        assert grouping == 1, "two scans asked for the same set at once"
        await asyncio.sleep(0.05)
        groupings.append(asset_ids)
        grouping -= 1

    await asyncio.gather(
        _scan(context_for, root, settings, service, reindexer, archive_settled=remember),
        _scan(context_for, root, settings, service, reindexer, archive_settled=remember),
    )

    assert sorted(pulled) == ["01.png", "02.png", "03.png"]
    assert len(reindexer.told) == 3
    assert groupings == [reindexer.told, reindexer.told]
    assert taking_in._IN_HAND == {}, "a finished take-in kept its claim"


async def test_an_unchanged_archive_is_offered_for_grouping_again_on_the_next_scan(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """As a folder is: a ZIP taken in while its grouping was off becomes a set on a later scan,
    though not one of its pictures is read again."""
    where = gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    stale = time.time() - 3600
    os.utime(where, (stale, stale))
    await _scan(context_for, root, settings, service, reindexer)
    settled: list[list[str]] = []

    async def remember(root_id: str, rel_path: str, name: str, asset_ids: list[str]) -> None:
        settled.append(asset_ids)

    await _scan(context_for, root, settings, service, reindexer, archive_settled=remember)

    assert settled == [reindexer.told]
    assert len(reindexer.told) == 3


async def test_a_second_scan_takes_nothing_in_and_opens_nothing(
    monkeypatch: pytest.MonkeyPatch,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """An unchanged archive must cost a directory entry and a read of its index, and nothing else.

    Every member borrows the ARCHIVE's modification time, which is what lets each one answer the
    same "unchanged since last scan" question an ordinary file answers, without a byte being
    decompressed. Without that, every scan of a library would re-extract every picture in it.
    """
    where = gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    # An archive put there a while ago, which is every archive in a real library. A member's
    # location is recorded with the ARCHIVE's mtime; written with the extracted copy's instead it
    # agrees only when the extraction lands in the same second the archive was written, which is
    # true of a zip a test has just made and of nothing else. The check would then answer "changed"
    # on every later pass and every picture would come out again.
    stale = time.time() - 3600
    os.utime(where, (stale, stale))

    await _scan(context_for, root, settings, service, reindexer)
    assert len(reindexer.told) == 3

    # Nothing taken in is necessary and not sufficient: re-extracting every picture would ALSO
    # report nothing new, because identity is the content and the same bytes are the same asset.
    # So the second pass runs with extraction made impossible: if it reaches for a single member
    # this raises, and a test that only counted assets would have passed either way.
    def refuses(*args: object, **kwargs: object) -> int:
        raise AssertionError("an unchanged archive was opened and read again")

    monkeypatch.setattr(taking_in, "extract_member", refuses)
    reindexer.told.clear()
    await _scan(context_for, root, settings, service, reindexer)
    assert reindexer.told == []


async def test_deleting_the_archive_takes_its_pictures_off_the_wall(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """The sweep, for a picture inside an archive.

    "Is this still on disk?" must not be asked through `path_of`, which for a member PRODUCES a copy
    in the cache so something can read it: the sweep would write the picture out in order to look
    for it, and then find it, so a deleted archive would keep every picture in it on the wall for
    ever, pointing at a file that was gone.
    """
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)
    assert len(reindexer.told) == 3
    # Read one through `path_of` first, so there IS a cached copy for the sweep to be fooled by.
    await content_store.path_of((await content_store.locations(reindexer.told[0]))[0])

    (root_path / "shoot.zip").unlink()
    await _scan(context_for, root, settings, service, reindexer)

    for asset_id in reindexer.told:
        for location in await content_store.locations(asset_id):
            assert location.status is LocationStatus.MISSING, location.rel_path


async def test_deleting_the_archive_takes_its_pictures_off_the_wall_when_it_is_NAMED(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """The same guarantee, reached the way a running Sift actually reaches it.

    A change notification names a FILE ON THE DISK, and the sweep it drives is filtered to the paths
    that pass really looked at, because a pass that stat-ed three files may not conclude anything
    about a million rows it never examined. A picture out of an archive is not a file on the disk:
    its row is recorded at `shoot.zip/01.png`, and what was named is `shoot.zip`.

    Matched as a path, the archive would match no row, and deleting one through a watched folder
    would mark NOTHING missing: the pictures would stay on the wall, pointing at a file that had
    gone, until something happened to walk the whole folder. The test above proves the walk; this
    proves the narrow pass, which is the one that can answer differently.

    Looking at the archive IS looking at the pictures, which is what `_was_examined` says.
    """
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)
    assert len(reindexer.told) == 3

    (root_path / "shoot.zip").unlink()
    named = await context_for(jobs.SCAN, {"root_id": root.id, "paths": ["shoot.zip"]})
    await jobs.scan(named, settings=settings, service=service, reindexer=reindexer)

    for asset_id in reindexer.told:
        for location in await content_store.locations(asset_id):
            assert location.status is LocationStatus.MISSING, location.rel_path


async def test_a_picture_taken_out_of_an_archive_that_is_still_there_goes_missing_too(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """The other half, and the reason the check reads the archive's index rather than only asking
    whether the archive exists. One picture removed from a shoot somebody still has is a row that
    would otherwise sit on the wall opening to nothing."""
    gallery(root_path / "shoot.zip", ["01.png", "02.png", "03.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)
    kept = list(reindexer.told)

    # The same archive with one picture taken out of it.
    gallery(root_path / "shoot.zip", ["01.png", "02.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)

    missing = [
        location.rel_path
        for asset_id in kept
        for location in await content_store.locations(asset_id)
        if location.status is LocationStatus.MISSING
    ]
    assert len(missing) == 1, missing


async def test_an_archive_that_is_refused_leaves_the_rest_of_the_library_indexed(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """One bad archive is not a reason to index none of somebody's library."""
    (root_path / "broken.zip").write_bytes(b"not an archive at all")
    draw(root_path / "photo.png", "testsrc2=size=64x48:rate=1")

    await _scan(context_for, root, settings, service, reindexer)
    assert len(reindexer.told) == 1


async def test_a_video_inside_an_archive_is_left_alone(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """Playing one means materialising the whole file to seek around in, which is the second copy
    this design exists to avoid, and it would happen silently, the first time somebody pressed
    play on what looked like an ordinary video."""
    made = tmp_path / "clip.mp4"
    draw(made, "testsrc2=size=64x48:rate=5", 1)
    with zipfile.ZipFile(root_path / "mixed.zip", "w") as writing:
        writing.write(made, "clip.mp4")
        picture = tmp_path / "p.png"
        draw(picture, "testsrc2=size=64x48:rate=1")
        writing.write(picture, "a.png")

    await _scan(context_for, root, settings, service, reindexer)
    assert len(reindexer.told) == 1


@pytest.mark.parametrize("name", ["../escaped.png", "..\\escaped.png"])
async def test_an_archive_naming_a_file_outside_itself_indexes_nothing(
    name: str,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """Zip-slip, reaching the scan rather than the unit test. The whole archive is refused, so the
    ordinary-looking picture beside the malicious name is not indexed either."""
    good = tmp_path / "good.png"
    draw(good, "testsrc2=size=64x48:rate=1")
    with zipfile.ZipFile(root_path / "slip.zip", "w") as writing:
        writing.write(good, "good.png")
        writing.write(good, name)

    await _scan(context_for, root, settings, service, reindexer)
    assert reindexer.told == []
    assert not (root_path.parent / "escaped.png").exists()


async def test_an_archive_that_was_refused_once_is_not_opened_again(
    monkeypatch: pytest.MonkeyPatch,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """A bomb or a password-protected file is remembered against the ARCHIVE's own path, exactly as
    a rejected file is, so it is opened once rather than on every pass for ever.

    Proved by making opening impossible on the second pass: a version that re-inspected would raise
    here, and one that only counted assets would have passed either way.
    """
    (root_path / "broken.zip").write_bytes(b"not an archive at all")
    await _scan(context_for, root, settings, service, reindexer)
    assert reindexer.told == []

    def refuses(*args: object, **kwargs: object) -> list[object]:
        raise AssertionError("a refused archive was opened again")

    monkeypatch.setattr(taking_in, "inspect_archive", refuses)
    await _scan(context_for, root, settings, service, reindexer)
    assert reindexer.told == []


async def test_an_archive_holding_nothing_worth_taking_is_not_an_error(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """A `.zip` of documents, or of video, is a perfectly ordinary file to find in a library. It
    yields no pictures and it is not the archive's fault, so nothing is remembered against it."""
    made = tmp_path / "clip.mp4"
    draw(made, "testsrc2=size=64x48:rate=5", 1)
    with zipfile.ZipFile(root_path / "footage.zip", "w") as writing:
        writing.write(made, "clip.mp4")
        writing.writestr("readme.txt", "nothing here")

    await _scan(context_for, root, settings, service, reindexer)

    assert reindexer.told == []


async def test_a_member_that_will_not_come_out_leaves_the_rest_of_the_archive_indexed(
    monkeypatch: pytest.MonkeyPatch,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """One picture the gate on the way out refuses is one picture, not a shoot.

    The refusal is deliberately NOT remembered: a refusal memory is keyed on a path's size and
    modification time, and a member borrows the archive's, so filing one would match every other
    picture in that archive and take the whole shoot out on the next pass.
    """
    gallery(root_path / "shoot.zip", ["01.png", "02.png"], tmp_path)
    real = extract_member
    calls: list[str] = []

    def one_bad(archive: Path, member: str, *args: object, **kwargs: object) -> int:
        calls.append(member)
        if member == "01.png":
            raise ArchiveRefused("that one will not come out")
        return real(archive, member, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(taking_in, "extract_member", one_bad)
    await _scan(context_for, root, settings, service, reindexer)

    assert calls == ["01.png", "02.png"]
    assert len(reindexer.told) == 1


async def test_a_member_the_ingress_gate_refuses_is_dropped_and_the_shoot_is_not(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """Every member goes through the same gate a downloaded file does, so a `.png` that is not one
    is refused where it is read rather than where it is listed."""
    good = tmp_path / "good.png"
    draw(good, "testsrc2=size=64x48:rate=1")
    with zipfile.ZipFile(root_path / "shoot.zip", "w") as writing:
        writing.write(good, "01.png")
        writing.writestr("02.png", b"\x89PNG\r\n\x1a\n" + b"not a picture" * 40)

    await _scan(context_for, root, settings, service, reindexer)

    assert len(reindexer.told) == 1


async def test_the_same_picture_twice_in_one_archive_is_one_asset_still_waiting_to_be_probed(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    job_queue: JobQueue,
) -> None:
    """Identity is the content, so the second copy is the same asset arriving in a second place.

    It is not new, and nothing has probed it yet, so what must happen is that the `probe` already
    coming is left alone rather than queued a second time: a second `probe` of one file is work
    done twice on every duplicate in somebody's library.
    """
    made = tmp_path / "one.png"
    draw(made, "testsrc2=size=64x48:rate=1")
    with zipfile.ZipFile(root_path / "shoot.zip", "w") as writing:
        writing.write(made, "01.png")
        writing.write(made, "02.png")

    await _scan(context_for, root, settings, service, reindexer)

    assert len(reindexer.told) == 1
    page = await job_queue.list(job_type=taking_in.PROBE, limit=100)
    assert len(page.jobs) == 1, "the same file was queued for probing twice"


async def test_an_archive_that_cannot_be_opened_leaves_its_pictures_on_the_wall(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """Anything other than a definite no counts as still there, and this is the case that rule is
    for: an archive that is there and unreadable is a question about ACCESS, and answering it as
    absence takes somebody's whole shoot off the wall while the file sits on the disk."""
    gallery(root_path / "shoot.zip", ["01.png", "02.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)
    kept = list(reindexer.told)
    assert len(kept) == 2

    (root_path / "shoot.zip").write_bytes(b"the file is there and it is rubbish")
    await _scan(context_for, root, settings, service, reindexer)

    for asset_id in kept:
        for location in await content_store.locations(asset_id):
            assert location.status is LocationStatus.PRESENT, location.rel_path


async def test_an_archive_that_cannot_be_read_at_all_leaves_its_pictures_on_the_wall(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """Anything other than a definite no counts as still there, and this is the case the rule is
    for: the file is on the disk and cannot be opened right now.

    A permissions change, a half-written copy, a mount that is misbehaving: every one of them is a
    question about ACCESS, and answering it as absence takes somebody's whole shoot off the wall
    while the file sits where it always was.
    """
    gallery(root_path / "shoot.zip", ["01.png", "02.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)
    kept = list(reindexer.told)
    assert len(kept) == 2

    (root_path / "shoot.zip").write_bytes(b"there and unreadable")
    await _scan(context_for, root, settings, service, reindexer)

    for asset_id in kept:
        for location in await content_store.locations(asset_id):
            assert location.status is LocationStatus.PRESENT, location.rel_path


async def test_a_picture_already_read_once_is_not_queued_to_be_read_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
    job_queue: JobQueue,
) -> None:
    """The same picture in two shoots is one asset in two places, and it has already been read.

    The other side of the branch beside it: a second location for a file nothing has probed asks for
    `probe`, and a second location for one that has been probed asks for nothing.
    """
    made = tmp_path / "one.png"
    draw(made, "testsrc2=size=64x48:rate=1")
    with zipfile.ZipFile(root_path / "first.zip", "w") as writing:
        writing.write(made, "01.png")
    await _scan(context_for, root, settings, service, reindexer)
    asset_id = reindexer.told[0]
    await content_store.record_probe(asset_id, width=64, height=48)
    before = len((await job_queue.list(job_type=taking_in.PROBE, limit=100)).jobs)

    with zipfile.ZipFile(root_path / "second.zip", "w") as writing:
        writing.write(made, "01.png")
    await _scan(context_for, root, settings, service, reindexer)

    locations = await content_store.locations(asset_id)
    assert {Path(one.archive_rel_path or "").name for one in locations} == {
        "first.zip",
        "second.zip",
    }
    after = len((await job_queue.list(job_type=taking_in.PROBE, limit=100)).jobs)
    assert after == before, "a file that had already been read was queued to be read again"


async def test_an_archive_refused_on_a_later_pass_keeps_the_pictures_it_already_gave(
    monkeypatch: pytest.MonkeyPatch,
    context_for: Context,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> None:
    """A refused archive claims nothing, so every picture in it reaches the sweep unclaimed, and
    the sweep has to go and look rather than reason from what the walk reported.

    The file itself is untouched and perfectly readable; only the pass in front of it declined. A
    version that treated unclaimed as gone would take a whole shoot off the wall because a cap moved.
    """
    gallery(root_path / "shoot.zip", ["01.png", "02.png"], tmp_path)
    await _scan(context_for, root, settings, service, reindexer)
    kept = list(reindexer.told)
    assert len(kept) == 2

    def refuses(*args: object, **kwargs: object) -> list[object]:
        raise ArchiveRefused("not this time")

    monkeypatch.setattr(taking_in, "inspect_archive", refuses)
    await _scan(context_for, root, settings, service, reindexer)

    for asset_id in kept:
        for location in await content_store.locations(asset_id):
            assert location.status is LocationStatus.PRESENT, location.rel_path
