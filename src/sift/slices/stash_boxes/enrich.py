# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a file is read and written when a stash-box recognises one.

The Site, the people and the tags are rows elsewhere, reached through narrow shapes the wiring
builds, so creating them is permission handed in. The word index is told so an imported title can be
found.
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

#: The word in `source` for anything a stash-box put on a file; the match row says which box.
IMPORTED = "stash_box"

#: The importable plain columns of a file, read by `current`; `write` names each beside its setter.
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
        # Told once a write put people on a file, for the face question the wiring knows about.
        self._people_filed = people_filed
        # The username a studio was read as, by name, or None for a Site. See `_studio_read`.
        self._studio_account = studio_account

    async def current(self, local_id: str) -> Mapping[str, object]:
        """What Sift holds about this file that a stash-box could also have an opinion on.

        Every importable field, or an offer would overwrite a held value without being called a
        conflict.
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
        """The people, tags and sites in this answer that do not exist in this library yet, each by
        kind.
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
        # A creator's username needs only its Site: the username is made with the filing.
        for entry in _accounts(values.get("accounts")):
            if await self._naming.site_named(entry["site"], creating=False) is None:
                unknown.append(Missing(name=entry["site"], kind=Subject.SITE.value))
        return tuple(dict.fromkeys(unknown))

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write the fields a plan decided on, and nothing else. Answers with what it wrote.

        `creating` is asked per name. The answer is the keys something landed for, and how many rows
        each list field ends with.
        """
        # `actor` records no event here: each setter is part of one Save, whose event is
        # `browse.set_record`'s. The box is written on every row it files.
        box = actor.id if actor.kind == ACTOR_BOX and self._source == IMPORTED else None
        values = await self._studio_read(values)
        written = await self._write_columns(local_id, values)

        # The whole list, replaced: right only because the plan merged it with what was there.
        links = _names(values.get("links"))
        if links:
            await self._content.set_links(local_id, links)
            # How many the file ends up with.
            written["links"] = len(links)

        # The people first, then which of them made it, from the ids the first resolved.
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

        # Once, after the writes.
        await self._reindex.touched(local_id)
        log.info("enrich.file.written", fields=sorted(written))
        return written

    async def _write_columns(self, local_id: str, values: Mapping[str, object]) -> dict[str, int]:
        """The plain columns of `write`, each one a key in the answer when it landed."""
        # Six columns, each named beside its setter, so a renamed setter cannot go unnoticed.
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
        # `music` is importable too, so a box's track never silently replaces one somebody typed.
        track = stripped_or_none(values.get("music"))
        if track is not None:
            await self._content.set_music(local_id, track)
            written["music"] = 1
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
        """Clear the columns and drop the addresses one answer wrote, while they still hold its value
        and no other standing answer says it. Answers with what came off, for `put_back_fields`.
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
        """Undo `take_back_fields` where the columns are still empty; how many changed."""
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
        """The answer with a studio read as her username filed under it instead, so the Site is not
        made again.
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
        """Put the people this answer names on the file; who landed, by folded name, for
        `_mark_creator`.
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
        """Mark the one of these people the answer's `creator` field says made it; False when there is
        none.

        Only somebody already attributed can be marked; the wrong person is never a fallback.
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
        """File this file under the site the answer names, looked up and created only with permission.

        The filing is marked by `source`, so it can be counted and taken back. False when neither
        happened.
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

        The username is said to be the one person attributed, where there is exactly one.
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
    """These writers, filing under a Stash import's own word, which a box's take-back cannot reach."""
    found = file_writer(enricher)
    if found is None:
        return enricher
    writer = copy(found)
    writer._source = source
    return Enricher(writers={**enricher.writers, Subject.ASSET: writer})


def file_writer(enricher: object) -> AssetWriter | None:
    """The file's writer an enrichment holds, or None where this process has none."""
    writers = getattr(enricher, "writers", None)
    found = writers.get(Subject.ASSET) if isinstance(writers, Mapping) else None
    return found if isinstance(found, AssetWriter) else None


async def record_who_invented(
    naming: Naming, *, invented: Iterable[tuple[str, str]], box_id: str
) -> list[Invented]:
    """Write down that THIS box made these rows, having just applied its answer. Answers with them.

    Created cannot be recovered later, so it is said at the moment it happens; `invented` is what
    the caller was allowed to create. A name that resolves to nothing is skipped.
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

    People and sites only: a tag has no picture for a link to bring.
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
    """The username entries in a value that name both a Site and a username; the rest are dropped."""
    out: list[dict[str, str]] = []
    for one in value if isinstance(value, (list, tuple)) else ():
        if not isinstance(one, Mapping):
            continue
        site = str(one.get("site") or "").strip()
        handle = str(one.get("handle") or "").strip()
        if site and handle:
            out.append({"site": site, "handle": handle, "url": str(one.get("url") or "").strip()})
    return out
