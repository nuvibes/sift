# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking the stash-boxes what a whole library is, as background work.

One job asks about one file, paced against somebody else's service; the sweep opens nothing, queues
a job per file not yet asked about, a page at a time, and re-queues itself. Both stop when the
switch is off, since what they do is send fingerprints out.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from sift.kernel.access import Repository, Viewer
from sift.kernel.access.constraints import NO_FILTER, AllOf, AssetFilter, Where
from sift.kernel.enrichment import (
    DEFAULT_STRATEGY,
    Enricher,
    Naming,
    Strategy,
    enrichable,
    strategy_key,
)
from sift.kernel.jobs import (
    JobBlocked,
    JobContext,
    JobQueue,
    WaitingForPassword,
    register_handler,
)
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.seams import CreatorPicturesSeam, SettingsSeam
from sift.kernel.vocabulary import VIA_STASH
from sift.slices.stash_boxes.adapter import USERNAME_REFS
from sift.slices.stash_boxes.enrich import ToLink, file_writer, linkable, record_who_invented
from sift.slices.stash_boxes.grades import Match
from sift.slices.stash_boxes.service import Grade, KeptLocal, StashBoxService
from sift.slices.stash_boxes.settings import (
    ASK_NEW_FILES_KEY,
    AUTO_APPLY_KEY,
    DURATION_KEY,
    SCAN_KEY,
    auto_box,
    may_invent,
)

if TYPE_CHECKING:  # pragma: no cover (a name for the type checker only)
    # Under the type checker only: `entities` imports `strategies_for` from here.
    from sift.slices.stash_boxes.entities import EntityEnricher

log = get_logger(__name__)

#: What a job says when what it was queued for is kept local. See `service.KEPT_LOCAL`.
KEPT_LOCAL_NOTE = "Kept local \u2014 nothing was sent outside this machine."

STASH_SCAN = "stash_box_scan"
STASH_SWEEP = "stash_box_sweep"
STASH_ENRICH = "stash_box_enrich"
STASH_LINK_INVENTED = "stash_box_link_invented"
STASH_CREATOR_PICTURE = "stash_box_creator_picture"

#: What the linking pass is remembered by in `stash_box_catch_ups`, one row per box; the prefix
#: never changes, or people taken off a box would be asked about again.
INVENTED_UNLINKED = "invented_unlinked_2026_09_25"


def invented_pass(box_id: str) -> str:
    """The name the linking pass is remembered by for ONE box, so a box off when it ran is asked later."""
    return f"{INVENTED_UNLINKED}:{box_id}"


#: The same pass for the Sites a box made, remembered apart under `invented_sites_pass`.
INVENTED_SITES_UNLINKED = "invented_sites_unlinked"


def invented_sites_pass(box_id: str) -> str:
    """The name the Site half of the linking pass is remembered by for ONE box."""
    return f"{INVENTED_SITES_UNLINKED}:{box_id}"


async def _boxes_owed(
    service: StashBoxService,
    owed: Sequence[tuple[str, str, str, str]],
    remembered: Callable[[str], str] = invented_pass,
) -> tuple[set[str], set[str]]:
    """Of the boxes `owed` names: those this pass asks now, and those that wait (off, or with no word)."""
    asking: set[str] = set()
    waiting: set[str] = set()
    for box_id, word in {box_id: word for _, _, box_id, word in owed}.items():
        if await service.catch_up_ran(remembered(box_id)):
            continue
        if word and await service.asks_anyone(word):
            asking.add(box_id)
        else:
            waiting.add(box_id)
    return asking, waiting


async def inventions_owed(service: StashBoxService) -> bool:
    """Whether the linking pass has anybody it can ask now: what a finished sweep asks before queueing
    it.
    """
    if await service.any_invented_unlinked():
        asking, _ = await _boxes_owed(service, await service.invented_unlinked())
        if asking:
            return True
    if await service.any_invented_sites_unlinked():
        owed = await service.invented_sites_unlinked()
        asking, _ = await _boxes_owed(service, owed, invented_sites_pass)
        return bool(asking)
    return False


