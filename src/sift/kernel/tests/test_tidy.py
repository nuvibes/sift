# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the tidyings count, and what they remove.

Everything here is irreversible, so the tests are written around the two properties that make it
safe rather than around the deletions themselves: a survey removes nothing, and a run removes only
what the survey counted. A tidying that removed a little more than it said would pass any test that
only checked the count went down.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from sift.kernel import tidy
from sift.kernel.config import Settings, ensure_directories
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue
from sift.kernel.tidy import (
    REPAIR_PLAYBACK_KEY,
    STRANDED_KEEP_DAYS,
    LeftoverDerivatives,
    Leftovers,
    RepackagedCopies,
    Resources,
    SettledFailures,
    StrandedAssets,
    build_all,
    register_tidying,
    registered_tidyings,
    remember_survey,
    survey_all,
    survey_costly,
)

_EPOCH = 1_700_000_000


@pytest_asyncio.fixture
async def resources(tmp_path: Path) -> AsyncIterator[Resources]:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    ensure_directories(settings)
    database = Database.for_data_dir(settings.data_dir)
    await database.connect()
    await database.initialize_schema()
    try:
        yield Resources(database=database, settings=settings)
    finally:
        await database.close()


async def _asset(resources: Resources, *, placed: bool) -> str:
    """One asset, optionally in a folder. Unplaced is what "stranded" means."""
    asset_id = new_id()
    await resources.database.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', ?)",
        (asset_id, f"digest-{asset_id}", _EPOCH),
    )
    if placed:
        root_id, folder_id = new_id(), new_id()
        await resources.database.execute(
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root_id, "Pictures", f"/library/{root_id}", _EPOCH),
        )
        await resources.database.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
            "VALUES (?, ?, NULL, '', 'Pictures')",
            (folder_id, root_id),
        )
        await resources.database.execute(
            "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename, "
            "size_bytes, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, 'a.jpg', 'a.jpg', 10, "
            "?, ?)",
            (new_id(), asset_id, root_id, folder_id, _EPOCH, _EPOCH),
        )
    return asset_id


def _named(found: list[tidy.Leftovers], name: str) -> tidy.Leftovers:
    return next(one for one in found if one.name == name)


# --- the registry -----------------------------------------------------------------------------


async def test_a_record_stranded_by_a_folder_removed_lately_is_not_offered_yet(
    resources: Resources,
) -> None:
    """Removing a folder promises that adding it back brings everything with it. The card keeps
    that promise by leaving a strand alone for a while; an old one, or one with no moment kept,
    is offered as it always was."""
    fresh = await _asset(resources, placed=False)
    old = await _asset(resources, placed=False)
    undated = await _asset(resources, placed=False)
    now = int(time.time())
    await resources.database.execute(
        "UPDATE assets SET stranded_at = ? WHERE id = ?", (now - 60, fresh)
    )
    await resources.database.execute(
        "UPDATE assets SET stranded_at = ? WHERE id = ?",
        (now - (STRANDED_KEEP_DAYS + 1) * 86_400, old),
    )

    found = await StrandedAssets(resources).survey()
    assert found.count == 2
    assert str(STRANDED_KEEP_DAYS) in found.detail

    removed = await StrandedAssets(resources).run()
    assert removed == 2
    left = await resources.database.fetch_all("SELECT id FROM assets")
    assert [str(row["id"]) for row in left] == [fresh]
    assert undated not in [str(row["id"]) for row in left]


def test_a_name_cannot_be_claimed_twice() -> None:
    """An override would silently replace somebody else's tidying with yours."""
    with pytest.raises(ValueError, match="already registered"):
        register_tidying("stranded-assets", StrandedAssets)


def test_the_registry_cannot_be_edited_through_the_reader() -> None:
    copy = registered_tidyings()
    copy.clear()
    assert registered_tidyings()


def test_rows_are_tidied_before_the_files_they_named(resources: Resources) -> None:
    """Order is load-bearing, not alphabetical.

    Removing a row strands the file it named, so the row tidyings run first and the file ones pick
    up what they leave. Alphabetical order would put the file sweep first and leave the stranded
    files for a second pass nobody would know to make.
    """
    order = [tidying.name for tidying in build_all(resources)]
    assert order.index("stranded-assets") < order.index("leftover-derivatives")
    assert order.index("settled-failures") < order.index("leftover-derivatives")


