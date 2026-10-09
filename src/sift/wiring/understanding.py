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
from sift.slices.faces.store_left_out import LeftOutStore
from sift.slices.swap.landed_folders import folders_made
from sift.wiring.built import Storage, Understanding
from sift.wiring.stash_doors import stash_doors

log = get_logger(__name__)


class _PhotoSetMaker:
    """Making a Photo Set for a feature that must not know how, through the folder derivation."""

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
    """The seam's implementation, read off the application late rather than captured."""
    return _PhotoSetMaker(app)


def _post_sets(app: FastAPI, store: Storage) -> suggestions.PostSets:
    """Making and unmaking the set a post of pictures deserves, for the filename pass."""

    async def derive(asset_ids: Sequence[str], name: str) -> suggestions.MadeSet | None:
        made = await photo_sets.set_from_post(
            asset_ids,
            name=name,
            content=store.content,
            service=wiring.part_of_app(app, photo_sets.SERVICE),
        )
        # The set's name crosses as a string, so the receipt snapshots what it was called.
        return None if made is None else suggestions.MadeSet(id=made.id, name=made.name)

    async def forget(photo_set_id: str, by: Viewer) -> None:
        # The Undo's own presser, never Sift: see `suggestions.PostSets.forget`.
        await wiring.part_of_app(app, photo_sets.SERVICE).delete(
            photo_set_id, actor=Actor.user(by.id)
        )

    return suggestions.PostSets(derive=derive, forget=forget)


def _picture_fields(store: Storage, settings: Settings) -> suggestions.PictureFields:
    """Opening one file to read two of its fields, for the filename pass; empty if unreadable."""

    async def read(asset_id: str) -> suggestions.PictureMetadata:
        try:
            source = await media.resolve_decodable(store.content, asset_id, settings=settings)
        except media.NoReadableCopy:
            return suggestions.PictureMetadata()
        return await suggestions.read_picture_fields(source.path, settings=settings)

    return suggestions.PictureFields(read=read)