class Outcome(StrEnum):
    """What asking the stash-boxes about one subject came to; only `FILLED` writes anything."""

    #: Exactly one match, linked, and fields filled in from it.
    WROTE = "wrote"
    #: Exactly one match and linked, with nothing left to fill in: already in agreement.
    LINKED = "linked"
    #: More than one match: a judgement for whoever knows their files. `Enrich` opens the chooser.
    AMBIGUOUS = "ambiguous"
    #: No match on any switched-on box, or no box switched on to ask.
    UNKNOWN = "unknown"
    FAILED = "failed"
    #: Kept local, so nothing was sent: a decision somebody made, counted back in those words.
    KEPT_LOCAL = "kept_local"

    @property
    def settled(self) -> bool:
        """Whether this one no longer needs a person. What the job counts as done."""
        return self in (Outcome.WROTE, Outcome.LINKED)


#: Here rather than beside the enricher, to keep the import one-way.


#: How much of the library one sweep job looks at; small, since its work is paced requests.
SWEEP_PAGE = 100

#: Who a queued job is acting for, resolved when it runs rather than when it was queued.
ViewerLookup = Callable[[str], Awaitable[Viewer | None]]


@dataclass(frozen=True, slots=True)
class ScanDeps:
    """What the two jobs need that is not the stash-box service itself, built once at boot."""

    access: Repository
    settings: SettingsSeam
    enricher: Enricher
    #: How a name becomes a row: only to say which box invented a row an unattended apply made.
    naming: Naming
    viewer_for: ViewerLookup


async def scan(
    context: JobContext,
    *,
    service: StashBoxService,
    deps: ScanDeps,
    entities: EntityEnricher | None = None,
) -> None:
    """Ask every switched-on box about one file, and apply the answer only if allowed to.

    `open_asset`, so a concealed file's fingerprints are never sent. A sealed key is `JobBlocked`,
    which parks the job. Only a `certain` match is ever applied without being asked.
    """
    if not await _switched_on(deps.settings):
        log.info("stashbox.job.skipped", job=STASH_SCAN, reason="switched off")
        return

    viewer = await deps.viewer_for(str(context.payload["viewer"]))
    if viewer is None:
        log.info("stashbox.job.skipped", job=STASH_SCAN, reason="the user that asked is gone")
        return

    asset_id = str(context.payload["asset_id"])
    asset = await deps.access.open_asset(viewer, asset_id)
    if asset is None:
        return
    hashes = {
        name: value
        for name, value in (("oshash", asset.oshash), ("phash", asset.video_phash))
        if value
    }
    if not hashes:
        # Not an error: a photograph has no video fingerprint, and a new clip none yet.
        await context.set_progress(1.0)
        return

    key = await context.master_key()
    if key is None:
        raise WaitingForPassword("the stash-box keys")

    tolerance = _seconds(await deps.settings.get_app(DURATION_KEY)) * 1000
    # Which box, decided once and carried: "" in the payload is every switched-on box, and only a
    # payload with no key reads the setting.
    named = context.payload.get("box")
    only = (str(named) or None) if named is not None else await auto_box(deps.settings)
    try:
        found = await service.scan_one(
            asset_id,
            hashes,
            key,
            length_ms=asset.duration_ms,
            tolerance_ms=tolerance,
            only=only,
            again=bool(context.payload.get("again")),
            # Asked again, an applied answer that comes back different is taken back.
            writer=file_writer(deps.enricher),
            actor=Actor.user(viewer.id),
        )
    except KeptLocal:
        # Kept local after this job was queued: the decision is the answer.
        await context.set_progress(1.0)
        await context.set_note(KEPT_LOCAL_NOTE)
        log.info("stashbox.job.skipped", job=STASH_SCAN, reason="kept local", asset_id=asset_id)
        return
    await context.set_progress(1.0)
    if not found or not await _applying(context, deps.settings):
        return
    certain = [one for one in found if one.grade is Grade.CERTAIN]
    if not certain:
        return
    await _apply_certain(
        certain, service=service, deps=deps, entities=entities, key=key, queue=context.queue
    )


