# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receiving side of a swap: a file that arrived lands through the one import, and only as offered.

Against a real library on a real disk, the real ingress gate, the real import pipeline and the real
ledger. The one stand-in is the face feature's door, which is another slice's and is proven where
it lives; what landing owes it (the right person, the right model, once) is what is asserted.

ONE real ffmpeg strip in this file (`test_a_video_lands_stripped_under_the_persons_folder`), plus
the one run that makes its tagged fixture. Every other landing here is a picture, whose strip is
the sender's own structural one and needs no ffmpeg.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import struct
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

# The two components a landing writes into beyond the kernel's: the swap's own rows, and the
# ledger's table.
import sift.slices.swap.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, FolderRow, LibraryStore, Root, songs
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import IngressRejected, Origin
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities, register_handler
from sift.kernel.migrations import check_allows, widen_a_check
from sift.slices.capture.pipeline import PROBE
from sift.slices.capture.pipeline import import_file as capture_import_file
from sift.slices.swap import ingest
from sift.slices.swap.ingest import (
    ArrivingPerson,
    ArrivingSong,
    LandingSession,
    OfferedFace,
    OfferedFaces,
    Received,
    Refused,
    land,
)

pytestmark = [pytest.mark.integration]

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"
SECRET = b"SIFTSECRET-47.6N-122.3W"
PEER = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
TITLE = "Ava Example, clip 14 of 38"
VECTOR = struct.pack("<2f", 0.1, 0.2)


# --- a library, a queue, a session ------------------------------------------------------------


class Told:
    """The search index, as a list of what it was told."""

    def __init__(self) -> None:
        self.told: list[str] = []

    async def touched(self, asset_id: str) -> None:
        self.told.append(asset_id)

    async def touched_many(self, asset_ids: Sequence[str]) -> None:
        self.told.extend(asset_ids)

    async def queue_many(self, asset_ids: Sequence[str]) -> None:  # pragma: no cover (no rename)
        await self.touched_many(asset_ids)

    async def renamed(self) -> None:
        return None


class Faces:
    """The face feature's pack import, remembering what it was handed."""

    def __init__(self, recognizer: str | None = "model-a") -> None:
        self._recognizer = recognizer
        self.held: list[tuple[str, str, str, int]] = []
        #: Who was held as a suggestion only, rather than added.
        self.suggested: list[str] = []
        #: The confirmed count handed on with each person held, by name.
        self.confirmed: dict[str, int | None] = {}

    async def recognizer(self) -> str | None:
        return self._recognizer

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
        self.held.append((pack, person, recognizer, len(faces)))
        self.confirmed[person] = confirmed
        if suggest_only:
            self.suggested.append(person)
        return len(faces)


@pytest.fixture
async def library(temp_db: Database, library_store: LibraryStore, tmp_path: Path) -> Root:
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Library", abs_path=directory)


@pytest.fixture
async def received_folder(library_store: LibraryStore, library: Root) -> FolderRow:
    """Where the guest said received files go."""
    return await library_store.upsert_folder(library.id, "Received")


@pytest.fixture
async def context(
    job_queue: JobQueue, content_store: ContentStore, library_store: LibraryStore
) -> JobContext:
    """The session job's own context, really queued and really claimed, as the import needs."""

    async def nothing(context: JobContext) -> None:
        return None

    register_handler("swap_landing_test", nothing, name="Test job")
    register_handler(PROBE, nothing, name="Test job")
    job_id = await job_queue.enqueue("swap_landing_test", {})
    worker = new_id()
    capabilities = SystemCapabilities(content=content_store, library=library_store)
    while True:
        job = await job_queue.claim(worker)
        assert job is not None
        if job.id == job_id:
            return JobContext(job=job, worker_id=worker, queue=job_queue, capabilities=capabilities)


@pytest.fixture
async def session(temp_db: Database, received_folder: FolderRow) -> LandingSession:
    session_id = new_id()
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO swap_sessions (id, role, state, peer_device, dest_folder_id, started_at)"
            " VALUES (?, 'guest', 'transferring', ?, ?, 1)",
            (session_id, PEER, received_folder.id),
        )
    return LandingSession(
        id=session_id, peer_device=PEER, dest_folder_id=received_folder.id, wanted=frozenset({"k1"})
    )


Lander = Callable[..., Awaitable[ingest.Landed]]


@pytest.fixture
def lander(
    session: LandingSession, context: JobContext, temp_db: Database, settings: Settings
) -> tuple[Lander, Told, Faces]:
    told, faces = Told(), Faces()

    async def run(received: Received, **overrides: object) -> ingest.Landed:
        return await land(
            overrides.pop("session", session),  # type: ignore[arg-type]
            received,
            ctx=context,
            import_file=partial(capture_import_file, settings=settings, reindexer=told),
            database=temp_db,
            reindexer=told,
            faces=overrides.pop("faces", faces),  # type: ignore[arg-type]
            settings=settings,
        )

    return run, told, faces


def _staged(tmp_path: Path, name: str, data: bytes) -> Path:
    """Reassembled bytes in the session's workspace, as the transfer leaves them."""
    workspace = tmp_path / "workspace" / "session"
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / name
    path.write_bytes(data)
    return path


