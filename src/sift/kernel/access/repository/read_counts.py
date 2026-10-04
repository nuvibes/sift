# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers on a card: its tabs' counts and a subject's O tally, over files the viewer may see."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.entities import (
    CARD_TAB_OF_KIND,
    CARD_TABS,
    card_counts_query,
)
from sift.kernel.access.repository.views import (
    _is_object_id,
)
from sift.kernel.access.sites import FILES_SITES_REACH
from sift.kernel.access.viewer import Viewer


#: One subject's O tally for one user: this viewer's own counters summed over the files that
#: subject reaches and they may see. One statement around each membership, so no kind can forget
#: the vault clause the walls read. An inner join walks the smaller side; COALESCE on the SUM,
#: since no rows sum to NULL. Every piece is a module constant; the subject binds.
def _o_count_over(members: str) -> str:
    """The tally statement around one kind's membership. `members` yields an `asset_id` column."""
    return (
        "SELECT COALESCE(SUM(s.o_count), 0) AS total FROM ("  # noqa: S608
        + members
        + ") m"
        " JOIN viewer_assets va ON va.asset_id = m.asset_id AND va.user_id = :viewer"
        " JOIN asset_user_state s ON s.asset_id = m.asset_id AND s.user_id = :viewer"
        " WHERE (:reveal_named = 1 OR va.concealed = 0)"
    )


_O_COUNT_OF_PERSON = _o_count_over(
    "SELECT asset_id FROM asset_people WHERE person_id = :subject_id"
)
_O_COUNT_OF_TAG = _o_count_over("SELECT asset_id FROM asset_tags WHERE tag_id = :subject_id")
_O_COUNT_OF_COLLECTION = _o_count_over(
    "SELECT asset_id FROM collection_items WHERE collection_id = :subject_id"
)
_O_COUNT_OF_PHOTO_SET = _o_count_over(
    "SELECT asset_id FROM photo_set_items WHERE photo_set_id = :subject_id"
)
_O_COUNT_OF_SONG = _o_count_over("SELECT asset_id FROM song_files WHERE song_id = :subject_id")
#: A Site reaches its labels' files too, the walk its card and Files tab read; DISTINCT, since a
#: file under two of its usernames is one file.
_O_COUNT_OF_SITE = _o_count_over(
    "SELECT DISTINCT reached.asset_id FROM ("  # noqa: S608
    + FILES_SITES_REACH.format(ancestors="= :subject_id")
    + ") reached"
)


class CountReads(RepositoryCore):
    """The scoped counts a card draws."""

    async def card_counts(
        self, viewer: Viewer, kind: str, ids: Sequence[str]
    ) -> dict[str, dict[str, int]]:
        """What each card on one page of a wall of `kind` reaches, keyed by the card's id: every
        cell its kind carries (`CARD_TABS`), nought included, off the stored pairs with the wall's
        own vault flags. A kind with no cells is refused rather than drawn bare."""
        tabs = CARD_TABS.get(kind)
        if tabs is None:
            raise ValueError(f"no card counts for a wall of {kind!r}")
        wanted = [one for one in ids if _is_object_id(one)]
        answer = {one: dict.fromkeys(tabs, 0) for one in wanted}
        if not wanted:
            return answer
        rows = await self._db.fetch_all(
            card_counts_query(),
            {
                "viewer": viewer.id,
                "kind": kind,
                "ids": json.dumps(wanted),
                # A Site's People cell counts the people with a username there as an admin is shown
                # them, files or none; see `_A_USERNAME_SHOWS_ITS_PERSON`.
                "is_admin": 1 if viewer.is_admin else 0,
                "reveal": self._reveal_existence(viewer),
                "reveal_named": self._reveal_named(viewer),
            },
        )
        for row in rows:
            tab = CARD_TAB_OF_KIND.get(str(row["kind"]))
            cells = answer.get(str(row["id"]))
            if cells is not None and tab in cells:
                cells[tab] = int(row["n"])
        return answer

    async def _o_count(self, viewer: Viewer, statement: str, subject_id: str) -> int:
        """This viewer's own O tally over the files one subject reaches and they may see.

        THE SUM IS SCOPED, with the wall's own `:reveal_named`, so a total never publishes what the
        vault holds back; per user on both sides. Not a point read: it grows with the subject's
        files. A non-id answers nought, never bound as NULL.
        """
        if not _is_object_id(subject_id):
            return 0
        rows = await self._db.fetch_all(
            statement,
            {
                "viewer": viewer.id,
                "reveal_named": self._reveal_named(viewer),
                "subject_id": subject_id,
            },
        )
        return int(rows[0]["total"]) if rows else 0

    async def o_count_of_person(self, viewer: Viewer, person_id: str) -> int:
        """One person's tally over the files they are on. See `_o_count`."""
        return await self._o_count(viewer, _O_COUNT_OF_PERSON, person_id)

    async def o_count_of_site(self, viewer: Viewer, site_id: str) -> int:
        """One Site's tally over the files it reaches, its labels' included. See `_o_count`."""
        return await self._o_count(viewer, _O_COUNT_OF_SITE, site_id)

    async def o_count_of_tag(self, viewer: Viewer, tag_id: str) -> int:
        """One tag's tally over the files carrying it. See `_o_count`."""
        return await self._o_count(viewer, _O_COUNT_OF_TAG, tag_id)

    async def o_count_of_collection(self, viewer: Viewer, collection_id: str) -> int:
        """One collection's tally over the files in it. See `_o_count`."""
        return await self._o_count(viewer, _O_COUNT_OF_COLLECTION, collection_id)

    async def o_count_of_photo_set(self, viewer: Viewer, photo_set_id: str) -> int:
        """One photo set's tally over the pictures in it. See `_o_count`."""
        return await self._o_count(viewer, _O_COUNT_OF_PHOTO_SET, photo_set_id)

    async def o_count_of_song(self, viewer: Viewer, song_id: str) -> int:
        """One song's tally over the files that carry it. See `_o_count`."""
        return await self._o_count(viewer, _O_COUNT_OF_SONG, song_id)
