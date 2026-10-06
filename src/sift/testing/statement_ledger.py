# SPDX-License-Identifier: AGPL-3.0-or-later
"""The statement ledger: what every screen and press asks of SQLite, priced in its steps.

The synthetic library is built at two sizes and the real application is booted over each. Every
screen's reads on open and every press on a handful of files are asked as the admin and as a
guest, with the budget hearing every statement (`StatementBudget.heard`), so each one comes back
with SQLite's own count of its work: the same on any machine and under any load. The record
(`tests/gates/data/statement_ledger.json`) keeps, per SQLite version, each statement's steps at
both sizes, how many times one request runs it, and, where it may grow, what it is priced by.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Repository
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import LIBRARY_SIZED_TABLES, Database, StatementRun, statement_budget
from sift.kernel.db_base import DATABASE_FILENAME
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.testing.auth import TEST_PIN, establish_session, give_pin
from sift.testing.authz import _conditions
from sift.testing.fixture_library import FixtureLibrary, fixture_library, questions
from sift.wiring import lifespan

#: The two libraries every statement is priced on, and the seed both are built from.
SIZES = (20_000, 80_000)
SEED = 7

#: A rise is a rise past this share of the record plus this many steps: room for the noise a
#: boot's own rows bring, and none for a statement that started walking something new.
RISE_SHARE = 0.10
RISE_SLACK = 200

#: How much a request statement may grow between the sizes when its record names no subject.
HELD_GROWTH = 1.5

#: What a statement may be priced by, and the growth each allows: a page holds; the rest grow
#: with the library, which is four times bigger at the second size.
SUBJECTS = {"page": HELD_GROWTH, "file": 4.4, "person": 4.4, "folder": 4.4, "library": 4.4}

RECORD = Path(__file__).resolve().parents[3] / "tests" / "gates" / "data" / "statement_ledger.json"

#: The stage whose whole-library reads are allowed: the lane built for them.
SWEEP = "db.sweep"

_PASSWORD = "fixture-ledger"

#: Which walked request a statement ran for; None outside one (a boot's or a timer's work).
_DURING: ContextVar[str | None] = ContextVar("statement_ledger_during", default=None)

_LABEL_HEADER = "x-statement-ledger"


@dataclass
class Seen:
    """One statement over a walk at one size."""

    stage: str
    steps: int = 0
    count: int = 0
    requests: set[str] = field(default_factory=set)
    sql: str = ""
    params: Any = ()
    #: The library-sized tables its plan walks end to end, with the values it bound.
    scans: list[str] = field(default_factory=list)


@dataclass
class Walk:
    """What one walk at one size found."""

    seen: dict[str, Seen]
    statuses: dict[str, int]
    library: FixtureLibrary


#: The label of the boot's window: from the lifespan's start until it says `boot.ready`.
BOOT = "boot"


class _Heard(list[StatementRun]):
    """The budget's ear: each run kept with the request it ran for, the rest dropped. The boot's
    window closes at `boot.ready`; work started inside it and run after is not the boot's."""

    def __init__(self) -> None:
        super().__init__()
        self.by_request: list[tuple[str, StatementRun]] = []
        self.ready = False

    def append(self, run: StatementRun) -> None:
        during = _DURING.get()
        if during is not None and not (during == BOOT and self.ready):
            self.by_request.append((during, run))


class _ReadySpy:
    """The lifespan's logger, closing the boot's window when it says the application is ready."""

    def __init__(self, log: Any, heard: _Heard) -> None:
        self._log = log
        self._heard = heard

    def info(self, event: str, *args: Any, **fields: Any) -> Any:
        if event == "boot.ready":
            self._heard.ready = True
        return self._log.info(event, *args, **fields)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._log, name)


