# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the stash-box job tests share: stand-ins for the access layer, the boxes and the writers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sift.slices.workbench.schema  # noqa: F401 (the ledger's table registers itself)
from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.kernel.enrichment import Decision, Enricher, Missing, Plan
from sift.kernel.enrichment import Outcome as FieldOutcome
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.adapter import Box
from sift.slices.stash_boxes.jobs import (
    Outcome,
    ScanDeps,
)
from sift.slices.stash_boxes.service import EXACT, StashBoxService

A_KEY = b"0" * 32


ADMIN = Viewer(id="admin", role=Role.ADMIN)


# --- the world the jobs run in ----------------------------------------------------------------


@dataclass
class _Asset:
    id: str
    oshash: str | None = "os"
    video_phash: str | None = "ph"
    duration_ms: int | None = 60_000


@dataclass
class _View:
    asset: _Asset
    #: Whether this row came back as a locked placeholder rather than as the file itself. The real
    #: `AssetView` carries the same flag for the same reason. See `_Access`.
    concealed: bool = False


@dataclass
class _Page:
    items: list[_View]
    total: int


class _Access:
    """The permission layer, stood in for. `hidden` is what THIS USER HAS VAULTED.

    The two reads differ here because they differ in the real one. A double that answered None
    from `get_asset` for a hidden id would be stricter than the thing it stands for, and could not
    catch a job reading through the wrong one. A double may be simpler than the real thing; it may
    not be safer than it.

    So `get_asset` hands back the row with the tile flagged `concealed`, exactly as the repository
    does for a viewer whose mode keeps the tile, and `open_asset` is the strict one that refuses.
    """

    def __init__(self, assets: Sequence[_Asset] = (), hidden: set[str] | None = None) -> None:
        self.assets = list(assets)
        self.hidden = hidden or set()
        self.pages: list[tuple[int, int]] = []
        #: Every filter a page was asked for with, so a scoped sweep can be told from a whole one.
        self.filters: list[object] = []
        #: What a real access layer does and a naive stand-in would not: cap the page.
        self.cap: int | None = None
        #: Whom a sweep nobody pressed reads the library as. None stands for an instance whose
        #: admins are all gone or disabled.
        self.admin: str | None = "admin"

    async def an_admin(self) -> str | None:
        """One enabled admin, or None where there is none. See `Repository.an_admin`."""
        return self.admin

    async def get_asset(self, viewer: Viewer, asset_id: str) -> _View | None:
        """The row, INCLUDING a vaulted one as a locked placeholder. See the note on the class."""
        _ = viewer
        for asset in self.assets:
            if asset.id == asset_id:
                return _View(asset=asset, concealed=asset_id in self.hidden)
        return None

    async def open_asset(self, viewer: Viewer, asset_id: str) -> _Asset | None:
        """The row only where the caller may have its CONTENT. Never a placeholder."""
        _ = viewer
        if asset_id in self.hidden:
            return None
        return next((one for one in self.assets if one.id == asset_id), None)

    async def visible_assets(
        self, viewer: Viewer, *, limit: int, offset: int = 0, asset_filter: object = None
    ) -> _Page:
        _ = viewer
        self.pages.append((limit, offset))
        self.filters.append(asset_filter)
        wanted = limit if self.cap is None else min(limit, self.cap)
        return _Page(
            items=[_View(one) for one in self.assets[offset : offset + wanted]],
            total=len(self.assets),
        )


class _Settings:
    def __init__(self, values: Mapping[str, object] | None = None) -> None:
        self.values = dict(values or {})

    async def get_app(self, key: str) -> object:
        return self.values.get(key)


class _Enricher:
    """The writers, stood in for. Records what it was asked to plan and to apply."""

    def __init__(
        self,
        *,
        writes: tuple[str, ...] = ("title",),
        plans: bool = True,
        missing: tuple[Missing, ...] = (),
    ) -> None:
        self.writes = writes
        self.plans = plans
        self.missing = missing
        self.applied: list[tuple[str, object]] = []

    async def plan_for(self, **kwargs: Any) -> object | None:
        return object() if self.plans else None

    async def apply(self, plan: object, *, creating: object) -> tuple[str, ...]:
        _ = plan
        self.applied.append(("apply", creating))
        return self.writes

    async def missing_for(self, plan: object) -> tuple[Missing, ...]:
        _ = plan
        return self.missing


