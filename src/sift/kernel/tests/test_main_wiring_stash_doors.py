# SPDX-License-Identifier: AGPL-3.0-or-later
"""The doors a Stash run writes through, as the composition root fills them in.

A run brings another library's opinions, links and groupings across, and the rule every door keeps
is the same: what is already here wins, so a run adds what is missing and never overwrites what
somebody chose. Stand-ins answer for the features; what is under test is each door's decision.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.ledger import Actor
from sift.slices import (
    backup,
    collections,
    loops,
    people,
    photo_sets,
    search,
    stash_boxes,
    tags_ratings,
)
from sift.slices.loops.service import Refused
from sift.slices.search.service import ASSET_WALL, TooMany
from sift.slices.stash_migration import StashRefused
from sift.slices.stash_migration.ports import Linked
from sift.wiring.stash_doors import stash_doors

ACTOR = Actor.sift("stash")


class _Recorder:
    """A feature's writer that records each call by name."""

    def __init__(self, **answers: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._answers = answers

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            answer = self._answers.get(name)
            if isinstance(answer, BaseException):
                raise answer
            return answer(*args, **kwargs) if callable(answer) else answer

        return call

    def named(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]


class _Database:
    """Answers each statement by its first word after FROM, with a row or nothing."""

    def __init__(self, rows: dict[str, dict[str, Any] | None]) -> None:
        self._rows = rows

    async def fetch_one(self, sql: str, params: Sequence[Any]) -> dict[str, Any] | None:
        table = sql.split(" FROM ", 1)[1].split()[0]
        return self._rows.get(table)


def _parts(*pairs: tuple[Any, Any]) -> Any:
    state = SimpleNamespace(**{part.name: value for part, value in pairs})
    return stash_doors(cast("FastAPI", SimpleNamespace(state=state)))


def _access(viewer: object | None = SimpleNamespace(id="u1")) -> _Recorder:
    return _Recorder(load_viewer=viewer)


# --- a moment kept as a Loop ------------------------------------------------------------------------


async def test_a_moment_already_marked_or_on_a_file_that_is_gone_makes_no_loop() -> None:
    marked = _Recorder(marked=True)
    doors = _parts((loops.SERVICE, marked))
    assert not await doors.mark("a1", 0, 1_000, name=None, tag_ids=(), created_by="u1")

    unmarked = _Recorder(marked=False)
    doors = _parts((loops.SERVICE, unmarked), (wiring.CONTENT, _Recorder(get=None)))
    assert not await doors.mark("a1", 0, 1_000, name=None, tag_ids=(), created_by="u1")
    assert unmarked.named("create") == []


async def test_a_moment_near_the_end_runs_to_the_end_and_takes_its_tags() -> None:
    service = _Recorder(marked=False, create=SimpleNamespace(id="loop1"))
    content = _Recorder(get=SimpleNamespace(duration_ms=5_000))
    doors = _parts((loops.SERVICE, service), (wiring.CONTENT, content))

    assert await doors.mark("a1", 4_000, 9_000, name="End", tag_ids=("t1", "t2"), created_by="u1")

    [(_, _, made)] = [one for one in service.calls if one[0] == "create"]
    assert (made["start_ms"], made["end_ms"], made["duration_ms"]) == (4_000, 5_000, 5_000)
    assert [args for args in service.named("set_tag")] == [("loop1", "t1"), ("loop1", "t2")]


async def test_a_moment_on_a_file_of_no_known_length_or_one_the_loops_refuse() -> None:
    kept = _Recorder(marked=False, create=SimpleNamespace(id="loop1"))
    content = _Recorder(get=SimpleNamespace(duration_ms=None))
    doors = _parts((loops.SERVICE, kept), (wiring.CONTENT, content))
    assert await doors.mark("a1", 4_000, 9_000, name=None, tag_ids=(), created_by="u1")
    assert kept.calls[-1][2]["end_ms"] == 9_000, "nothing to cut it to"

    refusing = _Recorder(marked=False, create=Refused("past the end"))
    doors = _parts((loops.SERVICE, refusing), (wiring.CONTENT, content))
    assert not await doors.mark("a1", 4_000, 9_000, name=None, tag_ids=("t1",), created_by="u1")
    assert refusing.named("set_tag") == []


# --- a heart and stars --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("held", "favorite", "rating", "wrote"),
    [
        (None, True, 4, ["favorite", "rating"]),
        ({"favorite": 1, "rating": 2}, True, 4, []),
        ({"favorite": 0, "rating": 2}, True, 4, ["favorite"]),
        ({"favorite": 1, "rating": None}, True, 4, ["rating"]),
        (None, False, None, []),
    ],
)
@pytest.mark.parametrize("kind", ["person", "site", "tag"])
async def test_an_opinion_fills_only_what_this_library_has_not_said(
    kind: str,
    held: dict[str, Any] | None,
    favorite: bool,
    rating: int | None,
    wrote: list[str],
) -> None:
    table = f"{kind}_user_state"
    catalog, tags = _Recorder(), _Recorder()
    doors = _parts(
        (wiring.DATABASE, _Database({table: held})),
        (wiring.ACCESS, _access()),
        (people.SERVICE, catalog),
        (tags_ratings.SERVICE, tags),
    )

    said = await doors.opinion(kind, "e1", "u1", favorite=favorite, rating=rating)

    assert said is bool(wrote)
    written = [name for name, _, _ in (tags if kind == "tag" else catalog).calls]
    assert [one.rsplit("_", 1)[-1] for one in written] == wrote


