# SPDX-License-Identifier: AGPL-3.0-or-later
"""The username lines on a Site's thread: which usernames were put on it, and how each arrived."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.access import sentences as say
from sift.kernel.access.history import VIAS, Actor, Event, by_of
from sift.kernel.access.sentences import SIFT, username_opens
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row
from sift.kernel.when import day_number

#: The usernames on this site, oldest first, grouped by day in Python. The nameless row is not a
#: username. Only usernames this viewer may be told about: one with a file they may be shown, by the
#: strict vault flag `:reveal_named`.
_SITE_USERNAMES = """
SELECT un.id AS id, un.name AS name, un.person_id AS person_id, un.created_at AS created_at
  FROM usernames un
 WHERE un.site_id = :subject AND un.name <> ''
   AND ((:admin = 1 AND NOT EXISTS (SELECT 1 FROM asset_usernames any_file
                                    WHERE any_file.username_id = un.id))
        OR EXISTS (SELECT 1 FROM viewer_entity_counts c
                    WHERE c.user_id = :viewer AND c.kind = 'username' AND c.object_id = un.id
                      AND c.permitted - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed END > 0))
 -- ordered by the clock: not every username has an id Sift minted in order; an old migration
 -- wrote random hex ones (access schema `_SITE_ONLY_ACCOUNT`)
 ORDER BY un.created_at ASC, COALESCE(un.name_sort, un.name) ASC, un.id ASC
"""

#: How each username on this site arrived, from its `added` event, newest first.
_ARRIVALS = """
SELECT s.subject_id AS id, d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.payload AS payload
  FROM workbench_decisions d
  JOIN workbench_decision_subjects s ON s.decision_id = d.id AND s.kind = 'username'
 WHERE d.object_kind = 'site' AND d.object_id = ? AND d.verb = 'added'
 ORDER BY d.decided_at DESC, d.id DESC
"""


def _arrival_of(row: Row | None, viewer: Viewer) -> tuple[Actor, str, bool]:
    """Who a username's arrival names, the task's phrase, and whether it is a backfill."""
    if row is None:
        return Actor.SOMEBODY, "", False
    kind, who = row["actor_kind"], row["actor_id"]
    if kind == "sift" and who:
        return Actor.SIFT, str(who), False
    if kind == "user" and who:
        return (Actor.YOU if str(who) == viewer.id else Actor.ANOTHER_USER), "", False
    backfilled = '"backfilled"' in str(row["payload"] or "")
    return Actor.SOMEBODY, "", backfilled


def _username_events(
    rows: Sequence[Row],
    arrivals: Sequence[Row] = (),
    viewer: Viewer | None = None,
) -> list[Event]:
    """One line per day of usernames put on a site, each named and linked (`username_opens`), past
    `sentences.FEED_MOST` folded in place."""
    told: dict[str, Row] = {}
    for one in arrivals:
        told.setdefault(str(one["id"]), one)
    days: dict[tuple[int, Actor, str, bool], list[Row]] = {}
    for row in rows:
        actor, via, untold = (
            _arrival_of(told.get(str(row["id"])), viewer)
            if viewer is not None
            else (Actor.SOMEBODY, "", False)
        )
        days.setdefault((day_number(int(row["created_at"])), actor, via, untold), []).append(row)
    events: list[Event] = []
    for (_day, actor, via, untold), day_rows in days.items():
        named = [
            say.thing(
                "username",
                str(row["id"]),
                str(row["name"]),
                href=username_opens(
                    str(row["id"]), None if row["person_id"] is None else str(row["person_id"])
                ),
            )
            for row in day_rows
        ]
        how = say.from_pass(via, len(named))
        events.append(
            Event(
                at=max(int(row["created_at"]) for row in day_rows),
                actor=actor,
                actor_name=SIFT if actor is Actor.SIFT else None,
                kind="filed",
                pieces=say.usernames_added(
                    by_of(actor, None), named, how if actor is Actor.SIFT else "", untold=untold
                ),
                via=via if via in VIAS else None,
            )
        )
    return events


async def site_username_lines(
    database: Database, viewer: Viewer, site_id: str, here: set[str]
) -> list[Event]:
    """The username lines of one Site's thread."""
    return _username_events(
        await database.fetch_all(
            _SITE_USERNAMES,
            {
                "viewer": viewer.id,
                "reveal_named": 1 if viewer.show_hidden else 0,
                "subject": site_id,
                "admin": 1 if viewer.is_admin else 0,
            },
        ),
        (
            await database.fetch_all(_ARRIVALS, (site_id,))
            if {"workbench_decisions", "workbench_decision_subjects"} <= here
            else []
        ),
        viewer,
    )