class _Labelled:
    """The application, told which walked request each call is, by a header the walk sends."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        label = BOOT if scope["type"] == "lifespan" else None
        if scope["type"] == "http":
            for name, value in scope["headers"]:
                if name.decode("latin-1") == _LABEL_HEADER:
                    label = value.decode("latin-1")
        token = _DURING.set(label)
        try:
            await self.app(scope, receive, send)
        finally:
            _DURING.reset(token)


@dataclass(frozen=True)
class Ask:
    """One request of the walk: who asks, the label it is recorded under, and the request."""

    who: str
    label: str
    method: str
    path: str
    body: dict[str, Any] | None = None


def _everyones_reads(lib: FixtureLibrary) -> dict[str, str]:
    """What a screen reads on open, asked as the admin and as a guest alike."""
    reads = {
        "GET /api/auth/me": "/api/auth/me",
        "GET /api/settings": "/api/settings",
        "GET /api/settings/interface": "/api/settings/interface",
        "GET /api/vault": "/api/vault",
        "GET /api/semantic/available": "/api/semantic/available",
        "GET /api/jobs": "/api/jobs?limit=50&offset=0",
        "GET /api/downloads/glance": "/api/downloads/glance",
        "GET /api/site-connections": "/api/site-connections",
        "GET /api/library/folders": "/api/library/folders",
        "GET /api/library/roots": "/api/library/roots",
        "GET /api/insights/recaps": "/api/insights/recaps",
        "GET /api/assets newest": "/api/assets?sort=newest&limit=100&offset=0",
        "GET /api/assets by name": "/api/assets?sort=name_az&limit=100&offset=0",
        "GET /api/assets shuffled": "/api/assets?sort=random&seed=3&limit=60&offset=0",
        "GET /api/assets videos": "/api/assets?sort=newest&media=video&limit=60&offset=0",
        "GET /api/assets favorites": "/api/assets?fav=yes&sort=newest&limit=100&offset=0",
        "GET /api/assets recent": "/api/assets?viewed=yes&sort=viewed&limit=100&offset=0",
        "GET /api/assets hidden": "/api/assets?hidden=true&sort=newest&limit=100&offset=0",
        "GET /api/assets person": f"/api/assets?people={lib.person}&sort=newest&limit=100&offset=0",
        "GET /api/assets small person": (
            f"/api/assets?people={lib.small_person}&sort=newest&limit=100&offset=0"
        ),
        "GET /api/assets tag": f"/api/assets?tags={lib.tag}&sort=newest&limit=100&offset=0",
        "GET /api/assets username": (
            f"/api/assets?username={lib.username}&sort=newest&limit=100&offset=0"
        ),
        "GET /api/assets folder": (
            f"/api/assets?in={lib.folder}&depth=direct&sort=newest&limit=100&offset=0"
        ),
        "GET /api/assets photo set": (
            f"/api/assets?photo_sets={lib.photo_set}&sort=newest&limit=100&offset=0"
        ),
        "GET /api/assets collection": (
            f"/api/assets?collections={lib.collection}&sort=newest&limit=100&offset=0"
        ),
        "GET /api/people": "/api/people?prefix=&anywhere=true&sort=largest&limit=60&offset=0",
        "GET /api/tags": "/api/tags?prefix=&anywhere=true&sort=largest&limit=60&offset=0",
        "GET /api/sites": "/api/sites?prefix=&anywhere=true&sort=largest&limit=60&offset=0",
        "GET /api/collections": (
            "/api/collections?prefix=&anywhere=true&sort=largest&limit=60&offset=0"
        ),
        "GET /api/photo-sets": (
            "/api/photo-sets?prefix=&anywhere=true&limit=24&offset=0&sort=largest"
        ),
        "GET /api/songs": "/api/songs?prefix=&anywhere=true&limit=16&offset=0&sort=largest",
        "GET /api/people/{id}": f"/api/people/{lib.person}",
        "GET /api/related/person/{id}": f"/api/related/person/{lib.person}",
        "GET /api/related/site/{id}": f"/api/related/site/{lib.site}",
        "GET /api/related/tag/{id}": f"/api/related/tag/{lib.tag}",
        "GET /api/related/collection/{id}": f"/api/related/collection/{lib.collection}",
        "GET /api/related/photo_set/{id}": f"/api/related/photo_set/{lib.photo_set}",
        "GET /api/related/song/{id}": f"/api/related/song/{lib.song}",
        "GET /api/insights": "/api/insights?period=day",
        "GET /api/downloads": "/api/downloads?limit=50&offset=0&show=all&sort=newest",
        "GET /api/assets/{id}": f"/api/assets/{lib.asset}",
        "GET /api/assets/{id}/people": f"/api/assets/{lib.asset}/people",
        "GET /api/assets/{id}/filings": f"/api/assets/{lib.asset}/filings",
        "GET /api/assets/{id}/tags": f"/api/assets/{lib.asset}/tags",
        "GET /api/assets/{id}/organize": f"/api/assets/{lib.asset}/organize",
        "GET /api/assets/{id}/faces": f"/api/assets/{lib.asset}/faces",
        "GET /api/assets/{id}/same-music": f"/api/assets/{lib.video}/same-music",
        "GET /api/assets/{id}/replays": f"/api/assets/{lib.video}/replays",
        "GET /api/collections?asset=": f"/api/collections?asset={lib.asset}",
        "GET /api/photo-sets?asset=": f"/api/photo-sets?asset={lib.asset}",
        "GET /api/songs?asset=": f"/api/songs?asset={lib.video}",
    }
    for facet in ("media", "rating", "tags", "people", "sites", "collections", "photo_sets"):
        reads[f"GET /api/assets/facets {facet}"] = f"/api/assets/facets?facet={facet}"
    return reads


def _admins_reads() -> dict[str, str]:
    """The screens only an admin has."""
    return {
        "GET /api/workbench": "/api/workbench",
        "GET /api/faces/to-check": "/api/faces/to-check?limit=24&offset=0&show=waiting",
        "GET /api/faces/identified/people": "/api/faces/identified/people?limit=24&offset=0",
        "GET /api/dedup/groups": "/api/dedup/groups",
        "GET /api/dedup/carry": "/api/dedup/carry",
        "GET /api/suggestions": "/api/suggestions?limit=40&offset=0",
        "GET /api/suggestions/filenames": "/api/suggestions/filenames?limit=24&offset=0",
        "GET /api/library/quarantine": "/api/library/quarantine",
        "GET /api/tasks": "/api/tasks",
        "GET /api/ledger": "/api/ledger?limit=50&offset=0",
        "GET /api/auth/users": "/api/auth/users",
        "GET /api/insights/path": "/api/insights/path",
        "GET /api/music/lookup": "/api/music/lookup",
        "GET /api/tidy": "/api/tidy",
        "GET /api/stash-boxes": "/api/stash-boxes",
    }


def screens(lib: FixtureLibrary) -> list[Ask]:
    """What every screen reads on open, as the admin and as a guest, then every press."""
    reads = _everyones_reads(lib)
    asks = [
        Ask(who, label, "GET", path) for who in ("admin", "guest") for label, path in reads.items()
    ]
    asks += [Ask("admin", label, "GET", path) for label, path in _admins_reads().items()]
    asks += presses(lib)
    return asks


def presses(lib: FixtureLibrary) -> list[Ask]:
    """Every kind of press, on at most ten files, as the admin."""
    files = list(lib.press_files)
    one = files[0]
    gone = files[-2:]
    return [
        Ask("admin", "POST /api/assets/favorite", "POST", "/api/assets/favorite",
            {"asset_ids": files, "favorite": True}),
        Ask("admin", "POST /api/assets/favorite", "POST", "/api/assets/favorite",
            {"asset_ids": files, "favorite": False}),
        Ask("admin", "POST /api/assets/rating", "POST", "/api/assets/rating",
            {"asset_ids": files, "rating": 3}),
        Ask("admin", "PUT /api/assets/{id}/rating", "PUT", f"/api/assets/{one}/rating",
            {"rating": 4}),
        Ask("admin", "POST /api/assets/tags", "POST", "/api/assets/tags",
            {"asset_ids": files, "tag_ids": [lib.tag], "add": True}),
        Ask("admin", "POST /api/assets/tags", "POST", "/api/assets/tags",
            {"asset_ids": files, "tag_ids": [lib.tag], "add": False}),
        Ask("admin", "POST /api/vault/unlock", "POST", "/api/vault/unlock", {"pin": TEST_PIN}),
        Ask("admin", "POST /api/assets/vault", "POST", "/api/assets/vault",
            {"asset_ids": files[:3], "vault": True}),
        Ask("admin", "POST /api/assets/vault", "POST", "/api/assets/vault",
            {"asset_ids": files[:3], "vault": False}),
        Ask("admin", "PUT /api/tags/{id}", "PUT", f"/api/tags/{lib.tag}", {"name": "tag renamed"}),
        Ask("admin", "PUT /api/sharing", "PUT", "/api/sharing",
            {"object_type": "folder", "object_id": lib.folder,
             "subject_user_id": lib.guests[1], "effect": "share"}),
        Ask("admin", "POST /api/assets/move", "POST", "/api/assets/move",
            {"asset_ids": files[:2], "folder_id": lib.folder}),
        Ask("admin", "POST /api/people/merge", "POST", "/api/people/merge",
            {"into": lib.small_person, "people": [lib.merged_person, lib.small_person]}),
        Ask("admin", "POST /api/assets/delete/check", "POST", "/api/assets/delete/check",
            {"asset_ids": gone}),
        Ask("admin", "POST /api/assets/delete", "POST", "/api/assets/delete",
            {"asset_ids": gone, "mode": "sift"}),
    ]  # fmt: skip


@contextmanager
def _hearing() -> Iterator[_Heard]:
    budget = statement_budget()
    heard = _Heard()
    budget.heard = heard
    try:
        yield heard
    finally:
        budget.heard = None


def walk(files: int, seed: int = SEED, directory: Path | None = None) -> Walk:
    """Build the library, boot the application over it, ask every request of the walk, and plan
    each statement it ran with the values it bound."""
    with tempfile.TemporaryDirectory(prefix="sift-ledger-", ignore_cleanup_errors=True) as scratch:
        where = directory or Path(scratch)
        db_path = where / "data" / DATABASE_FILENAME
        db_path.parent.mkdir(parents=True, exist_ok=True)
        lib = asyncio.run(fixture_library(files, seed, db_path))
        with _hearing() as heard, _conditions(where), pytest.MonkeyPatch.context() as patch:
            patch.setattr(lifespan, "log", _ReadySpy(lifespan.log, heard))
            app = create_app()
            with TestClient(_Labelled(app)) as client:
                who = _sign_in(db_path, lib)
                give_pin(db_path, lib.admin)
                statuses: dict[str, int] = {}
                for ask in screens(lib):
                    cookie, csrf = who[ask.who]
                    headers = {
                        _LABEL_HEADER: f"{ask.who} {ask.label}",
                        "cookie": f"{SESSION_COOKIE_NAME}={cookie}",
                        CSRF_HEADER_NAME: csrf,
                    }
                    answer = client.request(ask.method, ask.path, json=ask.body, headers=headers)
                    statuses[f"{ask.who} {ask.label}"] = answer.status_code
            asyncio.run(_ask_the_probe(where, lib))
        seen = _tallied(heard.by_request)
        for one in seen.values():
            one.scans = scanned(plan_of(db_path, one.sql, one.params), one.sql)
        return Walk(seen, statuses, lib)


async def _ask_the_probe(where: Path, lib: FixtureLibrary) -> None:
    """The scale probe's questions, each once, so its size holding is judged in steps here."""
    database = Database(lib.path, readers=1)
    await database.connect()
    try:
        settings = Settings(data_dir=where / "data", cache_dir=where / "cache")
        access = Repository(database, ContentStore(database, settings))
        for name, question in (await questions(database, access, lib)).items():
            token = _DURING.set(f"probe {name}")
            try:
                await question()
            finally:
                _DURING.reset(token)
    finally:
        await database.close()


