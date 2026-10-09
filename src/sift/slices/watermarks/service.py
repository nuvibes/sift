# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read the marks off a library: an exact address files the copy under its site, anything less is
only recorded."""

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

#: Also how a verdict of this pass is filed, so a file given up on leaves the count.
PRODUCT = "watermarks"

#: Also what `enriched:watermark` searches and the file's mark is drawn from.
FROM_WATERMARK = "watermark"

#: What an Undo reaches these filings through.
QUEUE = "watermarks"

_HOSTS: dict[str, str] = dict(signatures.SIGNATURES)


class SettingsReader(Protocol):
    """Reading a global preference; handed in, since a feature may not import another."""

    async def get_app(self, key: str) -> Any: ...


class WatermarkService:
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
        """Read by the routes to say which files are on disk."""
        return self._settings

    @property
    def store(self) -> Store:
        return self._store

    async def enabled(self) -> bool:
        return bool(await self._preferences.get_app(watermark_settings.ENABLED_KEY))

    async def device(self) -> str:
        return str(await self._preferences.get_app(watermark_settings.DEVICE_KEY))

    async def models(self) -> Reader:
        """The loaded models, rebuilt when the device changes since it is baked into the session."""
        device = await self.device()
        if self._reader is not None and self._device and self._device != device:
            self._reader.unload()
            self._reader = None
        if self._reader is None:
            self._reader = Reader(self._settings, self._hardware, device=device)
        self._device = device
        return self._reader

    def release(self) -> None:
        """Give back the models' memory, as switching the feature off does."""
        if self._reader is not None:
            self._reader.unload()
            self._reader = None

    async def ready(self) -> tuple[bool, str | None]:
        """Whether a pass would do anything, and the sentence saying why not."""
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
        """Fetch what is missing; returns what it installed."""
        store = weights.store(self._settings)
        installed = []
        for weight in weights.working_set():
            if store.installed(weight) and not force:
                continue
            await store.fetch(weight, progress=progress, fresh=force)
            installed.append(weight.id)
        return installed

    async def read_files(self) -> int:
        return await self._store.read_count(weights.REVISION)

    async def marks_found(self) -> int:
        return await self._store.found_count()

    async def waiting(self) -> int:
        """How many read files a pass would still open, counted no further than a page."""
        return await self._store.unread_count(weights.REVISION)

    async def unread(self) -> int:
        """How many unread files will want a reading, which `waiting` cannot see."""
        return await self._unread.get()

    # No "is this switched on" here: the import policy answers that for arriving files.

    async def lack(self) -> Lack | None:
        """Files with no reading yet, as one catch-up term; None while nothing will be read."""
        allowed, _ = await self.ready()
        if not allowed:
            return None
        return self._store.lack(weights.REVISION)

    async def unread_among(self, asset_ids: Sequence[str]) -> set[str]:
        allowed, _ = await self.ready()
        if not allowed:
            return set()
        return await self._store.unread_among(weights.REVISION, asset_ids)

    async def forget_reads(self) -> int:
        """Throw away what was read, leaving the filings standing."""
        return await self._store.forget_everything()

    async def visible_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files this user may be shown, by the read that decides visibility."""
        allowed = set()
        for asset_id in asset_ids:
            if await self._repository.can_view(viewer, asset_id):
                allowed.add(asset_id)
        return allowed

    async def read_asset(self, asset_id: str) -> Read | None:
        """Read one file's mark, file what is certain and record the look; None for no mark."""
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
                # The reader found no picture; no later pass does better, so it is given up on.
                await self._content.record_verdict(
                    asset_id,
                    PRODUCT,
                    code="no_picture",
                    reason="This file has no picture Sift can read, so there's no watermark to look at.",
                )
                log.info("watermarks.gave_up", asset_id=asset_id, code="no_picture")
            # Never probed: left for the next scan to probe.
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
        # A dead device is raised: the job fails once, the file stays unread.
        lines = await models.read_crops(pieces)
        read_text = [line.text for line in lines]
        mark = signatures.best_mark(read_text)
        found = None
        if mark is not None:
            found = Read(
                text=mark.text,
                kind=mark.kind,
                # Empty for a channel or a bare username (see `schema.py`).
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
        """Everything one reading writes, in one transaction; tags first, apart from any filing."""
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
            # Asks for a site, not a kind: a NOTICE carries one too.
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
        """The current name of the Site this site files under, found by host key so renames hold."""
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
        """File under the mark's site and a matching username, or none; returns the Site's name."""
        site, key = await self._site_now(connection, site)
        known = await self._store.usernames_on(connection, site)
        named = signatures.nearest_username(username, known) if username is not None else None
        if named is None:
            site_id = await catalog.file_assets_under_site_on(
                connection,
                asset_ids=[asset_id],
                site=site,
                source=FROM_WATERMARK,
                # Created by this pass, with the same word the filing carries.
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
                # Filed already: no receipt, so Undo cannot delete someone else's filing.
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
        """File under a bare `@name` only where one site alone has that username; invent nothing."""
        where = await self._store.sites_with_username(connection, username)
        if len(where) != 1:
            # None or several: a coin toss, so nothing is filed.
            return
        site, username_id, named = where[0]
        wrote = await catalog.link_username_to_asset_on(
            connection, asset_id=asset_id, username_id=username_id, source=FROM_WATERMARK
        )
        if not wrote:
            # Filed already: no receipt for a row this pass did not write.
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
        """One receipt per file, so one misread file can be undone alone."""
        if self._recorder is None:
            return
        # The object is what it was filed under, so the pane folds receipt and filing together.
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
            # The site travels too, to find its "poster unknown" row on Undo.
            payload=json.dumps(
                {
                    "kind": "filed",
                    "username_id": username_id,
                    "site": site,
                    "assets": [asset_id],
                }
            ),
            subjects=[Subject(kind="asset", id=asset_id)],
            # The ledger's words, as the file-name pass says them.
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
        """Undo one filing and refuse it again; the site and the username stay."""
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
    """The reader's confidence on the line the mark was found in, not an average."""
    return max(
        (line.confidence for line in lines if signatures.mark_in(line.text) is not None),
        default=0.0,
    )


def _moment_of(asset: Any) -> int:
    if str(asset.media_type) != "video":
        return 0
    return int((asset.duration_ms or 0) * frames.FRACTION)


SERVICE: Part[WatermarkService] = Part("watermarks")
