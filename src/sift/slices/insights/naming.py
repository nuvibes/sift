# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the things a list on Insights names are called, and the address of each one's picture.

Asked of the access layer, so absent means "not allowed" and "not there" together. A picture's
address carries the token its own wall puts on it, so the browser keeps it for a week rather than
asking on every visit (`kernel/serving.py` `keeps`); the server answers a token that no longer
names the picture the careful way, so a stale token costs a request and never shows a wrong one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple, Protocol
from urllib.parse import quote

from sift.kernel.access import AssetView, Repository, Viewer
from sift.kernel.access.sentences import said
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.covers import ChosenCover
from sift.kernel.db import Database, in_clause
from sift.kernel.serving import ART_KEY, face_version
from sift.kernel.wire import pieces_of
from sift.slices.insights import statements as st
from sift.slices.insights.models import NamedList, NamedRow, Unit, cover_of
from sift.slices.insights.recaps_models import RecapCard
from sift.slices.insights.statements import Named

#: Every kind of thing a list on the page names. Closed, so a kind with no way to be named is a
#: type error at the call rather than a list that silently comes back empty.
NamedKind = Literal["person", "site", "tag", "collection", "photo_set", "song", "asset", "wall"]

#: How many things a list on the page names.
LIST_LENGTH = 5

#: The kinds a recap card or row may draw a picture of, by the kind its piece names.
_COVERED: Mapping[str, NamedKind] = {
    "person": "person",
    "site": "site",
    "tag": "tag",
    "collection": "collection",
    "photo_set": "photo_set",
    "song": "song",
    "asset": "asset",
}

#: A User's own saved Theater walls, by id: what a wall in a statement is called.
_WALL_NAMES = "SELECT id, name FROM theater_arrangements WHERE user_id = ? AND id IN (?*)"


class Covered(Protocol):
    """An entity as the access layer describes it: its name and the cover chosen for it."""

    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def cover_asset_id(self) -> str | None: ...
    @property
    def cover_upload_id(self) -> str | None: ...
    @property
    def cover_at_ms(self) -> int | None: ...
    @property
    def cover_frame(self) -> CoverFrame | None: ...


@dataclass(frozen=True, slots=True)
class Known:
    """One thing this reader may be shown: what it is called and its picture's address."""

    name: str
    cover: str | None = None


class Ranked(NamedTuple):
    """One thing of a ranking, its figure, and its picture's address."""

    named: Named
    value: int
    cover: str | None


def cover_token(stamp: int, chosen: ChosenCover) -> str:
    """The token an entity's cover is kept under: the reader's stamp, then which picture it is,
    then its window. The server's half is `kernel/covers.py` `names_its_cover`; the client
    composes the same string (`coverToken` in `lib/entity/art.ts`)."""
    framed = "" if chosen.frame is None else f".{chosen.frame.token}"
    if chosen.upload_id is not None:
        return f"{face_version(stamp)}.{chosen.upload_id}{framed}"
    if chosen.asset_id is not None:
        moment = "" if chosen.at_ms is None else f".{chosen.at_ms}"
        return f"{face_version(stamp)}.{chosen.asset_id}{moment}{framed}"
    return face_version(stamp)


def _addressed(address: str | None, token: str | None) -> str | None:
    if address is None or not token:
        return address
    return f"{address}?{ART_KEY}={quote(token, safe='')}"


def _entity(kind: str, one: Covered, stamp: int) -> Known:
    chosen = ChosenCover(
        asset_id=one.cover_asset_id,
        at_ms=one.cover_at_ms,
        upload_id=one.cover_upload_id,
        frame=one.cover_frame,
    )
    return Known(one.name, _addressed(cover_of(kind, one.id), cover_token(stamp, chosen)))


def file_name(view: AssetView, on_disk: str | None) -> str:
    """What a file is called: the title somebody typed, its name in the folder now, or the name it
    arrived with: the file page's order (`Repository.names_on_disk`). "a file" only for one with
    none of the three."""
    return view.asset.title or on_disk or view.asset.original_filename or "a file"


