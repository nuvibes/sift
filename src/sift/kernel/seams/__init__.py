# SPDX-License-Identifier: AGPL-3.0-or-later
"""The swap points: typed interfaces for the parts most likely to be replaced."""

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
    """Where everything is stored; the single-writer transaction is SQLite's own, so not here."""

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def initialize_schema(self) -> None: ...
    async def schema_version(self, component: str) -> int: ...
    async def execute(self, sql: str, params: Params = ()) -> None: ...
    async def fetch_all(self, sql: str, params: Params = ()) -> list[Row]: ...
    async def fetch_one(self, sql: str, params: Params = ()) -> Row | None: ...


@runtime_checkable
class SemanticSeam(Protocol):
    """Asking a model what words mean and which files look like that; None means cannot now."""

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


@runtime_checkable
class SettingsSeam(Protocol):
    """Reading a preference without importing its feature; reads only, fresh every call."""

    async def get_app(self, key: str) -> Any: ...
    async def get_user(self, user_id: str, key: str) -> Any: ...


@runtime_checkable
class SavedFilterSeam(Protocol):
    """A saved search as the viewer-scoped file constraints it compiles to, or None."""

    async def saved_filter(self, viewer: Viewer, saved_id: str) -> AssetFilter | None: ...


@runtime_checkable
class ForgetGoneSeam(Protocol):
    """A thing deleted, so a saved filter naming only gone things goes too; answers how many."""

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
    """Telling the search index its text changed; `renamed` rebuilds everything, a last resort."""

    async def touched(self, asset_id: str) -> None: ...
    async def touched_many(self, asset_ids: Sequence[str]) -> None: ...
    async def queue_many(self, asset_ids: Sequence[str]) -> None: ...
    async def renamed(self) -> None: ...


@runtime_checkable
class StillSeam(Protocol):
    """Asking for a still of one moment of a video, without knowing what builds it."""

    async def wants_still(self, asset_id: str, at_ms: int) -> None: ...


@runtime_checkable
class RecognitionSeam(Protocol):
    """Handing a newly created person the faces held for their name; zero with recognition off."""

    async def claim_for(self, person_id: str, name: str) -> int: ...
    async def waiting_for(self, name: str) -> list[str]: ...
    async def held_for(self, person_id: str, name: str) -> int: ...
    async def released(self) -> None: ...
    async def linked(self, person_id: str) -> None: ...


@runtime_checkable
class BoxPicturesSeam(Protocol):
    """A stash-box's pictures of a linked person, as bytes, so the face feature never sees a URL."""

    async def linked_people(self, *, with_picture_lists: bool = False) -> list[str]: ...
    async def pictures_of(
        self, person_id: str, master_key: bytes | None, *, most: int
    ) -> list[tuple[str, bytes]] | None: ...


@runtime_checkable
class CreatorPicturesSeam(Protocol):
    """Giving a creator's username a picture, fetched only when it has none; never raises."""

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

    `teach` and `unteach` run after the caller's transaction: they decode and must not hold it."""

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
    """Fetches media from an address with an external tool, always a separate process."""

    def handles(self, url: str) -> bool: ...
    async def fetch(self, url: str, *, into: Path) -> list[Path]: ...


@runtime_checkable
class UrlImporter(Protocol):
    """The downloader as a feature that only hands it a link sees it."""

    async def submit_url(
        self, *, url: str, dest_folder_id: str | None, requested_by: str | None = None
    ) -> str: ...


@runtime_checkable
class FilingSeam(Protocol):
    """Filing a just-arrived file where its link was dropped; never raises for a gone target."""

    async def file_under(
        self, *, kind: str, target_id: str, asset_ids: Sequence[str], for_user: str
    ) -> int: ...


@runtime_checkable
class InferenceSeam(Protocol):
    """Turns an image or a phrase into an embedding vector; not built yet."""

    async def embed_image(self, path: Path) -> Sequence[float]: ...
    async def embed_text(self, text: str) -> Sequence[float]: ...


