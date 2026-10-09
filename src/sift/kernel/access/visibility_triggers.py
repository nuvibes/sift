# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every trigger that keeps the stored answers in `visibility` true, built from its tables."""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sift.kernel.access import visibility_panel, visibility_settled, visibility_walls
from sift.kernel.access.visibility_kinds import Counted
from sift.kernel.access.visibility_settled import RECOMPUTE
from sift.kernel.access.visibility_settled import widening as _widening
from sift.kernel.access.visibility_tables import (
    _CLEAR_PENDING,
    _EVERY_USER_FILES_OF_USERNAME,
    _EVERY_USER_ONE_FILE,
    _EVERY_USER_UNDER_FOLDER,
    _EVERY_USER_UNDER_SITE,
    _HIDING_TABLES,
    _MEMBERS_OF,
    _MEMBERSHIP_TABLES,
    _ONE_USER_IN_ROOT,
    _ONE_USER_ONE_FILE,
    _ONE_USER_UNDER_FOLDER,
    _PAIR_ALIVE,
    _PAIR_SHOWN,
    _PARTNER_EMPTIED,
    _PARTNER_ENSURED,
    _PARTNER_MOVED,
    _SITE_KEYS,
    _STAGE_PAIRS,
    _USERNAME_KEYS,
    _filled,
)

if TYPE_CHECKING:
    from sift.kernel.access.visibility import _Recompute

_Named = list[tuple[str, str]]


def _partner_trigger(event: str, dp: str, ds: str, row: str) -> tuple[str, str]:
    """One trigger on the pair table: its name and its DDL. Fires only on a crossing."""
    name = "vis_pair_counts_" + event.split()[0].lower()
    body = [
        _filled(template, ROW=row, THIS=this, THAT=that, DP=dp, DS=ds)
        for this, that in (("a", "b"), ("b", "a"))
        for template in (_PARTNER_ENSURED, _PARTNER_MOVED, _PARTNER_EMPTIED)
    ]
    return name, _trigger(
        name, event, "viewer_pair_counts", body, when=dp + " != 0 OR " + ds + " != 0"
    )


def _partner_triggers() -> list[tuple[str, str]]:
    alive_new, shown_new = _filled(_PAIR_ALIVE, ROW="NEW"), _filled(_PAIR_SHOWN, ROW="NEW")
    alive_old, shown_old = _filled(_PAIR_ALIVE, ROW="OLD"), _filled(_PAIR_SHOWN, ROW="OLD")
    return [
        _partner_trigger("INSERT", alive_new, shown_new, "NEW"),
        _partner_trigger("DELETE", "-" + alive_old, "-" + shown_old, "OLD"),
        _partner_trigger(
            "UPDATE OF permitted, concealed",
            "(" + alive_new + " - " + alive_old + ")",
            "(" + shown_new + " - " + shown_old + ")",
            "NEW",
        ),
    ]


# --- the triggers ------------------------------------------------------------------------------


def _trigger(
    name: str,
    event: str,
    table: str,
    body: Sequence[str],
    *,
    when: str = "",
    timing: str = "AFTER",
) -> str:
    """One trigger; `when` filters the rows that fire it."""
    guard = (" WHEN " + when) if when else ""
    return (
        "CREATE TRIGGER IF NOT EXISTS "
        + name
        + " "
        + timing
        + " "
        + event
        + " ON "
        + table
        + guard
        + " BEGIN\n"
        + ";\n".join(body)
        + ";\nEND"
    )


def _not_already_held(table: str, keys: Sequence[Sequence[str]], *, updating: bool) -> str:
    """The guard on a BEFORE half: no other row already holds what this change would write."""
    if not keys:
        raise ValueError(f"{table!r} declares no keys to guard its triggers on")
    clauses: list[str] = []
    for key in keys:
        same = " AND ".join(column + " = NEW." + column for column in key)
        if updating:
            itself = " AND ".join(column + " = OLD." + column for column in key)
            same = same + " AND NOT (" + itself + ")"
        clauses.append(_filled(_NOT_HELD, TABLE=table, SAME=same))
    return " AND ".join(clauses)


_NOT_HELD = "NOT EXISTS (SELECT 1 FROM <<TABLE>> WHERE <<SAME>>)"

_GRANT_READ = ("object_type", "object_id", "subject_user_id", "effect")


def _changed(columns: Sequence[str]) -> str:
    """The guard on an UPDATE trigger: any of these columns moved. Column names only."""
    return " OR ".join("OLD." + column + " IS NOT NEW." + column for column in columns)


def _update_columns(event: str) -> list[str] | None:
    """The columns an UPDATE event names, in order, or None for a bare UPDATE (every column)."""
    head = "UPDATE OF "
    if not event.startswith(head):
        return None
    return [column.strip() for column in event[len(head) :].split(",")]


