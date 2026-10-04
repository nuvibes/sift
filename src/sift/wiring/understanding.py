# SPDX-License-Identifier: AGPL-3.0-or-later
"""The passes that read a library and form an opinion about it, and the board they report to."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from fastapi import FastAPI

from sift.kernel import media, wiring
from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.content import DuplicateReads
from sift.kernel.covers import CoverHandle, SubjectCovers
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobQueue
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.records import Subject
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.kernel.wiring import provide
from sift.kernel.workbench import Workbench
from sift.slices import (
    dedup,
    faces,
    library_roots,
    people,
    photo_sets,
    semantic,
    settings_hub,
    shoots,
    stash_boxes,
    stash_migration,
    suggestions,
    tags_ratings,
    watermarks,
    workbench,
)
from sift.slices.swap.landed_folders import folders_made
from sift.wiring.built import Storage, Understanding
from sift.wiring.stash_doors import stash_doors

log = get_logger(__name__)


class _PhotoSetMaker:
    """Making a Photo Set for a feature that must not know how one is made.

    `PhotoSetSeam`, filled in here, which is the only place the feature that PROPOSES a grouping and
    the feature that OWNS Photo Sets are named together. What it reaches for is the derivation a
    folder of pictures already goes through (the same guards, the same cover, the same order), so
    there is one way of creating a set and not two.
    """

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def _service(self) -> photo_sets.PhotoSetService:
        return wiring.part_of_app(self._app, photo_sets.SERVICE)

    async def make(
        self, asset_ids: Sequence[str], *, name: str, origin: str = "shoot"
    ) -> str | None:
        derive = (
            photo_sets.set_from_stash_library
            if origin == VIA_STASH_LIBRARY
            else photo_sets.set_from_shoot
        )
        return await derive(
            asset_ids,
            name=name,
            content=wiring.part_of_app(self._app, wiring.CONTENT),
            service=self._service(),
        )

    async def holding(self, asset_ids: Sequence[str]) -> str | None:
        return await self._service().holding(asset_ids)

    async def forget(self, photo_set_id: str, *, by: Viewer) -> None:
        # The Undo's own presser, never Sift: see `PhotoSetSeam.forget`.
        await self._service().delete(photo_set_id, actor=Actor.user(by.id))


def _photo_set_maker(app: FastAPI) -> _PhotoSetMaker:
    """The seam's implementation, read off the application rather than captured.

    Read late for the reason the gallery grouper reads its service late: the service is provided
    while the application is being assembled, and capturing it here would bind whatever happened to
    exist at the moment this was built.
    """
    return _PhotoSetMaker(app)


def _post_sets(app: FastAPI, store: Storage) -> suggestions.PostSets:
    """Making and unmaking the set a post of pictures deserves, for the filename pass.

    `set_from_post` is the same body a shoot goes through (`MIN_PICTURES` at least, every arrival a
    still, the first as the cover), with its own `origin` word so the set says on its own page that
    it was made from the files' own names.

    `delete` is what the Undo on the set's own decision reaches. It removes the grouping and
    nothing else: the pictures keep every filing, tag and person they had.
    """

    async def derive(asset_ids: Sequence[str], name: str) -> suggestions.MadeSet | None:
        made = await photo_sets.set_from_post(
            asset_ids,
            name=name,
            content=store.content,
            service=wiring.part_of_app(app, photo_sets.SERVICE),
        )
        # The set's own name, carried across the seam, because the receipt the pass writes
        # snapshots what the set was CALLED. Neither slice imports the other's types, so the
        # crossing is two strings: see `suggestions.MadeSet`.
        return None if made is None else suggestions.MadeSet(id=made.id, name=made.name)

    async def forget(photo_set_id: str, by: Viewer) -> None:
        # The Undo's own presser, never Sift: see `suggestions.PostSets.forget`.
        await wiring.part_of_app(app, photo_sets.SERVICE).delete(
            photo_set_id, actor=Actor.user(by.id)
        )

    return suggestions.PostSets(derive=derive, forget=forget)


def _picture_fields(store: Storage, settings: Settings) -> suggestions.PictureFields:
    """Opening one file and reading two of its fields, for the filename pass.

    A closure for the reason `_post_sets` above is one: resolving which copy of a file is readable
    and where it is on disk is the content store's question, and the slice that reads filenames must
    not learn it. What crosses the seam is an asset id and two strings.

    A file that cannot be opened (a share that is offline, a copy that has gone) answers with
    both fields empty, which is already the ordinary answer: most pictures in any library carry
    none of these. See `suggestions.metadata` for why it is exactly two fields and no more.
    """

    async def read(asset_id: str) -> suggestions.PictureMetadata:
        try:
            source = await media.resolve_decodable(store.content, asset_id, settings=settings)
        except media.NoReadableCopy:
            return suggestions.PictureMetadata()
        return await suggestions.read_picture_fields(source.path, settings=settings)

    return suggestions.PictureFields(read=read)


def _swap_folders(store: Storage) -> suggestions.SwapFolders:
    """The folders a swap made, for the folder pass. A closure for the reason `_post_sets` is one:
    the swap's record is that slice's rows, and the folder reader may not import it. What crosses
    the seam is two sets of folder ids."""

    async def made() -> suggestions.Arrivals:
        found = await folders_made(store.database)
        return suggestions.Arrivals(containers=found.containers, by_name=found.people)

    return suggestions.SwapFolders(made=made)


def _build_faces(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    hub: settings_hub.SettingsService,
    workbench_store: workbench.Store,
    queue: JobQueue,
) -> tuple[faces.FaceService, faces.FaceEvidence]:
    reindexer = wiring.part_of_app(app, wiring.REINDEXER)
    # Recognizing faces. Off until somebody turns it on: constructing this loads no model and
    # reads no file; the first thing every one of its jobs does is ask whether the feature is on.
    # It is handed the access repository rather than a database handle because a face crop is a
    # fragment of the file it came from, so serving one has to ask the same question the file does,
    # of the same rule.
    face_store = faces.Store(store.database, data_dir=settings.data_dir)
    face_service = faces.FaceService(
        store=face_store,
        content=store.content,
        repository=store.access,
        preferences=hub,
        settings=settings,
        hardware=hardware,
        reindexer=reindexer,
        recorder=workbench_store,
    )
    provide(app, faces.SERVICE, face_service)
    # A stash-box's pictures of somebody linked to one, through that feature's own door, for the
    # starter references the face feature files from them. The stash-box service is built with
    # the downloads, earlier, so the door exists here; published for the Faces pane's count.
    box_pictures = stash_boxes.StarterPictures(wiring.part_of_app(app, stash_boxes.SERVICE))
    provide(app, wiring.BOX_PICTURES, box_pictures)
    faces.register_handlers(
        service=face_service,
        door=box_pictures,
        # A full regroup gives rebuilt groups new ids; the folder pass proposes them again.
        regroup_settles_into=(suggestions.SUGGESTION_SCAN,),
    )
    # Creating a person with a name a pack already knew hands them the faces it was holding. Wired
    # here because the slice that creates a person must not know recognition exists, and wrapped,
    # because with the feature switched off the service refuses rather than answering, and "nobody
    # was waiting" is the right answer to give somebody who has simply not turned it on.
    provide(app, wiring.RECOGNITION, faces.Recognition(face_service, queue=queue))
    # And the same slice again, wearing the face a FOLDER READER asks it questions in: counts of
    # what its groups say about a folder, and the one write that names a whole group. A seam for
    # the reason the one above is one: the feature that reads folder names for people must not
    # know recognition exists, and on an install with it switched off every answer is empty and a
    # folder is judged on its name alone.
    #
    # Handed the service and the queue as well, because a folder answer that names a group has to
    # teach from it afterwards (references, remembered decisions, a re-match) through the same
    # path a naming press takes, and the store for the proposals the folder reader hands across.
    face_evidence = faces.FaceEvidence(
        store.database, preferences=hub, store=face_store, teacher=face_service, queue=queue
    )
    provide(app, wiring.FACE_EVIDENCE, face_evidence)
    return face_service, face_evidence


def _build_suggestions(
    app: FastAPI,
    settings: Settings,
    store: Storage,
    hub: settings_hub.SettingsService,
    workbench_store: workbench.Store,
    queue: JobQueue,
    face_evidence: faces.FaceEvidence,
) -> suggestions.SuggestionService:
    # Reading the folder tree somebody already organised, and asking one short question per folder.
    # It is handed the resolver rather than a database handle because every row it puts on a screen
    # has to be resolved against the user reading it: suggestions are generated from the whole
    # library, and the library contains concealed things.
    folder_suggestions = suggestions.SuggestionService(
        store=suggestions.Store(store.database),
        access=store.access,
        faces=face_evidence,
        # Read per pass, not per boot: the half of this pass that reads FILENAMES has a switch on
        # the Importing screen, and a scan started a minute after somebody moved it honours it.
        preferences=hub,
        recorder=workbench_store,
        # A folder answer that files pictures under somebody is an input to the shoots pass.
        after_filing=partial(queue.settle_into, [shoots.SHOOTS_LOOK]),
        # And the Photo Set a post of enough pictures deserves (`kernel.photo_sets.MIN_PICTURES`).
        # Bound here for the reason the download grouper is: the filename reader knows which files
        # were posted together and the photo-set slice knows what a set is, and neither may import
        # the other. It carries NO switch of its own. The pass that makes these has one on the
        # Importing screen and this is that pass; the switch on the Downloads screen governs what a
        # FETCH does and reusing it here would be one control silently turning off something nobody
        # asked it about.
        sets=_post_sets(app, store),
        # And the two fields of a picture, for the one question a filename cannot answer on its own:
        # what the Site's own username number in it is CALLED. Bound here for the reason the sets
        # are (the pass must not learn how a file is opened), and it carries its own switch on
        # the Importing screen, because it is the only half of this pass that opens a file at all.
        pictures=_picture_fields(store, settings),
        # And the folders a swap made, which are containers of what arrived and never one
        # person's, whatever their faces say. Read from the swap's own record of where it put them.
        swap_folders=_swap_folders(store),
    )
    provide(app, suggestions.SERVICE, folder_suggestions)
    suggestions.register_handlers(service=folder_suggestions)
    return folder_suggestions


def _register_piles(
    app: FastAPI,
    settings: Settings,
    store: Storage,
    hub: settings_hub.SettingsService,
    board: Workbench,
    folder_suggestions: suggestions.SuggestionService,
) -> None:
    reindexer = wiring.part_of_app(app, wiring.REINDEXER)
    # Each area says what it has waiting. The workbench knows nothing about folders or faces, so a
    # panel added in a later version is one line here and no edit anywhere in the shell.
    board.register(suggestions.FolderQueue(folder_suggestions))
    board.register(suggestions.FiledQueue(folder_suggestions))
    # And what a file's OWN NAME said about where it came from: a report rather than a question,
    # standing alone rather than as a tab of the folders page: those files were filed under a site
    # with nobody named, which is not an alternative reading of a folder claim about a person.
    board.register(suggestions.FiledFromFilenamesQueue(folder_suggestions))
    # Pairs that look alike, which is a judgement and not housekeeping: the reclaim view next to
    # it is identical bytes and asks nothing. Handed the preferences because how close two files
    # have to be is a setting, and the card must count at the same setting the screen behind
    # it reads at.
    board.register(dedup.DedupQueue(wiring.part_of_app(app, dedup.SERVICE), store.access, hub))
    # And the same file in two places at once. Not a judgement (identical bytes are identical),
    # but still nobody else's decision: a second copy on a second disk may be exactly what somebody
    # wanted.
    board.register(dedup.ReclaimQueue(wiring.part_of_app(app, dedup.SERVICE), store.access))
    # And a reverser with no card: an attribution carried onto the copies of a file is offered on
    # the group somebody is already looking at, so there is no pile to survey, only the other half
    # of a workbench queue, which is being able to take one back, per file.
    board.register_reverser(
        dedup.CarriedAttributions(wiring.part_of_app(app, dedup.SERVICE), store.access)
    )
    # What the gate on the way in would not take, in the two piles it falls into. Handed the
    # preferences because the card says how long a quarantined file is kept, and that is a dial.
    board.register(library_roots.QuarantineQueue(settings, hub))
    board.register(library_roots.SkippedQueue(wiring.part_of_app(app, library_roots.SERVICE)))
    board.register(
        people.UsernameQueue(
            wiring.part_of_app(app, people.SERVICE), store.access, touched=reindexer.touched_many
        )
    )


def _register_face_piles(
    board: Workbench, face_service: faces.FaceService, queue: JobQueue
) -> None:
    # The faces: one page with five tabs, registered in the order the row draws them. The people
    # Sift is proposing lead, because one press there settles the most and teaches Sift the most;
    # the names a pass filed that the face disagrees with come next, because each of those is an
    # answer already in the library and wrong; then the groups nobody has named, then the groups put
    # aside, then the record of who Sift knows. Registration order IS tab order (see
    # `Workbench.register`), so this list is the row on screen.
    board.register(faces.SuggestionsQueue(face_service))
    board.register(faces.DisagreementsQueue(face_service))
    board.register(faces.ToNameQueue(face_service))
    board.register(faces.SetAsideQueue(face_service))
    board.register(faces.PeopleKnownQueue(face_service))
    # And the two names those four wrote their receipts under, which outlive the cards. A decision
    # is on disk for as long as the library is, so retiring a pile must not retire the only thing
    # that knows how to take its decisions back: see `Workbench.register_reverser`.
    board.register_reverser(faces.IgnoredRecords(face_service))
    board.register_reverser(faces.IdentifiedRecords(face_service, queue=queue))
    board.register_reverser(faces.AskedOnlyRecords())
    board.register_reverser(faces.BoxQuestionRecords())
    board.register_reverser(faces.StarterRecords(face_service))
    board.register_reverser(faces.FingerprintRecords(face_service))
    board.register_reverser(faces.RemovedFingerprintRecords())


def _register_stash_piles(app: FastAPI, store: Storage, board: Workbench) -> None:
    # Files the stash-boxes recognised, and the two jobs that find them. Registered here beside the
    # other queues rather than where the service is built, because the sweep is scoped to whoever
    # asked for it, and the only thing that can turn a stored user id back into a viewer is the
    # face service's lookup, which is the one place that rule is written down.
    stash_service = wiring.part_of_app(app, stash_boxes.SERVICE)
    board.register(
        stash_boxes.TaggerQueue(
            stash_service,
            store.access,
            writer=lambda: stash_boxes.file_writer(
                wiring.part_of_app_or_none(app, wiring.ENRICHER)
            ),
        )
    )
    board.register(stash_boxes.LinkedQueue(stash_service, store.access))
    board.register(stash_boxes.UndecidedQueue(stash_service, store.access))
    # The studios a box made Sites of that may be one person's own store: asked on a tab of the
    # same page, and every move of one (the catalog's own and a person's) taken back there.
    board.register(
        stash_boxes.StudioQueue(stash_boxes.CreatorStudios(store.database), store.access)
    )
    # Where two answers about one field disagree. Built here rather than beside the service, because
    # it needs the writers and the per-field rules as well as the boxes: three things from three
    # places, which is exactly what a composition root is for.
    #
    # NOT a queue on the board, deliberately. A disagreement is a property of ONE RECORD (this
    # person's birthday, this site's address), so a queue of unrelated fields asks somebody to
    # answer them away from the only thing that settles them. It is drawn on the record instead, and
    # in full under Settings > Stash-boxes.
    reconciler = stash_boxes.Reconciler(
        stash_service,
        wiring.part_of_app(app, wiring.ENRICHER),
        wiring.part_of_app(app, wiring.SETTINGS_HUB),
        store.access,
    )
    provide(app, stash_boxes.RECONCILER, reconciler)
    # And the same object under the shape the RELATED slice depends on. Two names for one thing,
    # deliberately: the stash-box routes use the whole reconciler, and the tab strip needs one verb
    # from it, so the strip depends on `DisagreementSeam` and cannot reach anything else here. A
    # slice may not import another, and this is the boundary that keeps that true.
    provide(app, wiring.DISAGREEMENTS, reconciler)
    # No card, but the receipts written while there WAS one are still on disk and are still
    # reversible. See `ReconcileReceipts` and `kernel.workbench.Reverser`.
    board.register_reverser(stash_boxes.ReconcileReceipts(reconciler))


def _subject_covers(app: FastAPI) -> SubjectCovers:
    # A fetched picture as a subject's COVER: the column every wall draws a person, a site or a
    # tag through. Each slice hands in its own two cover verbs here, because the kernel may not
    # import a slice and the enrichment may not import three; see `SubjectCovers`.
    subject_covers = SubjectCovers(wiring.part_of_app(app, wiring.COVER_PICTURES))
    people_service = wiring.part_of_app(app, people.SERVICE)
    tag_service = wiring.part_of_app(app, tags_ratings.SERVICE)
    subject_covers.register(
        Subject.PERSON,
        CoverHandle(
            chosen=partial(people_service.chosen_cover, "person"),
            # WHO did it and WHICH box the picture came from are the caller's to say, and both go
            # onto the event: a run nobody watched is Sift's act, a Keep picture press is the
            # user's, and the line names the box either way ("Cover set to FansDB's picture").
            # See `SubjectCovers.fill` / `keep` and `EntityEnricher._kept_picture`.
            point_at=lambda local_id, upload_id, actor, box: people_service.set_person_cover(
                local_id, None, None, upload_id, actor=actor, box=box
            ),
        ),
    )
    subject_covers.register(
        Subject.SITE,
        CoverHandle(
            chosen=partial(people_service.chosen_cover, "site"),
            point_at=lambda local_id, upload_id, actor, box: people_service.set_site_cover(
                local_id, None, None, upload_id, actor=actor, box=box
            ),
        ),
    )
    subject_covers.register(
        Subject.TAG,
        CoverHandle(
            chosen=tag_service.chosen_cover,
            point_at=lambda local_id, upload_id, actor, box: tag_service.set_cover(
                local_id, None, None, upload_id, actor=actor, box=box
            ),
        ),
    )
    provide(app, wiring.SUBJECT_COVERS, subject_covers)
    return subject_covers


def _build_stash_entities(
    app: FastAPI, settings: Settings, store: Storage, face_service: faces.FaceService
) -> None:
    stash_service = wiring.part_of_app(app, stash_boxes.SERVICE)
    # Enriching the PEOPLE, SITES and TAGS rather than the files. Built here beside the reconciler
    # because it uses the same three things (the boxes, the planner and the rules), and this is
    # the only place any of them are named together.
    subject_covers = _subject_covers(app)
    entity_enricher = stash_boxes.EntityEnricher(
        stash_service,
        store.access,
        wiring.part_of_app(app, wiring.ENRICHER),
        wiring.part_of_app(app, wiring.SETTINGS_HUB),
        subject_covers,
        # So a person linked here is heard of by the face feature (starter pictures).
        naming=wiring.part_of_app(app, wiring.NAMING),
        recognition=wiring.part_of_app(app, wiring.RECOGNITION),
    )
    provide(app, stash_boxes.ENTITIES, entity_enricher)
    # Also not a queue on the board. Auto-enrich and Enrich are already verbs on the People, Sites
    # and Tags walls, over a selection and one at a time, and the walls say the pile exists with a
    # "Never asked" filtering of their own.
    stash_boxes.register_handlers(
        service=stash_service,
        deps=stash_boxes.ScanDeps(
            access=store.access,
            settings=wiring.part_of_app(app, wiring.SETTINGS_HUB),
            enricher=wiring.part_of_app(app, wiring.ENRICHER),
            # The seam is reached from the wiring rather than held as a local, like the two
            # above it: this function does not build it, and what it wants is the one the rest of
            # the application already uses.
            naming=wiring.part_of_app(app, wiring.NAMING),
            viewer_for=face_service.viewer_for,
        ),
        entities=entity_enricher,
    )
    # Bringing a Stash library in: the same writers a stash-box answer is applied through (filing
    # under the import's own word), the same lookups, and the one door a Photo Set is made by.
    migration = stash_migration.StashMigration(
        store.database,
        settings,
        store.library,
        stash_boxes.filing_as(wiring.part_of_app(app, wiring.ENRICHER), VIA_STASH_LIBRARY),
        wiring.part_of_app(app, wiring.NAMING),
        wiring.part_of_app(app, wiring.USER_STATE),
        _photo_set_maker(app),
        content=store.content,
        doors=stash_doors(app),
    )
    provide(app, stash_migration.SERVICE, migration)
    stash_migration.register_stash_handlers(migration)


def _build_semantic(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    hub: settings_hub.SettingsService,
) -> semantic.SemanticService:
    # Searching by what a picture looks like. The store is made here and nothing is read, written
    # or created by making it. The vector table itself is not built until something genuinely
    # needs it, because the add-on it depends on is one some machines cannot load and Sift has to
    # boot on those. Whether it loaded was settled when the database was opened; this only reports
    # it, so an operator can see the feature is absent and why without going looking.
    vectors = semantic.VectorStore(store.database)
    provide(app, semantic.STORE, vectors)
    if not vectors.available:
        log.info("semantic.unavailable", reason="the vector add-on did not load")
    described = semantic.Records(store.database)
    meaning = semantic.SemanticService(
        store=vectors,
        records=described,
        content=store.content,
        repository=store.access,
        # The cheap tier of "what looks like this": the fingerprints every file already carries,
        # read through the one interface the content layer offers for them. Handed in rather than
        # reached for, which is what keeps this feature unable to read those tables any other way.
        similar=semantic.SimilarFinder(DuplicateReads(store.database)),
        preferences=hub,
        settings=settings,
        hardware=hardware,
    )
    provide(app, semantic.SERVICE, meaning)
    # The HEIF photographs described from one tile of the grid, for a start to ask about.
    tiles = semantic.WholePicture(content=store.content, records=described)
    provide(app, semantic.WHOLE_PICTURE, tiles)
    # A file described on arrival is an input to the shoots pass, which the scan's own settle
    # has already run by then: the describe asks for it again.
    semantic.register_handlers(service=meaning, tiles=tiles, settles_into=(shoots.SHOOTS_LOOK,))
    # Asking a model what some words mean, for a search box that must not know how. The search
    # slice depends on the shape and never on this slice; it is handed the implementation here,
    # which is the only place the two are named together.
    provide(app, wiring.SEMANTIC_SEARCH, semantic.SemanticSearch(meaning))
    return meaning


def _build_watermarks(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    hub: settings_hub.SettingsService,
    workbench_store: workbench.Store,
    board: Workbench,
) -> watermarks.WatermarkService:
    # Reading the site's own mark off the picture. Nothing is loaded or fetched by making this:
    # like the two passes above it, it does nothing at all until somebody switches it on. The board
    # is handed in as the RECORDER so a filing and the receipt that lets somebody take it back are
    # one transaction, and a reverser is registered without a card: there is no pile here to
    # survey.
    marks = watermarks.WatermarkService(
        store=watermarks.Store(store.database),
        content=store.content,
        repository=store.access,
        settings=settings,
        hardware=hardware,
        preferences=hub,
        recorder=workbench_store,
    )
    provide(app, watermarks.SERVICE, marks)
    watermarks.register_handlers(service=marks)
    board.register_reverser(watermarks.WatermarkFilings(marks))
    return marks


def _build_shoots(
    app: FastAPI,
    store: Storage,
    hub: settings_hub.SettingsService,
    workbench_store: workbench.Store,
    board: Workbench,
) -> None:
    # Proposing Photo Sets out of a creator's loose pictures: a run of stills that the meaning
    # index puts in one sitting and that nothing has grouped. Built here because it is the only
    # place that has all four of the things it needs in scope: the index (through the seam the
    # search box already depends on), the feature that owns Photo Sets (through a seam of its own,
    # so neither slice imports the other), the preferences, and the board as the RECORDER, so a
    # set and the receipt that lets somebody take it back are one transaction.
    proposer = shoots.ShootService(
        database=store.database,
        store=shoots.Store(store.database),
        access=store.access,
        semantic=wiring.part_of_app(app, wiring.SEMANTIC_SEARCH),
        photo_sets=_photo_set_maker(app),
        preferences=hub,
        recorder=workbench_store,
        # How few pictures are not a shoot, read from the feature that owns the rule. The two
        # slices may not import each other, so this is the one place that can hand the Photo Sets'
        # floor to the pass that proposes them; a second copy of the number would drift.
        least=photo_sets.MIN_PICTURES,
        # The longest name a Photo Set may carry, crossed the same way `least` is: the limit is
        # the photo-set slice's and the shoots slice may not import it.
        longest=photo_sets.MAX_PHOTO_SET_NAME,
    )
    provide(app, shoots.SERVICE, proposer)
    shoots.register_handlers(service=proposer)
    photo_sets.register_handlers(service=wiring.part_of_app(app, photo_sets.SERVICE))
    board.register(shoots.ShootQueue(proposer))


def build_understanding(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    hub: settings_hub.SettingsService,
    workbench_store: workbench.Store,
    queue: JobQueue,
) -> Understanding:
    """Everything that reads a library and forms an opinion about it, and the board it reports to.

    The board is built BEFORE anything registers into it, and handed to the areas that make
    decisions so a decision writes its own receipt inside its own transaction rather than
    afterwards, where it can be forgotten.

    The record itself arrives from the caller rather than being made here, because duplicate review
    also writes receipts and is built earlier: it needs the deleter, which belongs with the file
    actions.
    """
    face_service, face_evidence = _build_faces(
        app, settings, hardware, store, hub, workbench_store, queue
    )
    # The workbench: where every judgement a rule cannot settle waits for somebody.
    board = Workbench()
    provide(app, wiring.WORKBENCH, board)
    provide(
        app,
        workbench.SERVICE,
        workbench.WorkbenchService(store=workbench_store, workbench=board),
    )
    folder_suggestions = _build_suggestions(
        app, settings, store, hub, workbench_store, queue, face_evidence
    )
    _register_piles(app, settings, store, hub, board, folder_suggestions)
    _register_face_piles(board, face_service, queue)
    _register_stash_piles(app, store, board)
    _build_stash_entities(app, settings, store, face_service)
    meaning = _build_semantic(app, settings, hardware, store, hub)
    marks = _build_watermarks(app, settings, hardware, store, hub, workbench_store, board)
    _build_shoots(app, store, hub, workbench_store, board)
    return Understanding(faces=face_service, semantic=meaning, watermarks=marks, workbench=board)
