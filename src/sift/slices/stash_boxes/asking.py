# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking the boxes one question at a time, behind the refusal for what is kept local.

A box that is switched off, unreachable or has no key is an absent feature, never an error on a
screen: an answer carries the sentence for why it is empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.access.catalog import (
    kept_local as subject_kept_local,
)
from sift.kernel.access.catalog import (
    kept_local_over as file_or_its_filings_kept_local,
)
from sift.kernel.access.catalog import (
    set_kept_local_on as write_kept_local_on,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.vocabulary import Subject as DecisionSubject
from sift.slices.stash_boxes.adapter import (
    EXACT as EXACT,
)
from sift.slices.stash_boxes.adapter import (
    Box,
    StashBoxUnreachable,
    as_json,
    from_json,
    ranked,
)
from sift.slices.stash_boxes.configured import _ONE, _TABLES, Answer, ConfiguredBoxes

log = get_logger(__name__)

#: How long a fetched answer is served from the cache before it is asked again: names change over
#: years, and a short window would ask a public service the same question over and over.
CACHE_DAYS = 30

#: What somebody is told when they ask for a thing kept local to be enriched. It names the switch
#: on the thing itself, not a pane, and says what to do; the row that does it is on the same menu.
KEPT_LOCAL = "Kept local \u2014 not sent outside this device. Allow enrichment on it first."

#: The same refusal in the words a MENU ROW has room for, under a row drawn refused.
KEPT_LOCAL_WHY = "Kept local"

#: And the same, for a FILE refused by something it is filed under rather than by its own switch.
#:
#: It has to say which, because the switch on the row underneath will not turn this off and a row
#: that said only "Kept local" would make that look broken. See `catalog.kept_local_over`.
KEPT_LOCAL_INHERITED_WHY = "Kept local by something it's filed under"

#: Each kind a file can be kept local by, as the reason names it.
_KEPT_BY: Mapping[str, str] = {
    "folder": "the folder",
    "person": "the person",
    "site": "the Site",
    "tag": "the tag",
}


def kept_local_by(named: Sequence[tuple[str, str]]) -> str:
    """The reason a FILE is kept local by what it is filed under, naming the first of `named` (each
    a kind and a name the reader may see) and counting the rest; with none, the general words."""
    if not named:
        return KEPT_LOCAL_INHERITED_WHY
    kind, name = named[0]
    more = f" and {len(named) - 1} more" if len(named) > 1 else ""
    return f"Kept local by {_KEPT_BY.get(kind, 'the')} {name}{more}"


class KeptLocal(Exception):
    """Raised at the door when something kept local would have been sent to a stash-box.

    An exception, never an empty answer: an empty answer is what "no stash-box has heard of this"
    looks like, and the two must stay told apart.
    """

    def __init__(self, message: str = KEPT_LOCAL) -> None:
        super().__init__(message)


_READ_CACHE = (
    "SELECT payload, fetched_at FROM stash_box_answers WHERE box_id = ? AND kind = ? AND ask = ?"
)

_WRITE_CACHE = (
    "INSERT INTO stash_box_answers (box_id, kind, ask, payload, fetched_at) VALUES (?, ?, ?, ?, ?)"
    " ON CONFLICT(box_id, kind, ask) DO UPDATE SET payload = excluded.payload,"
    " fetched_at = excluded.fetched_at"
)


def _ask_of(hashes: Mapping[str, str]) -> str:
    """The cache key for one file's fingerprints. Sorted, so the same file asks the same question."""
    return "|".join(f"{name}={value}" for name, value in sorted(hashes.items()))


class AskingBoxes(ConfiguredBoxes):
    """The questions: a name, a file's hashes, a picture, and the door that refuses them."""

    async def kept_local(self, subject: Subject, local_id: str) -> bool:
        """Whether this thing must never be sent to a stash-box. The predicate, published."""
        if subject is Subject.ASSET:
            return await file_or_its_filings_kept_local(self._db, local_id)
        return await subject_kept_local(self._db, subject.value, local_id)

    async def kept_local_here(self, subject: Subject, local_id: str) -> bool:
        """What is written on the row ITSELF, with nothing inherited. What a menu draws: a menu row
        offers the switch that is actually on this thing."""
        return await subject_kept_local(self._db, subject.value, local_id)

    async def set_kept_local(
        self, viewer: Viewer, subject: Subject, local_id: str, kept: bool, *, name: str | None
    ) -> bool:
        """Keep this thing local, or let it be enriched again. False when there was no such row."""
        async with self._db.write() as connection:
            if not await write_kept_local_on(connection, subject.value, local_id, kept):
                return False
            await record_event(
                connection,
                actor=Actor.user(viewer.id),
                verb="kept_local" if kept else "allowed",
                # Every word `Subject` holds is a word `SubjectKind` holds, and the type checker
                # agrees, so no mapping table restates it here.
                subject=DecisionSubject(kind=subject.value, id=local_id, name=name),
            )
        announce_now(EVERY_ADMIN, About.LIBRARY)
        return True

    async def nothing_applied(self, subject: Subject, local_id: str) -> None:
        """The other half of the door. Refuse to apply an answer this machine already holds to
        something kept local now.
        """
        if await self.kept_local(subject, local_id):
            raise KeptLocal

    async def _nothing_leaves(self, about: tuple[Subject, str] | None) -> None:
        """The door. Refuse, where what is about to be sent is about something kept local."""
        if about is not None and await self.kept_local(*about):
            raise KeptLocal

    async def _asked(
        self,
        box: Box,
        kind: str,
        ask: str,
        hashes: Mapping[str, str] | None,
        *,
        about: tuple[Subject, str] | None,
    ) -> list[FoundRecord]:
        """One question, sent to the right query for the kind of thing being asked about."""
        await self._nothing_leaves(about)
        if kind == "recognise" and hashes is not None:
            return await self._adapter.recognise(box, hashes)
        if kind == f"search:{Subject.SITE.value}":
            return await self._adapter.search_sites(box, ask)
        if kind == f"search:{Subject.TAG.value}":
            return await self._adapter.search_tags(box, ask)
        return await self._adapter.search(box, ask)

    async def _fetched(
        self, box: Box, subject: Subject, remote_id: str, *, about: tuple[Subject, str] | None
    ) -> FoundRecord | None:
        """One subject by id, from the adapter that speaks this box's language."""
        await self._nothing_leaves(about)
        if subject is Subject.ASSET:
            return await self._adapter.scene(box, remote_id)
        if subject is Subject.SITE:
            return await self._adapter.site(box, remote_id)
        if subject is Subject.TAG:
            return await self._adapter.tag(box, remote_id)
        return await self._adapter.person(box, remote_id)

    async def _one(
        self,
        box_id: str,
        kind: str,
        ask: str,
        master_key: bytes | None,
        *,
        hashes: Mapping[str, str] | None = None,
        about: tuple[Subject, str] | None = None,
    ) -> Answer:
        """One box, one question: the cache first, then the service, then the cache again.

        The refusal is asked before the cache as well as at the door: a cached answer applied to
        something kept local is the enrichment somebody said they did not want.
        """
        await self._nothing_leaves(about)
        row = await self._db.fetch_one(_ONE, (box_id,))
        name = str(row["name"]) if row is not None else box_id

        await self._answers_read_now()
        cached = await self._db.fetch_one(_READ_CACHE, (box_id, kind, ask))
        if cached is not None and self._now() - int(cached["fetched_at"]) < CACHE_DAYS * 86400:
            records = from_json(str(cached["payload"]))
            # The certainty mark is not in the cache (it is worked out from the term and the
            # record), so a cached search is marked again on the way out. See `ranked`.
            if kind.startswith("search:"):
                records = ranked(records, ask)
            return Answer(
                source_id=box_id,
                source_name=name,
                records=records,
                fetched_at=int(cached["fetched_at"]),
                fresh=False,
            )

        box = await self._unsealed(box_id, master_key)
        if box is None:
            return Answer(box_id, name, [], self._now(), False, await self._sealed_sentence(box_id))

        try:
            records = await self._asked(box, kind, ask, hashes, about=about)
        except StashBoxUnreachable as failure:
            # An unreachable box is an absent feature, not a broken screen. The sentence travels
            # with the answer so one box failing does not hide what the others said.
            return Answer(box_id, name, [], self._now(), False, str(failure))

        now = self._now()
        # Written without a bell: no screen draws the cache (the answer goes back to whoever asked,
        # and only this read looks at it again), so ringing the library for it would re-read
        # every screen in the library for nothing, as often as once a second through a scan.
        await self._db.execute(_WRITE_CACHE, (box_id, kind, ask, as_json(records), now))
        return Answer(box_id, name, records, now, True)

    # --- Asking ----------------------------------------------------------------------------

    async def check(self, box_id: str, master_key: bytes | None) -> str | None:
        """Ask a box the smallest real question there is. None when it answered.

        IMPORTANT: Deliberately NOT `me { api_key }`, which returns the key itself. The question asked is a
        search for a term nothing matches: it exercises the address, the key and the error path, and
        the answer it produces is an empty list.
        """
        box = await self._unsealed(box_id, master_key)
        if box is None:
            return "That stash-box is not configured, or its key cannot be read yet."
        try:
            await self._adapter.search(box, "sift connection check")
        except StashBoxUnreachable as failure:
            return str(failure)
        return None

    async def search(
        self,
        term: str,
        master_key: bytes | None,
        *,
        subject: Subject = Subject.PERSON,
        about: str | None = None,
        only: str | None = None,
    ) -> list[Answer]:
        """Ask every switched-on box about a name. One answer each, cached or fresh."""
        if subject not in _TABLES:
            raise ValueError(f"a stash-box cannot be searched for a {subject.value}")
        asking = None if about is None else (subject, about)
        return [
            await self._one(
                box_id,
                f"search:{subject.value}",
                term.strip().lower(),
                master_key,
                about=asking,
            )
            for box_id in await self._enabled_ids(only)
        ]

    async def recognise(
        self,
        asset_id: str,
        hashes: Mapping[str, str],
        master_key: bytes | None,
        *,
        only: str | None = None,
    ) -> list[Answer]:
        """Ask every switched-on box what a FILE is, from hashes Sift already holds.

        A file kept local is refused here, at the door, and not merely left out of the sweep's work
        list. The sweep does leave it out (see `unasked`), and that is an efficiency rather than
        the rule: this route is reachable from a file's own menu, from a folder, and from a job
        queued before the flag was set.
        """
        ask = _ask_of(hashes)
        return [
            await self._one(
                box_id, "recognise", ask, master_key, hashes=hashes, about=(Subject.ASSET, asset_id)
            )
            for box_id in await self._enabled_ids(only)
        ]

    async def picture(
        self, box_id: str, url: str, master_key: bytes | None, *, vector: bool = False
    ) -> tuple[bytes, str] | None:
        """One picture from one box, fetched by Sift so Sift can serve it. None if it will not come.

        `vector` lets an SVG through, only where the bytes go to the cover door, which draws one
        safely; never for a picture handed to a browser.
        """
        box = await self._unsealed(box_id, master_key)
        if box is None:
            return None
        if vector:
            return await self._adapter.picture(box, url, vector=True)
        return await self._adapter.picture(box, url)

    async def site_picture(
        self,
        box_id: str,
        remote_id: str,
        master_key: bytes | None,
        *,
        about: tuple[Subject, str] | None,
    ) -> bytes | None:
        """The picture one box keeps for one of its sites, by the id it files it under.

        None when it will not come: a box sealed or unreachable, an id it does not know, a site
        with no picture. `about` is what the question concerns here, and the door refuses it
        (`KeptLocal`) where that is kept local.
        """
        box = await self._unsealed(box_id, master_key)
        if box is None:
            return None
        try:
            found = await self._fetched(box, Subject.SITE, remote_id, about=about)
        except StashBoxUnreachable as failure:
            log.info("stashbox.site_picture.failed", box=box.name, why=str(failure))
            return None
        if found is None or not found.image_url:
            return None
        # A creator picture goes to the cover door, which draws a vector safely, so a studio
        # whose only picture is an SVG gets one too.
        got = await self.picture(box_id, found.image_url, master_key, vector=True)
        return None if got is None else got[0]
