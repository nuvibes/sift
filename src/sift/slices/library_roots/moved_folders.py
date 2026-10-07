# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folders that moved: a renamed or moved folder told apart from one removed and one made.

Recognised by what is inside, compared against what Sift recorded (a location stores the name and
size a listing hands back), so no file is read. When it is not sure, it is not the same folder:
wrong that way loses what Sift remembered; wrong the other way gives one folder's sharing to another.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel.content import (
    ROOT_REL_PATH,
    subtree_prefix,
)
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FOLDER
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.sweeping import (
    _anything_under,
    _parent_of,
    _recorded_in,
    _refusals_by_folder,
)
from sift.slices.library_roots.walking import FolderMoves, _renamed

if TYPE_CHECKING:
    from sift.slices.library_roots.sweeping import _Item
    from sift.slices.library_roots.walking import Walk, _Rows

log = get_logger(__name__)


def _overlap(recorded: set[_Item], listed: set[_Item]) -> int:
    """How many of the recorded files are really there, by name and (where known) by size.

    Not a set intersection: a `None` size must still match a real one. Indexed by name first, so
    this is one pass over each side."""
    sizes: dict[str, set[int | None]] = {}
    for name, size in listed:
        sizes.setdefault(name, set()).add(size)
    hits = 0
    for name, size in recorded:
        found = sizes.get(name)
        if found is None:
            continue
        if size is None or None in found or size in found:
            hits += 1
    return hits


def _mostly_the_same(recorded: set[_Item], listed: set[_Item]) -> bool:
    """Whether these two are one folder.

    A majority of BOTH sides: *is most of the old folder here* and *is this mostly the old folder*.
    Without the second, a big folder matches any small one its contents were copied into."""
    if not recorded or not listed:
        return False
    hits = _overlap(recorded, listed)
    return hits * 2 > len(recorded) and hits * 2 > len(listed)


def _pair_up(
    vanished: dict[str, set[_Item]],
    appeared: dict[str, set[_Item]],
    *,
    childless_was: set[str],
    childless_now: set[str],
) -> dict[str, str]:
    """Which appeared directory is which folder that went, keeping only the certain ones.

    Three rules, in order, and the order is what makes the answer stable.

    **What is in it.** A folder is its files, so a pair whose contents mostly agree is a pair. Only
    where it is the ONLY candidate on both sides: two folders that swapped names, a folder split in
    two, a folder copied and then the original deleted: each leaves more than one candidate
    somewhere, and every one of them is dropped rather than guessed at.

    **What is under it.** Moving `shoot` into `archive` moves `shoot/inner` with it: `inner` is
    recognised by its files, and `shoot` by being its parent on both sides.

    **Nothing in it and nothing under it.** A folder somebody made and shared before putting
    anything in it has nothing to compare either way. Allowed only for a leaf, in the same place,
    and only when the uniqueness rule leaves exactly one candidate. Otherwise two empty folders
    renamed at once would be a coin toss, and one of the two outcomes hands somebody a folder they
    were never shared."""
    fits = [
        (was, now)
        for was, recorded in vanished.items()
        for now, listed in appeared.items()
        if _mostly_the_same(recorded, listed)
    ]
    pairs = {
        was: now
        for was, now in fits
        if sum(1 for other, _ in fits if other == was) == 1
        and sum(1 for _, other in fits if other == now) == 1
    }

    growing = True
    while growing:
        growing = False
        for was, now in list(pairs.items()):
            above_was, above_now = _parent_of(was), _parent_of(now)
            if above_was not in vanished or above_now not in appeared:
                continue
            if above_was in pairs or above_now in set(pairs.values()):
                continue
            pairs[above_was] = above_now
            growing = True

    empty_was = [
        was for was in vanished if was in childless_was and not vanished[was] and was not in pairs
    ]
    empty_now = [
        now
        for now in appeared
        if now in childless_now and not appeared[now] and now not in set(pairs.values())
    ]
    for was in empty_was:
        here = _parent_of(was)
        went = [one for one in empty_was if _parent_of(one) == here]
        came = [one for one in empty_now if _parent_of(one) == here]
        if len(went) == 1 and len(came) == 1:
            pairs[was] = came[0]

    return pairs


@dataclass(frozen=True, slots=True)
class FolderPlan:
    """What a walk would do to the folder rows, worked out and written nowhere."""

    #: Every pairing of a folder that went with a directory that appeared, shallowest first: the
    #: order a pass tries them in.
    pairs: tuple[tuple[str, str], ...]
    #: The pairings that become moves. A folder already carried along under its parent has no row
    #: of its own left to move, and a new path something is recorded at by then is refused.
    moves: FolderMoves
    #: The directories that get a folder row of their own.
    added: tuple[str, ...]
    #: The folder rows that go, deepest first.
    removed: tuple[str, ...]


