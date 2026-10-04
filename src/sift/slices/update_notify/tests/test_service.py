# SPDX-License-Identifier: AGPL-3.0-or-later
"""The check itself: what it says, what it sends, and how it behaves when it cannot reach anything.

The feed is injected here rather than reached over a network, so these run offline and say the same
thing every time. The one test that does exercise the real fetch does it against a local server it
starts itself, to assert what actually goes out on the wire.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest

# A dismissal is a settings apply, and the settings service writes every change into the ledger,
# whose table registers itself on this import. Without it these are green only when another module
# has imported it first.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.kernel.version import app_version
from sift.slices.settings_hub.service import SettingsService
from sift.slices.update_notify.service import (
    DISMISSED_KEY,
    MAX_NOTES_CHARS,
    Release,
    UpdateService,
    _read_release,
)
from sift.testing.fixtures import create_user

CURRENT = "1.4.0"
NEWER = "1.5.0"
NOTES = "Faster thumbnails. Fixes a crash when a folder disappears mid-scan."


@pytest.fixture
async def settings(temp_db: Database) -> SettingsService:
    await temp_db.initialize_schema()
    return SettingsService(temp_db)


@pytest.fixture
async def admin(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.ADMIN)


class Feed:
    """A stand-in for the release feed that counts how often it was asked."""

    def __init__(self, release: Release | None) -> None:
        self.release = release
        self.calls = 0

    async def __call__(self) -> Release | None:
        self.calls += 1
        return self.release


def build(
    settings: SettingsService, feed: Feed, *, now: float = 1000.0
) -> tuple[UpdateService, list[float]]:
    """A service reading `feed`, with a clock the test moves by hand."""
    clock = [now]
    return UpdateService(settings, feed, clock=lambda: clock[0]), clock


@pytest.fixture(autouse=True)
def fixed_version(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the version Sift reports as running.

    Read from the installed package in production, which on a source checkout is whatever was
    installed there. A test asserting "an update is available" has to know both sides.
    """
    monkeypatch.setattr("sift.slices.update_notify.service.app_version", lambda: CURRENT)
    yield


async def test_an_available_update_is_reported_with_its_notes_and_its_page(
    settings: SettingsService,
) -> None:
    page = "https://releases.example/sift/tag/v1.5.0"
    feed = Feed(Release(version=NEWER, notes=NOTES, page=page))
    service, _ = build(settings, feed)

    said = await service.check()
    report = await service.report()

    assert said == f"Sift {NEWER} is available."
    assert report.update_available is True
    assert report.current_version == CURRENT
    assert report.latest_version == NEWER
    assert report.notes == NOTES
    assert report.release_page == page
    # Nothing to paste into a terminal: installing is the desktop application's, on a press.
    assert set(report.as_dict()) == {
        "current_version",
        "latest_version",
        "update_available",
        "notes",
        "release_page",
        "last_checked",
        "dismissed",
    }


async def test_the_same_version_is_not_an_update(settings: SettingsService) -> None:
    service, _ = build(settings, Feed(Release(version=CURRENT, notes=NOTES)))

    assert await service.check() == "Sift is up to date."
    report = await service.report()

    assert report.update_available is False
    assert report.latest_version == CURRENT
    # Notes belong to an update somebody might apply. There is nothing to apply, so there is
    # nothing to read about.
    assert report.notes == ""


async def test_an_older_published_version_is_not_an_update(settings: SettingsService) -> None:
    service, _ = build(settings, Feed(Release(version="1.3.0", notes=NOTES)))

    await service.check()
    assert (await service.report()).update_available is False


async def test_with_no_network_the_check_answers_and_says_nothing_is_known(
    settings: SettingsService,
) -> None:
    """A feed that cannot be read is not an error. The task's row says so in a sentence, and the
    route still answers."""
    service, _ = build(settings, Feed(None))

    assert await service.check() == "Could not read the list of Sift releases."
    report = await service.report()

    assert report.update_available is False
    assert report.latest_version is None
    assert report.notes == ""
    assert report.current_version == CURRENT
    assert report.release_page == ""


async def test_a_screen_asking_never_reaches_the_network(settings: SettingsService) -> None:
    """The check is a task with a When of its own, and the task is the one thing that fetches, so
    a screen opening fetches nothing and "Only when I press it" means silence."""
    feed = Feed(Release(version=NEWER, notes=NOTES))
    service, _ = build(settings, feed)

    await asyncio.gather(*(service.report() for _ in range(10)))

    assert feed.calls == 0
    assert (await service.report()).latest_version is None


