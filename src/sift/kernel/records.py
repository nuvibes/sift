# SPDX-License-Identifier: AGPL-3.0-or-later
"""The field registry: what a person, a site, a tag or a file is made of, and how to draw one.

Declared once so the record, the table and the reconcile screen agree; only fillable fields."""

from __future__ import annotations

from sift.kernel.records_found import FoundRecord, SourceAnswer, SourceLink
from sift.kernel.records_registry import (
    CONSTANT,
    LIST_KINDS,
    SPELLED,
    Field,
    FieldError,
    Group,
    Kind,
    Shown,
    Subject,
    every_field,
    field,
    fields_filled,
    fields_of,
    register_field,
    said_plainly,
    value_said,
)

__all__ = [
    "CONSTANT",
    "LIST_KINDS",
    "SPELLED",
    "Field",
    "FieldError",
    "FoundRecord",
    "Group",
    "Kind",
    "Shown",
    "SourceAnswer",
    "SourceLink",
    "Subject",
    "every_field",
    "field",
    "fields_filled",
    "fields_of",
    "register_field",
    "said_plainly",
    "value_said",
]

# The declarations, kept together because the record is read as a whole: its order and labels
# are decisions about the page, not about any one feature.


def _person() -> None:
    """Somebody in the library."""
    _person_named()
    _person_tagged()
    _person_described()
    _person_looks()


def _person_named() -> None:
    # Behind the switch: it is the page's heading. Declared because editing it comes from here.
    register_field(
        key="name",
        subject=Subject.PERSON,
        label="Name",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
    )
    register_field(
        key="aliases",
        subject=Subject.PERSON,
        label="Aliases",
        kind=Kind.NAMES,
        editable=True,
        suggests="people",
        entry="another name",
        help="Other names this person goes by. Searching for any of them finds this person.",
    )
    # `notes` in the database, "Details" on every screen.
    register_field(
        key="details",
        subject=Subject.PERSON,
        label="Details",
        kind=Kind.PARAGRAPH,
        shown=Shown.MORE,
        editable=True,
    )
    register_field(
        key="links",
        subject=Subject.PERSON,
        label="Links",
        kind=Kind.LINKS,
        editable=True,
        entry="https://\u2026",
        help="Where they can be found.",
    )
    # Where they post, apart from where they are written about; attached on its own page, not
    # typed here, though an import does fill it.
    register_field(
        key="accounts",
        subject=Subject.PERSON,
        label="Usernames",
        kind=Kind.ACCOUNTS,
        editable=False,
        imported=True,
        help="Where they post, one per Site. Everything filed under a username counts under them.",
    )


def _person_tagged() -> None:
    # Behind the switch: the chips are already drawn above the record, beside the opinions.
    register_field(
        key="tags",
        subject=Subject.PERSON,
        label="Tags",
        kind=Kind.TAGS,
        shown=Shown.MORE,
        editable=True,
        # Faceted on the ID, as two tags can be spelled the same.
        faceted=True,
    )
    # Whether they make the edits: set here, read as the mark beside the name. Never imported,
    # as it is decided by which box answered, not by the answer.
    register_field(
        key="pmv_creator",
        subject=Subject.PERSON,
        label="Is PMV creator",
        kind=Kind.FLAG,
        shown=Shown.MORE,
        editable=True,
        imported=False,
        faceted=True,
        help=(
            "Sift ticks this for you when a stash-box that lists creators as people knows this "
            "person. You can also tick it yourself."
        ),
    )


def _person_described() -> None:
    # Facts beyond a name, cover and notes; the four on the record are the ones scanned for.
    register_field(
        key="birth_date",
        subject=Subject.PERSON,
        label="Birthdate",
        kind=Kind.DATE,
        editable=True,
    )
    register_field(
        key="country",
        subject=Subject.PERSON,
        label="Nationality",
        kind=Kind.COUNTRY,
        editable=True,
        help="Sift stores it as the two-letter country code.",
        faceted=True,
    )
    register_field(
        key="hair_color",
        subject=Subject.PERSON,
        label="Hair color",
        kind=Kind.WORD,
        editable=True,
        faceted=True,
    )
    # Computed from the birthdate and never stored: a written age is wrong within a year.
    register_field(
        key="age",
        subject=Subject.PERSON,
        label="Age",
        kind=Kind.COUNT,
        # On the record, as its input, the birthdate, is.
        help="Sift works this out from the birthdate each time, so it's never out of date.",
        # Faceted in five-year bands; the birthdate itself would be the wall again.
        faceted=True,
    )
    register_field(
        key="measurements",
        subject=Subject.PERSON,
        label="Measurements",
        kind=Kind.TEXT,
        editable=True,
        help="Bust, waist and hips on one line, for example 34B-27-37.",
    )
    register_field(
        key="disambiguation",
        subject=Subject.PERSON,
        label="Disambiguation",
        kind=Kind.TEXT,
        shown=Shown.EVERY,
        editable=True,
        help="What tells them apart from somebody else of the same name.",
    )
    register_field(
        key="gender",
        subject=Subject.PERSON,
        label="Gender",
        kind=Kind.WORD,
        shown=Shown.EVERY,
        editable=True,
        faceted=True,
    )