async def _apply_certain(
    certain: Sequence[Match],
    *,
    service: StashBoxService,
    deps: ScanDeps,
    entities: EntityEnricher | None,
    key: bytes,
    queue: JobQueue | None,
) -> None:
    """Apply each certain match of one file's scan."""
    # Read once for the batch: about eighteen settings reads that no match changes.
    rules = await strategies_for(deps.settings, Subject.ASSET)
    inventable = await may_invent(deps.settings)
    for one in certain:
        await _apply(
            one.asset_id,
            one.box_id,
            service=service,
            deps=deps,
            rules=rules,
            inventable=inventable,
            entities=entities,
            master_key=key,
            queue=queue,
        )


async def _applying(context: JobContext, settings: SettingsSeam) -> bool:
    """Whether this question may accept a certain answer unconfirmed: the press's `apply`, else
    `AUTO_APPLY_KEY`.
    """
    pressed = context.payload.get("apply")
    if pressed is not None:
        return bool(pressed)
    return bool(await settings.get_app(AUTO_APPLY_KEY))


async def _apply(
    asset_id: str,
    box_id: str,
    *,
    service: StashBoxService,
    deps: ScanDeps,
    rules: Mapping[str, Strategy],
    inventable: frozenset[str],
    entities: EntityEnricher | None = None,
    master_key: bytes | None = None,
    queue: JobQueue | None = None,
) -> None:
    """Write what one certain match says, under the rules somebody chose field by field.

    Nothing is created unless switched on per kind (`may_invent`); what it declines is written down.
    """
    held = await service.match(asset_id, box_id)
    if held is None:  # pragma: no cover (the scan wrote this row a moment ago)
        return
    try:
        # Asked again: a file kept local while its question was out was just refused.
        await service.nothing_applied(Subject.ASSET, asset_id)
    except KeptLocal:
        log.info("stashbox.applied.refused", reason="kept local", asset_id=asset_id)
        return
    plan = await deps.enricher.plan_for(
        subject=Subject.ASSET,
        local_id=asset_id,
        source_id=box_id,
        offered=held.record.fields,
        strategies=rules,
    )
    if plan is None:
        return
    # Before the write, while what it creates is still missing.
    wanted = await deps.enricher.missing_for(plan)
    allowed = frozenset((one.kind, one.name) for one in wanted if one.kind in inventable)
    written = await deps.enricher.apply(plan, creating=allowed)
    # `allowed` names exactly the rows this box invented. See `enrich.record_who_invented`.
    made = await record_who_invented(deps.naming, invented=allowed, box_id=box_id)
    await service.settle(asset_id, box_id, applied=True)
    # Automatic, the one fact nothing else records, and what the writer says it filled in.
    await service.record_enrichment(
        Subject.ASSET, asset_id, box_id, automatic=True, applied=written
    )
    if queue is not None:
        await ask_for_creator_pictures(
            queue, held.record, box_id=box_id, asset_id=asset_id, written=written
        )
    log.info(
        "stashbox.applied.certain",
        fields=len(written),
        created=sorted(f"{kind}:{name}" for kind, name in allowed),
        not_created=sorted(
            f"{one.kind}:{one.name}" for one in wanted if one.kind not in inventable
        ),
    )
    # Then linked by the box's own id, after the settle and the run, so a failed request leaves both
    # done. See `link_invented`.
    if entities is not None:
        await link_invented(entities, linkable(made, held.record, box_id), master_key)


def creator_pictures_owed(
    record: FoundRecord, *, box_id: str, asset_id: str
) -> list[dict[str, str]]:
    """One picture job's payload per creator username this answer names with the box's id."""
    ids = record.refs.get(USERNAME_REFS) or {}
    offered = record.fields.get("accounts")
    owed: list[dict[str, str]] = []
    for one in offered if isinstance(offered, (list, tuple)) else ():
        if not isinstance(one, Mapping):
            continue
        site = str(one.get("site") or "").strip()
        handle = str(one.get("handle") or "").strip()
        studio = ids.get(handle)
        if site and handle and studio:
            owed.append(
                {
                    "box": box_id,
                    "studio": studio,
                    "asset_id": asset_id,
                    "site": site,
                    "username": handle,
                    "address": str(one.get("url") or "").strip(),
                }
            )
    return owed


