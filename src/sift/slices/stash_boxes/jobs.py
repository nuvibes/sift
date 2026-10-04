# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking the stash-boxes what a whole library is, as background work.

Two jobs, and they are separate for the reason the face jobs are: they cost wildly different things
and are started by different events.

**Asking about one file** is a request to somebody else's public service, through a deliberate
throttle. It is the expensive one, and expensive here means SLOW rather than heavy: nothing is
decoded, nothing is read off disk, and the whole cost is waiting politely.

**The sweep** opens no file and asks nothing. It reads a page of the library, drops the files that
have already been asked about, queues one job for each of the rest, and re-queues itself. A job that
ran until the library was done would hold a worker for hours, could not report progress anybody
could read, and would lose everything it had queued if the machine went down halfway.

Both check the switch first and do nothing at all when it is off. A job queued before somebody
turned the feature off finds it off and stops rather than doing the work its payload describes,
and what it describes here is sending fingerprints of somebody's files to three public services.
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
    # Under the type checker only, because `entities` imports `strategies_for` from HERE. Importing
    # it back at runtime would be a cycle; the annotation is all that is needed, and
    # `from __future__ import annotations` at the top is what makes that enough.
    from sift.slices.stash_boxes.entities import EntityEnricher

log = get_logger(__name__)

#: What a job says when what it was queued for is kept local. The screens' own sentence, said in a
#: job's note rather than a second wording of it. See `service.KEPT_LOCAL`.
KEPT_LOCAL_NOTE = "Kept local \u2014 nothing was sent outside this machine."

STASH_SCAN = "stash_box_scan"
STASH_SWEEP = "stash_box_sweep"
STASH_ENRICH = "stash_box_enrich"
STASH_LINK_INVENTED = "stash_box_link_invented"
STASH_CREATOR_PICTURE = "stash_box_creator_picture"

#: What the linking pass is remembered by, in `stash_box_catch_ups`: every person a box invented
#: before the rows it invents were linked by its own id (0.1.190), linked to it. See
#: `link_what_boxes_invented`. Remembered ONE ROW PER BOX, under `invented_pass`. The stored names
#: carry this prefix, so it never changes: a new prefix would ask again about people somebody has
#: since taken off a box.
INVENTED_UNLINKED = "invented_unlinked_2026_09_25"


def invented_pass(box_id: str) -> str:
    """The name the linking pass is remembered by for ONE box, in `stash_box_catch_ups`.

    Per box, because the pass can only ask a box that is switched on: remembered once for the
    whole library, a box that was off when it ran would be recorded as done having asked nobody,
    and never asked once it was turned on. A box is remembered once its own people have been asked.
    """
    return f"{INVENTED_UNLINKED}:{box_id}"


#: The same pass for the SITES a box made, remembered one row per box under
#: `invented_sites_pass`. Its own name, because a box whose people were asked has not had its
#: Sites asked. Never renamed, for the reason `INVENTED_UNLINKED` gives.
INVENTED_SITES_UNLINKED = "invented_sites_unlinked"


def invented_sites_pass(box_id: str) -> str:
    """The name the Site half of the linking pass is remembered by for ONE box."""
    return f"{INVENTED_SITES_UNLINKED}:{box_id}"