def _person_looks() -> None:
    register_field(
        key="ethnicity",
        subject=Subject.PERSON,
        label="Ethnicity",
        kind=Kind.WORD,
        shown=Shown.EVERY,
        editable=True,
        faceted=True,
    )
    register_field(
        key="eye_color",
        subject=Subject.PERSON,
        label="Eye color",
        kind=Kind.WORD,
        shown=Shown.EVERY,
        editable=True,
        faceted=True,
    )
    register_field(
        key="height_cm",
        subject=Subject.PERSON,
        label="Height",
        kind=Kind.LENGTH,
        shown=Shown.EVERY,
        editable=True,
        # In ten-centimetre bands; see `HEIGHT_BAND`.
        faceted=True,
    )
    register_field(
        key="breast_type",
        subject=Subject.PERSON,
        label="Breast type",
        kind=Kind.WORD,
        shown=Shown.EVERY,
        editable=True,
        faceted=True,
    )
    register_field(
        key="career_start_year",
        subject=Subject.PERSON,
        label="Career start",
        kind=Kind.YEAR,
        shown=Shown.EVERY,
        editable=True,
        # The start is a facet; nearly every career end is blank.
        faceted=True,
    )
    register_field(
        key="career_end_year",
        subject=Subject.PERSON,
        label="Career end",
        kind=Kind.YEAR,
        shown=Shown.EVERY,
        editable=True,
    )
    register_field(
        key="tattoos",
        subject=Subject.PERSON,
        label="Tattoos",
        kind=Kind.NAMES,
        shown=Shown.EVERY,
        editable=True,
        entry="a tattoo",
        help="One per line, in your own words.",
    )
    register_field(
        key="piercings",
        subject=Subject.PERSON,
        label="Piercings",
        kind=Kind.NAMES,
        shown=Shown.EVERY,
        editable=True,
        entry="a piercing",
        help="One per line, in your own words.",
    )


def _site() -> None:
    """A site; without asking, the record shows where it can be found."""
    register_field(
        key="name",
        subject=Subject.SITE,
        label="Name",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
    )
    register_field(
        key="links",
        subject=Subject.SITE,
        label="Links",
        kind=Kind.LINKS,
        editable=True,
        entry="https://\u2026",
        help="Where the Site is on the web.",
    )
    register_field(
        key="details",
        subject=Subject.SITE,
        label="Details",
        kind=Kind.PARAGRAPH,
        shown=Shown.MORE,
        editable=True,
    )
    register_field(
        key="tags",
        subject=Subject.SITE,
        label="Tags",
        kind=Kind.TAGS,
        shown=Shown.MORE,
        editable=True,
        faceted=True,
    )
    # No `kind` field: a free word no importer writes filters nothing (see `schema.py`).
    register_field(
        key="aliases",
        subject=Subject.SITE,
        label="Aliases",
        kind=Kind.NAMES,
        editable=True,
        entry="another name",
        # Completed from existing sites, so a duplicate shows while it is typed.
        suggests="sites",
        help="Other names the Site goes by. Searching for any of them finds this Site.",
    )
    # A reference to another site, created when the typed name names none, so it can group.
    register_field(
        key="parent",
        subject=Subject.SITE,
        label="Part of",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
        # Completed from the sites that exist; a near-miss spelling groups nothing.
        suggests="sites",
        # Drawn as a way into that site; its id arrives as `parent_id`.
        links_to="site",
        help="The network or studio this one belongs to.",
        faceted=True,
    )


def _tag() -> None:
    """A tag; the fields below are what every stash-box carries and worth typing by hand."""
    # Behind the switch, and the one name never imported: another spelling is an alias.
    register_field(
        key="name",
        subject=Subject.TAG,
        label="Name",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
        imported=False,
    )
    register_field(
        key="aliases",
        subject=Subject.TAG,
        label="Aliases",
        kind=Kind.NAMES,
        editable=True,
        suggests="tags",
        entry="another word",
        help="Other words for the same thing. Searching for any of them finds this tag.",
    )
    # The tag this one is filed under, like a site's "Part of"; one parent, never imported.
    register_field(
        key="parent",
        subject=Subject.TAG,
        label="Part of",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
        imported=False,
        suggests="tags",
        links_to="tag",
        help="The broader tag this one is filed under. Filtering by that tag finds this one too.",
        faceted=True,
    )
    # ONE word: a stash-box carries a single category for a tag.
    register_field(
        key="category",
        subject=Subject.TAG,
        label="Category",
        kind=Kind.WORD,
        editable=True,
        faceted=True,
    )
    register_field(
        key="description",
        subject=Subject.TAG,
        label="Description",
        kind=Kind.PARAGRAPH,
        shown=Shown.MORE,
        editable=True,
        help="What this tag means, so it's used the same way every time.",
    )


