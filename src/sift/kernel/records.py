# SPDX-License-Identifier: AGPL-3.0-or-later
"""The field registry: what a person, a site, a tag or a file is made of, and how to draw one.

An entity page is more than a name with a picture beside it. A person carries a set of facts
somebody either typed or agreed to, a file carries what it is as well as how large it is, and three
different screens have to render the same field without disagreeing about what it is called or what
it is measured in: the record on the page, the table behind "show everything", and the screen that
reconciles two answers about one field.

So a field is declared once, here, and the screens are generated from the declaration. This is the
same arrangement `settings_registry` uses and it exists for the same reason: the alternative is a
hand-written list beside the thing it describes, which drifts the first time a field is added and
only one of the two is edited.

Nothing here reads or writes a value. This says what a field IS: its name in plain language, its
type, whether it belongs on the record by default. Where the value comes from is the business of
whichever feature owns the row.

**A field nothing can ever fill does not belong here.** A label with permanently nothing beside it
reads as a fault rather than as an absence, so a field is declared when something can supply it.
"""

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

# ---------------------------------------------------------------------------------------------
# The declarations.
#
# They are here, in the kernel, rather than spread across the features that own the rows, and
# that is a departure from how settings are declared, so it is worth saying why. A setting belongs
# to the feature that acts on it and is meaningless without it. A field belongs to a RECORD, and
# the record is read as a whole: the order of the rows, the words chosen for two labels next to
# each other, and which of them are worth showing without asking are decisions about the page, not
# about any one feature. Split across seven files those decisions get made seven times.
#
# Only fields something can actually fill are here. The rest arrive with whatever will fill them.
# ---------------------------------------------------------------------------------------------


def _person() -> None:
    """Somebody in the library."""
    _person_named()
    _person_tagged()
    _person_described()
    _person_looks()


