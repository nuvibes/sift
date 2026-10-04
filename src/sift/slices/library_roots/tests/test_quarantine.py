# SPDX-License-Identifier: AGPL-3.0-or-later
"""The quarantine directory, read and aged out. `resolve` decides which file a delete may touch,
from a name taken out of a URL, so it must refuse every way out of the directory."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.ingress import NOTE_SUFFIX
from sift.slices.library_roots import quarantine
from sift.slices.library_roots.tests.conftest import POSIX_ONLY, WINDOWS_ONLY, junction

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SIFT_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    made = Settings()
    made.quarantine_dir.mkdir(parents=True, exist_ok=True)
    return made


def put(settings: Settings, name: str, *, note: dict[str, object] | None = None) -> Path:
    """A quarantined file, with the note the ingress gate would have written beside it."""
    target = settings.quarantine_dir / name
    target.write_bytes(b"refused bytes")
    if note is not None:
        target.with_name(target.name + NOTE_SUFFIX).write_text(json.dumps(note), encoding="utf-8")
    return target


# --- what the screen is shown -------------------------------------------------------------


def test_a_file_is_listed_with_the_reason_written_beside_it(settings: Settings) -> None:
    put(
        settings,
        "abc-payload.mp4",
        note={
            "reason": "extension_contradicts_signature",
            "detected": "application/x-dosexec",
            "origin": "download",
            "original_name": "cute_puppy.mp4",
            "size_bytes": 4096,
            "quarantined_at": 1_700_000_000,
        },
    )

    (one,) = quarantine.listing(settings)

    assert one.id == "abc-payload.mp4"
    assert one.original_name == "cute_puppy.mp4"
    assert one.reason == "extension_contradicts_signature"
    assert one.detected == "application/x-dosexec"
    assert one.origin == "download"
    assert one.explained


def test_the_note_is_not_listed_as_a_file_of_its_own(settings: Settings) -> None:
    """It describes the file beside it. Listed separately it would double every row and offer a
    Delete button pointed at an explanation."""
    put(settings, "abc-payload.mp4", note={"reason": "not_decodable"})

    assert [one.id for one in quarantine.listing(settings)] == ["abc-payload.mp4"]


def test_a_file_with_no_note_is_still_listed_and_says_so(settings: Settings) -> None:
    """Quarantined before notes existed, or one whose note could not be written.

    Left out, the screen would show an empty quarantine over a directory with a file in it, which
    is the exact failure the screen exists to fix, reintroduced by the fix.
    """
    put(settings, "older-file.bin")

    (one,) = quarantine.listing(settings)

    assert one.id == "older-file.bin"
    assert one.reason == "unknown"
    assert not one.explained


def test_a_directory_that_does_not_exist_is_an_empty_list_not_a_failure(tmp_path: Path) -> None:
    """A fresh install has no quarantine folder until the first file needs one."""
    settings = Settings(SIFT_QUARANTINE_DIR=tmp_path / "never-made")

    assert quarantine.listing(settings) == []


# --- which file a request may name --------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "../../etc/passwd",
        "sub/inside.txt",
        "/etc/passwd",
        "",
        ".",
        "..",
    ],
)
def test_a_name_that_is_not_a_bare_filename_resolves_to_nothing(
    settings: Settings, name: str
) -> None:
    """The whole of the defence on the delete route.

    The name arrives in a URL. Every one of these would reach outside the directory if the path were
    simply joined, and the route deletes what it is given.
    """
    (settings.quarantine_dir.parent / "outside.txt").write_bytes(b"not yours")

    assert quarantine.resolve(settings, name) is None


@POSIX_ONLY
def test_a_symlink_pointing_out_of_the_directory_resolves_to_nothing(settings: Settings) -> None:
    """A bare filename that is not a bare file.

    The name passes every check on the string, so the resolved path is checked too: a link is the
    one way the two answers differ.
    """
    outside = settings.quarantine_dir.parent / "outside.txt"
    outside.write_bytes(b"not yours")
    (settings.quarantine_dir / "innocent.txt").symlink_to(outside)

    assert quarantine.resolve(settings, "innocent.txt") is None
    assert outside.exists()


@WINDOWS_ONLY
def test_a_junction_pointing_out_of_the_directory_resolves_to_nothing(settings: Settings) -> None:
    """A junction out of the directory resolves to nothing: only the resolved path shows it."""
    outside = settings.quarantine_dir.parent / "outside"
    outside.mkdir()
    (outside / "not-yours.txt").write_bytes(b"not yours")
    junction(settings.quarantine_dir / "innocent", outside)

    assert quarantine.resolve(settings, "innocent") is None
    assert (outside / "not-yours.txt").exists()


def test_a_name_dressed_up_as_a_path_is_refused_before_the_disk_is_touched(
    settings: Settings,
) -> None:
    """`./abc.mp4` is refused though it resolves correctly: the contract is a bare filename."""
    put(settings, "abc-payload.mp4")

    assert quarantine.resolve(settings, "./abc-payload.mp4") is None


def test_a_real_file_in_the_directory_resolves(settings: Settings) -> None:
    """The known positive. Without it every refusal above could be this returning None always."""
    put(settings, "abc-payload.mp4")

    assert quarantine.resolve(settings, "abc-payload.mp4") is not None


# --- removing, and ageing out --------------------------------------------------------------


def test_removing_takes_the_note_with_the_file(settings: Settings) -> None:
    target = put(settings, "abc-payload.mp4", note={"reason": "not_decodable"})

    assert quarantine.remove(settings, "abc-payload.mp4") is True

    assert not target.exists()
    assert not target.with_name(target.name + NOTE_SUFFIX).exists()


def test_removing_something_that_is_not_there_is_a_no(settings: Settings) -> None:
    assert quarantine.remove(settings, "never-existed.mp4") is False


def test_ageing_out_removes_only_what_is_older_than_the_rule(settings: Settings) -> None:
    now = 1_700_000_000.0
    day = 86_400
    put(settings, "old.mp4", note={"quarantined_at": int(now - 40 * day)})
    put(settings, "recent.mp4", note={"quarantined_at": int(now - 3 * day)})

    assert quarantine.prune(settings, keep_days=30, now=now) == 1

    assert [one.id for one in quarantine.listing(settings)] == ["recent.mp4"]


class _Context:
    """The two things the sweep tells a job's row: how far it got, and what it did."""

    def __init__(self) -> None:
        self.note = ""

    async def set_progress(self, _fraction: float) -> None:
        return None

    async def set_note(self, note: str) -> None:
        self.note = note