def _watching(kinds: Sequence[Counted]) -> dict[str, Counted]:
    """One watcher per table, whatever number of kinds are counted over it."""
    merged: dict[str, Counted] = {}
    for one in kinds:
        held = merged.get(one.table)
        if held is None:
            merged[one.table] = one
            continue
        mine, theirs = _update_columns(held.update), _update_columns(one.update)
        if mine is None or theirs is None:
            event = "UPDATE"
        else:
            event = "UPDATE OF " + ", ".join(dict.fromkeys([*mine, *theirs]))
        merged[one.table] = Counted(
            held.kind, held.source, held.column, held.table, event, held.keys
        )
    return merged


def _pair(
    stem: str,
    event: str,
    table: str,
    before: Sequence[str],
    after: Sequence[str],
    when: str = "",
) -> _Named:
    # The guard is the BEFORE half's alone: AFTER fires only for a change that landed.
    return [
        (
            stem + "_before",
            _trigger(stem + "_before", event, table, before, when=when, timing="BEFORE"),
        ),
        (stem, _trigger(stem, event, table, after)),
    ]


def _folders(halves: _Recompute) -> _Named:
    """The folder tree: ancestry rows, and the copies under a folder that moves or goes."""
    return [_folder_arrives(), _folder_goes(halves), _folder_moves(halves)]


def _folder_arrives() -> tuple[str, str]:
    return (
        "vis_folders_insert",
        _trigger(
            "vis_folders_insert",
            "INSERT",
            "folders",
            [
                # A folder whose parent the walk never reached gets no rows: denied everywhere.
                "INSERT INTO folder_ancestry (folder_id, ancestor_id, depth)"
                " SELECT NEW.id, NEW.id, 0"
                "  WHERE NEW.parent_id IS NULL"
                "     OR EXISTS (SELECT 1 FROM folders p"
                "                  JOIN folder_ancestry pa ON pa.folder_id = p.id"
                "                                        AND pa.ancestor_id = p.id"
                "                 WHERE p.id = NEW.parent_id AND p.root_id = NEW.root_id)",
                "INSERT INTO folder_ancestry (folder_id, ancestor_id, depth)"
                " SELECT NEW.id, pa.ancestor_id, pa.depth + 1"
                "   FROM folder_ancestry pa JOIN folders p ON p.id = pa.folder_id"
                "  WHERE p.id = NEW.parent_id AND p.root_id = NEW.root_id",
            ],
        ),
    )


def _folder_goes(halves: _Recompute) -> tuple[str, str]:
    # Taken BEFORE the row goes, while the ancestry still says what was under it; each copy's
    # own trigger then puts its file back as it now is.
    return (
        "vis_folders_delete_before",
        _trigger(
            "vis_folders_delete_before",
            "DELETE",
            "folders",
            [
                _CLEAR_PENDING,
                _filled(_STAGE_PAIRS, PAIRS=_EVERY_USER_UNDER_FOLDER.format(folder="OLD.id")),
                *halves.take(),
                _CLEAR_PENDING,
            ],
            timing="BEFORE",
        ),
    )


def _folder_moves(halves: _Recompute) -> tuple[str, str]:
    return (
        "vis_folders_move",
        _trigger(
            "vis_folders_move",
            "UPDATE OF parent_id, root_id",
            "folders",
            [
                _CLEAR_PENDING,
                _filled(_STAGE_PAIRS, PAIRS=_EVERY_USER_UNDER_FOLDER.format(folder="NEW.id")),
                *halves.take(),
                # Moved where the walk cannot arrive: the whole subtree goes out of reach.
                # Moving it back needs recursion, so the boot pass (`keep_true`) rebuilds.
                "DELETE FROM folder_ancestry"
                " WHERE folder_id IN (SELECT folder_id FROM folder_ancestry"
                "                      WHERE ancestor_id = NEW.id)"
                "   AND NOT (NEW.parent_id IS NULL"
                "            OR (EXISTS (SELECT 1 FROM folders p"
                "                          JOIN folder_ancestry pa ON pa.folder_id = p.id"
                "                                                AND pa.ancestor_id = p.id"
                "                         WHERE p.id = NEW.parent_id AND p.root_id = NEW.root_id)"
                "                AND NOT EXISTS (SELECT 1 FROM folder_ancestry s"
                "                                 WHERE s.ancestor_id = NEW.id"
                "                                   AND s.folder_id = NEW.parent_id)))",
                # Otherwise the subtree is cut from the old chain and joined to the new one.
                "DELETE FROM folder_ancestry"
                " WHERE folder_id IN (SELECT folder_id FROM folder_ancestry"
                "                      WHERE ancestor_id = NEW.id)"
                "   AND ancestor_id NOT IN (SELECT folder_id FROM folder_ancestry"
                "                            WHERE ancestor_id = NEW.id)",
                "INSERT INTO folder_ancestry (folder_id, ancestor_id, depth)"
                " SELECT s.folder_id, pa.ancestor_id, s.depth + 1 + pa.depth"
                "   FROM folder_ancestry s, folder_ancestry pa"
                "  WHERE s.ancestor_id = NEW.id"
                "    AND pa.folder_id = NEW.parent_id"
                "    AND EXISTS (SELECT 1 FROM folders p"
                "                 WHERE p.id = NEW.parent_id AND p.root_id = NEW.root_id)",
                *halves.give(),
            ],
        ),
    )


