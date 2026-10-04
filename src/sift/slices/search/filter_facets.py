# SPDX-License-Identifier: AGPL-3.0-or-later
"""The facets of what a file is: what it is left out of, its record, its age, sharing, kind,
resolution, size and codec, and the one door that compiles any of them."""

from __future__ import annotations

import re
from collections.abc import Mapping

from sift.kernel.access import (
    AllOf,
    AnyOf,
    ConstraintError,
    Not,
    Where,
    like_anywhere,
)
from sift.kernel.access import (
    Node as Constraint,
)
from sift.kernel.access.constraints import same_music_where
from sift.kernel.content import VerdictProduct
from sift.kernel.ids import is_id
from sift.slices.search.filter_fields import (
    _NUMBER,
    ENTITY_FIELDS,
    MAX_NUMBER,
    Field,
    Problem,
    Query,
    Term,
    Unreadable,
)
from sift.slices.search.filter_when import (
    _VIEWED_DONE,
    _VIEWED_STATES,
    Range,
    _flag,
    _instant,
    _length,
    _number,
    _range,
    _since,
    _stars,
    _times,
    _viewed,
)

#: What `orientation:` accepts, with the words people reach for.
_ORIENTATIONS = {
    "landscape": "landscape",
    "wide": "landscape",
    "horizontal": "landscape",
    "portrait": "portrait",
    "tall": "portrait",
    "vertical": "portrait",
    "square": "square",
}


#: What `created:` accepts: the five makers of a file, and the words people reach for each.
_CREATED_WAYS = {
    "library": "library",
    "folder": "library",
    "download": "download",
    "downloaded": "download",
    "swap": "swap",
    "swapped": "swap",
    "compress": "compress",
    "compressed": "compress",
    "edit": "edit",
    "edited": "edit",
}


#: What `enriched:` accepts: the seven ways, and the words people reach for each.
_ENRICHED_WAYS = {
    "stash": "enriched_stash",
    "stashbox": "enriched_stash",
    "stash-box": "enriched_stash",
    "stash_box": "enriched_stash",
    "faces": "enriched_faces",
    "face": "enriched_faces",
    "folder": "enriched_folder",
    "folders": "enriched_folder",
    "filename": "enriched_filename",
    "filenames": "enriched_filename",
    "watermark": "enriched_watermark",
    "watermarks": "enriched_watermark",
    "metadata": "enriched_metadata",
    "acoustid": "enriched_acoustid",
}


#: What `left_out:` accepts: every product the Importing pane can say "left out" about, by its own
#: key, and the words people reach for each (the pane's own labels among them, as one word).
LEFT_OUT_WAYS: dict[str, VerdictProduct] = {
    "thumbnails": VerdictProduct.THUMBNAILS,
    "thumbnail": VerdictProduct.THUMBNAILS,
    "previews": VerdictProduct.PREVIEWS,
    "preview": VerdictProduct.PREVIEWS,
    "hover_previews": VerdictProduct.PREVIEWS,
    "sprites": VerdictProduct.SPRITES,
    "sprite": VerdictProduct.SPRITES,
    "scrubber_strips": VerdictProduct.SPRITES,
    "fingerprints": VerdictProduct.FINGERPRINTS,
    "fingerprint": VerdictProduct.FINGERPRINTS,
    "faces": VerdictProduct.FACES,
    "face": VerdictProduct.FACES,
    "meaning": VerdictProduct.MEANING,
    "watermarks": VerdictProduct.WATERMARKS,
    "watermark": VerdictProduct.WATERMARKS,
}


#: Every product `left_out:any` and `left_out:none` range over, in the Build's order.
LEFT_OUT_PRODUCTS: tuple[VerdictProduct, ...] = tuple(dict.fromkeys(LEFT_OUT_WAYS.values()))


