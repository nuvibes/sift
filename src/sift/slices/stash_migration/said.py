# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Stash run and the pass that lands what waited say when they finish: the task's note."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover (a name for the type checker only)
    from sift.slices.stash_migration.tally import Tally


def counted(count: int, one: str, many: str) -> str:
    return f"1 {one}" if count == 1 else f"{count:,} {many}"


def listed(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def note_of_run(tally: Tally) -> str:
    """The task's own sentence when it finishes: what came across, what has no files in Stash
    either, and what waits for its files, with the list of those on the screen below it."""
    said = (
        f"Imported {tally.scenes_matched:,} of {tally.scenes:,} files and "
        f"{tally.images_matched:,} of {tally.images:,} pictures, with "
        f"{counted(tally.people, 'Person', 'People')}, {counted(tally.sites, 'Site', 'Sites')} "
        f"and {counted(tally.tags, 'Tag', 'Tags')}."
    )
    also = [
        counted(count, one, many)
        for count, one, many in (
            (tally.marks, "Loop", "Loops"),
            (tally.saved_searches, "saved search", "saved searches"),
            (tally.collections, "Collection", "Collections"),
            # A picture Stash kept on a Person, Site or Tag, which became its cover here: said as
            # a cover, so it is never read as one more of the pictures counted just before.
            (tally.pictures, "cover", "covers"),
            # The player's own words for a resume point (`Remember where you left off`).
            (
                tally.resume_points,
                "file that remembers where you left off",
                "files that remember where you left off",
            ),
        )
        if count
    ]
    if also:
        said += f" Also {listed(also)}."
    alone = [
        counted(count, one, many)
        for count, one, many in (
            (tally.people_without_files, "Person", "People"),
            (tally.sites_without_files, "Site", "Sites"),
            (tally.tags_without_files, "Tag", "Tags"),
        )
        if count
    ]
    if alone:
        one_only = tally.people_without_files + tally.sites_without_files + tally.tags_without_files
        verb = "has" if one_only == 1 else "have"
        said += f" Of those, {listed(alone)} {verb} no files in Stash either."
    moved = folders_moved(tally)
    if moved:
        said += f" {moved}"
    # What stayed behind says so, and why, so "0 Loops" never stands for "the videos are not here".
    waiting = [
        counted(count, one, many)
        for count, one, many in (
            (tally.waiting_scenes, "file", "files"),
            (tally.waiting_images, "picture", "pictures"),
        )
        if count
    ]
    if waiting:
        carried = [
            counted(count, one, many)
            for count, one, many in (
                (tally.marks_waiting, "marker", "markers"),
                (tally.people_waiting, "Person", "People"),
                (tally.sites_waiting, "Site", "Sites"),
                (tally.tags_waiting, "Tag", "Tags"),
            )
            if count
        ]
        single = tally.waiting_scenes + tally.waiting_images == 1
        said += f" {listed(waiting)} this library doesn't have yet "
        said += "waits" if single else "wait"
        if carried:
            said += f", with {listed(carried)} on {'it' if single else 'them'}"
        said += "."
        said += " Each comes across on its own once its file is in Sift. The list is below."
    return said


def folders_moved(tally: Tally) -> str:
    """The sentence for a folder match that differs from the one the read showed, in the
    screen's own words for the match; empty when it is the same. A read taken before a folder
    was added would otherwise leave the counts above unexplained."""
    parts = []
    for count, what in (
        (tally.folders_matched_since_read, "a folder in this library"),
        (tally.folders_rematched_since_read, "a different folder in this library"),
        (tally.folders_unmatched_since_read, "no folder in this library any more"),
    ):
        if count:
            verb = "matches" if count == 1 else "match"
            parts.append(f"{count:,} of its folders {verb} {what}")
    if not parts:
        return ""
    return f"Since Stash was read, {listed(parts)}."


def note_of_landing(files: int, tally: Tally) -> str:
    """The pass's own sentence: how many files that arrived were given what waited for them."""
    said = f"Imported what Stash kept for {counted(files, 'file', 'files')} that arrived"
    if tally.marks:
        said += f", with {counted(tally.marks, 'Loop', 'Loops')}"
    return said + "."