class _Preferences:
    def __init__(self, keep_days: object) -> None:
        self.keep_days = keep_days

    async def get_app(self, key: str) -> object:
        assert key == quarantine.KEEP_DAYS_KEY
        return self.keep_days


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("keep_days", "left"), [(30, ["recent.mp4"]), (0, ["old.mp4", "recent.mp4"])]
)
async def test_the_sweep_ages_files_out_by_the_days_maintenance_keeps_them(
    settings: Settings, keep_days: int, left: list[str]
) -> None:
    """The task reads `Settings > Maintenance` when it runs: thirty days takes the forty-day file,
    and zero, which keeps everything, takes nothing."""
    import time
    from typing import Any, cast

    from sift.slices.library_roots.jobs import prune_quarantine

    day = 86_400
    put(settings, "old.mp4", note={"quarantined_at": int(time.time() - 40 * day)})
    put(settings, "recent.mp4", note={"quarantined_at": int(time.time() - 3 * day)})

    await prune_quarantine(
        cast(Any, _Context()),
        settings=settings,
        preferences=cast(Any, _Preferences(keep_days)),
        queue=cast(Any, None),
    )

    assert sorted(one.id for one in quarantine.listing(settings)) == left


def test_zero_days_keeps_everything_rather_than_emptying_the_directory(
    settings: Settings,
) -> None:
    """The off switch.

    Read literally, "keep for zero days" means delete everything now, which is nobody's intention
    when they type it into a box labelled how long to keep things, and is unrecoverable.
    """
    put(settings, "old.mp4", note={"quarantined_at": 1})

    assert quarantine.prune(settings, keep_days=0, now=1_700_000_000.0) == 0
    assert len(quarantine.listing(settings)) == 1


