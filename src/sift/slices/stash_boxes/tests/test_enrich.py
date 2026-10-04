# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an import is allowed to do to a FILE, and what it must not do on its own.

This is the writer whose interesting fields are not columns. A stash-box that recognizes a video
answers with a title, a date, a Site, the people in it and what it is tagged as, and three of
those five are rows in tables belonging to two other areas. That is what makes it the writer that
can create things, and why creating is permission handed in rather than a decision taken here.

The two claims worth holding are the count and the join.

The count is what a confirm button shows before it is pressed. It is taken from what the writer
says it wrote rather than from what it was handed, because a count taken from the ask can only
ever agree with the ask.

The join: nothing reaches this writer on a title: a match is applied only once somebody has
agreed to a fingerprint, and the studio is the box's own statement of who released what it
recognized. So the file is filed under it, the filing carries a source, and the site is still only
ever created with permission.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from sift.kernel.enrichment import Missing
from sift.kernel.ledger import Actor
from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.enrich import (
    IMPORTED,
    AssetWriter,
    Invented,
    ToLink,
    linkable,
    record_who_invented,
)

pytestmark = pytest.mark.anyio


class _Asset:
    """One row of `assets`, stood in for. Every column a stash-box may fill in, and no other.

    The attribute names are the FIELD keys on purpose: that is what the real row does, and it is
    what lets the writer read six columns from one list instead of six readings that can each be
    forgotten separately.
    """

    def __init__(
        self,
        title: str | None = None,
        release_date: str | None = None,
        details: str | None = None,
        production_date: str | None = None,
        site_code: str | None = None,
        music: str | None = None,
    ) -> None:
        self.title = title
        self.release_date = release_date
        self.details = details
        self.production_date = production_date
        self.site_code = site_code
        self.music = music


class _Content:
    """The kernel's store, stood in for.

    It WRITES ONTO THE ROW rather than into a list beside it, which is the whole point of the
    double: a test that only watched the call go past would pass against a writer that set the
    wrong column. Reading back through `current` is what proves the value landed where the record
    reads it from.
    """

    def __init__(self, asset: _Asset | None = None) -> None:
        self.asset = asset
        self.links: list[str] = []

    async def get(self, asset_id: str) -> _Asset | None:
        _ = asset_id
        return self.asset

    def _set(self, key: str, value: str | None) -> None:
        if self.asset is None:
            self.asset = _Asset()
        setattr(self.asset, key, value)

    async def set_title(self, asset_id: str, title: str | None) -> None:
        _ = asset_id
        self._set("title", title)

    async def set_release_date(self, asset_id: str, date: str | None) -> None:
        _ = asset_id
        self._set("release_date", date)

    async def set_details(self, asset_id: str, details: str | None) -> None:
        _ = asset_id
        self._set("details", details)

    async def set_production_date(self, asset_id: str, date: str | None) -> None:
        _ = asset_id
        self._set("production_date", date)

    async def set_site_code(self, asset_id: str, code: str | None) -> None:
        _ = asset_id
        self._set("site_code", code)

    async def set_music(self, asset_id: str, music: str | None) -> None:
        _ = asset_id
        self._set("music", music)

    async def set_links(self, asset_id: str, urls: Sequence[str]) -> None:
        """The whole set, replaced, which is what the real one does, and why the plan merges
        first. A double that appended would hide a writer handing over the offer alone."""
        _ = asset_id
        self.links = list(urls)

    async def links_of(self, asset_id: str) -> list[str]:
        _ = asset_id
        return list(self.links)


