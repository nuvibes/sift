# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a FILE is read and written when a stash-box recognises one.

The one writer whose interesting fields are not columns. A stash-box that recognises a file answers
with what it is (a title, a release date, what it is about, when it was shot, the reference the
studio files it under, where it can be found) and with three things that are ROWS somewhere else:
the Site that released it, the people in it, and what it is tagged as. That is what makes this
the writer that can CREATE things, and why creating is permission handed in rather than a decision
taken here.

It imports neither of those areas. The title and the date go through the content store, which is
the kernel's; the names go through two narrow shapes the wiring builds out of whatever services
exist. So this file knows that a file has people on it and does not know what a person is.

The word index is told, and that is load-bearing rather than tidy. An imported title is meant to be
searchable and the index is kept current by whoever writes, so a writer that skipped it would put
a title on a file that the search box could never find.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from copy import copy
from dataclasses import dataclass

from sift.kernel.content import ContentStore
from sift.kernel.enrichment import Creating, Enricher, Filing, Missing, Naming, may_create
from sift.kernel.ledger import ACTOR_BOX, Actor
from sift.kernel.log import get_logger
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.seams import ReindexSeam
from sift.kernel.text import stripped_or_none

log = get_logger(__name__)

#: The word written into `source` for anything a stash-box put on a file.
#:
#: One word, not the stash-box's id folded into it. WHICH stash-box said so is already recorded, on
#: the match row that led to the write, and a value spelling two facts into one column is a value
#: every reader has to take apart before it can ask either of them.
IMPORTED = "stash_box"

#: The importable fields of a file that are plain columns on its row, in the order the record draws
#: them. Read by `current`, where the attribute on the asset row has the same name as the field,
#: which is what lets one list serve instead of six readings that can each be forgotten separately.
#:
#: `write` does NOT use it: it names each one beside the setter that writes it, because a setter
#: looked up by name is a setter that can be renamed with nothing saying so. That the two agree is
#: held by a test rather than by this comment.
COLUMNS = ("title", "release_date", "details", "production_date", "site_code", "music")