def _left_out_of(value: str) -> tuple[VerdictProduct, ...]:
    """The products one `left_out:` value names. Raises for a word that names none."""
    text = value.strip().lower().replace("-", "_")
    if text in _EITHER_WORDS or text in _UNSHARED_WORDS:
        return LEFT_OUT_PRODUCTS
    if text in LEFT_OUT_WAYS:
        return (LEFT_OUT_WAYS[text],)
    raise Unreadable("that is not something Sift makes for a file")


def _left_out(value: str) -> Constraint:
    """`left_out:` as a condition: the files this product has a standing verdict against."""
    each = tuple(Where("left_out", (product.value,)) for product in _left_out_of(value))
    text = value.strip().lower()
    if text in _EITHER_WORDS:
        return AnyOf(each)
    if text in _UNSHARED_WORDS:
        return AllOf(tuple(Not(one) for one in each))
    return each[0]


def left_out_products(query: Query) -> tuple[str, ...]:
    """Every product a `left_out:` term in this query asks about, in the Build's order."""
    named: set[VerdictProduct] = set()
    for leaf in query.leaves():
        if isinstance(leaf, Term) and leaf.field is Field.LEFT_OUT:
            try:
                named.update(_left_out_of(leaf.value))
            except Unreadable:
                continue
    return tuple(product.value for product in LEFT_OUT_PRODUCTS if product in named)


#: What `enrichment:` accepts, and the words people reach for each.
_ENRICHMENT_BANDS = {
    "local": "local",
    "kept": "local",
    "keptlocal": "local",
    "kept-local": "local",
    "never": "never",
    "none": "never",
    "today": "today",
    "day": "today",
    "week": "week",
    "month": "month",
    "older": "older",
    "old": "older",
}


def _enrichment(value: str) -> Constraint:
    """`enrichment:` as a condition: how lately a stash-box was asked about this file, or never."""
    text = value.strip().lower()
    if text not in _ENRICHMENT_BANDS:
        raise Unreadable("that is not a way a file stands with enrichment")
    return Where("enrichment", (_ENRICHMENT_BANDS[text],))


def _orientation(value: str) -> Constraint:
    """`orientation:` as a condition. Three words; a file never measured answers none of them."""
    text = value.strip().lower()
    if text not in _ORIENTATIONS:
        raise Unreadable("that is not an orientation")
    return Where("orientation", (_ORIENTATIONS[text],))


def _created(value: str) -> Constraint:
    """`created:` as a condition: which of the five made the file. One value, bound."""
    text = value.strip().lower()
    if text not in _CREATED_WAYS:
        raise Unreadable("that is not a way a file is made")
    return Where("created", (_CREATED_WAYS[text],))


def _enriched(value: str) -> Constraint:
    """`enriched:` as a condition: which of the seven things wrote to the file."""
    text = value.strip().lower()
    every = (
        Where("enriched_stash"),
        Where("enriched_faces"),
        Where("enriched_folder"),
        Where("enriched_filename"),
        Where("enriched_watermark"),
        Where("enriched_metadata"),
        Where("enriched_acoustid"),
    )
    if text in _EITHER_WORDS:
        return AnyOf(every)
    if text in _UNSHARED_WORDS:
        return AllOf(tuple(Not(one) for one in every))
    if text in _ENRICHED_WAYS:
        return Where(_ENRICHED_WAYS[text])
    if _looks_like_a_box(text):
        return Where("enriched_box", (text,))
    raise Unreadable("that is not a way a file is enriched")


#: What a stash-box's own word may be made of, and nothing else.
_BOX_WORD = re.compile(r"^[a-z0-9]{2,40}$")


def _looks_like_a_box(text: str) -> bool:
    """Whether this could be a stash-box's own word. Not whether one by that word is configured."""
    return bool(_BOX_WORD.match(text))


#: The six words read off a PERSON's own record, and the condition each of them becomes.
_PERSON_WORDS: dict[Field, str] = {
    Field.GENDER: "gender",
    Field.HAIR: "hair",
    Field.EYES: "eyes",
    Field.ETHNICITY: "ethnicity",
    Field.NATIONALITY: "nationality",
    Field.BREASTS: "breasts",
}