async def _boxes_owed(
    service: StashBoxService,
    owed: Sequence[tuple[str, str, str, str]],
    remembered: Callable[[str], str] = invented_pass,
) -> tuple[set[str], set[str]]:
    """Of the boxes `owed` names: those this pass asks now, and those whose people have to wait.

    A box whose pass is remembered is in neither: its people were asked, and one still unlinked
    was left for the chooser or taken off it since, which is a decision. One that is switched off,
    or one Sift has no word for (which cannot be asked alone), WAITS: nothing is asked of it and
    nothing is remembered, so the pass comes back for it after a sweep with it switched on.
    """
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
    """Whether the linking pass has anybody it can ask NOW: what a finished sweep asks before
    queueing it.

    "Anything owed at all" first, because that is one indexed row and no on most installs. A box
    that is switched off queues nothing: a job that could only report "nobody asked" is noise in
    Activity, and its people are still owed, not done.
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
    """What asking the stash-boxes about one subject came to.

    Five answers and only one of them writes anything. They are named rather than collapsed to a
    yes-or-no because the four that write nothing are not the same event and do not lead to the
    same next move: one is finished, one wants a person, one means nobody has heard of this, and
    one is this subject having gone wrong on its own. A screen that cannot tell them apart can only
    say "nothing happened".
    """

    #: Exactly one match, linked, and fields filled in from it.
    WROTE = "wrote"
    #: Exactly one match and linked, with nothing left to fill in: already in agreement.
    LINKED = "linked"
    #: More than one match. A judgement, and it belongs to whoever knows which one their files are
    #: of. `Enrich` opens the chooser with the name already in it.
    AMBIGUOUS = "ambiguous"
    #: No match on any switched-on box, or no box switched on to ask.
    UNKNOWN = "unknown"
    #: Asking about this one raised. The batch goes on. See `enrich_entities`.
    FAILED = "failed"
    #: Kept local, so nothing about it was sent anywhere. Its own answer rather than a failure or a
    #: silence: it is the one outcome that is a DECISION somebody made, and a person who marked
    #: forty people kept local and then pressed Auto-enrich over them is owed the count back in
    #: those words rather than "nothing happened".
    KEPT_LOCAL = "kept_local"

    @property
    def settled(self) -> bool:
        """Whether this one no longer needs a person. What the job counts as done."""
        return self in (Outcome.WROTE, Outcome.LINKED)


#: Lives here rather than beside the enricher that returns it, and only because of the import
#: graph: `entities` already imports `strategies_for` from this module, so putting the vocabulary
#: here keeps the dependency one-way. The other direction would be a cycle.


#: How much of the library one sweep job looks at.
#:
#: Smaller than the face sweep's page, and deliberately. That one enqueues work that runs on this
#: machine; this one enqueues requests to somebody else's service, paced at a few a second, so a
#: page of five hundred is a queue that takes minutes to drain and a progress bar that sits still.
SWEEP_PAGE = 100

#: Who a queued job is acting for, resolved when it runs rather than when it was queued.
ViewerLookup = Callable[[str], Awaitable[Viewer | None]]


@dataclass(frozen=True, slots=True)
class ScanDeps:
    """What the two jobs need that is not the stash-box service itself.

    A small bag rather than five keyword arguments threaded through every call. It is built once at
    boot beside everything it names, which is also the only place any of them are named together.
    """

    access: Repository
    settings: SettingsSeam
    enricher: Enricher
    #: How a name becomes a row here. Held for one thing only: saying which box INVENTED a row that
    #: an unattended apply has just created: the seam is what knows the id behind a name, and
    #: nothing else in these two jobs turns a word into one.
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

    The scoped read comes first: it is what stops a file this user may not be shown having its
    fingerprints sent to third-party services.

    `open_asset` and not `get_asset`: `get_asset` hands back a concealed row as a locked
    placeholder, and a placeholder carries the real row behind it, fingerprints and all. The rule is
    `kernel.reach.require_reachable`: a placeholder is not something to act on. A test double whose
    `get_asset` returns None for a hidden id is stricter than the real thing and cannot catch this.

    A box with a key cannot be asked without one, and after a restart nobody's key is open yet. That
    is `JobBlocked` rather than a failure: the kernel parks the job until somebody logs in, where
    failing would mark a whole unattended sweep as broken.

    Auto-apply is checked here rather than at the sweep, because the grade is not known until the
    answer is back. Only a `certain` match (an exact-file hash the scene itself carries, which is an
    identity rather than a resemblance) is ever applied without being asked, whatever the switch
    says: a perceptual match is fuzzy and capped by nature, and no setting should be able to turn
    that into a decision.
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
        # Not an error, and not worth a line per file: a photograph has no video fingerprint, and a
        # clip that has not been through the catch-up pass has none yet.
        await context.set_progress(1.0)
        return

    key = await context.master_key()
    if key is None:
        raise WaitingForPassword("the stash-box keys")

    tolerance = _seconds(await deps.settings.get_app(DURATION_KEY)) * 1000
    # Which box, decided once and carried. A payload that carries the key has already been decided
    # for (by the flyout on the press, or by the sweep that queued this child), and an empty
    # string there means every switched-on box. Only a payload with no key at all (an older queued
    # job) falls back to the setting. Re-reading the setting per child would split one sweep across
    # two answers if it changed halfway.
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
            # Asked again, an applied answer that comes back different is taken back with the
            # writer's columns, as the person who pressed Identify again.
            writer=file_writer(deps.enricher),
            actor=Actor.user(viewer.id),
        )
    except KeptLocal:
        # A file marked kept local after this job was queued. Not a failure: the decision is the
        # answer, and the job says so rather than reporting that three services had never heard of
        # a file nobody was allowed to ask about.
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
    # Once for the batch, not once per match. Both of these are settings reads (about fifteen for
    # the field rules and three for the switches), and neither changes between two matches of one
    # scan. Read inside the loop they would be fifteen round trips per file, on a pass whose whole
    # point is to get through a lot of files.
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
            queue=context.queue,
        )


async def _applying(context: JobContext, settings: SettingsSeam) -> bool:
    """Whether this question may accept a CERTAIN answer without anybody confirming it.

    The press decides where there was one, and the setting only where there was not. A pressed
    Auto-enrich carries `apply: True`: pressing it is the consent, as the same verb means on a
    person, a site and a tag. A pressed Enrich carries `apply: False`: it asks and lets somebody
    choose, so even an exact match waits in the pile. Only a question queued by a run nobody
    pressed carries no `apply`, and that is the one `AUTO_APPLY_KEY` decides for.

    Read off the payload rather than off "was there a viewer", because every one of these carries a
    viewer: the unattended sweep reads the library as an admin, so the viewer on its children
    cannot tell a press from the pass that asks about new files.
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

    **Nothing is invented unless somebody turned that on**, per kind, and off is the default for all
    three. See `may_invent`. Applying without asking is already the furthest this feature goes on
    its own, and a switch labelled "accept exact matches without asking" must not also populate the
    People wall overnight for somebody who never asked it to.

    What it declines to create is written down either way: a page of matches which files nothing
    because every person on it is new is otherwise indistinguishable from a switch that does not
    work.
    """
    held = await service.match(asset_id, box_id)
    if held is None:  # pragma: no cover (the scan wrote this row a moment ago)
        return
    try:
        # Asked again although the door let the question out a moment ago: the answer is being
        # APPLIED, and a file marked kept local while its question was out is a file somebody has
        # just said no to. See `StashBoxService.nothing_applied`.
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
    # Before the write: once a row has been created it is no longer missing, so asking afterwards
    # would report nothing was needed on exactly the runs that created something.
    wanted = await deps.enricher.missing_for(plan)
    allowed = frozenset((one.kind, one.name) for one in wanted if one.kind in inventable)
    written = await deps.enricher.apply(plan, creating=allowed)
    # Which rows this box INVENTED, as against which ones it merely described. `allowed` is already
    # the answer: it is what was missing a moment ago, narrowed to the kinds this run may create,
    # so after the write it names exactly the rows that did not exist before it. See
    # `enrich.record_who_invented`.
    made = await record_who_invented(deps.naming, invented=allowed, box_id=box_id)
    await service.settle(asset_id, box_id, applied=True)
    # Automatic, the one fact about this write nothing else records: a match applied by the switch
    # above and one agreed to on the confirm screen leave the same row in `asset_stash_box_matches`.
    #
    # And what it filled in, from `apply`'s own answer: the fields the writer says it wrote, never
    # the ones the plan asked for. The file's History line is made of this and of the file's own
    # rows. See `catalog.record_enrichment`.
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
    # AND THEN LINKED, BY THE BOX'S OWN ID, so each row this answer invented brings its record and
    # its cover the way a row linked by hand does. After the settle and the run, because the file's
    # work is done by then: a link is a request to somebody else's service, and one that fails must
    # not leave the match unsettled or the run unwritten. See `link_invented`.
    if entities is not None:
        await link_invented(entities, linkable(made, held.record, box_id), master_key)


def creator_pictures_owed(
    record: FoundRecord, *, box_id: str, asset_id: str
) -> list[dict[str, str]]:
    """One picture job's payload per creator username this answer names with the box's id.

    A username without the box's id for its studio has nothing to fetch a picture by, so it is
    left out rather than searched for by name.
    """
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
    """Queue the creator pictures an applied answer is owed, once its usernames have landed.

    Only where the write filed the file under a username: a rule that ignores usernames ignores
    their pictures too. Queued rather than fetched, so a confirm answers without waiting on a
    box, and a picture that will not come is never a match that failed.
    """
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
    """Give a creator's username the picture the box keeps for the studio it files them as.

    The box is asked only when the creator has no picture yet (`CreatorPicturesSeam.keep`), and
    about the file that named them, so a file kept local since sends nothing.
    """
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
    """Claim the creator-picture job. Called at boot by whoever holds the picture store.

    One at a time: many files of one creator queue one job each, and the first to keep a picture
    is what lets the rest finish without asking the box.
    """
    register_handler(
        STASH_CREATOR_PICTURE,
        lambda context: keep_creator_picture(context, service=service, pictures=pictures),
        name="Keeping a creator's picture from a stash-box",
        alone=True,
    )


async def link_invented(
    entities: EntityEnricher, wanted: Sequence[ToLink], master_key: bytes | None
) -> Counter[Outcome]:
    """Link each row an answer invented to the box that named it, and count what that came to.

    ## Why it is here and not left to the name search

    The answer carries the box's own id for each person (`FoundRecord.refs`), so the link needs no
    search. It goes through `EntityEnricher.link_known`: the same link, picture and fields every
    other link gets.

    One going wrong does not stop the rest, for the reason `_one_of_many` gives, and `JobBlocked`
    still parks the whole job because it is true of every row at once.
    """
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
    """The sweep's work list, narrowed to one folder and everything under it.

    The unnarrowed filter is the whole library, which is what the button in Settings asks for.

    ## Why a folder at all

    On a library of any size, asking about the whole of it is minutes of questions to somebody
    else's service about files settled long ago, when what was wanted was the folder something was
    just dropped into, the same reason a folder's menu offers "Scan this folder".

    The two verbs stay named apart: scanning is reading a disk, enriching is asking a stash-box
    what a thing is.

    The subtree expansion is the read's, not this function's: naming one folder here matches
    anything in it or under it, by the same rule `in:` follows on the grid. So enriching a folder
    somebody has organised into subfolders does what it looks like it does.

    Scoped like everything else: this narrows a list that is ALREADY what the asking admin may see,
    so it can only ever ask about fewer files, never reach past them to more.
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
    """Which files of this page no switched-on box has been asked about. Asks nothing.

    The sweep queues exactly these, and a dry run counts them, so the two cannot disagree.
    PLACEHOLDERS ARE STEPPED OVER, not asked about: `visible_assets` hands back a concealed row as
    a locked tile for a user in placeholder mode, and the per-file job refuses one. They are still
    WALKED, so the paging counts the library rather than a subset of it and still reaches the end.
    """
    page = await access.visible_assets(
        viewer, limit=SWEEP_PAGE, offset=offset, asset_filter=_within(folder)
    )
    wanted = [item.asset.id for item in page.items if not item.concealed]
    asking = await service.unasked(wanted, only)
    return SweepPage(asking=tuple(asking), walked=len(page.items), total=page.total)


