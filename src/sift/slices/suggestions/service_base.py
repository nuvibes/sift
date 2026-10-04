# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every part of the suggestions service stands on: its words, the shapes it hands back,
and the doors every rule writes through. Each rule is a mixin naming the parts it relies on as its
bases; this is the one they all share."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sift.kernel.access import (
    NumberedUsername,
    ObjectType,
    Repository,
    Viewer,
    bump_stamps_for_object,
)
from sift.kernel.audience import EVERY_ADMIN, Audience
from sift.kernel.content import FolderNode
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.db import Connection
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.seams import FaceEvidenceSeam, SettingsSeam
from sift.kernel.vocabulary import Subject
from sift.kernel.workbench import Recorder
from sift.slices.suggestions.metadata import Fields
from sift.slices.suggestions.naming import Username
from sift.slices.suggestions.store import Store

#: What goes in an attribution's `source` when a pass wrote it rather than a person.
AUTOMATIC = "folder"


#: What goes in a FILING's `source` when the file's own name said where it came from.
FROM_FILENAME = "filename"


#: And when the username's name could only be read from two fields inside its pictures.
FROM_METADATA = "metadata"


#: BOTH WORDS A FILING FROM A FILE'S OWN NAME CAN CARRY, as the one pair every read of them binds.
FILED_FROM_A_NAME: tuple[str, str] = (FROM_FILENAME, FROM_METADATA)


#: What this feature's queue is called in the record of decisions and the panel's address.
QUEUE = "folders"


#: The queue a silent write's receipt is written under, so its Undo comes back here.
FILED_QUEUE = "filed"


#: The filing pass's own queue: its receipts are taken back a different way from `QUEUE`'s.
FILENAMES_QUEUE = "filenames"


#: How many claims one page of the review screen carries: a short list somebody works through.
PAGE = 50


#: How many of a username's files the filings page draws under it.
SHOWN_PER_USERNAME = 24


#: The most outstanding questions the screen will ever resolve in one request.
MAX_OUTSTANDING = 500


class SuggestionError(Exception):
    """Something the caller asked for cannot be done, with a sentence saying why."""


class NotFound(SuggestionError):
    """No such claim, or none this user may be told about."""


@dataclass(frozen=True, slots=True)
class FolderPass:
    """What a pass now would read: the folders that moved since the last one, worked out and not
    read, because the reading is the work."""

    folders: tuple[FolderNode, ...]
    stamps: Mapping[str, object]
    moved: tuple[FolderNode, ...]
    #: Whether files under no Site are filed by their names on the way.
    by_names: bool


@dataclass(frozen=True, slots=True)
class Proposal:
    """One row of the review screen, resolved against whoever is reading it."""

    id: str
    kind: str
    proposed: str
    #: Where the claim came from, so the screen can say why it is asking.
    evidence: str
    folder: str
    #: The folder itself: the id above answers "which question", this answers "which folder".
    folder_id: str
    path: str
    files: int
    #: The face group, when the claim rests on one, and one face of it to draw.
    group_id: str | None = None
    face_id: str | None = None
    #: The token on the end of that face's crop address, so the browser may keep the picture.
    face_art: str | None = None
    #: Somebody who answers to a near-miss of this name: a merge to suggest, never to perform.
    near_miss: str | None = None
    #: The site this folder sits under, and whether the name is a username on it.
    site: str | None = None
    is_username: bool = False
    #: The files here whose faces do not agree, offered for unticking.
    dissenting: tuple[str, ...] = ()
    #: One file under the folder this user may see, to draw beside the question.
    cover: str = ""
    #: For a Site folder, the names read out of the filenames.
    per_file: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Filed:
    """One folder a pass filed under somebody, resolved for whoever is asking."""

    person_id: str
    person: str
    folder_id: str
    folder: str
    path: str
    files: int
    #: Which picture this person's cover is, so its address names it and may be kept (`EntityCard`).
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    cover_track_id: str | None = None
    art: str | None = None


