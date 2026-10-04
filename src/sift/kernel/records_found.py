# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the stash-boxes said about one record, kept per source."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass

from sift.kernel.records_registry import Subject


@dataclass(frozen=True, slots=True)
class SourceAnswer:
    """What ONE stash-box said, kept per source so an unreachable one reads as unreachable."""

    source_id: str
    source_name: str
    records: list[FoundRecord]
    fetched_at: int
    fresh: bool
    #: Why the source could not be asked; carried, not raised, so one failure hides no other.
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class SourceLink:
    """A stash-box's WHOLE record of a subject a person agreed was them; unlike an answer, kept."""

    source_id: str
    source_name: str
    remote_id: str
    record: FoundRecord
    fetched_at: int


@dataclass(frozen=True, slots=True)
class FoundRecord:
    """One subject as a stash-box describes it, translated into Sift's words by the adapter.

    `confidence` is the adapter's own 0.0 to 1.0 reading of the match, never the stash-box's."""

    source_id: str
    remote_id: str
    subject: Subject
    name: str
    disambiguation: str | None = None
    image_url: str | None = None
    #: Every usable picture, largest first; face recognition's starter references take up to five.
    pictures: tuple[str, ...] = ()
    file_count: int | None = None
    fields: Mapping[str, object] = dataclasses.field(default_factory=dict)
    confidence: float = 0.0
    #: Every typed word is in this entry's names: the box's search matches EITHER word of a name.
    every_word: bool = True
    #: What Sift has NO field for, by the box's own key: inventing a word would invent a claim.
    extra: Mapping[str, object] = dataclasses.field(default_factory=dict)
    #: The box's OWN id per named person and site, so a row this answer creates links by id.
    refs: Mapping[str, Mapping[str, str]] = dataclasses.field(default_factory=dict)

    def id_for(self, kind: str, name: str) -> str | None:
        """The box's id for a named person or site, matched as the writer matches a name."""
        wanted = name.strip().casefold()
        for named, remote_id in (self.refs.get(kind) or {}).items():
            if named.strip().casefold() == wanted and remote_id:
                return remote_id
        return None