async def sweep(context: JobContext, *, service: StashBoxService, deps: ScanDeps) -> None:
    """Queue one question per file that has not been asked about yet, a page at a time.

    It carries the user who asked for it, where somebody did. The work list comes from the
    access layer, so the sweep covers what that admin can see rather than reaching past them, and
    a user who has gone since is a sweep that stops rather than one that quietly runs as
    nobody. A sweep nobody pressed carries no user at all: it is Sift's own act, it reads the
    library as an admin would, and it is written down as Sift's when it finishes.

    Re-queues itself rather than looping, and steps by what the page WALKED rather than by what it
    asked for. The access layer caps a page, so a sweep asking for a hundred, given forty, and
    stepping on by a hundred would step over sixty files every time, silently, with nothing to see
    but a library that never finishes.
    """
    if not await _switched_on(deps.settings):
        log.info("stashbox.job.skipped", job=STASH_SWEEP, reason="switched off")
        return

    asked_by = context.payload.get("viewer")
    if asked_by is None and not bool(await deps.settings.get_app(ASK_NEW_FILES_KEY)):
        # The sub-switch gates the run nobody pressed and nothing else: a press is a decision
        # somebody just took, and the switch above both is what decides whether anything is asked.
        log.info("stashbox.job.skipped", job=STASH_SWEEP, reason="asking about new files is off")
        return
    if asked_by is None:
        # Sift's own act. The sweep the fingerprint chain asks for after its last page carries no
        # user, and it still has to read the library through one, because every read in the
        # access layer is scoped to a viewer. It reads as an admin: an admin's scope is the whole
        # library, so which admin makes no difference, and the repository picks the same one every
        # time. What the run does is recorded as Sift's (`record_asking`); the switch that lets it
        # run is already in History against whoever turned it on.
        admin = await deps.access.an_admin()
        if admin is None:
            log.info(
                "stashbox.job.skipped", job=STASH_SWEEP, reason="no admin to read the library as"
            )
            return
        viewer = await deps.viewer_for(admin)
    else:
        viewer = await deps.viewer_for(str(asked_by))
    if viewer is None:
        log.info("stashbox.job.skipped", job=STASH_SWEEP, reason="the user that asked is gone")
        return

    offset = int(context.payload.get("offset") or 0)
    queued_before = int(context.payload.get("queued") or 0)
    folder = context.payload.get("folder")
    # Which box. A payload that carries a word has been decided for (by the route, which turned
    # the press's answer into one (`settings.box_for`), or by the page before this one), and ""
    # there is every switched-on box, the same reading the per-file job gives it. Only a sweep with
    # no word at all reads the setting, and that is the pass nobody pressed.
    carried = context.payload.get("box")
    only = (str(carried) or None) if carried is not None else await auto_box(deps.settings)
    # Whether the answers may be accepted without asking, carried down unchanged. Absent stays
    # absent, so the unattended pass's children are decided by the setting. See `_applying`.
    applying = context.payload.get("apply")
    page = await sweep_page(deps.access, service, viewer, offset=offset, folder=folder, only=only)
    walked = page.walked
    waiting = page.asking
    for asset_id in waiting:
        # The box rides on each child rather than being read again when the child runs. A sweep is
        # one decision taken once, and a setting changed halfway through would otherwise split one
        # run across two answers with nothing saying so.
        child: dict[str, object] = {"asset_id": asset_id, "viewer": viewer.id, "box": only or ""}
        if applying is not None:
            child["apply"] = bool(applying)
        await context.enqueue_child(
            STASH_SCAN,
            child,
            # At the sweep's own urgency: a pass the fingerprint chain asked for is background
            # work and so are its questions, while a press stays a press all the way down.
            priority=context.job.priority,
        )

    reached = offset + walked
    queued = queued_before + len(waiting)
    await context.set_progress(1.0 if page.total == 0 else min(1.0, reached / page.total))

    # An empty page also ends it, and that is the guard rather than a tidy-up: a page that walked
    # nothing advances nothing, so re-queueing on one would be this job asking for itself again at
    # the same offset, for ever.
    done = walked == 0 or reached >= page.total
    if not done:
        following: dict[str, object] = {
            "offset": reached,
            "queued": queued,
            "folder": folder,
            # The ANSWER, not the payload: a first page that read the setting hands the next page
            # what it read, so the whole run asks one box even if the setting moves under it.
            "box": only or "",
        }
        if applying is not None:
            following["apply"] = bool(applying)
        # The user rides forward only where there was one: a run that began as Sift's stays
        # Sift's on every page, and the page that finishes it is the one that says so.
        if asked_by is not None:
            following["viewer"] = viewer.id
        await context.enqueue_child(STASH_SWEEP, following, priority=context.job.priority)
    else:
        await service.record_asking(
            actor=Actor.sift(VIA_STASH) if asked_by is None else Actor.user(viewer.id),
            count=queued,
            only=only or None,
        )
        # The people a switched-on box invented before they were linked to it are asked about
        # once a sweep has run with that box on. A box turned on later is reached by its next sweep.
        if await inventions_owed(service):
            await context.enqueue_child(STASH_LINK_INVENTED, {}, priority=context.job.priority)
    await context.set_note(_swept(queued=queued, done=done))
    log.info("stashbox.sweep.queued", queued=queued, seen=reached, total=page.total)


