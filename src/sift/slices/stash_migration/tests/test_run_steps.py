# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each step of a run, and of the pass that lands what waits, on its own.

The features a step writes through (the lookups, the writers, the doors, the waiting store) are
stood in for by small recorders, so each test says what one step asks of them and what it counts.
The run over a real application is `test_run`; this is the detail it cannot reach cheaply: a
lookup that refuses a name, a Cancel between two rows, a door that is not there.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (writes a foreign database file that is not a Sift library)
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel.content.identity_models import Carrier
from sift.kernel.jobs import JobCanceled, JobFailedPermanently, JobHeld
from sift.kernel.ledger import Actor
from sift.kernel.records import Subject
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.slices.stash_migration import reader, service
from sift.slices.stash_migration.ports import Linked
from sift.slices.stash_migration.reader import BoxId, Entity, Group, Item, Marker, StashFile
from sift.slices.stash_migration.service import (
    COPY_NAME,
    FOLDER,
    MOMENT_MS,
    PLAN_NAME,
    Decided,
    Finder,
    Known,
    Pictures,
    StashMigration,
    _database_in,
    _master_key,
    _place,
    _records_of,
    _with_what_they_carry,
)
from sift.slices.stash_migration.tally import Tally
from sift.slices.stash_migration.tests.stash_fixture import IN_DATABASE, IN_FOLDER, make_stash
from sift.slices.stash_migration.waiting import KeptGallery, KeptGroup, Waiting, WaitingFile

pytestmark = pytest.mark.unit

_ACTOR = Actor.sift(VIA_STASH_LIBRARY)