def _photo_with_location() -> bytes:
    """The corpus's JPEG with an EXIF block carrying a location inserted after its start."""
    raw = (CORPUS / "accepted.jpg").read_bytes()
    payload = b"Exif\x00\x00" + SECRET
    return raw[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + raw[2:]


def _received(staged: Path, **fields: object) -> Received:
    values: dict[str, object] = {
        "key": "k1",
        "staged": staged,
        "digest": ingest.whole_digest(staged),
        "title": TITLE,
    }
    values.update(fields)
    return Received(**values)  # type: ignore[arg-type]


async def _received_count(database: Database, session_id: str) -> int:
    row = await database.fetch_one(
        "SELECT sent_files FROM swap_sessions WHERE id = ?", (session_id,)
    )
    assert row is not None
    return int(row["sent_files"])


async def _asset_count(database: Database) -> int:
    row = await database.fetch_one("SELECT COUNT(*) AS n FROM assets", ())
    assert row is not None
    return int(row["n"])


# --- the headline: a received file lands as offered, and as nothing else ------------------------


async def test_a_photo_lands_filed_as_offered_and_carries_nothing_else(
    lander: tuple[Lander, Told, Faces],
    session: LandingSession,
    temp_db: Database,
    library: Root,
    tmp_path: Path,
) -> None:
    """The People the guest took, the Site and the Username, the faces, and `added` by swap from
    the device, and not one other thing the sender had: no location, no rating, no tag, no
    person the guest skipped."""
    run, told, faces = lander
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES ('p-ava', ?, ?, 1)",
            ("Ava Example", "ava example"),
        )
    offered = OfferedFaces(
        recognizer="model-a", dimension=2, faces=(OfferedFace("d1", 0.9, VECTOR),)
    )
    received = _received(
        _staged(tmp_path, "k1.part", _photo_with_location()),
        name="IMG_2041.JPEG",
        site="Clipyard",
        username="avaexample",
        people=(
            ArrivingPerson(name="Ava", taken=True, local_id="p-ava", faces=offered),
            ArrivingPerson(name="Bryn Calloway", aliases=("bryncalloway",), faces=offered),
            ArrivingPerson(name="Dorian Halstead", taken=False),
        ),
    )

    landed = await run(received)

    # Under the swap's own folder, then People, then the first taken person, as offered; the name
    # is the sender's own name for the file, extension and all.
    location = await temp_db.fetch_one(
        "SELECT rel_path FROM asset_locations WHERE id = ?", (landed.location_id,)
    )
    assert location is not None
    assert location["rel_path"] == f"Received/Swap-{session.short_id}/People/Ava/IMG_2041.JPEG"
    landed_bytes = (Path(library.abs_path) / location["rel_path"]).read_bytes()
    assert SECRET not in landed_bytes, "the receiver strips again"
    # The swap's own folder is kept on the session by its id (`record_folder`).
    kept = await temp_db.fetch_one(
        "SELECT f.rel_path AS rel_path FROM swap_sessions s JOIN folders f ON f.id = s.folder_id"
        " WHERE s.id = ?",
        (session.id,),
    )
    assert kept is not None and kept["rel_path"] == f"Received/Swap-{session.short_id}"

    # The People: the match, and a new person with their alias, never the one skipped.
    people = {
        row["name"]: row
        for row in await temp_db.fetch_all(
            "SELECT p.name, ap.source, p.created_by_via FROM asset_people ap"
            " JOIN people p ON p.id = ap.person_id WHERE ap.asset_id = ?",
            (landed.asset_id,),
        )
    }
    assert set(people) == {"Ava Example", "Bryn Calloway"}
    assert {row["source"] for row in people.values()} == {"swap"}
    assert people["Bryn Calloway"]["created_by_via"] == "swap"
    assert landed.created and len(landed.people) == 2
    assert (
        await temp_db.fetch_one("SELECT 1 FROM people WHERE name = 'Dorian Halstead'", ()) is None
    )
    alias = await temp_db.fetch_one("SELECT 1 FROM people_aliases WHERE alias = 'bryncalloway'", ())
    assert alias is not None

    # The Site and the Username, by name, filed by the swap.
    filed = await temp_db.fetch_one(
        "SELECT s.name AS site, u.name AS username, au.source FROM asset_usernames au"
        " JOIN usernames u ON u.id = au.username_id JOIN sites s ON s.id = u.site_id"
        " WHERE au.asset_id = ?",
        (landed.asset_id,),
    )
    assert filed is not None
    assert (filed["site"], filed["username"], filed["source"]) == ("Clipyard", "avaexample", "swap")

    # The faces, under the name THIS library calls each person, once per person per session.
    assert [(person, model, count) for _, person, model, count in faces.held] == [
        ("Ava Example", "model-a", 1),
        ("Bryn Calloway", "model-a", 1),
    ]
    assert all(session.id in pack for pack, *_ in faces.held)
    assert landed.faces_held == 2
    # The person this library already had gets them as a suggestion; the new person, as theirs.
    assert faces.suggested == ["Ava Example"]

    # The arrival: one `added` about the file, by the swap, with the device and the session only.
    events = await temp_db.fetch_all(
        "SELECT d.verb, d.actor_kind, d.actor_id, d.payload FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE s.kind = 'asset' AND s.subject_id = ?",
        (landed.asset_id,),
    )
    assert [(e["verb"], e["actor_kind"], e["actor_id"]) for e in events] == [
        ("added", "sift", "swap")
    ]
    assert json.loads(events[0]["payload"]) == {"device": PEER, "session": session.short_id}
    assert await _received_count(temp_db, session.id) == 1
    assert landed.asset_id in told.told


