# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the marks off a library, and what Sift does with what it read.

**A mark is a property of the copy, not of the work, and every rule here follows from that.** A
clip re-encoded by an aggregator carries that aggregator's mark or none at all; the same scene
posted by the person who made it carries theirs. So reading a mark says where THIS FILE came off,
which is exactly what a filing under a site is, and it says nothing whatever about who is in it,
which is why nothing here ever writes a person.

**What is certain files something; what is not is written down and shown.** Most marks read are an
exact address, and an exact twelve-character address is not something a recogniser invents, so it
is taken as the site on its own. Everything short of that (an address one or two letters out, an
address with its front cut off by a crop) is recorded against the file with the text that was
actually read, and decides nothing. There is no queue and no card: a hint somebody has to come
back and confirm is more work than it saves, and a recorded fact that turns out to be wrong is
corrected by looking at it.

**A username is attributed only where that username already exists on the site the mark names.**
Allowing a name known on some OTHER site to count would file a file under the site its mark named
while attributing it to a username on a different one, which is not a stronger answer, it is a
contradiction. So those are recorded as unmatched reads, like the rest.

**And it never re-reads a file it has read.** See `schema.py`: the pass costs the same on a file
with no mark as on a file with one, and much of a library has no mark.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from sift.kernel import media
from sift.kernel.access import Repository, Viewer, catalog
from sift.kernel.access.sites import host_key, keep_site_key_on, site_for_key_on
from sift.kernel.access.stamps import bump_stamps_for_object
from sift.kernel.access.viewer import ObjectType
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Lack
from sift.kernel.db import Connection
from sift.kernel.hardware import HardwareReport
from sift.kernel.ids import new_id
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.memo import PacedAnswer
from sift.kernel.ml.weights import Progress
from sift.kernel.vocabulary import VIA_WATERMARK, Subject
from sift.kernel.wiring import Part
from sift.kernel.workbench import Recorder
from sift.slices.watermarks import frames, signatures, weights
from sift.slices.watermarks import settings as watermark_settings
from sift.slices.watermarks.reader import Reader
from sift.slices.watermarks.store import Read, Store

log = get_logger(__name__)

#: The product this pass makes, as the Build and its count file it, and as a verdict of this
#: pass's is filed, so a file given up on leaves the count.
PRODUCT = "watermarks"

#: The word written into `asset_usernames.source` by everything here. One word per thing: it is also
#: the word `enriched:watermark` searches by and the word the mark on a file is drawn from.
FROM_WATERMARK = "watermark"

#: The decision queue these filings are recorded under, which is what an Undo reaches them through.
QUEUE = "watermarks"

#: Each site a mark can name, to its address: what the site is keyed by (`_site_now`).
_HOSTS: dict[str, str] = dict(signatures.SIGNATURES)


class SettingsReader(Protocol):
    """Reading a global preference.

    Declared here rather than imported from the feature that stores preferences, because a feature
    may not import another feature. An implementation is handed in when the app is assembled.
    """

    async def get_app(self, key: str) -> Any: ...