async def ask_for_creator_pictures(
    queue: JobQueue,
    record: FoundRecord,
    *,
    box_id: str,
    asset_id: str,
    written: Mapping[str, int],
) -> None:
    """Queue the creator pictures an applied answer is owed, once its usernames have landed."""
    if "accounts" not in written:
        return
    owed = creator_pictures_owed(record, box_id=box_id, asset_id=asset_id)
    if not owed:
        return
    try:
        for one in owed:
            await queue.enqueue(STASH_CREATOR_PICTURE, one, dedupe=True)
    except Exception as exc:
        log.info("stashbox.creator_picture.not_queued", detail=str(exc))


async def keep_creator_picture(
    context: JobContext, *, service: StashBoxService, pictures: CreatorPicturesSeam
) -> None:
    """Give a creator's username the picture the box keeps for the studio it files them as."""
    payload = context.payload
    key = await context.master_key()
    if key is None:
        raise WaitingForPassword("the stash-box keys")
    box_id = str(payload["box"])
    studio = str(payload["studio"])
    asset_id = str(payload["asset_id"])

    async def fetch() -> bytes | None:
        return await service.site_picture(box_id, studio, key, about=(Subject.ASSET, asset_id))

    kept = await pictures.keep(
        site=str(payload["site"]),
        username=str(payload["username"]),
        address=str(payload.get("address") or "") or None,
        picture=fetch,
    )
    await context.set_progress(1.0)
    log.info("stashbox.creator_picture", kept=kept, box_id=box_id)


def register_picture_handler(*, service: StashBoxService, pictures: CreatorPicturesSeam) -> None:
    """Claim the creator-picture job, one at a time; called at boot by whoever holds the picture store."""
    register_handler(
        STASH_CREATOR_PICTURE,
        lambda context: keep_creator_picture(context, service=service, pictures=pictures),
        name="Keeping a creator's picture from a stash-box",
        alone=True,
    )


async def link_invented(
    entities: EntityEnricher, wanted: Sequence[ToLink], master_key: bytes | None
) -> Counter[Outcome]:
    """Link each row an answer invented to the box that named it, by its own id, and count the outcome."""
    tally: Counter[Outcome] = Counter()
    for one in wanted:
        with _one_of_many(
            tally,
            event="stashbox.link_invented.failed",
            subject=one.subject.value,
            subject_id=one.local_id,
        ):
            tally[
                await entities.link_known(
                    one.subject, one.local_id, one.box_id, one.remote_id, master_key
                )
            ] += 1
    return tally


def _within(folder: object) -> AssetFilter:
    """The sweep's work list, narrowed to one folder and everything under it, or the whole library.

    Already scoped to the asking admin, so it can only ever ask about fewer files.
    """
    if not folder:
        return NO_FILTER
    return AssetFilter(where=AllOf((Where("folder", (0,)),)), folder_scope=((str(folder),),))


@dataclass(frozen=True, slots=True)
class SweepPage:
    """One page of a sweep, decided: the files it would ask about, and how far it walked."""

    asking: tuple[str, ...]
    walked: int
    total: int


async def sweep_page(
    access: Repository,
    service: StashBoxService,
    viewer: Viewer,
    *,
    offset: int,
    folder: object,
    only: str | None,
) -> SweepPage:
    """Which files of this page no switched-on box has been asked about; placeholders walked, not
    asked.
    """
    page = await access.visible_assets(
        viewer, limit=SWEEP_PAGE, offset=offset, asset_filter=_within(folder)
    )
    wanted = [item.asset.id for item in page.items if not item.concealed]
    asking = await service.unasked(wanted, only)
    return SweepPage(asking=tuple(asking), walked=len(page.items), total=page.total)


