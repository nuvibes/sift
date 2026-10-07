# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared test fixtures.

Slices import from here rather than defining their own. Duplicated setup helpers drift, and
a permission test that builds its own guest user eventually stops resembling a real one.

Fixtures are added here as the modules they depend on land: a real WAL SQLite database, the
job runner, the media corpus, and the admin/guest users.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import struct
import subprocess
import zlib
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from sift.kernel import attention, media, when
from sift.kernel import subprocess as tools
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database, probe_sqlite, registered_components, registered_point_reads
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue, worker_pool
from sift.kernel.sorting import sort_key
from sift.testing.library import hidden_row

if TYPE_CHECKING:
    from _collections_abc import dict_items, dict_keys, dict_values

_Named = logging.Logger | logging.PlaceHolder

#: The lock every writer of the logger registry holds (`logging.getLogger` takes it).
_LOGGING_LOCK: AbstractContextManager[bool] = logging._lock  # type: ignore[attr-defined]


class _LoggerRegistry(dict[str, _Named]):
    """The logging module's registry of named loggers, walked as a copy made under its lock.

    pytest's log capture walks `loggerDict.values()` without that lock as each test phase starts,
    while a booted app may be creating loggers on the test client's thread (a module's logger is
    made the first time it logs). A walk that meets an insert raises "dictionary changed size
    during iteration", so every walk of this registry reads a copy the writers cannot change.
    """

    def _copy(self) -> dict[str, _Named]:
        # The base view reads the storage; `dict(self)` would come back through `keys()` below.
        with _LOGGING_LOCK:
            return dict(dict.items(self))

    def __iter__(self) -> Iterator[str]:
        return iter(self._copy())

    def keys(self) -> dict_keys[str, _Named]:
        return self._copy().keys()

    def values(self) -> dict_values[str, _Named]:
        return self._copy().values()

    def items(self) -> dict_items[str, _Named]:
        return self._copy().items()


def pytest_configure() -> None:
    """Give the test process a logger registry that can be walked while it grows."""
    with _LOGGING_LOCK:
        manager = logging.Logger.manager
        if not isinstance(manager.loggerDict, _LoggerRegistry):
            manager.loggerDict = _LoggerRegistry(manager.loggerDict)


@pytest.fixture(scope="session", autouse=True)
def sqlite_can_do_what_sift_needs() -> None:
    """Fail the whole run, loudly, on a SQLite that Sift refuses to start on.

    A green suite proves nothing if it ran on a library the app would not have started against.
    Without this the search tests would fail one at a time, each for its own reason, instead of the
    run saying the one thing that is actually wrong.
    """
    capabilities = probe_sqlite()
    if not capabilities.fts5:
        pytest.fail(
            f"the tests are running on SQLite {capabilities.version}, which has no FTS5, and "
            "Sift will not start without it. FTS5 is a build option: use a SQLite built with it.",
            pytrace=False,
        )


#: The zone the suite's machine clock is held to: UTC, in the spelling both the Universal C Runtime
#: and the C library read. See `the_machine_keeps_utc`.
SUITE_ZONE = "UTC0"


def _hold_zone(zone: str | None) -> None:
    """Set the process's zone (None clears it back to the machine's) and have the clock read it."""
    if zone is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = zone
    when.refresh()
    _give_a_daylight_rule_its_hour(zone)


_NAMES_DAYLIGHT = re.compile(r"[A-Za-z]{3,}[-+]?\d[\d:]*[A-Za-z]{3,}")


def _give_a_daylight_rule_its_hour(zone: str | None) -> None:
    """On Windows the runtime keeps the daylight shift it last read from the system (nothing, on
    a machine in UTC), so a rule that names a daylight zone, such as "EST5EDT", is given its hour."""
    if os.name != "nt" or zone is None or not _NAMES_DAYLIGHT.fullmatch(zone):
        return
    import ctypes

    shift = ctypes.CDLL("ucrtbase").__dstbias
    shift.restype = ctypes.POINTER(ctypes.c_long)
    shift()[0] = -3600


@pytest.fixture(scope="session", autouse=True)
def the_machine_keeps_utc() -> Iterator[None]:
    """Hold the machine's clock to UTC for the whole run, whatever zone the computer running it is in.

    Every day and time Sift shows is the machine's local one (`kernel/when.py`), so a test that
    expects a filing at 22:13 UTC to fall on the 14th would pass in New York and fail in Tokyo.
    Held to UTC, every run reads the days a CI runner reads. A test about the zone names one
    (`machine_zone`).
    """
    before = os.environ.get("TZ")
    _hold_zone(SUITE_ZONE)
    try:
        yield
    finally:
        _hold_zone(before)


