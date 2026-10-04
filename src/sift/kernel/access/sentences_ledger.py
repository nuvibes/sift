# SPDX-License-Identifier: AGPL-3.0-or-later
"""The words every vantage shares for the ledger's own events: what a thing is called, what a field is called."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access.sentences_pieces import (
    FEED_MOST,
    Line,
    Part,
    Piece,
    and_then,
    counted_faces,
    files,
    listed,
    many,
    said,
    text_of,
    thing,
    things_in,
    usernames,
)
from sift.kernel.vocabulary import Subject


@dataclass(frozen=True, slots=True)
class FilledField:
    """One field a stash-box filled in, and WHAT it put there, as the record says it now.

    `label` is the record's own word for the field, as it reads mid-sentence ("aliases",
    "gender"). `count` is how many rows a list field gained, from the run; one for a single value.
    `values` are the things themselves, each a line so a tag or a username links to its page: the
    box's value for a single field, the aliases, usernames, tags and links the run added for a list.
    `changed` is a single value the record no longer holds, because somebody or something changed it
    after the box filled it in.
    """

    label: str
    count: int = 1
    values: tuple[Line, ...] = ()
    changed: bool = False


def since_removed(count: int) -> str:
    """ "2 since removed": rows a box added that are gone from the record now."""
    return f"{many(count)} since removed"


def filled_field(one: FilledField, most: int | None = FEED_MOST) -> Line:
    """One filled field with what it holds: "gender (Female)", "7 aliases (Ada, Bea, ...)".

    A list names five and folds the rest in place (`listed`); rows the run added that are gone
    from the record are counted last, in words, rather than dropped from a count the run recorded.
    A single value somebody changed afterwards says so: "gender (Female, since changed)". A field
    with nothing to show (a paragraph of details) is its word alone.
    """
    head = f"{many(one.count)} {one.label}" if one.count > 1 else one.label
    items = list(one.values)
    gone = one.count - len(items) if one.count > 1 else 0
    if gone > 0 and items:
        items.append(said(since_removed(gone)))
    if not items:
        return said(head)
    changed = ", since changed" if one.changed else ""
    return said(head, " (", listed(items, most), changed, ")")


def filled_fields(fields: Sequence[FilledField]) -> Line:
    """Every field a box filled in, each with its values, as one list: never folded, because the
    fields ARE what the line is about; only a field's own values fold."""
    return listed([filled_field(one) for one in fields], most=None)


#: WHAT EACH KIND OF THING IS CALLED when a line has to name one and the row kept no name. Older
#: rows have no snapshot, and the reader looks the current name up first
#: (`history_names.names_now`); these words are for what is left.
A_THING: Mapping[str, str] = {
    "asset": "a file",
    "person": "a person",
    "folder": "a folder",
    "pile": "a group of faces",
    "username": "a username",
    "login": "a user",
    "tag": "a tag",
    "site": "a Site",
    "collection": "a Collection",
    "photo_set": "a Photo Set",
    "song": "a song",
    "shoot": "a shoot",
    "box": "a stash-box",
    "grant": "a share",
    "setting": "a setting",
    "download": "a download",
    "run": "a task",
    "swap": "a swap",
    "database_file": "a database file",
    "computer": "the computer running Sift",
}

#: THE SAME, FOR SOMETHING THAT IS NOT THERE ANY MORE and never had its name written down. Only
#: reached where Sift can ask (`history_events.can_be_found`).
A_GONE: Mapping[str, str] = {kind: f"{words} that is gone" for kind, words in A_THING.items()}

#: WHICH LEDGER KINDS A LINE CAN LINK, and the word `history.LINK_KINDS` has for each. A kind not
#: here is named in words and not linked: a sign-in, a stash-box, a share, a shoot, a setting and a
#: task have no page to go to. A username has none either, but the reader that knows whether a
#: person is behind it gives it an address (`username_opens`).
LINKED_KINDS: Mapping[str, str] = {
    "asset": "asset",
    "person": "person",
    "site": "site",
    "tag": "tag",
    "collection": "collection",
    "photo_set": "photo_set",
    "song": "song",
    "folder": "folder",
    "pile": "face_pile",
    "username": "username",
    "download": "download",
}


