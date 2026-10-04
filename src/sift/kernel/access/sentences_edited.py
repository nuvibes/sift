# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines for an edit: which fields changed, to what, and the settings and tasks it ran."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sift.kernel.access.field_changed import field_changed
from sift.kernel.access.sentences_downloads import KIND_BEFORE
from sift.kernel.access.sentences_ledger import (
    COVER_OBJECTS,
    FIELD_WORDS,
    Said,
    _edited_words,
    cover_changed,
    edited_folded,
)
from sift.kernel.access.sentences_pieces import (
    HERE,
    SIFT,
    VANTAGE_PERSON,
    Line,
    Piece,
    and_then,
    files,
    many,
    said,
)
from sift.kernel.text import non_empty_str
from sift.kernel.vocabulary import UPDATE_TO

#: Payload keys that are the reader's own words rather than a field an edit moved.
_NOT_A_FIELD = frozenset(
    {
        "before",
        "after",
        "key",
        "more",
        "fields",
        "field",
        "code",
        "cover",
        "box",
        "box_id",
        "from",
        "under_floor",
    }
)


def passes_of(payload: Mapping[str, object]) -> list[str]:
    """The passes a press ran, off its payload (`kernel.presses`); none where it names none."""
    passes = payload.get("passes")
    return [one for one in passes if isinstance(one, str)] if isinstance(passes, list) else []


def fields_of(payload: Mapping[str, object]) -> list[str]:
    """Which fields an `edited` event moved: a file's two shapes, or an entity's payload keys."""
    listed_now = payload.get("fields")
    fields = (
        [str(one.get("field")) for one in listed_now if isinstance(one, Mapping)]
        if isinstance(listed_now, list)
        else []
    )
    if not fields and isinstance(payload.get("field"), str):
        fields = [str(payload["field"])]
    if not fields:
        fields = [str(key) for key in payload if key not in _NOT_A_FIELD]
    return fields


#: The kinds whose NAME alone would say whose cover it is wrongly, so the line says the kind: a
#: Photo Set is often named after the person in it, and "Sift chose beach.jpg as Cassia Lynn's
#: cover" about a set called Cassia Lynn would read as her own cover on the feed. A person's
#: name is theirs and keeps the possessive.
_COVER_OF_KINDS = frozenset({"photo_set", "collection", "tag", "site", "song"})


def _cover_of(by: str, picture: Line, whose: Line, kind: str | None) -> Line:
    """ "chose <picture> as <person>'s cover", or "as the cover of the Photo Set <name>"."""
    if kind in _COVER_OF_KINDS:
        return said(by, " chose ", picture, " as the cover of ", KIND_BEFORE[kind], whose)
    return said(by, " chose ", picture, " as ", whose, "'s cover")


def number_changed(payload: Mapping[str, object]) -> tuple[str, str] | None:
    """A Site's own number typed onto a username (`catalog.set_username_number`), as the two halves
    around the username: ("changed the Instagram ID", " from 4242 to 40506070"), else None."""
    after = non_empty_str(payload.get("number_after"))
    if after is None:
        return None
    site = non_empty_str(payload.get("number_site"))
    what = f"the {site} ID" if site else "the ID"
    before = non_empty_str(payload.get("number_before"))
    if before is None:
        return f"set {what}", f" to {after}"
    return f"changed {what}", f" from {before} to {after}"


def _edited_line(
    by: str,
    *,
    page: str | None,
    object_is_page: bool,
    subjects: Line,
    object_line: Line,
    object_kind: str | None,
    fields: Sequence[str],
    fields_kind: str | None,
    payload: Mapping[str, object],
    task: str | None = None,
) -> Said:
    """An edit: a cover chosen from a picture, a cover changed with no picture, or fields moved.

    `page` is the vantage word where the page is the thing that was edited, and None in the feed;
    `object_is_page` is a file's page reading "chosen as a cover" from the picture's side. A cover
    the faces task chose is a FACE in that file, and says so: "Sift chose their face in image.png
    as the cover". The file alone would read as if the whole picture were the cover.
    """
    whose = "their" if page == HERE[VANTAGE_PERSON] else "its"
    if object_kind in COVER_OBJECTS and object_line:
        face = by == SIFT and task == "faces" and object_kind == "asset"
        if object_is_page:
            if face:
                return Said(
                    said(by, " chose ", subjects, "'s face in ", object_line, " as the cover")
                )
            return Said(_cover_of(by, object_line, subjects, fields_kind))
        if page is not None:
            if face:
                return Said(said(by, f" chose {whose} face in ", object_line, " as the cover"))
            return Said(said(by, " chose ", object_line, " as the cover"))
        if face:
            return Said(said(by, " chose ", subjects, "'s face in ", object_line, " as the cover"))
        return Said(_cover_of(by, object_line, subjects, fields_kind))
    cover = non_empty_str(payload.get("cover"))
    if cover is not None:
        box = non_empty_str(payload.get("box"))
        if page is not None:
            return Said(said(by, " ", cover_changed(cover, box, whose=whose)))
        return Said(said(by, " ", cover_changed(cover, box, whose="the"), " of ", subjects))
    number = number_changed(payload) or field_changed(payload, FIELD_WORDS)
    if number is not None:
        return Said(said(by, f" {number[0]} of ", subjects, number[1]))
    folded = edited_folded(fields, fields_kind)
    named = _edited_words(fields, fields_kind)
    if not fields:
        what: str = page or ""
    elif folded is not None:
        what = folded[0]
    elif named is not None:
        what = and_then(named)
    else:
        one = len(fields) == 1
        if page is not None:
            what = f"{'one' if one else many(len(fields))} of {whose} details"
        else:
            what = "1 detail" if one else f"{many(len(fields))} details"
    if page is not None:
        return Said(said(by, f" edited {what}"), folded=folded)
    if not what:
        return Said(said(by, " edited ", subjects), folded=folded)
    return Said(said(by, f" edited {what} of ", subjects), folded=folded)


