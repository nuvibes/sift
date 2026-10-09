# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the log does, as two things somebody can choose once the library is open."""

from __future__ import annotations

from pathlib import Path

from sift.kernel.settings_registry import register_setting

NORMAL = "normal"
DETAILED = "detailed"

DETAIL_KEY = "logs.detail"
KEEP_MB_KEY = "logs.keep_mb"
HIDE_PERSONAL_KEY = "logs.hide_personal"

BYTES_PER_MB = 1024 * 1024


def detailed_from(value: object) -> bool:
    """Whether the stored answer means the detailed log; anything unrecognised is ordinary."""
    return value == DETAILED


def hide_personal_from(value: object) -> bool:
    """Whether personal detail is hidden as lines are written: only a stored True."""
    return value is True


#: About a fortnight at normal detail; less than a day of log is useless for a fault.
DEFAULT_KEEP_MB = 1024
#: The most the setting allows.
MOST_KEEP_MB = 10_000


def keep_bytes_from(value: object) -> int:
    """The stored megabytes as bytes, for the whole log, tolerating any stored value."""
    if not isinstance(value, str | int | float):
        return DEFAULT_KEEP_MB * BYTES_PER_MB
    try:
        megabytes = int(value)
    except ValueError:
        return DEFAULT_KEEP_MB * BYTES_PER_MB
    return max(1, megabytes) * BYTES_PER_MB


def per_file_bytes(total_bytes: int, backups: int) -> int:
    """How large one file may get: the setting is the total, and the handler adds backups."""
    return max(BYTES_PER_MB, total_bytes // max(1, backups + 1))


def largest_file_bytes(backups: int) -> int:
    """One file's size at the largest setting: what a start may let the file reach before the
    library's own setting is known, so a start never rolls a log the setting would keep."""
    return per_file_bytes(MOST_KEEP_MB * BYTES_PER_MB, backups)


def fit_within(log_file: Path, backups: int, total_bytes: int) -> int:
    """Delete the oldest rotated files until the log fits in `total_bytes`; returns bytes freed."""
    # The handler looks only at the current file, so a lowered setting would not shrink the rest.
    try:
        room = total_bytes - log_file.stat().st_size
    except OSError:
        room = total_bytes
    freed = 0
    number = 1
    while True:
        older = log_file.with_name(f"{log_file.name}.{number}")
        try:
            size = older.stat().st_size
        except OSError:
            # Rotation numbers contiguously, so past the kept range a gap ends the walk.
            if number > backups:
                break
            number += 1
            continue
        if size <= room and number <= backups:
            room -= size
        else:
            room = 0
            try:
                older.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash (Sift's own log file)
                freed += size
            except OSError:
                pass
        number += 1
    return freed


# Declared at import, so the settings screen generated from the registry shows it.
register_setting(
    key=DETAIL_KEY,
    scope="app",
    default=NORMAL,
    choices=(NORMAL, DETAILED),
    choice_labels=("Normal", "Detailed"),
    section="Logs",
    label="Log detail",
    help=(
        "Normal records what happened. Detailed also records each step, and fills the log faster."
    ),
)

# On the Logs page, as it changes what this log records; secrets stay hidden regardless.
register_setting(
    key=HIDE_PERSONAL_KEY,
    scope="app",
    default=False,
    section="Logs",
    label="Hide personal details in the log",
    help=(
        "Hides the names in file paths, usernames and email addresses as lines are written. "
        "Passwords, cookies and keys are always hidden."
    ),
    default_since="0.1.218",
)

register_setting(
    key=KEEP_MB_KEY,
    scope="app",
    default=DEFAULT_KEEP_MB,
    # Below about a hundred the log covers less than a day of ordinary use.
    minimum=16,
    maximum=MOST_KEEP_MB,
    unit="MB",
    section="Logs",
    label="Maximum log size",
    disclosure=(
        "The default holds about two weeks at normal detail, counting the current file and the "
        "older ones kept in the same folder. The log never grows past this size."
    ),
    help=("The most disk space the log may use. Sift deletes the oldest entries as it fills."),
)