@pytest.fixture
def machine_zone() -> Iterator[Callable[[str], None]]:
    """Put the machine's clock in a zone for one test: `machine_zone("EST5EDT")`, a POSIX rule
    (the one spelling both C runtimes read). Back to the suite's UTC after, and read again
    immediately, because the runtime keeps a zone it has read until it is asked again."""
    yield _hold_zone
    _hold_zone(SUITE_ZONE)


@pytest.fixture
def clean_registry() -> Iterator[None]:
    """Restore the database kernel's registries around a test.

    Both are process-global by design (features register at import time), so a test that adds a
    component, or declares a statement a point read, would otherwise leak it into every test that
    runs after it. The point-read one matters twice over: the gate that proves those statements
    plans everything in the registry, so a leaked one from a test is a gate failure somewhere else
    entirely.
    """
    import sift.kernel.db as db

    saved = registered_components()
    saved_point_reads = registered_point_reads()
    saved_invariants = db.registered_invariants()
    try:
        yield
    finally:
        db._REGISTRY.clear()
        db._REGISTRY.update(saved)
        db._POINT_READS.clear()
        db._POINT_READS.update(saved_point_reads)
        db._INVARIANTS.clear()
        db._INVARIANTS.update(saved_invariants)


@pytest.fixture
def clean_settings_registry() -> Iterator[None]:
    """Restore the preference registry around a test.

    Like the schema registry, it is process-global: features declare their settings at import time.
    A test that registers its own setting would otherwise leak it into every test after it, and a
    second registration of the same key raises on purpose.
    """
    import sift.kernel.jobs.schedules as schedules
    import sift.kernel.settings_registry as registry

    saved = registry.registered_settings()
    registry._REGISTRY.clear()
    # The schedule registry travels with it: a feature's `register()` declares its settings AND
    # its scheduled task in one call; restoring one and not the other would refuse a test's second
    # call for the wrong reason (the task, not the key, "registered twice").
    saved_tasks = dict(schedules._REGISTRY)
    schedules._REGISTRY.clear()
    # And the retirements, for the same reason: a `register()` that retires an old key beside the
    # setting that answers it now would be refused as "retired twice" on a test's second call.
    saved_retired = registry.retired_settings()
    registry._RETIRED.clear()
    # And the removals, for the same reason.
    saved_removed = dict(registry._REMOVED)
    registry._REMOVED.clear()
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)
        schedules._REGISTRY.clear()
        schedules._REGISTRY.update(saved_tasks)
        registry._RETIRED.clear()
        registry._RETIRED.update(saved_retired)
        registry._REMOVED.clear()
        registry._REMOVED.update(saved_removed)


@pytest.fixture
async def temp_db(tmp_path: Path) -> AsyncIterator[Database]:
    """A real SQLite database in WAL mode, on disk.

    On disk and not in memory, deliberately: WAL, the busy timeout and the single-writer lock are
    the things most worth testing here, and an in-memory database has none of them.
    """
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


#: Every process-global registry a boot fills beside the job handlers: the names and families, the
#: one-at-a-time and queue-to-itself types, the order of a file's work, the urgency each is held to,
#: whether it waits for its family, the product carriers, the trailing types, the counted units, and
#: what Activity leaves off its list or quiet on it. Each is read by the queue or the jobs page
#: rather than by the handler, so one left behind changes what a later test's enqueue, claim or
#: page does, in another file, with nothing naming the test that left it.
JOB_REGISTRIES = (
    "_HANDLERS",
    "_NAMES",
    "_FAMILIES",
    "_ALONE",
    "_EXCLUSIVE",
    "_FOLLOWS",
    "_URGENCY",
    "_NOT_GATED",
    "_CARRIERS",
    "_TRAILS",
    "_COUNTS",
    "_UNLISTED",
    "_BY_ITSELF",
)


@contextmanager
def job_registries_kept() -> Iterator[None]:
    """The job registries handed back on the way out exactly as they were on the way in."""
    held = {name: getattr(worker_pool, name).copy() for name in JOB_REGISTRIES}
    try:
        yield
    finally:
        for name, before in held.items():
            registry = getattr(worker_pool, name)
            registry.clear()
            registry.update(before)


