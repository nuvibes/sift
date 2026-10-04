# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receiving side of a swap: one file that arrived from another install, landed in the library.

Everything before this is transport. By the time a file reaches `land`, every 4 MiB piece of it has
been checked against its own digest and the pieces sit reassembled in the session's workspace. What
is left is the part that makes it a file in somebody's library, and it is the same part every other
arrival goes through: the swap is the seventh way in, not a second door:

    wanted?  -> the whole digest -> strip AGAIN -> `import_file` (the gate, the copy, the record,
    probing) -> the People, the Site and the Username it was offered under -> its song -> the
    faces held -> `added`, by swap, from that device

## Nothing the sender says is trusted, and each step says which lie it stops

- **Wanted.** The guest said which files it wanted, and a file it did not ask for is refused before
  anything reads it. The session refuses it first, at the frame that announces it; this is the
  second time, so a file that got past the first still does not land.
- **The whole digest.** Each piece was checked on arrival; the whole is checked again here, because
  the pieces being right one at a time is not the same claim as these being the bytes offered.
- **The strip, AGAIN.** The sender strips a file's metadata on the way out. A sender's strip is not
  a thing the receiver can see, so it is not a thing the receiver relies on: the file is stripped
  here as well, into a temporary file, and only that copy goes on. A title tag, a location, a
  camera's serial number or the name of the sender's machine would otherwise land in this library
  as the file's own facts.
- **The gate.** `import_file` runs `verify_ingress` with `Origin.SWAP`, exactly as it does for a
  download. What the bytes are is decided by the bytes.

## What a received file carries, and what it does not

It carries the People it was offered under (the ones the guest TOOK: a person skipped on the
screen is neither linked nor created), its Site and its Username (by name, never a web address),
the face descriptions of those People, its song, and the History line that says it arrived by swap
from a device, and its own name: it lands under the name the sender's file has on disk, numbered by
the folder on a clash as any taken name is. It carries NOTHING else: no rating, no stars, no O
count, no History from the other side, no notes, no folder, no address, no stash-box record, and
nobody on the file who was not offered. `tests/test_ingest.py` lists those and proves none is
written.

Its song is put on it as this library knows that song (`_put_song_on`): the song carrying the same
AcoustID recording, else the song of the same name (case and spacing folded, as the song's own door
folds them), else a new song Sift made by swap; only where the file carries no song of its own, so
bytes this library already held keep the song they have. The file's History says the song came by
swap. The song's other facts (its notes, its cover, the hearts and stars on it) stay where they
were, as a person's do.

## Where it lands

Everything one swap brings goes under ONE folder in the chosen destination, `Swap-<short id>`,
and inside it by the kind of thing it was offered under and then by that thing: `People/<person>`
for a file of a taken person, `Sites/<Site>` for one filed under a Site, and the swap's folder
itself for a file with neither. Not one folder per person or Site straight under the
destination, which would mix what a swap brought with folders of the same name that were
already there, and leave the receiver no one place that says "this came in that swap".

## Why the people, the Site and the faces come AFTER the import

