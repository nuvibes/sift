# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dry runs as the composition root wires them: what each task's row says it would do.

Each feature hands over a plan worked out and written nowhere; these hold what the row makes of
it. Stand-ins answer for the features and for the access layer, because what is under test is the
wording, the counting and above all the naming: a file or a folder the person who pressed may not
see is passed over, never named.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI

from sift.kernel.access import Viewer
from sift.slices import (
    backup,
    dedup,
    library_roots,
    music,
    shoots,
    stash_boxes,
    suggestions,
    tasks,
)
from sift.slices.library_roots.scan_plan import PlannedRead
from sift.wiring import dry_runs
from sift.wiring.built import Storage

VIEWER = cast("Viewer", SimpleNamespace(id="u1"))
EVERYTHING = tasks.Selection()


class _Access:
    """What the person who pressed may see: files by id, folders by id with whether concealed."""

    def __init__(
        self, *, files: set[str] | None = None, folders: dict[str, tuple[str, bool]] | None = None
    ) -> None:
        self._files = files or set()
        self._folders = folders or {}

    async def visible_of(self, _viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        return {one for one in asset_ids if one in self._files}

    async def visible_folders_of(self, _viewer: Viewer, ids: Sequence[str]) -> dict[str, Any]:
        return {
            one: SimpleNamespace(name=self._folders[one][0], concealed=self._folders[one][1])
            for one in ids
            if one in self._folders
        }


class _Content:
    def __init__(self, *, named: dict[str, str], recorded: dict[str, str] | None = None) -> None:
        self._named = named
        self._recorded = recorded or {}

    async def locations_of(self, asset_ids: Sequence[str]) -> dict[str, list[Any]]:
        return {
            one: [SimpleNamespace(filename=self._named[one])]
            for one in asset_ids
            if one in self._named
        }

    async def location_at(self, _root_id: str, rel_path: str) -> Any:
        asset_id = self._recorded.get(rel_path)
        return None if asset_id is None else SimpleNamespace(asset_id=asset_id)


class _Library:
    def __init__(self, *, roots: Sequence[Any] = (), folders: dict[str, str] | None = None) -> None:
        self._roots = list(roots)
        self._folders = folders or {}

    async def roots(self) -> list[Any]:
        return self._roots

    async def folder_at(self, _root_id: str, rel_path: str) -> Any:
        found = self._folders.get(rel_path)
        return None if found is None else SimpleNamespace(id=found)


def _store(
    *,
    access: _Access | None = None,
    content: _Content | None = None,
    library: _Library | None = None,
) -> Storage:
    return cast(
        "Storage",
        SimpleNamespace(
            access=access or _Access(),
            content=content or _Content(named={}),
            library=library or _Library(),
        ),
    )


class _Hub:
    def __init__(self, scanning: bool = True) -> None:
        self._scanning = scanning

    async def get_app(self, key: str) -> object:
        assert key == stash_boxes.SCAN_KEY
        return self._scanning


def _planners(store: Storage, *, hub: _Hub | None = None, **parts: object) -> dict[str, Any]:
    """The planners over an application holding these parts and no others."""
    state = SimpleNamespace(**{part.name: value for part, value in _named_parts(parts).items()})
    app = cast("FastAPI", SimpleNamespace(state=state))
    return dry_runs.dry_run_planners(app, store, hub or _Hub())  # type: ignore[arg-type]


_PARTS = {
    "scanning": library_roots.SERVICE,
    "duplicates": dedup.SERVICE,
    "suggesting": suggestions.SERVICE,
    "shoots": shoots.SERVICE,
    "boxes": stash_boxes.SERVICE,
    "songs": music.LOOKUP_STARTER,
    "backups": backup.SERVICE,
}


def _named_parts(parts: dict[str, object]) -> dict[Any, object]:
    return {_PARTS[key]: value for key, value in parts.items()}


class _Plans:
    """A feature whose plan is the one handed in."""

    def __init__(self, planned: object) -> None:
        self._planned = planned

    async def plan(self) -> object:
        return self._planned


# --- a feature not running in this process --------------------------------------------------------


@pytest.mark.parametrize(
    "task",
    ["scan", "duplicates", "suggestions", "shoots", "enrichment", music.LOOKUP_TASK, "backup"],
)
async def test_a_task_whose_feature_is_not_running_here_says_so_and_plans_nothing(
    task: str,
) -> None:
    planned = await _planners(_store())[task](EVERYTHING, VIEWER)

    assert planned.files == 0
    assert planned.refusals == (dry_runs.NOT_HERE,)


# --- Scan -----------------------------------------------------------------------------------------


def _scanned(**fields: Any) -> Any:
    shape: dict[str, Any] = {
        "answered": True,
        "folders": None,
        "new": 0,
        "changed": 0,
        "returned": 0,
        "archives": 0,
        "missing": 0,
        "reading": [],
        "going": [],
    }
    shape.update(fields)
    return SimpleNamespace(**shape)


async def test_a_scan_counts_every_library_asked_for_and_names_only_what_the_viewer_may_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A library that did not answer is said by name and planned for nothing; under the rest,
    a file in a folder the viewer cannot see, in a concealed folder, or recorded as a file they
    cannot see, is read and counted, never named."""
    roots = [SimpleNamespace(id=one, name=one.title()) for one in ("home", "away", "other")]
    reads = [
        PlannedRead(rel_path="Clips/one.mp4", recorded_at="Clips/one.mp4", folder="Clips"),
        PlannedRead(rel_path="Clips/two.mp4", recorded_at="Clips/two.mp4", folder="Clips"),
        PlannedRead(rel_path="Kept/three.mp4", recorded_at="Kept/three.mp4", folder="Kept"),
        PlannedRead(rel_path="Gone/four.mp4", recorded_at="Gone/four.mp4", folder="Gone"),
        PlannedRead(rel_path="Clips/new.mp4", recorded_at="Old/new.mp4", folder="Clips"),
    ]
    plans = {
        "home": _scanned(
            folders=SimpleNamespace(moves=SimpleNamespace(pairs=[("a", "b")])),
            new=3,
            changed=1,
            returned=2,
            archives=1,
            missing=1,
            reading=reads,
        ),
        "away": _scanned(answered=False),
    }

    async def plan_scan(_store: object, _service: object, root: Any, *, first: int) -> Any:
        assert first == dry_runs.NAMED * 5
        return plans[root.id]

    monkeypatch.setattr(dry_runs, "plan_scan", plan_scan)
    store = _store(
        access=_Access(files={"seen"}, folders={"f-clips": ("Clips", False), "f-kept": ("", True)}),
        content=_Content(named={}, recorded={"Clips/two.mp4": "hidden", "Old/new.mp4": "seen"}),
        library=_Library(roots=roots, folders={"Clips": "f-clips", "Kept": "f-kept"}),
    )
    planners = _planners(store, scanning=object())

    planned = await planners["scan"](tasks.Selection(locations=("home", "away")), VIEWER)

    assert planned.refusals == ("Away didn't answer, so nothing in it was planned.",)
    assert planned.files == 1 + 3 + 1 + 1 + 2 + 1
    assert [(line.label, line.count) for line in planned.lines] == [
        ("Folders moved", 1),
        ("New files", 3),
        ("Changed files", 1),
        ("Files back", 2),
        ("Archives to look inside", 1),
        ("Files marked missing", 1),
    ]
    assert planned.names == ("one.mp4", "new.mp4")
    assert planned.nameable == 5
    assert planned.doing == (
        "would move 1 folder, take in 3 new files, read 1 changed file again, find 2 files back "
        "where they were, look inside 1 archive and mark 1 file missing"
    )


async def test_a_scan_that_reads_nothing_names_the_files_it_would_mark_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named a batch at a time and no further than the first ten the viewer may see."""
    going = [f"a{index:02d}" for index in range(60)]

    async def plan_scan(*_args: object, **_kwargs: object) -> Any:
        return _scanned(missing=len(going), going=going)

    monkeypatch.setattr(dry_runs, "plan_scan", plan_scan)
    store = _store(
        access=_Access(files=set(going[1:])),
        content=_Content(named={one: f"{one}.mp4" for one in going}),
        library=_Library(roots=[SimpleNamespace(id="home", name="Home")]),
    )

    planned = await _planners(store, scanning=object())["scan"](EVERYTHING, VIEWER)

    assert planned.names == tuple(f"{one}.mp4" for one in going[1:11])
    assert planned.named == "First files it would mark missing"
    assert planned.nameable == len(going)
    assert planned.doing == "would mark 60 files missing"
    assert [line.label for line in planned.lines] == [
        "Folders moved",
        "New files",
        "Changed files",
        "Files marked missing",
    ]


async def test_a_scan_with_fewer_files_than_a_page_names_them_all() -> None:
    store = _store(access=_Access(files={"a", "b"}), content=_Content(named={"a": "a.mp4"}))
    runs = dry_runs._DryRuns(cast("FastAPI", None), store, cast("Any", None))

    assert await runs.files_named(VIEWER, ["a", "b", "a"]) == ("a.mp4",)


# --- the other passes -----------------------------------------------------------------------------


async def test_duplicates_counts_the_new_pairs_and_names_their_files() -> None:
    pair = SimpleNamespace(asset_a="a", asset_b="b")
    store = _store(
        access=_Access(files={"a", "b"}), content=_Content(named={"a": "a.mp4", "b": "b.mp4"})
    )
    planners = _planners(store, duplicates=_Plans(SimpleNamespace(pairs=[pair], compared=40)))

    planned = await planners["duplicates"](EVERYTHING, VIEWER)

    assert (planned.files, planned.names, planned.nameable) == (1, ("a.mp4", "b.mp4"), 2)
    assert (
        planned.doing == "would add 1 new pair of near duplicates to review, from 40 files compared"
    )

    idle = _planners(store, duplicates=_Plans(SimpleNamespace(pairs=[], compared=40)))
    assert (await idle["duplicates"](EVERYTHING, VIEWER)).names == ()


@pytest.mark.parametrize("by_names", [True, False])
async def test_suggestions_names_the_changed_folders_the_viewer_may_see(by_names: bool) -> None:
    moved = [SimpleNamespace(id=one) for one in ("f1", "f2", "f3")]
    store = _store(access=_Access(folders={"f1": ("Beach", False), "f2": ("Kept", True)}))
    planned_by = SimpleNamespace(moved=moved, folders=moved * 2, by_names=by_names)

    planned = await _planners(store, suggesting=_Plans(planned_by))["suggestions"](
        EVERYTHING, VIEWER
    )

    assert planned.names == ("Beach",)
    assert [(line.label, line.count) for line in planned.lines] == [
        ("Folders that changed", 3),
        ("Folders in all", 6),
    ]
    tail = ", and read the names of new files that have no Site yet, to file them"
    assert (
        planned.doing
        == f"would read 3 folders that changed since it last looked{tail if by_names else ''}"
    )
    assert planned.idle.startswith("has no changed folders" if by_names else "has nothing to do")


async def test_find_shoots_refuses_with_nothing_described_and_otherwise_says_what_it_makes() -> (
    None
):
    store = _store(access=_Access(files={"p1", "p2"}), content=_Content(named={"p1": "p1.jpg"}))
    blank = SimpleNamespace(can_compare=False)
    refused = await _planners(store, shoots=_Plans(blank))["shoots"](EVERYTHING, VIEWER)
    assert refused.refusals == (
        "Nothing has been described for Smart Search yet, so there is nothing to compare.",
    )

    found = [SimpleNamespace(groups=[SimpleNamespace(asset_ids=["p1", "p2"])])]
    for automatic, made in ((True, "would make 2 Photo Sets"), (False, "would suggest 2 shoots")):
        planned_by = SimpleNamespace(
            can_compare=True, shoots=2, found=found, creators=3, automatic=automatic
        )
        planned = await _planners(store, shoots=_Plans(planned_by))["shoots"](EVERYTHING, VIEWER)
        assert planned.doing.startswith(made) and planned.doing.endswith("of 1 person")
        assert planned.names == ("p1.jpg",)

    none_found = SimpleNamespace(can_compare=True, shoots=0, found=[], creators=0, automatic=True)
    idle = await _planners(store, shoots=_Plans(none_found))["shoots"](EVERYTHING, VIEWER)
    assert idle.names == ()


class _Boxes:
    def __init__(self, nobody: str | None = None) -> None:
        self._nobody = nobody

    async def cannot_ask(self, _box: object) -> str | None:
        return self._nobody

    async def names_asked(self, _box: object) -> list[str]:
        return ["StashDB", "FansDB"]


async def test_enrichment_is_refused_while_off_or_with_nobody_to_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_box(_hub: object, _named: object) -> str:
        return ""

    monkeypatch.setattr(stash_boxes, "box_for", no_box)
    store = _store()

    off = await _planners(store, hub=_Hub(scanning=False), boxes=_Boxes())["enrichment"](
        EVERYTHING, VIEWER
    )
    assert off.refusals == (stash_boxes.ENRICHING_OFF,)

    nobody = await _planners(store, boxes=_Boxes("No stash-box has a key."))["enrichment"](
        EVERYTHING, VIEWER
    )
    assert nobody.refusals == ("No stash-box has a key.",)


async def test_enrichment_walks_every_page_of_its_sweep_and_asks_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_box(_hub: object, _named: object) -> str:
        return ""

    pages = iter(
        [
            SimpleNamespace(asking=[f"a{index}" for index in range(30)], walked=40, total=90),
            SimpleNamespace(asking=[f"b{index}" for index in range(30)], walked=40, total=90),
            SimpleNamespace(asking=["c"], walked=10, total=90),
        ]
    )
    offsets: list[int] = []

    async def sweep_page(*_args: object, offset: int, **_kwargs: object) -> Any:
        offsets.append(offset)
        return next(pages)

    monkeypatch.setattr(stash_boxes, "box_for", no_box)
    monkeypatch.setattr(dry_runs, "sweep_page", sweep_page)
    store = _store(access=_Access(files={"a0"}), content=_Content(named={"a0": "a0.mp4"}))

    planned = await _planners(store, boxes=_Boxes())["enrichment"](EVERYTHING, VIEWER)

    assert offsets == [0, 40, 80]
    assert planned.files == 61
    assert planned.doing == "would ask StashDB and FansDB about 61 files"
    assert planned.names == ("a0.mp4",)


async def test_enrichment_with_nothing_left_to_ask_stops_at_an_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_box(_hub: object, _named: object) -> str:
        return ""

    async def sweep_page(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(asking=[], walked=0, total=500)

    monkeypatch.setattr(stash_boxes, "box_for", no_box)
    monkeypatch.setattr(dry_runs, "sweep_page", sweep_page)

    planned = await _planners(_store(), boxes=_Boxes())["enrichment"](EVERYTHING, VIEWER)

    assert (planned.files, planned.names) == (0, ())


class _Lookups:
    def __init__(self, refused: str | None = None, files: int = 2) -> None:
        self._refused = refused
        self._files = files

    async def cannot_run(self) -> str | None:
        return self._refused

    async def plan(self, *, first: int) -> Any:
        return SimpleNamespace(files=self._files, first=["s1", "s2"][: self._files])

    async def not_known(self) -> int:
        return 4


async def test_song_names_says_what_it_would_send_to_acoustid_and_sends_nothing() -> None:
    store = _store(
        access=_Access(files={"s1", "s2"}), content=_Content(named={"s1": "s1.mp4", "s2": "s2.mp4"})
    )

    refused = await _planners(store, songs=_Lookups("No key."))[music.LOOKUP_TASK](
        EVERYTHING, VIEWER
    )
    assert refused.refusals == ("No key.",)

    planned = await _planners(store, songs=_Lookups())[music.LOOKUP_TASK](EVERYTHING, VIEWER)
    assert planned.names == ("s1.mp4", "s2.mp4")
    assert [(line.label, line.count) for line in planned.lines] == [
        ("Files to ask about", 2),
        ("Files AcoustID did not know", 4),
    ]
    idle = await _planners(store, songs=_Lookups(files=0))[music.LOOKUP_TASK](EVERYTHING, VIEWER)
    assert idle.names == ()


class _Backups:
    def __init__(self, planned: object | None = None, refused: str | None = None) -> None:
        self._planned = planned
        self._refused = refused

    async def plan(self) -> object:
        if self._refused is not None:
            raise backup.DestinationRefused(self._refused)
        return self._planned


async def test_the_backup_names_the_file_it_would_write_and_the_older_ones_it_would_delete() -> (
    None
):
    older = [Path(f"sift-backup-{index}.zip") for index in range(2)]
    planned_by = SimpleNamespace(file=Path("sift-backup-new.zip"), drop=older)

    planned = await _planners(_store(), backups=_Backups(planned_by))["backup"](EVERYTHING, VIEWER)

    assert planned.doing == "would write sift-backup-new.zip and delete 2 older backups"
    assert planned.names == ("sift-backup-0.zip", "sift-backup-1.zip")
    keeping = SimpleNamespace(file=Path("sift-backup-new.zip"), drop=[])
    kept = await _planners(_store(), backups=_Backups(keeping))["backup"](EVERYTHING, VIEWER)
    assert kept.doing == "would write sift-backup-new.zip"

    refused = await _planners(_store(), backups=_Backups(refused="The folder is gone."))["backup"](
        EVERYTHING, VIEWER
    )
    assert refused.refusals == ("The folder is gone.",)


async def test_a_scan_stops_naming_once_ten_are_named_and_asks_nothing_more_of_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = [
        PlannedRead(rel_path=f"f{index}.mp4", recorded_at=f"f{index}.mp4", folder="")
        for index in range(12)
    ]

    async def plan_scan(*_args: object, **_kwargs: object) -> Any:
        return _scanned(new=len(reads), reading=reads)

    asked: list[str] = []
    library = _Library(
        roots=[SimpleNamespace(id=one, name=one) for one in ("first", "second")],
        folders={"": "top"},
    )
    real = library.folder_at

    async def folder_at(root_id: str, rel_path: str) -> Any:
        asked.append(root_id)
        return await real(root_id, rel_path)

    library.folder_at = folder_at  # type: ignore[method-assign, assignment]
    monkeypatch.setattr(dry_runs, "plan_scan", plan_scan)
    store = _store(access=_Access(folders={"top": ("", False)}), library=library)

    planned = await _planners(store, scanning=object())["scan"](EVERYTHING, VIEWER)

    assert planned.names == tuple(f"f{index}.mp4" for index in range(10))
    assert planned.files == 24
    assert set(asked) == {"first"}, "the second library's files were looked up to be named"