def _sign_in(db_path: Path, lib: FixtureLibrary) -> dict[str, tuple[str, str]]:
    sessions = {}
    for who, role, username in (
        ("admin", "admin", "fixture-admin"),
        ("guest", "guest", "fixture-guest-0"),
    ):
        _user, token, csrf = establish_session(
            db_path, role=role, username=username, password=_PASSWORD
        )
        sessions[who] = (token, csrf)
    return sessions


def _tallied(runs: list[tuple[str, StatementRun]]) -> dict[str, Seen]:
    seen: dict[str, Seen] = {}
    per_request: dict[tuple[str, str], int] = {}
    for during, run in runs:
        one = seen.setdefault(run.name, Seen(stage=run.stage))
        one.requests.add(during)
        if run.steps >= one.steps:
            one.steps, one.sql, one.params = run.steps, run.sql, run.params
        key = (during, run.name)
        per_request[key] = per_request.get(key, 0) + 1
        one.count = max(one.count, per_request[key])
    return seen


# --- the plans: a library-sized table walked end to end, outside the sweep lane ----------------

_ALIASED = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_NOT_AN_ALIAS = frozenset(
    {"on", "where", "group", "order", "limit", "union", "left", "join", "inner", "cross", "using"}
)


def scanned(plan: list[str], sql: str) -> list[str]:
    """Which library-sized tables a plan walks end to end. A SEARCH is a seek, and an automatic
    index is named too: it is a walk to build one."""
    names = {table: table for table in LIBRARY_SIZED_TABLES}
    for table, alias in _ALIASED.findall(sql):
        if table.lower() in LIBRARY_SIZED_TABLES and alias.lower() not in _NOT_AN_ALIAS:
            names[alias] = table.lower()
    found: set[str] = set()
    for step in plan:
        if not step.startswith("SCAN "):
            continue
        walked = step[len("SCAN ") :].split()
        table = names.get(walked[0]) if walked else None
        if table is not None:
            found.add(table)
    return sorted(found)