class AssetWriter:
    """Reads and writes one file, for whoever is applying a stash-box's answer."""

    subject = Subject.ASSET

    def __init__(
        self,
        *,
        content: ContentStore,
        filing: Filing,
        naming: Naming,
        reindex: ReindexSeam,
        people_filed: Callable[[], Awaitable[None]] | None = None,
        studio_account: Callable[[str], Awaitable[Mapping[str, str] | None]] | None = None,
        source: str = IMPORTED,
    ) -> None:
        #: The word every row it files carries: a box's, or a Stash library's (`filing_as`).
        self._source = source
        self._content = content
        self._filing = filing
        self._naming = naming
        self._reindex = reindex
        # Told once a write has put people on a file: the face in it may be the box's question
        # about them, and the wiring knows who asks it (this file does not know faces exist).
        self._people_filed = people_filed
        # The username a studio has been read as, by the studio's name, or None where it is a
        # Site (`kernel.access.creator_studios.account_for`). See `_studio_read`.
        self._studio_account = studio_account

    async def current(self, local_id: str) -> Mapping[str, object]:
        """What Sift holds about this file that a stash-box could also have an opinion on.

        Every field the registry marks importable, and that is not tidiness: it is what makes the
        plan's comparison honest. A held value this did not read is a blank as far as the rules are
        concerned, so an offer would be written over the top of something already there without
        ever being called a conflict. A field missing here and from the writer would hide both
        faults, because a plan that can only offer and never compare still looks like it works.
        """
        asset = await self._content.get(local_id)
        held: dict[str, object] = {}
        if asset is not None:
            for key in COLUMNS:
                value = getattr(asset, key, None)
                if value:
                    held[key] = value
        held["links"] = list(await self._content.links_of(local_id))
        held["people"] = list(await self._filing.people_on(local_id))
        held["tags"] = list(await self._filing.tags_on(local_id))
        site = await self._filing.site_of(local_id)
        if site:
            held["site"] = site
        held["accounts"] = [dict(one) for one in await self._filing.accounts_on(local_id)]
        return held

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        """The people, tags and sites in this answer that do not exist in this library yet.

        What the confirm button lists, and the reason it exists at all. Somebody applying a page of
        matches has to be able to see that it would also invent eleven people BEFORE they press it,
        which is the difference between a bulk action and a bulk accident.

        Each one says WHAT it would be. Half of what a stash-box names about a video is people and
        half is its own filing vocabulary, and told apart they are two easy decisions where mixed
        into one alphabetical list they are thirty-one hard ones.
        """
        values = await self._studio_read(values)
        unknown: list[Missing] = []
        for name in _names(values.get("people")):
            if await self._naming.person_named(name, creating=False) is None:
                unknown.append(Missing(name=name, kind=Subject.PERSON.value))
        for name in _names(values.get("tags")):
            if await self._naming.tag_named(name, creating=False) is None:
                unknown.append(Missing(name=name, kind=Subject.TAG.value))
        site = stripped_or_none(values.get("site"))
        if site and await self._naming.site_named(site, creating=False) is None:
            unknown.append(Missing(name=site, kind=Subject.SITE.value))
        # A creator's username needs its Site and nothing else: the username itself is made with
        # the filing, the way a person's usernames are.
        for entry in _accounts(values.get("accounts")):
            if await self._naming.site_named(entry["site"], creating=False) is None:
                unknown.append(Missing(name=entry["site"], kind=Subject.SITE.value))
        return tuple(dict.fromkeys(unknown))

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write the fields a plan decided on, and nothing else. Answers with what it wrote.

        `creating` is asked per NAME. A page of matches can offer thirty-one entries this library
        has never heard of, and "all of them or none" is not an answer anybody has: half are people
        worth having and half are words somebody else files as tags. So the names that were ticked
        are made and the rest are dropped: the same drop as a run with no permission at all, and
        the file still gets every name it already knows.

        The answer is the list of keys something actually landed for, which is what the confirm
        toast counts; a count taken from the keys handed in could only ever agree with the ask.

        And how many rows each field gained: four of a file's fields are lists (the addresses,
        the people, the tags and the site), and "wrote its links" is the same sentence for one
        address and for nine. See `Writer.write`.
        """
        # `actor` records no event here: every field this writer touches goes through a setter that
        # is one eighth of one Save, and the event for the whole Save is `browse.set_record`'s. See
        # `test_every_write_records_an_event`, where those eight are excused for exactly that reason.
        # What it does give is WHICH box answered, written on every row the box files so a line
        # about the filing can name the box (`box_id` on the filing tables). A Stash library's rows
        # (`filing_as`) carry none: no box answered them.
        box = actor.id if actor.kind == ACTOR_BOX and self._source == IMPORTED else None
        values = await self._studio_read(values)
        # Six columns, named one at a time beside the setter each goes through. A loop over pairs of
        # key and setter would look the setter up by NAME, which can be renamed with nothing saying
        # so, and would hide the one thing worth reading here: every field has somewhere to land.
        written: dict[str, int] = {}
        title = stripped_or_none(values.get("title"))
        if title is not None:
            await self._content.set_title(local_id, title)
            written["title"] = 1
        released = stripped_or_none(values.get("release_date"))
        if released is not None:
            await self._content.set_release_date(local_id, released)
            written["release_date"] = 1
        about = stripped_or_none(values.get("details"))
        if about is not None:
            await self._content.set_details(local_id, about)
            written["details"] = 1
        shot = stripped_or_none(values.get("production_date"))
        if shot is not None:
            await self._content.set_production_date(local_id, shot)
            written["production_date"] = 1
        code = stripped_or_none(values.get("site_code"))
        if code is not None:
            await self._content.set_site_code(local_id, code)
            written["site_code"] = 1
        # `music` is offered by no stash-box today and is importable all the same: it is a fact about
        # the release. Without this branch, and its reading in `current`, the first box to answer
        # with a track would write over one somebody had typed, silently, as a blank filled in.
        track = stripped_or_none(values.get("music"))
        if track is not None:
            await self._content.set_music(local_id, track)
            written["music"] = 1

        # The whole list, replaced, and that is right ONLY because the plan merged it first: what
        # arrives here is what is already on the file plus whatever is new. Handing the offer alone
        # to a setter that replaces would throw away every address somebody typed.
        links = _names(values.get("links"))
        if links:
            await self._content.set_links(local_id, links)
            # How many the file ends up WITH: the plan merged what was there with what arrived, so
            # "how many are new" is a question about a diff nobody takes.
            written["links"] = len(links)

        # The people first, then which of them MADE it. The second reads the ids the first resolved
        # rather than asking for the name again: a creator this run had no permission to invent is a
        # name with no row behind it, and marking one would mean finding a different person of the
        # same spelling. See `_mark_creator`.
        attributed = await self._attribute(local_id, values, creating=creating, box=box)
        if attributed:
            written["people"] = len(attributed)
            if self._people_filed is not None:
                await self._people_filed()
        if await self._mark_creator(values, attributed):
            written["creator"] = 1
        tagged = await self._tag(local_id, values, creating=creating, box=box)
        if tagged:
            written["tags"] = tagged
        if await self._file_under(local_id, values, creating=creating, box=box):
            written["site"] = 1
        filed = await self._file_under_accounts(
            local_id, values, attributed, creating=creating, box=box
        )
        if filed:
            written["accounts"] = filed

        # Once, after the writes. The index reads the title, the people and the tags, so telling it
        # three times would rebuild one file's row three times for one decision.
        await self._reindex.touched(local_id)
        log.info("enrich.file.written", fields=sorted(written))
        return written

    async def read_as_written(self, values: Mapping[str, object]) -> Mapping[str, object]:
        """An answer's fields the way `write` reads them, for a take-back to compare against."""
        return await self._studio_read(values)

    async def take_back_fields(
        self,
        local_id: str,
        offered: Mapping[str, object],
        *,
        still_said: Sequence[Mapping[str, object]] = (),
    ) -> tuple[dict[str, object], list[str]]:
        """Clear the columns and drop the addresses one answer wrote. Answers with what came off.

        The undo of the six setters and of `set_links` in `write`. A column is cleared only while
        it still holds exactly the value the answer offered and no answer still standing on the
        file says it, so a value somebody edited since stays, and one two answers agree on stays
        with the other. An address comes off the same way. What was cleared is answered, column by
        column with the value it held, and the addresses dropped, so an Undo can write them back
        (`put_back_fields`). The people, tags and filings are the catalog's rows and are taken back
        there (`taken_back`).
        """
        asset = await self._content.get(local_id)
        if asset is None:
            return {}, []
        setters = {
            "title": self._content.set_title,
            "release_date": self._content.set_release_date,
            "details": self._content.set_details,
            "production_date": self._content.set_production_date,
            "site_code": self._content.set_site_code,
        }
        cleared: dict[str, object] = {}
        for key in COLUMNS:
            held = stripped_or_none(getattr(asset, key, None))
            if held is None or held != stripped_or_none(offered.get(key)):
                continue
            if held in {stripped_or_none(one.get(key)) for one in still_said}:
                continue
            if key == "music":
                await self._content.set_music(local_id, None)
            else:
                await setters[key](local_id, None)
            cleared[key] = held
        offered_links = set(_names(offered.get("links")))
        kept_links = {link for one in still_said for link in _names(one.get("links"))}
        links = list(await self._content.links_of(local_id))
        dropped = [one for one in links if one in offered_links and one not in kept_links]
        if dropped:
            await self._content.set_links(local_id, [one for one in links if one not in dropped])
        if cleared or dropped:
            await self._reindex.touched(local_id)
        return cleared, dropped

    async def put_back_fields(
        self, local_id: str, fields: Mapping[str, object], links: Sequence[str]
    ) -> int:
        """Undo `take_back_fields`: each column written back where it is still empty, and the
        addresses added back. How many changed."""
        asset = await self._content.get(local_id)
        if asset is None:
            return 0
        setters = {
            "title": self._content.set_title,
            "release_date": self._content.set_release_date,
            "details": self._content.set_details,
            "production_date": self._content.set_production_date,
            "site_code": self._content.set_site_code,
        }
        put = 0
        for key, value in fields.items():
            if key not in COLUMNS or stripped_or_none(getattr(asset, key, None)) is not None:
                continue
            if key == "music":
                await self._content.set_music(local_id, str(value))
            else:
                await setters[key](local_id, str(value))
            put += 1
        held = list(await self._content.links_of(local_id))
        missing = [one for one in links if one not in held]
        if missing:
            await self._content.set_links(local_id, [*held, *missing])
            put += 1
        if put:
            await self._reindex.touched(local_id)
        return put

    async def _studio_read(self, values: Mapping[str, object]) -> Mapping[str, object]:
        """The answer with a studio that has been read as her username filed under it instead.

        A studio read as one creator's own store (by the repair, or by somebody answering the
        question on Organize) is a USERNAME from then on, and the Site it once was is not made
        again by the next answer that names it: a lookup still running, a match kept from before,
        or a scene of hers that does not credit her all still say `site`. So `site` becomes that
        username, beside whatever usernames the answer already names, before anything is looked
        up or written.
        """
        site = stripped_or_none(values.get("site"))
        if not site or self._studio_account is None:
            return values
        account = await self._studio_account(site)
        if account is None:
            return values
        read = {key: value for key, value in values.items() if key != "site"}
        read["accounts"] = [*_accounts(values.get("accounts")), dict(account)]
        return read

    async def _attribute(
        self,
        local_id: str,
        values: Mapping[str, object],
        *,
        creating: Creating,
        box: str | None = None,
    ) -> dict[str, str]:
        """Put the people this answer names on the file. Who landed, by their name, folded.

        The NAMES and their rows rather than a bare yes-or-no, because the very next thing asked
        of this answer is "and which of them made it": a question about one of these people,
        answerable only where their ids are. Handing back a bool and looking the creator up again would ask
        this library for a name it has just resolved, and get a different answer whenever two people
        share a spelling: `person_named` refuses an ambiguous name, so the second ask would mark
        nobody on exactly the library where it matters.

        Folded, because the creator is matched to it case-insensitively, the same normalisation
        `_names` applies, plus the fold, so `QuillMoss` and `quillmoss` are one person here as
        they are everywhere else in Sift.
        """
        put: dict[str, str] = {}
        for name in _names(values.get("people")):
            person_id = await self._naming.person_named(
                name, creating=may_create(creating, Subject.PERSON.value, name)
            )
            if person_id is not None:
                await self._filing.attribute(local_id, person_id, source=self._source, box_id=box)
                put[name.casefold()] = person_id
        return put

    async def _mark_creator(self, values: Mapping[str, object], attributed: dict[str, str]) -> bool:
        """Mark the one of these people the answer says MADE it. False when there is none to mark.

        Only a box that keeps its creators where another keeps its studios answers with this, so on
        every other box it is absent and nothing here happens, which is the right shape: the flag
        is a claim about evidence, and a box that files creators as sites has offered none.

        Read off the `creator` field rather than off the head of `people`. The creator does lead
        that list, and a position is not a statement: a payload kept before the mapper said so leads
        with a performer, and nothing about the list itself could ever tell the two apart. The
        MIGRATION is where that older shape is read, once, against the box it came from.

        Only somebody already attributed can be marked. A creator whose name this run had no
        permission to invent has no row, and a name this library holds twice is refused by
        `person_named`: both come back from `_attribute` as absent, and both mean the same thing
        here: there is nobody to put the mark on, and the wrong person is not a fallback.
        """
        creator = stripped_or_none(values.get("creator"))
        if creator is None:
            return False
        person_id = attributed.get(creator.casefold())
        if person_id is None:
            return False
        await self._naming.mark_pmv_creator(person_id)
        return True

    async def _tag(
        self,
        local_id: str,
        values: Mapping[str, object],
        *,
        creating: Creating,
        box: str | None = None,
    ) -> int:
        """Put the words this answer names on the file. How many landed; nought when none did."""
        put = 0
        for name in _names(values.get("tags")):
            tag_id = await self._naming.tag_named(
                name, creating=may_create(creating, Subject.TAG.value, name)
            )
            if tag_id is not None:
                await self._filing.attach_tag(local_id, tag_id, source=self._source, box_id=box)
                put += 1
        return put

    async def _file_under(
        self,
        local_id: str,
        values: Mapping[str, object],
        *,
        creating: Creating,
        box: str | None = None,
    ) -> bool:
        """Make the site the answer names, and file this file under it. False when neither happened.

        A match reaches this writer only once it has been confirmed, and a confirmation is agreement
        to a fingerprint: an exact file hash, or a perceptual match whose length agrees. The
        studio on that answer is the stash-box's statement of who released the file, the same kind
        of claim as every other field on it, and every screen reaches a file's Site through
        `asset_usernames`, so the file is filed under it.

        The filing is marked: `source` says a stash-box decided it, so it can be counted, filtered
        and taken back off, unlike a filing made by hand. The site is only looked up, never
        created here without permission, so a run that may create nothing files nothing either.
        """
        site = stripped_or_none(values.get("site"))
        if not site:
            return False
        if (
            await self._naming.site_named(
                site, creating=may_create(creating, Subject.SITE.value, site)
            )
            is None
        ):
            return False
        await self._filing.file_under_site(local_id, site, source=self._source, box_id=box)
        return True

    async def _file_under_accounts(
        self,
        local_id: str,
        values: Mapping[str, object],
        attributed: Mapping[str, str],
        *,
        creating: Creating,
        box: str | None = None,
    ) -> int:
        """File this file under the usernames the answer names. How many filings are new.

        A box that files a creator as a studio under a network names that creator here, as a
        username on the network's Site (`adapter.creator_account`), so the file is filed under a
        NAME and never under the Site's nameless row. The Site is only looked up, and made only
        with permission, as `_file_under` does; the username is made with the filing.

        The username is said to be the one person this answer attributed, where there is exactly
        one: on such a box the performer on a creator's scene is that creator. Two or more is a
        collaboration, and guessing which of them owns the account is not an answer.
        """
        owners = set(attributed.values())
        person_id = next(iter(owners)) if len(owners) == 1 else None
        filed = 0
        for entry in _accounts(values.get("accounts")):
            site = entry["site"]
            if (
                await self._naming.site_named(
                    site,
                    creating=may_create(creating, Subject.SITE.value, site),
                    address=entry["url"] or None,
                )
                is None
            ):
                continue
            filed += await self._filing.file_under_username(
                local_id,
                site=site,
                handle=entry["handle"],
                url=entry["url"] or None,
                source=self._source,
                person_id=person_id,
                box_id=box,
            )
        return filed


