# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two refused piles as workbench cards: quarantine (in Sift's own folder) and skipped
(untouched where it was). Neither draws a picture, since a refused file was never imported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.ingress import NOTE_SUFFIX
from sift.slices.library_roots.queue import (
    QUARANTINE_QUEUE,
    SKIPPED_QUEUE,
    QuarantineQueue,
    SkippedQueue,
)

pytestmark = pytest.mark.unit

ADMIN = Viewer(id="admin", role=Role.ADMIN)


class Preferences:
    """The retention rule as an untouched install reads it, or as one that changed it does."""

    def __init__(self, keep_days: object = None) -> None:
        self._keep_days = keep_days

    async def get_app(self, key: str) -> Any:
        return self._keep_days if key == "quarantine.keep_days" else None

    async def get_user(self, user_id: str, key: str) -> Any:
        return None


class Rejections:
    """A stand-in for the one count the skipped card asks its service."""

    def __init__(self, total: int = 0) -> None:
        self._total = total
        #: Whether the card asked, rather than surveying every row.
        self.asked = 0

    async def rejection_count(self) -> int:
        self.asked += 1
        return self._total


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SIFT_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    made = Settings()
    made.quarantine_dir.mkdir(parents=True, exist_ok=True)
    return made


def put(settings: Settings, name: str, *, note: dict[str, object] | None = None) -> Path:
    target = settings.quarantine_dir / name
    target.write_bytes(b"refused bytes")
    if note is not None:
        target.with_name(target.name + NOTE_SUFFIX).write_text(json.dumps(note), encoding="utf-8")
    return target


# --- the quarantine card ------------------------------------------------------------------------


async def test_the_card_counts_what_is_in_the_directory(settings: Settings) -> None:
    put(settings, "one.mp4", note={"reason": "not_decodable"})
    put(settings, "two.bin")

    survey = await QuarantineQueue(settings, Preferences()).survey(ADMIN)

    assert survey.name == QUARANTINE_QUEUE
    assert survey.title == "Quarantine"
    assert survey.count == 2


async def test_an_empty_quarantine_is_a_card_at_zero_rather_than_no_card(
    settings: Settings,
) -> None:
    """An empty quarantine is a card at zero rather than no card."""
    queue = QuarantineQueue(settings, Preferences())

    assert await queue.available() is True
    assert (await queue.survey(ADMIN)).count == 0


async def test_the_card_says_how_long_a_file_is_kept_for(settings: Settings) -> None:
    """The card says how long a file is kept for."""
    survey = await QuarantineQueue(settings, Preferences(7)).survey(ADMIN)

    assert "7 days" in survey.decision


async def test_the_card_says_what_a_quarantined_download_is(settings: Settings) -> None:
    """What is in the pile, before how long it stays: a download that was not the promised file."""
    survey = await QuarantineQueue(settings, Preferences(30)).survey(ADMIN)

    assert survey.decision.startswith(
        "Downloads whose bytes weren't the file the page promised, held here for 30 days"
    )


async def test_one_day_is_not_written_as_one_days(settings: Settings) -> None:
    survey = await QuarantineQueue(settings, Preferences(1)).survey(ADMIN)

    assert "for 1 day each" in survey.decision


async def test_the_card_says_so_when_the_rule_is_switched_off(settings: Settings) -> None:
    """With the rule off, the card says files stay until deleted."""
    survey = await QuarantineQueue(settings, Preferences(0)).survey(ADMIN)

    assert "until you delete them" in survey.decision
    assert "days" not in survey.decision


async def test_an_unreadable_rule_falls_back_to_the_default(settings: Settings) -> None:
    """An unreadable rule falls back to the default, as the settings screen does."""
    survey = await QuarantineQueue(settings, Preferences("a fortnight")).survey(ADMIN)

    # The default is off (see `DEFAULT_KEEP_DAYS`); the card still draws.
    assert "until you delete them" in survey.decision


async def test_the_quarantine_card_draws_no_pictures_and_never_can(settings: Settings) -> None:
    """The quarantine card draws no pictures: there is no asset."""
    put(settings, "one.mp4")
    queue = QuarantineQueue(settings, Preferences())

    assert (await queue.survey(ADMIN)).preview == ()
    assert await queue.pictures_of(ADMIN, '{"name": "one.mp4"}') == ()


async def test_deleting_a_quarantined_file_cannot_be_taken_back(settings: Settings) -> None:
    """The only decision this pile records is a deletion, and the file is gone from the disk."""
    queue = QuarantineQueue(settings, Preferences())

    assert await queue.reverse(ADMIN, "receipt", '{"name": "one.mp4"}') is False


# --- the skipped card ---------------------------------------------------------------------------


async def test_the_skipped_card_counts_across_every_folder_together() -> None:
    """The skipped card counts with one `COUNT` across every folder."""
    rejections = Rejections(total=16)

    survey = await SkippedQueue(rejections).survey(ADMIN)  # type: ignore[arg-type]

    assert survey.name == SKIPPED_QUEUE
    assert survey.title == "Skipped"
    assert survey.count == 16
    assert rejections.asked == 1


async def test_the_skipped_card_says_nothing_was_moved() -> None:
    """The skipped card says nothing was moved."""
    survey = await SkippedQueue(Rejections()).survey(ADMIN)  # type: ignore[arg-type]

    assert "They stay where they are" in survey.decision


async def test_a_library_with_nothing_skipped_is_a_card_at_zero() -> None:
    queue = SkippedQueue(Rejections(total=0))  # type: ignore[arg-type]

    assert await queue.available() is True
    assert (await queue.survey(ADMIN)).count == 0


async def test_the_skipped_card_draws_no_pictures_either() -> None:
    queue = SkippedQueue(Rejections(total=3))  # type: ignore[arg-type]

    assert (await queue.survey(ADMIN)).preview == ()
    assert await queue.pictures_of(ADMIN, '{"rel_path": "a/b.mp4"}') == ()


async def test_letting_a_file_through_is_not_undone_here() -> None:
    """Letting a file through is not undone here: nothing was destroyed, and the next scan decides
    again."""
    queue = SkippedQueue(Rejections())  # type: ignore[arg-type]

    assert await queue.reverse(ADMIN, "receipt", '{"root_id": "r", "rel_path": "a/b.mp4"}') is False
