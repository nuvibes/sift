# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the log records, and how much disk it may take.

## Why this file exists at all

The module holds the arithmetic that decides how much of somebody's disk a log may take, and the two
things it can get wrong are both silent. A default that is too small throws away what went wrong
before anybody reads it; a total handed straight to a rotating handler takes `backups + 1` times
what the screen said.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.log_settings import (
    BYTES_PER_MB,
    DEFAULT_KEEP_MB,
    DETAILED,
    NORMAL,
    detailed_from,
    fit_within,
    hide_personal_from,
    keep_bytes_from,
    per_file_bytes,
)


class TestWhatTheLogRecords:
    def test_the_detailed_word_means_detailed(self) -> None:
        assert detailed_from(DETAILED) is True

    def test_the_ordinary_word_means_ordinary(self) -> None:
        assert detailed_from(NORMAL) is False

    @pytest.mark.parametrize("stored", [None, "", "verbose", 1, True, object()])
    def test_anything_unreadable_falls_BACK_to_the_ordinary_log(self, stored: object) -> None:
        """The safe direction, and it is the whole reason this is not a plain `==` at the call site.

        A value that cannot be read must not turn detail ON. A log that quietly became enormous
        because a stored word was misspelled is the worse of the two failures.
        """
        assert detailed_from(stored) is False


class TestWhetherPersonalDetailIsHidden:
    def test_a_stored_true_hides_it(self) -> None:
        assert hide_personal_from(True) is True

    @pytest.mark.parametrize("stored", [None, False, 1, "true", "yes", object()])
    def test_anything_else_writes_the_log_whole(self, stored: object) -> None:
        """Only a person's own choice hides detail; a value nobody chose never does."""
        assert hide_personal_from(stored) is False


class TestHowMuchDiskTheLogMayTake:
    def test_a_stored_number_is_megabytes(self) -> None:
        assert keep_bytes_from(64) == 64 * BYTES_PER_MB

    def test_a_numeric_string_is_read_rather_than_refused(self) -> None:
        """A value written by an older version, or by hand, still means what it says."""
        assert keep_bytes_from("64") == 64 * BYTES_PER_MB

    @pytest.mark.parametrize("stored", [None, "", "lots", object()])
    def test_anything_unreadable_falls_back_to_the_measured_default(self, stored: object) -> None:
        assert keep_bytes_from(stored) == DEFAULT_KEEP_MB * BYTES_PER_MB

    @pytest.mark.parametrize("stored", [0, -1, -4096])
    def test_zero_and_below_still_leave_room_for_one_megabyte(self, stored: int) -> None:
        """Not the default, and not zero. Zero would hand the handler a cap it can never satisfy,
        and rolling back to a gigabyte would give somebody who asked for the smallest log the
        largest one."""
        assert keep_bytes_from(stored) == BYTES_PER_MB

    def test_the_default_is_a_gigabyte(self) -> None:
        """Pinned because it was MEASURED rather than picked. See the module. Changing it is a
        decision about how far back a person can look, not a tidy-up."""
        assert DEFAULT_KEEP_MB == 1024


class TestTheTotalIsDividedAmongTheFiles:
    def test_the_setting_is_the_TOTAL_not_the_size_of_one_file(self) -> None:
        """The defect this function exists for. A rotating handler takes a per-FILE cap and keeps
        `backups` more beside it, so handing it the total means `backups + 1` times the disk the
        label promised."""
        total = 600 * BYTES_PER_MB
        assert per_file_bytes(total, backups=5) == 100 * BYTES_PER_MB

    def test_with_no_backups_one_file_may_take_the_lot(self) -> None:
        total = 8 * BYTES_PER_MB
        assert per_file_bytes(total, backups=0) == total

    def test_a_share_smaller_than_a_megabyte_is_raised_to_one(self) -> None:
        """A handler given a cap of a few hundred bytes rotates on almost every line, which costs
        more in file churn than the disk it saves."""
        assert per_file_bytes(BYTES_PER_MB, backups=99) == BYTES_PER_MB

    @pytest.mark.parametrize("backups", [-1, -10])
    def test_a_negative_backup_count_cannot_divide_by_zero_or_invert_the_answer(
        self, backups: int
    ) -> None:
        """`max(1, backups + 1)` is doing two jobs: -1 would be a division by zero and anything
        below it would hand back a NEGATIVE cap, which a handler reads as "rotate immediately"."""
        total = 4 * BYTES_PER_MB
        assert per_file_bytes(total, backups=backups) == total

    def test_the_whole_of_the_total_is_accounted_for(self) -> None:
        """Every file at its cap adds up to no more than what was asked for. Integer division can
        only round DOWN, so the sum can be under and must never be over."""
        total = 1000 * BYTES_PER_MB
        for backups in range(0, 12):
            each = per_file_bytes(total, backups)
            assert each * (backups + 1) <= total