# --- assets with nowhere left to be -----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_asset_in_a_folder_is_not_stranded(resources: Resources) -> None:
    await _asset(resources, placed=True)

    assert (await StrandedAssets(resources).survey()).count == 0


@pytest.mark.asyncio
async def test_an_asset_with_no_location_is_counted(resources: Resources) -> None:
    await _asset(resources, placed=False)

    found = await StrandedAssets(resources).survey()

    assert found.count == 1
    # Not a disk figure. The bytes are somebody's file, wherever it went; what is being removed is
    # Sift's record of it.
    assert found.frees_bytes is None


@pytest.mark.asyncio
async def test_looking_removes_nothing(resources: Resources) -> None:
    """The whole reason survey and run are separate calls."""
    await _asset(resources, placed=False)

    await survey_all(resources)
    await survey_all(resources)

    assert (await StrandedAssets(resources).survey()).count == 1


@pytest.mark.asyncio
async def test_only_the_stranded_are_removed(resources: Resources) -> None:
    kept = await _asset(resources, placed=True)
    await _asset(resources, placed=False)

    removed = await StrandedAssets(resources).run()

    assert removed == 1
    rows = await resources.database.fetch_all("SELECT id FROM assets", ())
    assert [str(row["id"]) for row in rows] == [kept]


@pytest.mark.asyncio
async def test_removing_a_stranded_asset_takes_its_grants(resources: Resources) -> None:
    """A grant names its object by id with nothing behind it, so nothing cascades one away.

    Left behind it goes on applying to whatever is issued that id next, which is the shape of
    leak that hands somebody a file nobody shared with them.
    """
    from sift.kernel.access import ObjectType, Repository

    asset_id = await _asset(resources, placed=False)
    access = Repository(resources.database, ContentStore(resources.database, resources.settings))
    user_id = new_id()
    await resources.database.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (?, 'guest-for-tidy', 'x', 'guest', ?)",
        (user_id, _EPOCH),
    )
    await resources.database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at) "
        "VALUES (?, ?, ?, ?, 'share', ?)",
        (new_id(), ObjectType.ITEM.value, asset_id, user_id, _EPOCH),
    )

    await StrandedAssets(resources).run()

    assert await access.grants_on(ObjectType.ITEM, asset_id) == []