def _file_going(halves: _Recompute) -> _Named:
    # Taken BEFORE the row goes, while its copies and memberships still say what the counts were.
    return [
        (
            "vis_assets_delete_before",
            _trigger(
                "vis_assets_delete_before",
                "DELETE",
                "assets",
                halves.going(_EVERY_USER_ONE_FILE.format(asset="OLD.id")),
                timing="BEFORE",
            ),
        )
    ]


def _memberships(halves: _Recompute, by_table: dict[str, Counted]) -> _Named:
    """A copy or a membership appearing, changing or going, in two halves around the change.

    The BEFORE half of an insert or update is guarded on the table's keys: a write the engine then
    ignores as a duplicate would otherwise leave the taken half with nothing to put it back."""
    unwatched = sorted(set(_MEMBERSHIP_TABLES) - set(by_table))
    if unwatched:  # pragma: no cover (an edit that took a membership table out of the kinds)
        raise RuntimeError(f"membership tables {unwatched} are not counted, so carry no keys")
    made: _Named = []
    for table, one in by_table.items():
        pairs = _EVERY_USER_ONE_FILE.format(asset="NEW.asset_id")
        made += _pair(
            "vis_" + table + "_insert",
            "INSERT",
            table,
            *halves.split(pairs, table=table),
            when=_not_already_held(table, one.keys, updating=False),
        )
        pairs = _EVERY_USER_ONE_FILE.format(asset="OLD.asset_id")
        made += _pair(
            "vis_" + table + "_delete",
            "DELETE",
            table,
            *halves.split(pairs, table=table),
        )
        both = (
            _EVERY_USER_ONE_FILE.format(asset="OLD.asset_id")
            + " UNION ALL "
            + _EVERY_USER_ONE_FILE.format(asset="NEW.asset_id")
        )
        made += _pair(
            "vis_" + table + "_update",
            one.update,
            table,
            *halves.split(both, table=table),
            when=_not_already_held(table, one.keys, updating=True),
        )
    return made


def _sites(halves: _Recompute) -> _Named:
    """A username changing site, a label changing network, and either going.

    Pairs, because a stored Site count reads the column being changed: the take has to run while
    the old value is there. A delete takes BEFORE the row goes, since a trigger fired by a foreign
    key action reads the parent row as already gone."""
    return [
        *_pair(
            "vis_usernames_site",
            "UPDATE OF site_id",
            "usernames",
            *halves.split(_EVERY_USER_FILES_OF_USERNAME.format(username="NEW.id")),
            when=_not_already_held("usernames", _USERNAME_KEYS, updating=True),
        ),
        (
            "vis_usernames_delete_before",
            _trigger(
                "vis_usernames_delete_before",
                "DELETE",
                "usernames",
                [
                    _CLEAR_PENDING,
                    _filled(
                        _STAGE_PAIRS,
                        PAIRS=_EVERY_USER_FILES_OF_USERNAME.format(username="OLD.id"),
                    ),
                    *halves.take(),
                    _CLEAR_PENDING,
                ],
                timing="BEFORE",
            ),
        ),
        # Deleting a network arrives here as each label's `ON DELETE SET NULL` update.
        *_pair(
            "vis_sites_parent",
            "UPDATE OF parent_id",
            "sites",
            *halves.split(_EVERY_USER_UNDER_SITE.format(site="NEW.id")),
            when=_not_already_held("sites", _SITE_KEYS, updating=True),
        ),
        (
            "vis_sites_delete_before",
            _trigger(
                "vis_sites_delete_before",
                "DELETE",
                "sites",
                [
                    _CLEAR_PENDING,
                    _filled(_STAGE_PAIRS, PAIRS=_EVERY_USER_UNDER_SITE.format(site="OLD.id")),
                    *halves.take(),
                    _CLEAR_PENDING,
                ],
                timing="BEFORE",
            ),
        ),
    ]