class _Filing:
    def __init__(
        self,
        people: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
        site: str | None = None,
    ) -> None:
        self._people = people
        self._tags = tags
        self._site = site
        self.attributed: list[tuple[str, str]] = []
        self.tagged: list[tuple[str, str]] = []
        #: Every filing this writer made, as `(site, source)`.
        self.filed: list[tuple[str, str]] = []
        #: Every filing under a named username, as `(site, handle, url, source, person)`.
        self.usernames: list[tuple[str, str, str, str, str | None]] = []
        #: The box every filing named, in the order filed.
        self.boxes: list[str | None] = []

    async def people_on(self, asset_id: str) -> tuple[str, ...]:
        _ = asset_id
        return self._people

    async def tags_on(self, asset_id: str) -> tuple[str, ...]:
        _ = asset_id
        return self._tags

    async def site_of(self, asset_id: str) -> str | None:
        _ = asset_id
        return self._site

    async def attribute(
        self, asset_id: str, person_id: str, *, source: str, box_id: str | None = None
    ) -> None:
        _ = asset_id
        self.attributed.append((person_id, source))
        self.boxes.append(box_id)

    async def attach_tag(
        self, asset_id: str, tag_id: str, *, source: str, box_id: str | None = None
    ) -> None:
        _ = asset_id
        self.tagged.append((tag_id, source))
        self.boxes.append(box_id)

    async def file_under_site(
        self, asset_id: str, site: str, *, source: str, box_id: str | None = None
    ) -> None:
        _ = asset_id
        self.filed.append((site, source))
        self.boxes.append(box_id)
        self._site = site

    async def accounts_on(self, asset_id: str) -> tuple[dict[str, str], ...]:
        _ = asset_id
        return tuple(
            {"site": site, "handle": handle, "url": url}
            for site, handle, url, _source, _person in self.usernames
        )

    async def file_under_username(
        self,
        asset_id: str,
        *,
        site: str,
        handle: str,
        url: str | None,
        source: str,
        person_id: str | None = None,
        box_id: str | None = None,
    ) -> bool:
        _ = asset_id
        self.boxes.append(box_id)
        if any((site, handle) == (one[0], one[1]) for one in self.usernames):
            return False
        self.usernames.append((site, handle, url or "", source, person_id))
        return True


class _Naming:
    """Turns a name into a row. `known` is what this library already has."""

    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()
        self.made: list[str] = []
        #: Who was marked as making the edits, in the order the writer said so.
        self.marked: list[str] = []
        #: Which rows were recorded as INVENTED by a box, as (kind, id, box).
        self.made_by: list[tuple[str, str, str]] = []

    async def _named(self, name: str, *, creating: bool) -> str | None:
        if name in self.known:
            return f"id-{name}"
        if not creating:
            return None
        self.made.append(name)
        self.known.add(name)
        return f"id-{name}"

    async def person_named(self, name: str, *, creating: bool) -> str | None:
        return await self._named(name, creating=creating)

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None:
        return await self._named(name, creating=creating)

    async def tag_named(self, name: str, *, creating: bool) -> str | None:
        return await self._named(name, creating=creating)

    async def mark_pmv_creator(self, person_id: str) -> None:
        self.marked.append(person_id)

    async def mark_created_by_box(self, kind: str, local_id: str, source_id: str) -> None:
        self.made_by.append((kind, local_id, source_id))


class _Reindex:
    def __init__(self) -> None:
        self.told: list[str] = []

    async def touched(self, asset_id: str) -> None:
        self.told.append(asset_id)

    async def renamed(self) -> None:  # pragma: no cover - unused by this writer
        raise AssertionError


def _writer(
    *,
    content: _Content | None = None,
    filing: _Filing | None = None,
    naming: _Naming | None = None,
    reindex: _Reindex | None = None,
) -> tuple[AssetWriter, _Content, _Filing, _Naming, _Reindex]:
    content = content or _Content()
    filing = filing or _Filing()
    naming = naming or _Naming()
    reindex = reindex or _Reindex()
    writer = AssetWriter(
        content=content,  # type: ignore[arg-type]
        filing=filing,
        naming=naming,
        reindex=reindex,  # type: ignore[arg-type]
    )
    return writer, content, filing, naming, reindex


