# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who did an act, as a History line may name them.

A row records a user, a pass or a stash-box in different columns and words. These turn each into
an `Actor` and a name, and withhold another user's name from anybody who is not an admin.
"""

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
    """Turning a user id into an actor, for one viewer.

    A user who is not the viewer is named only to an admin. Every route that reads the
    users on this install is admin-only, so handing a guest the name of somebody else who uses
    it would be this read answering a question the sharing screens refuse, through a file they
    happen to be allowed to see.
    """

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


#: The box that made a row, by name. Guarded by its caller on `stash_boxes` being here at all.
MADE_BY_BOX = "SELECT name AS name FROM stash_boxes WHERE id = ?"


def maker_of(row: Row, viewer: Viewer, box_name: str | None) -> tuple[Actor, str | None]:
    """Who made a person, a site or a tag, as the four columns on the row say. (Actor, name.)

    Three makers and a fourth answer for the rows that predate anything recording it.
    `created_by_kind` is the word, `created_by_box_id` says WHICH box and `created_by_user_id` WHICH
    user, and the last of those is nullable even when the kind is 'user', because two creation
    paths are not handed a viewer to name. So a missing id is "a person did it and the row does not
    say which" rather than anything else, which is exactly what `Actor.SOMEBODY` already means.

    A box whose row has since been forgotten is still a box: `Actor.STASH_BOX` with no name, which
    the screens draw as "a stash-box". The row says a box made it and not which, and that is more
    than "somebody" would say.

    The user is named only as "you". A user who is not the viewer is a fact the sharing
    screens keep to an admin, and this read is drawn to anybody who may see the thing, so the same
    withholding `_Who` makes is made here, without the names map, because there is nothing here that
    would use it.
    """
    kind = None if row["created_by_kind"] is None else str(row["created_by_kind"])
    if kind == "box":
        return Actor.STASH_BOX, box_name or None
    if kind == "sift":
        # The PASS word, not the row: handed the whole row, `_sift_from` would find no pass by that
        # name and say plain "Sift" for everything it made.
        return Actor.SIFT, _sift_from(row["created_by_via"])
    if kind == "user":
        made = row["created_by_user_id"]
        if made is not None and str(made) == viewer.id:
            return Actor.YOU, None
        return (Actor.ANOTHER_USER, None) if made is not None else (Actor.SOMEBODY, None)
    return Actor.SOMEBODY, None


def actor_of_source(source: str | None, box: str | None = None) -> tuple[Actor, str | None]:
    """Who decided a row that carries a source word, and the name a counted line gives them.

    NULL means a person did it and nothing recorded which (see `Actor.SOMEBODY`). The stash-box is
    somebody else's service, named `box`. Every other word is a pass Sift ran, one this build has
    never heard of included: an unknown task is still a task, and dropping the row would lose files
    somebody can see on screen. A file's own line names the pass instead (`_actor_name_of_source`).
    """
    if source is None:
        return Actor.SOMEBODY, None
    if source == "stash_box":
        return Actor.STASH_BOX, box
    return Actor.SIFT, SIFT


def _actor_name_of_source(source: str | None) -> str | None:
    """What a link row's actor is called: Sift and the pass that decided it, or nothing else.

    A naming, a tagging and a filing Sift decided name Sift beside the line, as every other line
    Sift writes on the same pane does, and the pass is right there in the source word. `sift_from`
    is the one table that says a pass as an actor.
    """
    return _sift_from(source) if actor_of_source(source)[0] is Actor.SIFT else None


def _via_of_source(source: str | None) -> str | None:
    """Which of the four ways a row arrived, from the word the row carries.

    Only three of the four can ever come out of a column, and that is the truth rather than a gap
    to be filled in: `stash_box`, `folder` and `filename` are written into one, and a face match is
    not. And `filename` is only ever written into `asset_usernames`, which is a FILING. The
    faces slice inserts an `asset_people` row with NO source at all, so a naming Sift worked out
    from a face is indistinguishable here from one somebody typed, and it already has its own two
    kinds, `confirmed` and `rejected`, which say so outright.

    The mapping is the `enriched:` filter's, one word for one word, so a row's mark and the filter
    that finds that row cannot come to mean different things. A word this build has never heard of
    answers None, which draws the kind's own mark: an unknown pass is still a pass, and guessing
    which of three it was would be a mark that says something the row does not.
    """
    if source == "stash_box":
        return "stash"
    if source == "folder":
        return "folder"
    if source == "filename":
        return "filename"
    # The fifth, and the one that is NOT a fifth reader: `metadata` is written by the same filing
    # pass as `filename`, on the files whose username this library could only name because the
    # pictures' own fields said what the number in their name is called. It is a separate word
    # because what the two rest on is different evidence, and because the file's own line has to
    # be able to say which.
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
    """Who took one act, from the actor the ledger wrote beside it. See `_ledger_actor`.

    One answer for a line and for a decision card, because they are one row: a card read by
    `user_id` alone would say nobody for every decision Sift made while the feed says "Sift" about
    the same press. A row with no actor kind predates the ledger: its user where it
    names one, and otherwise Sift's, which is what the migration that widened the table decided for
    it (`_BACKFILL_ACTOR`).
    """
    if kind is None and user_id is not None:
        return who.of(user_id)
    if kind == "user":
        return who.of(actor_id or user_id)
    if kind == "box":
        return Actor.STASH_BOX, None
    return Actor.SIFT, _sift_from(actor_id)