def filing_as(enricher: Enricher, source: str) -> Enricher:
    """These writers, with the file's filing its rows under `source`: a Stash import's own word,
    so nothing that takes back what a box filed can reach a row the person's Stash filed."""
    found = file_writer(enricher)
    if found is None:
        return enricher
    writer = copy(found)
    writer._source = source
    return Enricher(writers={**enricher.writers, Subject.ASSET: writer})


def file_writer(enricher: object) -> AssetWriter | None:
    """The file's writer an enrichment holds, or None where this process has none (a take-back
    then leaves the columns and takes the rows alone)."""
    writers = getattr(enricher, "writers", None)
    found = writers.get(Subject.ASSET) if isinstance(writers, Mapping) else None
    return found if isinstance(found, AssetWriter) else None


async def record_who_invented(
    naming: Naming, *, invented: Iterable[tuple[str, str]], box_id: str
) -> list[Invented]:
    """Write down that THIS box made these rows, having just applied its answer. Answers with them.

    ## Why it answers with the rows

    Because the next thing asked of them is to link them to the box that made them (`linkable`),
    which needs their ids. Resolving the names a second time would ask the same question twice,
    and the second ask is where a library holding two people of one spelling answers differently.

    ## Why it is asked here rather than worked out later

    Enriched and created are different facts and the second cannot be recovered from the first: a
    person five boxes know about may have been typed in by hand, and the only moment anybody can say
    for certain that a box created a row is the moment it happens. The v39 migration infers it for
    older rows from their links, and says how much of a guess that is.

    ## Why it takes the names rather than finding them itself

    `invented` is what the caller already knows: both apply paths ask `missing_for` before the
    write (a row that has been created is no longer missing) and narrow it to what this run was
    allowed to create. Recomputing it here would ask again after the answer had changed.

    A name that resolves to nothing is skipped: `person_named` refuses a name this library holds
    twice, and a permitted name can still have cleaned away to nothing. Neither is a row to claim.
    """
    made: list[Invented] = []
    for kind, name in invented:
        if kind == Subject.PERSON.value:
            local_id = await naming.person_named(name, creating=False)
        elif kind == Subject.SITE.value:
            local_id = await naming.site_named(name, creating=False)
        elif kind == Subject.TAG.value:
            local_id = await naming.tag_named(name, creating=False)
        else:  # pragma: no cover (`Missing` carries one of the three and nothing else)
            continue
        if local_id is not None:
            await naming.mark_created_by_box(kind, local_id, box_id)
            made.append(Invented(kind=kind, name=name, local_id=local_id))
    return made