def name_of(kind: str, name: str | None) -> str:
    """What one thing is called in a line: its name, or what kind of thing it was."""
    return name or A_THING.get(kind, "something")


#: The acts that END the thing they name: a line about the delete or the merge is the one place a
#: gone thing's name is said bare ("Sift deleted the Photo Set Cassia Lynn"), because the line IS
#: why it is gone. Every other line says it with `since_deleted`.
ENDS_ITS_THING: frozenset[str] = frozenset({"deleted", "merged", "forgot"})

#: The kinds a thing's absence means it was DELETED. A download taken off the list keeps its row
#: and is removed, not deleted; a folder, a group of faces and a username go for other reasons.
DELETED_KINDS: frozenset[str] = frozenset(
    {"asset", "person", "site", "tag", "collection", "photo_set", "song"}
)


def since_deleted(name: str) -> str:
    """A thing that is no longer there, by the name it had: the ONE way every line says it.

    Every History page and the feed (its Decisions too) all say it the same way ("Sift created the
    Photo Set Cassia Lynn (since deleted)"), so no line beside it names the set as if it were
    there.
    """
    return f"{name} (since deleted)"


def mention(one: Subject, *, gone: bool = False) -> Piece:
    """One thing an event names, as a piece: a way to it where its kind has a page."""
    words = name_of(one.kind, one.name)
    drawn = LINKED_KINDS.get(one.kind)
    if drawn is None:
        return Piece(words)
    return thing(drawn, one.id, words, gone=gone)


@dataclass(frozen=True, slots=True)
class Group:
    """One group of things a line stands for, listed under its "Show each": the kind every one of
    them is, the words the line counts them in, and the things themselves."""

    kind: str
    words: str
    things: tuple[Piece, ...]


@dataclass(frozen=True, slots=True)
class Said:
    """A finished line, and what its "Show each" opens to where it stands for more than it names."""

    pieces: Line
    #: A line that COUNTS ("You edited 5 details") and the things it counted, in words: the fold's
    #: heading and its members. None where the line names everything itself.
    folded: tuple[str, tuple[str, ...]] | None = None
    #: The things a line is ABOUT and does not name in its words (a decision's files), by kind.
    groups: tuple[Group, ...] = ()

    @property
    def what(self) -> str:
        return text_of(self.pieces)

    @property
    def names(self) -> tuple[Piece, ...]:
        return things_in(self.pieces)


#: WHAT THE RECORD'S EDITABLE FIELDS ARE CALLED, in the words the editor's own form uses.
FIELD_WORDS: Mapping[str, str] = {
    "title": "the title",
    "details": "the details",
    "release_date": "the release date",
    "production_date": "the production date",
    "site_code": "the site code",
    "music": "the music",
    "download_url": "the download address",
    "links": "the links",
    "notes": "the notes",
    # A song's credits, written by its own route (`PUT /songs/{id}/artists`).
    "artists": "the artists",
}

#: How many fields a line names before it counts them instead. Four named is already twelve words.
MOST_FIELDS = 3

#: The entity kinds whose fields the record registry names (`kernel.records`).
_REGISTRY_KINDS = frozenset({"person", "site", "tag"})

#: Registry labels that do not read after "edited": the label heads a row on the record.
_SAID_INSTEAD: Mapping[tuple[str, str], str] = {
    ("site", "parent"): "the parent Site",
    ("tag", "parent"): "the parent tag",
    ("site", "aliases"): "the other names",
    ("person", "aliases"): "the other names",
    ("tag", "aliases"): "the other names",
}


def _entity_field_words(kind: str, fields: Sequence[str]) -> list[str]:
    """The registry's words for an entity's fields, in the record's order, each asked for its own
    word. See `said_plainly`."""
    from sift.kernel.records import Subject as RecordSubject
    from sift.kernel.records import said_plainly

    subject = RecordSubject(kind)
    word = {one: said_plainly(subject, [one])[0] for one in fields}
    order = {spoken: at for at, spoken in enumerate(said_plainly(subject, fields))}
    return [
        _SAID_INSTEAD.get((kind, one), f"the {word[one]}")
        for one in sorted(fields, key=lambda key: order.get(word[key], len(order)))
    ]


