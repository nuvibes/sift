# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who did an act, as a History line may name them; another user's name only to an admin."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access.history_line import Actor
from sift.kernel.access.sentences import SIFT
from sift.kernel.access.sentences import sift_from as _sift_from
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row, in_clause


@dataclass(frozen=True, slots=True)
class _Who:
    """Turning a user id into an actor for one viewer; another user is named only to an admin."""

    viewer: Viewer
    names: Mapping[str, str]

    def of(self, user_id: str | None) -> tuple[Actor, str | None]:
        if user_id is None:
            return Actor.SOMEBODY, None
        if user_id == self.viewer.id:
            return Actor.YOU, None
        if self.viewer.is_admin:
            return Actor.ANOTHER_USER, self.names.get(user_id)
        return Actor.ANOTHER_USER, None


MADE_BY_BOX = "SELECT name AS name FROM stash_boxes WHERE id = ?"


def maker_of(row: Row, viewer: Viewer, box_name: str | None) -> tuple[Actor, str | None]:
    """Who made a person, a site or a tag, from the maker columns: (Actor, name). Another user is
    never named."""
    kind = None if row["created_by_kind"] is None else str(row["created_by_kind"])
    if kind == "box":
        return Actor.STASH_BOX, box_name or None
    if kind == "sift":
        # The pass word, not the row, or `_sift_from` finds no pass.
        return Actor.SIFT, _sift_from(row["created_by_via"])
    if kind == "user":
        made = row["created_by_user_id"]
        if made is not None and str(made) == viewer.id:
            return Actor.YOU, None
        return (Actor.ANOTHER_USER, None) if made is not None else (Actor.SOMEBODY, None)
    return Actor.SOMEBODY, None


def actor_of_source(source: str | None, box: str | None = None) -> tuple[Actor, str | None]:
    """Who decided a row that carries a source word: NULL is somebody, `box` a stash-box, anything
    else a pass."""
    if source is None:
        return Actor.SOMEBODY, None
    if source == "stash_box":
        return Actor.STASH_BOX, box
    return Actor.SIFT, SIFT


def _actor_name_of_source(source: str | None) -> str | None:
    """What a link row's actor is called: Sift and the pass that decided it."""
    return _sift_from(source) if actor_of_source(source)[0] is Actor.SIFT else None


def _via_of_source(source: str | None) -> str | None:
    """Which way a row arrived, by the `enriched:` filter's word; an unknown word answers None."""
    if source == "stash_box":
        return "stash"
    if source == "folder":
        return "folder"
    if source == "filename":
        return "filename"
    # `metadata` is the filename pass reading the pictures' own fields: other evidence, so its own
    # word.
    if source == "metadata":
        return "metadata"
    if source == "watermark":
        return "watermark"
    return None


async def _names_of(database: Database, user_ids: Sequence[str]) -> dict[str, str]:
    """What each of those users is called. Only the ones an event actually names."""
    wanted = sorted({one for one in user_ids if one})
    if not wanted:
        return {}
    statement, bound = in_clause("SELECT id, username FROM users WHERE id IN (?*)", wanted)
    rows = await database.fetch_all(statement, bound)
    return {str(row["id"]): str(row["username"]) for row in rows}


def _actor_of_act(
    kind: str | None, actor_id: str | None, user_id: str | None, who: _Who
) -> tuple[Actor, str | None]:
    """Who took one act, from the ledger's actor; a row from before the ledger is its user's or
    Sift's."""
    if kind is None and user_id is not None:
        return who.of(user_id)
    if kind == "user":
        return who.of(actor_id or user_id)
    if kind == "box":
        return Actor.STASH_BOX, None
    return Actor.SIFT, _sift_from(actor_id)