async def test_an_opinion_on_a_tag_for_somebody_no_longer_here_is_not_written() -> None:
    tags = _Recorder()
    doors = _parts(
        (wiring.DATABASE, _Database({})),
        (wiring.ACCESS, _access(None)),
        (tags_ratings.SERVICE, tags),
    )

    assert not await doors.opinion("tag", "t1", "gone", favorite=True, rating=3)
    assert tags.calls == []


# --- a saved search -------------------------------------------------------------------------------


async def test_a_search_is_kept_once_and_never_past_what_a_person_may_hold() -> None:
    kept = [SimpleNamespace(kind=ASSET_WALL, name="Beach days")]
    service = _Recorder(saved_searches=kept)
    doors = _parts((wiring.ACCESS, _access()), (search.SERVICE, service))

    assert not await doors.keep_search("u1", "Beach   days", "beach")
    assert await doors.keep_search("u1", "Night", "night")
    assert service.named("save_search")[-1][1:] == ("Night", "night", ASSET_WALL)

    for refusal in (TooMany(), ValueError("not a query")):
        full = _Recorder(saved_searches=[], save_search=refusal)
        doors = _parts((wiring.ACCESS, _access()), (search.SERVICE, full))
        assert not await doors.keep_search("u1", "Night", "night")

    nobody = _parts((wiring.ACCESS, _access(None)), (search.SERVICE, service))
    assert not await nobody.keep_search("gone", "Night", "night")


# --- a stash-box id -------------------------------------------------------------------------------


def _box(**fields: Any) -> Any:
    shape = {"id": "b1", "endpoint": "https://stashdb.example/graphql/", "sites_are": "site"}
    shape.update(fields)
    return SimpleNamespace(**shape)


async def test_a_stash_box_id_is_linked_only_where_this_library_allows_it() -> None:
    def boxes(**answers: Any) -> _Recorder:
        defaults: dict[str, Any] = {
            "boxes": [_box()],
            "links_of": [],
            "kept_local": False,
            "link": SimpleNamespace(id="l1"),
        }
        defaults.update(answers)
        return _Recorder(**defaults)

    endpoint = "  HTTPS://STASHDB.EXAMPLE/graphql "

    async def went(service: _Recorder, kind: str = "person") -> Linked:
        doors = _parts((stash_boxes.SERVICE, service))
        answer: Linked = await doors.link(kind, "p1", endpoint, "remote", b"k")
        return answer

    assert await went(boxes(boxes=[])) is Linked.NO_BOX
    assert await went(boxes(boxes=[_box(sites_are="person")]), "site") is Linked.NOT_A_SITE
    assert await went(boxes(links_of=[SimpleNamespace(source_id="b1")])) is Linked.ALREADY
    assert await went(boxes(kept_local=True)) is Linked.KEPT_LOCAL
    assert await went(boxes(link=stash_boxes.StashBoxUnreachable("no answer"))) is Linked.NOT_ASKED
    assert await went(boxes(link=None)) is Linked.NOT_ASKED
    assert await went(boxes(), "site") is Linked.LINKED