async def sweep(context: JobContext, *, service: StashBoxService, deps: ScanDeps) -> None:
    """Queue one question per file that has not been asked about yet, a page at a time.

    Carries the user who asked, or none for Sift's own run. Steps by what the page walked, since a
    page is capped.
    """
    if not await _switched_on(deps.settings):
        log.info("stashbox.job.skipped", job=STASH_SWEEP, reason="switched off")
        return

    asked_by = context.payload.get("viewer")
    viewer = await _sweeping_as(asked_by, deps)
    if viewer is None:
        return

    offset = int(context.payload.get("offset") or 0)
    queued_before = int(context.payload.get("queued") or 0)
    folder = context.payload.get("folder")
    # Which box: a payload's word was decided for, "" being every box; none reads the setting.
    carried = context.payload.get("box")
    only = (str(carried) or None) if carried is not None else await auto_box(deps.settings)
    # Absent `apply` stays absent, so the children follow the setting. See `_applying`.
    applying = context.payload.get("apply")
    page = await sweep_page(deps.access, service, viewer, offset=offset, folder=folder, only=only)
    walked = page.walked
    waiting = page.asking
    for asset_id in waiting:
        # The box rides on each child, so a setting changed halfway cannot split the run.
        child: dict[str, object] = {"asset_id": asset_id, "viewer": viewer.id, "box": only or ""}
        if applying is not None:
            child["apply"] = bool(applying)
        await context.enqueue_child(
            STASH_SCAN,
            child,
            # At the sweep's own urgency.
            priority=context.job.priority,
        )

    reached = offset + walked
    queued = queued_before + len(waiting)
    await context.set_progress(1.0 if page.total == 0 else min(1.0, reached / page.total))

    # An empty page ends it, or it would re-queue itself at the same offset for ever.
    done = walked == 0 or reached >= page.total
    if not done:
        following: dict[str, object] = {
            "offset": reached,
            "queued": queued,
            "folder": folder,
            # The answer, not the payload, so the whole run asks one box.
            "box": only or "",
        }
        if applying is not None:
            following["apply"] = bool(applying)
        # A run that began as Sift's stays Sift's.
        if asked_by is not None:
            following["viewer"] = viewer.id
        await context.enqueue_child(STASH_SWEEP, following, priority=context.job.priority)
    else:
        await service.record_asking(
            actor=Actor.sift(VIA_STASH) if asked_by is None else Actor.user(viewer.id),
            count=queued,
            only=only or None,
        )
        # People a box invented before they were linked are asked once a sweep ran with that box on.
        if await inventions_owed(service):
            await context.enqueue_child(STASH_LINK_INVENTED, {}, priority=context.job.priority)
    await context.set_note(_swept(queued=queued, done=done))
    log.info("stashbox.sweep.queued", queued=queued, seen=reached, total=page.total)


async def _sweeping_as(asked_by: object, deps: ScanDeps) -> Viewer | None:
    """Who a sweep reads the library as, or None (logged) where it must not run."""
    if asked_by is None and not bool(await deps.settings.get_app(ASK_NEW_FILES_KEY)):
        # The sub-switch gates only the run nobody pressed.
        log.info("stashbox.job.skipped", job=STASH_SWEEP, reason="asking about new files is off")
        return None
    if asked_by is None:
        # Sift's own act, read through an admin's scope, which is the whole library.
        admin = await deps.access.an_admin()
        if admin is None:
            log.info(
                "stashbox.job.skipped", job=STASH_SWEEP, reason="no admin to read the library as"
            )
            return None
        viewer = await deps.viewer_for(admin)
    else:
        viewer = await deps.viewer_for(str(asked_by))
    if viewer is None:
        log.info("stashbox.job.skipped", job=STASH_SWEEP, reason="the user that asked is gone")
        return None
    return viewer


def _swept(*, queued: int, done: bool) -> str:
    """How many files this run is going to ask about, once that is known."""
    if not done:
        return "Working out which files to ask about\u2026"
    if queued == 0:
        return "Every file has already been asked about."
    files = "file" if queued == 1 else "files"
    return f"{queued} {files} queued to ask about."