def _swept(*, queued: int, done: bool) -> str:
    """How many files this run is going to ask about, once that is known.

    A button that finishes in ten milliseconds having done nothing is indistinguishable from a
    broken one. Nothing to do is a perfectly good answer: it just has to be given.
    """
    if not done:
        return "Working out which files to ask about\u2026"
    if queued == 0:
        return "Every file has already been asked about."
    files = "file" if queued == 1 else "files"
    return f"{queued} {files} queued to ask about."


def _seconds(held: object) -> int:
    """A stored tolerance as whole seconds. Anything unreadable is none, which grades every
    perceptual match as unsure: the cautious reading, and the one that loses nothing."""
    try:
        return max(0, int(str(held)))
    except (TypeError, ValueError):
        return 0


async def _switched_on(settings: SettingsSeam) -> bool:
    return bool(await settings.get_app(SCAN_KEY))


async def strategies_for(settings: SettingsSeam, subject: Subject) -> dict[str, Strategy]:
    """The rule somebody chose for each field of one subject.

    Read by the key the registry builds, so a field added later gets its rule without this function
    knowing it exists. A value that is not one of the three (a database edited by hand, or a
    setting from a newer version) falls back to the default rather than raising: an unreadable
    preference must not stop an import, and the default is the one that cannot lose anything.
    """
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
    """One item of a batch going wrong, counted and carried rather than ending the job.

    Both jobs below walk a list of separate subjects and ask the same question of each. One of them
    raising is a fact about that subject, not about the list, and left to itself the exception
    comes out of the loop, so everything after it is never asked and nobody is told which. That
    would cost a batch most of its people (see `enrich_entities`) and a once-only pass its one run
    (see `link_what_boxes_invented`).

    The two loops share this rather than a copy each, because the part worth getting right is not
    the `try`: it is the THREE things that go with it, and a second copy is where one of them
    goes missing. The failure is logged with the identifiers of whatever it happened to, because a
    note can only carry a count and the next question is always which one; it is counted, because a
    pass that swallows failures silently is a pass that reports a clean run it did not have; and
    `JobBlocked` is re-raised, because it means the keys are sealed (true of every subject and of
    none of them in particular), and the kernel parks the whole job for it at no cost to its
    attempts.
    """
    try:
        yield
    except JobBlocked:
        raise
    except Exception:
        log.exception(event, **about)
        tally[Outcome.FAILED] += 1