@runtime_checkable
class StorageSeam(Protocol):
    """Where an asset's bytes physically live; reads are streamed, since the files are videos."""

    async def exists(self, key: str) -> bool: ...
    async def size(self, key: str) -> int: ...
    async def delete(self, key: str) -> None: ...
    def open(self, key: str) -> AsyncIterator[bytes]: ...


@runtime_checkable
class LibraryWriteSeam(Protocol):
    """Putting a file Sift produced beside its source; nothing can overwrite."""

    async def writable_beside(self, asset_id: str, *, actor: Viewer) -> str | None: ...
    async def name_taken_beside(self, asset_id: str, *, filename: str, actor: Viewer) -> bool: ...
    async def stage_beside(self, asset_id: str, *, filename: str, actor: Viewer) -> Staged: ...
    async def keep(self, staged: Staged) -> Placed: ...
    async def discard(self, staged: Staged) -> None: ...


@runtime_checkable
class DuplicatePairSeam(Protocol):
    """Saying in advance that two files are not a mistake, so nobody is asked about them."""

    async def mark_unrelated(self, first_asset_id: str, second_asset_id: str) -> None: ...


@runtime_checkable
class MetadataSourceSeam(Protocol):
    """The only door to an external stash-box, with its pacing; the box key is passed per call."""

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
    """Throwing away the transcoded pieces of a video that has ended."""

    def discard_asset(self, asset_id: str) -> int: ...


@runtime_checkable
class RemovedMembersSeam(Protocol):
    """Remembering a picture removed from an archive, so a scan leaves it out."""

    async def remember_removed(self, *, root_id: str, rel_path: str, size_bytes: int) -> None: ...


@runtime_checkable
class DisagreementSeam(Protocol):
    """How many fields a linked stash-box disagrees with about one record; None is not nought."""

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
    """Lists and retrieves optional downloadable data packs; not built yet."""

    async def available(self) -> list[str]: ...
    async def fetch(self, pack_id: str, *, into: Path) -> Path: ...


@runtime_checkable
class PhotoSetSeam(Protocol):
    """Making a Photo Set from pictures somebody agreed belong together; `forget` undoes it."""

    async def make(
        self, asset_ids: Sequence[str], *, name: str, origin: str = "shoot"
    ) -> str | None: ...

    async def holding(self, asset_ids: Sequence[str]) -> str | None:
        """The set already holding every one of these pictures, where they are most of it."""
        ...

    async def forget(self, photo_set_id: str, *, by: Viewer) -> None: ...


class Narrowed(Protocol):
    """The filter engine's answer: constraints, and whether they reach the end of the page."""

    @property
    def asset_filter(self) -> AssetFilter: ...

    @property
    def complete(self) -> bool: ...

    #: Filters whose value could not be read, so the screen can say why it is empty.
    @property
    def problems(self) -> Sequence[tuple[str, str, str]]: ...

    #: The products the query's `left_out:` terms ask about.
    @property
    def left_out(self) -> Sequence[str]: ...


class FilterEngine(Protocol):
    """The one query language: raw query parameters in, viewer-scoped constraints out."""

    async def constrain(self, viewer: Viewer, raw: Mapping[str, str]) -> AssetFilter: ...

    async def narrow(
        self, viewer: Viewer, raw: Mapping[str, str], *, by_meaning: bool, need: int
    ) -> Narrowed:
        """The same, where a model may answer the words; `need` is the page's last row."""
        ...

    async def kept(self, viewer: Viewer, typed: Sequence[str]) -> list[str]:
        """Typed filter texts with each named thing as its id, so a rename does not break them."""
        ...

    async def shown(self, viewer: Viewer, typed: Sequence[str]) -> list[str]:
        """Kept filter texts as they read today, each id as its thing's current name."""
        ...


# `Staged` and `Placed` are imported for a signature only; this publishes interfaces.
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