async def test_people_put_on_a_file_are_told_once_and_a_write_with_none_tells_nobody() -> None:
    """What lets the face in the file be asked about them: told once a write lands people, and
    only then (the wiring asks the faces' pass; this writer does not know faces exist)."""
    told: list[str] = []

    async def people_filed() -> None:
        told.append("people")

    writer = AssetWriter(
        content=_Content(),  # type: ignore[arg-type]
        filing=_Filing(),
        naming=_Naming({"Known"}),
        reindex=_Reindex(),  # type: ignore[arg-type]
        people_filed=people_filed,
    )

    await writer.write("a1", {"title": "A Clip"}, creating=False, actor=Actor.sift("stash"))
    await writer.write("a1", {"people": ["nobody-here"]}, creating=False, actor=Actor.sift("stash"))
    assert told == []

    await writer.write("a1", {"people": ["Known"]}, creating=False, actor=Actor.sift("stash"))
    assert told == ["people"]


# --- what Sift already holds ------------------------------------------------------------------


async def test_it_reads_the_fields_a_stash_box_could_also_have_an_opinion_on() -> None:
    writer, *_ = _writer(
        content=_Content(
            _Asset(
                title="A Clip",
                release_date="1991-02-02",
                details="What it is about.",
                production_date="1991-01-01",
                site_code="SITE-1234",
                music="Somebody - A Track",
            )
        ),
        filing=_Filing(people=("p1",), tags=("t1",), site="s1"),
    )

    held = await writer.current("a1")

    assert held == {
        "title": "A Clip",
        "release_date": "1991-02-02",
        "details": "What it is about.",
        "production_date": "1991-01-01",
        "site_code": "SITE-1234",
        "music": "Somebody - A Track",
        "links": [],
        "people": ["p1"],
        "tags": ["t1"],
        "site": "s1",
        "accounts": [],
    }


async def test_a_field_with_nothing_in_it_is_left_out_rather_than_carried_as_empty() -> None:
    """The plan reads an empty offer and an absent one the same way, and a blank held value has to
    read as "nobody has filled this in" rather than as a value to be compared against."""
    writer, *_ = _writer(content=_Content(_Asset()))

    held = await writer.current("a1")

    assert held == {"links": [], "people": [], "tags": [], "accounts": []}


async def test_a_file_sift_does_not_know_reads_as_nothing_rather_than_failing() -> None:
    """A decision can be applied after the file it names has gone."""
    writer, *_ = _writer(content=_Content(None))

    assert await writer.current("a1") == {
        "links": [],
        "people": [],
        "tags": [],
        "accounts": [],
    }


# --- what it would have to invent ---------------------------------------------------------------


async def test_it_counts_the_names_this_library_does_not_have() -> None:
    """What the confirm button shows before it is pressed. Somebody applying a page of matches has
    to see that it would also invent eleven people BEFORE they press it."""
    writer, *_ = _writer(naming=_Naming({"Known"}))

    unknown = await writer.missing(
        {"people": ["Known", "New Person"], "tags": ["New Tag"], "site": "New Site"}
    )

    assert unknown == (
        Missing(name="New Person", kind="person"),
        Missing(name="New Tag", kind="tag"),
        Missing(name="New Site", kind="site"),
    )


async def test_counting_invents_nothing() -> None:
    """The count is a question, not a decision. A screen that made eleven people while telling you
    it would is a screen that has already done it."""
    writer, _, _, naming, _ = _writer(naming=_Naming())

    await writer.missing({"people": ["Somebody"], "site": "A Site"})

    assert naming.made == []


async def test_a_name_this_library_already_has_is_not_counted_as_one_to_invent() -> None:
    """The count is what the confirm button shows. A name that already exists in it would say the
    press does more than it does, which is the one thing that number must never do."""
    writer, *_ = _writer(naming=_Naming({"Jane", "beach", "A Site"}))

    unknown = await writer.missing({"people": ["Jane"], "tags": ["beach"], "site": "A Site"})

    assert unknown == ()


async def test_a_row_that_would_be_invented_twice_is_counted_once() -> None:
    """The SAME row twice, which is one row. Twenty files from one release name one person."""
    writer, *_ = _writer(naming=_Naming())

    assert await writer.missing({"people": ["Jane", "Jane"]}) == (
        Missing(name="Jane", kind="person"),
    )