async def _entities(
    access: Repository, viewer: Viewer, kind: NamedKind, ids: Sequence[str]
) -> Mapping[str, Covered]:
    if kind == "person":
        return await access.visible_people(viewer, ids)
    if kind == "site":
        return await access.visible_sites(viewer, ids)
    if kind == "tag":
        return await access.visible_tags(viewer, ids)
    if kind == "collection":
        found: list[Covered | None] = [await access.visible_collection(viewer, k) for k in ids]
    elif kind == "photo_set":
        found = [await access.visible_photo_set(viewer, key) for key in ids]
    else:
        found = [await access.visible_song(viewer, key) for key in ids]
    return {one.id: one for one in found if one is not None}


async def known(
    access: Repository, database: Database, viewer: Viewer, kind: NamedKind, ids: Sequence[str]
) -> dict[str, Known]:
    """What each of these things is called, and its picture, for the ones this reader may see."""
    if not ids:
        return {}
    if kind == "asset":
        files = await access.assets_of(viewer, ids)
        on_disk = await access.names_on_disk(viewer, list(files))
        return {
            key: Known(
                file_name(view, on_disk.get(key)),
                _addressed(cover_of(kind, key), None if view.concealed else view.art_version),
            )
            for key, view in files.items()
        }
    if kind == "wall":
        # A saved Theater wall: the one kind that is not the access layer's; it belongs to the one
        # user who saved it, and has no picture.
        asked, values = in_clause(_WALL_NAMES, list(ids))
        rows = await database.fetch_all(asked, [viewer.id, *values])
        return {str(row["id"]): Known(str(row["name"])) for row in rows}
    stamp = viewer.cache_stamp
    return {
        key: _entity(kind, one, stamp)
        for key, one in (await _entities(access, viewer, kind, ids)).items()
    }


async def top(
    access: Repository,
    database: Database,
    viewer: Viewer,
    kind: NamedKind,
    ranked: Mapping[str, int],
    most: int = LIST_LENGTH,
) -> list[Ranked]:
    """The first `most` of a ranking that this reader may be shown, with their figures.

    Names are asked for a few more than are wanted, so a thing that has gone since it was counted
    does not leave the list short.
    """
    wanted = [key for key in ranked if key][: most * 2]
    names = await known(access, database, viewer, kind, wanted)
    found = [
        Ranked(Named(kind, key, names[key].name), ranked[key], names[key].cover)
        for key in wanted
        if key in names
    ]
    return found[:most]


def named_list(title: str, found: Sequence[Ranked], unit: Unit) -> NamedList | None:
    """A list under a heading, or None for nothing to list."""
    if not found:
        return None
    return NamedList(
        title=title,
        rows=[
            NamedRow(
                piece=pieces_of(said(st.named(one.named)))[0],
                value=one.value,
                unit=unit,
                cover=one.cover,
            )
            for one in found
        ],
    )


async def covered_cards(
    access: Repository, database: Database, viewer: Viewer, cards: Sequence[RecapCard]
) -> list[RecapCard]:
    """A recap's cards with each picture's address carrying its token, for this reader now.

    A recap is drawn from figures frozen when it was made, so its pictures are addressed bare
    (`models.cover_of`); the token is the reader's and is put on when the recap is opened. The
    thing a picture shows is the one its card or row names.
    """
    wanted: dict[NamedKind, set[str]] = {}
    for card in cards:
        for piece in [*card.statement, *(row.piece for row in card.rows)]:
            kind = _COVERED.get(piece.kind or "")
            if kind is not None and piece.id:
                wanted.setdefault(kind, set()).add(piece.id)
    tokened: dict[str, str | None] = {}
    for kind, ids in wanted.items():
        for key, one in (await known(access, database, viewer, kind, sorted(ids))).items():
            bare = cover_of(kind, key)
            # Every kind asked for here has a picture (`test_every_kind_a_card_pictures_has_an_address`).
            if bare is not None:  # pragma: no branch
                tokened[bare] = one.cover
    return [
        card.model_copy(
            update={
                "cover": tokened.get(card.cover, card.cover) if card.cover else card.cover,
                "rows": [
                    row.model_copy(update={"cover": tokened.get(row.cover, row.cover)})
                    if row.cover
                    else row
                    for row in card.rows
                ],
            }
        )
        for card in cards
    ]