async def enrich_entities(context: JobContext, *, entities: EntityEnricher) -> None:
    """Ask the stash-boxes about a batch of people, sites or tags.

    Queued rather than done in the request, because a batch is one third-party request per subject
    paced at a few a second: forty people is most of a minute, and a screen that waits for it is a
    screen that looks broken. What it writes is small and what it decides is nothing. See
    `EntityEnricher.enrich` for the rule.

    One job for the batch and not one per subject. The pacing is the adapter's and is per box, so
    splitting them would queue forty jobs that spend their lives waiting on the same limiter.

    A subject that has gone since the job was queued is skipped rather than failing the batch: this
    runs unattended, and the batch is worth finishing.

    So is one that goes wrong. A batch is separate questions about separate subjects, and one of
    them raising is a fact about that subject; ending the loop there would leave the rest unasked
    and unnamed. The failure is counted like any other outcome, said in the note, and the loop
    carries on.

    `JobBlocked` is the exception that must not be swallowed: it means the keys are sealed, which
    is true of every subject in the batch and not of any one of them, and the kernel parks the
    whole job for it rather than costing it an attempt.
    """
    key = context.payload.get("key")
    master_key = bytes.fromhex(str(key)) if key else None
    # Named by whoever queued it: the flyout on the verb, or the setting the route read. Read
    # off the payload rather than from Settings here, for the reason the sweep carries it: one
    # press is one decision, and a setting changed while a batch of forty runs must not split it.
    only = str(context.payload.get("box") or "") or None
    tally: Counter[Outcome] = Counter()
    for one in context.payload.get("subjects") or []:
        if not isinstance(one, dict):
            continue
        # A queued job's payload says `site`. The older word a job queued before the rename
        # carried is no longer read: every such job ran long ago, and one that did not is a row
        # skipped here like any other unknown subject.
        try:
            subject = Subject(str(one.get("subject")))
        except ValueError:
            continue
        local_id, name = str(one.get("id") or ""), str(one.get("name") or "")
        if not local_id or not name:
            continue
        # THE BOX'S OWN ID, where the answer that named this row gave one: a confirmed page links
        # what it named by that id rather than by asking about the name (see `link_invented`).
        # Only for the box this batch may ask, so a press that named one box asks no other.
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
    """What the batch came to, in a sentence somebody reading the job list can act on.

    Without it, pressing Auto-enrich on a Site with twenty possible matches would produce a green
    job, an unchanged record and no explanation anywhere. The job list is where the toast sends
    people; arriving there to find a finished job saying nothing is worse than not looking.

    Each clause earns its place by leading somewhere different. Filled in is done. More than one
    match is an invitation to press Enrich and choose. Nobody has heard of them is neither: it
    usually means the name here is not the name the boxes file them under, which is a rename or an
    alias rather than anything this job can do.
    """
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
        # Last, and it says where to look rather than what went wrong: what went wrong is a
        # traceback, and a traceback in a job's note is not something anybody can act on.
        said.append(f"{tally[Outcome.FAILED]} went wrong \u2014 the log says which")
    return ", ".join(said) if said else "Nothing to ask about."


