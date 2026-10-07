# SPDX-License-Identifier: AGPL-3.0-or-later
"""The swap points: typed interfaces for the parts most likely to be replaced.

Each is a `Protocol`: what a component does, with no implementation, so a backend can be replaced
and a test can stand in a fake without patching. Several have no backend yet; naming the interface
keeps a later feature from reaching around a boundary, and none has a placeholder implementation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sift.kernel.access import AssetFilter, Viewer
from sift.kernel.attribution import FolderFaces, FolderStamp
from sift.kernel.db import Connection, Params, Row
from sift.kernel.library_write import Placed, Staged
from sift.kernel.records import SourceAnswer, SourceLink, Subject
from sift.kernel.vocabulary import SubjectKind


@runtime_checkable
class DatabaseSeam(Protocol):
    """Where everything is stored. Backed by SQLite; a client/server database is the swap.

    The portable subset. The single-writer transaction is not here: it hands out a live driver
    connection no other database could return, which would make this a description of SQLite.
    """

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def initialize_schema(self) -> None: ...
    async def schema_version(self, component: str) -> int: ...
    async def execute(self, sql: str, params: Params = ()) -> None: ...
    async def fetch_all(self, sql: str, params: Params = ()) -> list[Row]: ...
    async def fetch_one(self, sql: str, params: Params = ()) -> Row | None: ...


@runtime_checkable
class SemanticSeam(Protocol):
    """Asking a model what some words mean, and which files look like that.

    Search and the vector index are different slices, so search is handed this at boot. It returns
    an answer, never a query: the files `asker` may see and their distances, closest first, which
    the visibility read then orders by. `None` means this install cannot answer by meaning now (off,
    no models, too little memory) and the caller falls back to the ordinary order; an empty tuple
    means nothing came near.

    `can_answer` asks that once rather than per file, so a library-wide pass learns early that
    there is nothing to compare against; False while the feature is off. `describe_many` gives
    known files' vectors for comparing a set among itself (the Shoots pass); a file not described
    is absent, and None means no index. `lookalikes` answers by the model where it has described the
    file and by perceptual hashes where not, so it never declines; it serves both the file page's
    strip and the search box's `like:`, so the two cannot disagree.
    """

    async def neighbours(
        self, text: str, *, limit: int, asker: Viewer | None
    ) -> tuple[tuple[str, float], ...] | None: ...

    async def like_asset(
        self, asset_id: str, *, limit: int
    ) -> tuple[tuple[str, float], ...] | None: ...

    async def lookalikes(
        self, asset_id: str, *, limit: int, asker: Viewer | None
    ) -> tuple[tuple[str, float], ...]: ...

    async def describe_many(
        self, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[float]] | None: ...

    async def can_answer(self) -> bool: ...


# No full-text seam: search is the kernel's own index, reached through `ReindexSeam`. An interface
# with no implementation and no caller is a claim of a boundary, not one.


@runtime_checkable
class SettingsSeam(Protocol):
    """Reading a preference, for anything that is not the feature that stores them.

    Many features read preferences and none may import the one that stores them. Reads only: a
    write must check the key, the type and the user's right, which only the storing feature does.
    Both calls read the database each time, so a changed control takes effect on the next request.
    """

    async def get_app(self, key: str) -> Any: ...
    async def get_user(self, user_id: str, key: str) -> Any: ...


@runtime_checkable
class SavedFilterSeam(Protocol):
    """A saved search as the viewer-scoped constraints its words compile to, for a feature that
    wants its FILES (a swap's "everything this filter finds"); None where none of theirs is that id
    or it is not over files.
    """

    async def saved_filter(self, viewer: Viewer, saved_id: str) -> AssetFilter | None: ...


@runtime_checkable
class ForgetGoneSeam(Protocol):
    """A thing deleted, told to the search feature: a saved filter left naming only gone things goes
    too. Answers how many went."""

    async def forget_gone(
        self,
        kind: SubjectKind,
        thing_id: str,
        *,
        name: str | None,
        by: Viewer,
        also: Sequence[str] = (),
    ) -> int: ...


class ReindexSeam(Protocol):
    """Telling the search index that text it already holds has changed.

    A slice that changes what a clip matches must not know there is an index. `touched` names one
    asset; `touched_many` a set, in one transaction rather than one per asset; `queue_many` a set
    too, by a job after the answer, for a name many files carry; `renamed` queues a
    whole rebuild and is the LAST resort, since it holds the write lock for the whole library: only
    a write whose affected set is decided inside a transaction it cannot see (a merge) uses it.
    """

    async def touched(self, asset_id: str) -> None: ...
    async def touched_many(self, asset_ids: Sequence[str]) -> None: ...
    async def queue_many(self, asset_ids: Sequence[str]) -> None: ...
    async def renamed(self) -> None: ...


@runtime_checkable
class StillSeam(Protocol):
    """Asking for a still of one MOMENT of a video, without knowing what builds it.

    The media slice renders it and the catalog slices want it, and neither may import the other,
    so the composition root joins them. A cover's chosen frame and a saved Loop's picture are one
    kind of thing under one cache key, so there is one job. Nothing is promised about when: the
    client shows the file's own picture until it lands, and repeated asks are deduped.
    """

    async def wants_still(self, asset_id: str, at_ms: int) -> None: ...


@runtime_checkable
class RecognitionSeam(Protocol):
    """Handing a newly created person the faces something was already holding for their name.

    A pack can name somebody the library has nobody for; when a person is created with that name,
    the held faces become theirs. A seam because creating a person must not know recognition
    exists; with recognition off every call answers zero. `waiting_for` and `held_for` are the
    read-only halves for an offer. `linked` says a person was linked to a stash-box, so the face
    feature may take the box's pictures as starters through `BoxPicturesSeam`. `released` says a
    person was deleted: their faces were named, so they are in no pile, and the piles must be built
    again or the faces sit invisible on every screen.
    """

    async def claim_for(self, person_id: str, name: str) -> int: ...
    async def waiting_for(self, name: str) -> list[str]: ...
    async def held_for(self, person_id: str, name: str) -> int: ...
    async def released(self) -> None: ...
    async def linked(self, person_id: str) -> None: ...


@runtime_checkable
class BoxPicturesSeam(Protocol):
    """A stash-box's pictures of somebody linked to one, for the feature that learns faces.

    The stash-box feature is the only door to a box (its pacing, sealed keys, kept-local refusal),
    so the pictures come through here as bytes and the face feature never learns a URL.
    `linked_people` is everybody linked, for a press's count; `with_picture_lists` keeps links made
    since the box's whole picture list was recorded. `pictures_of` gives up to `most` of one
    person's pictures, largest first, as `(box name, bytes)`. An empty list means every box answered
    with none (nothing more to try); None means she could not be asked now (kept local, a box sealed
    or unreachable), so she is asked again later.
    """

    async def linked_people(self, *, with_picture_lists: bool = False) -> list[str]: ...
    async def pictures_of(
        self, person_id: str, master_key: bytes | None, *, most: int
    ) -> list[tuple[str, bytes]] | None: ...


@runtime_checkable
class CreatorPicturesSeam(Protocol):
    """Giving a creator's username a picture, for a feature that learns of one and owns no store.

    The store belongs to the download feature, which the stash-box feature may not import.
    `picture` fetches the bytes only when the creator has none yet, so an existing picture costs no
    request. True when a picture was kept; every other outcome answers False and none raises.
    """

    async def keep(
        self,
        *,
        site: str,
        username: str,
        address: str | None,
        picture: Callable[[], Awaitable[bytes | None]],
    ) -> bool: ...


@runtime_checkable
class FaceEvidenceSeam(Protocol):
    """What the faces in a folder's files say, for a feature that reads folder names.

    A folder name is a claim and a face is its proof, found by features that may not import each
    other: counts and ids, nothing about how they were worked out. `stamps` is the cheap half for
    the whole library, so `faces_in` is asked only where a stamp moved. `name_group` takes the
    caller's connection so confirming a folder names its files and the group atomically; its
    recording form says which appearances took the name, so `unname_faces` can undo exactly those.
    `contradicting` vetoes a silent write: files whose faces are named as somebody else (or refused
    as this person) and none of theirs. `teach` and `unteach` run after the caller's transaction,
    since they decode pictures and must not hold the writer. `propose_group` and
    `withdraw_proposals` hand the face feature a question to put; the folder reader never asks it.
    With recognition off all answer empty, and a folder is judged on its name alone, said plainly.
    """

    async def looking(self) -> bool: ...
    async def stamps(self) -> dict[str, FolderStamp]: ...
    async def faces_in(self, folder_id: str) -> FolderFaces: ...
    async def faces_in_many(self, folder_ids: Sequence[str]) -> dict[str, FolderFaces]: ...
    async def name_group(self, connection: Connection, group_id: str, person_id: str) -> int: ...
    async def name_group_recording(
        self, connection: Connection, group_id: str, person_id: str
    ) -> list[str]: ...
    async def unname_faces(self, connection: Connection, track_ids: Sequence[str]) -> int: ...
    async def contradicting(self, person_id: str, asset_ids: Sequence[str]) -> set[str]: ...
    async def teach(self, track_ids: Sequence[str], person_id: str) -> int: ...
    async def unteach(self, track_ids: Sequence[str], person_id: str) -> int: ...
    async def propose_group(
        self, group_id: str, person_id: str, *, folder_id: str, files: int, of: int
    ) -> bool: ...
    async def withdraw_proposals(self, folder_id: str, person_id: str) -> int: ...


@runtime_checkable
class DownloaderSeam(Protocol):
    """Fetches media from a web address using an external tool run as a separate process.

    Which tool handles an address is hidden, so tools can change freely; they are always separate
    processes, never imported.
    """

    def handles(self, url: str) -> bool: ...
    async def fetch(self, url: str, *, into: Path) -> list[Path]: ...


@runtime_checkable
class UrlImporter(Protocol):
    """The downloader, as a feature that only wants to hand it a link needs to see it.

    Narrow on purpose: capture only hands over a pasted link and watches the row id it gets back.
    Declared here so neither feature imports the other to name it.
    """

    async def submit_url(
        self, *, url: str, dest_folder_id: str | None, requested_by: str | None = None
    ) -> str: ...


@runtime_checkable
class FilingSeam(Protocol):
    """Putting a file that has just arrived where somebody said it should go.

    A link dropped ON a person, site, collection, tag or photo set means fetch this and file it
    there; the filing belongs to five features, decided at the composition root. `for_user` matters
    because one kind (the heart) is an opinion per user. Answers how many were filed, and never
    raises for a target deleted while the download ran: the download is kept regardless.
    """

    async def file_under(
        self, *, kind: str, target_id: str, asset_ids: Sequence[str], for_user: str
    ) -> int: ...


@runtime_checkable
class InferenceSeam(Protocol):
    """Turns an image or a phrase into an embedding vector, for similarity and semantic search.

    Optional and additive. Not built yet; where the models cannot run, the features that depend
    on it are not offered rather than degraded.
    """

    async def embed_image(self, path: Path) -> Sequence[float]: ...
    async def embed_text(self, text: str) -> Sequence[float]: ...


@runtime_checkable
class StorageSeam(Protocol):
    """Where an asset's bytes physically live. The local filesystem today; object storage is the swap.

    Reads are streamed, since the files are videos.
    """

    async def exists(self, key: str) -> bool: ...
    async def size(self, key: str) -> int: ...
    async def delete(self, key: str) -> None: ...
    def open(self, key: str) -> AsyncIterator[bytes]: ...


@runtime_checkable
class LibraryWriteSeam(Protocol):
    """Putting a file Sift produced into a library folder, beside the file it came from.

    Features that PRODUCE files depend on this, not the filesystem, so one piece of code decides
    whether a write into somebody's library is allowed. `name_taken_beside` is asked before
    anybody presses anything, where a refusal can still be read. The bytes are written elsewhere
    (ffmpeg, for minutes), so `stage_beside` settles everything that can be settled in advance and
    says where to build, `keep` places the result, `discard` clears a failure. Nothing can
    overwrite: `keep` claims the name with a create that fails if anything is there.
    """

    async def writable_beside(self, asset_id: str, *, actor: Viewer) -> str | None: ...
    async def name_taken_beside(self, asset_id: str, *, filename: str, actor: Viewer) -> bool: ...
    async def stage_beside(self, asset_id: str, *, filename: str, actor: Viewer) -> Staged: ...
    async def keep(self, staged: Staged) -> Placed: ...
    async def discard(self, staged: Staged) -> None: ...


@runtime_checkable
class DuplicatePairSeam(Protocol):
    """Saying in advance that two files are not a mistake, so nobody is asked about them.

    A copy Sift produced is a near duplicate of its source by every measure, so compressing forty
    files would put forty pairs before somebody, each an invitation to delete the wrong file. The
    producing feature writes the lasting "these are different" answer as it produces, through the
    feature that owns duplicate detection.
    """

    async def mark_unrelated(self, first_asset_id: str, second_asset_id: str) -> None: ...


@runtime_checkable
class MetadataSourceSeam(Protocol):
    """Everything Sift asks an external stash-box about a subject, and the only way it may ask.

    **The only door.** The pacing, caching and hard stop on a refusal live behind it; a slice going
    around it would ask at its own rate and get the person's account restricted. No writes:
    contributing back is a different feature. `search` asks about a name per source; the others
    are about a LINK (make it, read what was kept, take it back, ask again by hand).

    The box key is a parameter because it is sealed under the master key, which exists only while
    somebody is signed in; a source built holding a key could be asked with nobody there. `None` is
    ordinary (a resumed session) and makes the source absent. A by-id read exists only behind
    `link` and `refresh`, so nothing is fetched that is not kept.
    """

    async def search(
        self, term: str, master_key: bytes | None, *, subject: Subject = Subject.PERSON
    ) -> list[SourceAnswer]: ...
    async def link(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> SourceLink | None: ...
    async def links_of(self, subject: Subject, local_id: str) -> list[SourceLink]: ...
    async def unlink(self, subject: Subject, local_id: str, box_id: str) -> bool: ...
    async def refresh(
        self, subject: Subject, local_id: str, box_id: str, master_key: bytes | None
    ) -> SourceLink | None: ...


@runtime_checkable
class PlaybackCacheSeam(Protocol):
    """Throwing away the transcoded pieces of a video that has ended.

    Transcoded pieces are kept in a byte-capped cache keyed by asset, which nothing in the database
    points at; eviction would remove them eventually, but somebody who deleted a file meant it off
    the disk now. A courtesy, not a correctness fix, and one verb so the deleter knows nothing of
    how segments are kept.
    """

    def discard_asset(self, asset_id: str) -> int: ...


@runtime_checkable
class RemovedMembersSeam(Protocol):
    """Remembering that a picture inside an archive was removed from Sift, so a scan leaves it out:
    Sift does not rewrite an archive, so the Skipped list is the picture's only way out."""

    async def remember_removed(self, *, root_id: str, rel_path: str, size_bytes: int) -> None: ...


@runtime_checkable
class DisagreementSeam(Protocol):
    """How many fields a linked stash-box disagrees with, about ONE record.

    The tab strip's mark arrives with the strip's other numbers in one request; the strip and the
    disagreements are different slices. A number, never rows: a row carries an identifying pair (a
    name beside a birth date) that must not ride on every entity page. None (no question to ask: a
    kind no box knows, or a user who may not be told) is not nought (asked, nothing disagrees);
    neither draws a mark. `disagreement_mark` is the same count with the boxes that disagree, by
    name, taken from the one answer, so the mark can say which box it means.

    The last method filters and counts a WALL by it. A disagreement depends on per-field rules and
    a record read through its kind's writer, Python the wall's statement cannot reach, so the ids
    are worked out here and bound in: ids, since a count cannot filter the rows it counted. None
    means nothing may be filtered by it, never an empty answer.
    """

    async def disagreement_count(
        self, viewer: Viewer, subject: str, local_id: str
    ) -> int | None: ...

    async def disagreement_mark(
        self, viewer: Viewer, subject: str, local_id: str
    ) -> tuple[int | None, list[str]]: ...

    async def subjects_with_disagreements(
        self, viewer: Viewer, subject: str
    ) -> tuple[str, ...] | None: ...


@runtime_checkable
class PackSourceSeam(Protocol):
    """Lists and retrieves optional downloadable data packs.

    Not built yet. The swap is where the packs are distributed from.
    """

    async def available(self) -> list[str]: ...
    async def fetch(self, pack_id: str, *, into: Path) -> Path: ...


@runtime_checkable
class PhotoSetSeam(Protocol):
    """Making a Photo Set out of pictures somebody has agreed belong together.

    The proposing slice and the Photo Sets slice may not import each other, and the implementation
    is the same derivation folders and archives take, so there is one creation path with its cover,
    order and least-pictures rule. Answers the new id, or None when these were not a set after all.
    `origin` is the pass word the set is made under: a shoot by default, or the Stash import's.
    `forget` is what an undo reaches for: it removes the grouping and leaves every picture, recorded
    as `by`'s act rather than Sift's.
    """

    async def make(
        self, asset_ids: Sequence[str], *, name: str, origin: str = "shoot"
    ) -> str | None: ...

    async def holding(self, asset_ids: Sequence[str]) -> str | None:
        """The set already holding every one of these pictures, where they are most of it."""
        ...

    async def forget(self, photo_set_id: str, *, by: Viewer) -> None: ...


class Narrowed(Protocol):
    """What the filter engine answers a query with: constraints, and whether they reach the end.

    A Protocol because the value belongs to the search feature. `complete` is false when asking by
    meaning gave up before filling the page, so the grid says so rather than drawing a short page
    that looks like a small library.
    """

    @property
    def asset_filter(self) -> AssetFilter: ...

    @property
    def complete(self) -> bool: ...

    #: The filters whose value could not be read, as (field, value, reason). Each of them matches
    #: nothing; the page carries them so the screen can say which chip is the reason it is empty.
    @property
    def problems(self) -> Sequence[tuple[str, str, str]]: ...

    #: The products the query's `left_out:` terms ask about, so a wall filtered to the files a
    #: product gave up on can say under each one why. Empty for every other query.
    @property
    def left_out(self) -> Sequence[str]: ...


class FilterEngine(Protocol):
    """The one query language: raw query parameters in, constraints out.

    Exactly one, the search feature's compiler, published at boot for the grid and every wall that
    filters, which may not import it; two parsers would disagree. The answer goes straight into the
    statement that decides visibility. The viewer is a parameter because resolving names is scoped:
    a person this viewer may not be told about resolves, for them, to nobody. There is no stand-in
    engine; an unfiltered grid is an empty query.
    """

    async def constrain(self, viewer: Viewer, raw: Mapping[str, str]) -> AssetFilter: ...

    async def narrow(
        self, viewer: Viewer, raw: Mapping[str, str], *, by_meaning: bool, need: int
    ) -> Narrowed:
        """The same, for a search whose WORDS may be answered by a model rather than by the index.

        `by_meaning` says how to search, never how to arrange: the model's answer filters and the
        order arranges, in one statement. `need` is the last row of the page asked for, so the
        engine knows how far the answer must reach.
        """
        ...

    async def kept(self, viewer: Viewer, typed: Sequence[str]) -> list[str]:
        """Typed filter texts as a feature keeps them: each thing named by its id, in order.

        A kept filter (a saved Theater wall's cell) typed by NAME would break on a rename; by id it
        names the same thing. Resolved as this viewer's wall resolves it, and only the engine knows
        which words are names.
        """
        ...

    async def shown(self, viewer: Viewer, typed: Sequence[str]) -> list[str]:
        """Kept filter texts as they read today: each id as the name its thing goes by now, in order.

        The other half of `kept`. An id whose name would not lead back to it (shared, deleted, or
        not visible to this viewer) stays an id, and filters as it did.
        """
        ...


# `Staged` and `Placed` (from `kernel.library_write`) are imported for a signature and left out of
# `__all__`: this package publishes interfaces only.
__all__ = [
    "BoxPicturesSeam",
    "CreatorPicturesSeam",
    "DatabaseSeam",
    "DisagreementSeam",
    "DownloaderSeam",
    "DuplicatePairSeam",
    "FaceEvidenceSeam",
    "FilingSeam",
    "FilterEngine",
    "ForgetGoneSeam",
    "InferenceSeam",
    "LibraryWriteSeam",
    "MetadataSourceSeam",
    "Narrowed",
    "PackSourceSeam",
    "PhotoSetSeam",
    "PlaybackCacheSeam",
    "RecognitionSeam",
    "ReindexSeam",
    "RemovedMembersSeam",
    "SavedFilterSeam",
    "SemanticSeam",
    "SettingsSeam",
    "StillSeam",
    "StorageSeam",
    "UrlImporter",
]