def _edited_words(fields: Sequence[str], kind: str | None) -> list[str] | None:
    """Every field a save moved, in words, or None where not all of them have one."""
    if kind in _REGISTRY_KINDS and fields:
        named = _entity_field_words(kind, fields)
    else:
        named = [FIELD_WORDS[one] for one in fields if one in FIELD_WORDS]
    return named if named and len(named) == len(fields) else None


def edited_folded(
    fields: Sequence[str], kind: str | None = None
) -> tuple[str, tuple[str, ...]] | None:
    """The phrase an edit's line counts its fields in, and the fields, or None where it names them."""
    named = _edited_words(fields, kind)
    if named is None or len(named) <= MOST_FIELDS:
        return None
    return f"{many(len(named))} details", tuple(one.removeprefix("the ") for one in named)


def edited_fields(fields: Sequence[str], kind: str | None = None) -> str:
    """What one save moved: named, counted into a fold, or "N of its details" where unknown."""
    if not fields:
        return ""
    named = _edited_words(fields, kind)
    if named is None:
        return f"{many(len(fields))} of its details"
    folded = edited_folded(fields, kind)
    if folded is not None:
        return folded[0]
    return and_then(named)


#: WHAT CHOOSING A COVER WITH NO PICTURE IN THE LIBRARY SAYS, by the word its writer records under
#: the payload's `cover`. Lower case, after the actor.
COVER_WITHOUT_OBJECT: Mapping[str, str] = {
    "picture": "chose a new picture as {whose} cover",
    "none": "removed {whose} cover",
    "reframed": "reframed {whose} cover",
}

#: WHAT AN `edited` EVENT'S OBJECT IS WHEN THE EDIT WAS A COVER BEING CHOSEN: a whole file or one
#: appearance in one. Five writers record this shape; it is the fact, rather than a flag they would
#: all have to agree to set.
COVER_OBJECTS = frozenset({"asset", "pile"})


def cover_changed(word: str, box: str | None = None, *, whose: str = "its") -> str:
    """A cover change with no picture in the library behind it, lower case after the actor."""
    if word == "picture" and box:
        return f"chose {box}'s picture as {whose} cover"
    return COVER_WITHOUT_OBJECT.get(word, "changed {whose} cover").format(whose=whose)


#: The heading over what a merge brought, in the survivor's line's "Show each" fold.
BROUGHT_OVER = "Brought over"

#: How many of each kind a merge writes down BY NAME (`brought_named`): the five a line shows and
#: one more, so a list one over the fold is said whole rather than as "and 1 more" (`FEED_MOST`).
MERGE_NAMES_KEPT = FEED_MOST + 1


def brought_named(names: Sequence[str], count: int) -> str:
    """The names a merge kept for one kind, five and then "and N more" of the `count` that moved.

    `names` are the first `MERGE_NAMES_KEPT` the writer read; past that only the count is known,
    which is what the rest is said as.
    """
    shown = list(names)
    if count <= len(shown) and len(shown) <= MERGE_NAMES_KEPT:
        return and_then(shown)
    shown = shown[:FEED_MOST]
    return f"{', '.join(shown)} and {many(count - len(shown))} more"