def _value_said(stored: object, key: object = None) -> str | None:
    """A setting's stored value as somebody reads it, or None where it is not a plain value.

    Stored as JSON text. A switch is "on" or "off". A choice is the word its menu shows ("GPU",
    never the stored `nvidia`), a whole number carries the unit its control shows ("70%", "30 MB")
    and a zero that means "work it out" says so, all read off the setting's own declaration
    (`settings_registry`), so the line and the control cannot disagree. A list or an object is not
    said: a line reading out a structure is the machine talking.
    """
    import json

    from sift.kernel.settings_registry import get_registered, get_removed

    if not isinstance(stored, str):
        return None
    if isinstance(key, str) and get_removed(key) is not None:
        # Its values meant something to a control that is gone: "left it at done" is the machine
        # talking. The line says the change without them. See `settings_registry.Removed`.
        return None
    try:
        value = json.loads(stored)
    except ValueError:
        return None
    declared = get_registered(key) if isinstance(key, str) else None
    if isinstance(value, bool):
        return "on" if value else "off"
    if declared is not None and declared.choices and declared.choice_labels:
        for choice, label in zip(declared.choices, declared.choice_labels, strict=False):
            if choice == value:
                return label
    if isinstance(value, int):
        if value == 0 and declared is not None and declared.automatic_label:
            return declared.automatic_label
        unit = declared.unit if declared is not None else None
        if unit:
            return f"{value:,}{unit}" if unit == "%" else f"{value:,} {unit}"
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str):
        return value or None
    return None


def setting_changed(by: str, setting: Piece, payload: Mapping[str, object]) -> Line:
    """A setting changed: from what to what, where both are plain values ("You changed Starting
    volume from 40% to 70%"), and "You changed Starting volume" where they are not.

    A value that is not plain can be said in the words its control showed, where the writer kept
    them: `before_said` / `after_said`. A Site's name template that is empty means "the default",
    which only the screen that draws it knows (no declaration here can say so), and a line
    reading "changed ... to" and nothing would hide the one fact somebody opens History for."""
    key = payload.get("key")
    before = _value_said(payload.get("before"), key) or non_empty_str(payload.get("before_said"))
    after = _value_said(payload.get("after"), key) or non_empty_str(payload.get("after_said"))
    # A default an update changed (`settings_hub.defaults`) is said as that update's act, by its
    # release: Sift alone would not say which update, and the reader is asking when it changed.
    release = non_empty_str(payload.get(UPDATE_TO))
    if release is not None:
        by = f"The update to {release}"
    if before is not None and after is not None:
        return said(by, " changed ", setting, f" from {before} to {after}")
    if after is not None:
        return said(by, " changed ", setting, f" to {after}")
    return said(by, " changed ", setting)


def _took(seconds: int) -> str:
    """How long a task ran, in the short units every screen uses: "42 s", "12 min", "1 h 5 min"."""
    if seconds < 60:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, left = divmod(minutes, 60)
    return f"{hours} h {left} min" if left else f"{hours} h"


def _count_in(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ran_line(by: str, run: Line, payload: Mapping[str, object]) -> Line:
    """A long task that finished, with what the run recorded (`jobs.ledger._finish`): how long it
    took, how many files it went through, how many of them were new (a scan) and how many of its
    jobs failed. "Sift ran Scan in 42 s: 1,203 files, 12 new, 3 failed". "Sift ran Scan" alone says nothing a reader could use.
    A run somebody stopped says it ended early rather than guessing why. A dry run says what it
    found, which is the only thing it did: "You did a dry run in 3 s: Find shoots would suggest
    4 shoots, of 2 people. Nothing was changed."
    """
    seconds = _count_in(payload, "seconds")
    reported = _dry_run_said(payload)
    if reported is not None:
        took = f" in {_took(seconds)}" if seconds is not None else ""
        return said(by, " did a dry run", took, ": ", reported)
    extent = []
    if file_count := _count_in(payload, "files"):
        extent.append(files(file_count))
    # A scan's new files (`jobs.ledger._finish`): the figure its line is read for. "none new" is
    # said too, because a scan that found nothing new is an answer; a run recorded before this was
    # kept carries no figure and says nothing about it.
    arrived = _count_in(payload, "new")
    if arrived is not None and file_count:
        extent.append(f"{many(arrived)} new" if arrived else "none new")
    if failed := _count_in(payload, "jobs_failed"):
        extent.append(f"{many(failed)} failed")
    listed_now = f": {', '.join(extent)}" if extent else ""
    if payload.get("stopped") is True:
        took = f" for {_took(seconds)}" if seconds is not None else ""
        return said(by, " ran ", run, took, " and it ended early", listed_now)
    took = f" in {_took(seconds)}" if seconds is not None else ""
    return said(by, " ran ", run, took, listed_now)


def _dry_run_said(payload: Mapping[str, object]) -> str | None:
    """The sentence of a dry run's report, where this run is one. A dry run's note is its report as
    fields (a JSON object marked `dry`). Its headline where it has one: the whole sentence lists the
    first files by name, a paragraph in a line of history, and "did a dry run" already says that
    nothing was changed."""
    note = payload.get("said")
    if not isinstance(note, str) or not note.startswith("{"):
        return None
    try:
        held = json.loads(note)
    except ValueError:
        return None
    if isinstance(held, dict) and held.get("dry") == 1 and isinstance(held.get("said"), str):
        head = held.get("head")
        return head if isinstance(head, str) and head else str(held["said"])
    return None