@dataclass(frozen=True, slots=True)
class Invented:
    """One row an answer made, by the name it was made under and the id it now has."""

    kind: str
    name: str
    local_id: str


@dataclass(frozen=True, slots=True)
class ToLink:
    """One row to link to the box that named it, by that box's own id for it."""

    subject: Subject
    local_id: str
    box_id: str
    remote_id: str


def linkable(made: Iterable[Invented], record: FoundRecord, box_id: str) -> list[ToLink]:
    """The rows just invented that this answer gave the box's own id for, ready to be linked.

    People and sites, and not tags. A scene's tags are asked for by name only, and a tag has no
    picture for a link to bring, so linking one would be a request to somebody else's service
    per word, for a record nobody reads, on a pass that invents tags a page at a time.

    A row the answer gave no id for is left to the name search it always had: an older kept answer
    carries none, and a credit with no performer behind it has none to give.
    """
    wanted: list[ToLink] = []
    for one in made:
        if one.kind not in (Subject.PERSON.value, Subject.SITE.value):
            continue
        remote_id = record.id_for(one.kind, one.name)
        if remote_id:
            wanted.append(
                ToLink(
                    subject=Subject(one.kind),
                    local_id=one.local_id,
                    box_id=box_id,
                    remote_id=remote_id,
                )
            )
    return wanted


def _names(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(one).strip() for one in value if str(one).strip()]


def _accounts(value: object) -> list[dict[str, str]]:
    """The username entries in a value that name both a Site and a username; the rest are dropped.

    A merged list reaches the writer (what the file holds plus what the box offers), so an entry
    already held is filed again, which changes nothing and counts nothing. An entry with no name is
    never one: filing it would be the Site's nameless row under another name.
    """
    out: list[dict[str, str]] = []
    for one in value if isinstance(value, (list, tuple)) else ():
        if not isinstance(one, Mapping):
            continue
        site = str(one.get("site") or "").strip()
        handle = str(one.get("handle") or "").strip()
        if site and handle:
            out.append({"site": site, "handle": handle, "url": str(one.get("url") or "").strip()})
    return out