#: A four-digit year, which is the whole of what `released:` takes.
_YEAR = re.compile(r"^\d{4}$")


#: A BAND, written exactly as the facet column writes it: `160-169`, `200+`, `<150`.
_BAND = re.compile(r"^(?:<\d+|\d+\+|\d+-\d+)$")


def _year(value: str) -> str:
    """A year, for the two dates a file carries. The year and not the day (see `Field.RELEASED`)."""
    text = value.strip()
    if not _YEAR.match(text):
        raise Unreadable("that is not a year")
    return text


#: The oldest age a span with no top end reaches: past any birthdate the column reads.
_OLDEST = 120


def _age_span(value: str) -> tuple[int, int]:
    """An age as the two ends the kernel compares against (`AGE_WITHIN`)."""
    text = value.strip()
    if not text.isdigit():
        text = _band(text)
    if text.isdigit():
        return int(text), int(text)
    if text.endswith("+"):
        return int(text[:-1]), _OLDEST
    if text.startswith("<"):
        return 0, int(text[1:]) - 1
    low, high = text.split("-")
    return int(low), int(high)


def _band(value: str) -> str:
    """One band name, as the facet column writes it. See `_BAND` for why it is not parsed."""
    text = value.strip()
    if not _BAND.match(text):
        raise Unreadable("that is not a band")
    return text


#: What `sharing:` accepts. Three answers and their obvious synonyms, because this is a token
#: people type as well as a control they click.
_SHARED_WORDS = frozenset({"shared", "share"})


_RESTRICTED_WORDS = frozenset({"restricted", "restrict"})


_UNSHARED_WORDS = frozenset({"none", "no", "private", "neither"})


_EITHER_WORDS = frozenset({"any", "yes", "either"})


def _sharing(value: str) -> Constraint:
    """`sharing:` as a condition. See the two predicates it uses for what it does and does not ask."""
    text = value.strip().lower()
    shared = Where("shared_here")
    restricted = Where("restricted_here")
    if text in _SHARED_WORDS:
        return shared
    if text in _RESTRICTED_WORDS:
        return restricted
    if text in _UNSHARED_WORDS:
        return AllOf((Not(shared), Not(restricted)))
    if text in _EITHER_WORDS:
        return AnyOf((shared, restricted))
    raise Unreadable("that is not a sharing state")


#: The three kinds of media, as the schema spells them. Read by the parser and by the values the
#: dropdown offers after `media:`, so the list offered and the list understood are one list.
_MEDIA_KINDS = ("video", "image", "gif")


def _media_type(value: str) -> str:
    text = value.strip().lower()
    # Named here rather than imported as a set to iterate, because the point is the alias: people
    # say "photo" and "clip", and the schema says "image" and "video".
    aliases = {"photo": "image", "picture": "image", "clip": "video", "movie": "video"}
    text = aliases.get(text, text)
    if text not in _MEDIA_KINDS:
        raise Unreadable("that is not a kind of media")
    return text


#: What the names for a picture size mean, as the shorter side in pixels.
_RESOLUTIONS = {
    "8k": 4320,
    "4k": 2160,
    "uhd": 2160,
    "2k": 1440,
    "1440p": 1440,
    "1080p": 1080,
    "fhd": 1080,
    "hd": 720,
    "720p": 720,
    "480p": 480,
    "360p": 360,
    "sd": 480,
}


#: How much room something takes. Powers of 1024, because that is what a file manager shows and
#: what somebody comparing two numbers on screen is reading.
_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmgt]?b)$")


#: The word for a file with no sound at all. `acodec:none` is a real question (a silent clip is
#: a thing people look for), and it cannot be asked by naming a codec.
_NO_AUDIO = frozenset({"none", "no", "silent"})


def _pixels(value: str) -> int:
    """A picture size as a number of pixels on the shorter side."""
    text = value.strip().lower()
    named = _RESOLUTIONS.get(text)
    if named is not None:
        return named
    if not _NUMBER.match(text):
        raise Unreadable("that is not a picture size")
    return _number(text)


