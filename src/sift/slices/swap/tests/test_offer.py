# SPDX-License-Identifier: AGPL-3.0-or-later
"""The host's offer: what was chosen becomes which files, what may travel, and the titles.

The failure worth guarding here is a WIDE one. An offer that forgot a rule does not fail: it
sends somebody a file from the vault, or one the host said never leaves this device, and the screen
looks exactly as it would have anyway. So every test that proves something is left out also proves
the same read reaches the file beside it: an empty answer proves nothing unless something was there.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass
from typing import Any

import pytest
from starlette.datastructures import QueryParams

import sift.slices.search.schema  # registers the table saved filters are kept in
import sift.slices.stash_boxes.schema  # noqa: F401 (registers the stash-box link tables)
from sift.kernel.access import AssetFilter, Repository, Role, Viewer
from sift.kernel.content import songs
from sift.kernel.content.identity import Asset
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.wire import Wire
from sift.slices.search.filters import FilterCompiler
from sift.slices.search.service import ASSET_WALL, SearchService
from sift.slices.swap import models
from sift.slices.swap.models import Chosen, OfferedFaces, chosen_list
from sift.slices.swap.offer import (
    Offered,
    OfferedName,
    PersonFacts,
    Selection,
    build,
    faces_of,
    files_of,
    offer_for,
    people_of,
    title,
    weigh,
)
from sift.testing.fixtures import create_user, hide

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000


class _NoFilters:
    """A host with no saved filters."""

    async def saved_filter(self, viewer: Viewer, saved_id: str) -> AssetFilter | None:
        return None


class _SavedFilters:
    """The search feature's saved filters, parsed by its own compiler: the shape the composition
    root wires."""

    def __init__(self, database: Database, access: Repository) -> None:
        compiler = FilterCompiler(access)
        self._service = SearchService(database, access, compiler)
        self._compiler = compiler

    async def saved_filter(self, viewer: Viewer, saved_id: str) -> AssetFilter | None:
        for kept in await self._service.saved_searches(viewer):
            if kept.id == saved_id and kept.kind == ASSET_WALL:
                return await self._compiler.constrain(viewer, QueryParams(kept.query))
        return None


@dataclass
class Library:
    db: Database
    access: Repository
    admin: Viewer
    ids: dict[str, str]

    def name_of(self, asset_id: str) -> str:
        return next(name for name, one in self.ids.items() if one == asset_id)


async def _asset(
    db: Database, asset_id: str, *, media_type: str = "video", added: int = 0, size: int = 100
) -> None:
    await db.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at, size_bytes,"
        " oshash, video_phash, phash, fingerprint_version, duration_ms, width, height)"
        " VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, 1, ?, 1920, 1080)",
        (
            asset_id,
            f"digest-{asset_id}",
            media_type,
            _EPOCH + added,
            size,
            f"os-{asset_id}"[:16],
            "0123456789abcdef" if media_type == "video" else None,
            "fedcba9876543210" if media_type == "image" else None,
            60_000 if media_type == "video" else None,
        ),
    )
    await db.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " status, first_seen_at, last_seen_at) VALUES (?, ?, 'r1', 'f1', ?, ?, 'present', ?, ?)",
        (new_id(), asset_id, f"shoot/{asset_id}.mp4", f"{asset_id}.mp4", _EPOCH, _EPOCH),
    )


@pytest.fixture
async def library(temp_db: Database, access: Repository) -> Library:
    """Nine files: one reached by each kind of chosen thing, and four the rules must leave out.

    `person` is on `p1` and `p2`, `site` reaches `s1`, `tag` is on `t1`, `collection` holds `c1`,
    `photo_set` holds `ps1`. `stray` is reached by nothing. `vaulted` is in the host's Hidden and
    reached by nothing; `local` is under the person and kept local by its own switch; `local_tag` is
    under the person and under a tag that is kept local.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    db = temp_db
    await db.execute(
        "INSERT INTO library_roots (id, name, abs_path, kind, created_at)"
        " VALUES ('r1', 'root', '/library/r1', 'local', ?)",
        (_EPOCH,),
    )
    await db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
        " VALUES ('f1', 'r1', NULL, 'shoot', 'shoot')"
    )
    ids = {
        name: new_id()
        for name in (
            "p1",
            "p2",
            "s1",
            "t1",
            "c1",
            "ps1",
            "stray",
            "vaulted",
            "local",
            "local_tag",
        )
    }
    for order, (name, asset_id) in enumerate(ids.items()):
        await _asset(db, asset_id, media_type="image" if name == "p2" else "video", added=order)
    for key, value in {
        "person": new_id(),
        "site": new_id(),
        "username": new_id(),
        "tag": new_id(),
        "kept_tag": new_id(),
        "collection": new_id(),
        "photo_set": new_id(),
    }.items():
        ids[key] = value
    await db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'Juno Pellerin', 'juno', ?)",
        (ids["person"], _EPOCH),
    )
    for name in ("p1", "p2", "local", "local_tag"):
        await db.execute(
            "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
            (ids[name], ids["person"]),
        )
    await db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, 'Northlight Media', 'n', ?)",
        (ids["site"], _EPOCH),
    )
    await db.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at)"
        " VALUES (?, ?, 'northlight_clips', 'n', ?)",
        (ids["username"], ids["site"], _EPOCH),
    )
    for name in ("s1", "p1"):
        await db.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
            (ids[name], ids["username"]),
        )
    await db.execute(
        "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, 'beach', 'beach', ?),"
        " (?, 'private', 'private', ?)",
        (ids["tag"], _EPOCH, ids["kept_tag"], _EPOCH),
    )
    await db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?), (?, ?)",
        (ids["t1"], ids["tag"], ids["local_tag"], ids["kept_tag"]),
    )
    await db.execute(
        "INSERT INTO collections (id, name, name_sort, created_at) VALUES (?, 'kept', 'kept', ?)",
        (ids["collection"], _EPOCH),
    )
    await db.execute(
        "INSERT INTO collection_items (collection_id, asset_id, added_at) VALUES (?, ?, ?)",
        (ids["collection"], ids["c1"], _EPOCH),
    )
    await db.execute(
        "INSERT INTO photo_sets (id, name, name_sort, origin, created_at)"
        " VALUES (?, 'set', 'set', 'manual', ?)",
        (ids["photo_set"], _EPOCH),
    )
    await db.execute(
        "INSERT INTO photo_set_items (photo_set_id, asset_id, position, added_at)"
        " VALUES (?, ?, 0, ?)",
        (ids["photo_set"], ids["ps1"], _EPOCH),
    )
    await hide(db, "asset", ids["vaulted"], admin.id)
    await db.execute("UPDATE assets SET keep_local = 1 WHERE id = ?", (ids["local"],))
    await db.execute("UPDATE tags SET keep_local = 1 WHERE id = ?", (ids["kept_tag"],))
    return Library(db=db, access=access, admin=admin, ids=ids)