@pytest.mark.asyncio
async def test_an_asset_that_comes_back_between_looking_and_removing_is_left(
    resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drive plugged back in, or a scan finishing, in the gap between the two.

    The content store answers whether the row actually went, and everything after hangs off that
    answer rather than off the delete having been attempted, so an asset that is placed again is
    left whole, grants included.
    """
    from sift.kernel.access import ObjectType, Repository

    asset_id = await _asset(resources, placed=False)
    user_id = new_id()
    await resources.database.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (?, 'guest-in-the-gap', 'x', 'guest', ?)",
        (user_id, _EPOCH),
    )
    await resources.database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at) "
        "VALUES (?, ?, ?, ?, 'share', ?)",
        (new_id(), ObjectType.ITEM.value, asset_id, user_id, _EPOCH),
    )

    async def placed_again(self: ContentStore, _asset_id: str) -> bool:
        return False

    monkeypatch.setattr(ContentStore, "remove_asset_if_unplaced", placed_again)

    assert await StrandedAssets(resources).run() == 0

    access = Repository(resources.database, ContentStore(resources.database, resources.settings))
    assert len(await access.grants_on(ObjectType.ITEM, asset_id)) == 1


# --- pictures nothing points at ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_derivative_in_use_is_left_alone(resources: Resources) -> None:
    asset_id = await _asset(resources, placed=True)
    content = ContentStore(resources.database, resources.settings)
    from sift.kernel.content import DerivativeKind

    derivative = await content.add_derivative(asset_id, DerivativeKind.THUMB, extension="jpg")
    on_disk = resources.settings.cache_dir / derivative.rel_cache_path
    on_disk.parent.mkdir(parents=True, exist_ok=True)
    on_disk.write_bytes(b"a picture")

    assert (await LeftoverDerivatives(resources).survey()).count == 0
    assert await LeftoverDerivatives(resources).run() == 0
    assert on_disk.exists()


@pytest.mark.asyncio
async def test_a_file_no_row_names_is_counted_and_removed(resources: Resources) -> None:
    stray = resources.settings.cache_dir / "ab" / "cd" / "thumb.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"orphaned")

    found = await LeftoverDerivatives(resources).survey()
    assert found.count == 1
    assert found.frees_bytes == len(b"orphaned")

    assert await LeftoverDerivatives(resources).run() == 1
    assert not stray.exists()


class _Switch:
    """The preferences, answering the repair switch and nothing else."""

    def __init__(self, *, on: bool) -> None:
        self._on = on

    async def get_app(self, key: str) -> object:
        assert key == REPAIR_PLAYBACK_KEY
        return self._on

    async def get_user(self, user_id: str, key: str) -> object:
        raise AssertionError("a tidying reads no one person's preferences")


async def _repackaged(resources: Resources) -> tuple[Path, Path]:
    """One repackaged copy and one thumbnail beside it, both on disk."""
    from sift.kernel.content import DerivativeKind

    asset_id = await _asset(resources, placed=True)
    content = ContentStore(resources.database, resources.settings)
    copy = await content.add_derivative(
        asset_id, DerivativeKind.REMUX, extension="mp4", size_bytes=len(b"a whole copy")
    )
    thumb = await content.add_derivative(asset_id, DerivativeKind.THUMB, extension="jpg")
    paths = []
    for one in (copy, thumb):
        on_disk = resources.settings.cache_dir / one.rel_cache_path
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        on_disk.write_bytes(b"a whole copy")
        paths.append(on_disk)
    return paths[0], paths[1]


@pytest.mark.asyncio
async def test_repackaged_copies_are_offered_for_deleting_while_the_repair_is_off(
    resources: Resources,
) -> None:
    """Turning the repair off keeps every copy; this is the place that gives the space back, and
    it takes the repackaged copies and nothing else."""
    copy, thumb = await _repackaged(resources)
    off = Resources(resources.database, resources.settings, preferences=_Switch(on=False))

    found = await RepackagedCopies(off).survey()
    assert (found.count, found.frees_bytes) == (1, len(b"a whole copy"))
    assert copy.exists(), "counting removes nothing"

    assert await RepackagedCopies(off).run() == 1
    assert not copy.exists()
    assert thumb.exists()
    assert (await RepackagedCopies(off).survey()).count == 0


@pytest.mark.asyncio
async def test_repackaged_copies_are_in_use_while_the_repair_is_on(resources: Resources) -> None:
    """On, a repaired copy is what plays, and deleting it would leave the file stuttering until the
    switch went off and on again. Nothing is offered and a run removes nothing."""
    copy, _thumb = await _repackaged(resources)
    on = Resources(resources.database, resources.settings, preferences=_Switch(on=True))

    assert (await RepackagedCopies(on).survey()).count == 0
    assert await RepackagedCopies(on).run() == 0
    assert copy.exists()
    # And with no preferences to read, no count at all rather than every copy.
    assert (await RepackagedCopies(resources).survey()).count is None
    assert await RepackagedCopies(resources).run() == 0
    assert copy.exists()


@pytest.mark.asyncio
async def test_the_transcode_cache_is_left_alone(resources: Resources) -> None:
    """Its files have no rows here and never will.

    Sweeping them from this side would delete segments out from under somebody watching a video:
    the transcode cache has its own lifetime and its own cap.
    """
    segment = resources.settings.transcode_cache_dir / "session" / "0.ts"
    segment.parent.mkdir(parents=True, exist_ok=True)
    segment.write_bytes(b"a segment")

    assert (await LeftoverDerivatives(resources).survey()).count == 0
    assert await LeftoverDerivatives(resources).run() == 0
    assert segment.exists()


@pytest.mark.asyncio
async def test_an_absent_cache_is_not_an_error(resources: Resources, tmp_path: Path) -> None:
    """A new install has not made one yet."""
    settings = Settings(data_dir=resources.settings.data_dir, cache_dir=tmp_path / "never-made")
    elsewhere = Resources(database=resources.database, settings=settings)

    assert (await LeftoverDerivatives(elsewhere).survey()).count == 0


def test_a_file_that_vanishes_while_being_measured_is_skipped(tmp_path: Path) -> None:
    """Between listing and measuring, which is a real window on a live install."""
    gone = tmp_path / "gone.jpg"

    assert tidy.size_of([gone]) == 0


def test_a_file_already_removed_counts_as_removed(tmp_path: Path) -> None:
    assert tidy.remove_files([tmp_path / "never-existed.jpg"]) == 1


def test_a_file_that_will_not_unlink_is_reported_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only directory is the ordinary cause. The rest of the sweep still runs."""
    stubborn = tmp_path / "stubborn.jpg"
    stubborn.write_bytes(b"x")
    fine = tmp_path / "fine.jpg"
    fine.write_bytes(b"x")

    real = Path.unlink

    def refuse(self: Path, missing_ok: bool = False) -> None:
        if self == stubborn:
            raise PermissionError("read-only")
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)

    assert tidy.remove_files([stubborn, fine]) == 1
    assert stubborn.exists()
    assert not fine.exists()