@pytest.fixture(scope="module", autouse=True)
def module_keeps_the_job_registries() -> Iterator[None]:
    """Hand the job registries back at the end of every test module, as `clean_handlers` does per test.

    A fixture wider than one test that boots the application (a gate reading what the started app
    registered, say) fills the registries before any test's own keeping of them begins, so the
    per-test restore puts its handlers back every time. Left there, every later boot in the same
    worker process refuses its first handler as already registered, in another module.
    """
    with job_registries_kept():
        yield


@pytest.fixture(autouse=True)
def clean_handlers() -> Iterator[None]:
    """Restore the job-handler registry around a test.

    Process-global for the same reason the schema registry is (which handlers exist is a
    property of the code, not of a database), and leaky for the same reason too.

    Automatic, unlike the other two, because the thing that fills this registry is booting the
    application rather than importing a module. A module is imported once per process and a schema
    registers itself as it goes; handlers are claimed by the app's startup, which the tests run
    over and over. Leaving it to each test to remember would mean every test that boots an app
    anywhere in the suite has to know this, and the one that forgets fails in a different file,
    with "a handler for job type 'probe' is already registered", which says nothing about the test
    that actually leaked it.

    Claiming a type twice within one boot still raises, which is the case the rule is for: two
    features quietly fighting over one job type.
    """
    with job_registries_kept():
        yield


@pytest.fixture(autouse=True)
def nobody_at_the_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the computer as left alone, so a booted pool runs its full count in every test.

    The worker pool runs a share of its workers while somebody is using the computer
    (`kernel.attention`), and the real reading is whoever is at the keyboard of the machine running
    the suite. Left real, a test comparing the pool's worker count with its caps would pass at
    night and fail while somebody typed. A test about the lever builds its own reading. The share
    in force is put back after each test too, so a test that stepped back hands the next one the
    whole device.
    """
    monkeypatch.setattr(attention, "ATTENTION", attention.Attention(lambda: None))
    monkeypatch.setattr(media, "_share", None)
    monkeypatch.setattr(tools, "_background_rate", None)


@pytest.fixture
def first_folder_benchmarks() -> None:
    """Asked for by a test about the benchmark a first library folder queues: keeps it real.

    See `no_benchmark_on_a_first_folder`, which reads whether a test asked for this by name.
    """


@pytest.fixture(autouse=True)
def no_benchmark_on_a_first_folder(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test that adds its first library folder has it read immediately, unless it is about the benchmark.

    In use, the first folder on a device never measured queues the benchmark first and holds the
    folder's scan behind it (`performance.benchmark.FirstFolder`). In a booted test application
    every device is unmeasured, so every test adding a folder would wait minutes on a real encoder
    measuring the machine running the suite, and a test counting the queue would count it too.
    A test about it asks for `first_folder_benchmarks` and keeps the real reaction.
    """
    if "first_folder_benchmarks" in request.fixturenames:
        return
    from sift.slices.performance import benchmark

    async def declined(_self: object, _root_id: str, _by: str | None, _scan: bool) -> bool:
        return False

    monkeypatch.setattr(benchmark.FirstFolder, "__call__", declined)


@pytest.fixture(autouse=True)
def quiet_search_refresh(request: pytest.FixtureRequest) -> Iterator[None]:
    """Stop the search index refreshing itself, unless a test is about that.

    Booting the application starts a refresh loop, which is exactly what it is for: without it
    nothing would fill the index, and free text would find nothing in a running Sift. The
    consequence is that every booted application puts jobs on the queue and rewrites the index
    in the background, and neither is what most tests are looking at.

    Two ways that bites, and the second is why this is here rather than in the search slice's own
    conftest. A test that manages the index by hand races a worker rebuilding it underneath. And a
    test that counts the QUEUE (in a different slice entirely, which has no reason to know search
    exists) counts the refresh jobs too, and fails with "assert 4 == 1" in a file whose author
    never went near this.

    Automatic, for the same reason `clean_handlers` is: it is a property of booting the app, so
    leaving each test to remember means the one that forgets fails somewhere else.

    Marked `indexes_itself`, a test keeps the real thing. That is how the two tests asserting the
    application fills its own index still assert it: if this suppression ever hid that behaviour
    for real, those are what go red.
    """
    if request.node.get_closest_marker("indexes_itself"):
        yield
        return

    from sift.slices.search import jobs as search_jobs

    async def no_refresh(**_: object) -> None:
        return None

    saved = search_jobs.ensure_scheduled
    search_jobs.ensure_scheduled = no_refresh
    try:
        yield
    finally:
        search_jobs.ensure_scheduled = saved