class TestLoweringTheSizeTrimsTheOlderFilesImmediately:
    """The setting is the TOTAL, and the rotating handler only ever looks at one file, so lowering
    it would leave every older file as large as the old setting let it grow (well past the new
    setting). `fit_within` is what makes the number on the screen true immediately."""

    @staticmethod
    def _log(tmp_path: Path, sizes: list[int]) -> Path:
        current = tmp_path / "sift.log"
        current.write_bytes(b"c" * sizes[0])
        for number, size in enumerate(sizes[1:], start=1):
            (tmp_path / f"sift.log.{number}").write_bytes(b"o" * size)
        return current

    def test_the_older_files_that_no_longer_fit_are_deleted_newest_kept(
        self, tmp_path: Path
    ) -> None:
        current = self._log(tmp_path, [100, 300, 200, 900])

        freed = fit_within(current, backups=5, total_bytes=700)

        assert freed == 900
        assert sorted(one.name for one in tmp_path.iterdir()) == [
            "sift.log",
            "sift.log.1",
            "sift.log.2",
        ]
        assert sum(one.stat().st_size for one in tmp_path.iterdir()) <= 700

    def test_once_one_file_does_not_fit_nothing_older_is_kept(self, tmp_path: Path) -> None:
        """Newest first, and a smaller file behind a big one is still OLDER than it: keeping it
        would keep old lines while newer ones were thrown away."""
        current = self._log(tmp_path, [100, 900, 50])

        fit_within(current, backups=5, total_bytes=700)

        assert sorted(one.name for one in tmp_path.iterdir()) == ["sift.log"]

    def test_the_current_file_is_never_touched(self, tmp_path: Path) -> None:
        current = self._log(tmp_path, [5000, 10])

        fit_within(current, backups=5, total_bytes=700)

        assert current.stat().st_size == 5000, "the handler holds it open and rotates it itself"
        assert not (tmp_path / "sift.log.1").exists()

    def test_a_log_already_within_the_setting_is_left_alone(self, tmp_path: Path) -> None:
        current = self._log(tmp_path, [100, 200, 200])

        assert fit_within(current, backups=5, total_bytes=700) == 0
        assert len(list(tmp_path.iterdir())) == 3

    def test_files_past_the_handlers_own_are_deleted_too(self, tmp_path: Path) -> None:
        """Left by a larger backup count on an earlier run: the handler never rotates them away,
        so nothing else ever would."""
        current = self._log(tmp_path, [10, 10, 10, 10])

        fit_within(current, backups=2, total_bytes=10_000)

        assert sorted(one.name for one in tmp_path.iterdir()) == [
            "sift.log",
            "sift.log.1",
            "sift.log.2",
        ]

    def test_a_missing_current_file_leaves_the_whole_total_for_the_older_ones(
        self, tmp_path: Path
    ) -> None:
        """Between a rotation and the next line there is no current file; the older ones then
        have the whole of the setting to fit in, not a share of it."""
        current = tmp_path / "sift.log"
        (tmp_path / "sift.log.1").write_bytes(b"o" * 600)
        (tmp_path / "sift.log.2").write_bytes(b"o" * 200)

        freed = fit_within(current, backups=5, total_bytes=700)

        assert freed == 200
        assert sorted(one.name for one in tmp_path.iterdir()) == ["sift.log.1"]

    def test_a_file_that_cannot_be_removed_is_left_and_not_counted_as_freed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A log that could not be trimmed is never a reason for a log line to fail."""
        current = self._log(tmp_path, [100, 900])

        def refuse(self: Path, missing_ok: bool = False) -> None:
            raise PermissionError("held open by another process")

        monkeypatch.setattr(Path, "unlink", refuse)

        assert fit_within(current, backups=5, total_bytes=700) == 0
        assert (tmp_path / "sift.log.1").exists()


def test_lowering_the_log_size_trims_the_older_files_immediately(tmp_path: Path) -> None:
    """The rotating handler looks at the one file it writes, so a setting lowered while a 27 MB
    rotated file exists would be exceeded until five more rotations pushed it out. Applying a lower
    size trims the oldest copies until the set fits the setting."""
    import logging
    import logging.handlers

    from sift.kernel import log as log_module

    log_file = tmp_path / "sift.log"
    log_file.write_bytes(b"x" * 1_000)
    for n in (1, 2, 3):
        (tmp_path / f"sift.log.{n}").write_bytes(b"x" * 10_000)
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=50_000, backupCount=3)
    root = logging.getLogger()  # nosemgrep: sift-no-print-or-raw-logger (the handlers under test)
    root.addHandler(handler)
    try:
        log_module.apply_log_preferences(detailed=False, per_file_bytes=5_000)
    finally:
        root.removeHandler(handler)
        handler.close()
    total = sum(f.stat().st_size for f in tmp_path.glob("sift.log*"))
    assert total <= 5_000 * 4
    assert log_file.exists(), "the file being written is never the one trimmed"