def _person_named() -> None:
    # Behind the switch, not on the record: it is the heading of the page the record is on, and a
    # row repeating it says nothing. Still declared, because editing it comes from this list.
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
    # `notes` in the database, "Details" on every screen: one word for this across people, sites
    # and photo sets. The column keeps its name because renaming a column is a migration and this
    # is a label; what matters is that no screen says "Notes".
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
    # Where they POST, as opposed to where they are written about. The two are different answers and
    # a stash-box gives both in one list: a page on a site somebody publishes to is a Username under
    # a Site, and a page in a reference database is a link. Splitting them is what turns a row of
    # addresses into rows that a library can be searched and filed by.
    #
    # Not editable, and imported: a Username is a row with a Site, a spelling, files under it and
    # a page of its own, so it is attached and detached from that page rather than typed into a box
    # on this one. An import genuinely does fill it in, which is the whole reason the registry
    # asks those two questions separately.
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
        # Faceted on their ID and not their name: two tags can be spelled the same, and a
        # filtering written against a word would find both of them.
        faceted=True,
    )
    # Whether they MAKE the edits, as against appearing in them. A creator wears a mark beside
    # their name, on their card and on a preview of them.
    #
    # In the panel rather than on the record, and that is the one decision here worth writing down.
    # A row reading "Is PMV creator: No" under every one of several thousand performers is a line
    # that says nothing on nearly every page it is drawn on, and the record is meant to be readable
    # before anybody has filled anything in. The mark beside the name is where the answer is READ;
    # this row is where it is SET, which is a different job and one step further in. Not behind the
    # stash-box switch, which is for fields nobody types: this one is typed, by hand, on purpose.
    #
    # `imported=False` against `editable=True`, which is the second field in Sift to need the two
    # to differ. No stash-box sends this: it is decided from WHICH box answered, not from anything
    # in the answer, so a rule offering to import it would be a rule that can never fire and a row
    # on the reconcile screen that is permanently blank.
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
    # Everything below is a fact about a person Sift can be told beyond a name, a cover and
    # some notes.
    #
    # Which of them sit on the record and which sit behind the switch was settled field by field
    # rather than by a rule: the four on the record are the ones somebody scans a page for, and the
    # rest are the ones they go looking for. All of them are editable by hand: a record is
    # somebody's own, and a field that can only be filled by an import is a field that is wrong
    # until an import happens to run.
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
    # Computed, never stored. There is no `age` column and there must not be one: an age written
    # down is wrong within a year of being written, and the birthdate it comes from is already
    # here. The read works it out on the way past; nothing accepts a write to it.
    register_field(
        key="age",
        subject=Subject.PERSON,
        label="Age",
        kind=Kind.COUNT,
        # On the record without asking, unlike the stash-box-only fields beside it. It is
        # arithmetic on the BIRTHDATE, which is itself on the default record, so putting the
        # answer behind a switch while its input was in plain view would make the one fact
        # somebody actually reads the one they had to go and turn on.
        help="Sift works this out from the birthdate each time, so it's never out of date.",
        # Faceted in five-year bands, worked out from the birthdate in SQL. The BIRTHDATE is
        # not a facet: every row has its own, so a column of them is the wall again.
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
        # The start is a facet and the END is not, deliberately: a library sorts people by
        # when they began, and nearly every career_end_year in it is blank.
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
    """A site.

    The same default view a person has, and for the same reasons: the name is the heading of the
    page, the tags are already chips above the record, and the details are a paragraph rather than
    a fact you read at a glance. What is left without asking is where the site can be found, which
    is the one thing somebody opens a site's page wanting.
    """
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
    # No `kind` field: a free word ("studio", "tube") that no importer writes filters nothing as a
    # facet. The COLUMN is still in `sites` and is simply not read; see the note beside it in
    # `schema.py`.
    register_field(
        key="aliases",
        subject=Subject.SITE,
        label="Aliases",
        kind=Kind.NAMES,
        editable=True,
        entry="another name",
        # Completed from the sites already here, so typing a name that IS one is visible while it
        # is being typed rather than discovered as a duplicate later.
        suggests="sites",
        help="Other names the Site goes by. Searching for any of them finds this Site.",
    )
    # A REFERENCE to another site, resolved by the name somebody types and created when that name
    # names nothing yet. `sites.parent_id`, with `ON DELETE SET NULL`: deleting a network does
    # not delete what was published under it.
    #
    # `set_site_record` resolves the typed name against the sites and creates one when it matches
    # none. Inventing the site is the point: the wall groups by it and a network's own page lists by
    # it, so a parent has to BE a row before either can happen. A name that names nothing is worse
    # than a site with nothing under it yet, because nothing can ever be put under a name.
    register_field(
        key="parent",
        subject=Subject.SITE,
        label="Part of",
        kind=Kind.TEXT,
        shown=Shown.MORE,
        editable=True,
        # Completed from the sites that exist: a network is nearly always one of them, and a typed
        # spelling that is one letter off is a network nothing groups by.
        suggests="sites",
        # ...and the value is a site, so the record draws it as a way IN to that site rather than as
        # a word. The id arrives beside the name as `parent_id`; see `site_record`.
        links_to="site",
        help="The network or studio this one belongs to.",
        faceted=True,
    )


def _tag() -> None:
    """A tag.

    Beyond its name, the three below are what every stash-box carries for a tag, and they are also
    worth typing by hand: a tag whose meaning is written down once is a tag two people use the same
    way.
    """
    # Behind the switch, like a person's and a site's: it is the heading of the page the record is
    # on, and a row repeating it says nothing. Still declared, because editing it comes from here.
    #
    # NOT imported, and it is the only name on any subject that is not. A tag's name is the word
    # itself: every file that carries it carries a pointer to this row, and a stash-box spelling it
    # differently is not a correction: it is another word for the same thing, which is what the
    # aliases below are. A person's name is a different case and is imported, because a person is
    # somebody who has a name rather than a row that IS one.
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
    # A REFERENCE to the tag this one is filed under, the same shape as a site's "Part of": typed
    # as a name, completed from the tags, created when it names none yet, and carried with its id
    # so the record opens it. One parent at most, so the tags form a tree a wall can draw. Not
    # imported: a stash-box carries a category for a tag, never a parent.
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
    """A piece of music: what it is called and what somebody wrote about it. Which AcoustID
    recording it is, where AcoustID said, is the song's identity rather than a field anybody
    types, and is shown on its page as where its name came from."""
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
    # The artists it credits, in order: AcoustID's, or typed in the song's form. Editable there,
    # and written by the song's own `PUT /songs/{id}/artists` rather than a record write, as its
    # name and details are by their own routes: each name is an artist of its own (a name finds
    # its artist or makes one). Never imported: no stash-box knows a song.
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
    """A file.

    The first five are what the file IS. The rest are what it MEASURES, which the player's
    stats overlay already draws, declared here so both surfaces take the same word and the same
    unit from one place rather than each holding its own idea of what "bit depth" is called.

    None of the measured ones is editable. A size on disk is not an opinion, and a box around it
    invites an edit that cannot be honoured.
    """
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
        # NOT imported. This is where SIFT fetched the file from: a fact about this copy of it,
        # not about the release. A stash-box's addresses for the same scene are a different claim
        # and belong nowhere near a column that answers "where did this come from".
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
        # Faceted as the YEAR. A date to the day is a value one file has.
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
    # Where a stash-box says this can be found. NOT the same column as `download_url`, which is
    # where SIFT fetched this copy from: a fact about the copy on the disk rather than about the
    # release. Declared so a stash-box's addresses have somewhere to go rather than being read off
    # the wire and thrown away.
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
        # NOT faceted, where the release date above it is: a shooting year is blank on nearly every
        # row a library holds, so the panel would offer one value, and a dimension with one value
        # filters nothing. The field is shown, editable and written by an import.
        faceted=False,
    )
    # The release's own reference on the Site that put it out: `SITE-1234`. Worth keeping
    # because it is what a person searches a stash-box BY when a name and a date are ambiguous.
    # TEXT, not WORD: a reference is not "one value out of a known set", and a WORD field's
    # upper-case value is said as a box's constant: `S03E03` would read "S03e03".
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
    # The track a file is set to.
    #
    # On the record proper rather than behind the switch, and that is the whole reason it is worth
    # having: for a lot of files the music IS what the file is, and a field somebody has to
    # turn a setting on to see is a field they will not know exists. A download from a site that
    # says which track it used fills this in; everywhere else it is typed or it is blank.
    #
    # One line of text, not an artist and a song in two boxes. It is written differently on every
    # site it comes from ("Artist - Title", "Title by Artist", a title alone) and splitting it
    # means guessing which half is which, then being wrong on a track whose name has a dash in it.
    # What is stored is what was written, which is also what somebody would search for.
    register_field(
        key="music",
        subject=Subject.ASSET,
        label="Music",
        kind=Kind.TEXT,
        editable=True,
        help="The track this is set to, as the Site it came from wrote it.",
    )
    # Editable, and it is the ONE field on any record whose save touches the disk.
    #
    # Read-only here beside a Rename button further down the same page, the record would show a
    # name it would not let you change while the way to change it was somewhere else: two
    # controls for one fact, and the one you are looking at the one that does not work.
    #
    # It is deliberately NOT part of the record's own write. Renaming moves a file and can be
    # refused for reasons no other field has (a name already taken, a character the filesystem
    # will not have), so it goes through the rename route, which is where those refusals are
    # written, and the record's save calls it when the box has changed. `imported=False`: a
    # stash-box's title is a title, and nothing a stash-box says renames a file on somebody's disk.
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
    # From here down is the MEASURED half: what the file is, rather than what anybody said about
    # it. Every one of them carries `group=Group.MEDIA`, which is what the record panel draws its
    # second pane from; a gate holds each of them to saying so rather than leaving it to be guessed
    # from `editable`.
    #
    # `original_filename` is not among them. The name the file ARRIVED under is not a property of
    # the file; it is something that happened to it on a day, and `Added to the library as <name>`
    # is that line in its History. A second name row that nothing can change, under the box that
    # renames the file on disk, would read as that box having failed. The COLUMN is what the Browse
    # wall sorts by and what the search index reads, and the field stays on the wire.
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
    # The three a stash-box knows about a file that are drawn ABOVE the record rather than in it.
    # See `Shown.ELSEWHERE`: they are declared so an import can write them and for no other reason,
    # and none of them is typed into the record form.
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
    # A fourth of the same kind: who MADE this, on a box that keeps its creators where another keeps
    # its studios. Declared for one reason and no other: so it reaches the writer.
    #
    # A plan is built by walking THIS registry, not the keys an answer happens to carry, and that is
    # deliberate: a key nothing declared is a translation that did not happen. Which makes the
    # registry the only honest way to hand the writer a fact the answer already states. The creator
    # also LEADS `people` above and is attributed from there; this says which of those names it is,
    # so the mark on their record is read off a word rather than off a position in a list.
    #
    # `shown=ELSEWHERE`, like the three above: nothing on a file's record draws it. What it writes
    # is a flag on a PERSON, and that is drawn beside their name where somebody would look for it.
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
    """One username on one site.

    Deliberately small, and the smallness is the design. Everything about the HUMAN lives on the
    person; what belongs here is only what is true of the identity: what the site shows beside
    the username, and where its page is. A field that appears on both would be two places to edit
    one fact, and the one somebody edited would be whichever screen they happened to be on.

    The username itself is not here. It is the row's name (what the header draws) in the same
    way a person's name and a site's name are not fields on their records.
    """
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
    # THE NUMBER THE SITE ITSELF KNOWS THIS USERNAME BY. On the record so it can be shown and typed:
    # files whose own names carry nothing but that number sit under no site until somebody tells the
    # library what it is called. It is a fact about the IDENTITY rather than about the human, which
    # is what the rest of this record is, and it is the one field here that a pass also fills in on
    # its own.
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
    """The stash-boxes that have been agreed to know a subject.

    Declared here, in one loop, rather than written out three times inside the three functions
    above. It is the same field with the same label and the same reason on every subject that can
    be linked, and three copies of one declaration is three places to edit when the wording changes.

    Behind the switch on all of them. It is provenance (which stash-box said the rest of this, and
    when) and provenance is what somebody goes looking for rather than what they read at a glance.
    """
    for subject in (Subject.PERSON, Subject.SITE, Subject.TAG):
        register_field(
            key="sources",
            subject=subject,
            label="Stash-boxes",
            kind=Kind.SOURCES,
            # Behind the switch, with the rest of what a stash-box contributes. On a library where
            # nothing has been linked it is a dash, and it is provenance rather than a fact about
            # the thing, which is exactly what the switch is for.
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