async def test_a_received_file_carries_none_of_the_forbidden_facts(
    lander: tuple[Lander, Told, Faces], temp_db: Database, tmp_path: Path
) -> None:
    """The list the design names, checked row by row: nothing a sender held about a file but its
    People, its Site and Username and its faces is written for it here."""
    run, _, _ = lander
    landed = await run(_received(_staged(tmp_path, "k1.part", _photo_with_location())))

    asset = await temp_db.fetch_one(
        "SELECT title, download_url, release_date, original_filename FROM assets WHERE id = ?",
        (landed.asset_id,),
    )
    assert asset is not None
    # No title, no web address, no date. The sender had no name for it, so it is "Swapped file",
    # and never the offer's made-up title.
    assert (asset["title"], asset["download_url"], asset["release_date"]) == (None, None, None)
    assert asset["original_filename"] == "Swapped file.jpeg"
    tables = {
        str(row["name"])
        for row in await temp_db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'", ())
    }
    # Ratings, stars, O, favorites and views; tags; Collections; Photo Sets; People and
    # Usernames (none were offered on this file).
    forbidden = {
        "asset_user_state": "SELECT COUNT(*) AS n FROM asset_user_state WHERE asset_id = ?",
        "asset_tags": "SELECT COUNT(*) AS n FROM asset_tags WHERE asset_id = ?",
        "collection_items": "SELECT COUNT(*) AS n FROM collection_items WHERE asset_id = ?",
        "photo_set_items": "SELECT COUNT(*) AS n FROM photo_set_items WHERE asset_id = ?",
        "asset_people": "SELECT COUNT(*) AS n FROM asset_people WHERE asset_id = ?",
        "asset_usernames": "SELECT COUNT(*) AS n FROM asset_usernames WHERE asset_id = ?",
        # A song only when one was offered with the file: none was.
        "song_files": "SELECT COUNT(*) AS n FROM song_files WHERE asset_id = ?",
    }
    for table, count in forbidden.items():
        if table in tables:
            row = await temp_db.fetch_one(count, (landed.asset_id,))
            assert row is not None
            assert row["n"] == 0, f"{table} carries something for a received file"
    everything = json.dumps(
        [dict(row) for row in await temp_db.fetch_all("SELECT * FROM workbench_decisions", ())]
    )
    assert "k1.part" not in everything and SECRET.decode() not in everything


# --- the refusals -----------------------------------------------------------------------------


async def test_a_file_nobody_asked_for_is_refused_before_anything_reads_it(
    lander: tuple[Lander, Told, Faces], session: LandingSession, temp_db: Database, tmp_path: Path
) -> None:
    run, _, _ = lander
    staged = _staged(tmp_path, "k9.part", _photo_with_location())
    with pytest.raises(Refused) as caught:
        await run(_received(staged, key="k9"))
    assert caught.value.reason == ingest.UNWANTED
    assert await _asset_count(temp_db) == 0
    assert await _received_count(temp_db, session.id) == 0


async def test_bytes_that_are_not_the_bytes_announced_are_refused(
    lander: tuple[Lander, Told, Faces], session: LandingSession, temp_db: Database, tmp_path: Path
) -> None:
    run, _, _ = lander
    staged = _staged(tmp_path, "k1.part", _photo_with_location())
    with pytest.raises(Refused) as caught:
        await run(_received(staged, digest="0" * 64))
    assert caught.value.reason == ingest.DIGEST
    assert await _asset_count(temp_db) == 0
    assert await _received_count(temp_db, session.id) == 0


async def test_a_file_the_strip_cannot_read_is_refused_rather_than_let_in_unstripped(
    lander: tuple[Lander, Told, Faces], session: LandingSession, temp_db: Database, tmp_path: Path
) -> None:
    """A HEIC cut short: its last box runs past the end, so its tables cannot be read and what
    they hold cannot be taken out. It is not let in carrying it, whatever arrives."""
    run, _, _ = lander
    cut = (CORPUS / "accepted.heic").read_bytes()[:-16]
    staged = _staged(tmp_path, "k1.part", cut)
    with pytest.raises(Refused) as caught:
        await run(_received(staged))
    assert caught.value.reason == ingest.STRIP
    assert await _asset_count(temp_db) == 0
    assert await _received_count(temp_db, session.id) == 0


async def test_a_disguised_file_reaches_the_gate_and_is_set_aside_as_a_swap(
    lander: tuple[Lander, Told, Faces],
    session: LandingSession,
    temp_db: Database,
    settings: Settings,
    tmp_path: Path,
) -> None:
    """Not stripped, not refused quietly: the gate decides what bytes are, and a file from another
    install that is not media is quarantined with a note saying it came by swap."""
    run, _, _ = lander
    staged = _staged(tmp_path, "k1.part", (CORPUS / "disguised_exe.mp4").read_bytes())
    with pytest.raises(IngressRejected):
        await run(_received(staged))
    notes = list(settings.quarantine_dir.glob("*.why.json"))
    assert len(notes) == 1
    assert json.loads(notes[0].read_text(encoding="utf-8"))["origin"] == str(Origin.SWAP)
    assert await _received_count(temp_db, session.id) == 0
    assert staged.exists(), "the staged bytes are the session's to remove"