async def test_each_run_of_the_check_reads_the_feed_once(settings: SettingsService) -> None:
    feed = Feed(Release(version=NEWER, notes=NOTES))
    service, _ = build(settings, feed)

    await service.check()
    await service.check()

    assert feed.calls == 2


async def test_a_dismissed_version_is_still_available_but_marked_dismissed(
    settings: SettingsService, admin: Viewer
) -> None:
    """Dismissing hides the banner, not the fact. The Updates section still says what is there."""
    service, _ = build(settings, Feed(Release(version=NEWER, notes=NOTES)))

    await service.check()
    await service.dismiss(admin, NEWER)
    report = await service.report()

    assert report.dismissed is True
    assert report.update_available is True
    assert report.latest_version == NEWER
    assert await settings.get_app(DISMISSED_KEY) == NEWER


async def test_a_dismissal_does_not_carry_to_the_next_release(
    settings: SettingsService, admin: Viewer
) -> None:
    service, _ = build(settings, Feed(Release(version=NEWER, notes=NOTES)))
    await service.check()
    await service.dismiss(admin, NEWER)
    assert (await service.report()).dismissed is True

    service._fetch = Feed(Release(version="1.6.0", notes=NOTES))
    await service.check()

    report = await service.report()

    assert report.latest_version == "1.6.0"
    assert report.dismissed is False


async def test_nothing_published_is_never_reported_as_dismissed(
    settings: SettingsService, admin: Viewer
) -> None:
    """A dismissal recorded earlier must not make "nothing is known" read as "you hid it"."""
    await settings.apply(admin, {DISMISSED_KEY: NEWER})
    service, _ = build(settings, Feed(None))

    await service.check()
    report = await service.report()

    assert report.latest_version is None
    assert report.dismissed is False


async def test_last_checked_is_stamped_even_when_the_feed_could_not_be_read(
    settings: SettingsService,
) -> None:
    service, _ = build(settings, Feed(None), now=4242.0)

    await service.check()
    report = await service.report()

    assert report.last_checked == 4242


async def test_before_any_check_last_checked_is_nothing(settings: SettingsService) -> None:
    service, _ = build(settings, Feed(None))

    assert (await service.report()).last_checked == 0


# --- reading the feed document -------------------------------------------------------------------


def test_reads_a_release_out_of_a_feed_document() -> None:
    body = json.dumps({"tag_name": " v1.5.0 ", "body": NOTES}).encode()

    release = _read_release(body)

    assert release == Release(version="v1.5.0", notes=NOTES)


def test_a_release_with_no_notes_is_still_a_release() -> None:
    assert _read_release(json.dumps({"tag_name": "v1.5.0"}).encode()) == Release("v1.5.0", "")
    assert _read_release(json.dumps({"tag_name": "v1.5.0", "body": None}).encode()) == Release(
        "v1.5.0", ""
    )


def test_notes_are_capped() -> None:
    body = json.dumps({"tag_name": "v1.5.0", "body": "x" * (MAX_NOTES_CHARS + 500)}).encode()

    release = _read_release(body)

    assert release is not None
    assert len(release.notes) == MAX_NOTES_CHARS


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json at all",
        b"\xff\xfe\x00",
        json.dumps([1, 2, 3]).encode(),
        json.dumps("a string").encode(),
        json.dumps({}).encode(),
        json.dumps({"tag_name": ""}).encode(),
        json.dumps({"tag_name": "   "}).encode(),
        json.dumps({"tag_name": 15}).encode(),
        json.dumps({"tag_name": None}).encode(),
    ],
)
def test_a_document_that_is_not_a_release_reads_as_nothing(body: bytes) -> None:
    """A feed that has moved or changed shape must degrade to silence, not to a notice built out of
    whatever happened to be in the reply."""
    assert _read_release(body) is None


def test_the_running_version_is_readable() -> None:
    """Read from the kernel, which reads the installed package. One declaration, one reader."""
    assert isinstance(app_version(), str)


async def test_a_release_found_tells_every_admin_s_open_screens_once(
    settings: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The banner and `Settings > Updates` read the check when they open, so a release the daily
    check found would reach a window already open only when it was reloaded; the check says so
    once, and a check that finds the same release again says nothing."""
    from sift.kernel.audience import EVERY_ADMIN
    from sift.kernel.changes import About

    told: list[tuple[object, object]] = []
    monkeypatch.setattr(
        "sift.slices.update_notify.service.announce_now",
        lambda who, about: told.append((who, about)),
    )
    service, _ = build(settings, Feed(Release(version=NEWER, notes=NOTES)))

    await service.check()
    await service.check()

    assert told == [(EVERY_ADMIN, About.SETTINGS)]