def _swap_folders(store: Storage) -> suggestions.SwapFolders:
    """The folders a swap made, for the folder pass, as two sets of folder ids."""

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
    # Recognizing faces, off until turned on; handed the access repository so crops obey file rules.
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
    # A stash-box's pictures of somebody, for the face feature's starter references.
    box_pictures = stash_boxes.StarterPictures(wiring.part_of_app(app, stash_boxes.SERVICE))
    provide(app, wiring.BOX_PICTURES, box_pictures)
    faces.register_handlers(
        service=face_service,
        door=box_pictures,
        # A full regroup gives rebuilt groups new ids; the folder pass proposes them again.
        regroup_settles_into=(suggestions.SUGGESTION_SCAN,),
        left_out=LeftOutStore(store.database),
    )
    faces.register_agreeing(face_service)
    # A person created under a name a pack knew gets its faces; "nobody waiting" while off.
    provide(app, wiring.RECOGNITION, faces.Recognition(face_service, queue=queue))
    # Recognition as the folder reader's seam: counts per folder, and the one write naming a group.
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
    # Reading the folder tree, given the resolver: the library holds concealed things.
    folder_suggestions = suggestions.SuggestionService(
        store=suggestions.Store(store.database),
        access=store.access,
        faces=face_evidence,
        # Read per pass, so a switch moved a minute ago is honoured.
        preferences=hub,
        recorder=workbench_store,
        # A folder answer that files pictures under somebody is an input to the shoots pass.
        after_filing=partial(queue.settle_into, [shoots.SHOOTS_LOOK]),
        # The Photo Set a post deserves; no switch of its own, as the pass already has one.
        sets=_post_sets(app, store),
        # The fields naming a Site's username number; this half opens files, so has its own switch.
        pictures=_picture_fields(store, settings),
        # The folders a swap made, which are containers and never one person's.
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
    # Each area says what it has waiting, so a new panel is one line here.
    board.register(suggestions.FolderQueue(folder_suggestions))
    board.register(suggestions.FiledQueue(folder_suggestions))
    # What a file's own name said about where it came from: a report on its own page.
    board.register(suggestions.FiledFromFilenamesQueue(folder_suggestions))
    # Pairs that look alike, counted at the same closeness setting the screen reads.
    board.register(dedup.DedupQueue(wiring.part_of_app(app, dedup.SERVICE), store.access, hub))
    # The same file in two places: not a judgement, but still nobody else's decision.
    board.register(dedup.ReclaimQueue(wiring.part_of_app(app, dedup.SERVICE), store.access))
    # A reverser with no card, for an attribution carried onto a file's copies.
    board.register_reverser(
        dedup.CarriedAttributions(wiring.part_of_app(app, dedup.SERVICE), store.access)
    )
    # What the gate on the way in would not take, in its two piles.
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
    # The faces page; registration order is tab order (see `Workbench.register`).
    board.register(faces.SuggestionsQueue(face_service))
    board.register(faces.DisagreementsQueue(face_service))
    board.register(faces.ToNameQueue(face_service))
    board.register(faces.SetAsideQueue(face_service))
    board.register(faces.PeopleKnownQueue(face_service))
    # The names those four wrote receipts under, kept reversible after the cards go.
    board.register_reverser(faces.IgnoredRecords(face_service))
    board.register_reverser(faces.IdentifiedRecords(face_service, queue=queue))
    board.register_reverser(faces.AskedOnlyRecords())
    board.register_reverser(faces.RetiredPickRecords())
    board.register_reverser(faces.BoxQuestionRecords())
    board.register_reverser(faces.StarterRecords(face_service))
    board.register_reverser(faces.FingerprintRecords(face_service))
    board.register_reverser(faces.RemovedFingerprintRecords())


def _register_stash_piles(app: FastAPI, store: Storage, board: Workbench) -> None:
    # Files the stash-boxes recognised, beside the face service that turns a user id into a viewer.
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
    # Studios a box made Sites of that may be one person's own store.
    board.register(
        stash_boxes.StudioQueue(stash_boxes.CreatorStudios(store.database), store.access)
    )
    # Where two answers about one field disagree: drawn on the record, not queued on the board.
    reconciler = stash_boxes.Reconciler(
        stash_service,
        wiring.part_of_app(app, wiring.ENRICHER),
        wiring.part_of_app(app, wiring.SETTINGS_HUB),
        store.access,
    )
    provide(app, stash_boxes.RECONCILER, reconciler)
    # The same object under `DisagreementSeam`, the one verb the tab strip may reach.
    provide(app, wiring.DISAGREEMENTS, reconciler)
    # No card, but receipts from when there was one stay reversible.
    board.register_reverser(stash_boxes.ReconcileReceipts(reconciler))


def _subject_covers(app: FastAPI) -> SubjectCovers:
    # A fetched picture as a subject's cover, through each slice's own two cover verbs.
    subject_covers = SubjectCovers(wiring.part_of_app(app, wiring.COVER_PICTURES))
    people_service = wiring.part_of_app(app, people.SERVICE)
    tag_service = wiring.part_of_app(app, tags_ratings.SERVICE)
    subject_covers.register(
        Subject.PERSON,
        CoverHandle(
            chosen=partial(people_service.chosen_cover, "person"),
            # Who did it and which box it came from both go onto the event.
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
    # The enrichers of people, Sites and Tags, beside the reconciler that uses the same three things.
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
    # Also not a queue: the walls already offer Enrich and a "Never asked" filter.
    stash_boxes.register_handlers(
        service=stash_service,
        deps=stash_boxes.ScanDeps(
            access=store.access,
            settings=wiring.part_of_app(app, wiring.SETTINGS_HUB),
            enricher=wiring.part_of_app(app, wiring.ENRICHER),
            # Reached from the wiring: the one the rest of the application already uses.
            naming=wiring.part_of_app(app, wiring.NAMING),
            viewer_for=face_service.viewer_for,
        ),
        entities=entity_enricher,
    )
    # Bringing a Stash library in, through the same writers, lookups and Photo Set door.
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
    # Searching by appearance: making the store reads and creates nothing, and absence is reported.
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
        # The cheap tier of "what looks like this", via the content layer's fingerprint interface.
        similar=semantic.SimilarFinder(DuplicateReads(store.database), store.database),
        preferences=hub,
        settings=settings,
        hardware=hardware,
    )
    provide(app, semantic.SERVICE, meaning)
    # The HEIF photographs described from one tile of the grid, for a start to ask about.
    tiles = semantic.WholePicture(content=store.content, records=described)
    provide(app, semantic.WHOLE_PICTURE, tiles)
    # A file described on arrival asks for the shoots pass again.
    semantic.register_handlers(service=meaning, tiles=tiles, settles_into=(shoots.SHOOTS_LOOK,))
    # Asking a model what words mean, for a search box that must not know how.
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
    # Reading a site's mark off the picture, off until switched on; receipts in one transaction.
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
    # Proposing Photo Sets from a creator's loose pictures; set and receipt in one transaction.
    proposer = shoots.ShootService(
        database=store.database,
        store=shoots.Store(store.database),
        access=store.access,
        semantic=wiring.part_of_app(app, wiring.SEMANTIC_SEARCH),
        photo_sets=_photo_set_maker(app),
        preferences=hub,
        recorder=workbench_store,
        # The Photo Sets' floor, handed across so no second copy of the number can drift.
        least=photo_sets.MIN_PICTURES,
        # The longest Photo Set name, crossed the same way.
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
    """Everything that reads a library and forms an opinion, and the board, built first."""
    face_service, face_evidence = _build_faces(
        app, settings, hardware, store, hub, workbench_store, queue
    )
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