class WatermarkService:
    """The feature, as one object the routes and the jobs both talk to."""

    def __init__(
        self,
        *,
        store: Store,
        content: ContentStore,
        repository: Repository,
        settings: Settings,
        hardware: HardwareReport,
        preferences: SettingsReader,
        recorder: Recorder | None = None,
        reader: Reader | None = None,
    ) -> None:
        self._store = store
        self._content = content
        self._unread = PacedAnswer(lambda: self._content.coming_count(PRODUCT))
        self._repository = repository
        self._settings = settings
        self._hardware = hardware
        self._preferences = preferences
        self._recorder = recorder
        self._reader = reader
        self._device = ""

    @property
    def settings(self) -> Settings:
        """Where this install keeps things. Read by the routes to say which files are on disk."""
        return self._settings

    @property
    def store(self) -> Store:
        return self._store

    # --- what is switched on ------------------------------------------------------------------

    async def enabled(self) -> bool:
        return bool(await self._preferences.get_app(watermark_settings.ENABLED_KEY))

    async def device(self) -> str:
        return str(await self._preferences.get_app(watermark_settings.DEVICE_KEY))

    async def models(self) -> Reader:
        """The loaded models, made once and rebuilt if the device behind them changed.

        Rebuilt rather than reconfigured: the device is baked into a prepared session, and quietly
        keeping the old one is how a change to it takes effect for everything except the thing
        already running.
        """
        device = await self.device()
        if self._reader is not None and self._device and self._device != device:
            self._reader.unload()
            self._reader = None
        if self._reader is None:
            self._reader = Reader(self._settings, self._hardware, device=device)
        self._device = device
        return self._reader

    def release(self) -> None:
        """Give back the memory the models hold. What switching the feature off does."""
        if self._reader is not None:
            self._reader.unload()
            self._reader = None

    async def ready(self) -> tuple[bool, str | None]:
        """Whether a pass would do anything, and the sentence saying why not when it would not."""
        if not await self.enabled():
            return False, None
        models = await self.models()
        if not models.installed():
            return False, (
                "The models have not been obtained yet. Sift does not include them; they are "
                "fetched once, from their publisher, when you ask for them."
            )
        broken = models.broken
        if broken is not None:
            return False, broken
        return True, None

    async def install_models(
        self, *, progress: Progress | None = None, force: bool = False
    ) -> list[str]:
        """Fetch what is missing. Hands back what it installed."""
        store = weights.store(self._settings)
        installed = []
        for weight in weights.working_set():
            if store.installed(weight) and not force:
                continue
            await store.fetch(weight, progress=progress, fresh=force)
            installed.append(weight.id)
        return installed

    # --- counts a screen asks for --------------------------------------------------------------

    async def read_files(self) -> int:
        return await self._store.read_count(weights.REVISION)

    async def marks_found(self) -> int:
        return await self._store.found_count()

    async def waiting(self) -> int:
        """How many read files a pass would still open, counted no further than a page."""
        return await self._store.unread_count(weights.REVISION)

    async def unread(self) -> int:
        """How many files not read yet will want a reading, which `waiting` cannot see."""
        return await self._unread.get()

    # --- what the catch-up pass over the library asks ------------------------------------------

    # There is deliberately no "is this switched on" method here. Whether an arriving file is read
    # is already answered, for this pass and for the two beside it, by the import policy: one
    # object that reads the stage's master, the feature's own switch and the per-file switch
    # together, and that a folder can answer differently. A second answer living here would be a
    # second place for the rule to be wrong.

    async def lack(self) -> Lack | None:
        """Files with no reading yet, as one term of the catch-up pass's count.

        None while nothing is going to be read (the feature off, or its models not obtained),
        which is the same answer `unread_among` gives as a set. Note that being SWITCHED ON for
        arriving files is a separate question, answered by `reads_on_import`: this one says whether
        the pass could read anything at all, and a person may untick the row for one run.
        """
        allowed, _ = await self.ready()
        if not allowed:
            return None
        return self._store.lack(weights.REVISION)

    async def unread_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files have no reading yet. Empty while nothing is going to be read."""
        allowed, _ = await self.ready()
        if not allowed:
            return set()
        return await self._store.unread_among(weights.REVISION, asset_ids)

    async def forget_reads(self) -> int:
        """Throw away what was read, leaving the filings standing. See the route for why."""
        return await self._store.forget_everything()

    async def visible_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files this user may actually be shown.

        Asked of the one read that decides visibility rather than answered here: a record of a
        decision about a file somebody may no longer see is still their record, and it still must
        not draw the file.
        """
        allowed = set()
        for asset_id in asset_ids:
            if await self._repository.can_view(viewer, asset_id):
                allowed.add(asset_id)
        return allowed

    # --- one file --------------------------------------------------------------------------

    async def read_asset(self, asset_id: str) -> Read | None:
        """Read one file's mark, file what is certain, and remember that it was looked at.

        Returns what was read, or None where there was no mark. **A file with no mark is written
        down exactly as firmly as one with**, and that is the point of the record: a pass costs the
        same either way, and a library where half the files carry nothing would otherwise be opened
        again in full on every sweep, for ever.

        A file that could not be READ is a different thing from a file with no mark on it, and is
        not written down at all. A share that is offline, a device that has died underneath the
        models: both leave the file unread and the next pass picks it up. Recording those as done
        is how a library comes to be marked read by a pass that never ran.
        """
        allowed, _ = await self.ready()
        if not allowed:
            return None
        try:
            source = await media.resolve_decodable(self._content, asset_id, settings=self._settings)
        except media.NoReadableCopy:
            log.info("watermarks.no_copy", asset_id=asset_id)
            return None
        asset = source.asset
        if not asset.width or not asset.height:
            if asset.probed_at is not None:
                # Read, and the reader found no picture in it (a GIF the decoder cannot
                # open is one). No later pass does better, so it is written down as given up on,
                # the way the face and Smart Search passes write theirs.
                await self._content.record_verdict(
                    asset_id,
                    PRODUCT,
                    code="no_picture",
                    reason="This file has no picture Sift can read, so there's no watermark to look at.",
                )
                log.info("watermarks.gave_up", asset_id=asset_id, code="no_picture")
            # Never probed, there is no frame to plan crops in yet: left unread, and the next scan
            # probes it so the pass after that can look at it.
            return None

        pieces = await frames.read(
            source.path,
            media_type=str(asset.media_type),
            width=int(asset.width),
            height=int(asset.height),
            duration_ms=int(asset.duration_ms or 0),
            settings=self._settings,
        )
        models = await self.models()
        # A device that has died is raised rather than swallowed: the job fails loudly, once, the
        # file stays unread, and the settings screen says what to do about it.
        lines = await models.read_crops(pieces)
        read_text = [line.text for line in lines]
        mark = signatures.best_mark(read_text)
        found = None
        if mark is not None:
            found = Read(
                text=mark.text,
                kind=mark.kind,
                # Empty on the kinds with no site to file under: a channel and a bare username.
                # See the note beside the column in `schema.py`; `kind` is what says which.
                site=mark.site or "",
                username=mark.username,
                confidence=_confidence_of(lines),
                frame_ms=_moment_of(asset),
            )
        await self._settle(
            asset_id, str(asset.identity), mark, found, signatures.tags_in(read_text)
        )
        log.info(
            "watermarks.read",
            asset_id=asset_id,
            kind=None if mark is None else mark.kind,
            site=None if found is None else found.site,
            exact=mark.exact if mark is not None else False,
        )
        return found

    async def _settle(
        self,
        asset_id: str,
        identity: str,
        mark: signatures.Mark | None,
        found: Read | None,
        tags: Sequence[str],
    ) -> None:
        """Everything one reading writes, in one transaction.

        The tags are written first and OUTSIDE the branch that decides a filing, because they are
        not part of it: a Telegram address is a fact about the copy that stands whether or not the
        same frame also carried an address worth filing on. Writing it inside the branch would make
        a file that carries both lose the tag, silently, for as long as the address kept winning.
        """
        async with self._store.write() as connection:
            await self._store.remember_on(
                connection,
                asset_id=asset_id,
                revision=weights.REVISION,
                identity=identity,
                found=1 if found is not None else 0,
            )
            for tag in tags:
                await self._store.tag_on(
                    connection, asset_id=asset_id, tag=tag, tag_id=new_id(), source=FROM_WATERMARK
                )
            if found is None or mark is None:
                await self._store.clear_read_on(connection, asset_id)
                return
            await self._store.record_on(connection, asset_id=asset_id, read=found)
            # `site is not None` is asked rather than the kind, and the two are not the same test:
            # a distributor's band files under the site its distributor re-hosts, so a NOTICE
            # carries one too. The property this branch needs is exactly "there is a site to file
            # under", which is what the field is, and asking it directly is also the narrowing the
            # call below needs: a list of kinds here would be a second place to remember when a
            # fifth kind is read.
            named_site = ""
            if mark.site is not None and mark.exact:
                named_site = await self._file(
                    connection, asset_id=asset_id, site=mark.site, username=mark.username
                )
            elif mark.site is not None:
                named_site, _ = await self._site_now(connection, mark.site)
            elif mark.kind == signatures.USERNAME:
                await self._attribute_bare_username(
                    connection, asset_id=asset_id, username=mark.username or ""
                )
            await self._store.name_site_on(connection, asset_id, named_site)

    async def _site_now(self, connection: Connection, site: str) -> tuple[str, str | None]:
        """The name the Site a mark's site files under has here now, and the site's key.

        A mark names a site the way `signatures` writes it (`OnlyFans`), and a library may have
        renamed the Site that site files under. Looked up by that word, the renamed Site would not
        answer and the next reading would make a second Site of the old name. So the Site is found
        through the site's key (its address, `host_key`, the key a download from that address files
        by too) wherever the site has filed somewhere, and by the word only before it ever has. A
        band names no address of its own, and is keyed by the address of the site it names.
        """
        host = _HOSTS.get(site)
        key = host_key(host) if host is not None else None
        if key is not None:
            filed = await site_for_key_on(connection, key)
            if filed is not None:
                return filed[1], key
        return site, key

    async def _file(
        self, connection: Connection, *, asset_id: str, site: str, username: str | None
    ) -> str:
        """File one file under the site its mark names or implies, and record the decision that did
        it. Returns the name of the Site it filed under, which is the Site's name now (`_site_now`).

        Two kinds of mark arrive here and they take the same path deliberately. An exact address
        names its site; a distributor's band implies one, because the distributor that draws it
        re-hosts that site's material. What they mean for the library is identical (this copy came
        off that site), so filing them differently would be two ways of writing one fact, and two
        Undos to keep working. A band carries no username, so it always lands on the "poster
        unknown" row below.

        The site is created if this library has never seen it, exactly the way the file-name reader
        creates one. A username is seeded only where the name read already answers to one on that
        site; otherwise the file is filed under the site itself, through the row that means "from
        here, poster unknown": a filing needs a username row to exist at all, and inventing a
        username out of a name nothing corroborates is the proposal-with-nothing-to-attach-to this
        feature exists to avoid.

        The first filing from a site remembers which Site it filed under (`keep_site_key_on`), so a
        Site renamed after a reading made it is still where the next reading files.
        """
        site, key = await self._site_now(connection, site)
        known = await self._store.usernames_on(connection, site)
        named = signatures.nearest_username(username, known) if username is not None else None
        if named is None:
            site_id = await catalog.file_assets_under_site_on(
                connection,
                asset_ids=[asset_id],
                site=site,
                source=FROM_WATERMARK,
                # The site can be invented here, and nobody was asked: this pass reads a mark burnt
                # into the picture. The same word the filing carries, so the site's own page and
                # `enriched:watermark` say one thing.
                made=catalog.by_sift(VIA_WATERMARK),
            )
            if key is not None:
                await keep_site_key_on(connection, key, site_id)
            where = site
            username_id = None
        else:
            site_id, username_id = await catalog.seed_site_username_on(
                connection,
                site=site,
                name=named,
                made=catalog.by_sift(VIA_WATERMARK),
            )
            if key is not None:
                await keep_site_key_on(connection, key, site_id)
            wrote = await catalog.link_username_to_asset_on(
                connection, asset_id=asset_id, username_id=username_id, source=FROM_WATERMARK
            )
            if not wrote:
                # Already filed under that username, by a person or by an earlier pass. A receipt
                # for a row this pass did not write would let an Undo delete somebody else's
                # filing, so there is no receipt and nothing to take back.
                return site
            where = f"{named} on {site}"
        await self._receipt(
            connection,
            asset_id=asset_id,
            where=where,
            username_id=username_id,
            site=site,
        )
        return site

    async def _attribute_bare_username(
        self, connection: Connection, *, asset_id: str, username: str
    ) -> None:
        """Attribute a file whose frame carried a username and no address, or write nothing.

        **The mark says who and not where, so the library has to supply the where, and only where
        it supplies exactly one.** A username drawn on its own is the weakest mark this reads: the
        same name exists on several sites, sometimes as the same person and sometimes not, and a
        name nothing in this library answers to is a string a recogniser produced. So there are two
        conditions and both are the library's own answer rather than this pass's guess: a username
        of exactly that name must already exist, and it must exist on exactly ONE site.

        **No site is invented and no username is, which is what separates this from the filing
        above.** That one reads an address, so it knows the site even when the name is new and can
        create both. This one knows neither: inventing a site out of a bare username would be making
        up the one fact the mark does not carry.

        Exact only, with none of the one-edit tolerance the marks that carry an address get. Those
        earn it from the address beside them: a name a letter out next to `onlyfans.com` is a
        misreading of an OnlyFans username. A name a letter out beside nothing at all is a different
        name, and there is nothing to say otherwise.
        """
        where = await self._store.sites_with_username(connection, username)
        if len(where) != 1:
            # Nothing, or several. Both are recorded as a read and neither files: a username on two
            # sites is a coin toss, which is the same answer `nearest_username` gives to two
            # usernames one edit apart, for the same reason.
            return
        site, username_id, named = where[0]
        wrote = await catalog.link_username_to_asset_on(
            connection, asset_id=asset_id, username_id=username_id, source=FROM_WATERMARK
        )
        if not wrote:
            # Already filed under that username. No receipt for a row this pass did not write.
            return
        await self._receipt(
            connection,
            asset_id=asset_id,
            where=f"{named} on {site}",
            username_id=username_id,
            site=site,
        )

    async def _receipt(
        self,
        connection: Connection,
        *,
        asset_id: str,
        where: str,
        username_id: str | None,
        site: str,
    ) -> None:
        """One receipt per file, which is what makes one file's filing correctable on its own.

        Not one per pass: a single receipt over thousands of files would mean the one misread file
        could not be put right without undoing every other file the same pass got right. The
        file-name reader keeps one per file for the same reason.
        """
        if self._recorder is None:
            return
        # WHAT IT WAS FILED UNDER, always. A read that named no poster lands on the site's own
        # "poster unknown" username, and that row IS the object: it is what the filing's own line
        # on the file is keyed by, so the file's pane folds the two into one line as it does every
        # other filing: without an object the file would say "Filed under OnlyFans from a
        # watermark" twice.
        under = (
            username_id
            if username_id is not None
            else await self._store.unattributed_on(connection, site)
        )
        await self._recorder.record_on(
            connection,
            queue=QUEUE,
            user_id=None,
            via=VIA_WATERMARK,
            title=f"Filed under {where} from a watermark",
            detail=f"One file filed under {where}, read from a watermark on the picture.",
            # The SITE travels beside the username, because a filing under a site with no name
            # read lands on that site's own "poster unknown" row, whose id nothing here held. An
            # Undo that could not name the row it was undoing would have had to guess.
            payload=json.dumps(
                {
                    "kind": "filed",
                    "username_id": username_id,
                    "site": site,
                    "assets": [asset_id],
                }
            ),
            subjects=[Subject(kind="asset", id=asset_id)],
            # The ledger's words for it, exactly as the file-name pass says them: this filed the
            # file under a username. It is what a file's pane folds this receipt and the filing's
            # own line together on. The "poster unknown" row has no name of its own: its words are
            # the site's, which is what the filing's own line says, so the snapshot is the site.
            verb="filed",
            object=(
                None
                if under is None
                else LedgerObject(
                    kind="username", id=under, name=site if username_id is None else None
                )
            ),
        )

    async def take_back(
        self, *, username_id: str | None, site: str | None, asset_ids: Sequence[str]
    ) -> bool:
        """Undo one filing. The site and the username are left standing.

        Removing them would reach beyond what was decided: the site may hold files nobody read a
        mark off, and a username may be somebody's own. What goes is the link this pass made, the
        read it was made from, and a standing refusal so the next sweep does not put it back.

        A username's wall and its Site draw the files taken off, on every tab open on them, so
        the take-back tells every admin and each user who may see that Site, in the same
        transaction, and moves their cache stamps with it: files leaving a Site can change what a
        share of it reaches.
        """
        if not asset_ids:
            return False
        async with self._store.write() as connection:
            where = username_id
            if where is None and site is not None:
                where = await self._store.unattributed_on(connection, site)
            if where is None:
                return False
            removed = await self._store.unfile_on(
                connection, username_id=where, source=FROM_WATERMARK, asset_ids=asset_ids
            )
            if removed:
                await self._store.refuse_on(connection, asset_ids)
                for asset_id in asset_ids:
                    await self._store.clear_read_on(connection, asset_id)
                told = EVERY_ADMIN
                on = await self._store.site_of_on(connection, where)
                if on is not None:
                    told |= await bump_stamps_for_object(connection, ObjectType.SITE, on)
                announce(told, About.LIBRARY)
        return removed > 0


def _confidence_of(lines: Sequence[Any]) -> float:
    """How sure the reader was of the line the mark was found in.

    The line's own number rather than an average over every line in the frame: what is being
    recorded is how well THIS was read, and a caption read perfectly beside a mark read badly would
    otherwise flatter it.
    """
    return max(
        (line.confidence for line in lines if signatures.mark_in(line.text) is not None),
        default=0.0,
    )


def _moment_of(asset: Any) -> int:
    """Where in the file the frame that carried the mark came from, in milliseconds."""
    if str(asset.media_type) != "video":
        return 0
    return int((asset.duration_ms or 0) * frames.FRACTION)


SERVICE: Part[WatermarkService] = Part("watermarks")