async def test_faces_from_another_model_are_refused_and_the_file_still_lands(
    lander: tuple[Lander, Told, Faces], temp_db: Database, tmp_path: Path
) -> None:
    run, _, _ = lander
    other = Faces(recognizer="model-b")
    offered = OfferedFaces(
        recognizer="model-a", dimension=2, faces=(OfferedFace("d1", 0.9, VECTOR),)
    )
    received = _received(
        _staged(tmp_path, "k1.part", _photo_with_location()),
        people=(ArrivingPerson(name="Bryn Calloway", faces=offered),),
    )
    landed = await run(received, faces=other)
    assert other.held == []
    assert landed.faces_held == 0
    assert await _asset_count(temp_db) == 1


async def test_faces_from_another_model_come_as_their_pictures_and_are_held(
    lander: tuple[Lander, Told, Faces], temp_db: Database, tmp_path: Path
) -> None:
    """The host sends the face pictures when the models differ; only the pictured faces go on."""
    run, _, _ = lander
    other = Faces(recognizer="model-b")
    offered = OfferedFaces(
        recognizer="model-a",
        dimension=2,
        faces=(
            OfferedFace("d1", 0.9, VECTOR, picture=b"\xff\xd8\xffface"),
            OfferedFace("d2", 0.8, VECTOR),
        ),
    )
    received = _received(
        _staged(tmp_path, "k1.part", _photo_with_location()),
        people=(ArrivingPerson(name="Bryn Calloway", faces=offered),),
    )
    landed = await run(received, faces=other)
    assert [(one[1], one[2], one[3]) for one in other.held] == [("Bryn Calloway", "model-a", 1)]
    assert landed.faces_held == 1


def test_a_face_picture_that_is_not_a_jpeg_never_reaches_a_decoder() -> None:
    import base64

    from sift.slices.swap.models import OfferedFace as WireFace
    from sift.slices.swap.models import OfferedFaces as WireFaces

    vector = base64.b64encode(VECTOR).decode()
    wire = WireFaces(
        recognizer="model-a",
        dimension=2,
        faces=[
            WireFace(
                digest="a",
                quality=0.9,
                vector=vector,
                picture=base64.b64encode(b"\xff\xd8\xffok").decode(),
            ),
            WireFace(
                digest="b", quality=0.8, vector=vector, picture=base64.b64encode(b"<svg/>").decode()
            ),
            WireFace(digest="c", quality=0.7, vector=vector, picture="not base64 at all!"),
        ],
    )
    decoded = ingest._faces_of(wire)
    assert decoded is not None
    assert [one.picture for one in decoded.faces] == [b"\xff\xd8\xffok", None, None]


def _offer_of_fingerprints(*names: str, recognizer: str = "model-a") -> Any:
    import base64

    from sift.slices.swap import models

    face = models.OfferedFace(
        digest="d1",
        quality=0.9,
        vector=base64.b64encode(VECTOR).decode(),
        picture=base64.b64encode(b"\xff\xd8\xffface").decode(),
    )
    return models.Offer(
        people=[
            models.OfferedPerson(
                name=name,
                aliases=[f"{name} Alias"],
                faces=models.OfferedFaces(
                    recognizer=recognizer, dimension=2, faces=[face], confirmed=17
                ),
            )
            for name in names
        ]
    )


async def _person_named(database: Database, name: str) -> str | None:
    row = await database.fetch_one("SELECT id FROM people WHERE name = ?", (name,))
    return None if row is None else str(row["id"])


async def test_people_offered_for_their_facial_fingerprints_alone_land_with_no_file(
    session: LandingSession, temp_db: Database
) -> None:
    """Somebody new is made with their aliases and given the fingerprints; somebody already here
    gets them as a suggestion; a person skipped is neither made nor held."""
    known = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'Bea Sample', 'bea', 1)",
        (known,),
    )
    faces = Faces()
    offer = _offer_of_fingerprints("Ada Example", "Bea Sample", "Ava Example")
    held = await ingest.land_fingerprints(
        session,
        offer,
        matched={1: known},
        skipped=[2],
        database=temp_db,
        faces=faces,
    )
    assert held == 2
    made = await _person_named(temp_db, "Ada Example")
    assert made is not None
    assert await _person_named(temp_db, "Ava Example") is None
    assert [one[1] for one in faces.held] == ["Ada Example", "Bea Sample"]
    assert faces.suggested == ["Bea Sample"]
    # How many confirmed faces the other side has of each travels on to what is held.
    assert faces.confirmed == {"Ada Example": 17, "Bea Sample": 17}
    alias = await temp_db.fetch_one("SELECT alias FROM people_aliases WHERE person_id = ?", (made,))
    assert alias is not None and alias["alias"] == "Ada Example Alias"
    assert await _asset_count(temp_db) == 0


async def test_nobody_is_made_for_fingerprints_this_install_cannot_use(
    session: LandingSession, temp_db: Database
) -> None:
    """Faces off, or another model's numbers with no pictures: no person is a name with nothing
    behind it."""
    offer = _offer_of_fingerprints("Ada Example")
    assert (
        await ingest.land_fingerprints(
            session, offer, matched={}, skipped=[], database=temp_db, faces=Faces(recognizer=None)
        )
        == 0
    )
    numbers_only = offer.model_copy(deep=True)
    assert numbers_only.people[0].faces is not None
    numbers_only.people[0].faces.faces[0].picture = None
    assert (
        await ingest.land_fingerprints(
            session,
            numbers_only,
            matched={},
            skipped=[],
            database=temp_db,
            faces=Faces(recognizer="model-b"),
        )
        == 0
    )
    assert await _person_named(temp_db, "Ada Example") is None