def _seconds(held: object) -> int:
    """A stored tolerance as whole seconds; anything unreadable is none, grading every perceptual match
    unsure.
    """
    try:
        return max(0, int(str(held)))
    except (TypeError, ValueError):
        return 0


async def _switched_on(settings: SettingsSeam) -> bool:
    return bool(await settings.get_app(SCAN_KEY))


async def strategies_for(settings: SettingsSeam, subject: Subject) -> dict[str, Strategy]:
    """The rule somebody chose for each field of one subject; an unreadable value is the default."""
    out: dict[str, Strategy] = {}
    for key in enrichable(subject):
        held = await settings.get_app(strategy_key(subject, key))
        try:
            out[key] = Strategy(str(held))
        except ValueError:
            out[key] = DEFAULT_STRATEGY
    return out


@contextmanager
def _one_of_many(tally: Counter[Outcome], *, event: str, **about: object) -> Iterator[None]:
    """One item of a batch going wrong: logged with its ids, counted, and the loop carries on.

    `JobBlocked` is re-raised: sealed keys are true of every subject, and the kernel parks the job.
    """
    try:
        yield
    except JobBlocked:
        raise
    except Exception:
        log.exception(event, **about)
        tally[Outcome.FAILED] += 1


async def enrich_entities(context: JobContext, *, entities: EntityEnricher) -> None:
    """Ask the stash-boxes about a batch of people, sites or tags, in one job paced per box.

    A subject gone since is skipped and one that goes wrong is counted (`_one_of_many`).
    """
    key = context.payload.get("key")
    master_key = bytes.fromhex(str(key)) if key else None
    # Named by whoever queued it, so one press stays one decision.
    only = str(context.payload.get("box") or "") or None
    tally: Counter[Outcome] = Counter()
    for one in context.payload.get("subjects") or []:
        if not isinstance(one, dict):
            continue
        # A queued job's payload says `site`; the retired word is no longer read.
        try:
            subject = Subject(str(one.get("subject")))
        except ValueError:
            continue
        local_id, name = str(one.get("id") or ""), str(one.get("name") or "")
        if not local_id or not name:
            continue
        # The box's own id where the answer gave one, and only for the box this batch may ask.
        known_box, remote_id = str(one.get("box") or ""), str(one.get("remote_id") or "")
        with _one_of_many(
            tally,
            event="stashbox.enrich.subject_failed",
            subject=subject.value,
            subject_id=local_id,
        ):
            if known_box and remote_id and only in (None, known_box):
                tally[
                    await entities.link_known(subject, local_id, known_box, remote_id, master_key)
                ] += 1
            else:
                tally[await entities.enrich(subject, local_id, name, master_key, only=only)] += 1

    settled = sum(count for outcome, count in tally.items() if outcome.settled)
    await context.set_note(_enriched(tally))
    log.info(
        "stashbox.enrich.batch",
        asked=len(context.payload.get("subjects") or []),
        done=settled,
        ambiguous=tally[Outcome.AMBIGUOUS],
        unknown=tally[Outcome.UNKNOWN],
        failed=tally[Outcome.FAILED],
    )


def _enriched(tally: Counter[Outcome]) -> str:
    """What the batch came to, in a sentence somebody reading the job list can act on."""
    said: list[str] = []
    if tally[Outcome.WROTE]:
        said.append(f"{tally[Outcome.WROTE]} filled in")
    if tally[Outcome.LINKED]:
        said.append(f"{tally[Outcome.LINKED]} already agreed")
    if tally[Outcome.AMBIGUOUS]:
        many = tally[Outcome.AMBIGUOUS]
        said.append(f"{many} had more than one match \u2014 use Enrich to choose")
    if tally[Outcome.UNKNOWN]:
        said.append(f"{tally[Outcome.UNKNOWN]} not found on any switched-on box")
    if tally[Outcome.KEPT_LOCAL]:
        said.append(f"{tally[Outcome.KEPT_LOCAL]} kept local \u2014 nothing was sent")
    if tally[Outcome.FAILED]:
        # Last, and it says where to look: a traceback in a note helps nobody.
        said.append(f"{tally[Outcome.FAILED]} went wrong \u2014 the log says which")
    return ", ".join(said) if said else "Nothing to ask about."