def _resolution(value: str) -> tuple[int, int]:
    """A picture size as an END of a range: the number itself, exactly."""
    edge = _pixels(value)
    return edge, edge


def _resolution_band(value: str) -> tuple[int, int]:
    """A picture size STANDING ALONE, which is a band rather than a number."""
    text = value.strip().lower()
    named = _RESOLUTIONS.get(text)
    if named is None:
        return _resolution(value)
    sizes = sorted(set(_RESOLUTIONS.values()))
    above = [size for size in sizes if size > named]
    # The SMALLEST named size opens downward. It is the bottom of the scale, so "360p" is what
    # anybody would call a file below it too, and the facet panel groups them there, so a band
    # that started exactly at 360 would count files the filter behind it then refused to return.
    low = 0 if named == sizes[0] else named
    return low, (above[0] - 1 if above else MAX_NUMBER)


def _bytes(value: str) -> int:
    """A file size in bytes. Written the way people write one: `500mb`, `1.5gb`, `2048`."""
    text = value.strip().lower()
    if _NUMBER.match(text):
        return _number(text)
    found = _SIZE.match(text)
    if found is None:
        raise Unreadable("that is not a file size")
    amount = float(found.group(1))
    scaled = int(amount * _SIZE_UNITS[found.group(2)])
    if scaled > MAX_NUMBER:
        raise Unreadable("no file is that big")
    return scaled


def _size(value: str) -> tuple[int, int]:
    """A file size as an END of a range: that many bytes, exactly."""
    edge = _bytes(value)
    return edge, edge


def _size_band(value: str) -> tuple[int, int]:
    """A file size STANDING ALONE, which is a band rather than an exact number of bytes."""
    text = value.strip().lower()
    found = _SIZE.match(text)
    if found is None:
        return _size(value)
    step = _SIZE_UNITS[found.group(2)]
    low = _bytes(text)
    return low, low + step - 1


def _codec(value: str) -> str:
    """A codec name, with the spellings people use folded onto the ones ffmpeg writes."""
    text = value.strip().lower()
    # Named rather than accepted as-is, because the column holds ffmpeg's spelling and nobody says
    # `hevc` when they mean the thing their phone calls HEVC and their player calls H.265.
    aliases = {
        "h.264": "h264",
        "avc": "h264",
        "x264": "h264",
        "h.265": "hevc",
        "h265": "hevc",
        "x265": "hevc",
        "av-1": "av1",
        "vp-9": "vp9",
    }
    if not text:
        raise Unreadable("that is not a codec")
    return aliases.get(text, text)


def _container(value: str) -> str:
    """A file type, without the dot somebody may have typed in front of it."""
    text = value.strip().lower().lstrip(".")
    if not text:
        raise Unreadable("that is not a file type")
    return text


def _first_spellings(words: Mapping[str, object]) -> tuple[str, ...]:
    """The first word each answer in a table of spellings is written as, in the table's order."""
    first: dict[object, str] = {}
    for word, meaning in words.items():
        first.setdefault(meaning, word)
    return tuple(first.values())


#: THE VALUES A FILTER WITH A FIXED SET OF ANSWERS OFFERS AFTER ITS PREFIX: `media:` offers video,
#: image and gif as `tags:` offers the tags.
OFFERED_VALUES: dict[Field, tuple[str, ...]] = {
    Field.MEDIA: _MEDIA_KINDS,
    Field.ORIENTATION: _first_spellings(_ORIENTATIONS),
    Field.FAV: ("yes", "no"),
    Field.PMV: ("yes", "no"),
    Field.LOOPS: ("any", "none"),
    Field.VIEWED: (*_first_spellings(_VIEWED_STATES), *_VIEWED_DONE, "none"),
    Field.SHARING: ("shared", "restricted", "none", "any"),
    Field.ENRICHED: _first_spellings(_ENRICHED_WAYS),
    Field.CREATED: _first_spellings(_CREATED_WAYS),
    Field.ENRICHMENT: _first_spellings(_ENRICHMENT_BANDS),
    Field.LEFT_OUT: _first_spellings(LEFT_OUT_WAYS),
}


