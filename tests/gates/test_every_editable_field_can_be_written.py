# SPDX-License-Identifier: AGPL-3.0-or-later
"""A field the record offers a box for is a field something can actually write.

A record form draws a control for every field the registry marks editable, but a save writes only
what its route's model accepts, so a control can take a value, report success and write nothing.
For every subject, each editable field is accepted by the model that writes that subject's record,
or is named in `_WRITTEN_ELSEWHERE` with what writes it; both directions. Whether the screen SENDS
it is `test_every_record_screen_sends_its_fields`.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from sift.kernel.records import Subject, fields_of
from sift.slices.browse.models import RecordWrite
from sift.slices.people.models import PersonWrite, SiteDetailsWrite, SiteRecordWrite, UsernameWrite
from sift.slices.people.service import PROMOTED
from sift.slices.photo_sets.models import NotesWrite, PhotoSetWrite
from sift.slices.songs.models import NotesWrite as SongNotesWrite
from sift.slices.songs.models import SongWrite
from sift.slices.tags_ratings.models import TagWrite

pytestmark = [pytest.mark.gate, pytest.mark.unit]


def _accepted(*models: type) -> set[str]:
    """Every field name the models that write one subject's record accept, together: some subjects
    are written by several routes."""
    return {key for model in models for key in model.model_fields}


#: A person's record is written through `PROMOTED`, the pairing of declared fields and columns.
_PERSON_ACCEPTED = {key for key, _column in PROMOTED} | _accepted(PersonWrite)


#: What each subject's record write accepts, and the fields written some other way, each with the
#: route that honours it.
_SUBJECTS: dict[Subject, tuple[set[str], dict[str, str]]] = {
    Subject.ASSET: (
        _accepted(RecordWrite),
        {
            "filename": "POST /assets/{id}/rename: it moves a file, and has its own refusals",
        },
    ),
    Subject.PERSON: (
        _PERSON_ACCEPTED,
        {
            "details": "the `notes` column, written by the same statement as the name",
            "aliases": "the `people_aliases` table, one row each",
            "links": "the `people_links` table, one row each",
            "tags": "the `asset_tags` join, through PUT /people/{id}/tags",
            "age": "nothing writes it: it is arithmetic on the birthdate, done on the read",
        },
    ),
    Subject.SITE: (
        _accepted(SiteRecordWrite, SiteDetailsWrite),
        {
            "details": "the `notes` field of SiteDetailsWrite, under its older name",
            "tags": "the entity-tag join, through PUT /sites/{id}/tags",
        },
    ),
    Subject.TAG: (_accepted(TagWrite), {}),
    Subject.PHOTO_SET: (
        _accepted(PhotoSetWrite, NotesWrite),
        {"details": "the `notes` field of NotesWrite, under its older name"},
    ),
    Subject.SONG: (
        _accepted(SongWrite, SongNotesWrite),
        {
            "details": "the `notes` field of the song's NotesWrite, under its older name",
            "artists": "PUT /songs/{id}/artists, the whole ordered list of names in one go",
        },
    ),
    Subject.USERNAME: (
        _accepted(UsernameWrite),
        {"links": "the `url` field of UsernameWrite: one address, drawn as a list of one"},
    ),
}


def _subjects() -> Iterable[Subject]:
    """Every subject the registry declares, so one added later fails here."""
    return sorted({one.subject for one in fields_of_every()}, key=lambda one: one.value)


def fields_of_every() -> tuple[object, ...]:
    return tuple(one for subject in Subject for one in fields_of(subject))


def test_every_subject_is_covered() -> None:
    """Every subject is covered."""
    for subject in _subjects():
        assert subject in _SUBJECTS, (
            f"`{subject.value}` has declared fields and no entry in this gate, so nothing checks"
            " that its editable fields can be written. Add it."
        )


def test_every_editable_field_has_somewhere_to_land() -> None:
    for subject, (accepted, elsewhere) in _SUBJECTS.items():
        for one in fields_of(subject):
            if not one.editable or one.key in elsewhere:
                continue
            assert one.key in accepted, (
                f"`{subject.value}.{one.key}` is drawn as a box on a record and nothing that"
                " writes that record accepts it, so filling it in saves nothing and says nothing."
                " Add it to the write model, or to _WRITTEN_ELSEWHERE with what writes it."
            )


def test_nothing_is_excused_by_accident() -> None:
    """No exception names a field that does not exist."""
    for subject, (_accept, elsewhere) in _SUBJECTS.items():
        declared = {one.key for one in fields_of(subject)}
        for key in elsewhere:
            assert key in declared, f"`{subject.value}.{key}` is excused and is not a field"


def test_the_files_write_model_accepts_nothing_undeclared() -> None:
    """`RecordWrite` accepts nothing undeclared; other subjects' models carry more than the
    record."""
    declared = {one.key for one in fields_of(Subject.ASSET)}
    for key in RecordWrite.model_fields:
        assert key in declared, f"`RecordWrite` accepts `{key}` and no field declares it"