async def test_a_files_stash_box_id_is_linked_through_the_scan_doors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[Any, ...]] = []

    async def link_known_scene(*args: Any, **kwargs: Any) -> Any:
        asked.append(args)
        return Linked.LINKED

    monkeypatch.setattr(stash_boxes, "link_known_scene", link_known_scene)
    service = _Recorder(boxes=[_box()])
    doors = _parts(
        (stash_boxes.SERVICE, service),
        (wiring.ACCESS, _access()),
        (wiring.SETTINGS_HUB, object()),
        (wiring.ENRICHER, object()),
        (wiring.NAMING, object()),
        (wiring.QUEUE, object()),
    )

    assert (
        await doors.link_file("a1", "https://stashdb.example/graphql", "r1", None) is Linked.LINKED
    )
    assert [args[2:5] for args in asked] == [("a1", "b1", "r1")]
    assert (
        await _parts((stash_boxes.SERVICE, _Recorder(boxes=[]))).link_file(
            "a1", "https://elsewhere.example", "r1", None
        )
        is Linked.NO_BOX
    )


# --- groupings, pictures and a new library --------------------------------------------------------


async def test_a_photo_set_or_collection_somebody_deleted_since_is_not_made_again() -> None:
    sets, groups = _Recorder(add=2), _Recorder(add=3)
    gone = _parts(
        (wiring.DATABASE, _Database({})),
        (photo_sets.SERVICE, sets),
        (collections.SERVICE, groups),
    )
    assert await gone.add_to_photo_set("s1", ["a1"], actor=ACTOR) is None
    assert await gone.add_to_collection("c1", ["a1"], actor=ACTOR) is None
    assert sets.calls == [] and groups.calls == []

    there = _parts(
        (wiring.DATABASE, _Database({"photo_sets": {"1": 1}, "collections": {"1": 1}})),
        (photo_sets.SERVICE, sets),
        (collections.SERVICE, groups),
    )
    assert await there.add_to_photo_set("s1", ["a1", "a2"], actor=ACTOR) == 2
    assert await there.add_to_collection("c1", ["a1"], actor=ACTOR) == 3


async def test_a_collection_is_made_only_for_somebody_who_is_still_here() -> None:
    groups = _Recorder(create=SimpleNamespace(id="c9"))
    here = _parts((wiring.ACCESS, _access()), (collections.SERVICE, groups))
    assert await here.make_collection("Favourites", "u1", actor=ACTOR) == "c9"

    gone = _parts((wiring.ACCESS, _access(None)), (collections.SERVICE, groups))
    assert await gone.make_collection("Favourites", "gone", actor=ACTOR) is None
    assert len(groups.named("create")) == 1


async def test_a_picture_fills_a_blank_and_never_replaces_one_chosen_here() -> None:
    covers = _Recorder(fill=True)
    doors = _parts((wiring.SUBJECT_COVERS, covers))

    assert await doors.picture("site", "s1", b"png", actor=ACTOR)
    [(name, _, kwargs)] = covers.calls
    assert name == "fill" and kwargs["box"] is None


async def test_a_new_library_is_made_or_refused_in_words_the_run_can_say() -> None:
    made = _Recorder(create=SimpleNamespace(id="lib1"))
    viewer = cast("Any", SimpleNamespace(id="u1"))
    assert await _parts((backup.LIBRARIES, made)).new_library("Second", viewer, None) == "lib1"

    class _Taken(backup.LibraryError):
        status = 422

    for refusal, status in (
        (_Taken("That name is taken."), 422),
        (backup.BackupError("Busy."), 409),
    ):
        refusing = _Recorder(create=refusal)
        with pytest.raises(StashRefused) as refused:
            await _parts((backup.LIBRARIES, refusing)).new_library("Second", viewer, None)
        assert (str(refused.value), refused.value.status) == (str(refusal), status)