class _NeverAsked(Faces):
    async def recognizer(self) -> str | None:
        raise AssertionError("the face feature is asked only when somebody may land")


async def test_nobody_offered_for_fingerprints_alone_lands_nothing_and_asks_nothing(
    session: LandingSession, temp_db: Database
) -> None:
    """Without the face feature nothing lands. With it, a person on an offered file lands with
    that file and a skipped one not at all, so with no one else the feature is never asked."""
    from sift.slices.swap import models

    offer = _offer_of_fingerprints("Ada Example", "Bea Sample")
    assert (
        await ingest.land_fingerprints(
            session, offer, matched={}, skipped=[], database=temp_db, faces=None
        )
        == 0
    )
    on_a_file = offer.model_copy(
        update={
            "files": [
                models.OfferedFile(
                    key="k", size=1, identity="i", kind="video", title="clip", people=[0]
                )
            ]
        }
    )
    assert (
        await ingest.land_fingerprints(
            session, on_a_file, matched={}, skipped=[1], database=temp_db, faces=_NeverAsked()
        )
        == 0
    )
    assert await _person_named(temp_db, "Ada Example") is None
    assert await _person_named(temp_db, "Bea Sample") is None


async def test_two_offered_people_who_are_one_person_here_are_held_once_and_an_unclear_name_never(
    session: LandingSession, temp_db: Database
) -> None:
    """Two offered people the guest matched to the same person here are that person once; a name
    two people here answer to is left alone, as a file's person is."""
    known = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'Bea Sample', 'bea', 1)",
        (known,),
    )
    for _ in range(2):
        await temp_db.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'Juno Marsh', 'juno', 1)",
            (new_id(),),
        )
    faces = Faces()
    offer = _offer_of_fingerprints("Bea Sample", "Ava Example", "Juno Marsh")

    held = await ingest.land_fingerprints(
        session,
        offer,
        matched={0: known, 1: known},
        skipped=[],
        database=temp_db,
        faces=faces,
    )

    assert held == 1
    assert [one[1] for one in faces.held] == ["Bea Sample"]


# --- the strip, once, for real ------------------------------------------------------------------


async def test_a_video_lands_stripped_under_the_persons_folder(
    lander: tuple[Lander, Told, Faces],
    session: LandingSession,
    temp_db: Database,
    library: Root,
    settings: Settings,
    tmp_path: Path,
) -> None:
    """The sender's strip is not trusted: a title tag that arrives is gone from what lands."""
    run, _, _ = lander
    tagged = tmp_path / "tagged.mp4"
    made = subprocess.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(CORPUS / "accepted.mp4"),
            "-map",
            "0",
            "-c",
            "copy",
            "-metadata",
            f"title={SECRET.decode()}",
            str(tagged),
        ],
        capture_output=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr
    assert SECRET in tagged.read_bytes()
    staged = _staged(tmp_path, "k1.part", tagged.read_bytes())

    landed = await run(
        _received(
            staged,
            name="beach day, take 2.mp4",
            people=(ArrivingPerson(name="Bryn Calloway"),),
            site="Clipyard",
        )
    )

    location = await temp_db.fetch_one(
        "SELECT rel_path FROM asset_locations WHERE id = ?", (landed.location_id,)
    )
    assert location is not None
    assert (
        location["rel_path"]
        == f"Received/Swap-{session.short_id}/People/Bryn Calloway/beach day take 2.mp4"
    ), "the sender's name, held to the names a library keeps (a comma is not one)"
    assert SECRET not in (Path(library.abs_path) / location["rel_path"]).read_bytes()
    shutil.rmtree(tmp_path / "workspace")


# --- the folder and the name ------------------------------------------------------------------


@pytest.mark.unit
def test_the_folder_is_the_swaps_own_then_people_or_sites_then_the_entity(tmp_path: Path) -> None:
    """Everything one swap brings lands under `Swap-<short id>`, split by what it was offered under."""
    staged = tmp_path / "x"
    here = LandingSession(
        id="01KZTESTSESSION7K3QM2RD", peer_device=PEER, dest_folder_id="f", wanted=frozenset()
    )
    skipped = ArrivingPerson(name="Dorian Halstead", taken=False)
    taken = ArrivingPerson(name="Bryn Calloway")
    received = Received(key="k", staged=staged, digest="", title=TITLE, site="Clipyard")
    assert (
        ingest.folder_name(received, [taken], session=here) == "Swap-7K3QM2RD/People/Bryn Calloway"
    )
    both = Received(key="k", staged=staged, digest="", title=TITLE, people=(skipped, taken))
    taken_only = [p for p in both.people if p.taken]
    assert (
        ingest.folder_name(both, taken_only, session=here) == "Swap-7K3QM2RD/People/Bryn Calloway"
    )
    assert ingest.folder_name(received, [], session=here) == "Swap-7K3QM2RD/Sites/Clipyard"
    bare = Received(key="k", staged=staged, digest="", title=TITLE)
    assert ingest.folder_name(bare, [], session=here) == "Swap-7K3QM2RD"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "stem", "suffix"),
    [
        ("IMG_2041.JPEG", "IMG_2041", ".JPEG"),
        ("beach day (2).mp4", "beach day (2)", ".mp4"),
        ("../../etc/passwd", "passwd", None),
        ("C:\\Users\\someone\\clip.mov", "clip", ".mov"),
        ("CON.mp4", "Swapped file", ".mp4"),
        ("\u5199\u771f.jpg", "Swapped file", ".jpg"),
        ("Zo\u00eb.png", "Zoe", ".png"),
        (None, "Swapped file", None),
    ],
)
def test_a_received_file_keeps_its_own_name_and_only_ever_a_name(
    name: str | None, stem: str, suffix: str | None
) -> None:
    """The sender's name, the leaf alone and held to the names a library keeps; never the title."""
    received = Received(key="k", staged=Path("x"), digest="", title=TITLE, name=name)
    assert ingest.file_stem(received) == stem
    assert ingest.file_suffix(received) == suffix


