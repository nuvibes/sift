# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a backup file is called: the library's mark, the moment, and which no rule deletes."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sift.kernel.version import app_version
from sift.kernel.when import day_of
from sift.kernel.when import stamp as machine_stamp
from sift.kernel.whole_file import write_json_whole

#: The local time and offset in an automatic backup's name; a name with no offset is UTC.
_STAMP_IN_NAME = re.compile(r"-(?P<stamp>\d{8}-\d{6})(?P<offset>[+-]\d{4})?-")


def the_backup_from(when: float) -> str:
    """How a History line names a backup: by the day it was taken, on the server's clock."""
    day = day_of(when)
    return f"the backup from {day.day} {day:%B %Y}"


def moment_in_name(name: str) -> float | None:
    """When an automatic backup was taken, read from its name, or None for a name with no stamp."""
    found = _STAMP_IN_NAME.search(name)
    if found is None:
        return None
    return datetime.strptime(
        found["stamp"] + (found["offset"] or "+0000"), "%Y%m%d-%H%M%S%z"
    ).timestamp()


def _by_age(entry: Path) -> tuple[float, str]:
    """Rotation's order: the moment each name carries, oldest first; local time repeats an hour."""
    return (moment_in_name(entry.name) or 0.0, entry.name)


def _taken_at(entry: Path) -> float:
    """When an automatic backup was taken, from its name, else from the file itself. Blocking."""
    found = moment_in_name(entry.name)
    return entry.stat().st_mtime if found is None else found


#: The library's mark, the moment with its offset (`20260930-213000-0400`), then the version.
FILENAME_PREFIX = "sift-backup-"


FILENAME_SUFFIX = ".zip"


#: What a backup is called while it is packed, which no listing of backups matches (`_pack`).
PARTIAL_SUFFIX = ".partial"


#: Marks a backup saved by hand, which rotation never matches.
SAVED_MARK = "-saved"


#: How long a library's mark is, in hexadecimal digits. See `mark_of_library`.
LIBRARY_MARK_LENGTH = 12


_MARK = re.compile(rf"[0-9a-f]{{{LIBRARY_MARK_LENGTH}}}")


#: The file in a library's data folder that holds the mark its backups are named with.
LIBRARY_MARK_FILENAME = "backup-mark.json"


#: Rotation deletes only names this matches with this library's own mark.
_OURS = re.compile(
    rf"^{re.escape(FILENAME_PREFIX)}(?P<library>[0-9a-f]{{{LIBRARY_MARK_LENGTH}}})"
    rf"-\d{{8}}-\d{{6}}(?:[+-]\d{{4}})?-[A-Za-z0-9._+-]+{re.escape(FILENAME_SUFFIX)}$"
)


def _version_stamp() -> str:
    """The version as it goes onto a backup, never blank, so rotation can still match the name."""
    return app_version() or "unknown"


def filename_for(when: float, *, library: str, saved: bool = False) -> str:
    """An export's name: the library's mark, the local moment with its offset, the version."""
    stamp = machine_stamp(when, "%Y%m%d-%H%M%S%z")
    mark = SAVED_MARK if saved else ""
    return f"{FILENAME_PREFIX}{library}-{stamp}-{_version_stamp()}{mark}{FILENAME_SUFFIX}"


def is_backup_filename(name: str, *, library: str) -> bool:
    """Whether this library's rotation may delete this file: its own automatic backup."""
    matched = _OURS.match(name)
    if matched is None or matched["library"] != library:
        return False
    return not name.endswith(SAVED_MARK + FILENAME_SUFFIX)


#: A backup named before libraries marked theirs; no rule ever deletes one.
_UNMARKED = re.compile(
    rf"^{re.escape(FILENAME_PREFIX)}\d{{8}}-\d{{6}}(?:[+-]\d{{4}})?-[A-Za-z0-9._+-]+"
    rf"(?:{re.escape(FILENAME_SUFFIX)}|\.sqlite3)$"
)


#: Past this, the folder holds something else; the newest are shown.
MAX_UNMARKED = 200


def is_unmarked_backup(name: str) -> bool:
    """Whether this is a Sift backup whose name carries no library's mark. See `_UNMARKED`."""
    return _UNMARKED.match(name) is not None


def is_saved_by_hand(name: str, *, library: str) -> bool:
    """Whether this is a backup THIS library saved by hand (`SAVED_MARK`): no rule deletes it."""
    matched = _OURS.match(name)
    return (
        matched is not None
        and matched["library"] == library
        and name.endswith(SAVED_MARK + FILENAME_SUFFIX)
    )


@dataclass(frozen=True, slots=True)
class UnmarkedBackup:
    """One backup no rule deletes: unmarked, or saved by hand (`saved`)."""

    name: str
    #: When it was taken: the moment its name carries.
    taken_at: int
    size_bytes: int
    #: Saved by hand by this library, rather than unmarked.
    saved: bool = False


def _unmarked_in(folder: Path, library: str) -> list[UnmarkedBackup]:
    """The backups no rule deletes in a folder, newest first, at most `MAX_UNMARKED`."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    listed_here = []
    for entry in entries:
        saved = is_saved_by_hand(entry.name, library=library)
        if not saved and not is_unmarked_backup(entry.name):
            continue
        try:
            if not entry.is_file():
                continue
            size = entry.stat().st_size
        except OSError:  # pragma: no cover (a file taken away between the listing and its size)
            continue
        listed_here.append(UnmarkedBackup(entry.name, int(_taken_at(entry)), size, saved))
    listed_here.sort(key=lambda one: (one.taken_at, one.name), reverse=True)
    return listed_here[:MAX_UNMARKED]


def mark_of_library(data_dir: Path) -> str:
    """This library's backup mark: random, kept in its data folder, made again if copied."""
    record = data_dir / LIBRARY_MARK_FILENAME
    here = os.path.normcase(os.path.abspath(data_dir))
    try:
        written = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        written = None
    if isinstance(written, dict) and written.get("for") == here:
        kept = written.get("mark")
        if isinstance(kept, str) and _MARK.fullmatch(kept):
            return kept
    made = secrets.token_hex(LIBRARY_MARK_LENGTH // 2)
    data_dir.mkdir(parents=True, exist_ok=True)
    write_json_whole(record, {"mark": made, "for": here})
    return made