The import is the one function that makes an asset, and it commits on its own. The rest is about a
row that must exist first. Written together in one transaction afterwards (the People, the
Site and Username, the `added` event and the session's count), so a file is never filed under
half of what it was offered with. The faces go last and apart, through the face feature's own
door, because they are that feature's rows and nothing here may write them.
"""

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
    """What landing needs to know about the session a file arrived in, and nothing more.

    Built by the session from its own row and the guest's diff. Deliberately not the row: landing
    reads four facts, and a type carrying the rest would invite a fifth.
    """

    #: The session's id, a ULID. Its last eight characters are the short id History shows.
    id: str
    #: The other install's device id, from the hello. Written into the `added` event; never an
    #: address, which no row anywhere holds.
    peer_device: str
    #: Where the guest said received files go ("Put received files in").
    dest_folder_id: str
    #: The offer keys the guest asked for, after every Take and Skip. A file whose key is not here
    #: is refused before anything reads it.
    wanted: frozenset[str]

    @property
    def short_id(self) -> str:
        """The last eight characters of the id: what History and the log call a session."""
        return self.id[-8:]


@dataclass(frozen=True, slots=True)
class OfferedFace:
    """One facial fingerprint the other side sent for a person, and the face picture it was read
    from when the two sides' models differ (see `_hold_faces`)."""

    #: The digest of the picture it was read from, which keys it: the same face offered twice is
    #: held once.
    digest: str
    quality: float
    #: The numbers as a face pack stores them: little-endian four-byte floats, `dimension` of them.
    vector: bytes
    #: The face square as a JPEG, or None: sent only when the other side uses another face model.
    picture: bytes | None = None


@dataclass(frozen=True, slots=True)
class OfferedFaces:
    """A person's face descriptions and the recognition model that made them."""

    recognizer: str
    dimension: int
    faces: tuple[OfferedFace, ...]
    #: How many confirmed faces the other side has of them, where it said: kept on what is held.
    confirmed: int | None = None


@dataclass(frozen=True, slots=True)
class ArrivingPerson:
    """One offered person on the file, with what the guest decided about them.

    `taken` is the guest's Take or Skip. A file can arrive because of one taken person while also
    being under a person the guest skipped; the skipped one is not linked, not created and does not
    name the folder: the guest said no to them.

    `local_id` is who the guest's diff matched them to (a shared stash-box id, then a name or an
    alias), or None: they arrive as a new person.
    """

    name: str
    aliases: tuple[str, ...] = ()
    taken: bool = True
    local_id: str | None = None
    faces: OfferedFaces | None = None


@dataclass(frozen=True, slots=True)
class ArrivingSong:
    """The song a file was offered with: its name, its artists where the sender keeps them apart
    from the name, and the AcoustID recording it is where one said."""

    name: str
    artists: tuple[str, ...] = ()
    recording: str | None = None


@dataclass(frozen=True, slots=True)
class Received:
    """One file whose every piece has arrived and been checked, waiting in the workspace."""

    #: The offer's key for the file: the host's asset id, opaque here.
    key: str
    #: The reassembled bytes, in the session's workspace. Sift's own; landing never removes it:
    #: the session does, with the manifest that names it.
    staged: Path
    #: The whole file's BLAKE3, hex, checked against what the sender said: its digest, or its pieces' digest.
    digest: str
    #: The offer's generated title ("Ava Example, clip 14 of 38"): what the offer screen said.
    title: str
    #: The Site and the Username the file had on the other side, by name. Never an address.
    site: str | None = None
    username: str | None = None
    #: The offered people on this file, in the offer's order.
    people: tuple[ArrivingPerson, ...] = ()
    #: The file's own name on the sender's disk, the leaf alone: what it lands under. None where
    #: the sender had none, and it is then called "Swapped file".
    name: str | None = None
    #: The file's song, where the offer named one (`models.OfferedSong`).
    song: ArrivingSong | None = None


@dataclass(frozen=True, slots=True)
class Landed:
    """What landing one file did."""

    asset_id: str
    location_id: str
    #: True when these exact bytes were already in the library: a second place they sit.
    was_duplicate: bool
    #: The folder the file went into: `Swap-<short id>` under the destination, then the person's
    #: or the Site's folder under that where it has one.
    folder_id: str
    #: Every person now on the file because of this landing, matched or new.
    people: tuple[str, ...] = ()
    #: The people this landing created, a subset of `people`.
    created: tuple[str, ...] = ()
    #: How many face descriptions the face feature took in.
    faces_held: int = 0


class Refused(Exception):
    """This file does not land. The session counts it as failed and goes on with the next one.

    Never raised for a file the gate refused: that is `IngressRejected`, and it keeps its own
    reason and its quarantine. These are the three things landing itself will not take.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"refused: {reason}")
        self.reason = reason


#: The file was not one the guest asked for.
UNWANTED: Final = "unwanted"
#: The reassembled bytes are not the bytes the sender announced.
DIGEST: Final = "digest"
#: The file could not be stripped, so it is not let in carrying whatever the strip would have
#: removed.
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
) -> Received:
    """The session's view of one received file, as landing reads it.

    `file` is the offer's entry for it and `people` the offer's WHOLE people list: the file names
    its people by their index in it, and the diff's `matched` is keyed by that same index. `taken`
    is the indexes of the people the guest took, None meaning all of them.

    Read through the offer's own wire shapes (`models.OfferedFile`, `models.OfferedPerson`), so a
    field the offer never declares is refused here exactly as it is on the wire, and nothing the
    shapes leave out (a rating, a note, a filename) can reach a landing by being in a mapping.
    """
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
    """The descriptions, decoded, each one that is not `dimension` four-byte numbers left out.

    Left out one at a time rather than refusing the person: a description is a suggestion, and one
    that does not decode is one fewer suggestion, not a reason to lose the others or the file.
    """
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


#: How every JPEG opens. A face picture is a JPEG square and nothing else: anything else in that
#: field is refused before a decoder is handed it.
_JPEG = b"\xff\xd8\xff"


def _picture_of(text: str | None) -> bytes | None:
    """A face picture from the offer, or None: one that does not decode, or is not a JPEG, is one
    fewer picture, and its fingerprint is still what it was."""
    if not text:
        return None
    try:
        picture = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    return picture if picture.startswith(_JPEG) else None


# --- the two doors this slice may not open itself ---------------------------------------------


class ImportFile(Protocol):
    """The one import pipeline, as landing needs it: capture's `import_file`, bound at boot.

    A slice may not import another slice, so it is handed in: the same shape and the same binding
    the downloader gets (`partial(capture.import_file, settings=..., reindexer=...)`).
    """

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
    """The face feature's pack import, as landing needs it.

    The descriptions go in the way a face pack's do (`faces.packs.read` refusing a pack made by a
    different model, then the import holding a person nobody here has and adding to one who is),
    because that is the one door into those rows, and a second would be free to forget the model
    check. Bound at the composition root, over the face feature's own service.
    """

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
        """Take in one person's descriptions as a pack called `pack`. Returns how many were added.

        `suggest_only` holds them for a person this library already had, rather than adding them:
        see `_hold_faces`; `confirmed` is the other side's confirmed count of them, kept on what is held.

        MUST be idempotent on `pack`: landing asks once per file, and every file of one person in
        one session hands the same descriptions under the same pack name. A pack that is already
        held is left exactly as it is.

        When `recognizer` is not this install's model, every face handed in carries its picture
        (`_hold_faces` sees to it), and the pictures are described again with this install's
        model on the way in: the numbers that came with them mean nothing here.
        """
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
    """Land one received file. Raises `Refused`, `IngressRejected` or `NoDestination` if it does not.

    See the module docstring for the order and what each step stops. `database` is the library's,
    for the writes after the import; `ctx` is the session job's own context, which the import runs
    its gate, its copy and its probe under.
    """
    if received.key not in session.wanted:
        # Before anything reads the bytes: the other side can send anything, and what the guest did
        # not ask for is not read, stripped, gated or kept.
        log.warning("swap.file_refused", swap=session.short_id, reason=UNWANTED)
        raise Refused(UNWANTED)

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
        # The folder only once there is a file to put in it: a refusal leaves no empty folder
        # behind in somebody's library.
        folder = await _folder_for(
            ctx.library, session.dest_folder_id, folder_name(received, taken, session=session)
        )
        # The swap's own folder, which the one above is inside or is: known as the swap's by its
        # id from here on (`record_folder`).
        top = await _folder_for(ctx.library, session.dest_folder_id, swap_folder(session))
        await record_folder(database, session, top.id)
        outcome = await import_file(
            path=stripped, origin=Origin.SWAP, dest_folder_id=folder.id, ctx=ctx
        )
    finally:
        await asyncio.to_thread(scratch.cleanup)

    filed = await _file_it(session, received, taken, outcome.asset_id, database)
    # The People and the Username are indexed text, written after the import told the index about
    # the file, so it is told again, as the downloader does after its own filing.
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
    #: What this library calls them: the name their faces are held under, so the face feature
    #: finds THIS person rather than whoever the other side's spelling would find.
    name: str
    created: bool


async def _file_it(
    session: LandingSession,
    received: Received,
    taken: Sequence[ArrivingPerson],
    asset_id: str,
    database: Database,
) -> list[_Filed]:
    """The People, the Site and Username, the arrival and the count: one transaction.

    Every row it makes says the swap made it (`by_sift(VIA_SWAP)`), and every filing it writes
    carries `swap` as its source, so each is found again by how it came.
    """
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
                # Their first file is their picture, as every pass that invents somebody does it:
                # a person made a file at a time is otherwise a wall of initials.
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
            # The device and the session, and NOTHING else: never an address, a token, a name the
            # other side gave the file, or anything it said about it.
            payload=json.dumps({"device": session.peer_device, "session": session.short_id}),
        )
        await store.count_landed_on(connection, session.id)
    return filed


#: How a song that came with a file is said to have come: the membership's source, the History
#: line's, and the via of a song Sift made for it.
SONG_SOURCE: Final = VIA_SWAP


async def _put_song_on(
    connection: Connection, session: LandingSession, asset_id: str, song: ArrivingSong | None
) -> str | None:
    """Put the song the file came with on it, through the song's one door (`songs.name_song_on`):
    the song this library has for the recording, else for the name, else one made by swap, and
    only where the file has no song yet. The song it is on now, or None. In the landing's own
    write, with the act on the file's History that says the song came by swap. The artists it was
    offered with are credited on the song where it credits none yet (`songs.credit_where_none`),
    as the lookup's are.
    """
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
        # The device the song came from, as the file's own `added` act names it, so the line says
        # which swap brought it.
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
    """Who this offered person is here, and whether this call made them.

    The guest's match first. Where the guest matched nobody (or matched somebody deleted since),
    they arrive as a new person with the name and aliases they were offered under. A person made
    by an earlier file of the same session is found again by that name rather than made twice;
    a name two people here now answer to is left alone, because which was meant is not a question
    a swap can answer.
    """
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
    """Hold each taken person's facial fingerprints, through the face feature's pack import.

    **When the models differ, the pictures are what is held.** Fingerprints from two models occupy
    different spaces, and comparing them does not give a worse answer: it gives a meaningless one
    that looks like matching quietly stopped working. So the host sends the face pictures beside
    them when the guest's hello named another model (`offer.FaceDescriptions`), and only the faces
    that came with a picture are handed on, to be described again here. A person whose faces came
    as numbers alone from another model (an older host, or pictures past the offer's budget) is
    refused with a log line: nothing is sent to the import that it would only refuse.

    **A person this library already had gets them as a suggestion, never as references.** The
    descriptions are somebody else's say-so about a person we know, and a reference is what names
    faces here, so they are held (`suggest_only`) until somebody gives them to that person. A person
    this swap made is new and has nothing of their own, so theirs are added as a pack's would be.

    Never fails the landing. The file is in; faces are what a person's page is recognized from, and
    a refusal here is a suggestion missing, not a file lost.
    """
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
    """The faces this install can do something with: every one when the models agree, and only the
    ones that came with their picture when they do not."""
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
    """Land the people the host offered for their facial fingerprints alone. Returns how many
    fingerprints the face feature took in.

    A person no offered file names arrives here and nowhere else: `land` only ever sees people on
    a file. Each one the guest took is matched or made exactly as a file's person is
    (`_person_for`: the guest's match, then the name, then somebody new with their aliases), and
    their fingerprints are held exactly as a file's person's are (`_hold_faces`): a person this
    library already had gets them as a suggestion to add from their page, a person made here gets
    them as their own, and the face feature then compares the library's unnamed faces with them
    (the re-check the pack import asks for). Taking such a person is the guest's yes to them, the
    same yes "Create People missing from this library" is for a file of them.

    Nobody is made when there is nothing this install can use: with the faces feature off, or
    fingerprints from another model that came without their pictures, a new person would be a
    name with nothing behind it.
    """
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


#: What a name keeps. The same set capture's own cleaner keeps, so what is chosen here is what
#: lands: a character outside it would only be turned into an underscore on the way in.
_NOT_KEPT = re.compile(r"[^A-Za-z0-9 ()._\-]")
_LONGEST_FOLDER = 100
_LONGEST_STEM = 120
#: The one folder a swap's arrivals go under, before the session's short id: `Swap-7K3QM2RD`.
SWAP_FOLDER: Final = "Swap"
#: Inside it, the folder for files of a taken person, and the one for files filed under a Site.
PEOPLE_FOLDER: Final = "People"
SITES_FOLDER: Final = "Sites"
#: The file's name when the sender had none, or its name leaves nothing usable: a name entirely in
#: a script the filename rule does not keep.
_FALLBACK_STEM: Final = "Swapped file"


def _segment(text: str, *, longest: int, comma_as_dash: bool = True) -> str:
    """A name the other side chose, made into one safe path segment, or "" when nothing is left.

    Accents are folded rather than dropped (an e with an accent is an e, not an underscore), a comma becomes a dash so
    "Ava Example, clip 14 of 38" keeps its shape, and anything else outside the kept set is a
    space. Then the kernel's own rule for a name (`check_folder_name`) has the last word: a
    reserved name, a trailing dot, anything a filesystem would quietly change.
    """
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
    """Where under the destination: the swap's folder, then `People/<the first taken person>`,
    else `Sites/<the Site>`, else the swap's folder itself."""
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
    # `_segment` has held it to the kernel's rule for a name already, and that is one rule for a
    # file and a folder (`check_filename` and `check_folder_name` differ only in their sentence).
    # The leaf only: a name with a folder in it is cut to its last part before anything reads it.
    leaf = PurePosixPath((received.name or "").replace("\\", "/")).name
    stem = PurePosixPath(leaf).stem
    return _segment(stem, longest=_LONGEST_STEM, comma_as_dash=False) or _FALLBACK_STEM


def file_suffix(received: Received) -> str | None:
    """The sender's extension as written, or None: kept only where the bytes are that kind."""
    suffix = PurePosixPath((received.name or "").replace("\\", "/")).suffix
    return suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix) else None


#: The swap's folder, written once: the first file landed names it, and every later one finds it
#: there already by a read, which costs a landing nothing a write would.
_FOLDER_KNOWN: Final = "SELECT folder_id FROM swap_sessions WHERE id = ?"
_RECORD_FOLDER: Final = "UPDATE swap_sessions SET folder_id = ? WHERE id = ? AND folder_id IS NULL"


async def record_folder(database: Database, session: LandingSession, folder_id: str) -> None:
    """Keep the id of the folder this swap made for what it lands, on the session's row.

    Read by the folder pass (`landed_folders`) to know the swap's folder for what it is, wherever
    it is and whatever it is called now. Nothing on a screen draws it, so nothing is announced.
    """
    known = await database.fetch_one(_FOLDER_KNOWN, (session.id,))
    if known is not None and known["folder_id"] is not None:
        return
    async with database.write() as connection:
        await connection.execute(_RECORD_FOLDER, (folder_id, session.id))


async def _folder_for(library: LibraryStore, dest_folder_id: str, name: str) -> FolderRow:
    """The named folder inside the destination, made if it is not there.

    The destination is checked the way capture checks it and with the same sentence, because the
    person reading it is the same person: a folder deleted since it was chosen.
    """
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


# --- the strip, AGAIN ---------------------------------------------------------------------------
#
# ONE STRIP, the sender's own (`transfer.strip`), run a second time on this side: two copies of
# "what metadata is" would come to disagree (a phone photo's orientation is the easy one to lose),
# so the rule deciding what leaves a person's library is written once. A stream copy takes a title
# and a creation time out of a video container, nothing out of a JPEG or a PNG, and cannot write an
# animated WebP or a HEIC, so each picture format is stripped by its own structure; a type with no
# strip, or whose structure cannot be read, is refused (`transfer.CannotStrip`).


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
    """Give the stripped copy the name it lands under. Blocking.

    A rename inside the strip's own scratch directory: both paths are under a directory this
    landing made a moment ago in Sift's workspace, and neither is anybody's library.
    """
    os.replace(source, target)  # nosemgrep: sift-no-file-removal-outside-delete-trash
    return target


def _copied(source: Path, target: Path) -> Path:
    """The staged bytes, copied unchanged under the landing name. Blocking."""
    target.write_bytes(source.read_bytes())
    return target


async def strip(
    source: Path, into: Path, *, stem: str, settings: Settings, suffix: str | None = None
) -> Path:
    """Strip `source` again into `into/<stem>.<ext>` and return that path. Raises `StripError`.

    The extension is the sender's (`suffix`) where it is one of the kinds the bytes are, and the
    kind's own otherwise: a name never decides what a file is, but a `.jpeg` stays a `.jpeg`.

    What the file IS is read from its bytes, never from a name, and the name here came from the
    other side. A file whose bytes are not an accepted type is NOT stripped and NOT refused here:
    it is handed on unchanged, so the gate, the one judge of that, refuses it, sets it aside and
    writes the security record a disguised file from another install deserves.
    """
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