async def test_a_received_name_already_taken_is_numbered(
    lander: tuple[Lander, Told, Faces],
    session: LandingSession,
    temp_db: Database,
    library: Root,
    tmp_path: Path,
) -> None:
    """A name the folder already holds is numbered, as any taken name is, and never overwritten."""
    run, _, _ = lander
    folder = Path(library.abs_path) / "Received" / f"Swap-{session.short_id}"
    folder.mkdir(parents=True)
    (folder / "IMG_2041.jpeg").write_bytes(b"already here")
    landed = await run(
        _received(_staged(tmp_path, "k1.part", _photo_with_location()), name="IMG_2041.jpeg")
    )
    location = await temp_db.fetch_one(
        "SELECT rel_path FROM asset_locations WHERE id = ?", (landed.location_id,)
    )
    assert location is not None
    assert location["rel_path"] == f"Received/Swap-{session.short_id}/IMG_2041-1.jpeg"
    assert (folder / "IMG_2041.jpeg").read_bytes() == b"already here"


@pytest.mark.unit
def test_the_offer_is_read_through_its_own_shapes(tmp_path: Path) -> None:
    """The file names its people by their index in the offer; the diff's match and the Take are
    read by that same index; a face that does not decode is one fewer face, never a lost file."""
    good = base64.b64encode(VECTOR).decode()
    people: list[dict[str, Any]] = [
        {
            "name": "Ava Example",
            "faces": {
                "recognizer": "model-a",
                "dimension": 2,
                "faces": [
                    {"digest": "d1", "quality": 0.9, "vector": good},
                    {"digest": "d2", "quality": 0.5, "vector": base64.b64encode(b"short").decode()},
                ],
            },
        },
        {"name": "Dorian Halstead"},
        {"name": "Bryn Calloway", "aliases": ["bryncalloway"]},
    ]
    file: dict[str, Any] = {
        "key": "k1",
        "size": 5,
        "identity": "i",
        "kind": "image",
        "title": TITLE,
        "site": "Clipyard",
        "username": "avaexample",
        "people": [0, 2],
    }
    received = ingest.received_from_offer(
        file, people, matched={0: "p-ava", 2: None}, taken={0}, staged=tmp_path, digest="ab"
    )
    assert (received.key, received.title, received.site, received.username) == (
        "k1",
        TITLE,
        "Clipyard",
        "avaexample",
    )
    ava, bryn = received.people
    assert (ava.name, ava.local_id, ava.taken) == ("Ava Example", "p-ava", True)
    assert (bryn.name, bryn.local_id, bryn.taken, bryn.aliases) == (
        "Bryn Calloway",
        None,
        False,
        ("bryncalloway",),
    )
    assert ava.faces is not None and [one.digest for one in ava.faces.faces] == ["d1"]
    with pytest.raises(ValueError):
        ingest.received_from_offer(
            {**file, "rating": 5}, people, matched={}, taken=None, staged=tmp_path, digest="ab"
        )


@pytest.mark.unit
def test_the_offer_carries_a_files_song_to_the_landing(tmp_path: Path) -> None:
    file: dict[str, Any] = {
        "key": "k1",
        "size": 5,
        "identity": "i",
        "kind": "video",
        "title": TITLE,
        "song": {"name": "Night Drive", "artists": ["Example Band"], "recording": "rec-1"},
    }
    received = ingest.received_from_offer(
        file, [], matched={}, taken=None, staged=tmp_path, digest="ab"
    )
    assert received.song == ArrivingSong("Night Drive", ("Example Band",), "rec-1")
    plain = ingest.received_from_offer(
        {**file, "song": None}, [], matched={}, taken=None, staged=tmp_path, digest="ab"
    )
    assert plain.song is None


@pytest.mark.unit
def test_a_file_naming_a_person_the_offer_does_not_list_is_refused(tmp_path: Path) -> None:
    file: dict[str, Any] = {
        "key": "k1",
        "size": 5,
        "identity": "i",
        "kind": "image",
        "title": TITLE,
        "people": [3],
    }
    with pytest.raises(ValueError, match="does not list"):
        ingest.received_from_offer(
            file, [{"name": "Ava Example"}], matched={}, taken=None, staged=tmp_path, digest="ab"
        )


@pytest.mark.unit
def test_a_face_that_is_not_base64_at_all_is_one_fewer_face(tmp_path: Path) -> None:
    good = base64.b64encode(VECTOR).decode()
    people: list[dict[str, Any]] = [
        {
            "name": "Ava Example",
            "faces": {
                "recognizer": "model-a",
                "dimension": 2,
                "faces": [
                    {"digest": "d1", "quality": 0.9, "vector": "not base64 at all!"},
                    {"digest": "d2", "quality": 0.5, "vector": good},
                ],
            },
        }
    ]
    file: dict[str, Any] = {
        "key": "k1",
        "size": 5,
        "identity": "i",
        "kind": "image",
        "title": TITLE,
        "people": [0],
    }

    received = ingest.received_from_offer(
        file, people, matched={}, taken=None, staged=tmp_path, digest="ab"
    )

    (ava,) = received.people
    assert ava.faces is not None and [one.digest for one in ava.faces.faces] == ["d2"]