def _grants(halves: _Recompute) -> _Named:
    """A grant: one trigger per kind of object, each reaching the files that object decides."""
    made: _Named = []
    grant_events = [("INSERT", "NEW", ""), ("DELETE", "OLD", ""), ("UPDATE", "NEW", "")]
    if not halves.version_13:
        grant_events.append(("UPDATE", "OLD", "_was"))
    for event, row, tail in grant_events:
        user = row + ".subject_user_id"
        obj = row + ".object_id"
        owing = functools.partial(halves.owing, row=row, widening=_widening(event, tail))
        kinds: dict[str, Sequence[str]] = {
            "global": halves.user(user),
            "root": owing(_ONE_USER_IN_ROOT.format(user=user, root=obj)),
            "folder": owing(_ONE_USER_UNDER_FOLDER.format(user=user, folder=obj)),
            "item": halves.owing(_ONE_USER_ONE_FILE.format(user=user, asset=obj)),
        }
        for kind, members in _MEMBERS_OF.items():
            kinds[kind] = owing(members.format(user=user, object=obj))
        for kind, body in kinds.items():
            # The STORED spelling of `object_type`: a guard on a value no row carries never fires.
            name = "vis_acl_grants_" + event.lower() + "_" + kind + tail
            when = row + ".object_type = '" + kind + "'"
            if event == "UPDATE" and not halves.version_13:
                when = when + " AND (" + _changed(_GRANT_READ) + ")"
            made.append((name, _trigger(name, event, "acl_grants", body, when=when)))
    return made


def _hiding(halves: _Recompute) -> _Named:
    """Something hidden or shown again for one user; an update only when the flag or key moves."""
    made: _Named = []
    hiding: list[tuple[str, str, str]] = [
        ("asset_user_state", "asset_id", _ONE_USER_ONE_FILE.replace("{asset}", "{object}")),
        ("root_user_state", "root_id", _ONE_USER_IN_ROOT.replace("{root}", "{object}")),
        ("folder_user_state", "folder_id", _ONE_USER_UNDER_FOLDER.replace("{folder}", "{object}")),
    ]
    hiding += [
        (table, column, _MEMBERS_OF[kind]) for kind, (table, column) in _HIDING_TABLES.items()
    ]
    for table, column, members in hiding:
        for event, row in (("INSERT", "NEW"), ("DELETE", "OLD")):
            name = "vis_" + table + "_" + event.lower()
            body = halves.whole(members.format(user=row + ".user_id", object=row + "." + column))
            made.append((name, _trigger(name, event, table, body)))
        name = "vis_" + table + "_update"
        reached = members.format(user="NEW.user_id", object="NEW." + column)
        if halves.version_13:
            made.append((name, _trigger(name, "UPDATE OF hidden", table, halves.whole(reached))))
            continue
        read = ("hidden", "user_id", column)
        was = members.format(user="OLD.user_id", object="OLD." + column)
        made.append(
            (
                name,
                _trigger(
                    name,
                    "UPDATE OF " + ", ".join(read),
                    table,
                    halves.whole(was + " UNION ALL " + reached),
                    when=_changed(read),
                ),
            )
        )
    return made


def _users(halves: _Recompute) -> _Named:
    """A user arriving or changing role, and a pair crossing nought."""
    return [
        (
            "vis_users_insert",
            _trigger("vis_users_insert", "INSERT", "users", halves.user("NEW.id")),
        ),
        (
            "vis_users_role",
            _trigger("vis_users_role", "UPDATE OF role", "users", halves.user("NEW.id")),
        ),
        *_partner_triggers(),
    ]


def _on_table(name: str, ddl: str) -> tuple[str, str, str]:
    # The table is the word after ON in the header `_trigger` wrote.
    return (name, ddl.split(" ON ", 1)[1].split()[0], ddl)


def build(halves: _Recompute, watched: Sequence[Counted]) -> list[tuple[str, str, str]]:
    """Every trigger the component keeps, as (name, table, DDL).

    Built from the tables rather than written out, so a membership table cannot be left without
    its triggers by being forgotten."""
    by_table = _watching(watched)
    panel = visibility_panel.triggers(halves, by_table.pop("assets", None))
    named = [
        *_folders(halves),
        *_pair(*panel.renamed),
        *_file_going(halves),
        *_pair(*panel.sized),
        *_memberships(halves, by_table),
        *_sites(halves),
        *_grants(halves),
        *_hiding(halves),
        *_users(halves),
    ]
    made = [_on_table(name, ddl) for name, ddl in named]
    # The steps every trigger above calls, each written once (see `RECOMPUTE`).
    if not halves.version_13:
        made += panel.emptied + visibility_walls.triggers()
        for step, body in (
            ("user", visibility_settled.user_step(halves)),
            *visibility_settled.steps(),
            *halves.moving,
        ):
            name = "vis_recompute_" + step
            made.append(
                _on_table(
                    name, _trigger(name, "INSERT", RECOMPUTE + step, body, timing="INSTEAD OF")
                )
            )
    return made