async def _names(library: Library, chosen: list[Chosen], viewer: Viewer | None = None) -> set[str]:
    picked = await files_of(
        library.access, library.db, _NoFilters(), viewer or library.admin, chosen
    )
    return {library.name_of(one.asset.id) for one in picked.files}


async def test_each_kind_reaches_its_files_and_the_union_reaches_them_all(library: Library) -> None:
    ids = library.ids
    reached = {
        "person": {"p1", "p2"},
        "site": {"s1", "p1"},
        "tag": {"t1"},
        "collection": {"c1"},
        "photo_set": {"ps1"},
    }
    for kind, expected in reached.items():
        chosen = [Chosen(kind=typing.cast(models.ChosenKind, kind), id=ids[kind])]
        assert await _names(library, chosen) == expected, kind
    everything = [
        Chosen(kind=typing.cast(models.ChosenKind, kind), id=ids[kind]) for kind in reached
    ]
    assert await _names(library, everything) == {"p1", "p2", "s1", "t1", "c1", "ps1"}


async def test_a_file_in_hidden_is_offered_whatever_the_session_unlocked(library: Library) -> None:
    """Hidden governs what a screen shows, never what a swap sends: "Do not swap" does."""
    ids = library.ids
    # Hidden really is shut for this session: the wall does not show the file.
    assert ids["vaulted"] not in await library.access.assets_of(library.admin, [ids["vaulted"]])
    picked = [Chosen(kind="asset", id=ids["vaulted"])]
    assert await _names(library, picked) == {"vaulted"}
    await library.db.execute(
        "UPDATE assets SET keep_from_swaps = 1 WHERE id = ?", (ids["vaulted"],)
    )
    assert await _names(library, picked) == set()


