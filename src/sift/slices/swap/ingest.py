# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receiving side of a swap: one file from another install, through the one import path."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Protocol

from blake3 import blake3

from sift.kernel.access.catalog import (
    add_alias_on,
    attribute_assets_recording_on,
    by_sift,
    create_person_on,
    file_assets_under_site_on,
    give_person_a_cover_on,
    link_username_to_asset_on,
    people_named_on,
    seed_site_username_on,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.config import Settings
from sift.kernel.content import FolderRow, LibraryStore, songs
from sift.kernel.db import Connection, Database
from sift.kernel.filenames import InvalidFilename, check_folder_name
from sift.kernel.ingress import (
    MediaType,
    NoDestination,
    Origin,
    classify,
    read_ends,
)
from sift.kernel.jobs import JobContext
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.seams import ReindexSeam
from sift.kernel.vocabulary import VIA_SWAP, Subject
from sift.slices.swap import models, store, transfer

log = get_logger(__name__)


# --- what landing is handed -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LandingSession:
    """What landing needs to know about the session a file arrived in, and nothing more."""

    #: The session's id; its last eight characters are the short id History shows.
    id: str
    #: The other install's device id, never an address.
    peer_device: str
    dest_folder_id: str
    #: The keys the guest asked for; any other file is refused before anything reads it.
    wanted: frozenset[str]

    @property
    def short_id(self) -> str:
        """The last eight characters of the id: what History and the log call a session."""
        return self.id[-8:]


@dataclass(frozen=True, slots=True)
class OfferedFace:
    """One facial fingerprint sent for a person, with its face picture when the models differ."""

    #: Keyed by the picture's digest, so the same face offered twice is held once.
    digest: str
    quality: float
    vector: bytes
    picture: bytes | None = None


@dataclass(frozen=True, slots=True)
class OfferedFaces:
    """A person's face descriptions and the recognition model that made them."""

    recognizer: str
    dimension: int
    faces: tuple[OfferedFace, ...]
    confirmed: int | None = None


@dataclass(frozen=True, slots=True)
class ArrivingPerson:
    """One offered person on the file, with the guest's Take or Skip and their match here."""

    name: str
    aliases: tuple[str, ...] = ()
    taken: bool = True
    local_id: str | None = None
    faces: OfferedFaces | None = None


@dataclass(frozen=True, slots=True)
class ArrivingSong:
    """The song a file was offered with: its name, its artists and its AcoustID recording."""

    name: str
    artists: tuple[str, ...] = ()
    recording: str | None = None


@dataclass(frozen=True, slots=True)
class Received:
    """One file whose every piece has arrived and been checked, waiting in the workspace."""

    key: str
    #: Sift's own workspace copy; the session removes it, never landing.
    staged: Path
    digest: str
    title: str
    #: By name, never an address.
    site: str | None = None
    username: str | None = None
    people: tuple[ArrivingPerson, ...] = ()
    #: The leaf of the sender's own name; None means "Swapped file".
    name: str | None = None
    song: ArrivingSong | None = None
    #: `digest` was read from these staged bytes by the session that wrote them, after the last
    #: chunk: the landing does not read them again for it.
    checked: bool = False


@dataclass(frozen=True, slots=True)
class Landed:
    """What landing one file did."""

    asset_id: str
    location_id: str
    was_duplicate: bool
    folder_id: str
    people: tuple[str, ...] = ()
    created: tuple[str, ...] = ()
    faces_held: int = 0


class Refused(Exception):
    """This file does not land; the session counts it as failed and goes on."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"refused: {reason}")
        self.reason = reason


UNWANTED: Final = "unwanted"
DIGEST: Final = "digest"
#: Unstrippable, so not let in carrying what the strip would have removed.
STRIP: Final = "strip"


# --- from the offer, as the session holds it ----------------------------------------------------


def received_from_offer(
    file: Mapping[str, Any],
    people: Sequence[Mapping[str, Any]],
    *,
    matched: Mapping[int, str | None],
    taken: Collection[int] | None,
    staged: Path,
    digest: str,
    checked: bool = False,
) -> Received:
    """The session's view of one received file, read through the offer's own wire shapes."""
    entry = models.OfferedFile.model_validate(file)
    arriving: list[ArrivingPerson] = []
    for index in entry.people:
        if not 0 <= index < len(people):
            raise ValueError("a file in the offer names a person the offer does not list")
        person = models.OfferedPerson.model_validate(people[index])
        arriving.append(
            ArrivingPerson(
                name=person.name,
                aliases=tuple(person.aliases),
                taken=taken is None or index in taken,
                local_id=matched.get(index),
                faces=_faces_of(person.faces),
            )
        )
    return Received(
        key=entry.key,
        staged=staged,
        digest=digest,
        checked=checked,
        title=entry.title,
        site=entry.site,
        username=entry.username,
        people=tuple(arriving),
        name=entry.name,
        song=(
            ArrivingSong(
                name=entry.song.name,
                artists=tuple(entry.song.artists),
                recording=entry.song.recording,
            )
            if entry.song is not None
            else None
        ),
    )


def _faces_of(offered: models.OfferedFaces | None) -> OfferedFaces | None:
    """The descriptions decoded, each one that does not decode left out alone."""
    if offered is None:
        return None
    faces: list[OfferedFace] = []
    for face in offered.faces:
        try:
            vector = base64.b64decode(face.vector, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(vector) == offered.dimension * 4:
            faces.append(
                OfferedFace(
                    digest=face.digest,
                    quality=face.quality,
                    vector=vector,
                    picture=_picture_of(face.picture),
                )
            )
    return OfferedFaces(
        recognizer=offered.recognizer,
        dimension=offered.dimension,
        faces=tuple(faces),
        confirmed=offered.confirmed,
    )


#: A face picture is a JPEG square, refused before a decoder sees anything else.
_JPEG = b"\xff\xd8\xff"


def _picture_of(text: str | None) -> bytes | None:
    """A face picture from the offer, or None when it does not decode as a JPEG."""
    if not text:
        return None
    try:
        picture = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    return picture if picture.startswith(_JPEG) else None


# --- the two doors this slice may not open itself ---------------------------------------------


class ImportFile(Protocol):
    """The one import pipeline, capture's `import_file`, handed in at boot."""

    async def __call__(
        self, *, path: Path, origin: Origin, dest_folder_id: str | None, ctx: JobContext
    ) -> ImportOutcome: ...


class ImportOutcome(Protocol):
    """What the import pipeline reports. Read structurally."""

    @property
    def asset_id(self) -> str: ...
    @property
    def location_id(self) -> str: ...
    @property
    def was_duplicate(self) -> bool: ...


class FaceHolding(Protocol):
    """The face feature's pack import, the one door into those rows, bound at boot."""

    async def recognizer(self) -> str | None:
        """The recognition model this install uses, or None when faces are off or not set up."""
        ...

    async def hold(
        self,
        *,
        pack: str,
        person: str,
        recognizer: str,
        dimension: int,
        faces: Sequence[OfferedFace],
        suggest_only: bool = False,
        confirmed: int | None = None,
    ) -> int:
        """Take in one person's descriptions as pack `pack`, idempotently; returns how many."""
        ...


# --- landing one file -------------------------------------------------------------------------


async def land(
    session: LandingSession,
    received: Received,
    *,
    ctx: JobContext,
    import_file: ImportFile,
    database: Database,
    reindexer: ReindexSeam,
    faces: FaceHolding | None,
    settings: Settings,
) -> Landed:
    """Land one received file; raises `Refused`, `IngressRejected` or `NoDestination` if not."""
    if received.key not in session.wanted:
        # Before anything reads the bytes: what the guest did not ask for is never read.
        log.warning("swap.file_refused", swap=session.short_id, reason=UNWANTED)
        raise Refused(UNWANTED)

    if not received.checked:
        whole = await asyncio.to_thread(whole_digest, received.staged)
        if whole != received.digest.strip().lower():
            log.warning("swap.file_refused", swap=session.short_id, reason=DIGEST)
            raise Refused(DIGEST)

    taken = [person for person in received.people if person.taken]
    scratch = await asyncio.to_thread(
        tempfile.TemporaryDirectory, prefix="swap-strip-", dir=received.staged.parent
    )
    try:
        try:
            stripped = await strip(
                received.staged,
                Path(scratch.name),
                stem=file_stem(received),
                suffix=file_suffix(received),
                settings=settings,
            )
        except StripError as error:
            log.warning("swap.file_refused", swap=session.short_id, reason=STRIP)
            raise Refused(STRIP) from error
        # Only once there is a file, so a refusal leaves no empty folder behind.
        folder = await _folder_for(
            ctx.library, session.dest_folder_id, folder_name(received, taken, session=session)
        )
        # The swap's own folder, recorded by id (`record_folder`).
        top = await _folder_for(ctx.library, session.dest_folder_id, swap_folder(session))
        await record_folder(database, session, top.id)
        outcome = await import_file(
            path=stripped, origin=Origin.SWAP, dest_folder_id=folder.id, ctx=ctx
        )
    finally:
        await asyncio.to_thread(scratch.cleanup)

    filed = await _file_it(session, received, taken, outcome.asset_id, database)
    # The People and Username are indexed text written after the import, so the index is told again.
    await reindexer.touched(outcome.asset_id)
    held = await _hold_faces(session, filed, faces)
    people = [one.person_id for one in filed]
    created = [one.person_id for one in filed if one.created]

    log.info(
        "swap.file_landed",
        swap=session.short_id,
        asset_id=outcome.asset_id,
        duplicate=outcome.was_duplicate,
        people=len(people),
        created=len(created),
        faces=held,
    )
    return Landed(
        asset_id=outcome.asset_id,
        location_id=outcome.location_id,
        was_duplicate=outcome.was_duplicate,
        folder_id=folder.id,
        people=tuple(people),
        created=tuple(created),
        faces_held=held,
    )


@dataclass(frozen=True, slots=True)
class _Filed:
    """One taken person as they now stand in this library, and what they were offered with."""

    offered: ArrivingPerson
    person_id: str
    #: The name their faces are held under here, so the face feature finds this person.
    name: str
    created: bool


async def _file_it(
    session: LandingSession,
    received: Received,
    taken: Sequence[ArrivingPerson],
    asset_id: str,
    database: Database,
) -> list[_Filed]:
    """The People, Site, Username, arrival and count in one transaction, each marked as by swap."""
    made = by_sift(VIA_SWAP)
    filed: list[_Filed] = []
    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        for person in taken:
            person_id, is_new = await _person_for(connection, person)
            if person_id is None or any(one.person_id == person_id for one in filed):
                continue
            await attribute_assets_recording_on(
                connection, asset_ids=(asset_id,), person_id=person_id, source=VIA_SWAP
            )
            name = await store.person_name_on(connection, person_id) or person.name
            filed.append(_Filed(person, person_id, name, is_new))
            if is_new:
                # Their first file is their picture, or a new person is a wall of initials.
                await give_person_a_cover_on(connection, person_id=person_id, asset_id=asset_id)

        site = (received.site or "").strip()
        username = (received.username or "").strip()
        if site and username:
            _, username_id = await seed_site_username_on(
                connection, site=site, name=username, made=made
            )
            await link_username_to_asset_on(
                connection, asset_id=asset_id, username_id=username_id, source=VIA_SWAP
            )
        elif site:
            await file_assets_under_site_on(
                connection, asset_ids=(asset_id,), site=site, source=VIA_SWAP, made=made
            )
        await _put_song_on(connection, session, asset_id, received.song)

        await record_event(
            connection,
            actor=Actor.sift(VIA_SWAP),
            verb="added",
            subject=Subject(kind="asset", id=asset_id),
            # The device and the session, and nothing else the other side said.
            payload=json.dumps({"device": session.peer_device, "session": session.short_id}),
        )
        await store.count_landed_on(connection, session.id)
    return filed


SONG_SOURCE: Final = VIA_SWAP


async def _put_song_on(
    connection: Connection, session: LandingSession, asset_id: str, song: ArrivingSong | None
) -> str | None:
    """Put the song the file came with on it (`songs.name_song_on`), only where it has none."""
    if song is None:
        return None
    song_id = await songs.name_song_on(
        connection, asset_id, song.name, source=SONG_SOURCE, recording_id=song.recording
    )
    if song_id is None:
        return None
    await songs.credit_where_none(
        connection, song_id, list(song.artists), source=songs.CREDIT_FROM_SWAP
    )
    await record_event(
        connection,
        actor=Actor.sift(VIA_SWAP),
        verb="song_named",
        subject=Subject(kind="asset", id=asset_id),
        payload=json.dumps(
            {
                "song": songs.cleaned_song(song.name),
                "song_id": song_id,
                "source": SONG_SOURCE,
                "device": session.peer_device,
            }
        ),
    )
    return song_id


async def _person_for(connection: Connection, person: ArrivingPerson) -> tuple[str | None, bool]:
    """Who this offered person is here, and whether this call made them."""
    if (
        person.local_id is not None
        and await store.person_name_on(connection, person.local_id) is not None
    ):
        return person.local_id, False
    found = await people_named_on(connection, person.name)
    if len(found) == 1:
        return found[0], False
    if found:
        log.info("swap.person_ambiguous", candidates=len(found))
        return None, False
    person_id = await create_person_on(connection, person.name, made=by_sift(VIA_SWAP))
    if person_id is None:
        return None, False
    for alias in person.aliases:
        if alias.strip().casefold() != person.name.strip().casefold():
            await add_alias_on(connection, person_id=person_id, alias=alias)
    return person_id, True


async def _hold_faces(
    session: LandingSession, filed: Sequence[_Filed], faces: FaceHolding | None
) -> int:
    """Hold each taken person's fingerprints through the pack import; never fails the landing."""
    offered_faces = [
        (one, one.offered.faces)
        for one in filed
        if one.offered.faces is not None and one.offered.faces.faces
    ]
    if not offered_faces or faces is None:
        return 0
    here = await faces.recognizer()
    if here is None:
        log.info("swap.faces_not_held", swap=session.short_id, reason="faces_off")
        return 0
    added = 0
    for one, offered in offered_faces:
        usable = _usable_here(offered, here)
        if not usable:
            log.warning(
                "swap.faces_refused",
                swap=session.short_id,
                reason="recognizer",
                theirs=offered.recognizer,
                ours=here,
            )
            continue
        try:
            added += await faces.hold(
                pack=f"swap {session.id} {one.person_id}",
                person=one.name,
                recognizer=offered.recognizer,
                dimension=offered.dimension,
                faces=usable,
                suggest_only=not one.created,
                confirmed=offered.confirmed,
            )
        except Exception as error:
            # See the docstring: the file is in, and this never takes it back out.
            log.warning("swap.faces_not_held", swap=session.short_id, reason=type(error).__name__)
    return added


def _usable_here(offered: OfferedFaces, here: str) -> tuple[OfferedFace, ...]:
    """The faces this install can use: all when the models agree, else only those with a picture."""
    if offered.recognizer == here:
        return offered.faces
    return tuple(face for face in offered.faces if face.picture is not None)


# --- the people offered for their facial fingerprints alone -----------------------------------


async def land_fingerprints(
    session: LandingSession,
    offer: models.Offer,
    *,
    matched: Mapping[int, str | None],
    skipped: Collection[int],
    database: Database,
    faces: FaceHolding | None,
) -> int:
    """Land the people offered for their fingerprints alone; returns how many were taken in."""
    if faces is None:
        return 0
    on_a_file = {index for one in offer.files for index in one.people}
    passed = set(skipped)
    candidates = [
        (index, person, _faces_of(person.faces))
        for index, person in enumerate(offer.people)
        if index not in on_a_file and index not in passed and person.faces is not None
    ]
    if not candidates:
        return 0
    here = await faces.recognizer()
    if here is None:
        log.info("swap.faces_not_held", swap=session.short_id, reason="faces_off")
        return 0
    arriving = [
        ArrivingPerson(
            name=person.name,
            aliases=tuple(person.aliases),
            local_id=matched.get(index),
            faces=decoded,
        )
        for index, person, decoded in candidates
        if decoded is not None and _usable_here(decoded, here)
    ]
    if not arriving:
        log.info("swap.faces_not_held", swap=session.short_id, reason="recognizer")
        return 0
    filed: list[_Filed] = []
    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        for person in arriving:
            person_id, is_new = await _person_for(connection, person)
            if person_id is None or any(one.person_id == person_id for one in filed):
                continue
            name = await store.person_name_on(connection, person_id) or person.name
            filed.append(_Filed(person, person_id, name, is_new))
    held = await _hold_faces(session, filed, faces)
    log.info(
        "swap.fingerprints_landed",
        swap=session.short_id,
        people=len(filed),
        created=sum(1 for one in filed if one.created),
        faces=held,
    )
    return held


# --- where it goes, and what it is called -----------------------------------------------------


#: The same set capture's own cleaner keeps, so what is chosen here is what lands.
_NOT_KEPT = re.compile(r"[^A-Za-z0-9 ()._\-]")
_LONGEST_FOLDER = 100
_LONGEST_STEM = 120
SWAP_FOLDER: Final = "Swap"
PEOPLE_FOLDER: Final = "People"
SITES_FOLDER: Final = "Sites"
_FALLBACK_STEM: Final = "Swapped file"


def _segment(text: str, *, longest: int, comma_as_dash: bool = True) -> str:
    """A name from the other side as one safe path segment, or "" when nothing is left."""
    folded = "".join(
        character
        for character in unicodedata.normalize(
            "NFKD", text.replace(",", " -") if comma_as_dash else text
        )
        if not unicodedata.combining(character)
    )
    cleaned = " ".join(_NOT_KEPT.sub(" ", folded).split())
    cleaned = cleaned[:longest].strip(" .-")
    try:
        return check_folder_name(cleaned)
    except InvalidFilename:
        return ""


def swap_folder(session: LandingSession) -> str:
    """The one folder everything this swap brings goes under: `Swap-<short id>`."""
    return f"{SWAP_FOLDER}-{session.short_id}"


def folder_name(
    received: Received, taken: Sequence[ArrivingPerson], *, session: LandingSession
) -> str:
    """Where under the destination: the swap's folder, then the first taken person's or Site's."""
    parent = swap_folder(session)
    for kind, candidate in (
        *((PEOPLE_FOLDER, person.name) for person in taken[:1]),
        (SITES_FOLDER, received.site or ""),
    ):
        name = _segment(candidate, longest=_LONGEST_FOLDER)
        if name:
            return f"{parent}/{kind}/{name}"
    return parent


def file_stem(received: Received) -> str:
    """The file's name without its extension: the sender's own name for it, else "Swapped file"."""
    # `_segment` already applied the kernel's name rule; the leaf only.
    leaf = PurePosixPath((received.name or "").replace("\\", "/")).name
    stem = PurePosixPath(leaf).stem
    return _segment(stem, longest=_LONGEST_STEM, comma_as_dash=False) or _FALLBACK_STEM


def file_suffix(received: Received) -> str | None:
    """The sender's extension as written, or None: kept only where the bytes are that kind."""
    suffix = PurePosixPath((received.name or "").replace("\\", "/")).suffix
    return suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix) else None


