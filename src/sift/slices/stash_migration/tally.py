# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Stash run did, counted as it went, and how its folder match differs from its read's."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Tally:
    """What a run did, counted as it went. Written to the report and said in the task's note."""

    tags: int = 0
    tags_filed_under_a_parent: int = 0
    tags_with_several_parents: list[str] = field(default_factory=list)
    sites: int = 0
    people: int = 0
    #: Of those, the ones Stash attaches to no file at all, counted apart for one review.
    people_without_files: int = 0
    sites_without_files: int = 0
    tags_without_files: int = 0
    scenes: int = 0
    scenes_matched: int = 0
    images: int = 0
    images_matched: int = 0
    images_in_zips: int = 0
    matched_by_place: int = 0
    matched_by_oshash: int = 0
    matched_by_fingerprint: int = 0
    ratings: int = 0
    o_counts: int = 0
    viewed: int = 0
    #: Files given the place Stash would pick them up from, and the time watched in all.
    resume_points: int = 0
    time_watched: int = 0
    photo_sets: int = 0
    #: Groups made into Collections, and the pictures of People, Sites and Tags that became their
    #: covers (and those Stash named whose bytes could not be read).
    collections: int = 0
    pictures: int = 0
    pictures_not_found: int = 0
    #: Pictures found through a file inside a zip.
    zip_pictures_matched: int = 0
    #: Hearts and stars carried onto People, Sites and Tags for whoever pressed Run.
    favorites: int = 0
    entity_ratings: int = 0
    #: Stash-box ids of People, Sites and Tags, and of files, each by how it went (`ports.Linked`),
    #: and how many ids Stash kept on scenes.
    links: dict[str, int] = field(default_factory=dict)
    file_links: dict[str, int] = field(default_factory=dict)
    scene_box_ids: int = 0
    #: Scene markers: made into Loops, made from a moment, on a video this library lacks, refused.
    marks: int = 0
    marks_from_moments: int = 0
    marks_not_here: int = 0
    marks_refused: int = 0
    #: Markers that wait with their video (not those on a scene Stash no longer has).
    marks_waiting: int = 0
    #: Saved filters kept as saved searches, and the rest by name with the reason.
    saved_searches: int = 0
    filters_not_brought: list[str] = field(default_factory=list)
    #: What waits for its file: scenes, pictures, and the People, Sites and Tags only they carry.
    waiting_scenes: int = 0
    waiting_images: int = 0
    people_waiting: int = 0
    sites_waiting: int = 0
    tags_waiting: int = 0
    #: Every waiting scene and picture as the screen lists it (`said_waiting`), for the report.
    waiting: list[dict[str, Any]] = field(default_factory=list)
    #: What this run could not do at all because the feature it writes through was not there.
    not_done: list[str] = field(default_factory=list)
    #: Stash folders whose match here is new, different or gone since the read.
    folders_matched_since_read: int = 0
    folders_rematched_since_read: int = 0
    folders_unmatched_since_read: int = 0


def moved_since_read(tally: Tally, read: Mapping[str, str], now: Mapping[str, str]) -> None:
    """Count how the folder match a run uses differs from the one its read showed."""
    tally.folders_matched_since_read = sum(1 for stash in now if stash not in read)
    tally.folders_rematched_since_read = sum(
        1 for stash, here in now.items() if stash in read and read[stash] != here
    )
    tally.folders_unmatched_since_read = sum(1 for stash in read if stash not in now)