def _photo_set() -> None:
    """A shoot: the pictures that arrived together."""
    register_field(
        key="name",
        subject=Subject.PHOTO_SET,
        label="Name",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
    )
    register_field(
        key="details",
        subject=Subject.PHOTO_SET,
        label="Details",
        kind=Kind.PARAGRAPH,
        shown=Shown.MORE,
        editable=True,
    )
    register_field(
        key="origin_url",
        subject=Subject.PHOTO_SET,
        label="Downloaded from",
        kind=Kind.LINKS,
        help="The page Sift downloaded it from. Blank when Sift found it in a folder.",
    )


def _song() -> None:
    """A piece of music; its AcoustID recording is its identity, not a typed field."""
    register_field(
        key="name",
        subject=Subject.SONG,
        label="Name",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
    )
    register_field(
        key="details",
        subject=Subject.SONG,
        label="Details",
        kind=Kind.PARAGRAPH,
        shown=Shown.MORE,
        editable=True,
    )
    # Its artists in order, written by the song's own route; never imported.
    register_field(
        key="artists",
        subject=Subject.SONG,
        label="Artists",
        kind=Kind.NAMES,
        shown=Shown.MORE,
        editable=True,
        imported=False,
        entry="an artist",
        ordered=True,
        faceted=True,
    )


def _asset() -> None:
    """A file: what it is, then what it measures, which is never editable."""
    _asset_written()
    _asset_named()
    _asset_measured()
    _asset_drawn_above()
    _asset_bit_depth()


def _asset_written() -> None:
    register_field(
        key="title",
        subject=Subject.ASSET,
        label="Title",
        kind=Kind.TEXT,
        editable=True,
        help="A name for the file that is not its filename.",
    )
    register_field(
        key="download_url",
        subject=Subject.ASSET,
        label="Downloaded from",
        kind=Kind.LINK,
        editable=True,
        # Not imported: where Sift fetched this copy from, not a fact about the release.
        imported=False,
        help="Where Sift downloaded this from. Blank when Sift didn't download it.",
    )
    register_field(
        key="release_date",
        subject=Subject.ASSET,
        label="Released",
        kind=Kind.DATE,
        editable=True,
        help="When this was released, which isn't when Sift imported it.",
        # Faceted as the year.
        faceted=True,
    )
    register_field(
        key="details",
        subject=Subject.ASSET,
        label="Details",
        kind=Kind.PARAGRAPH,
        editable=True,
        help="What this is about, in the words of whoever released it.",
    )
    # Where a stash-box says this can be found, apart from where Sift fetched the copy.
    register_field(
        key="links",
        subject=Subject.ASSET,
        label="Links",
        kind=Kind.LINKS,
        editable=True,
        entry="https://\u2026",
        help="Where this release can be found.",
    )
    register_field(
        key="production_date",
        subject=Subject.ASSET,
        label="Shot on",
        kind=Kind.DATE,
        shown=Shown.EVERY,
        editable=True,
        help="When it was filmed, which is usually before it was released.",
        # Not faceted: blank on nearly every row, so it would filter nothing.
        faceted=False,
    )
    # The release's reference on its Site, what a stash-box is searched by; TEXT, not WORD.
    register_field(
        key="site_code",
        subject=Subject.ASSET,
        label="Site code",
        kind=Kind.TEXT,
        shown=Shown.EVERY,
        editable=True,
        help="The reference the Site that released it uses for it.",
    )


def _asset_named() -> None:
    # The track a file is set to, on the record proper; one line, as sites write it differently.
    register_field(
        key="music",
        subject=Subject.ASSET,
        label="Music",
        kind=Kind.TEXT,
        editable=True,
        help="The track this is set to, as the Site it came from wrote it.",
    )
    # The one field whose save touches the disk, through the rename route and its refusals.
    register_field(
        key="filename",
        subject=Subject.ASSET,
        label="Filename",
        kind=Kind.FILENAME,
        editable=True,
        imported=False,
        help="What it is called on disk. Changing this renames the file.",
    )