async def link_what_boxes_invented(
    context: JobContext, *, service: StashBoxService, entities: EntityEnricher
) -> None:
    """Link every person and Site a box invented to that box, where it was never done. Once.

    ## What was left behind

    A row a box's answer creates is linked to that box by the box's own id the moment it is made
    (`link_invented`). Rows created by an older version were left a bare name, with no link to the
    box that made them. The record of the creation is `people.created_by_box_id`; the box's id for
    the person was never kept, so each is asked for by name, of that box and no other, through the
    one path every unattended enrichment takes (`EntityEnricher.enrich`). Exactly one entry links
    them; two or none are left for the chooser, and the note says how many of each.

    ## Why once, and why it is still safe to run twice

    Sites are the second half, asked the same way and remembered apart (`invented_sites_pass`): a
    studio or network named before its id was kept wears a letter until its box is asked.

    Remembered in `stash_box_catch_ups`, one row per box (`invented_pass`), because a person
    somebody has since taken off that box is a decision, and a pass that came back every sweep would
    put the link back. Run twice before that row is written, it asks only about those still
    unlinked, so nobody is linked twice.

    A box that cannot be asked (switched off, or one Sift has no word for, which cannot be asked
    alone) is not asked and not remembered: its people are counted as waiting and the pass comes
    back for them after a sweep with that box switched on (`inventions_owed`). Sealed keys park the
    whole pass (`JobBlocked`) without recording it.
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
        # A box that failed on any of these is not remembered, so the pass asks it again next time
        # rather than filing what it could not answer as done.
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
        # `entities` so an exact match applied here links the rows it invents. See `_apply`.
        lambda context: scan(context, service=service, deps=deps, entities=entities),
        name="Asking the stash-boxes about a file",
    )
    register_handler(
        STASH_SWEEP,
        lambda context: sweep(context, service=service, deps=deps),
        name="Looking for files the stash-boxes know",
        # One at a time, because the sweep is asked for by a settle as well as by a press: two of
        # it walking the same library queue the same questions twice, and the settle's shape is
        # one running, at most one waiting, everything else collapsing onto that one.
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