class _Adapter:
    def __init__(self, answers: list[FoundRecord] | None = None) -> None:
        self.answers = answers or []
        self.asked: list[Mapping[str, str]] = []

    async def search(self, box: Box, term: str) -> list[FoundRecord]:  # pragma: no cover
        raise AssertionError("the pass never searches by name")

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        self.asked.append(dict(hashes))
        return list(self.answers)


class _Secrets:
    def __init__(self, key: bytes | None) -> None:
        self.key = key

    async def master_key(self) -> bytes | None:
        return self.key


def _found(confidence: float, *, duration_ms: int | None = None) -> FoundRecord:
    fields: dict[str, object] = {}
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    return FoundRecord(
        source_id="box",
        remote_id="r1",
        subject=Subject.ASSET,
        name="A Clip",
        fields=fields,
        confidence=confidence,
    )


class _Naming:
    """Turns a name back into the row that was just made for it.

    `known` is what the library holds; anything the job invented is put there by the test that
    invented it, because this stands in for a seam that is asked AFTER the write.
    """

    def __init__(self, known: dict[tuple[str, str], str] | None = None) -> None:
        self.known = dict(known or {})
        #: Which rows were recorded as invented by a box, as (kind, id, box).
        self.made_by: list[tuple[str, str, str]] = []

    async def person_named(self, name: str, *, creating: bool) -> str | None:
        _ = creating
        return self.known.get(("person", name))

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None:
        _ = creating
        return self.known.get(("site", name))

    async def tag_named(self, name: str, *, creating: bool) -> str | None:
        _ = creating
        return self.known.get(("tag", name))

    async def mark_pmv_creator(self, person_id: str) -> None:  # pragma: no cover: the file's
        """...writer says this, not the job. Carried so the double answers the whole seam."""
        _ = person_id

    async def mark_created_by_box(self, kind: str, local_id: str, source_id: str) -> None:
        self.made_by.append((kind, local_id, source_id))


def _deps(
    access: _Access,
    settings: _Settings,
    enricher: _Enricher | Enricher,
    *,
    viewer: Viewer | None = ADMIN,
    naming: _Naming | None = None,
) -> ScanDeps:
    async def viewer_for(user_id: str) -> Viewer | None:
        _ = user_id
        return viewer

    return ScanDeps(
        access=access,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        enricher=enricher,  # type: ignore[arg-type]
        naming=naming or _Naming(),
        viewer_for=viewer_for,
    )


async def _never_called(context: JobContext) -> None:  # pragma: no cover - see `handlers`
    raise AssertionError("the handlers are driven directly by these tests")


async def _a_box(service: StashBoxService, adapter: _Adapter) -> str:
    """Configure one box, with the adapter this test wants behind it.

    Replaced on the service rather than handed to its constructor because these tests build the
    service through the ordinary fixture, and what they vary is the one thing behind it that would
    otherwise reach a network.
    """
    service._adapter = adapter  # type: ignore[assignment]
    return await service.add(
        name="StashDB",
        endpoint="https://stashdb.example/graphql",
        api_key="a-real-key",
        master_key=A_KEY,
    )


async def _a_file(temp_db: Database, asset_id: str) -> None:
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        (asset_id, asset_id),
    )


async def _admin_exists(temp_db: Database) -> None:
    """Put the stand-in admin in the users table. A sweep that finishes writes the user who
    pressed it down by a foreign key, so the user has to be a row and not only a viewer a test
    made up."""
    await temp_db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at, disabled)"
        " VALUES (?, ?, ?, ?, ?, 0)",
        (ADMIN.id, ADMIN.id, "x", Role.ADMIN.value, 0),
    )


async def _asked_events(temp_db: Database) -> list[tuple[object, ...]]:
    """Every `asked` event the ledger holds, as the columns a sweep's record is made of."""
    rows = await temp_db.fetch_all(
        "SELECT verb, actor_kind, actor_id, object_kind, object_id, object_name, touched"
        " FROM workbench_decisions WHERE verb = 'asked' ORDER BY id"
    )
    return [tuple(row) for row in rows]