async def test_people_arriving_are_found_by_their_name_here_and_never_guessed(
    lander: tuple[Lander, Told, Faces], temp_db: Database, tmp_path: Path
) -> None:
    """A name one person here answers to is that person; a name two answer to files nobody; a
    name with nothing in it makes nobody; the same person twice is filed once; and an alias that
    is only the name again is not written as an alias."""
    run, _, _ = lander
    async with temp_db.write() as connection:
        for person_id, name in (("p-orla", "Orla Tennant"), ("p-n1", "Nadia Vance")):
            await connection.execute(
                "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 1)",
                (person_id, name, name.lower()),
            )
        await connection.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES ('p-n2', ?, ?, 1)",
            ("Nadia Vance", "nadia vance"),
        )
    received = _received(
        _staged(tmp_path, "k1.part", _photo_with_location()),
        people=(
            ArrivingPerson(name="Orla Tennant"),
            ArrivingPerson(name="Orla Tennant", local_id="p-orla"),
            ArrivingPerson(name="Nadia Vance"),
            ArrivingPerson(name="   "),
            ArrivingPerson(name="Bryn Calloway", aliases=("BRYN CALLOWAY", "bryncalloway")),
        ),
    )

    landed = await run(received)

    names = [
        str(row["name"])
        for row in await temp_db.fetch_all(
            "SELECT p.name FROM asset_people ap JOIN people p ON p.id = ap.person_id"
            " WHERE ap.asset_id = ? ORDER BY p.name",
            (landed.asset_id,),
        )
    ]
    assert names == ["Bryn Calloway", "Orla Tennant"]
    aliases = await temp_db.fetch_all(
        "SELECT a.alias FROM people_aliases a JOIN people p ON p.id = a.person_id"
        " WHERE p.name = 'Bryn Calloway'",
        (),
    )
    assert [str(row["alias"]) for row in aliases] == ["bryncalloway"]


async def test_with_the_faces_feature_off_the_file_lands_and_no_face_is_held(
    lander: tuple[Lander, Told, Faces], temp_db: Database, tmp_path: Path
) -> None:
    run, _, _ = lander
    off = Faces(recognizer=None)
    offered = OfferedFaces(
        recognizer="model-a", dimension=2, faces=(OfferedFace("d1", 0.9, VECTOR),)
    )
    received = _received(
        _staged(tmp_path, "k1.part", _photo_with_location()),
        people=(ArrivingPerson(name="Bryn Calloway", faces=offered),),
    )

    landed = await run(received, faces=off)

    assert off.held == [] and landed.faces_held == 0
    assert await _asset_count(temp_db) == 1


async def test_a_pack_import_that_fails_never_takes_the_file_back_out(
    lander: tuple[Lander, Told, Faces], temp_db: Database, tmp_path: Path
) -> None:
    """Faces are a suggestion: the import refusing them is one missing, and the file stays in."""

    class Failing(Faces):
        async def hold(self, **_kwargs: Any) -> int:
            raise RuntimeError("the pack could not be read")

    run, _, _ = lander
    offered = OfferedFaces(
        recognizer="model-a", dimension=2, faces=(OfferedFace("d1", 0.9, VECTOR),)
    )
    received = _received(
        _staged(tmp_path, "k1.part", _photo_with_location()),
        people=(ArrivingPerson(name="Bryn Calloway", faces=offered),),
    )

    landed = await run(received, faces=Failing())

    assert landed.faces_held == 0
    assert await _asset_count(temp_db) == 1


async def test_a_destination_folder_deleted_since_it_was_chosen_is_said_in_words(
    lander: tuple[Lander, Told, Faces],
    session: LandingSession,
    temp_db: Database,
    tmp_path: Path,
) -> None:
    run, _, _ = lander
    gone = LandingSession(
        id=session.id, peer_device=PEER, dest_folder_id=new_id(), wanted=frozenset({"k1"})
    )

    from sift.kernel.ingress import NoDestination

    with pytest.raises(NoDestination, match="isn't there any more"):
        await run(_received(_staged(tmp_path, "k1.part", _photo_with_location())), session=gone)

    assert await _asset_count(temp_db) == 0


# --- the song a file came with -----------------------------------------------------------------

#: The CHECK on how a song came to be on a file, as the catalog writes it and as a step widens it.
_SONG_SOURCES_WERE = "source IN ('acoustid', 'site', 'shared')"
_SONG_SOURCES_NOW = "source IN ('acoustid', 'site', 'shared', 'swap')"