#: Every field the dropdown offers values for after its prefix: the ones looked up in the library,
#: and the ones with a fixed list above. The suggest route asks this and nothing narrower.
SUGGESTED_FIELDS = ENTITY_FIELDS | frozenset(OFFERED_VALUES)


def problems_in(query: Query, *, now: int) -> list[Problem]:
    """Every filter in the query whose value cannot be read, with the reason."""
    found: list[Problem] = []
    for leaf in query.leaves():
        # An entity term (a tag, a person, a folder) is resolved by name against the library,
        # not converted; a name nothing matches is not a value nobody could read.
        if not isinstance(leaf, Term) or leaf.field in ENTITY_FIELDS:
            continue
        try:
            _scalar(leaf, now=now)
        except Unreadable as why:
            found.append(Problem(field=leaf.field.value, value=leaf.value, reason=str(why)))
    return found


def scalar(term: Term, *, now: int) -> Constraint:
    """One non-entity term as a condition. Pure: given a term and a clock it is a function."""
    try:
        return _scalar(term, now=now)
    except Unreadable:
        return AnyOf()


def _scalar(term: Term, *, now: int) -> Constraint:
    """The conversion itself, which raises `Unreadable` for a value nobody could act on."""
    for family in (_file_scalar, _mark_scalar, _person_scalar):
        found = family(term)
        if found is not None:
            return found
    if term.field is Field.RATING:
        return _between("rating_min", "rating_max", _range(term.value, _stars))
    if term.field is Field.O_COUNT:
        # The same range grammar the stars take (`o_count:3`, `o_count:5+`, `o_count:2..4`), so
        # a facet row's value IS a filter somebody could have typed. The bands the column cuts are
        # written in exactly this grammar; see `FACETS` in the access layer.
        return _between("o_count_min", "o_count_max", _range(term.value, _times))
    if term.field is Field.DURATION:
        return _between("duration_min", "duration_max", _range(term.value, _length))
    return _between(
        "added_from",
        "added_to",
        _range(
            term.value,
            lambda value: _instant(value, now),
            lambda value: _since(value, now),
        ),
    )


def _file_scalar(term: Term) -> Constraint | None:
    """What the file is: its kind, container, codecs, names, resolution and size."""
    if term.field is Field.MEDIA:
        return Where("media_type", (_media_type(term.value),))
    if term.field is Field.FILETYPE:
        return Where("container", (_container(term.value),))
    if term.field is Field.VCODEC:
        return Where("vcodec", (_codec(term.value),))
    if term.field is Field.ACODEC:
        # "No sound at all" is the honest question a codec name cannot ask, so it is answered
        # by the presence of a track rather than by comparing against a name.
        if term.value.strip().lower() in _NO_AUDIO:
            return Not(Where("has_audio"))
        return Where("acodec", (_codec(term.value),))
    if term.field is Field.TITLE:
        return Where("title", (like_anywhere(term.value.strip()),))
    if term.field is Field.MUSIC:
        return Where("music", (like_anywhere(term.value.strip()),))
    if term.field is Field.FILENAME:
        pattern = like_anywhere(term.value.strip())
        # Bound twice because the predicate asks two columns the same question: the name it
        # arrived under and the name it is stored under now.
        return Where("filename", (pattern, pattern))
    if term.field is Field.RESOLUTION:
        return _between(
            "height_min", "height_max", _range(term.value, _resolution, _resolution_band)
        )
    if term.field is Field.SIZE:
        return _between("size_min", "size_max", _range(term.value, _size, _size_band))
    return None