async def _children(context: JobContext, job_type: str) -> list[dict[str, Any]]:
    """What one job queued underneath itself. Read through the queue rather than counted, because
    what matters is the payload each child carries."""
    rows = await context.queue.children(context.job.id)
    return [dict(one.payload) for one in rows if one.type == job_type]


# --- the batch that asks about people, Sites and tags ---------------------------------------


class _Entities:
    """The enricher, stood in for. It answers whatever the test lined up, in order.

    An exception in that list is raised rather than returned, because asking about one subject
    can go wrong (a stash-box repeating a name the library already holds, say), and what the
    batch does about it is under test.
    """

    def __init__(self, outcomes: list[Outcome | Exception] | None = None) -> None:
        self._outcomes = list(outcomes or [])
        self.asked: list[tuple[Subject, str, str, bytes | None]] = []
        #: Which box each ask named, or None for every switched-on one. Kept rather than dropped:
        #: it is the flyout's whole answer, and a double that swallowed it would let the batch stop
        #: passing it with every test still green.
        self.boxes: list[str | None] = []
        #: Every row linked by an id the batch carried, rather than searched by name.
        self.known: list[tuple[Subject, str, str, str, bytes | None]] = []

    async def link_known(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> Outcome:
        self.known.append((subject, local_id, box_id, remote_id, master_key))
        return Outcome.LINKED

    async def enrich(
        self,
        subject: Subject,
        local_id: str,
        name: str,
        master_key: bytes | None,
        *,
        only: str | None = None,
    ) -> Outcome:
        self.asked.append((subject, local_id, name, master_key))
        self.boxes.append(only)
        answer = self._outcomes.pop(0) if self._outcomes else Outcome.UNKNOWN
        if isinstance(answer, Exception):
            raise answer
        return answer


# --- the people a box invented before they were linked to it ------------------------------------


async def _people_a_box_invented(service: StashBoxService, temp_db: Database) -> str:
    """A box Sift has a word for, one person it invented and never linked, one it invented and did
    link, and one it did not invent at all. The adapter is the stand-in: nothing reaches a network."""
    service._adapter = _Adapter()  # type: ignore[assignment]
    box = await service.add(
        name="StashDB",
        endpoint="https://stashdb.org/graphql",
        api_key="a-real-key",
        master_key=A_KEY,
    )
    # Invented for this file; see `tests/gates/data/names_cast.txt`.
    for person_id, name, made_by in (
        ("p-bare", "Neve Arbor", box),
        ("p-linked", "Neve Arbogast", box),
        ("p-yours", "Neve Arb", None),
    ):
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at, created_by_kind, created_by_box_id)"
            " VALUES (?, ?, 0, ?, ?)",
            (person_id, name, "box" if made_by else "user", made_by),
        )
    await temp_db.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES ('p-linked', ?, 'r1', '[]', 0)",
        (box,),
    )
    return box


class _KnowsHer(_Adapter):
    """A box that recognises the file, credits one person by its own id, and knows her by it."""

    def __init__(self) -> None:
        found = _found(EXACT)
        super().__init__(
            [
                FoundRecord(
                    source_id=found.source_id,
                    remote_id=found.remote_id,
                    subject=Subject.ASSET,
                    name="A Clip",
                    fields={"title": "A Clip", "people": ["Odette Varnley"]},
                    confidence=EXACT,
                    refs={"person": {"Odette Varnley": "pf-1"}},
                )
            ]
        )
        self.people_asked: list[str] = []

    async def person(self, box: Box, remote_id: str) -> FoundRecord | None:
        self.people_asked.append(remote_id)
        return FoundRecord(
            source_id=box.id,
            remote_id=remote_id,
            subject=Subject.PERSON,
            name="Odette Varnley",
            fields={"name": "Odette Varnley"},
            image_url="https://box.example/odette.jpg",
        )

    async def picture(
        self, box: Box, url: str, *, vector: bool = False
    ) -> tuple[bytes, str] | None:
        _ = (box, url)
        return (b"\x89PNG\r\n\x1a\n", "image/png")