def test_a_file_with_no_note_still_ages_out(settings: Settings) -> None:
    """Otherwise the files that predate notes are the ones that stay for ever, which is backwards:
    they are the oldest things in the directory."""
    old = put(settings, "older-file.bin")
    import os

    os.utime(old, (1_600_000_000, 1_600_000_000))

    assert quarantine.prune(settings, keep_days=30, now=1_700_000_000.0) == 1
    assert quarantine.listing(settings) == []


# --- what the directory can hold besides files ------------------------------------------------


def test_a_note_that_cannot_be_read_leaves_the_file_listed_and_unexplained(
    settings: Settings,
) -> None:
    """A note truncated by a full disk, or written by a version that wrote something else.

    The file is still listed and still says the reason is not known, which is the whole promise of
    this screen: dropping it because its note is unreadable would hide the file with the worst
    provenance in the directory.
    """
    target = put(settings, "abc-payload.mp4")
    target.with_name(target.name + NOTE_SUFFIX).write_text("{not json", encoding="utf-8")

    (listed,) = quarantine.listing(settings)

    assert listed.id == "abc-payload.mp4"
    assert listed.explained is False
    assert listed.reason == "unknown"


def test_a_note_that_is_json_but_not_a_record_is_read_as_no_note(settings: Settings) -> None:
    target = put(settings, "abc-payload.mp4")
    target.with_name(target.name + NOTE_SUFFIX).write_text('["a list"]', encoding="utf-8")

    (listed,) = quarantine.listing(settings)

    assert listed.explained is False


@POSIX_ONLY
def test_a_link_pointing_at_nothing_is_not_listed(settings: Settings) -> None:
    """The directory is on disk and Sift is not the only thing that can write to it. A link with
    nothing behind it has no size and no age, so there is nothing to draw and nothing to say."""
    (settings.quarantine_dir / "dangling.mp4").symlink_to(settings.quarantine_dir / "gone.mp4")
    put(settings, "real.mp4")

    assert [one.id for one in quarantine.listing(settings)] == ["real.mp4"]


def test_a_folder_in_the_directory_is_not_listed_as_a_file(settings: Settings) -> None:
    (settings.quarantine_dir / "a-folder").mkdir()
    put(settings, "real.mp4")

    assert [one.id for one in quarantine.listing(settings)] == ["real.mp4"]


@POSIX_ONLY
def test_a_link_that_points_at_itself_resolves_to_nothing_rather_than_failing(
    settings: Settings,
) -> None:
    """`Path.resolve` reports a symlink loop by raising, and not with the error type everything
    else here raises. Uncaught it would turn a bad filename into a failed request; what it means
    is that the name leads nowhere, which is the same answer as any other unusable name.
    """
    (settings.quarantine_dir / "a").symlink_to(settings.quarantine_dir / "b")
    (settings.quarantine_dir / "b").symlink_to(settings.quarantine_dir / "a")

    assert quarantine.resolve(settings, "a") is None
    assert quarantine.remove(settings, "a") is False


@pytest.mark.skipif(
    # `os.geteuid` does not exist on Windows, and this decorator runs at IMPORT time:
    # reaching for it there fails the whole module before a test is collected.
    sys.platform == "win32" or os.geteuid() == 0,
    reason="root ignores the permission bits this relies on, and the permission bits this relies on are accepted and then ignored by Windows, so the folder stays readable and the refusal being tested never happens on Windows",
)
def test_a_file_that_cannot_be_deleted_is_reported_as_not_deleted(settings: Settings) -> None:
    """A read-only quarantine directory, which is what a restored backup or a tightened mount
    produces. Saying the file was removed when it is still there is the one answer that would send
    somebody away believing a suspicious file is gone."""
    target = put(settings, "abc-payload.mp4", note={"reason": "not_decodable"})
    os.chmod(settings.quarantine_dir, 0o500)
    try:
        assert quarantine.remove(settings, "abc-payload.mp4") is False
    finally:
        os.chmod(settings.quarantine_dir, 0o700)

    assert target.exists(), "and the file is still there, which is what the answer said"


