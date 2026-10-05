# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for songs.

The Photo Sets endpoints' four rules, for the same reasons (see `slices/photo_sets/router.py`):

**A song is shared, so editing one is admin-only.** One `songs` table for the install; renaming,
merging, deleting and putting files on one change what every user's files say. Reading is open to
any signed-in user, scoped to what they may see; the heart, the stars and the pin are each
viewer's own.

**Every read is scoped, including the numbers.** A song is seen through its files: one with no file
this viewer may see does not exist for them, and its count is of the files they may see.

**Denied and missing are the same answer.** Every refusal is the 404 an unknown id would get.

**Nothing on disk moves.** A file put on a song is one row, and its Music field follows.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    ADMIN_ENTITY_FACETS,
    ENTITY_FACETS,
    ENTITY_SORT_SEEN,
    SONG_SORT_KEYS,
    EntityNarrowing,
    ObjectType,
    Repository,
    SongView,
    Viewer,
    related_filter,
)
from sift.kernel.access.catalog import made_by
from sift.kernel.access.history import DEFAULT_LIMIT, MAX_LIMIT
from sift.kernel.access.history_entity import history_of_song
from sift.kernel.content import EntityStateStore, PinnableKind, PinView, PinWrite
from sift.kernel.covers import (
    CoverPictures,
    forget_displaced,
    receive_cover,
    serve_cover,
    upload_kept_by_put,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.paging import resume_at
from sift.kernel.reach import BulkWriteDone, require_reachable
from sift.kernel.seams import ForgetGoneSeam, ReindexSeam, StillSeam
from sift.kernel.serving import face_version
from sift.kernel.wire import (
    FacetCounts,
    FacetValue,
    HistoryEvent,
    MadeBy,
    history_event,
    made_by_wire,
)
from sift.kernel.wiring import part_of
from sift.kernel.workbench import Workbench
from sift.slices.auth import csrf_protect, current_viewer, require_admin, require_vault_pin
from sift.slices.songs.models import (
    MAX_SONG_NAME,
    ArtistCredit,
    ArtistsWrite,
    ArtistWrite,
    CoverWrite,
    FavoriteWrite,
    FilesWrite,
    MergeSongs,
    NotesWrite,
    RatingWrite,
    SongList,
    SongsMerged,
    SongStateView,
    SongSummary,
    SongWrite,
    VaultWrite,
)
from sift.slices.songs.service import SERVICE, Merged, SongService

router = APIRouter(tags=["songs"])

_NOT_FOUND = "not found"


def _service(request: Request) -> SongService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _view(song: SongView, *, art: str) -> SongSummary:
    return SongSummary(
        id=song.id,
        name=song.name,
        cover_asset_id=song.cover_asset_id,
        cover_upload_id=song.cover_upload_id,
        cover_at_ms=song.cover_at_ms,
        cover_frame=song.cover_frame,
        art=art,
        item_count=song.item_count,
        size_bytes=song.size_bytes,
        recording_id=song.recording_id,
        notes=song.notes,
        created_at=song.created_at,
        favorite=song.favorite,
        pinned=song.pinned,
        rating=song.rating,
        locked=song.locked,
        vault=song.vault,
        artists=[ArtistCredit(id=one, name=name) for one, name in song.artists],
    )


async def _require_song(access: Repository, viewer: Viewer, song_id: str) -> SongView:
    """The song, if this viewer may be shown it. 404 otherwise, either way.

    Resolved through the access layer even on admin-only writes, for the reason `_require_set` gives
    on Photo Sets: a write that could reach a song this viewer is not shown would say it is there.
    """
    song = await access.visible_song(viewer, song_id)
    if song is None:
        raise _missing()
    return song


async def _song_now(access: Repository, viewer: Viewer, song_id: str) -> SongSummary:
    """The song's own row as a write hands it back."""
    visible = await access.visible_song(viewer, song_id)
    if visible is None:
        raise _missing()  # pragma: no cover (a write to a song cannot conceal it)
    return _view(visible, art=face_version(viewer.cache_stamp))


class SongsNarrowing:
    """What the SONGS on the wall are: the artists they credit, whether they have a cover, who
    made them, and (an admin's) what has been shared and held back.

    A song has no owner and no tags of its own, so the wall has neither facet. See
    `PeopleNarrowing` in the people slice for the repeated-key rule.
    """

    def __init__(
        self,
        artists: Annotated[list[str] | None, Query()] = None,
        cover: Annotated[list[str] | None, Query()] = None,
        created: Annotated[list[str] | None, Query()] = None,
        sharing: Annotated[list[str] | None, Query()] = None,
    ) -> None:
        self.picks: dict[str, list[str] | None] = {
            "artists": artists,
            "cover": cover,
            "created": created,
            "sharing": sharing,
        }

    def of(self, viewer: Viewer) -> EntityNarrowing:
        """The picks as the one conjunct the statement takes. See `EntityNarrowing`."""
        return EntityNarrowing.of("song", self.picks, is_admin=viewer.is_admin)


@router.get("/songs")
async def list_songs(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[SongsNarrowing, Depends()],
    prefix: Annotated[str, Query(max_length=MAX_SONG_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = ENTITY_SORT_SEEN,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
    count: Annotated[Literal["whole", "narrowed"], Query()] = "whole",
) -> SongList:
    """One page of the songs this viewer may know about: the Music page, and a related list.

    The Photo Sets wall's parameters, each meaning what it means there (`list_photo_sets`): `person`,
    `tag`, `site`, `collection` and `asset` narrow to the songs those reach (`asset` is one file,
    so its song; `collection` is the Music tab of a Collection's page);
    `prefix` and `anywhere` to the names somebody is typing; `count` which tally the card prints;
    `from` and `near` the song to start the page at, a stale one serving the page it was on.
    """
    if sort not in SONG_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    narrowing = related_filter(
        person=person, tag=tag, site=site, collection=collection, asset=asset
    )
    rows = narrowed.of(viewer)
    if start is not None:
        at = await access.position_of_song(
            viewer,
            start,
            prefix,
            anywhere=anywhere,
            sort=sort,
            asset_filter=narrowing,
            narrowing=rows,
            count_narrowed=count == "narrowed",
        )
        offset = resume_at(at, near)
    page = await access.list_songs(
        viewer,
        prefix,
        anywhere=anywhere,
        limit=limit,
        offset=offset,
        sort=sort,
        asset_filter=narrowing,
        narrowing=rows,
        count_narrowed=count == "narrowed",
    )
    # What each card draws beside its name, read for this page only. See `Repository.card_counts`.
    counts = await access.card_counts(viewer, "song", [song.id for song in page.items])
    art = face_version(viewer.cache_stamp)
    return SongList(
        items=[
            _view(song, art=art).model_copy(update={"counts": counts.get(song.id, {})})
            for song in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/songs/facets")
async def song_facets(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[SongsNarrowing, Depends()],
    facet: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    prefix: Annotated[str, Query(max_length=MAX_SONG_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
) -> FacetCounts:
    """What the songs this wall reaches are made of, along one dimension, with counts.

    Declared before `/songs/{song_id}`, for the reason the Photo Sets facets route gives.
    """
    if facet not in ENTITY_FACETS["song"] or (facet in ADMIN_ENTITY_FACETS and not viewer.is_admin):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown facet {facet!r}")
    counted = await access.song_facets(
        viewer,
        facet,
        limit=limit,
        prefix=prefix,
        anywhere=anywhere,
        asset_filter=related_filter(
            person=person, tag=tag, site=site, collection=collection, asset=asset
        ),
        narrowing=narrowed.of(viewer),
    )
    return FacetCounts(
        facet=facet,
        values=[FacetValue(value=one.value, count=one.count, label=one.label) for one in counted],
    )


@router.post("/songs", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def create_song(
    body: SongWrite,
    service: Annotated[SongService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SongSummary:
    """The song a name means, made where no song carries that name yet.

    A song is identified by its name where AcoustID has not said which recording it is, so a name a
    song already carries answers that song (`SongService.create`). The answer is built from the row
    rather than read back through the access layer: a song made by hand carries no file yet, and an
    admin is the only one who may make one.
    """
    song = await service.create(body.name, by_user=viewer.id)
    return SongSummary(id=song.id, name=song.name, recording_id=song.recording_id)


@router.get("/songs/{song_id}")
async def get_song(
    song_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SongSummary:
    """One song's own row: its name, its cover, which recording it is and its scoped count, with
    this viewer's own O tally over its files (asked here and nowhere else, as a Photo Set's is)."""
    song = await _require_song(access, viewer, song_id)
    return _view(song, art=face_version(viewer.cache_stamp)).model_copy(
        update={"o_count": await access.o_count_of_song(viewer, song_id)}
    )


@router.get("/songs/{song_id}/history")
async def history_of_a_song(
    song_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    workbench: Annotated[Workbench, Depends(wiring.workbench)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[HistoryEvent]:
    """What happened to this song, oldest first. See `history_of_song`, and the Photo Sets route
    beside it for why the registry is asked which decisions can never be taken back."""
    await _require_song(access, viewer, song_id)
    final = [one.name for one in workbench.reversers if not one.reversible]
    return [
        history_event(event)
        for event in await history_of_song(
            database, viewer, song_id, limit=limit, final_queues=final, bench=workbench
        )
    ]


@router.get("/songs/{song_id}/made-by")
async def maker_of_a_song(
    song_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> MadeBy | None:
    """WHO MADE IT: Sift and the task (AcoustID, a download), the user who typed it, or nobody the
    row can say. Its own route for the reason the Photo Sets one gives."""
    await _require_song(access, viewer, song_id)
    made = await made_by(database, viewer, "song", song_id)
    return None if made is None else made_by_wire(made)


@router.put("/songs/{song_id}", dependencies=[Depends(csrf_protect)])
async def rename_song(
    song_id: str,
    body: SongWrite,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SongSummary:
    """Rename a song, and with it the Music field of every file carrying it.

    The search index IS told, unlike a Photo Set's rename: a file's song is one of its words (the
    index's `music` column), so the files carrying it are named to the index, one indexed read.
    """
    await _require_song(access, viewer, song_id)
    changed = await service.rename(song_id, body.name, actor=Actor.user(viewer.id))
    if not changed.done:
        raise _missing()  # pragma: no cover (resolved above, so the row is there)
    if changed.files:
        await reindexer.touched_many(changed.files)
    return await _song_now(access, viewer, song_id)


@router.put("/songs/{song_id}/notes", dependencies=[Depends(csrf_protect)])
async def set_song_notes(
    song_id: str,
    body: NotesWrite,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SongSummary:
    """Write the note somebody typed on a song."""
    await _require_song(access, viewer, song_id)
    await service.set_notes(song_id, body.notes, actor=Actor.user(viewer.id))
    return await _song_now(access, viewer, song_id)


@router.delete("/songs/{song_id}", dependencies=[Depends(csrf_protect)])
async def delete_song(
    song_id: str,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    forgets: Annotated[ForgetGoneSeam, Depends(wiring.forget_gone)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Delete the song. Its files stay where they are, keep their music fingerprints and lose the
    name; the index is told which files they were."""
    song = await _require_song(access, viewer, song_id)
    # Every grant naming it first, so a failure between the two leaves grants naming a song that
    # is still there rather than grants naming nothing (`CollectionService.delete` says why).
    await access.forget_object(ObjectType.SONG, song_id)
    changed = await service.delete(song_id, actor=Actor.user(viewer.id))
    if changed.files:
        await reindexer.touched_many(changed.files)
    await forgets.forget_gone("song", song_id, name=song.name, by=viewer)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _songs_going(body: MergeSongs) -> list[str]:
    """The set, with the survivor dropped rather than refused. See `_sites_going`."""
    going = [one for one in dict.fromkeys(body.songs) if one != body.into]
    if not going:
        raise HTTPException(status.HTTP_409_CONFLICT, "that's the same song")
    return going


def _merged(merged: Merged) -> SongsMerged:
    return SongsMerged(
        into_name=merged.into_name, from_names=list(merged.from_names), files=len(merged.files)
    )


@router.post("/songs/weigh-merge", dependencies=[Depends(csrf_protect)])
async def weigh_merging_songs(
    body: MergeSongs,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SongsMerged:
    """What folding several songs into one would move, counted. Nothing is written. A POST for the
    reason the people one gives: a list of ids in a query string is a request something truncates.
    """
    await _require_song(access, viewer, body.into)
    going = _songs_going(body)
    for one in going:
        await _require_song(access, viewer, one)
    weighed = await service.weigh_merge(body.into, going)
    if weighed is None:  # pragma: no cover (every id was resolved a line ago)
        raise _missing()
    return _merged(weighed)


@router.post("/songs/merge", dependencies=[Depends(csrf_protect)])
async def merge_songs(
    body: MergeSongs,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SongsMerged:
    """Fold several songs that are one piece of music into one, in a single transaction.

    It cannot be taken back, which is why it is one call and why the screen weighs it first. Every
    id is resolved through the scoped read, the survivor included.
    """
    await _require_song(access, viewer, body.into)
    going = _songs_going(body)
    for one in going:
        await _require_song(access, viewer, one)
    for one in going:
        # The songs going take their grants with them, as a deleted song's do.
        await access.forget_object(ObjectType.SONG, one)
    merged = await service.merge(body.into, going, actor=Actor.user(viewer.id))
    if merged is None:  # pragma: no cover (every id was resolved a line ago)
        raise _missing()
    if merged.files:
        await reindexer.touched_many(merged.files)
    return _merged(merged)


@router.get("/songs/{song_id}/cover")
async def song_cover(
    song_id: str,
    request: Request,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The picture this song is drawn as. See `serve_cover`. A song with none answers what a cover
    nobody chose answers, and the screen draws the music glyph."""
    await _require_song(access, viewer, song_id)
    chosen = await service.chosen_cover(song_id)
    return await serve_cover(request, access, viewer, chosen=chosen, pictures=pictures)


@router.put("/songs/{song_id}/cover", dependencies=[Depends(csrf_protect)])
async def set_song_cover(
    song_id: str,
    body: CoverWrite,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    stills: Annotated[StillSeam, Depends(wiring.stills)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SongSummary:
    """Choose the picture the song is drawn as, and which moment of it. Null takes it off."""
    await _require_song(access, viewer, song_id)
    asset_id = (
        None
        if body.asset_id is None
        else await require_reachable(access, viewer, body.asset_id, _missing)
    )
    before = await service.chosen_cover(song_id)
    kept = upload_kept_by_put(body.upload_id, asset_id=asset_id, frame=body.frame, before=before)
    await service.set_cover(
        song_id,
        asset_id,
        None if kept else body.at_ms,
        kept,
        actor=Actor.user(viewer.id),
        frame=body.frame,
    )
    await forget_displaced(pictures, before, after=kept)
    if body.asset_id is not None and body.at_ms is not None:
        await stills.wants_still(body.asset_id, body.at_ms)
    return await _song_now(access, viewer, song_id)


@router.post("/songs/{song_id}/cover-picture", dependencies=[Depends(csrf_protect)])
async def upload_song_cover(
    song_id: str,
    file: Annotated[UploadFile, File()],
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SongSummary:
    """A picture from outside the library as the song's cover. See `receive_cover`."""
    await _require_song(access, viewer, song_id)
    before = await service.chosen_cover(song_id)

    async def pointed(upload_id: str) -> bool:
        return await service.set_cover(song_id, None, None, upload_id, actor=Actor.user(viewer.id))

    await receive_cover(pictures, file.read, before=before, point_at=pointed)
    return await _song_now(access, viewer, song_id)


@router.put("/songs/{song_id}/favorite", dependencies=[Depends(csrf_protect)])
async def set_song_favorite(
    song_id: str,
    body: FavoriteWrite,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SongStateView:
    """Heart a song. This viewer's opinion, not the song's, so not admin-only."""
    await _require_song(access, viewer, song_id)
    favorite, rating = await service.set_favorite(viewer, song_id, favorite=body.favorite)
    return SongStateView(favorite=favorite, rating=rating)


@router.put("/songs/{song_id}/pin", dependencies=[Depends(csrf_protect)])
async def set_song_pinned(
    song_id: str,
    body: PinWrite,
    pins: Annotated[EntityStateStore, Depends(wiring.entity_state)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PinView:
    """Keep a song at the top of the Music wall, for this user. See the heart."""
    await _require_song(access, viewer, song_id)
    return PinView(
        pinned=await pins.set_pinned(PinnableKind.SONG, song_id, viewer.id, pinned=body.pinned)
    )


@router.put("/songs/{song_id}/rating", dependencies=[Depends(csrf_protect)])
async def set_song_rating(
    song_id: str,
    body: RatingWrite,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SongStateView:
    await _require_song(access, viewer, song_id)
    favorite, rating = await service.set_rating(viewer, song_id, rating=body.rating)
    return SongStateView(favorite=favorite, rating=rating)


@router.put(
    "/songs/{song_id}/vault",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_song_vault(
    song_id: str,
    body: VaultWrite,
    request: Request,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide the song from this user, or stop hiding it: the Photo Sets route's door, PIN and all
    (`set_photo_set_vault` says why the PIN check is awaited here). Hiding a song conceals the files
    that carry it as well, which is what somebody hiding it meant."""
    await _require_song(access, viewer, song_id)
    await require_vault_pin(request, viewer)
    await service.set_vault(viewer, song_id, vault=body.vault)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/songs/{song_id}/artists", dependencies=[Depends(csrf_protect)])
async def set_song_artists(
    song_id: str,
    body: ArtistsWrite,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SongSummary:
    """Set the artists the song credits, in order: the whole list, so adding, removing, reordering
    and correcting one are this one write. The song's name is left as it is."""
    await _require_song(access, viewer, song_id)
    await service.set_artists(song_id, body.names, actor=Actor.user(viewer.id))
    return await _song_now(access, viewer, song_id)


@router.put(
    "/artists/{artist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def rename_artist(
    artist_id: str,
    body: ArtistWrite,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Rename an artist on every song that credits it. A name another artist already has folds the
    two into one. An artist is shown to whoever may be shown a song that credits it, so one with
    no such song is the 404 an unknown id gets."""
    reached = await access.list_songs(
        viewer,
        limit=1,
        narrowing=EntityNarrowing.of("song", {"artists": [artist_id]}, is_admin=viewer.is_admin),
    )
    if reached.total == 0:
        raise _missing()
    if not await service.rename_artist(artist_id, body.name, actor=Actor.user(viewer.id)):
        raise _missing()  # pragma: no cover (resolved through a song that credits it)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/songs/{song_id}/files", dependencies=[Depends(csrf_protect)])
async def edit_song_files(
    song_id: str,
    body: FilesWrite,
    service: Annotated[SongService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    remove: Annotated[bool, Query()] = False,
) -> BulkWriteDone:
    """Put files on a song by hand, or take them off it. No file is moved on disk.

    A file that cannot be resolved is SKIPPED and counted, as on a Photo Set (`sift.kernel.reach`).
    A file on another song moves to this one: a file carries one song. The files whose Music field
    changed are named to the search index.
    """
    await _require_song(access, viewer, song_id)
    actionable = await access.actionable_of(viewer, body.asset_ids)
    wanted = list(actionable.allowed)
    changed: list[str] = []
    if wanted:
        actor = Actor.user(viewer.id)
        changed = (
            await service.remove(song_id, wanted, actor=actor)
            if remove
            else await service.add(song_id, wanted, actor=actor)
        )
    if changed:
        await reindexer.touched_many(changed)
    return BulkWriteDone.after(actionable, len(changed))