def _mark_scalar(term: Term) -> Constraint | None:
    """What has been done to the file: viewed, marked, shared, enriched, made, left out."""
    if term.field is Field.VIEWED:
        return _viewed(term.value)
    if term.field is Field.FAV:
        favorite = Where("favorite")
        return favorite if _flag(term.value) else Not(favorite)
    if term.field is Field.PMV:
        # Read the same way `fav:` is, and for the same reason: the column is a flag, so `no` is
        # the NEGATION of the predicate rather than a second value to compare against. Written as
        # `Not` rather than as a second leaf so there is one condition to keep right.
        creator = Where("pmv_creator")
        return creator if _flag(term.value) else Not(creator)
    if term.field is Field.SHARING:
        return _sharing(term.value)
    if term.field is Field.ORIENTATION:
        return _orientation(term.value)
    if term.field is Field.ENRICHED:
        return _enriched(term.value)
    if term.field is Field.CREATED:
        return _created(term.value)
    if term.field is Field.ENRICHMENT:
        return _enrichment(term.value)
    if term.field is Field.LEFT_OUT:
        return _left_out(term.value)
    return None


def _person_scalar(term: Term) -> Constraint | None:
    """Who is in the file and what it belongs to: the people's own words, network, likeness."""
    if term.field in _PERSON_WORDS:
        return Where(_PERSON_WORDS[term.field], (term.value.strip(),))
    if term.field is Field.HEIGHT:
        return Where("height", (_band(term.value),))
    if term.field is Field.AGE:
        return Where("age", _age_span(term.value))
    if term.field is Field.RELEASED:
        return Where("released", (_year(term.value),))
    if term.field is Field.NETWORK:
        # One site id, through the leaf `sites:` ALREADY uses, not a leaf of its own.
        return Where("sites", (term.value.strip(),))
    if term.field is Field.SAME_MUSIC:
        # The access layer decides the id's place in the predicate and refuses a value that is
        # not shaped like an ID with a `ConstraintError`. Turned into this module's own
        # `Unreadable` HERE, because this is the conversion every caller shares: `scalar` answers
        # it as the condition nothing satisfies and `problems_in` names it. Let through as it is,
        # `same_music:beach` would answer 500 on the wall and on the search box's parse alike: the
        # service's own catch sits around building the filter, after this conversion has run.
        try:
            return same_music_where(term.value)
        except ConstraintError as refused:
            raise Unreadable(str(refused)) from refused
    if term.field is Field.UNNAMED_FACE:
        if not is_id(term.value.strip()):
            raise Unreadable("unnamed_face takes the ID of one person")
        return Where("unnamed_face", (term.value.strip(),))
    if term.field is Field.LIKE:
        # Only the value's SHAPE is decided here, so `problems_in` can name `like:beach`. Which files
        # an id names is the index's answer: the compiler asks it and builds this term itself
        # (`FilterCompiler._build`), so reaching this line for a well-formed id matches nothing.
        if not is_id(term.value.strip()):
            raise Unreadable("like takes the ID of one file")
        return Where("assets", ())
    return None


def _between(low: str, high: str, span: Range) -> Constraint:
    """A range as the conditions on its ends. A range with neither end cannot be written."""
    ends: list[Constraint] = []
    if span.low is not None:
        ends.append(Where(low, (span.low,)))
    if span.high is not None:
        ends.append(Where(high, (span.high,)))
    if not ends:
        return AnyOf()
    if len(ends) == 1:
        return ends[0]
    return AllOf(tuple(ends))


# --- the compiler ---------------------------------------------------------------------------


def _folded(name: str) -> str:
    return name.strip().casefold()


def _ids_among(values: list[str]) -> list[str]:
    """The values shaped like an id, for the one batched read that answers them all."""
    return sorted({value.strip() for value in values if is_id(value.strip())})


#: How many files to ask a model about, however small the page is.
CANDIDATES = 200


#: How far that ask may grow before the search gives up and says so.
CANDIDATE_CEILING = 500


#: How much wider each step asks. Four rather than two: a step is a round trip plus a count, and
#: doubling from two hundred takes five steps to reach the ceiling where this takes two.
CANDIDATE_STEP = 4


def _cut_down(reached: int, offered: int) -> bool:
    """Whether everything BESIDE the words threw away more than half of what the model offered."""
    return reached * 2 < offered