async def test_the_weight_of_the_picks_is_the_offers_own_read_and_hidden_adds_too(
    library: Library,
) -> None:
    """What the sender is told it will send is what the offer would hold: the same files, each
    once, a file in Hidden among them whatever the session unlocked."""
    ids = library.ids
    await library.db.execute("UPDATE assets SET size_bytes = 1500000000 WHERE id = ?", (ids["p1"],))
    await library.db.execute(
        "UPDATE assets SET size_bytes = 900000000 WHERE id = ?", (ids["vaulted"],)
    )
    unlocked = Viewer(id=library.admin.id, role=Role.ADMIN, show_hidden=True)
    person = Chosen(kind="person", id=ids["person"])
    site = Chosen(kind="site", id=ids["site"])

    assert await weigh(library.access, _NoFilters(), unlocked, [person]) == (2, 1_500_000_100)
    # `p1` is under the person and the Site: counted once.
    assert await weigh(library.access, _NoFilters(), unlocked, [person, site]) == (3, 1_500_000_200)
    assert await weigh(library.access, _NoFilters(), unlocked, []) == (0, 0)
    vaulted = Chosen(kind="asset", id=ids["vaulted"])
    assert await weigh(library.access, _NoFilters(), library.admin, [vaulted]) == (1, 900_000_000)
    gone = Viewer(id="nobody", role=Role.ADMIN)
    assert await weigh(library.access, _NoFilters(), gone, [person]) == (0, 0)


async def test_a_kept_local_file_is_left_out_by_its_own_switch_or_a_tag_above_it(
    library: Library,
) -> None:
    ids = library.ids
    # Both are reachable when the rule does not apply: on the person's own wall.
    seen = await library.access.assets_of(library.admin, [ids["local"], ids["local_tag"]])
    assert set(seen) == {ids["local"], ids["local_tag"]}
    chosen = [Chosen(kind="person", id=ids["person"])]
    names = await _names(library, chosen)
    assert "local" not in names and "local_tag" not in names
    assert names == {"p1", "p2"}


async def test_a_file_marked_do_not_swap_is_never_offered_by_its_own_mark_or_one_above_it(
    library: Library,
) -> None:
    """The file's own mark, the tag on it and the Site it is filed under: each keeps it out, and
    the same read reaches the files beside it, so an empty answer is not the proof."""
    ids, db = library.ids, library.db
    chosen = [Chosen(kind="person", id=ids["person"]), Chosen(kind="tag", id=ids["tag"])]
    assert await _names(library, chosen) == {"p1", "p2", "t1"}

    await db.execute("UPDATE assets SET keep_from_swaps = 1 WHERE id = ?", (ids["p2"],))
    await db.execute("UPDATE tags SET keep_from_swaps = 1 WHERE id = ?", (ids["tag"],))
    assert await _names(library, chosen) == {"p1"}

    # A Site marked keeps out the file filed under it, whoever it was chosen through.
    await db.execute("UPDATE sites SET keep_from_swaps = 1 WHERE id = ?", (ids["site"],))
    assert await _names(library, chosen) == set()


