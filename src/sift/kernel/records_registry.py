# SPDX-License-Identifier: AGPL-3.0-or-later
"""The field registry's machinery: what a field is, how one is declared, and how its value is said."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.countries import country_name


class Subject(StrEnum):
    """What a field belongs to. One per kind of thing that has a record."""

    PERSON = "person"
    SITE = "site"
    TAG = "tag"
    ASSET = "asset"
    PHOTO_SET = "photo_set"
    SONG = "song"
    USERNAME = "username"

    @classmethod
    def _missing_(cls, value: object) -> Subject | None:
        """`account`, the older spelling a bookmark or a stored History payload may carry."""
        return cls.USERNAME if value == "account" else None


class Kind(StrEnum):
    """What a field IS, which decides how it is drawn and edited; not a storage type."""

    TEXT = "text"
    PARAGRAPH = "paragraph"
    NAMES = "names"
    LINKS = "links"
    #: ONE web address: a list of one would make every reader handle a case that cannot happen.
    LINK = "link"
    TAGS = "tags"
    #: Usernames on sites, each a THING with a page of its own, drawn as chips, never as addresses.
    ACCOUNTS = "accounts"
    DATE = "date"
    TIMESTAMP = "timestamp"
    BYTES = "bytes"
    #: Held in milliseconds, shown as minutes and seconds.
    DURATION = "duration"
    DIMENSIONS = "dimensions"
    RATE = "rate"
    COUNT = "count"
    FILENAME = "filename"
    PATH = "path"
    #: One value of a closed set; a box's constant (`BLONDE`) is said as its word (`CONSTANT`).
    WORD = "word"
    #: An encoder: stored as the stream spells it (`h264`), shown as its name (`H.264`).
    CODEC = "codec"
    #: Bits per colour sample, shown with its unit (`10-bit`) as `LENGTH` is.
    DEPTH = "depth"
    #: Read-only: the record of a person's judgement, not a fact anybody types.
    SOURCES = "sources"
    #: Not a `COUNT`: a thousands-grouping formatter would draw 1991 as "1,991".
    YEAR = "year"
    #: Stored as the two-letter code (`US`), shown as the country's name.
    COUNTRY = "country"
    #: Whole centimetres; meaningless without its unit.
    LENGTH = "length"
    #: Exactly two states, never absent (its column refuses NULL); drawn as Yes or No, never `1`.
    FLAG = "flag"


class Group(StrEnum):
    """Which HALF of a record a field belongs to: what somebody wrote, or what the file is.

    NOT `editable` under another name: that is who may write a value, this is what the value IS.
    """

    RECORD = "record"
    #: Measured off the file rather than written, so never editable.
    MEDIA = "media"


class Shown(StrEnum):
    """Where a field is drawn: on the record, in the panel, behind the switch, or elsewhere."""

    RECORD = "record"
    MORE = "more"
    #: ONLY with "show every field from a stash-box" on: fields only a stash-box fills in.
    EVERY = "every"
    #: Drawn by a surface of its own (people, tags); declared anyway so an import can write it,
    #: since the enrichment rules read this registry.
    ELSEWHERE = "elsewhere"


class FieldError(ValueError):
    """A field was declared wrongly. The message is meant to be read."""


@dataclass(frozen=True, slots=True)
class Field:
    """One declared field: how to draw, name and edit it, and nothing about where it is kept."""

    key: str
    subject: Subject
    label: str
    kind: Kind
    shown: Shown = Shown.RECORD
    group: Group = Group.RECORD
    #: False for anything Sift works out or measures: a file's size is not an opinion.
    editable: bool = False
    #: Whether a stash-box may fill this in: a person's usernames are imported, never typed.
    imported: bool = False
    help: str | None = None
    #: The query language's token for the vocabulary this completes from, so no second spelling.
    suggests: str | None = None
    #: Whether a wall can be filtered by it: values a finite set rows SHARE. Its column expression
    #: is the access layer's (`ENTITY_FACETS`), held equal by a gate.
    faceted: bool = False
    #: The kind of thing the value names; the record then carries `<key>_id` beside the name.
    links_to: str | None = None
    #: What ONE entry of a list is, for the adder's empty box; required on a typed list.
    entry: str | None = None
    #: Whether a list's ORDER is stored (a song's artists), so the form offers to move entries.
    ordered: bool = False


#: The kinds edited as boxes with an adder; the client's `FieldEditor` holds the same pair.
LIST_KINDS = frozenset({Kind.NAMES, Kind.LINKS})

_REGISTRY: dict[tuple[Subject, str], Field] = {}

#: Declaration order, which IS the order the record is read in.
_ORDER: list[tuple[Subject, str]] = []


def register_field(
    *,
    key: str,
    subject: Subject,
    label: str,
    kind: Kind,
    shown: Shown = Shown.RECORD,
    group: Group = Group.RECORD,
    editable: bool = False,
    imported: bool | None = None,
    help: str | None = None,
    suggests: str | None = None,
    links_to: str | None = None,
    faceted: bool = False,
    entry: str | None = None,
    ordered: bool = False,
) -> None:
    """Declare a field, once, at import time, by whoever owns the row it comes off.

    The same key on one subject twice raises at import, so two owners of `person.details` surface
    at the declaration rather than as a wrong word on a screen. `imported` defaults to `editable`.
    """
    if not label.strip():
        raise FieldError(f"field {subject.value}.{key} needs a label")
    if help is not None and not help.strip():
        raise FieldError(f"field {subject.value}.{key} has empty help; leave it out instead")

    if kind in LIST_KINDS:
        if editable and not (entry or "").strip():
            raise FieldError(
                f"field {subject.value}.{key} is a list somebody types into; say its entry"
            )
    elif entry is not None:
        raise FieldError(f"field {subject.value}.{key} is not a list; leave entry out")
    elif ordered:
        raise FieldError(f"field {subject.value}.{key} is not a list; it has no order to keep")

    at = (subject, key)
    if at in _REGISTRY:
        raise FieldError(f"field {subject.value}.{key} is registered twice")

    _REGISTRY[at] = Field(
        key=key,
        subject=subject,
        label=label,
        kind=kind,
        shown=shown,
        group=group,
        editable=editable,
        imported=editable if imported is None else imported,
        help=help,
        suggests=suggests,
        links_to=links_to,
        faceted=faceted,
        entry=entry,
        ordered=ordered,
    )
    _ORDER.append(at)


def field(subject: Subject, key: str) -> Field | None:
    """One declared field, or None if nothing declared it."""
    return _REGISTRY.get((subject, key))


#: A stash-box's constant for a choice (`BLONDE`); the client's `$lib/entity/constant-word` pattern is
#: held equal to this, and `SPELLED` below, by `tests/gates/test_word_values_agree.py`.
CONSTANT = re.compile(r"[A-Z][A-Z0-9_]*")

#: Constants that are not words, which the rule above would say wrongly ("Na").
SPELLED: Mapping[str, str] = {"NA": "Not applicable"}


def value_said(subject: Subject | str, key: str, stored: object) -> str | None:
    """One field's STORED value (or its JSON text) as the record draws it, or None for a list,
    an object or a blank: the one reading of a stored value for a sentence."""
    value = stored
    if isinstance(stored, str):
        try:
            value = json.loads(stored)
        except ValueError:
            value = stored
    if value is None or value == "" or isinstance(value, dict | list | tuple | bool):
        return None
    try:
        declared = field(Subject(subject), key)
    except ValueError:
        declared = None
    kind = declared.kind if declared is not None else None
    # By the field's KIND, never the value's look: a nationality "US" must not read as "Us".
    if isinstance(value, str) and kind is Kind.WORD and value in SPELLED:
        text = SPELLED[value]
    elif isinstance(value, str) and kind is Kind.WORD and CONSTANT.fullmatch(value):
        text = value.replace("_", " ").capitalize()
    elif isinstance(value, str) and kind is Kind.COUNTRY:
        text = country_name(value)
    else:
        text = str(value)
    if kind is Kind.LENGTH and isinstance(value, int | float):
        text = f"{text} cm"
    return text


def fields_of(subject: Subject) -> tuple[Field, ...]:
    """Every field of one subject, in declaration (reading) order."""
    return tuple(_REGISTRY[at] for at in _ORDER if at[0] is subject)


def every_field() -> tuple[Field, ...]:
    """Every declared field, in declaration order."""
    return tuple(_REGISTRY[at] for at in _ORDER)


def said_plainly(
    subject: Subject, keys: Iterable[str], counts: Mapping[str, int] | None = None
) -> tuple[str, ...]:
    """These field keys as the registry's labels, lowercased for mid-sentence, in RECORD order
    (never the caller's), for a history line. An undeclared key is kept, underscores opened, so
    a count is never short. `counts` prefixes a label only above one ("1 links" is no sentence).
    """
    wanted = list(keys)
    known = {one.key for one in fields_of(subject)}

    def _said(key: str, label: str) -> str:
        many = (counts or {}).get(key, 1)
        return f"{many} {label}" if many > 1 else label

    said = [
        _said(one.key, _plainly(_REGISTRY[(subject, one.key)].label))
        for one in fields_of(subject)
        if one.key in wanted
    ]
    said.extend(_said(one, one.replace("_", " ")) for one in wanted if one not in known)
    return tuple(said)


def fields_filled(
    subject: Subject, stored: object, omitting: Collection[str] = ()
) -> tuple[str, ...] | None:
    """The one reader of `enrichment_runs.applied`, as the words somebody reads.

    None (the row says nothing, or is unreadable) and the empty tuple (nothing to fill) are
    different answers all the way to the sentence. A key list and a key-to-count object are both
    read. `omitting` drops keys the caller counts from rows of its own, so no act counts twice.
    """
    if not isinstance(stored, str) or not stored:
        return None
    try:
        held = json.loads(stored)
    except ValueError:
        return None
    if isinstance(held, dict):
        counts = {
            str(key): value for key, value in held.items() if isinstance(value, int) and value > 0
        }
        return said_plainly(subject, (key for key in counts if key not in omitting), counts=counts)
    if not isinstance(held, list):
        return None
    return said_plainly(subject, (str(one) for one in held if str(one) not in omitting))


def _plainly(label: str) -> str:
    """A label mid-sentence: first letter lowered unless the label has capitals of its own."""
    return label[:1].lower() + label[1:] if label[1:] == label[1:].lower() else label