def brought_over(brought: Mapping[str, object], kind: str | None) -> tuple[str, ...]:
    """What a merge brought to the survivor, one phrase per kind of thing, as the fold lists it.

    Where the merge wrote down the names of what moved (`named`, by kind), each phrase names them:
    "3 other names (Ada, Bea and Cy)", "2 usernames (riverbend on OnlyFans and riverbend on X)".
    A merge from before that kept only the counts says the counts.
    """

    def counted(key: str) -> int:
        value = brought.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    kept = brought.get("named")
    named_by_kind = kept if isinstance(kept, Mapping) else {}

    def with_names(phrase: str, key: str, count: int) -> str:
        rows = named_by_kind.get(key)
        names: list[str] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or not isinstance(row.get("name"), str):
                continue
            where = row.get("where")
            names.append(f"{row['name']} on {where}" if isinstance(where, str) else row["name"])
        return f"{phrase} ({brought_named(names, count)})" if names else phrase

    listed_now: list[str] = []
    if count := counted("files"):
        listed_now.append(files(count))
    if count := counted("usernames"):
        listed_now.append(with_names(usernames(count), "usernames", count))
    if count := counted("aliases"):
        phrase = "1 other name" if count == 1 else f"{many(count)} other names"
        listed_now.append(with_names(phrase, "aliases", count))
    if count := counted("links"):
        if kind == "site":
            phrase = "1 address" if count == 1 else f"{many(count)} addresses"
        else:
            phrase = "1 link" if count == 1 else f"{many(count)} links"
        listed_now.append(with_names(phrase, "links", count))
    if count := counted("tags"):
        listed_now.append(
            with_names("1 tag" if count == 1 else f"{many(count)} tags", "tags", count)
        )
    if count := counted("faces"):
        listed_now.append(counted_faces(count))
    if count := counted("children"):
        listed_now.append(
            "1 Site published under it" if count == 1 else f"{many(count)} Sites published under it"
        )
    filled = brought.get("filled")
    keys = [one for one in filled if isinstance(one, str)] if isinstance(filled, list) else []
    if keys:
        listed_now.append(f"{and_then(_filled_words(kind, keys))} filled in")
    return tuple(listed_now)


def _filled_in(
    by: str,
    subject: Piece,
    kind: str,
    box: Piece,
    payload: Mapping[str, object],
    named: Sequence[FilledField] | None = None,
) -> Line:
    """A stash-box's answer applied, in the feed: WHICH details, named, and the box said once.

    "StashDB filled in Neve Alder's birthday and height" where the box acted on its own, "You
    filled in Neve Alder's birthday from StashDB" where somebody pressed it. The payload is the
    run's `key -> count`; past `MOST_FIELDS` the details are counted. "StashDB filled in Neve Alder
    from StashDB" said the box twice and not one thing it filled. A run that recorded no detail
    (a bare link, or nothing new) says what is true of both: the box recognized them.

    `named` is the same fields WITH WHAT THEY HOLD (`history_boxes.filled_named`): up to
    `MOST_FIELDS` the line names each with its values, as the thing's own History does; past it the
    line counts and `filled_in_fold` lists them.
    """
    keys = [str(key) for key in payload]
    words = _filled_words(kind, keys) if keys else []
    if not words:
        if by == box.text:
            return said(by, " recognized ", subject)
        return said(by, " linked ", subject, " to ", box)
    if len(words) > MOST_FIELDS:
        what: str | None = f"{many(len(words))} of"
        after = "'s details"
    else:
        what, after = None, f"'s {and_then(words)}"
    from_box: Part = None if by == box.text else said(" from ", box)
    if what is None and named:
        return said(by, " filled in ", subject, "'s ", filled_fields(named), from_box)
    if what is not None:
        return said(by, f" filled in {what} ", subject, after, from_box)
    return said(by, " filled in ", subject, after, from_box)


def filled_in_fold(named: Sequence[FilledField] | None) -> tuple[str, tuple[str, ...]] | None:
    """What a counted box line opens to: each field it filled with its values, whole, under the
    same "N details" the line counts in. None where the line names every field itself."""
    if not named or len(named) <= MOST_FIELDS:
        return None
    return (
        f"{many(len(named))} details",
        tuple(text_of(filled_field(one, most=None)) for one in named),
    )


def _filled_words(kind: str | None, keys: Sequence[str]) -> list[str]:
    """The record's own words for the blanks a merge filled, in the record's order, the cover last."""
    from sift.kernel.records import Subject as RecordSubject
    from sift.kernel.records import said_plainly

    fields = [one for one in keys if one != "cover"]
    words = (
        list(said_plainly(RecordSubject(kind), fields))
        if kind in _REGISTRY_KINDS and fields
        else [one.replace("_", " ") for one in fields]
    )
    if "cover" in keys:
        words.append("cover picture")
    return words