async def test_one_word_naming_two_kinds_of_row_is_two_answers() -> None:
    """A person called Jane and a tag called Jane are two rows, and one tick cannot mean both.

    Folded into a single word without the kind beside the name, ticking it would create whichever
    of the two the writer happened to reach, and there would be nothing on the screen to say
    which had been agreed to.
    """
    writer, *_ = _writer(naming=_Naming())

    assert await writer.missing({"people": ["Jane"], "tags": ["Jane"]}) == (
        Missing(name="Jane", kind="person"),
        Missing(name="Jane", kind="tag"),
    )


async def test_nothing_is_counted_from_a_field_that_is_not_a_list() -> None:
    """An adapter answering with a bare string is not eleven people."""
    writer, *_ = _writer(naming=_Naming())

    assert await writer.missing({"people": "Jane", "site": "   "}) == ()


# --- what it writes -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "offered", "landed"),
    [
        ("title", " A Clip ", "A Clip"),
        ("release_date", "1991-02-02", "1991-02-02"),
        ("details", "  What it is about.  ", "What it is about."),
        ("production_date", "1991-01-01", "1991-01-01"),
        ("site_code", "SITE-1234", "SITE-1234"),
        ("music", "Somebody - A Track", "Somebody - A Track"),
    ],
)
async def test_every_column_a_stash_box_may_fill_in_lands_where_the_record_reads_it(
    key: str, offered: str, landed: str
) -> None:
    """One case per declared column, and read back through `current` rather than watched going past.

    A field can be declared importable, carried by the mapper and offered by every plan, and still
    have no setter in the writer: counted as written and dropped. A test that asserted the CALL
    had happened would be just as green, so each column is read back.
    """
    writer, _, _, _, _ = _writer()

    written = await writer.write("a1", {key: offered}, creating=False, actor=Actor.sift("stash"))

    assert written == {key: 1}
    assert (await writer.current("a1"))[key] == landed