# --- jobs that will not be retried ----------------------------------------------------------------


async def _job(resources: Resources, state: str) -> str:
    job_id = new_id()
    await resources.database.execute(
        "INSERT INTO jobs (id, type, state, priority, payload, attempts, max_attempts, "
        "created_at, updated_at) VALUES (?, 'probe', ?, 0, '{}', 3, 3, ?, ?)",
        (job_id, state, _EPOCH, _EPOCH),
    )
    return job_id


@pytest.mark.asyncio
async def test_only_settled_failures_are_counted(resources: Resources) -> None:
    await _job(resources, "failed")
    await _job(resources, "done")
    await _job(resources, "queued")

    assert (await SettledFailures(resources).survey()).count == 1


@pytest.mark.asyncio
async def test_clearing_failures_leaves_everything_else(resources: Resources) -> None:
    await _job(resources, "failed")
    surviving = {await _job(resources, "done"), await _job(resources, "queued")}

    assert await SettledFailures(resources).run() == 1

    rows = await resources.database.fetch_all("SELECT id FROM jobs", ())
    assert {str(row["id"]) for row in rows} == surviving


@pytest.mark.asyncio
async def test_a_survey_of_everything_names_each_one(resources: Resources) -> None:
    """The screen reads this list. Every entry has to say what it is without being looked up.

    A superset rather than an exact set: which tidyings exist depends on which slices this process
    has imported, and the kernel is not the place that decides that.
    """
    found = await survey_all(resources)

    assert {one.name for one in found} >= {
        "stranded-assets",
        "settled-failures",
        "leftover-derivatives",
    }
    for one in found:
        assert one.title and one.detail
        # The count on screen says its noun, and only the tidying knows what it counts.
        assert one.noun and one.nouns and one.noun != one.nouns
        assert one.count in (0, None)
    assert (_named(found, "settled-failures").noun, _named(found, "settled-failures").nouns) == (
        "job",
        "jobs",
    )
    assert _named(found, "settled-failures").frees_bytes is None
    # The one that reads the disk has not been surveyed, and says so rather than saying zero.
    assert _named(found, "leftover-derivatives").count is None
    assert _named(found, "leftover-derivatives").surveyed_at is None


# --- a count that reads the disk is kept from a survey, not taken on every look -----------------


class _Walks:
    """A costly tidying that records how often it read the disk."""

    name = "walks-the-disk"
    costly = True
    title = "Something on disk"
    noun = "file"
    nouns = "files"
    detail = "What it is."

    def __init__(self, resources: Resources) -> None:
        self.resources = resources

    async def survey(self) -> Leftovers:
        _WALKED.append(self.name)
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=7,
            frees_bytes=70,
        )

    async def run(self) -> int:
        return 0