async def test_a_person_marked_do_not_swap_is_neither_offered_nor_named(library: Library) -> None:
    """Their files are left out, so their name has no file to travel with, and it does not."""
    ids, db = library.ids, library.db
    chosen = [Chosen(kind="person", id=ids["person"]), Chosen(kind="tag", id=ids["tag"])]
    before = await files_of(library.access, db, _NoFilters(), library.admin, chosen)
    assert [one.name for one in before.people] == ["Juno Pellerin"]

    await db.execute("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (ids["person"],))
    after = await files_of(library.access, db, _NoFilters(), library.admin, chosen)

    assert {library.name_of(one.asset.id) for one in after.files} == {"t1"}
    assert after.people == ()
    offer = build(after, await people_of(db, after))
    assert "Juno Pellerin" not in offer.model_dump_json()


async def test_one_file_picked_in_swap_mode_offers_that_file(library: Library) -> None:
    ids = library.ids
    assert await _names(library, [Chosen(kind="asset", id=ids["stray"])]) == {"stray"}
    # And a file in Hidden by being picked, Hidden being no door of a swap.
    assert await _names(library, [Chosen(kind="asset", id=ids["vaulted"])]) == {"vaulted"}


async def test_a_heic_or_avif_is_offered_like_any_other_picture(library: Library) -> None:
    """Their location items are taken out on the way (`transfer.strip_heif`), so they are offered
    as every other picture is, rather than left out."""
    ids, db = library.ids, library.db
    chosen = [Chosen(kind="person", id=ids["person"])]
    await db.execute("UPDATE assets SET mime = 'image/heic' WHERE id = ?", (ids["p2"],))
    await db.execute("UPDATE assets SET mime = 'image/avif' WHERE id = ?", (ids["p1"],))
    assert await _names(library, chosen) == {"p1", "p2"}


async def test_a_format_this_device_cannot_strip_is_never_offered(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every format Sift takes has a strip today, so the list is empty; a format added without one
    is left out of the offer the day it is added, never sent with whatever it carries."""
    from sift.slices.swap import offer as offer_module

    ids, db = library.ids, library.db
    await db.execute("UPDATE assets SET mime = 'image/heic' WHERE id = ?", (ids["p2"],))
    monkeypatch.setattr(offer_module, "NEVER_SENT_MIMES", frozenset({"image/heic"}))

    assert await _names(library, [Chosen(kind="person", id=ids["person"])]) == {"p1"}


async def test_a_saved_filter_is_the_search_features_own_parse(library: Library) -> None:
    ids = library.ids
    saved_id = new_id()
    await library.db.execute(
        "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
        " VALUES (?, ?, 'asset', 'beach', 'q=tags%3Abeach', ?)",
        (saved_id, library.admin.id, _EPOCH),
    )
    search = _SavedFilters(library.db, library.access)
    picked = await files_of(
        library.access,
        library.db,
        search,
        library.admin,
        [Chosen(kind="filter", id=saved_id), Chosen(kind="collection", id=ids["collection"])],
    )
    assert {library.name_of(one.asset.id) for one in picked.files} == {"t1", "c1"}
    # Somebody else's id names nothing.
    other = await create_user(library.db, Role.ADMIN)
    none = await files_of(
        library.access, library.db, search, other, [Chosen(kind="filter", id=saved_id)]
    )
    assert none.files == ()


async def test_the_offer_carries_the_person_the_site_and_the_titles(library: Library) -> None:
    ids = library.ids
    await library.db.execute(
        "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, 'Rafe Underhill')",
        (new_id(), ids["person"]),
    )
    await library.db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
        " VALUES ('b1', 'box', 'https://box.test/g', 0)"
    )
    await library.db.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, 'b1', 'remote-1', '{}', ?)",
        (ids["person"], _EPOCH),
    )
    chosen = [Chosen(kind="person", id=ids["person"]), Chosen(kind="site", id=ids["site"])]
    offer = await offer_for(library.access, library.db, _NoFilters(), library.admin, chosen)
    assert [one.name for one in offer.people] == ["Juno Pellerin"]
    assert offer.people[0].aliases == ["Rafe Underhill"]
    assert offer.people[0].boxes == ["remote-1"]
    assert [one.name for one in offer.sites] == ["Northlight Media"]
    by_key = {library.name_of(one.key): one for one in offer.files}
    assert by_key["p1"].title == "Juno Pellerin, clip 1 of 2"
    assert by_key["p2"].title == "Juno Pellerin, picture 2 of 2"
    assert by_key["s1"].title == "Northlight Media, clip 1 of 1"
    assert by_key["p1"].site == "Northlight Media"
    assert by_key["p1"].username == "northlight_clips"
    assert by_key["p1"].people == [0] and by_key["s1"].people == []
    assert by_key["p2"].phash == "fedcba9876543210" and by_key["p2"].video_phash is None
    # Off, the stash-box ids stay here.
    kept = await offer_for(
        library.access, library.db, _NoFilters(), library.admin, chosen, share_boxes=False
    )
    assert kept.people[0].boxes == []


async def test_a_files_song_travels_with_it_and_only_to_a_side_that_takes_songs(
    library: Library,
) -> None:
    ids = library.ids
    async with library.db.write() as connection:
        await songs.name_song_on(
            connection, ids["p1"], "Harbour Lights", source="acoustid", recording_id="rec-1"
        )
    chosen = [Chosen(kind="person", id=ids["person"])]
    offer = await offer_for(library.access, library.db, _NoFilters(), library.admin, chosen)

    by_key = {library.name_of(one.key): one for one in offer.files}
    assert by_key["p1"].song == models.OfferedSong(name="Harbour Lights", recording="rec-1")
    assert by_key["p2"].song is None, "a file on no song carries none"
    sent = offer.as_sent(songs=True)
    assert [one.get("song") for one in sent["files"]] == [
        {"name": "Harbour Lights", "artists": [], "recording": "rec-1"},
        None,
    ]
    # To an install from before songs travelled: no `song` key at all, which it would refuse.
    older = offer.as_sent(songs=False)
    assert all("song" not in one for one in older["files"])
    assert models.Offer.model_validate(older).files[0].song is None


async def test_a_songs_artists_travel_and_a_hidden_song_does_too(library: Library) -> None:
    """The artists a song credits ride with it, in order; and a song the admin hid travels with
    Hidden shut (`Repository.visible_song`, read with Hidden open)."""
    ids = library.ids
    async with library.db.write() as connection:
        song_id = await songs.name_song_on(
            connection, ids["p1"], "Harbour Lights", source="acoustid", recording_id="rec-1"
        )
        assert song_id is not None
        await songs.credit(
            connection, song_id, ["Odo Venn", "Ilsa Moor"], made=songs.UNSAID, source=None
        )
    chosen = [Chosen(kind="person", id=ids["person"])]
    offer = await offer_for(library.access, library.db, _NoFilters(), library.admin, chosen)
    by_key = {library.name_of(one.key): one for one in offer.files}
    assert by_key["p1"].song == models.OfferedSong(
        name="Harbour Lights", artists=["Odo Venn", "Ilsa Moor"], recording="rec-1"
    )
    await library.db.execute(
        "INSERT INTO song_user_state (song_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, 0, 0)",
        (song_id, library.admin.id),
    )
    assert await library.access.visible_song(library.admin, song_id) is None
    hidden = await offer_for(library.access, library.db, _NoFilters(), library.admin, chosen)
    assert {library.name_of(one.key): one for one in hidden.files}["p1"].song == by_key["p1"].song


async def test_a_chosen_person_with_nothing_offered_is_not_named(library: Library) -> None:
    ids = library.ids
    await library.db.execute("UPDATE people SET keep_local = 1 WHERE id = ?", (ids["person"],))
    chosen = [Chosen(kind="person", id=ids["person"]), Chosen(kind="tag", id=ids["tag"])]
    picked = await files_of(library.access, library.db, _NoFilters(), library.admin, chosen)
    assert {library.name_of(one.asset.id) for one in picked.files} == {"t1"}
    assert picked.people == ()
    assert await people_of(library.db, picked) == []


def _asset_row(asset_id: str, media_type: str, added: int) -> Asset:
    return Asset(
        id=asset_id,
        identity=f"digest-{asset_id}",
        media_type=media_type,
        mime=None,
        width=None,
        height=None,
        duration_ms=None,
        fps=None,
        size_bytes=10,
        container=None,
        vcodec=None,
        acodec=None,
        bit_depth=None,
        phash=None,
        videohash=None,
        original_filename="never-sent.mp4",
        added_at=added,
        probed_at=None,
    )


def test_titles_count_in_the_first_offered_persons_set() -> None:
    """A file under two people is titled under the first and counted in both people's sets."""
    first, second = (
        PersonFacts(id="pa", name="Juno Pellerin"),
        PersonFacts(id="pb", name="Orla Tennant"),
    )
    files = (
        Offered(asset=_asset_row("a1", "video", 1), people=("pa",)),
        Offered(asset=_asset_row("a2", "gif", 2), people=("pa", "pb")),
        Offered(asset=_asset_row("a3", "video", 3), people=("pb",)),
        Offered(asset=_asset_row("a4", "image", 4), site="Quiet Harbour"),
        Offered(asset=_asset_row("a5", "video", 5)),
        Offered(asset=_asset_row("a6", "video", 6)),
    )
    selection = Selection(
        files=files, people=(OfferedName("pa", first.name), OfferedName("pb", second.name))
    )
    offer = build(selection, [first, second])
    assert [one.title for one in offer.files] == [
        "Juno Pellerin, clip 1 of 2",
        "Juno Pellerin, GIF 2 of 2",
        "Orla Tennant, clip 2 of 2",
        "Quiet Harbour, picture 1 of 1",
        "Clip 1 of 2",
        "Clip 2 of 2",
    ]
    assert offer.files[1].people == [0, 1]
    assert title(None, "image", 3, 9) == "Picture 3 of 9"


def test_the_offer_carries_each_files_own_name_and_only_the_name() -> None:
    """The wire carries the name a file has on the host's disk, so the guest lands it under it; a
    folder in front of it is cut off before it is offered, and a file with none offers none."""
    selection = Selection(
        files=(
            Offered(asset=_asset_row("a1", "video", 1), name="beach day (2).mp4"),
            Offered(asset=_asset_row("a2", "image", 2), name="Holidays/2024/IMG_2041.JPEG"),
            Offered(asset=_asset_row("a3", "image", 3), name="C:\\Users\\someone\\x.png"),
            Offered(asset=_asset_row("a4", "image", 4)),
        )
    )
    offer = build(selection, [])
    assert [one.name for one in offer.files] == [
        "beach day (2).mp4",
        "IMG_2041.JPEG",
        "x.png",
        None,
    ]
    # And across the wire as the guest reads it: the same names, through the strict shapes.
    again = models.Offer.model_validate(offer.model_dump(mode="json"))
    assert [one.name for one in again.files] == [one.name for one in offer.files]


#: Everything the offer may carry, by class. A field added to any of these has to be argued for
#: here, which is the point: this is the list of facts one library tells another.
_ALLOWED: dict[type[Wire], set[str]] = {
    models.Offer: {"files", "people", "sites"},
    models.OfferedFile: {
        "key",
        "oshash",
        "size",
        "identity",
        "video_phash",
        "phash",
        "fingerprint_version",
        "duration_ms",
        "kind",
        "width",
        "height",
        "title",
        "site",
        "username",
        "people",
        # The file's own name on disk, the leaf alone, so it lands under it (the rule: a received
        # file keeps its own name). Never a folder or a path: `_leaf` cuts one off before it is offered.
        "name",
        # The file's song, to a side whose hello said it takes one (`Offer.as_sent`).
        "song",
    },
    models.OfferedSong: {"name", "artists", "recording"},
    models.OfferedPerson: {"name", "aliases", "boxes", "faces"},
    # How many confirmed faces the host has of them, to a side whose hello takes it.
    models.OfferedFaces: {"recognizer", "dimension", "faces", "confirmed"},
    # The picture travels only when the two sides' face models differ (`FaceDescriptions`).
    models.OfferedFace: {"digest", "quality", "vector", "picture"},
    models.OfferedSite: {"name"},
    models.Diff: {"wanted", "people"},
}

#: What must never cross, whatever it is called on a class.
_FORBIDDEN = ("url", "path", "filename", "rating", "note", "history", "folder", "address")


def test_the_offer_carries_nothing_it_may_not() -> None:
    for model, allowed in _ALLOWED.items():
        fields = set(model.model_fields)
        assert fields == allowed, model.__name__
        for name in fields:
            assert not any(word in name for word in _FORBIDDEN), (model.__name__, name)


def test_the_offer_refuses_what_a_lying_peer_might_send() -> None:
    good: dict[str, Any] = {
        "files": [
            {"key": "k", "size": 1, "identity": "i", "kind": "video", "title": "t", "people": [0]}
        ],
        "people": [{"name": "Juno Pellerin"}],
        "sites": [],
    }
    assert models.Offer.model_validate(good).files[0].people == [0]
    for bad in (
        {**good, "people": []},  # an index into nobody
        {**good, "files": [*good["files"], *good["files"]]},  # one key twice
        {**good, "extra": 1},  # a field nobody declared
        {**good, "files": [{**good["files"][0], "path": "/x"}]},  # a path smuggled in
    ):
        with pytest.raises(ValueError):
            models.Offer.model_validate(bad)


def test_the_chosen_list_takes_one_saved_filter_and_folds_repeats() -> None:
    picked = chosen_list(
        [
            {"kind": "person", "id": "p"},
            {"kind": "person", "id": "p"},
            {"kind": "filter", "id": "f"},
        ]
    )
    assert [(one.kind, one.id) for one in picked] == [("person", "p"), ("filter", "f")]
    with pytest.raises(ValueError):
        chosen_list([{"kind": "filter", "id": "a"}, {"kind": "filter", "id": "b"}])
    with pytest.raises(ValueError):
        chosen_list([{"kind": "folder", "id": "a"}])


def test_face_descriptions_travel_as_the_pack_stores_them_best_first() -> None:
    described = faces_of(
        "arcface-r100",
        2,
        [("low", 0.2, (1.0, 2.0)), ("high", 0.9, (0.5, -0.5)), ("wrong", 0.99, (1.0,))],
    )
    assert isinstance(described, OfferedFaces)
    assert [one.digest for one in described.faces] == ["high", "low"]
    # Little-endian four-byte floats, base64: 0.5 and -0.5.
    assert described.faces[0].vector == "AAAAPwAAAL8="
    # Every reference handed in counts as confirmed, the one another model wrote included: the
    # number the person's page bands, of which the best travel.
    assert described.confirmed == 3


def test_a_persons_confirmed_count_is_left_out_for_a_side_that_does_not_take_it() -> None:
    offer = models.Offer(
        people=[
            models.OfferedPerson(
                name="Juno Pellerin",
                faces=models.OfferedFaces(recognizer="m", dimension=2, faces=[], confirmed=7),
            ),
            models.OfferedPerson(name="Odo Venne"),
        ]
    )
    assert offer.as_sent(songs=True, counts=True)["people"][0]["faces"]["confirmed"] == 7
    older = offer.as_sent(songs=True)
    assert "confirmed" not in older["people"][0]["faces"]
    assert older["people"][1]["faces"] is None
    # What an older install reads back is an offer it declares every key of.
    assert models.Offer.model_validate(older).people[0].faces is not None


def test_what_was_chosen_is_a_bounded_list_or_it_is_refused_with_words() -> None:
    with pytest.raises(ValueError, match=r"Choose what to offer\."):
        chosen_list({"kind": "person", "id": "p"})
    too_many = [{"kind": "person", "id": f"p{n}"} for n in range(models.MAX_CHOSEN + 1)]
    with pytest.raises(ValueError, match="at most"):
        chosen_list(too_many)


def test_a_peer_cannot_send_a_song_whose_artist_is_longer_than_an_offer_carries() -> None:
    assert models.OfferedSong(name="Harbour Lights", artists=["Example Band"]).artists
    with pytest.raises(ValueError, match="too long"):
        models.OfferedSong(name="Harbour Lights", artists=["x" * 301])


def test_a_peer_cannot_send_a_name_longer_than_an_offer_carries() -> None:
    long = "x" * 301
    for person in ({"name": "Juno", "aliases": [long]}, {"name": "Juno", "boxes": [long]}):
        with pytest.raises(ValueError, match="too long"):
            models.OfferedPerson.model_validate(person)


def test_an_answer_naming_more_people_or_longer_ids_than_an_offer_could_is_refused() -> None:
    with pytest.raises(ValueError, match="more people"):
        models.Diff(people={n: None for n in range(models.MAX_OFFER_PEOPLE + 1)})
    with pytest.raises(ValueError, match="no install could have made"):
        models.Diff(wanted=["k" * 65])
    with pytest.raises(ValueError, match="no install could have made"):
        models.Diff(people={0: "p" * 65})
    assert models.Diff(wanted=["k"], people={0: None}).wanted == ["k"]


def test_taking_an_offer_names_only_places_and_keys_the_offer_could_hold() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        models.TakeOffer(skipped=[-1])
    with pytest.raises(ValueError, match="could not have carried"):
        models.TakeOffer(unticked=["k" * 65])
    assert models.TakeOffer(skipped=[0], unticked=["k"]).skipped == [0]


async def test_a_host_whose_account_is_gone_offers_nothing(library: Library) -> None:
    """Nobody to read as (the user removed or disabled since the swap was started), and so
    nothing is offered rather than everything."""
    gone = Viewer(id=new_id(), role=Role.ADMIN)

    picked = await files_of(
        library.access,
        library.db,
        _NoFilters(),
        gone,
        [Chosen(kind="person", id=library.ids["person"])],
    )

    assert picked.files == () and picked.people == ()


async def test_a_saved_filter_reaching_past_one_page_is_read_to_its_end(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filter is read a wall's page at a time, seeking past the last file: with a page of one
    file, a full page asks for the next, and the file it reaches arrives once."""
    from sift.slices.swap import offer as offer_module

    monkeypatch.setattr(offer_module, "MAX_PAGE_SIZE", 1)
    saved_id = new_id()
    await library.db.execute(
        "INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)"
        " VALUES (?, ?, 'asset', 'beach', 'q=tags%3Abeach', ?)",
        (saved_id, library.admin.id, _EPOCH),
    )

    picked = await files_of(
        library.access,
        library.db,
        _SavedFilters(library.db, library.access),
        library.admin,
        [Chosen(kind="filter", id=saved_id)],
    )

    assert [library.name_of(one.asset.id) for one in picked.files] == ["t1"]


class _Described:
    """The face feature, as the offer asks it: two fingerprints for whoever is asked about, and
    their pictures when the guest named another model. Records what it was asked."""

    def __init__(self, *, nobody: frozenset[str] = frozenset()) -> None:
        self.asked: list[tuple[list[str], str | None]] = []
        self._nobody = nobody

    async def descriptions(
        self, person_ids: typing.Sequence[str], *, peer_model: str | None = None
    ) -> dict[str, OfferedFaces]:
        self.asked.append((list(person_ids), peer_model))
        pictures = (
            {"a": b"\xff\xd8\xffA", "b": b"\xff\xd8\xffB"}
            if peer_model not in (None, "ours")
            else None
        )
        return {
            person_id: faces_of(
                "ours", 2, [("a", 0.9, [0.5, -0.5]), ("b", 0.8, [0.1, 0.2])], pictures=pictures
            )
            for person_id in person_ids
            if person_id not in self._nobody
        }


async def _somebody(library: Library, name: str) -> str:
    person_id = new_id()
    await library.db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (person_id, name, name.casefold(), _EPOCH),
    )
    return person_id


async def test_facial_fingerprints_offer_the_person_with_no_files(library: Library) -> None:
    """The category a file-only swap lacks: the person, their names and fingerprints, no file."""
    ada = await _somebody(library, "Ada Example")
    chosen = [Chosen(kind="facial_fingerprints", id=ada)]
    offer = await offer_for(
        library.access, library.db, _NoFilters(), library.admin, chosen, faces=_Described()
    )
    assert offer.files == []
    assert [one.name for one in offer.people] == ["Ada Example"]
    faces = offer.people[0].faces
    assert faces is not None and [one.digest for one in faces.faces] == ["a", "b"]
    # The models agree (the guest named none), so the numbers go alone.
    assert all(one.picture is None for one in faces.faces)


async def test_facial_fingerprints_pass_the_same_doors_a_person_does(library: Library) -> None:
    """Never somebody kept from swaps or kept local, never one with nothing to send, and a person
    offered through a file is named once. Hidden is not one of the doors."""
    ids, db = library.ids, library.db
    kept = await _somebody(library, "Bea Sample")
    local = await _somebody(library, "Ava Example")
    vaulted = await _somebody(library, "Cy Fixture")
    empty = await _somebody(library, "Dee Fixture")
    shown = await _somebody(library, "Eve Fixture")
    await db.execute("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (kept,))
    await db.execute("UPDATE people SET keep_local = 1 WHERE id = ?", (local,))
    await hide(db, "person", vaulted, library.admin.id)
    chosen = [
        Chosen(kind="person", id=ids["person"]),
        *(
            Chosen(kind="facial_fingerprints", id=one)
            for one in (ids["person"], kept, local, vaulted, empty, shown)
        ),
    ]
    offer = await offer_for(
        library.access,
        library.db,
        _NoFilters(),
        library.admin,
        chosen,
        faces=_Described(nobody=frozenset({empty})),
    )
    assert [one.name for one in offer.people] == ["Juno Pellerin", "Cy Fixture", "Eve Fixture"]


async def test_a_person_in_hidden_is_offered_and_do_not_swap_keeps_them_home(
    library: Library,
) -> None:
    """Hidden is not what keeps a person out of a swap: "Do not swap" is."""
    ids, db = library.ids, library.db
    await hide(db, "person", ids["person"], library.admin.id)
    chosen = [Chosen(kind="person", id=ids["person"])]

    offer = await offer_for(library.access, db, _NoFilters(), library.admin, chosen)
    assert [one.name for one in offer.people] == ["Juno Pellerin"]

    await db.execute("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (ids["person"],))
    kept = await offer_for(library.access, db, _NoFilters(), library.admin, chosen)
    assert kept.people == []


async def test_the_pictures_go_only_when_the_guest_uses_the_other_model(library: Library) -> None:
    ada = await _somebody(library, "Ada Example")
    chosen = [Chosen(kind="facial_fingerprints", id=ada)]
    described = _Described()
    same = await offer_for(
        library.access,
        library.db,
        _NoFilters(),
        library.admin,
        chosen,
        faces=described,
        peer_model="ours",
    )
    other = await offer_for(
        library.access,
        library.db,
        _NoFilters(),
        library.admin,
        chosen,
        faces=described,
        peer_model="theirs",
    )
    assert [one[1] for one in described.asked] == ["ours", "theirs"]
    assert same.people[0].faces is not None
    assert all(one.picture is None for one in same.people[0].faces.faces)
    assert other.people[0].faces is not None
    assert [one.picture for one in other.people[0].faces.faces] == ["/9j/QQ==", "/9j/Qg=="]


def test_pictures_past_the_offers_budget_go_as_numbers_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.slices.swap import offer as offer_module

    monkeypatch.setattr(offer_module, "MAX_OFFER_PICTURES", 10)
    faces = faces_of(
        "ours",
        2,
        [("a", 0.9, [0.5, -0.5]), ("b", 0.8, [0.1, 0.2])],
        pictures={"a": b"\xff\xd8\xffA", "b": b"\xff\xd8\xffB"},
    )
    built = build(
        Selection(people=(OfferedName(id="p", name="Ada Example"),)),
        [PersonFacts(id="p", name="Ada Example", faces=faces)],
    )
    kept = built.people[0].faces
    assert kept is not None
    assert [one.picture for one in kept.faces] == ["/9j/QQ==", None]
    assert [one.vector for one in kept.faces] == [one.vector for one in faces.faces]


async def test_a_chosen_song_offers_the_files_that_carry_it_hidden_or_not(
    library: Library,
) -> None:
    """A song is a thing a swap can pick, through the grammar's own `songs:` leaf: its files,
    weighed as the offer reads them. A song the sender hid offers the same, Hidden being no door
    of a swap."""
    ids = library.ids
    async with library.db.write() as connection:
        song_id = await songs.name_song_on(
            connection, ids["t1"], "Harbour Lights", source="acoustid", recording_id="rec-1"
        )
        assert song_id is not None
        await songs.put_on_by_hand(connection, ids["c1"], song_id)
        # And a file of the song kept local by its own switch: never offered.
        await songs.put_on_by_hand(connection, ids["local"], song_id)
    chosen = [Chosen(kind="song", id=song_id)]
    assert await _names(library, chosen) == {"t1", "c1"}
    unlocked = Viewer(id=library.admin.id, role=Role.ADMIN, show_hidden=True)
    assert await weigh(library.access, _NoFilters(), unlocked, chosen) == (2, 200)
    await hide(library.db, "song", song_id, library.admin.id)
    assert await _names(library, chosen) == {"t1", "c1"}
    assert await weigh(library.access, _NoFilters(), library.admin, chosen) == (2, 200)