def test_a_prune_that_ages_nothing_out_still_answers_zero(settings: Settings) -> None:
    """The ordinary day. The job runs on a timer whether or not there is anything to do, so this
    is the path it takes almost every time it runs."""
    put(settings, "recent.mp4", note={"quarantined_at": 1_699_999_000})

    assert quarantine.prune(settings, keep_days=30, now=1_700_000_000.0) == 0
    assert len(quarantine.listing(settings)) == 1


def test_the_retention_rule_falls_back_where_nothing_usable_is_stored() -> None:
    """One reader for both the screen that draws the rule and the job that acts on it, so the two
    cannot disagree about how long files are kept."""
    assert quarantine.keep_days_from(7) == 7
    assert quarantine.keep_days_from("7") == 7
    assert quarantine.keep_days_from(None) == quarantine.DEFAULT_KEEP_DAYS
    assert quarantine.keep_days_from("a fortnight") == quarantine.DEFAULT_KEEP_DAYS


def test_a_negative_retention_rule_is_off_rather_than_the_default(settings: Settings) -> None:
    """Somebody who typed one was turning the rule off. Quietly restoring thirty days would delete
    the files they had just asked to keep, which is the one outcome that cannot be undone."""
    put(settings, "old.mp4", note={"quarantined_at": 1})

    assert quarantine.keep_days_from(-1) == 0
    assert quarantine.prune(settings, keep_days=quarantine.keep_days_from(-1)) == 0
    assert len(quarantine.listing(settings)) == 1


# --- when the machine refuses --------------------------------------------------------------------
#
# The call is made to fail, since links and permission bits cannot be arranged on Windows.


def _refuses(*_args: object, **_kwargs: object) -> object:
    raise PermissionError(13, "Permission denied")


def test_a_file_that_will_not_answer_when_asked_about_is_not_listed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is nothing to draw: no size, no age, and no way to know whether it is even a file.
    Leaving it off the list is the whole answer, and it must not take the screen down with it."""
    unreadable = put(settings, "unanswerable.mp4")
    put(settings, "real.mp4")
    real_stat = Path.stat

    def refuse_that_one(self: Path, **_kwargs: object) -> os.stat_result:
        if self.name == unreadable.name:
            raise PermissionError(13, "Permission denied")
        return real_stat(self)

    monkeypatch.setattr(Path, "stat", refuse_that_one)

    assert [one.id for one in quarantine.listing(settings)] == ["real.mp4"]


def test_a_name_the_machine_will_not_resolve_leads_nowhere_rather_than_failing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uncaught this would turn a bad filename into a failed request. What it means is that the
    name leads nowhere, which is the same answer as any other unusable name, and the route in
    front of it has to be able to say "there is no such file" without raising."""
    put(settings, "a.mp4")
    monkeypatch.setattr(Path, "resolve", _refuses)

    assert quarantine.resolve(settings, "a.mp4") is None
    assert quarantine.remove(settings, "a.mp4") is False


def test_a_file_that_cannot_be_deleted_is_reported_as_not_deleted_on_any_platform(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saying the file was removed while it is still there is the one answer that would send
    somebody away believing a suspicious file is gone."""
    target = put(settings, "abc-payload.mp4", note={"reason": "not_decodable"})
    monkeypatch.setattr(Path, "unlink", _refuses)

    assert quarantine.remove(settings, target.name) is False
    assert target.exists(), "it reported a failure and the file is still there, which is the truth"