_WALKED: list[str] = []


@pytest.mark.asyncio
async def test_a_costly_count_is_read_from_the_last_survey_and_never_taken_on_a_look(
    resources: Resources,
) -> None:
    """The Maintenance screen reads `survey_all` on every open. Reading the cache directory of a
    large library there can take half a minute, so a costly tidying answers with what the last
    survey kept, and until there has been one, with no count at all rather than a zero."""
    _WALKED.clear()
    walks = _Walks(resources)

    before = await tidy._current(resources, walks)
    assert before.count is None and before.surveyed_at is None
    assert _WALKED == [], "a look must not read the disk"

    kept = await remember_survey(resources, walks)
    assert kept.count == 7 and kept.frees_bytes == 70
    assert kept.surveyed_at is not None
    assert _WALKED == ["walks-the-disk"]

    after = await tidy._current(resources, walks)
    assert (after.count, after.frees_bytes, after.surveyed_at) == (7, 70, kept.surveyed_at)
    assert _WALKED == ["walks-the-disk"], "the kept answer is read, the disk is not"


@pytest.mark.asyncio
async def test_the_survey_job_takes_every_costly_count_and_no_cheap_one(
    resources: Resources,
) -> None:
    """The cheap ones answer live and are not kept: a kept row for one would be a second answer
    to a question the screen already asks directly."""
    surveyed = {one.name for one in await survey_costly(resources)}
    costly = {one.name for one in build_all(resources) if one.costly}
    assert surveyed == costly
    assert "leftover-derivatives" in surveyed and "settled-failures" not in surveyed
    kept = _named(await survey_all(resources), "leftover-derivatives")
    assert kept.count == 0 and kept.surveyed_at is not None


# --- which of a feature's remembered ids are still files ---------------------------------------


@pytest.mark.asyncio
async def test_the_kernel_answers_which_remembered_ids_are_still_files(
    resources: Resources,
) -> None:
    """Here rather than in the tidying that needs it, and that placement is the point.

    A feature keeping its own table of asset ids has no way to be told when a file is deleted (a
    virtual table takes no foreign key, so nothing cascades) and it has to ask. But a query
    against the assets table from inside a feature is refused by a gate, and rightly: that table
    carries permissions and the store that reads it checks nothing. A tidying is a maintenance
    question rather than a viewer's, so the answer lives on this side of the seam.
    """
    here = await _asset(resources, placed=True)

    alive = await tidy.existing_asset_ids(resources, [here, "01HX0000000000000000000ZZZ"])

    assert alive == {here}


@pytest.mark.asyncio
async def test_asking_about_nothing_asks_the_database_nothing(resources: Resources) -> None:
    assert await tidy.existing_asset_ids(resources, []) == set()


async def test_the_kernel_answers_which_remembered_job_ids_are_still_queued(
    resources: Resources,
) -> None:
    """For the reason the asset question is answered here: a feature naming its scratch after a
    job cannot be told when the queue sweeps the row, and the jobs table is the kernel's."""
    queue = JobQueue(resources.database)
    here = await queue.enqueue("tidy-test", {}, require_handler=False)

    alive = await tidy.existing_job_ids(resources, [here, "01HX0000000000000000000ZZZ"])

    assert alive == {here}


async def test_asking_about_no_jobs_asks_the_database_nothing(resources: Resources) -> None:
    assert await tidy.existing_job_ids(resources, []) == set()


async def test_a_database_already_at_this_version_has_its_survey_table_left_alone(
    resources: Resources,
) -> None:
    """The initializer is told what is on disk and builds only what is missing from it."""
    async with resources.database.write() as connection:
        await connection.execute(
            "INSERT INTO tidy_surveys (name, count, frees_bytes, surveyed_at) "
            "VALUES ('kept', 1, NULL, ?)",
            (_EPOCH,),
        )
        await tidy._initialize(connection, on_disk=tidy.VERSION)

    row = await resources.database.fetch_one("SELECT count FROM tidy_surveys WHERE name = 'kept'")
    assert row is not None and row["count"] == 1