async def link_what_boxes_invented(
    context: JobContext, *, service: StashBoxService, entities: EntityEnricher
) -> None:
    """Link every person and Site a box invented to that box, where it was never done. Once per box.

    Each is asked for by name of that box alone (`EntityEnricher.enrich`), and the pass is
    remembered in `stash_box_catch_ups` so a link somebody removed is not put back. A box that
    cannot be asked is not remembered; sealed keys park the pass.
    """
    people = await service.invented_unlinked()
    sites = await service.invented_sites_unlinked()
    halves = (
        (Subject.PERSON, people, invented_pass, await _boxes_owed(service, people)),
        (
            Subject.SITE,
            sites,
            invented_sites_pass,
            await _boxes_owed(service, sites, invented_sites_pass),
        ),
    )
    if not any(asking for *_, (asking, _waiting) in halves):
        log.info("stashbox.link_invented.skipped", reason="no switched-on box is owed")
        return
    key = await context.master_key()
    if key is None:
        raise WaitingForPassword("the stash-box keys")
    tally: Counter[Outcome] = Counter()
    not_asked = 0
    owed = [*people, *sites]
    done = 0
    for subject, rows, remembered, (asking, waiting) in halves:
        # A box that failed is not remembered, so it is asked again next time.
        faltered: set[str] = set()
        for local_id, name, box_id, word in rows:
            done += 1
            if box_id in waiting:
                not_asked += 1
                continue
            if box_id not in asking:
                continue
            before = tally[Outcome.FAILED]
            with _one_of_many(
                tally,
                event="stashbox.link_invented.subject_failed",
                subject=subject.value,
                subject_id=local_id,
                box_id=box_id,
            ):
                tally[await entities.enrich(subject, local_id, name, key, only=word)] += 1
            if tally[Outcome.FAILED] > before:
                faltered.add(box_id)
            await context.set_progress(done / len(owed))
        for box_id in sorted(asking - faltered):
            await service.record_catch_up(remembered(box_id))
    note = _enriched(tally)
    if not_asked:
        note = (
            f"{note} {not_asked} not asked yet \u2014 their stash-box is turned off. They are"
            " asked the next time Sift looks up files with it turned on."
        )
    await context.set_note(note)
    log.info(
        "stashbox.link_invented.done",
        owed=len(owed),
        linked=sum(count for outcome, count in tally.items() if outcome.settled),
        ambiguous=tally[Outcome.AMBIGUOUS],
        unknown=tally[Outcome.UNKNOWN],
        kept_local=tally[Outcome.KEPT_LOCAL],
        failed=tally[Outcome.FAILED],
        not_asked=not_asked,
    )


def register_handlers(
    *, service: StashBoxService, deps: ScanDeps, entities: EntityEnricher
) -> None:
    """Claim the job types. Called once, at boot, by whoever built the service."""
    register_handler(
        STASH_SCAN,
        # `entities` so an exact match applied here links the rows it invents.
        lambda context: scan(context, service=service, deps=deps, entities=entities),
        name="Asking the stash-boxes about a file",
    )
    register_handler(
        STASH_SWEEP,
        lambda context: sweep(context, service=service, deps=deps),
        name="Looking for files the stash-boxes know",
        # One at a time: a settle asks for the sweep as well as a press.
        alone=True,
    )
    register_handler(
        STASH_ENRICH,
        lambda context: enrich_entities(context, entities=entities),
        name="Enriching people, sites and tags from the stash-boxes",
    )
    register_handler(
        STASH_LINK_INVENTED,
        lambda context: link_what_boxes_invented(context, service=service, entities=entities),
        name="Linking people and Sites the stash-boxes added to them",
    )