async def test_the_addresses_a_release_can_be_found_at_land_as_the_whole_list() -> None:
    """`links` is a list field, and the plan has already merged what is held with what arrived,
    so what reaches the writer IS the whole list and the setter that replaces is the right one."""
    writer, content, _, _, _ = _writer()
    content.links = ["https://example.test/one"]

    written = await writer.write(
        "a1",
        {"links": ["https://example.test/one", "https://example.test/two"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    # Two of them, counted, so the History line can say "2 links" rather than "links".
    assert written == {"links": 2}
    assert await writer.current("a1") == {
        "links": ["https://example.test/one", "https://example.test/two"],
        "people": [],
        "tags": [],
        "accounts": [],
    }


async def test_a_field_the_plan_did_not_decide_is_not_written() -> None:
    """The plan is the offer; this writes what it decided and nothing else. A writer that wrote
    every key it knows about would blank the fields the plan left alone."""
    writer, content, _, _, _ = _writer(content=_Content(_Asset(release_date="1991-02-02")))

    await writer.write("a1", {"title": "A Clip"}, creating=False, actor=Actor.sift("stash"))

    assert content.asset is not None
    assert content.asset.release_date == "1991-02-02"


async def test_it_answers_with_the_fields_it_wrote_and_not_the_ones_it_was_handed() -> None:
    """What the confirm toast counts. Counting the ASK would give a number that agrees with itself
    whatever the writer does, while fields were dropped underneath it."""
    writer, _, filing, _, _ = _writer(naming=_Naming())

    written = await writer.write(
        "a1",
        {"title": "A Clip", "people": ["Orla Tennant"], "site": "A Site"},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert written == {"title": 1}
    assert (filing.attributed, filing.filed) == ([], [])


async def test_people_and_tags_are_marked_as_the_stash_boxes_doing() -> None:
    """How a tag a stash-box applied is told from one somebody chose: counted, filtered, and
    taken back off without touching the other kind."""
    writer, _, filing, _, _ = _writer(naming=_Naming({"Jane", "beach"}))

    await writer.write(
        "a1", {"people": ["Jane"], "tags": ["beach"]}, creating=False, actor=Actor.sift("stash")
    )

    assert filing.attributed == [("id-Jane", IMPORTED)]
    assert filing.tagged == [("id-beach", IMPORTED)]


async def test_every_row_a_box_files_names_the_box_that_answered() -> None:
    """Which box filed a person, a tag, a Site and a username is written on the row, so a line
    about it says the box's name. A write no box answered names none."""
    writer, _, filing, _, _ = _writer(naming=_Naming({"Jane", "beach", "A Site", "Clips"}))
    answer = {
        "people": ["Jane"],
        "tags": ["beach"],
        "site": "A Site",
        "accounts": [{"site": "Clips", "handle": "jane", "url": ""}],
    }

    await writer.write("a1", answer, creating=False, actor=Actor.box("box-fansdb"))
    assert filing.boxes == ["box-fansdb"] * 4

    filing.boxes.clear()
    await writer.write("a2", answer, creating=False, actor=Actor.sift("stash"))
    assert filing.boxes and set(filing.boxes) == {None}


async def test_a_name_this_library_does_not_have_is_skipped_when_creating_was_not_asked_for() -> (
    None
):
    """The refusal that makes the tick on the confirm screen mean something."""
    writer, _, filing, naming, _ = _writer(naming=_Naming())

    await writer.write(
        "a1", {"people": ["Jane"], "tags": ["beach"]}, creating=False, actor=Actor.sift("stash")
    )

    assert (filing.attributed, filing.tagged, naming.made) == ([], [], [])


async def test_creating_makes_the_rows_and_then_files_the_file_under_them() -> None:
    writer, _, filing, naming, _ = _writer(naming=_Naming())

    await writer.write(
        "a1", {"people": ["Jane"], "tags": ["beach"]}, creating=True, actor=Actor.sift("stash")
    )

    assert naming.made == ["Jane", "beach"]
    assert filing.attributed == [("id-Jane", IMPORTED)]
    assert filing.tagged == [("id-beach", IMPORTED)]


async def test_a_site_is_made_and_the_file_is_filed_under_it() -> None:
    """A confirmed answer files the file under the site it names, and marks the filing.

    This writer is only reached once somebody has agreed to a fingerprint, and the studio is the
    box's own statement of who released what it just recognized. The filing carries its source so
    it can be told apart from one made by hand.
    """
    writer, _, filing, naming, _ = _writer(naming=_Naming())

    await writer.write("a1", {"site": "A Site"}, creating=True, actor=Actor.sift("stash"))

    assert naming.made == ["A Site"]
    assert filing.filed == [("A Site", IMPORTED)]


async def test_a_site_this_run_may_not_invent_is_not_filed_under_either() -> None:
    """The filing follows the permission rather than going around it. A run that may create nothing
    would otherwise make a username under a site it was refused permission to make."""
    writer, _, filing, naming, _ = _writer(naming=_Naming())

    written = await writer.write(
        "a1", {"site": "A Site"}, creating=False, actor=Actor.sift("stash")
    )

    assert (naming.made, filing.filed, written) == ([], [], {})


async def test_the_filing_is_read_back_as_what_the_file_is_now_under() -> None:
    """What makes applying the same answer twice a KEEP rather than a second filing: the plan
    compares what `current` says the file is filed under against what the box offers."""
    writer, _, filing, _, _ = _writer(naming=_Naming({"A Site"}))

    await writer.write("a1", {"site": "A Site"}, creating=False, actor=Actor.sift("stash"))

    assert (await writer.current("a1"))["site"] == "A Site"
    assert filing.filed == [("A Site", IMPORTED)]


async def test_the_word_index_is_told_once_after_the_writes() -> None:
    """The index reads the title, the people and the tags, so telling it three times would rebuild
    one file's row three times for one decision. Telling it at all is load-bearing: an imported
    title is meant to be searchable, and the index is kept current by whoever writes."""
    writer, _, _, _, reindex = _writer(naming=_Naming({"Jane", "beach"}))

    await writer.write(
        "a1",
        {"title": "A Clip", "people": ["Jane"], "tags": ["beach"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert reindex.told == ["a1"]


async def test_a_name_that_is_only_spaces_is_not_a_name() -> None:
    writer, _, filing, naming, _ = _writer(naming=_Naming())

    await writer.write(
        "a1", {"people": ["   ", ""], "site": "  "}, creating=True, actor=Actor.sift("stash")
    )

    assert (naming.made, filing.attributed) == ([], [])


# --- and which of them MADE it -------------------------------------------------------------------


async def test_the_creator_an_answer_names_is_marked_and_nobody_else_is() -> None:
    """The creator mark, at the place almost every creator actually arrives.

    A box that keeps its creators where another keeps its studios answers with `creator` as well as
    with the people, and the creator leads that list. Exactly one of them is marked: a badge on
    everybody in the edit says nothing, and it is the performers who would wear it.
    """
    writer, _, filing, naming, _ = _writer(
        naming=_Naming({"Bramble Cutwork", "Odette Varnley", "Sable Quintrell"})
    )

    written = await writer.write(
        "a1",
        {
            "creator": "Bramble Cutwork",
            "people": ["Bramble Cutwork", "Odette Varnley", "Sable Quintrell"],
        },
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert naming.marked == ["id-Bramble Cutwork"]
    assert filing.attributed == [
        ("id-Bramble Cutwork", IMPORTED),
        ("id-Odette Varnley", IMPORTED),
        ("id-Sable Quintrell", IMPORTED),
    ]
    assert "creator" in written


async def test_an_answer_that_names_no_creator_marks_nobody() -> None:
    """Which is every box but one. A studio on StashDB is a site and rides in `site`."""
    writer, _, _, naming, _ = _writer(naming=_Naming({"Odette Varnley"}))

    written = await writer.write(
        "a1", {"people": ["Odette Varnley"]}, creating=False, actor=Actor.sift("stash")
    )

    assert naming.marked == []
    assert "creator" not in written


async def test_the_creator_is_matched_to_the_person_however_it_is_spelled() -> None:
    """The same normalisation every other name goes through, so one creator is one person."""
    writer, _, _, naming, _ = _writer(naming=_Naming({"Bramble Cutwork"}))

    await writer.write(
        "a1",
        {"creator": "  bramble CUTWORK ", "people": ["Bramble Cutwork"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert naming.marked == ["id-Bramble Cutwork"]


async def test_a_creator_with_no_row_behind_them_is_not_marked() -> None:
    """A run with no permission to invent makes nobody, so there is nobody to mark.

    The wrong person is not a fallback: `person_named` also answers with nothing for a name this
    library holds twice, and both absences arrive here as the same one.
    """
    writer, _, filing, naming, _ = _writer(naming=_Naming())

    written = await writer.write(
        "a1",
        {"creator": "Bramble Cutwork", "people": ["Bramble Cutwork"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert (naming.marked, filing.attributed) == ([], [])
    assert "creator" not in written


async def test_a_creator_nobody_in_the_answer_is_named_as_is_not_marked() -> None:
    """A payload naming a creator who is not among its own people writes nothing about them.

    It cannot happen from the mapper, which puts the creator at the head of that list. It can happen
    from a kept answer written by another version, and the honest reading of one is that there is
    nobody here to mark rather than somebody to go and find.
    """
    writer, _, _, naming, _ = _writer(naming=_Naming({"Bramble Cutwork", "Odette Varnley"}))

    await writer.write(
        "a1",
        {"creator": "Bramble Cutwork", "people": ["Odette Varnley"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert naming.marked == []


async def test_the_rows_an_answer_invented_are_recorded_against_the_box_that_invented_them() -> (
    None
):
    """WHICH BOX MADE a row, written down at the one moment anybody can be certain of it.

    The pairs handed in are what both apply paths already hold: `missing_for` is asked BEFORE the
    write, because a row that has been created is no longer missing, and narrowed to what the run
    was allowed to invent. So they are exactly the rows that did not exist a moment ago and do now.

    A name that resolves to nothing is skipped in silence, and that is the honest reading rather
    than a swallowed fault: a library holding two people of one name refuses to say which, and a
    name can clean away to nothing. Neither is a row to claim.
    """
    naming = _Naming({"Odette Varnley", "moonlit"})

    made = await record_who_invented(
        naming,
        invented=[
            ("person", "Odette Varnley"),
            ("tag", "moonlit"),
            ("site", "Nowhere At All"),
        ],
        box_id="box-one",
    )

    assert naming.made_by == [
        ("person", "id-Odette Varnley", "box-one"),
        ("tag", "id-moonlit", "box-one"),
    ]
    # ...and it ANSWERS with them, because the next thing asked of these rows is to link them to
    # the box that made them: a question about these ids, not about the names again.
    assert made == [
        Invented(kind="person", name="Odette Varnley", local_id="id-Odette Varnley"),
        Invented(kind="tag", name="moonlit", local_id="id-moonlit"),
    ]


def test_a_person_and_a_site_an_answer_invented_are_linked_by_the_boxs_own_id() -> None:
    """A tag is left out on purpose (no picture for a link to bring, a request per word), and so is
    a row the answer gave no id for: that one keeps the name search it always had."""
    record = FoundRecord(
        source_id="box-one",
        remote_id="scene-1",
        subject=Subject.ASSET,
        name="A Clip",
        # A tag id too, although no mapper writes one today: the tag is left out by KIND, not
        # because its id happens to be missing.
        refs={
            "person": {"Odette Varnley": "pf-1"},
            "site": {"Northlight Media": "st-1"},
            "tag": {"moonlit": "tg-1"},
        },
    )
    made = [
        Invented(kind="person", name="odette varnley", local_id="p1"),
        Invented(kind="person", name="Bramble Cutwork", local_id="p2"),
        Invented(kind="site", name="Northlight Media", local_id="s1"),
        Invented(kind="tag", name="moonlit", local_id="t1"),
    ]

    assert linkable(made, record, "box-one") == [
        ToLink(subject=Subject.PERSON, local_id="p1", box_id="box-one", remote_id="pf-1"),
        ToLink(subject=Subject.SITE, local_id="s1", box_id="box-one", remote_id="st-1"),
    ]


# --- a creator's username ---------------------------------------------------------------------

_ACCOUNT = {"site": "OnlyFans", "handle": "quillmoss", "url": "https://onlyfans.com/quillmoss"}


async def test_a_creator_is_filed_under_their_username_and_never_the_sites_nameless_row() -> None:
    """The username carries its Site, so the file is filed under a NAME, and the Site's own
    "poster unknown" filing is never made for it. The one person the answer attributed is who the
    username is."""
    writer, _, filing, _, _ = _writer(naming=_Naming({"OnlyFans", "Esme Wrenfield"}))

    written = await writer.write(
        "a1",
        {"accounts": [_ACCOUNT], "people": ["Esme Wrenfield"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert filing.usernames == [
        ("OnlyFans", "quillmoss", "https://onlyfans.com/quillmoss", IMPORTED, "id-Esme Wrenfield")
    ]
    assert filing.filed == []
    assert written["accounts"] == 1
    assert (await writer.current("a1"))["accounts"] == [_ACCOUNT]


async def test_a_username_already_filed_is_not_counted_again() -> None:
    writer, _, filing, _, _ = _writer(naming=_Naming({"OnlyFans"}))
    await writer.write("a1", {"accounts": [_ACCOUNT]}, creating=False, actor=Actor.sift("stash"))

    again = await writer.write(
        "a1", {"accounts": [_ACCOUNT]}, creating=False, actor=Actor.sift("stash")
    )

    assert "accounts" not in again
    assert len(filing.usernames) == 1


async def test_a_collaboration_links_the_username_to_nobody() -> None:
    writer, _, filing, _, _ = _writer(naming=_Naming({"OnlyFans", "Esme Wrenfield", "Jane Roe"}))

    await writer.write(
        "a1",
        {"accounts": [_ACCOUNT], "people": ["Esme Wrenfield", "Jane Roe"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert [one[4] for one in filing.usernames] == [None]


async def test_a_username_on_a_site_this_run_may_not_invent_is_not_filed() -> None:
    writer, _, filing, naming, _ = _writer(naming=_Naming())

    written = await writer.write(
        "a1", {"accounts": [_ACCOUNT]}, creating=False, actor=Actor.sift("stash")
    )

    assert (filing.usernames, naming.made, written) == ([], [], {})
    assert await writer.missing({"accounts": [_ACCOUNT]}) == (
        Missing(name="OnlyFans", kind=Subject.SITE.value),
    )


async def test_an_entry_with_no_name_is_never_filed() -> None:
    """An entry without a name would be the Site's nameless row by another road."""
    writer, _, filing, _, _ = _writer(naming=_Naming({"OnlyFans"}))

    await writer.write(
        "a1",
        {"accounts": [{"site": "OnlyFans", "handle": "  ", "url": ""}, "quillmoss"]},
        creating=True,
        actor=Actor.sift("stash"),
    )

    assert filing.usernames == []


# --- taking an answer's fields back off a file, and the Undo of that --------------------------------


async def test_taking_an_answer_back_clears_only_what_it_wrote_and_nothing_another_answer_says() -> (
    None
):
    """A column is cleared only while it holds exactly the answer's value and no other answer
    standing on the file says it; the Music field and the addresses come off the same way."""
    asset = _Asset(title="A Better Name", release_date="2024-03-09", music="Blue - Marla Quist")
    writer, content, _filing, _naming, reindex = _writer(content=_Content(asset))
    content.links = ["https://a.example/1", "https://b.example/2", "https://mine.example/3"]
    offered = {
        "title": "A Better Name",
        "release_date": "2024-03-09",
        "details": "Not what the file holds",
        "music": "Blue - Marla Quist",
        "links": ["https://a.example/1", "https://b.example/2"],
    }
    still = [{"release_date": "2024-03-09", "links": ["https://b.example/2"]}]

    cleared, dropped = await writer.take_back_fields("a1", offered, still_said=still)

    assert cleared == {"title": "A Better Name", "music": "Blue - Marla Quist"}
    assert dropped == ["https://a.example/1"]
    assert (asset.title, asset.release_date, asset.music) == (None, "2024-03-09", None)
    assert content.links == ["https://b.example/2", "https://mine.example/3"]
    assert reindex.told == ["a1"]

    put = await writer.put_back_fields("a1", cleared, dropped)

    assert put == 3
    assert (asset.title, asset.music) == ("A Better Name", "Blue - Marla Quist")
    assert content.links == ["https://b.example/2", "https://mine.example/3", "https://a.example/1"]


async def test_putting_back_writes_only_into_an_empty_field_and_tells_nobody_when_nothing_moved() -> (
    None
):
    asset = _Asset(title="Typed since")
    writer, content, _filing, _naming, reindex = _writer(content=_Content(asset))
    content.links = ["https://a.example/1"]

    put = await writer.put_back_fields(
        "a1", {"title": "A Better Name", "not_a_column": "x"}, ["https://a.example/1"]
    )

    assert put == 0 and asset.title == "Typed since" and reindex.told == []
    cleared, dropped = await writer.take_back_fields("a1", {"links": []}, still_said=[])
    assert (cleared, dropped, reindex.told) == ({}, [], [])


async def test_a_file_gone_since_has_nothing_taken_back_or_put_back() -> None:
    writer, _content, _filing, _naming, reindex = _writer(content=_Content(None))
    assert await writer.take_back_fields("gone", {"title": "x"}, still_said=[]) == ({}, [])
    assert await writer.put_back_fields("gone", {"title": "x"}, ["https://a.example/1"]) == 0
    assert reindex.told == []