@dataclass(frozen=True, slots=True)
class FiledFile:
    """One file a pass filed from its own name, with the decision that takes it back."""

    asset_id: str
    filename: str
    #: None where the record cannot offer one (see `FiledFromNameView.decision_id`).
    decision_id: str | None = None
    #: The token on the end of this file's still, so a browser may keep it.
    art: str | None = None


@dataclass(frozen=True, slots=True)
class FilingGroup:
    """One username, and the files a pass filed under it from their own names."""

    username_id: str
    name: str
    site: str | None
    #: Who the username is joined to, or None (`sentences.username_opens`).
    person_id: str | None
    #: How many the pass filed of the files this viewer may be shown. See `filings_by_username`.
    files: int
    shown: list[FiledFile] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class UsernameTakenOff:
    """What taking one username's filename filings back did: how many files, and its record."""

    files: int
    #: The one receipt, or None where the service records nothing (a pass built for nobody).
    decision_id: str | None


@dataclass(frozen=True, slots=True)
class Page:
    items: list[Proposal] = field(default_factory=list)
    total: int = 0


@dataclass(frozen=True, slots=True)
class Outline:
    """What the board's Folders card draws: how many, and a still per folder."""

    total: int = 0
    #: `(claim id, cover)` for the front of the queue, in screen order.
    covers: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Ignored:
    """What setting a folder aside did, and what putting it back would need."""

    settled: bool = False
    claim_id: str = ""
    name_key: str = ""
    proposed: str = ""
    decision_id: str = ""


@dataclass(frozen=True, slots=True)
class Written:
    """Exactly what one confirmation created, so exactly that can be put back."""

    attributed: tuple[tuple[str, str], ...] = ()
    faces: tuple[str, ...] = ()
    #: People it had to invent. Removed on an undo only if nothing has been attached to them since.
    created_people: tuple[str, ...] = ()
    alias: tuple[str, str] | None = None
    username_linked: tuple[str, str] | None = None
    #: Folders whose standing answer it recorded, so a later pass keeps applying it.
    remembered: tuple[tuple[str, str], ...] = ()
    #: Files FILED under a username by this decision, never one already filed there, whose first
    #: answer the insert kept.
    filed: tuple[tuple[str, str], ...] = ()
    #: The other folders asking the same name that this Yes answered, as `(question, folder)`: one
    #: press is one line, so its Undo puts these questions back as well.
    namesakes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Applied:
    """What a confirmation actually did. Every number is a write that landed."""

    #: Who the folder turned out to be. Empty for a Site folder, whose people come one per file.
    person_id: str = ""
    created: bool = False
    files: int = 0
    faces: int = 0
    alias: bool = False
    username_linked: bool = False
    people: int = 0
    site: bool = False
    claim_id: str = ""
    proposed: str = ""
    #: The rows it created, and nothing it merely found. See `Written`.
    written: Written = field(default_factory=Written)
    decision_id: str = ""


@dataclass(frozen=True, slots=True)
class MadeSet:
    """A Photo Set this pass just made: its id, and what it ended up being CALLED."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class PostSets:
    """Making and unmaking the Photo Set a post of pictures deserves, handed in from outside."""

    #: `(the post's files, in the order they were posted, the name) -> the set, or None`.
    derive: Callable[[Sequence[str], str], Awaitable[MadeSet | None]]
    #: `(the set's id, who pressed Undo) -> gone`, recorded as that person's act, never Sift's.
    forget: Callable[[str, Viewer], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PictureFields:
    """Reading the two fields of one file, handed in from outside. See `metadata.FIELDS`."""

    #: `(asset id) -> its `Artist` and `ImageDescription`, both empty where there are none.`
    read: Callable[[str], Awaitable[Fields]]


@dataclass(frozen=True, slots=True)
class FolderTakenOff:
    """What taking a folder back from a person did, and the record it wrote."""

    files: int
    forgot: bool
    decision_id: str | None


@dataclass(frozen=True, slots=True)
class SwapFolders:
    """The folders a swap made to hold what it brought, handed in from outside."""

    #: `() -> every folder a swap made that is still in the library, by what it is.`
    made: Callable[[], Awaitable[Arrivals]]


@dataclass(frozen=True, slots=True)
class Arrivals:
    """The folders swaps made, by what each one is: what `SwapFolders` answers.

    **A container is never one person's**
    """

    containers: frozenset[str] = frozenset()
    by_name: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Filing:
    """One username's files, and everything the sentence about them has to say."""

    where: Username
    #: `FROM_FILENAME` or `FROM_METADATA` (see both).
    source: str
    #: How this library came by the username's number, or None where none was looked up.
    number: NumberedUsername | None = None