class _Inventing:
    """The writers, for a file whose answer names one person nobody has yet.

    The FILE's plan writes its title and needs her made; applying it with permission makes her row,
    as the real person writer would. HER plan, once she is linked, finds nothing more to fill.
    """

    def __init__(self, temp_db: Database, naming: _Naming) -> None:
        self._db = temp_db
        self._naming = naming

    async def plan_for(self, *, subject: Subject, local_id: str, source_id: str, **_: Any) -> Plan:
        decisions = (
            (Decision(key="title", outcome=FieldOutcome.WRITE, value="A Clip"),)
            if subject is Subject.ASSET
            else ()
        )
        return Plan(subject=subject, local_id=local_id, source_id=source_id, decisions=decisions)

    async def missing_for(self, plan: Plan) -> tuple[Missing, ...]:
        if plan.subject is not Subject.ASSET:
            return ()
        return (Missing(name="Odette Varnley", kind=Subject.PERSON.value),)

    async def apply(self, plan: Plan, *, creating: object) -> dict[str, int]:
        wanted = (Subject.PERSON.value, "Odette Varnley")
        if plan.subject is Subject.ASSET and isinstance(creating, frozenset) and wanted in creating:
            await self._db.execute(
                "INSERT INTO people (id, name, created_at) VALUES ('p-new', 'Odette Varnley', 0)"
            )
            self._naming.known[wanted] = "p-new"
        return {key: 1 for key in plan.writes}


class _Covers:
    """The subject covers: what landed, and on whom."""

    def __init__(self) -> None:
        self.filled: dict[tuple[Subject, str], bytes] = {}

    async def has_one(self, subject: Subject, local_id: str) -> bool:
        return (subject, local_id) in self.filled

    async def fill(
        self, subject: Subject, local_id: str, blob: bytes, *, actor: object, box: object
    ) -> bool:
        _ = (actor, box)
        self.filled[(subject, local_id)] = blob
        return True


# --- a creator's picture, after their username lands ----------------------------------------------


def _a_creators_scene(*, studio_id: str | None = "studio-9") -> FoundRecord:
    """A scene a box files under a creator's own studio: a username on OnlyFans, with its id."""
    return FoundRecord(
        source_id="box",
        remote_id="r1",
        subject=Subject.ASSET,
        name="A Clip",
        fields={
            "accounts": [
                {"site": "OnlyFans", "handle": "quillmoss", "url": "https://onlyfans.com/quillmoss"}
            ]
        },
        confidence=EXACT,
        refs={"username": {"quillmoss": studio_id}} if studio_id else {},
    )


async def _queued(temp_db: Database, job_type: str) -> list[dict[str, Any]]:

    rows = await temp_db.fetch_all("SELECT payload FROM jobs WHERE type = ?", (job_type,))
    return [json.loads(str(row["payload"])) for row in rows]


class _Studios(_Adapter):
    """A box that keeps a picture for the creator's studio."""

    def __init__(self) -> None:
        super().__init__()
        self.studios: list[str] = []

    async def site(self, box: Box, remote_id: str) -> FoundRecord | None:
        self.studios.append(remote_id)
        return FoundRecord(
            source_id=box.id,
            remote_id=remote_id,
            subject=Subject.SITE,
            name="quillmoss (OnlyFans)",
            image_url="https://stashdb.example/images/studio-9",
        )

    async def picture(
        self, box: Box, url: str, *, vector: bool = False
    ) -> tuple[bytes, str] | None:
        return (b"\x89PNG\r\n\x1a\nstudio", "image/png")


class _Pictures:
    """The creator-picture seam, stood in for: it asks for the bytes only for a blank."""

    def __init__(self, *, held: bool = False) -> None:
        self.held = held
        self.kept: list[tuple[str, str, str | None, bytes]] = []

    async def keep(
        self,
        *,
        site: str,
        username: str,
        address: str | None,
        picture: Callable[[], Awaitable[bytes | None]],
    ) -> bool:
        if self.held:
            return False
        blob = await picture()
        if blob is None:
            return False
        self.kept.append((site, username, address, blob))
        return True