async def _plan_folders(
    rows: _Rows,
    *,
    root_id: str,
    under: str,
    walk: Walk,
    prefix: str,
    service: LibraryService,
) -> FolderPlan | None:
    """Which folders moved, appeared and went, from a walk and the rows. Writes nothing.

    None when the walk did not happen: a refused walk and a library on a drive that is not
    plugged in both come back holding nothing, which read naively says every folder has gone.

    The scope is read from the same `under` the sweep is filtered by rather than worked out again:
    handed the wrong one, the plan says a library's entire folder tree has gone."""
    if not walk.looked:
        return None

    on_disk = {prefix + one for one in walk.directories}
    listed: dict[str, set[_Item]] = {one: set() for one in on_disk}
    for item in walk.files:
        where = _parent_of(prefix + item.rel_path)
        listed.setdefault(where, set()).add((Path(item.rel_path).name, item.size))

    known = {
        row.rel_path: row
        for row in await rows.library.folders_in_subtree(root_id, under)
        if row.rel_path != under
    }

    refused = _refusals_by_folder(await service.rejections_of_root(root_id))
    vanished = {
        path: await _recorded_in(rows, row, refused)
        for path, row in known.items()
        if path not in on_disk
    }
    unlisted = {prefix + one for one in walk.unlisted}
    appeared = {
        path: listed.get(path, set())
        for path in on_disk
        if path not in known and path not in unlisted
    }

    pairs = _pair_up(
        vanished,
        appeared,
        childless_was={one for one in vanished if not _anything_under(one, known)},
        childless_now={one for one in appeared if not _anything_under(one, on_disk)},
    )
    # Shallowest first: a pair recognised deeper is carried along by the move above it.
    ordered = tuple(
        (was, pairs[was]) for was in sorted(pairs, key=lambda one: (one.count("/"), one))
    )
    moves, recorded = _carried_out(ordered, set(known))
    scope = subtree_prefix(under)
    settled = {one for one in recorded if one != under and one.startswith(scope)}
    return FolderPlan(
        pairs=ordered,
        moves=moves,
        added=tuple(sorted(on_disk - settled)),
        removed=tuple(sorted(settled - on_disk, reverse=True)),
    )


def _carried_out(
    pairs: Sequence[tuple[str, str]], recorded: set[str]
) -> tuple[FolderMoves, set[str]]:
    """Which pairings become moves, and the folder paths recorded afterwards. Pure.

    Played the way `_reconcile_folders` makes them, in order: a folder no longer recorded at its
    old path (it went along with its parent) is passed over; the chain the folder lands in is made
    first; a new path something is recorded at by then is refused, as `move_folder` refuses it."""
    paths = set(recorded)
    made: list[tuple[str, str]] = []
    for was, now in pairs:
        if was not in paths:
            continue
        above = _parent_of(now)
        if above != ROOT_REL_PATH:
            parts = above.split("/")
            paths.update("/".join(parts[: depth + 1]) for depth in range(len(parts)))
        if now in paths:
            continue
        paths = {_renamed(one, was, now) for one in paths}
        made.append((was, now))
    return FolderMoves(tuple(made)), paths


async def _reconcile_folders(
    context: JobContext,
    *,
    root_id: str,
    under: str,
    walk: Walk,
    prefix: str,
    service: LibraryService,
) -> None:
    """Make the folder rows say what is really on the disk, before anything is taken in.

    Three things happen, and the order is the design. A folder that MOVED is recognised and keeps
    its id, so everything written about it survives (a share, a restriction, a concealment, the
    rule saying who the files landing in it are), and every file in it stays exactly where it is
    recorded, so the take-in below reads none of them. A folder that has APPEARED gets a row, an
    empty one included, which is what lets somebody make a folder and immediately download into it.
    A folder that has GONE loses its row, and its grants go first.

    **Nothing happens at all if the walk did not happen.** That would delete a whole tree, and the
    sharing with it, because somebody unplugged a disk. See `_plan_folders`, which works out what
    this carries out."""
    plan = await _plan_folders(
        context, root_id=root_id, under=under, walk=walk, prefix=prefix, service=service
    )
    if plan is None:
        log.info("library.folders_not_settled", root_id=root_id)
        return

    moved: list[tuple[str, str]] = []
    for was, now in plan.pairs:
        row = await context.library.folder_at(root_id, was)
        if row is None:
            # Carried along already, under a folder that moved before it.
            continue
        above = _parent_of(now)
        if above != ROOT_REL_PATH:
            # The chain the folder is landing in has to exist before it can hang off it.
            await service.adopt_locations(await context.library.upsert_folder(root_id, above))
        # SIFT, by the scan: this is the catch-up recognising that a directory somebody renamed
        # outside Sift is the same folder, not a person moving one from the screen.
        if await context.library.move_folder(row, now, actor=Actor.sift(VIA_FOLDER)) is None:
            # Something is already recorded where it would land, so these are not one folder after
            # all. Left alone: the loops below give the new path a row of its own and the old one
            # goes, which is what would have happened without any of this.
            continue
        await service.move_rejections(root_id=root_id, old_rel_path=was, new_rel_path=now)
        moved.append((was, now))
        log.info("library.folder_recognised", root_id=root_id, folder_id=row.id)
    if tuple(moved) != plan.moves.pairs:
        # Only another writer moving folders during this pass can do it.
        log.warning("library.folder_moves_unplanned", root_id=root_id)

    # Re-read rather than taken from the plan. A move takes every folder under it along with it, so
    # what is still missing and what is still new are both answers only the rows have.
    settled = {
        row.rel_path
        for row in await context.library.folders_in_subtree(root_id, under)
        if row.rel_path != under
    }

    on_disk = {prefix + one for one in walk.directories}
    for path in sorted(on_disk - settled):
        await service.adopt_locations(await context.library.upsert_folder(root_id, path))

    # Deepest first, so a folder is removed before whatever holds it. The other order works by
    # cascade and reports nothing, which makes a count of what was removed a lie.
    for path in sorted(settled - on_disk, reverse=True):
        await service.remove_folder(root_id=root_id, rel_path=path)