def plan_of(db_path: Path, sql: str, params: Any) -> list[str]:
    """The plan SQLite chooses for this statement with the values it bound, read-only."""
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        rows = connection.execute(
            "EXPLAIN QUERY PLAN " + sql,  # nosemgrep: sift-no-string-built-sql
            params,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [str(row[3]) for row in rows]


# --- the record and the rules -------------------------------------------------------------------


def version() -> str:
    return sqlite3.sqlite_version


def read_record(path: Path = RECORD) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def write_record(record: dict[str, Any], path: Path = RECORD) -> None:
    ordered = {v: dict(sorted(names.items())) for v, names in sorted(record.items())}
    path.write_text(
        json.dumps(ordered, indent=1, sort_keys=False) + "\n", encoding="utf-8", newline="\n"
    )


def entry(small: Seen | None, large: Seen | None) -> dict[str, Any]:
    """A statement's record from what the two walks saw."""
    either = large or small
    assert either is not None
    sizes = {}
    if small is not None:
        sizes[str(SIZES[0])] = small.steps
    if large is not None:
        sizes[str(SIZES[1])] = large.steps
    found: dict[str, Any] = {
        "stage": either.stage,
        "steps": sizes,
        "count": max(s.count for s in (small, large) if s is not None),
    }
    walked = sorted({table for s in (small, large) if s is not None for table in s.scans})
    if walked:
        found["scans"] = walked
    return found


def _boot_only(name: str, walks: dict[int, dict[str, Seen]]) -> bool:
    """Run only before `boot.ready`: judged by `boot_scans` alone, since which of the boot's
    housekeeping writes run depends on what the last run left."""
    return _requests(name, walks) == {BOOT}


def unrecorded(recorded: dict[str, Any], walks: dict[int, dict[str, Seen]]) -> list[str]:
    """A statement the record has never seen: every new one is recorded on purpose."""
    names = {name for seen in walks.values() for name in seen if not _boot_only(name, walks)}
    return [
        f"{name}: a statement the ledger has never seen ({sorted(_requests(name, walks))[0]})."
        f' Record it on purpose: scripts/statement_ledger.py --raise {name} --why "..."'
        for name in sorted(names - set(recorded))
    ]


def rises(recorded: dict[str, Any], walks: dict[int, dict[str, Seen]]) -> list[str]:
    """A recorded statement costing more than its record at either size, past the noise."""
    problems = []
    for size, seen in walks.items():
        for name, now in sorted(seen.items()):
            if _boot_only(name, walks):
                continue
            was = recorded.get(name, {}).get("steps", {}).get(str(size))
            if was is not None and now.steps > was * (1 + RISE_SHARE) + RISE_SLACK:
                problems.append(
                    f"{name}: {now.steps:,} steps at {size:,} files against {was:,} recorded"
                    f" ({(now.steps - was) / max(was, 1):+.0%}, {sorted(now.requests)[0]})."
                    f' Make it cheaper, or record the rise: --raise {name} --why "..."'
                )
    return problems


def growth(recorded: dict[str, Any], walks: dict[int, dict[str, Seen]]) -> list[str]:
    """A request statement whose steps grow with the library when its record names no subject."""
    problems = []
    small_walk, large_walk = (walks.get(size, {}) for size in SIZES)
    for name in sorted(set(small_walk) & set(large_walk)):
        small, large = small_walk[name], large_walk[name]
        if small.stage == SWEEP or _boot_only(name, walks):
            continue
        kept = recorded.get(name, {})
        allowed = SUBJECTS.get(kept.get("per", "page"), HELD_GROWTH)
        if "per" in kept:
            # A priced statement may grow as fast as its record says, and no faster.
            was = [kept["steps"].get(str(size)) for size in SIZES]
            if None not in was:
                allowed = max(allowed, was[1] / max(was[0], 1) * (1 + RISE_SHARE))
        if large.steps > max(small.steps, 1) * allowed + RISE_SLACK:
            problems.append(
                f"{name}: grows {large.steps / max(small.steps, 1):.1f}x from {SIZES[0]:,} to"
                f" {SIZES[1]:,} files (held: {allowed}x, {sorted(large.requests)[0]}). Bound it"
                " by the page, or name what it is priced by: --raise NAME --per SUBJECT --why"
            )
    return problems


def _requests(name: str, walks: dict[int, dict[str, Seen]]) -> set[str]:
    return set().union(*(seen[name].requests for seen in walks.values() if name in seen))


def scans(recorded: dict[str, Any], walks: dict[int, dict[str, Seen]]) -> list[str]:
    """A request statement that walks a library-sized table end to end, off the sweep lane,
    where its record does not already say it does."""
    problems = []
    for seen in walks.values():
        for name, one in sorted(seen.items()):
            if one.stage == SWEEP:
                continue
            known = set(recorded.get(name, {}).get("scans", ()))
            new = [table for table in one.scans if table not in known]
            if new and name in recorded:
                problems.append(
                    f"{name}: walks {', '.join(new)} end to end on a request "
                    f"({sorted(one.requests)[0]}). Seek it, or move it to the sweep lane"
                )
    return sorted(set(problems))


@dataclass(frozen=True)
class Raise:
    """A rise or a new statement, recorded on purpose: the reason is kept beside the number."""

    why: str
    per: str | None = None
    #: The reason is why it may run before `boot.ready` (`boot_why`), not why it costs more.
    boot: bool = False


def updated(
    block: dict[str, Any], walks: dict[int, dict[str, Seen]], raised: dict[str, Raise]
) -> tuple[dict[str, Any], list[str]]:
    """The record after these walks: a fall is written, a statement no longer run leaves, a rise,
    a new statement or a new library walk only where `raised` names it. Returns what was refused."""
    small_walk, large_walk = (walks.get(size, {}) for size in SIZES)
    names = set(small_walk) | set(large_walk)
    found: dict[str, Any] = {}
    refused: list[str] = []
    for name in sorted(names):
        fresh = entry(small_walk.get(name), large_walk.get(name))
        kept = block.get(name)
        allowed = raised.get(name)
        if kept is None:
            if allowed is None and not _boot_only(name, walks):
                refused.append(f"{name}: new; record it with --raise and a reason")
                continue
            found[name] = fresh
        else:
            found[name] = _moved(name, kept, fresh, allowed, refused)
        if allowed is not None:
            found[name]["boot_why" if allowed.boot else "why"] = allowed.why
            if allowed.per is not None:
                found[name]["per"] = allowed.per
    for name in raised:
        if name not in names:
            refused.append(f"{name}: no walk ran it, so there is nothing to record")
    return found, refused


def _moved(
    name: str,
    kept: dict[str, Any],
    fresh: dict[str, Any],
    allowed: Raise | None,
    refused: list[str],
) -> dict[str, Any]:
    """One recorded statement brought to what was seen: down freely, up only on purpose."""
    moved = dict(kept)
    steps = dict(kept["steps"])
    for size, now in fresh["steps"].items():
        was = steps.get(size)
        if was is None or now < (was - RISE_SLACK) / (1 + RISE_SHARE):
            steps[size] = now
        elif now > was * (1 + RISE_SHARE) + RISE_SLACK:
            if allowed is None:
                refused.append(f"{name}: {now:,} steps at {size} files against {was:,}")
            else:
                steps[size] = now
    moved["steps"] = steps
    moved["count"] = fresh["count"]
    walked = set(fresh.get("scans", ()))
    new = walked - set(kept.get("scans", ()))
    if new and allowed is None:
        refused.append(f"{name}: walks {', '.join(sorted(new))} end to end")
        walked -= new
    if walked:
        moved["scans"] = sorted(walked)
    else:
        moved.pop("scans", None)
    return moved


#: Why a statement in the first record grows or walks: it did before the ledger existed.
STARTING_WHY = "costs this before the ledger: a debt on the record, not a decision"


def first_block(walks: dict[int, dict[str, Seen]]) -> dict[str, Any]:
    """The first record for a SQLite version: what the tree costs today, every statement that
    grows with the library marked as priced by it, so the next change can only lower it."""
    small_walk, large_walk = (walks.get(size, {}) for size in SIZES)
    block: dict[str, Any] = {}
    for name in sorted(set(small_walk) | set(large_walk)):
        small, large = small_walk.get(name), large_walk.get(name)
        found = entry(small, large)
        grows = (
            small is not None
            and large is not None
            and small.stage != SWEEP
            and large.steps > max(small.steps, 1) * HELD_GROWTH + RISE_SLACK
        )
        if grows:
            found["per"] = "library"
        if grows or "scans" in found:
            found["why"] = STARTING_WHY
        block[name] = found
    return block


def boot_scans(recorded: dict[str, Any], walks: dict[int, dict[str, Seen]]) -> list[str]:
    """A statement between the lifespan's start and `boot.ready` that walks a library-sized table,
    on any lane: the first screen waits for it. Moved after ready, or exempted with its reason
    (`boot_why` in the record)."""
    problems = []
    for size, seen in walks.items():
        for name, one in sorted(seen.items()):
            if BOOT in one.requests and one.scans and "boot_why" not in recorded.get(name, {}):
                problems.append(
                    f"{name}: walks {', '.join(one.scans)} before boot.ready at {size:,} files."
                    " Run it after ready, or record why it may not wait (boot_why)"
                )
    return problems