@pytest.fixture
async def swap_songs(temp_db: Database, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """The song's door taking a swap as a source, as the landing asks of it: `swap` among its
    sources, a song it makes made by the swap, and the CHECK widened the way a step widens one."""
    monkeypatch.setattr(songs, "SOURCES", (*songs.SOURCES, "swap"))
    monkeypatch.setitem(songs._MADE_BY_SOURCE, "swap", songs.Maker("sift", "swap"))
    async with temp_db.write() as connection:
        if not await check_allows(connection, "song_files", "swap"):
            await widen_a_check(
                connection, "song_files", was=_SONG_SOURCES_WERE, now=_SONG_SOURCES_NOW
            )
    yield


def _picture(shade: int) -> bytes:
    """A small JPEG of its own colour, so each one lands as a file of its own."""
    out = io.BytesIO()
    Image.new("RGB", (24, 16), (shade * 40 % 256, 90, 160)).save(out, "JPEG")
    return out.getvalue()


async def test_a_file_lands_on_its_song_by_recording_then_by_name_or_on_a_new_one(
    lander: tuple[Lander, Told, Faces],
    session: LandingSession,
    temp_db: Database,
    tmp_path: Path,
    swap_songs: None,
) -> None:
    run, _, _ = lander
    async with temp_db.write() as connection:
        known = await songs.song_called(
            connection, "Night Drive - Example Band", made=songs.UNSAID, recording_id="rec-known"
        )
        tide = await songs.song_called(connection, "Low Tide", made=songs.UNSAID)
    wide = LandingSession(
        id=session.id,
        peer_device=PEER,
        dest_folder_id=session.dest_folder_id,
        wanted=frozenset({"k1", "k2", "k3", "k4"}),
    )
    arriving = {
        # The recording outranks the name: the other side calls it something else.
        "k1": ArrivingSong("Called Otherwise", recording="rec-known"),
        # No recording: the name, case and spacing folded.
        "k2": ArrivingSong("  low   TIDE "),
        # Nothing here carries either: a new song, made by the swap.
        "k3": ArrivingSong("Harbour Lights", recording="rec-new"),
        # A name that cleans to nothing names no song.
        "k4": ArrivingSong("   "),
    }
    landed: dict[str, ingest.Landed] = {}
    for shade, (key, song) in enumerate(arriving.items(), start=1):
        staged = _staged(tmp_path, f"{key}.part", _picture(shade))
        landed[key] = await run(_received(staged, key=key, song=song), session=wide)

    assert (
        await temp_db.fetch_one(
            "SELECT 1 FROM song_files WHERE asset_id = ?", (landed.pop("k4").asset_id,)
        )
        is None
    )
    on: dict[str, Any] = {}
    for key, one in landed.items():
        row = await temp_db.fetch_one(
            "SELECT sf.song_id, sf.source, s.name, s.recording_id, s.created_by_kind,"
            " s.created_by_via, a.music FROM song_files sf JOIN songs s ON s.id = sf.song_id"
            " JOIN assets a ON a.id = sf.asset_id WHERE sf.asset_id = ?",
            (one.asset_id,),
        )
        assert row is not None, key
        on[key] = row
    assert on["k1"]["song_id"] == known and on["k1"]["music"] == "Night Drive - Example Band"
    assert on["k2"]["song_id"] == tide and on["k2"]["music"] == "Low Tide"
    made = on["k3"]
    assert made["song_id"] not in (known, tide)
    assert (made["name"], made["recording_id"]) == ("Harbour Lights", "rec-new")
    assert (made["created_by_kind"], made["created_by_via"]) == ("sift", "swap")
    assert {row["source"] for row in on.values()} == {"swap"}

    # Each file's History says the song came by swap, beside the arrival itself.
    acts = await temp_db.fetch_all(
        "SELECT d.verb, d.actor_kind, d.actor_id, d.payload FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE s.kind = 'asset' AND s.subject_id = ? ORDER BY d.id",
        (landed["k3"].asset_id,),
    )
    assert [(one["verb"], one["actor_kind"], one["actor_id"]) for one in acts] == [
        ("song_named", "sift", "swap"),
        ("added", "sift", "swap"),
    ]
    # And the device it came from, as the arrival names it, so the line says which swap.
    assert json.loads(acts[0]["payload"]) == {
        "song": "Harbour Lights",
        "song_id": made["song_id"],
        "source": "swap",
        "device": json.loads(acts[1]["payload"])["device"],
    }
    assert json.loads(acts[0]["payload"])["device"]


async def test_a_song_arriving_by_swap_credits_its_artists_where_it_credits_none(
    lander: tuple[Lander, Told, Faces], temp_db: Database, tmp_path: Path
) -> None:
    """The song's door takes a swap with no help (catalog 82): the file lands on its song, and the
    artists it was offered with are credited in order, by the swap, on a song crediting nobody.
    A song somebody here credited already keeps its own list."""
    run, _, _ = lander
    received = _received(
        _staged(tmp_path, "k1.part", _picture(1)),
        song=ArrivingSong("Harbour Lights - Odo Venn", ("Odo Venn", "Ilsa Moor"), "rec-new"),
    )

    landed = await run(received)

    row = await temp_db.fetch_one(
        "SELECT song_id, source FROM song_files WHERE asset_id = ?", (landed.asset_id,)
    )
    assert row is not None and row["source"] == "swap"
    credited = await temp_db.fetch_all(
        "SELECT a.name, sa.source, a.created_by_via FROM song_artists sa"
        " JOIN artists a ON a.id = sa.artist_id WHERE sa.song_id = ? ORDER BY sa.position",
        (row["song_id"],),
    )
    assert [(one["name"], one["source"], one["created_by_via"]) for one in credited] == [
        ("Odo Venn", "swap", "swap"),
        ("Ilsa Moor", "swap", "swap"),
    ]
    async with temp_db.write() as connection:
        assert not await songs.credit_where_none(
            connection, str(row["song_id"]), ["Marla Quist"], source=songs.CREDIT_FROM_SWAP
        )
