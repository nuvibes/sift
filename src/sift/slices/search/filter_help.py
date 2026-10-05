# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the search box offers while somebody types: the word under the caret and the filters that
match it."""

from __future__ import annotations

from dataclasses import dataclass

from sift.slices.search.filter_fields import ALIASES, Field
from sift.slices.search.filter_parse import _OR, _as_field, _scan, _unquote


@dataclass(frozen=True, slots=True)
class Caret:
    """Where the caret is, for the dropdown: which token, what has been typed of its value, and
    where in the line that token starts."""

    field: Field
    prefix: str
    at: int


def token_prefix(text: str) -> Caret | None:
    """The token the caret is sitting in, for the search box."""
    pieces = _scan(text)
    if not pieces or (text and text[-1].isspace()):
        return None
    begin, last = pieces[-1]
    negated = last.startswith("-")
    body = last[1:] if negated else last
    name, colon, value = body.partition(":")
    if not colon:
        return None
    found = _as_field(_unquote(name))
    if found is None:
        return None
    return Caret(field=found, prefix=_unquote(value).strip(), at=begin + (1 if negated else 0))


@dataclass(frozen=True, slots=True)
class Word:
    """A bare word the caret is sitting on, for offering the entities it could become."""

    prefix: str
    at: int


def word_prefix(text: str) -> Word | None:
    """The bare word the caret is on, if it is on one and not inside a `field:` token."""
    pieces = _scan(text)
    if not pieces or (text and text[-1].isspace()):
        return None
    begin, last = pieces[-1]
    body = last[1:] if last.startswith("-") else last
    name, colon, _ = body.partition(":")
    if colon and _as_field(_unquote(name)) is not None:
        return None
    word = _unquote(last).strip()
    if not word:
        return None
    return Word(prefix=word, at=begin)


def phrase_prefix(text: str) -> Word | None:
    """The whole run of plain words at the end of the line, rather than only the last of them."""
    pieces = _scan(text)
    if not pieces or (text and text[-1].isspace()):
        return None

    first = len(pieces)
    for index in range(len(pieces) - 1, -1, -1):
        _, raw = pieces[index]
        body = raw[1:] if raw.startswith("-") else raw
        name, colon, _ = body.partition(":")
        if colon and _as_field(_unquote(name)) is not None:
            break
        if _unquote(raw).strip().lower() == _OR:
            break
        first = index

    if first >= len(pieces) - 1:
        return None
    at = pieces[first][0]
    phrase = text[at:].strip()
    return Word(prefix=phrase, at=at) if phrase else None


# --- the filters themselves, as something to offer ---------------------------------------------


@dataclass(frozen=True, slots=True)
class FilterHelp:
    """One filter, described for somebody who has never seen the query language."""

    field: Field
    label: str
    hint: str
    example: str
    #: Where a filter that takes an id is written, drawn after its bare token instead of an id.
    set_from: str | None = None

    def __post_init__(self) -> None:
        """Refuse a label the parser would not take, at import."""
        spelling = _spelling(self.label)
        if spelling != self.field.value and ALIASES.get(spelling) is not self.field:
            raise ValueError(
                f"the label {self.label!r} is not a spelling of {self.field.value!r}: "
                f"add {spelling!r} to ALIASES, or the dropdown will teach a word the parser refuses"
            )


def _spelling(label: str) -> str:
    """The token a label is written as: lowercased, underscores for the spaces."""
    return label.strip().casefold().replace(" ", "_")


#: Every filter, in the order they are worth learning.
FILTERS: tuple[FilterHelp, ...] = (
    FilterHelp(Field.TAGS, "Tags", "Anything you have tagged", "tags:beach"),
    FilterHelp(Field.PEOPLE, "People", "Someone in it", "people:alex"),
    FilterHelp(Field.SITES, "Sites", "The site it came from", "sites:youtube"),
    FilterHelp(Field.COLLECTIONS, "Collections", "A collection you made", "collections:summer"),
    FilterHelp(
        Field.PHOTO_SETS,
        "Photo Sets",
        "A set of pictures that arrived together",
        "photo_sets:beach",
    ),
    # The label is the token written for reading (`_one_name`), so it stays "Songs" although the
    # page is Music: "Music" is already `music:`'s label, the Music field of a file.
    FilterHelp(Field.SONGS, "Songs", "A song from the Music page", "songs:blue"),
    FilterHelp(
        Field.LOOPS,
        "Loops",
        "Videos you have marked a moment in",
        "loops:any",
    ),
    FilterHelp(Field.IN, "Folder", "A folder in your library", "in:videos"),
    FilterHelp(Field.MEDIA, "Media", "Video, image or GIF", "media:video"),
    FilterHelp(Field.FILETYPE, "File type", "The container on disk", "filetype:mp4"),
    FilterHelp(Field.RATING, "Rating", "Your stars, exactly or upwards", "rating:4+"),
    # The label is two words so that it reads, and `_spelling` turns it back into the token, which
    # is what makes typing what the panel taught you work.
    FilterHelp(Field.O_COUNT, "O count", "Your tally, exactly or upwards", "o_count:5+"),
    FilterHelp(Field.FAV, "Favorites", "Whether you hearted it", "fav:yes"),
    FilterHelp(
        Field.SHARING,
        "Sharing status",
        "Shared or restricted on the file itself",
        "sharing:shared",
    ),
    FilterHelp(Field.DURATION, "Duration", "How long a video runs", "duration:2m+"),
    FilterHelp(Field.ADDED, "Added", "When it arrived", "added:7d"),
    FilterHelp(Field.RESOLUTION, "Resolution", "How big the picture is", "resolution:4k"),
    FilterHelp(Field.SIZE, "File size", "How much room it takes", "size:500mb+"),
    FilterHelp(Field.VCODEC, "Video codec", "How the picture is encoded", "vcodec:h264"),
    FilterHelp(Field.ACODEC, "Audio codec", "How the sound is, or none", "acodec:none"),
    FilterHelp(Field.TITLE, "Title", "The name it was given", "title:holiday"),
    FilterHelp(Field.MUSIC, "Music", "The track it is set to", 'music:"night drive"'),
    # Quoted, and it is the only example here that needs to be. Every other filter takes a
    # single word; a track name is usually several, and unquoted `music:night drive` parses as
    # `music:night` with a stray `drive` as free text, which filters to nothing and looks like
    # the filter not working. An example somebody copies has to be one that runs.
    FilterHelp(Field.FILENAME, "File name", "The name on disk", "filename:holiday"),
    # "View status" rather than "Viewed": the column this names answers WHICH of several states a
    # file is in, and a heading that is one of those states reads as a filter for that state.
    FilterHelp(
        Field.VIEWED,
        "View status",
        "Finished, part-way through, started, or never opened",
        "viewed:started",
    ),
    FilterHelp(
        Field.ORIENTATION, "Orientation", "Landscape, portrait or square", "orientation:portrait"
    ),
    FilterHelp(
        Field.ENRICHED,
        "Enriched by",
        "A stash-box, AcoustID, or something Sift read: a face, a folder name, a file name,"
        " a watermark",
        "enriched:stash",
    ),
    FilterHelp(
        Field.CREATED,
        "Created by",
        "A copy Sift made, a download, a swap, or a folder of your library",
        "created:download",
    ),
    # It counts `stash_box_scans`, one row per box ASKED, and the label says that question. Counting
    # `enrichment_runs` (a box that ENRICHED something) under "Never enriched" would contradict the
    # "Enriched by" column in plain sight. `enrichment:` still parses (`ALIASES` keeps it), so a
    # saved search and a shared link written under the old name go on meaning what they meant.
    FilterHelp(
        Field.ENRICHMENT,
        "Asked a stash-box",
        "Kept local, never asked, or how lately one was last asked about it",
        "enrichment:never",
    ),
    # The file's release date, as a year. `production_date` is a record field and not a filter;
    # see `Field.RELEASED` for why there is only one year word.
    FilterHelp(Field.RELEASED, "Released", "The year it was published", "released:2021"),
    # The four that take an id are written by a click, so their row says where instead of an id.
    FilterHelp(
        Field.NETWORK,
        "Network",
        "Everything one network put out",
        "network:",
        set_from="from the Network column",
    ),
    FilterHelp(
        Field.SAME_MUSIC,
        "Same music",
        "Files that share a song with one file",
        "same_music:",
        set_from="from a file",
    ),
    FilterHelp(
        Field.LIKE,
        "Similar to this",
        "Files similar to one file",
        "like:",
        set_from="from a file",
    ),
    # The files one product gave up on. Written by the count in the Importing pane's sentence
    # ("24 files couldn't have thumbnails generated and are left out"), and typeable.
    FilterHelp(
        Field.LEFT_OUT,
        "Left out",
        "Files Generate or Identify couldn't make something for",
        "left_out:thumbnails",
    ),
    FilterHelp(
        Field.UNNAMED_FACE,
        "Face still unnamed",
        "Files filed under one person from a folder, with a face nobody named yet",
        "unnamed_face:",
        set_from="from a person",
    ),
    # WHAT THE PEOPLE ON A FILE ARE LIKE. Last, and in the order the person record declares them,
    # because these answer a question about somebody rather than about the file, and somebody
    # reading the list for the first time is looking for the file's own properties.
    FilterHelp(Field.GENDER, "Gender", "The gender of someone in it", "gender:female"),
    FilterHelp(Field.HAIR, "Hair color", "The hair color of someone in it", "hair:blonde"),
    FilterHelp(Field.EYES, "Eye color", "The eye color of someone in it", "eyes:brown"),
    FilterHelp(Field.ETHNICITY, "Ethnicity", "The ethnicity of someone in it", "ethnicity:asian"),
    FilterHelp(Field.NATIONALITY, "Nationality", "Where someone in it is from", "nationality:us"),
    FilterHelp(Field.BREASTS, "Breast type", "Natural or enhanced", "breasts:natural"),
    FilterHelp(Field.HEIGHT, "Height", "How tall someone in it is, in bands", "height:160-169"),
    FilterHelp(Field.AGE, "Age", "How old someone in it is", "age:27"),
    # The one of these that answers yes or no rather than naming a value, because the column behind
    # it is a flag. Said as what somebody is looking for (files a creator is in) rather than as
    # the state of a column.
    FilterHelp(Field.PMV, "PMV creator", "Files with a PMV creator in them", "pmv:yes"),
)


def filters_matching(prefix: str) -> tuple[FilterHelp, ...]:
    """The filters worth offering for what has been typed so far."""
    wanted = prefix.strip().casefold()
    if not wanted:
        return FILTERS
    return tuple(
        entry
        for entry in FILTERS
        if entry.field.value.startswith(wanted)
        or entry.label.casefold().startswith(wanted)
        or any(old.startswith(wanted) for old, field in ALIASES.items() if field is entry.field)
    )