#: Written once by the first file landed; every later one finds it by a read.
_FOLDER_KNOWN: Final = "SELECT folder_id FROM swap_sessions WHERE id = ?"
_RECORD_FOLDER: Final = "UPDATE swap_sessions SET folder_id = ? WHERE id = ? AND folder_id IS NULL"


async def record_folder(database: Database, session: LandingSession, folder_id: str) -> None:
    """Keep the id of the folder this swap made on the session's row, for the folder pass."""
    known = await database.fetch_one(_FOLDER_KNOWN, (session.id,))
    if known is not None and known["folder_id"] is not None:
        return
    async with database.write() as connection:
        await connection.execute(_RECORD_FOLDER, (folder_id, session.id))


async def _folder_for(library: LibraryStore, dest_folder_id: str, name: str) -> FolderRow:
    """The named folder inside the destination, made if it is not there."""
    destination = await library.get_folder(dest_folder_id)
    if destination is None:
        raise NoDestination("That folder isn't there any more. Pick another one.")
    rel_path = f"{destination.rel_path}/{name}" if destination.rel_path else name
    return await library.upsert_folder(destination.root_id, rel_path)


# --- the whole digest ---------------------------------------------------------------------------

_DIGEST_CHUNK = 4 * 1024 * 1024


def whole_digest(path: Path) -> str:
    """The BLAKE3 of the whole file, hex. Blocking; Sift's own workspace, never a library file."""
    hasher = blake3()
    with path.open("rb") as handle:
        while chunk := handle.read(_DIGEST_CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest()


# The strip, again: the sender's own `transfer.strip`, run a second time on this side.


class StripError(Exception):
    """A file could not be stripped. It does not land."""


def _extension(media_type: MediaType) -> str:
    """The extension a stripped file is written with: its own name's where it has one."""
    own = f".{media_type.name}"
    return own if own in media_type.extensions else sorted(media_type.extensions)[0]


def _what_it_is(path: Path) -> MediaType | None:
    """What the bytes are, read the way the gate reads them. Blocking."""
    head, tail, _ = read_ends(path)
    outcome = classify(head, tail)
    return outcome if isinstance(outcome, MediaType) else None


def _named(source: Path, target: Path) -> Path:
    """Give the stripped copy its landing name in the strip's own scratch directory. Blocking."""
    os.replace(source, target)  # nosemgrep: sift-no-file-removal-outside-delete-trash
    return target


def _copied(source: Path, target: Path) -> Path:
    """The staged bytes, copied unchanged under the landing name. Blocking."""
    target.write_bytes(source.read_bytes())
    return target


async def strip(
    source: Path, into: Path, *, stem: str, settings: Settings, suffix: str | None = None
) -> Path:
    """Strip `source` again into `into/<stem>.<ext>`; a refused type passes on to the gate."""
    media_type = await asyncio.to_thread(_what_it_is, source)
    if media_type is None:
        return await asyncio.to_thread(_copied, source, into / f"{stem}.bin")
    try:
        stripped = await transfer.strip(source, into, settings=settings)
    except transfer.CannotStrip as error:
        raise StripError(str(error)) from error
    kind_of = suffix.lower() if suffix else None
    extension = suffix if kind_of in media_type.extensions else _extension(media_type)
    return await asyncio.to_thread(_named, stripped, into / f"{stem}{extension}")
