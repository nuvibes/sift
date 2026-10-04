# SPDX-License-Identifier: AGPL-3.0-or-later
"""What runs for a file that has just landed, and what happens when it will not.

The properties worth holding are the two that decide whether this can ever break an import: a hook
that raises is logged and the next one still runs, and a registry with no handle installed does
nothing at all rather than failing.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from sift.kernel import landing
from sift.kernel.config import Settings
from sift.kernel.db import Database

pytestmark = [pytest.mark.unit]


class _Noted:
    """A landing that writes down what it was handed."""

    def __init__(
        self, name: str, seen: list[tuple[str, str, str]], roots: list[str | None] | None = None
    ) -> None:
        self.name = name
        self._seen = seen
        self._roots = roots if roots is not None else []

    async def landed(
        self, path: Path, identity: str, settings: Settings, *, root_id: str | None
    ) -> None:
        self._seen.append((self.name, str(path), identity))
        self._roots.append(root_id)


class _Refuses:
    name = "refuses"

    async def landed(
        self, path: Path, identity: str, settings: Settings, *, root_id: str | None
    ) -> None:
        raise RuntimeError("the tool would not read it")


@pytest.fixture(autouse=True)
def _clean_registry() -> object:
    """Each case gets the registry to itself: it is process-global, like every other one here."""
    kept = landing.registered_landings()
    landing._REGISTERED.clear()
    landing.install(None)
    yield
    landing._REGISTERED.clear()
    landing._REGISTERED.update(kept)
    landing.install(None)


def test_a_name_is_claimed_once() -> None:
    landing.register_landing("one", lambda database: _Noted("one", []))
    with pytest.raises(ValueError, match="already registered"):
        landing.register_landing("one", lambda database: _Noted("one", []))


@pytest.mark.asyncio
async def test_nothing_runs_until_a_handle_is_installed(tmp_path: Path) -> None:
    """A build that forgot the wiring is inert rather than quietly wrong."""
    seen: list[tuple[str, str, str]] = []
    landing.register_landing("one", lambda database: _Noted("one", seen))
    await landing.landed(tmp_path / "a.mp4", "identity-a", settings=Settings(), root_id=None)
    assert seen == []


@pytest.mark.asyncio
async def test_every_landing_is_handed_the_file_and_its_identity(tmp_path: Path) -> None:
    seen: list[tuple[str, str, str]] = []
    landing.register_landing("one", lambda database: _Noted("one", seen))
    landing.register_landing("two", lambda database: _Noted("two", seen))
    database = Database(tmp_path / "landing.sqlite3")
    await database.connect()
    landing.install(database)
    try:
        await landing.landed(tmp_path / "a.mp4", "identity-a", settings=Settings(), root_id=None)
    finally:
        await database.close()
    assert seen == [
        ("one", str(tmp_path / "a.mp4"), "identity-a"),
        ("two", str(tmp_path / "a.mp4"), "identity-a"),
    ]


@pytest.mark.asyncio
async def test_every_landing_is_told_the_folder_the_file_is_going_into(tmp_path: Path) -> None:
    """A landing a folder can switch off has to ask THAT folder, and at staging the destination is
    the only folder there is to ask. None where nothing is known, handed on as None."""
    roots: list[str | None] = []
    landing.register_landing("one", lambda database: _Noted("one", [], roots))
    database = Database(tmp_path / "landing.sqlite3")
    await database.connect()
    landing.install(database)
    try:
        await landing.landed(tmp_path / "a.mp4", "identity-a", settings=Settings(), root_id="r-1")
        await landing.landed(tmp_path / "b.mp4", "identity-b", settings=Settings(), root_id=None)
    finally:
        await database.close()
    assert roots == ["r-1", None]


@pytest.mark.asyncio
async def test_a_landing_that_raises_never_fails_the_landing(tmp_path: Path) -> None:
    """The import is what somebody asked for. Work done on the side while the bytes happened to be
    local is not, and it must not be able to take the import down with it."""
    seen: list[tuple[str, str, str]] = []
    landing.register_landing("refuses", lambda database: _Refuses())
    landing.register_landing("after", lambda database: _Noted("after", seen))
    database = Database(tmp_path / "landing.sqlite3")
    await database.connect()
    landing.install(database)
    try:
        await landing.landed(tmp_path / "a.mp4", "identity-a", settings=Settings(), root_id=None)
    finally:
        await database.close()
    assert [one[0] for one in seen] == ["after"]


@pytest.mark.asyncio
async def test_a_landing_is_built_once_and_kept(tmp_path: Path) -> None:
    """Building one per file would open whatever it holds on every import."""
    built = 0

    def build(database: Database) -> _Noted:
        nonlocal built
        built += 1
        return _Noted("one", [])

    landing.register_landing("one", build)
    database = Database(tmp_path / "landing.sqlite3")
    await database.connect()
    landing.install(database)
    try:
        await landing.landed(tmp_path / "a.mp4", "identity-a", settings=Settings(), root_id=None)
        await landing.landed(tmp_path / "b.mp4", "identity-b", settings=Settings(), root_id=None)
    finally:
        await database.close()
    assert built == 1


@pytest.mark.asyncio
async def test_installing_again_drops_what_was_built_against_the_old_handle(tmp_path: Path) -> None:
    """A landing bound to a database that has been closed is worse than none: it would fail every
    import quietly for the rest of the run."""
    built = 0

    def build(database: Database) -> _Noted:
        nonlocal built
        built += 1
        return _Noted("one", [])

    landing.register_landing("one", build)
    database = Database(tmp_path / "landing.sqlite3")
    await database.connect()
    landing.install(database)
    try:
        await landing.landed(tmp_path / "a.mp4", "identity-a", settings=Settings(), root_id=None)
        landing.install(database)
        await landing.landed(tmp_path / "b.mp4", "identity-b", settings=Settings(), root_id=None)
    finally:
        await database.close()
    assert built == 2


class _Store:
    """The content store's one read a landing may make, answering from a dict."""

    held: ClassVar[dict[str, object]] = {}

    def __init__(self, database: object, settings: Settings) -> None:
        pass

    async def resolve_by_identity(self, identity: str) -> object | None:
        return self.held.get(identity)


@pytest.mark.asyncio
async def test_the_known_shape_is_the_files_own_row_or_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A landing asks what Sift already holds about the bytes it was handed: the kind, and the
    probe's answer where the probe has run. Bytes Sift has no row for yet answer None rather than a
    shape made up from defaults, because a landing deciding on an invented shape decides wrongly."""
    from types import SimpleNamespace

    from sift.kernel.content import identity

    _Store.held = {
        "known": SimpleNamespace(media_type="audio", probed_at=1_700_000_000, acodec="flac"),
    }
    monkeypatch.setattr(identity, "ContentStore", _Store)

    shape = await landing.known_shape(object(), "known", Settings())  # type: ignore[arg-type]
    assert shape == landing.KnownShape(media_type="audio", probed_at=1_700_000_000, acodec="flac")
    assert await landing.known_shape(object(), "never-seen", Settings()) is None  # type: ignore[arg-type]