def _run[T](step: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(step)


class _Context:
    """What a step reads of its task: whether Cancel was pressed, and where it reports to."""

    def __init__(self, stopping: str | None = None, *, key: bool = True) -> None:
        self._stopping = stopping
        self._key = key
        self.progress: list[float] = []
        self.units: list[int] = []
        self.notes: list[str] = []

    def stopping(self) -> str | None:
        return self._stopping

    async def report_progress(self, share: float) -> None:
        self.progress.append(share)

    async def set_units(self, units: int) -> None:
        self.units.append(units)

    async def set_note(self, note: str) -> None:
        self.notes.append(note)

    async def master_key(self) -> bytes:
        if not self._key:
            raise RuntimeError("locked")
        return b"key"


class _Doors:
    """The other features' doors, each recording what it was asked and answering yes."""

    def __init__(self, *, collection: str | None = "01COLLECTION") -> None:
        self.asked: list[tuple[Any, ...]] = []
        self._collection = collection

    async def link(self, kind: str, local: str, endpoint: str, remote_id: str, key: Any) -> Linked:
        self.asked.append(("link", kind, local, remote_id))
        return Linked.LINKED

    async def link_file(self, asset_id: str, endpoint: str, remote_id: str, key: Any) -> Linked:
        self.asked.append(("link_file", asset_id, remote_id))
        return Linked.LINKED

    async def make_collection(self, name: str, user_id: str, *, actor: Actor) -> str | None:
        self.asked.append(("make_collection", name, user_id))
        return self._collection

    async def add_to_collection(self, collection_id: str, scenes: Any, *, actor: Actor) -> str:
        self.asked.append(("add_to_collection", collection_id, list(scenes)))
        return collection_id

    async def picture(self, kind: str, local: str, blob: bytes, *, actor: Actor) -> bool:
        self.asked.append(("picture", kind, local, len(blob)))
        return True

    async def mark(self, asset_id: str, start_ms: int, end_ms: int, **said: Any) -> bool:
        self.asked.append(("mark", asset_id, start_ms, end_ms, said["name"], said["tag_ids"]))
        return True


class _Kept:
    """The waiting store: galleries and groups remembered, entities by kind and name."""

    def __init__(self, **entities: dict[str, Any]) -> None:
        self.galleries: dict[int, KeptGallery] = {}
        self.groups: dict[int, KeptGroup] = {}
        self.entities = {tuple(key.split(":", 1)): record for key, record in entities.items()}
        self.forgotten: list[Any] = []

    async def gallery(self, source: str, gallery_id: int) -> KeptGallery | None:
        return self.galleries.get(gallery_id)

    async def keep_gallery(self, source: str, gallery_id: int, kept: KeptGallery) -> None:
        self.galleries[gallery_id] = kept

    async def group(self, source: str, group_id: int) -> KeptGroup | None:
        return self.groups.get(group_id)

    async def keep_group(self, source: str, group_id: int, kept: KeptGroup) -> None:
        self.groups[group_id] = kept

    async def entity(self, kind: str, name: str) -> dict[str, Any] | None:
        return self.entities.get((kind, name))

    async def entity_landed(self, kind: str, name: str) -> None:
        self.forgotten.append((kind, name))

    async def landed(self, waiting_id: str) -> None:
        self.forgotten.append(waiting_id)


def _migration(tmp_path: Path | None = None, **parts: Any) -> StashMigration:
    """The service with only the parts a step reads; no door unless one is handed in."""
    migration = StashMigration.__new__(StashMigration)
    migration.doors = parts.pop("doors", None)
    if tmp_path is not None:
        migration._settings = SimpleNamespace(data_dir=tmp_path)  # type: ignore[assignment]
    for name, part in parts.items():
        setattr(migration, f"_{name}", part)
    return migration


async def _nothing(*args: Any, **kwargs: Any) -> None:
    return None


def _entity(stash_id: int, name: str, **more: Any) -> Entity:
    return Entity(stash_id=stash_id, name=name, fields=more.pop("fields", {}), **more)


# --- matching ------------------------------------------------------------------------------------


def test_a_path_is_placed_under_whichever_library_folder_holds_it() -> None:
    """Every library folder is tried, so a file in the second is placed there, and a file in
    none of them is placed nowhere rather than under the first."""
    roots = [("ONE", Path("/library/one")), ("TWO", Path("/library/two"))]
    assert _place(roots, Path("/library/two/pool/first.mp4")) == ("TWO", "pool/first.mp4")
    assert _place(roots, Path("/elsewhere/first.mp4")) is None


def test_a_file_whose_place_holds_nothing_here_is_still_found_by_its_hash() -> None:
    """A place is the first thing trusted, not the only one: where nothing sits at the place a
    Stash path maps to, the file's OSHash still finds it."""
    finder = Finder([("ONE", Path("/library"))], {}, {"00ffee": "01HASHED"}, {})
    found = finder.find_here(StashFile("/stash/first.mp4", oshash="00FFEE"), Path("/library/x.mp4"))
    assert found == ("01HASHED", "oshash")


def test_a_parent_already_chosen_is_followed_only_once() -> None:
    """A tag and its parent both chosen give the same two, not the parent twice over."""
    stashed = SimpleNamespace(
        studios=[], tags=[_entity(2, "Evening", parent="Time of day"), _entity(1, "Time of day")]
    )
    chosen = {("tag", "Evening"), ("tag", "Time of day")}
    assert _with_what_they_carry(chosen, stashed, {}) == chosen  # type: ignore[arg-type]


def test_a_config_that_names_no_database_leaves_stashs_own_name(tmp_path: Path) -> None:
    """Stash writes `database:` only when told to use another file, so a config without that
    line means the database has Stash's own name."""
    make_stash(tmp_path / "stash-go.sqlite")
    (tmp_path / "config.yml").write_text("port: 9999\n", encoding="utf-8")
    assert _database_in(tmp_path) == (tmp_path / "stash-go.sqlite").resolve()


def test_a_stash_folder_is_matched_to_the_library_folder_of_its_own_name() -> None:
    """Each of Stash's top folders is offered the library folder called what it is called; one
    called anything else here is passed over, and a Stash folder with no namesake is left out."""
    roots = [
        SimpleNamespace(name="Holiday", abs_path="/media/Holiday"),
        SimpleNamespace(name="Clips", abs_path="/media/Clips"),
    ]

    async def every_root() -> list[Any]:
        return roots

    migration = _migration(library=SimpleNamespace(roots=every_root))
    folders = [
        reader.TopFolder(path="/media/stash/Clips", files=1),
        reader.TopFolder(path="/media/stash/Elsewhere", files=1),
    ]
    assert _run(migration._proposed(folders)) == {"/media/stash/Clips": "/media/Clips"}


# --- the run's own refusals ------------------------------------------------------------------------


def test_a_run_nobody_asked_for_or_over_a_copy_that_has_gone_stops_for_good(
    tmp_path: Path,
) -> None:
    """A run writes ratings and hearts as somebody's, so one with no requester cannot run, and
    one whose copy has gone has nothing to read: both stop rather than retry for ever."""
    migration = _migration(tmp_path)
    unasked = SimpleNamespace(job=SimpleNamespace(requested_by=None))
    with pytest.raises(JobFailedPermanently, match="doesn't know who asked"):
        _run(migration.run(unasked))  # type: ignore[arg-type]
    asked = SimpleNamespace(job=SimpleNamespace(requested_by="01USER"))
    with pytest.raises(JobFailedPermanently, match="copy has gone"):
        _run(migration.run(asked))  # type: ignore[arg-type]


# --- People, Sites and Tags ---------------------------------------------------------------------


def _stashed_names() -> SimpleNamespace:
    return SimpleNamespace(
        tags=[_entity(1, "Beach"), _entity(2, "Evening")],
        studios=[_entity(1, "Another Studio"), _entity(2, "Moe Studio")],
        performers=[_entity(1, "Jane Doe"), _entity(2, "Jane Roe")],
        several_parents=[],
    )


def _every_name(stashed: SimpleNamespace) -> Decided:
    come = {("tag", one.name) for one in stashed.tags}
    come |= {("site", one.name) for one in stashed.studios}
    come |= {("person", one.name) for one in stashed.performers}
    return Decided(come=come, without_files=set())


def test_a_name_the_lookups_refuse_is_left_out_of_every_kind() -> None:
    """A tag, a Site or a person whose name the lookup will not make is not counted and not
    remembered, so nothing later in the run writes onto a row that does not exist."""
    refused = {"Evening", "Moe Studio", "Jane Roe"}

    async def made(subject: Subject, name: str, *, via: str) -> str | None:
        return None if name in refused else f"01-{name}"

    migration = _migration()
    migration._made = made  # type: ignore[method-assign,assignment]
    migration._merge = _nothing  # type: ignore[method-assign]
    stashed = _stashed_names()
    tally = Tally()
    known = _run(
        migration._catalog(_Context(), stashed, tally, _ACTOR, _every_name(stashed))  # type: ignore[arg-type]
    )
    assert (tally.tags, tally.sites, tally.people) == (1, 1, 1)
    assert [local for local, _one in known.tag.values()] == ["01-Beach"]
    assert [local for local, _one in known.site.values()] == ["01-Another Studio"]
    assert [local for local, _one in known.person.values()] == ["01-Jane Doe"]


def test_cancel_stops_the_run_between_two_people() -> None:
    """People are the longest list a Stash library holds, so Cancel is looked for after each."""

    async def made(subject: Subject, name: str, *, via: str) -> str:
        return f"01-{name}"

    migration = _migration()
    migration._made = made  # type: ignore[method-assign,assignment]
    migration._merge = _nothing  # type: ignore[method-assign]
    stashed = _stashed_names()
    tally = Tally()
    with pytest.raises(JobCanceled):
        _run(
            migration._catalog(_Context("cancel"), stashed, tally, _ACTOR, _every_name(stashed))  # type: ignore[arg-type]
        )
    assert tally.people == 1


def test_rows_a_merge_invents_are_named_as_made_from_the_stash_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scene's write can make the People, Sites and Tags it names. Each one that exists after
    the write is marked as made from this Stash library; one the lookup cannot find is left."""
    marked: list[tuple[str, str, str]] = []

    async def mark(db: Any, kind: str, local: str, via: str) -> None:
        marked.append((kind, local, via))

    monkeypatch.setattr(service, "mark_made_via", mark)
    decided = SimpleNamespace(writes=["title"])

    async def plan_for(**asked: Any) -> Any:
        return decided

    async def missing_for(plan: Any) -> list[Any]:
        return [
            SimpleNamespace(kind="person", name="Jane Doe"),
            SimpleNamespace(kind="tag", name="Beach"),
        ]

    async def named(kind: str, name: str, *, creating: bool) -> str | None:
        return "01JANE" if name == "Jane Doe" else None

    enricher = SimpleNamespace(plan_for=plan_for, missing_for=missing_for, apply=_nothing)
    migration = _migration(enricher=enricher, db=None)
    migration._named = named  # type: ignore[method-assign]
    _run(migration._merge(Subject.ASSET, "01FILE", _entity(1, "first"), _ACTOR))
    assert marked == [("person", "01JANE", VIA_STASH_LIBRARY)]


def test_a_name_the_lookup_will_not_make_is_not_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a row that was made carries the mark of a Stash library; a refused name is none."""
    marked: list[Any] = []

    async def mark(*args: Any) -> None:
        marked.append(args)

    async def refuse(name: str, *, creating: bool) -> None:
        return None

    monkeypatch.setattr(service, "mark_made_via", mark)
    migration = _migration(naming=SimpleNamespace(person_named=refuse), db=None)
    assert _run(migration._made(Subject.PERSON, "Jane Doe")) is None
    assert marked == []


def test_a_kind_with_no_lookup_asks_none() -> None:
    """People, Sites and Tags alone are looked up by name; any other kind is no row here."""
    migration = _migration(naming=SimpleNamespace())
    assert _run(migration._named("asset", "first.mp4", creating=True)) is None


# --- files ---------------------------------------------------------------------------------------


def _scene(stash_id: int) -> Item:
    return Item(stash_id=stash_id, kind="scene", files=(), fields={})


def test_a_long_run_reports_its_progress_and_looks_for_cancel_each_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Between two batches the run says how far it is and stops if Cancel was pressed, so a
    library of thousands of scenes is neither silent nor unstoppable."""
    monkeypatch.setattr(service, "BATCH", 1)
    written: list[str] = []

    async def write(one: Item, asset_id: str, *args: Any) -> None:
        written.append(asset_id)

    migration = _migration()
    migration._write_item = write  # type: ignore[method-assign,assignment]
    stashed = SimpleNamespace(scenes=[_scene(1), _scene(2)], images=[])
    matched = {("scene", 1): "01A", ("scene", 2): "01B"}
    context = _Context()
    _run(migration._items(context, stashed, Tally(), _ACTOR, "01USER", matched))  # type: ignore[arg-type]
    assert written == ["01A", "01B"]
    assert context.progress == pytest.approx([0.5, 0.85])

    written.clear()
    with pytest.raises(JobCanceled):
        _run(migration._items(_Context("cancel"), stashed, Tally(), _ACTOR, "01USER", matched))  # type: ignore[arg-type]
    assert written == ["01A"]


# --- galleries and groups ----------------------------------------------------------------------


def test_a_gallery_s_set_is_remembered_with_its_new_pictures_without_the_set_door() -> None:
    """Where the set a gallery became cannot be added to here, its new pictures are still
    remembered as the gallery's, and where no set can be made the gallery waits for none."""
    kept = _Kept()
    kept.galleries[2] = KeptGallery("Poolside", "01SET", ("01A",))
    migration = _migration(waiting=kept, photo_sets=None)
    tally = Tally()
    _run(migration._grow_gallery("s", 2, "Poolside", ["01A", "01B"], _ACTOR, tally))
    assert kept.galleries[2] == KeptGallery("Poolside", "01SET", ("01A", "01B"))
    _run(migration._grow_gallery("s", 3, "Poolside", ["01C"], _ACTOR, tally))
    assert kept.galleries[3] == KeptGallery("Poolside", None, ("01C",))
    assert tally.photo_sets == 0


def test_a_gallery_s_new_pictures_are_added_to_the_set_it_became() -> None:
    """A second run adds the pictures that arrived since to the set the first one made, and
    only those, rather than making a second set."""
    added: list[tuple[str, list[str]]] = []

    async def add_to_photo_set(photo_set_id: str, assets: Any, *, actor: Actor) -> None:
        added.append((photo_set_id, list(assets)))

    kept = _Kept()
    kept.galleries[2] = KeptGallery("Poolside", "01SET", ("01A",))
    doors = SimpleNamespace(add_to_photo_set=add_to_photo_set)
    migration = _migration(waiting=kept, photo_sets=None, doors=doors)
    _run(migration._grow_gallery("s", 2, "Poolside", ["01A", "01B"], _ACTOR, Tally()))
    assert added == [("01SET", ["01B"])]
    assert kept.galleries[2] == KeptGallery("Poolside", "01SET", ("01A", "01B"))


def test_a_group_with_none_of_its_scenes_here_is_left_for_later() -> None:
    grown: list[Any] = []

    async def grow(*args: Any) -> None:
        grown.append(args)

    migration = _migration()
    migration._grow_group = grow  # type: ignore[method-assign,assignment]
    stashed = SimpleNamespace(groups=[Group(1, "Poolside Movie", (5,))])
    _run(migration._groups(stashed, Tally(), {}, "s", _ACTOR, "01USER"))  # type: ignore[arg-type]
    assert grown == []


def test_a_group_becomes_a_collection_only_for_somebody_and_only_once() -> None:
    """A Collection is somebody's, so without a User none is made and nothing is remembered.
    Where the Collections writer makes none, the group is remembered without one, so a later
    run does not ask again, and nothing is counted."""
    kept = _Kept()
    _run(
        _migration(waiting=kept)._grow_group(
            "s", 1, "Poolside Movie", ["01A"], _ACTOR, None, Tally()
        )
    )
    assert kept.groups == {}

    doors = _Doors()
    _run(
        _migration(waiting=kept, doors=doors)._grow_group(
            "s", 1, "Poolside Movie", ["01A"], _ACTOR, None, Tally()
        )
    )
    assert (kept.groups, doors.asked) == ({}, [])

    refused = _Doors(collection=None)
    tally = Tally()
    _run(
        _migration(waiting=kept, doors=refused)._grow_group(
            "s", 1, "Poolside Movie", ["01A"], _ACTOR, "01USER", tally
        )
    )
    assert refused.asked == [("make_collection", "Poolside Movie", "01USER")]
    assert kept.groups[1] == KeptGroup("Poolside Movie", None, ("01A",))
    assert tally.collections == 0


# --- pictures ------------------------------------------------------------------------------------


def test_no_picture_is_read_without_the_cover_door_or_with_none_wanted(tmp_path: Path) -> None:
    """The copy is opened only when there is a door to hand pictures to and a row with one: the
    folder here holds no copy, so opening it would fail the step."""
    known = Known(person={1: ("01JANE", _entity(1, "Jane Doe"))})
    tally = Tally()
    _run(_migration(tmp_path)._pictures(tally, known, None, _ACTOR))
    _run(_migration(tmp_path, doors=_Doors())._pictures(tally, known, None, _ACTOR))
    assert (tally.pictures, tally.pictures_not_found) == (0, 0)


def _copy(tmp_path: Path) -> Path:
    """A read's copy where the service looks for it, with the pictures of `extras`."""
    copy = tmp_path / FOLDER / COPY_NAME
    reader.copy_in(make_stash(tmp_path / "stash.sqlite", extras=True), copy)
    return copy


def test_a_picture_stash_does_not_hold_is_counted_as_not_found(tmp_path: Path) -> None:
    """A checksum whose bytes are in neither the database nor a named blobs folder is said in
    the report, never handed on as an empty picture."""
    connection = reader.open_copy(_copy(tmp_path))
    try:
        tally = Tally()
        doors = _Doors()
        _run(
            _migration(doors=doors)._picture(
                connection, "person", "01JANE", IN_FOLDER, None, _ACTOR, tally
            )
        )
        _run(
            _migration()._picture(connection, "person", "01JANE", IN_DATABASE, None, _ACTOR, tally)
        )
    finally:
        connection.close()
    assert (tally.pictures, tally.pictures_not_found, doors.asked) == (0, 1, [])


def test_a_picture_for_a_row_that_landed_later_is_read_from_the_copy_if_it_is_still_here(
    tmp_path: Path,
) -> None:
    """A person who arrives with a later file has their picture read from the read's copy. With
    the copy gone, or replaced by a file that is not Stash's, it is counted as not found."""
    doors = _Doors()
    migration = _migration(tmp_path, doors=doors)
    tally = Tally()
    chosen = Pictures(None)
    _run(migration._picture_from_copy("person", "01JANE", IN_DATABASE, chosen, _ACTOR, tally))
    assert tally.pictures_not_found == 1

    (tmp_path / FOLDER).mkdir()
    other = sqlite3.connect(tmp_path / FOLDER / COPY_NAME)
    other.execute("CREATE TABLE notes (text TEXT)")
    other.commit()
    other.close()
    _run(migration._picture_from_copy("person", "01JANE", IN_DATABASE, chosen, _ACTOR, tally))
    assert tally.pictures_not_found == 2

    (tmp_path / FOLDER / COPY_NAME).unlink()
    _copy(tmp_path)
    _run(migration._picture_from_copy("person", "01JANE", IN_DATABASE, chosen, _ACTOR, tally))
    assert tally.pictures == 1
    assert doors.asked[0][:3] == ("picture", "person", "01JANE")


# --- ids, markers and filters without the doors ------------------------------------------------


def test_without_the_doors_nothing_is_linked_marked_or_kept_and_nothing_is_counted() -> None:
    """Each step that writes through another feature does nothing when that feature's door is
    not there: the run's report names those parts as not done instead (`test_refused`)."""
    migration = _migration()
    box = BoxId(1, "https://stash-box.example/graphql", "remote-scene")
    stashed = SimpleNamespace(
        box_ids={"scene": [box], "performer": [], "studio": [], "tag": []},
        markers=[Marker(scene_id=1, title="The jump", start_seconds=1.0, end_seconds=2.0)],
        filters=[reader.SavedFilter("SCENES", "Beach days", {}, {})],
        tag_names={},
        person_names={},
        site_names={},
    )
    tally = Tally()
    context = _Context()
    _run(migration._links(context, stashed, tally, Known()))  # type: ignore[arg-type]
    _run(migration._file_links(context, stashed, tally, {1: "01A"}))  # type: ignore[arg-type]
    _run(migration._link_file("01A", box.endpoint, box.remote_id, None, tally))
    _run(migration._link("person", "01JANE", box.endpoint, box.remote_id, None, tally))
    _run(migration._marks(stashed, tally, "01USER", {1: "01A"}, Known()))  # type: ignore[arg-type]
    _run(migration._mark("01A", stashed.markers[0], {}, "01USER", tally))
    _run(migration._searches(stashed, tally, "01USER"))  # type: ignore[arg-type]
    _run(migration._picture(None, "person", "01JANE", IN_DATABASE, None, _ACTOR, tally))
    assert tally.scene_box_ids == 1
    assert (tally.links, tally.file_links) == ({}, {})
    assert (tally.marks, tally.marks_not_here, tally.marks_refused) == (0, 0, 0)
    assert (tally.saved_searches, tally.filters_not_brought) == (0, [])
    assert (tally.pictures, tally.pictures_not_found) == (0, 0)


def test_cancel_stops_the_linking_between_two_ids() -> None:
    """Each id is a question to a stash-box over the network, so Cancel is looked for after each."""
    doors = _Doors()
    migration = _migration(doors=doors)
    endpoint = "https://stash-box.example/graphql"
    stashed = SimpleNamespace(
        box_ids={
            "scene": [BoxId(1, endpoint, "s1"), BoxId(2, endpoint, "s2")],
            "performer": [BoxId(1, endpoint, "p1"), BoxId(2, endpoint, "p2")],
            "studio": [],
            "tag": [],
        }
    )
    known = Known(
        person={1: ("01JANE", _entity(1, "Jane Doe")), 2: ("01ROE", _entity(2, "Jane Roe"))}
    )
    with pytest.raises(JobCanceled):
        _run(migration._links(_Context("cancel"), stashed, Tally(), known))  # type: ignore[arg-type]
    with pytest.raises(JobCanceled):
        _run(migration._file_links(_Context("cancel"), stashed, Tally(), {1: "01A", 2: "01B"}))  # type: ignore[arg-type]
    assert doors.asked == [("link", "person", "01JANE", "p1"), ("link_file", "01A", "s1")]


def test_a_run_stopped_partway_asks_the_boxes_again_only_for_what_it_had_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The links are paced requests to somebody else's service: a restart resumes them, and a
    new read starts from nothing."""
    from sift.slices.stash_migration import service_base as checkpoint

    monkeypatch.setattr(checkpoint, "EVERY", 1)
    endpoint = "https://stash-box.example/graphql"
    stashed = SimpleNamespace(
        box_ids={
            "scene": [BoxId(1, endpoint, "s1"), BoxId(2, endpoint, "s2")],
            "performer": [BoxId(1, endpoint, "p1"), BoxId(2, endpoint, "p2")],
            "studio": [],
            "tag": [],
        }
    )
    known = Known(
        person={1: ("01JANE", _entity(1, "Jane Doe")), 2: ("01ROE", _entity(2, "Jane Roe"))}
    )
    scenes = {1: "01A", 2: "01B"}

    async def a_run(stopping: str | None, read_at: int, doors: _Doors, tally: Tally) -> None:
        migration = _migration(doors=doors)
        kept = checkpoint.Checkpoint(tmp_path, read_at)
        await kept.load()
        try:
            await migration._links(_Context(stopping), stashed, tally, known, kept)  # type: ignore[arg-type]
            await migration._file_links(_Context(stopping), stashed, tally, scenes, kept)  # type: ignore[arg-type]
        finally:
            await kept.flush()
        await kept.finish()

    first = _Doors()
    with pytest.raises(JobCanceled):
        _run(a_run("cancel", 7, first, Tally()))
    assert first.asked == [("link", "person", "01JANE", "p1")]

    again, tally = _Doors(), Tally()
    _run(a_run(None, 7, again, tally))
    assert again.asked == [
        ("link", "person", "01ROE", "p2"),
        ("link_file", "01A", "s1"),
        ("link_file", "01B", "s2"),
    ]
    assert tally.links == {"linked": 2} and tally.file_links == {"linked": 2}
    assert not (tmp_path / checkpoint.CHECKPOINT_NAME).exists(), "a finished run keeps nothing"

    first = _Doors()
    with pytest.raises(JobCanceled):
        _run(a_run("cancel", 7, first, Tally()))
    newer = _Doors()
    _run(a_run(None, 8, newer, Tally()))
    assert len(newer.asked) == 4, "another read's answers are not this one's"


def test_a_file_link_answered_on_an_earlier_run_is_counted_and_not_asked_again(
    tmp_path: Path,
) -> None:
    from sift.slices.stash_migration import service_base as checkpoint

    endpoint = "https://stash-box.example/graphql"
    stashed = SimpleNamespace(
        box_ids={"scene": [BoxId(1, endpoint, "s1")], "performer": [], "studio": [], "tag": []}
    )
    kept_before = {"read_at": 7, "done": {f"file 01A {endpoint} s1": "linked"}}
    (tmp_path / checkpoint.CHECKPOINT_NAME).write_text(json.dumps(kept_before), encoding="utf-8")
    doors, tally = _Doors(), Tally()

    async def a_run() -> None:
        kept = checkpoint.Checkpoint(tmp_path, 7)
        await kept.load()
        await _migration(doors=doors)._file_links(_Context(), stashed, tally, {1: "01A"}, kept)  # type: ignore[arg-type]

    _run(a_run())
    assert doors.asked == []
    assert tally.file_links == {"linked": 1}


def test_a_checkpoint_of_this_read_whose_answers_are_not_a_mapping_is_ignored(
    tmp_path: Path,
) -> None:
    from sift.slices.stash_migration import service_base as checkpoint

    (tmp_path / checkpoint.CHECKPOINT_NAME).write_text(
        json.dumps({"read_at": 7, "done": ["file 01A"]}), encoding="utf-8"
    )
    kept = checkpoint.Checkpoint(tmp_path, 7)
    _run(kept.load())
    assert kept.went("file 01A") is None


def test_a_moment_becomes_a_loop_of_its_own_length_even_where_its_tag_cannot_be_made() -> None:
    """A marker with no end is a Loop of the set length; the tag that says so is added where it
    can be made, and the Loop is made without it where it cannot."""

    async def refuse(subject: Subject, name: str) -> None:
        return None

    doors = _Doors()
    migration = _migration(doors=doors)
    migration._made = refuse  # type: ignore[method-assign,assignment]
    moment = Marker(scene_id=1, title="", start_seconds=12.0, end_seconds=None, tags=("Evening",))
    tally = Tally()
    _run(migration._mark("01A", moment, {"Evening": "01EVENING"}, "01USER", tally))
    assert doors.asked == [("mark", "01A", 12_000, 12_000 + MOMENT_MS, "Evening", ["01EVENING"])]
    assert (tally.marks, tally.marks_from_moments) == (1, 1)


# --- the pass that lands what waits -------------------------------------------------------------


def _row(waiting_id: str, user_id: str | None = None, **package: Any) -> Waiting:
    return Waiting(
        id=waiting_id,
        kind="scene",
        stash_id=1,
        read_at=0,
        user_id=user_id,
        label="first.mp4",
        package={"fields": {}, **package},
    )


def test_a_pass_with_nothing_arrived_says_nothing_and_cancel_stops_it_between_rows(
    tmp_path: Path,
) -> None:
    """With rows waiting and none of their files here, the pass ends with no work counted and no
    note. With nothing waiting it reads no file at all. With files here, each row is forgotten
    once landed, and Cancel stops it after that."""

    async def some() -> bool:
        return True

    async def none() -> bool:
        return False

    async def never() -> list[Any]:
        raise AssertionError("the pass looked for files with nothing waiting")

    idle = _migration(tmp_path, waiting=SimpleNamespace(anything=none))
    idle._arrived = never  # type: ignore[method-assign]
    untouched = _Context()
    _run(idle.land(untouched))  # type: ignore[arg-type]
    assert (untouched.units, untouched.notes) == ([], [])

    kept = _Kept()
    kept.anything = some  # type: ignore[attr-defined]
    migration = _migration(tmp_path, waiting=kept)

    async def none_arrived() -> list[Any]:
        return []

    migration._arrived = none_arrived  # type: ignore[method-assign]
    quiet = _Context()
    _run(migration.land(quiet))  # type: ignore[arg-type]
    assert (quiet.units, quiet.notes) == ([0], [])

    async def two_arrived() -> list[Any]:
        return [(_row("w1"), "01A"), (_row("w2"), "01B")]

    migration._arrived = two_arrived  # type: ignore[method-assign]
    migration._land_one = _nothing  # type: ignore[method-assign]
    with pytest.raises(JobCanceled):
        _run(migration.land(_Context("cancel")))  # type: ignore[arg-type]
    assert kept.forgotten == ["w1"]


def test_how_much_the_pass_has_ahead_is_none_without_reading_keys_when_nothing_waits() -> None:
    answers = iter([False, True])

    async def anything() -> bool:
        return next(answers)

    async def arrived_ids() -> dict[str, str]:
        return {"w1": "01A", "w2": "01B"}

    migration = _migration(waiting=SimpleNamespace(anything=anything))
    migration._arrived_ids = arrived_ids  # type: ignore[method-assign]
    assert _run(migration.arrived_count()) == 0
    assert _run(migration.arrived_count()) == 2


def test_a_waiting_row_is_found_by_its_place_its_oshash_or_its_fingerprint(tmp_path: Path) -> None:
    """The pass asks the library once for the files that carry any waiting row's keys, then
    matches each row the way the run does. A place asked twice is asked once, a row with two
    files is found by the first that is here, and a carrier with no key carries nothing."""
    root = tmp_path / "Clips"
    here = str(root / "first.mp4")
    keys = [
        ("w1", WaitingFile(path="/stash/first.mp4", here=here)),
        ("w1", WaitingFile(path="/stash/copy/first.mp4", here=here)),
        ("w2", WaitingFile(path="/stash/second.mp4", oshash="00ffee")),
        ("w3", WaitingFile(path="/stash/third.mp4", phash="fffe")),
        ("w4", WaitingFile(path="/stash/fourth.mp4")),
    ]
    asked: dict[str, Any] = {}

    async def every_key() -> list[Any]:
        return keys

    async def roots() -> list[Any]:
        return [SimpleNamespace(id="CLIPS", abs_path=str(root))]

    async def carriers_of(**wanted: Any) -> list[Carrier]:
        asked.update(wanted)
        return [
            Carrier("place", None, "CLIPS", "first.mp4", "01A"),
            Carrier("oshash", "00ffee", None, None, "01B"),
            Carrier("phash", "fffe", None, None, "01C"),
            Carrier("phash", None, None, None, "01NOTHING"),
        ]

    migration = _migration(
        waiting=SimpleNamespace(keys=every_key),
        library=SimpleNamespace(roots=roots),
        content=SimpleNamespace(carriers_of=carriers_of),
    )
    assert _run(migration._arrived_ids()) == {"w1": "01A", "w2": "01B", "w3": "01C"}
    assert asked == {
        "oshashes": ["00ffee"],
        "phashes": ["fffe"],
        "places": [("CLIPS", "first.mp4")],
    }


def test_with_nothing_waiting_the_library_is_not_asked() -> None:
    async def no_keys() -> list[Any]:
        return []

    migration = _migration(waiting=SimpleNamespace(keys=no_keys), library=None, content=None)
    assert _run(migration._arrived_ids()) == {}


def test_a_plan_that_cannot_be_read_means_no_pictures_were_asked_for(tmp_path: Path) -> None:
    (tmp_path / FOLDER).mkdir()
    (tmp_path / FOLDER / PLAN_NAME).write_text("{not json", encoding="utf-8")
    assert _run(_migration(tmp_path)._pictures_chosen()) is None


def test_a_landing_row_whose_user_has_gone_lands_as_nobodys_into_its_gallery_and_group() -> None:
    """A rating and a heart are somebody's: when the User who pressed Run has gone, the file's
    record still lands but no opinion is written for anyone. A picture joins the set its gallery
    became and a scene the Collection its group became."""
    calls: list[tuple[Any, ...]] = []

    async def gone(user_id: str) -> bool:
        return False

    async def write(
        item: Item, asset_id: str, actor: Any, user_id: str | None, tally: Tally
    ) -> None:
        calls.append(("write", asset_id, user_id))

    async def gallery(source: str, gallery_id: int, name: str, assets: Any, *rest: Any) -> None:
        calls.append(("gallery", source, gallery_id, name, assets))

    async def group(
        source: str, group_id: int, name: str, assets: Any, actor: Any, user_id: Any, tally: Any
    ) -> None:
        calls.append(("group", source, group_id, name, assets, user_id))

    migration = _migration()
    migration._still_a_user = gone  # type: ignore[method-assign]
    migration._write_item = write  # type: ignore[method-assign,assignment]
    migration._grow_gallery = gallery  # type: ignore[method-assign,assignment]
    migration._grow_group = group  # type: ignore[method-assign,assignment]
    row = _row(
        "w1", "01GONE", source="s", galleries=[[2, "Poolside"]], groups=[[1, "Poolside Movie"]]
    )
    _run(migration._land_one(row, "01A", _ACTOR, None, Tally()))
    assert calls == [
        ("write", "01A", None),
        ("gallery", "s", 2, "Poolside", ["01A"]),
        ("group", "s", 1, "Poolside Movie", ["01A"], None),
    ]


# --- what waited as a record ---------------------------------------------------------------------


def _ensuring(
    kept: _Kept, refused: frozenset[str] = frozenset()
) -> tuple[StashMigration, list[str]]:
    """The service with lookups that make every name but `refused`, recording the order made."""
    made: list[str] = []

    async def make(subject: Subject, name: str) -> str | None:
        if name in refused:
            return None
        made.append(name)
        return f"01-{name}"

    async def named(kind: str, name: str, *, creating: bool) -> str:
        made.append(f"found {name}")
        return f"01-{name}"

    migration = _migration(waiting=kept)
    migration._made = make  # type: ignore[method-assign,assignment]
    migration._named = named  # type: ignore[method-assign]
    migration._merge = _nothing  # type: ignore[method-assign]
    return migration, made


def test_a_waiting_site_is_made_after_its_waiting_parent() -> None:
    """A Site's parent that waited too is made first, from its own record, so the Site's write
    finds it rather than inventing it from a name."""
    kept = _Kept(
        **{"site:Another Studio": {"parent": "Jane Doe Videos"}, "site:Jane Doe Videos": {}}
    )
    migration, made = _ensuring(kept)
    tally = Tally()
    assert _run(migration._ensure("site", "Another Studio", _ACTOR, None, None, tally)) == (
        "01-Another Studio"
    )
    assert made == ["Jane Doe Videos", "Another Studio"]
    assert kept.forgotten == [("site", "Jane Doe Videos"), ("site", "Another Studio")]
    assert tally.sites == 2


def test_a_waiting_tag_is_filed_under_its_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    filed: list[tuple[str, str]] = []

    async def file_under(writing: Any, local: str, parent: str, *, actor: Actor) -> bool:
        filed.append((local, parent))
        return local == "01-Evening"

    @asynccontextmanager
    async def write() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(service, "file_under_if_unfiled", file_under)
    kept = _Kept(**{"tag:Evening": {"parent": "Time of day"}, "tag:Beach": {"parent": "Outdoors"}})
    migration, _made = _ensuring(kept)
    migration._db = SimpleNamespace(write=write)  # type: ignore[assignment]
    tally = Tally()
    _run(migration._ensure("tag", "Evening", _ACTOR, None, None, tally))
    # A tag somebody has filed here already stays where they put it, and is not counted.
    _run(migration._ensure("tag", "Beach", _ACTOR, None, None, tally))
    assert filed == [("01-Evening", "01-Time of day"), ("01-Beach", "01-Outdoors")]
    assert tally.tags_filed_under_a_parent == 1


def test_the_tags_a_waiting_person_wears_are_found_before_she_is_made() -> None:
    kept = _Kept(**{"person:Jane Doe": {"fields": {"tags": ["Freckles"]}}})
    migration, made = _ensuring(kept)
    _run(migration._ensure("person", "Jane Doe", _ACTOR, None, None, Tally()))
    assert made == ["found Freckles", "Jane Doe"]


def test_a_waiting_record_whose_name_is_refused_is_kept_waiting() -> None:
    """A name the lookup will not make lands nothing, and its record is not forgotten."""
    kept = _Kept(**{"person:Jane Doe": {}})
    migration, _made = _ensuring(kept, frozenset({"Jane Doe"}))
    assert _run(migration._ensure("person", "Jane Doe", _ACTOR, None, None, Tally())) is None
    assert kept.forgotten == []


def test_a_waiting_person_s_picture_is_brought_when_pictures_were_asked_for() -> None:
    kept = _Kept(**{"person:Jane Doe": {"picture": IN_DATABASE}})
    migration, _made = _ensuring(kept)
    asked: list[tuple[Any, ...]] = []

    async def from_copy(kind: str, local: str, checksum: str, *rest: Any) -> None:
        asked.append((kind, local, checksum))

    migration._picture_from_copy = from_copy  # type: ignore[method-assign,assignment]
    _run(
        migration._ensure(
            "person", "Jane Doe", _ACTOR, None, None, Tally(), pictures=Pictures(None)
        )
    )
    assert asked == [("person", "01-Jane Doe", IN_DATABASE)]


# --- a new library, records and the key --------------------------------------------------------


def test_with_no_scan_task_to_ask_for_the_run_still_waits_once_and_then_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where no Scan task is scheduled, nothing is asked of the queue, and the wait is still
    recorded as asked, so the next look lets the run go instead of waiting for ever."""
    (tmp_path / FOLDER).mkdir()
    plan_path = tmp_path / FOLDER / PLAN_NAME
    plan_path.write_text(json.dumps({"after_first_scan": True}), encoding="utf-8")
    enqueued: list[Any] = []

    async def listed(**asked: Any) -> Any:
        return SimpleNamespace(total=0)

    async def enqueue(*args: Any, **kwargs: Any) -> None:
        enqueued.append(args)

    monkeypatch.setattr(service, "get_schedule", lambda name: None)
    context = SimpleNamespace(queue=SimpleNamespace(list=listed, enqueue=enqueue))
    migration = _migration(tmp_path)
    with pytest.raises(JobHeld):
        _run(migration._wait_for_first_scan(context, "01USER"))  # type: ignore[arg-type]
    assert enqueued == []
    assert json.loads(plan_path.read_text(encoding="utf-8"))["scan_asked"] is True
    _run(migration._wait_for_first_scan(context, "01USER"))  # type: ignore[arg-type]


def test_a_waiting_name_stash_holds_no_record_of_keeps_none() -> None:
    stashed = SimpleNamespace(
        performers=[_entity(1, "Jane Doe")],
        studios=[],
        tags=[],
        box_ids={"performer": [], "studio": [], "tag": []},
    )
    records = _records_of(stashed, {("person", "Jane Doe"), ("person", "someone else")})  # type: ignore[arg-type]
    assert list(records) == [("person", "Jane Doe")]


def test_a_locked_key_links_without_one() -> None:
    """The stash-box keys are read with the library's key; while it is locked the run links
    with none rather than failing, and the box answers what it can without a key."""
    assert _run(_master_key(_Context(key=False))) is None  # type: ignore[arg-type]
    assert _run(_master_key(_Context())) == b"key"  # type: ignore[arg-type]