@pytest.fixture
async def job_queue(temp_db: Database) -> JobQueue:
    """A queue on a real database, with the `jobs` table created."""
    await temp_db.initialize_schema()
    return JobQueue(temp_db)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings whose directories are all under `tmp_path`, so nothing escapes the test."""
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@pytest.fixture
async def content_store(temp_db: Database, settings: Settings) -> ContentStore:
    await temp_db.initialize_schema()
    return ContentStore(temp_db, settings)


@pytest.fixture
async def library_store(temp_db: Database, settings: Settings) -> LibraryStore:
    await temp_db.initialize_schema()
    return LibraryStore(temp_db, settings)


@dataclass(frozen=True, slots=True)
class LibraryRoot:
    """A root row, and the real directory it points at."""

    id: str
    path: Path


@pytest.fixture
async def library_root(
    temp_db: Database, content_store: ContentStore, tmp_path: Path
) -> LibraryRoot:
    """A library root: a directory on disk, and the row that says Sift is watching it.

    Written with a raw insert rather than through the store that owns roots, and deliberately: this
    is the fixture the *content* tests use, and they are about what an asset is. Giving them a root
    built by the real path would hand them a folder row they never asked for and a set of rules
    (overlap, reserved directories) they are not testing. The tests that are about roots use
    `library_store` and create them properly.
    """
    directory = tmp_path / "library"
    await asyncio.to_thread(directory.mkdir, exist_ok=True)

    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, "library", str(directory), 1_700_000_000),
    )
    return LibraryRoot(id=root_id, path=directory)


# --- access -------------------------------------------------------------------------------
#
# The rows below are written with raw inserts. Users, tags, people and collections are created
# through the features that own those screens, and none of them exist at kernel level: a
# fixture that reached for one would make the kernel's own tests depend on something built on
# top of the kernel.

_EPOCH = 1_700_000_000


@dataclass(frozen=True, slots=True)
class Actors:
    admin: Viewer
    guest: Viewer


@dataclass(frozen=True, slots=True)
class World:
    """A library with enough shape to ask every question the resolver can be asked.

    root                     root_two
      top                      other
        mid
          leaf

    solo    one copy, in `leaf`. Tagged, has a person, in a collection and a photo set,
            from a site.
    twin    two copies: one in `leaf`, one in `other`. The multi-location case.
    loose   one copy, sitting directly in `root` with no folder at all.
    """

    root: str
    root_two: str
    top: str
    mid: str
    leaf: str
    other: str
    solo: str
    twin: str
    loose: str
    tag: str
    person: str
    collection: str
    photo_set: str
    site: str
    username: str
    song: str

    def object_id(self, name: str) -> str | None:
        """Resolve a name from the truth table to the id it was given."""
        if name == "global":
            return None
        return str(getattr(self, name))


async def create_user(database: Database, role: Role, *, disabled: bool = False) -> Viewer:
    user_id = new_id()
    await database.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at, disabled) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, f"{role.value}-{user_id}", "x", role.value, _EPOCH, int(disabled)),
    )
    return Viewer(id=user_id, role=role)


@pytest.fixture
async def access(temp_db: Database, content_store: ContentStore) -> Repository:
    await temp_db.initialize_schema()
    return Repository(temp_db, content_store)


@pytest.fixture
async def actors(temp_db: Database, access: Repository) -> Actors:
    return Actors(
        admin=await create_user(temp_db, Role.ADMIN),
        guest=await create_user(temp_db, Role.GUEST),
    )


async def build_world(database: Database, world: World) -> None:
    """Write the library described by `World` into an already-migrated database."""

    async def folder(folder_id: str, root: str, parent: str | None, path: str) -> None:
        await database.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder_id, root, parent, path, path.rsplit("/", 1)[-1]),
        )

    async def asset(asset_id: str, name: str) -> None:
        await database.execute(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
            " VALUES (?, ?, 1, 'video', ?)",
            (asset_id, f"digest-{name}-{asset_id}", _EPOCH),
        )

    async def location(asset_id: str, root: str, folder_id: str | None, path: str) -> None:
        await database.execute(
            "INSERT INTO asset_locations "
            "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), asset_id, root, folder_id, path, path.rsplit("/", 1)[-1], _EPOCH, _EPOCH),
        )

    for root_id in (world.root, world.root_two):
        await database.execute(
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root_id, root_id, f"/library/{root_id}", _EPOCH),
        )

    await folder(world.top, world.root, None, "top")
    await folder(world.mid, world.root, world.top, "top/mid")
    await folder(world.leaf, world.root, world.mid, "top/mid/leaf")
    await folder(world.other, world.root_two, None, "other")

    await asset(world.solo, "solo")
    await asset(world.twin, "twin")
    await asset(world.loose, "loose")

    await location(world.solo, world.root, world.leaf, "top/mid/leaf/solo.mp4")
    await location(world.twin, world.root, world.leaf, "top/mid/leaf/twin.mp4")
    await location(world.twin, world.root_two, world.other, "other/twin.mp4")
    await location(world.loose, world.root, None, "loose.mp4")

    await database.execute(
        "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (world.tag, "tag", sort_key("tag"), _EPOCH),
    )
    await database.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.solo, world.tag)
    )
    await database.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (world.person, "person", sort_key("person"), _EPOCH),
    )
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
        (world.solo, world.person),
    )
    # The collection wears `solo` as its cover as well as holding it, so the truth table can put
    # both the count and the cover to every way of concealing that one file. A cover is a picture
    # of an item, and it leaks the item just as surely as counting it does.
    await database.execute(
        "INSERT INTO collections (id, name, name_sort, cover_asset_id, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (world.collection, "collection", sort_key("collection"), world.solo, _EPOCH),
    )
    await database.execute(
        "INSERT INTO collection_items (collection_id, asset_id, added_at) VALUES (?, ?, ?)",
        (world.collection, world.solo, _EPOCH),
    )
    # The photo set holds `solo` and wears it as its cover, exactly as the collection above
    # does and for the same reason: the truth table can then put every way of concealing that one
    # file to both the count and the cover. A cover is a picture of an item and leaks it as surely
    # as counting it does.
    await database.execute(
        "INSERT INTO photo_sets (id, name, name_sort, cover_asset_id, origin, created_at)"
        " VALUES (?, ?, ?, ?, 'manual', ?)",
        (world.photo_set, "photo set", sort_key("photo set"), world.solo, _EPOCH),
    )
    await database.execute(
        "INSERT INTO photo_set_items (photo_set_id, asset_id, position, added_at)"
        " VALUES (?, ?, ?, ?)",
        (world.photo_set, world.solo, 0, _EPOCH),
    )
    await database.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (world.site, "site", sort_key("site"), _EPOCH),
    )
    await database.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, ?)",
        (world.username, world.site, "handle", sort_key("handle"), _EPOCH),
    )
    await database.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
        (world.solo, world.username),
    )


@pytest.fixture
async def world(temp_db: Database, access: Repository) -> World:
    built = World(**{name: new_id() for name in World.__slots__})
    await build_world(temp_db, built)
    return built


async def hide(
    database: Database, kind: str, object_id: str, user_id: str, *, hidden: bool = True
) -> None:
    """Hide one thing from one user, straight into the table the resolver reads.

    Deliberately not through a route or a service: what this exists to set up is the state, so that
    a test of the resolver is a test of the resolver and not of whichever endpoint happened to write
    the row. The statement comes from the seeding module beside this one, so the tests that reach a
    database this way and the tests that reach one over HTTP write the same row.
    """
    statement, params = hidden_row(kind, object_id, user_id, hidden=hidden)
    await database.execute(statement, params)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Repository root, resolved via git rather than by counting parent directories."""
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent,
    )
    return Path(out.stdout.strip())


class FakeClock:
    """Controllable time source.

    Job retries, session expiry and lockout backoff are all time-dependent. Driving them with
    real sleeps makes tests slow and flaky under load, and a flaky test around a security
    boundary is one that ends up skipped.
    """

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += seconds
        return self._now


@pytest.fixture
def fake_clock() -> Iterator[FakeClock]:
    yield FakeClock()


def png_bytes(pixel: bytes = b"\x00\xff\x00\x00") -> bytes:
    """A minimal but genuine 1x1 PNG: the signature the gate reads, and the IEND it checks for.

    `pixel` is the raw scanline (a filter byte then one RGB triple); varying it makes a valid PNG
    whose bytes (and so its content hash) differ, which is how a test produces a genuinely new
    image versus a duplicate of one already ingested. The default is a single red pixel.

    Here rather than in one slice's fixtures because the store is content-addressed, so "give me a
    file that is genuinely not the last one" is a thing every slice's tests eventually need.
    """

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(pixel))
    iend = chunk(b"IEND", b"")
    return signature + ihdr + idat + iend