def _asset_measured() -> None:
    # The measured half from here, each with `group=Group.MEDIA`; a gate holds them to it.
    register_field(
        key="where",
        subject=Subject.ASSET,
        label="File location",
        kind=Kind.PATH,
        group=Group.MEDIA,
    )
    register_field(
        key="added_at",
        subject=Subject.ASSET,
        label="Added",
        kind=Kind.TIMESTAMP,
        group=Group.MEDIA,
    )
    register_field(
        key="duration_ms",
        subject=Subject.ASSET,
        label="Length",
        kind=Kind.DURATION,
        group=Group.MEDIA,
    )
    register_field(
        key="dimensions",
        subject=Subject.ASSET,
        label="Dimensions",
        kind=Kind.DIMENSIONS,
        group=Group.MEDIA,
    )
    register_field(
        key="size_bytes",
        subject=Subject.ASSET,
        label="Size",
        kind=Kind.BYTES,
        group=Group.MEDIA,
    )
    register_field(
        key="container",
        subject=Subject.ASSET,
        label="Container",
        kind=Kind.WORD,
        group=Group.MEDIA,
    )
    register_field(
        key="vcodec",
        subject=Subject.ASSET,
        label="Video codec",
        kind=Kind.CODEC,
        group=Group.MEDIA,
    )
    register_field(
        key="acodec",
        subject=Subject.ASSET,
        label="Audio codec",
        kind=Kind.CODEC,
        group=Group.MEDIA,
    )
    register_field(
        key="fps",
        subject=Subject.ASSET,
        label="Frame rate",
        kind=Kind.RATE,
        group=Group.MEDIA,
    )


def _asset_drawn_above() -> None:
    # Three a stash-box knows that are drawn above the record (`Shown.ELSEWHERE`).
    register_field(
        key="people",
        subject=Subject.ASSET,
        label="Who is in this",
        kind=Kind.NAMES,
        shown=Shown.ELSEWHERE,
        imported=True,
        help="Who a stash-box says appears in this file.",
    )
    register_field(
        key="tags",
        subject=Subject.ASSET,
        label="Tags",
        kind=Kind.NAMES,
        shown=Shown.ELSEWHERE,
        imported=True,
        help="What a stash-box says this file is.",
    )
    register_field(
        key="site",
        subject=Subject.ASSET,
        label="Site",
        kind=Kind.TEXT,
        shown=Shown.ELSEWHERE,
        imported=True,
        help="The Site a stash-box says released this.",
    )
    register_field(
        key="accounts",
        subject=Subject.ASSET,
        label="Usernames",
        kind=Kind.ACCOUNTS,
        shown=Shown.ELSEWHERE,
        imported=True,
        help="Who a stash-box says posted this, as a username on a Site.",
    )
    # Who made this, so the plan, which walks this registry, carries it to the writer.
    register_field(
        key="creator",
        subject=Subject.ASSET,
        label="Who made it",
        kind=Kind.TEXT,
        shown=Shown.ELSEWHERE,
        imported=True,
        help="Who a stash-box says made this, where the box files its creators as people.",
    )


def _asset_bit_depth() -> None:
    register_field(
        key="bit_depth",
        subject=Subject.ASSET,
        label="Bit depth",
        kind=Kind.DEPTH,
        shown=Shown.MORE,
        group=Group.MEDIA,
    )


def _account() -> None:
    """One username on one site: only what is true of the identity, never of the human."""
    register_field(
        key="display_name",
        subject=Subject.USERNAME,
        label="Shown as",
        kind=Kind.TEXT,
        editable=True,
        help="The name the Site shows beside the username, when it is not the username itself.",
    )
    register_field(
        key="links",
        subject=Subject.USERNAME,
        label="Links",
        kind=Kind.LINKS,
        editable=True,
        entry="https://\u2026",
        help="This username's own page on the Site it is on.",
    )
    # The number the site knows this username by, so files named only by it can be filed.
    register_field(
        key="number",
        subject=Subject.USERNAME,
        label="Number on the Site",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
        help=(
            "The Site's own permanent number for this username. It survives a rename, and files "
            "named with it are filed here."
        ),
    )


def _sources() -> None:
    """The stash-boxes agreed to know a subject, declared once for every linkable subject."""
    for subject in (Subject.PERSON, Subject.SITE, Subject.TAG):
        register_field(
            key="sources",
            subject=subject,
            label="Stash-boxes",
            kind=Kind.SOURCES,
            # Behind the switch: provenance rather than a fact about the thing.
            shown=Shown.EVERY,
            help="Where the rest of this came from, and when it was last asked.",
        )


_person()

_site()

_tag()

_account()

_sources()

_photo_set()

_song()

_asset()