#: What a silent write's receipt says it is, so its Undo is read as one (`queue.FiledQueue.reverse`).
SILENT = "silent"


#: And what the record of a folder taken back from its row says it is.
TAKEN_BACK = "taken_back"


class SuggestionBase:
    """The service's state, and the record and announcement every rule writes through."""

    def __init__(
        self,
        *,
        store: Store,
        access: Repository,
        faces: FaceEvidenceSeam,
        preferences: SettingsSeam,
        recorder: Recorder | None = None,
        sets: PostSets | None = None,
        pictures: PictureFields | None = None,
        after_filing: Callable[[], Awaitable[None]] | None = None,
        swap_folders: SwapFolders | None = None,
    ) -> None:
        self._store = store
        self._access = access
        self._faces = faces
        self._preferences = preferences
        self._recorder = recorder
        self._sets = sets
        self._pictures = pictures
        # Told once after an answer filed files under somebody, so the shoots pass is asked for.
        self._after_filing = after_filing
        self._swap_folders = swap_folders

    async def _filed(self, applied: Applied) -> Applied:
        if self._after_filing is not None and (applied.files or applied.person_id):
            await self._after_filing()
        return applied

    async def _record(
        self,
        connection: Connection,
        viewer: Viewer,
        receipt: tuple[str, str, str],
        subjects: Sequence[Subject] = (),
        object: LedgerObject | None = None,
    ) -> str:
        """Write down what a decision did, on the decision's own connection."""
        if self._recorder is None:
            return ""
        title, detail, payload = receipt
        return await self._recorder.record_on(
            connection,
            queue=QUEUE,
            user_id=viewer.id,
            title=title,
            detail=detail,
            payload=payload,
            subjects=subjects,
            object=object,
        )

    async def _told(
        self,
        connection: Connection,
        *,
        people: Iterable[str] = (),
        usernames: Iterable[str] = (),
        sites: Iterable[str] = (),
    ) -> Audience:
        """Who a press putting People, Sites and Usernames on files (or taking them off) must tell:
        every admin, and each user whose view moved, whose cache stamps move with it. The access
        layer's `_on` writers announce nothing, so this is where it happens, once per press."""
        told = EVERY_ADMIN
        for person_id in sorted(set(people)):
            told |= await bump_stamps_for_object(connection, ObjectType.PERSON, person_id)
        for site_id in sorted(
            await self._store.sites_of_on(
                connection, usernames=sorted(set(usernames)), named=sorted(set(sites))
            )
        ):
            told |= await bump_stamps_for_object(connection, ObjectType.SITE, site_id)
        return told

    @staticmethod
    def _subjects_of(applied: Applied, folder_id: str) -> list[Subject]:
        """What a confirmation was about: the folder, and everything it wrote a name onto."""
        subjects: list[Subject] = [Subject(kind="folder", id=folder_id)]
        subjects += [Subject(kind="folder", id=one) for _, one in applied.written.namesakes]
        subjects += [Subject(kind="asset", id=one) for one, _ in applied.written.attributed]
        subjects += [Subject(kind="person", id=one) for _, one in applied.written.attributed]
        # And who the folder turned out to be, even where not one file needed attributing.
        if applied.person_id:
            subjects.append(Subject(kind="person", id=applied.person_id))
        return subjects
