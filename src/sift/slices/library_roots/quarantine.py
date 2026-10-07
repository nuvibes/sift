# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files Sift would not take, and what became of them.

## It is two piles, and a screen showing one of them is misleading

**Files Sift moved.** Something Sift wrote (a download, an upload, a drop, a paste) failed the
ingress gate, so it was moved into the quarantine directory. Sift put it there, so Sift may move it,
and it does.

**Files left where they were.** A file already in somebody's library failed the same gate during a
scan. It is still sitting in their folder, untouched, because Sift reads a library and does not
move things around in it. What exists is a row saying it was walked past.

The two have opposite answers to "where is my file", which is why they are shown together and
labelled rather than merged into one list. The first pile is Sift's mess to clear up; the second is
a file on somebody's disk that Sift has an opinion about.

## Why the moved pile is read from the directory

Because the directory is the only thing that is true. The reason for each file is written beside it
as a note when it is moved (see the ingress gate), so what is listed here and why are the same
fact read from the same place. A table of quarantined files would be a second record of a directory
and would drift from it the first time somebody deleted a file by hand or restored a backup, and a
screen describing a quarantine nobody has is worse than no screen.

## Nothing here is kept for ever

A quarantined file is stored in the clear, which is right for a suspicious video and wrong for
whatever else somebody managed to download. So the pile can be aged out on a schedule an admin
sets (see `DEFAULT_KEEP_DAYS` for why the default keeps everything).
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG

from sift.kernel.config import Settings
from sift.kernel.db import Database, Row, in_clause
from sift.kernel.ingress import NOTE_SUFFIX
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: How long a quarantined file is kept before it is removed, unless an admin says otherwise.
#:
#: **ZERO: keep them for ever, until somebody says otherwise.** Thirty days would not be
#: unreasonable, but:
#:
#: A quarantined file is the PERSON'S BYTES, refused by Sift, which admits in the same breath that
#: it could not read them: the reasons include `UNREADABLE` and `NOT_DECODABLE`, and those are as
#: often a truncated download or a codec this build lacks as they are a bad file. Deleting on a
#: timer is therefore a permanent, unattended decision taken on the strength of NOT having
#: understood something, and it fails silently and totally: the bytes are simply gone, and the
#: only record is a log line nobody was reading.
#:
#: The counter-argument stands and is answered elsewhere rather than dismissed: a folder nobody
#: empties does grow for ever. The answer is to make the pile LEGIBLE (the card and the settings
#: row both carry the count, so it is a number somebody can act on) rather than to act for them.
#: A number a person can act on beats a timer that acts on their behalf.
#:
#: Deliberately NOT aligned with the thirty-day window a stranded RECORD gets. A record is
#: Sift's own bookkeeping and costs a row; this is somebody's file and costs the file.
DEFAULT_KEEP_DAYS = 0

#: What the retention rule is stored under.
KEEP_DAYS_KEY = "quarantine.keep_days"

_SECONDS_A_DAY = 86_400


@dataclass(frozen=True, slots=True)
class Quarantined:
    """One file Sift moved out of the way, as the screen shows it."""

    #: What to name it in a request to delete it. The filename on disk, never a path (see `resolve`).
    id: str
    #: What the file was called before Sift renamed it to something safe to put in a directory.
    original_name: str
    reason: str
    #: What the bytes turned out to actually be, where that was worked out.
    detected: str | None
    #: How it got here: a download, an upload, a drop, a paste.
    origin: str
    size_bytes: int | None
    quarantined_at: int
    #: False where the note is missing: a file quarantined before notes existed, or one whose note
    #: could not be written. The file is still listed, and says plainly that the reason is not known,
    #: because a file sitting in quarantine unexplained is exactly what somebody needs to be told.
    explained: bool = True


def _read_note(note: Path) -> dict[str, object]:
    try:
        found = json.loads(note.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _text(note: dict[str, object], key: str) -> str | None:
    value = note.get(key)
    return str(value) if isinstance(value, (str, int)) else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def keep_days_from(stored: object) -> int:
    """The retention rule as a whole number of days, or the default where nothing usable is stored.

    Here rather than beside either reader, because there are two of them (the screen that draws
    the rule and the job that acts on it), and two copies would be two answers to "how long are
    files kept", one of them shown and the other one enforced.

    A negative number is read as zero rather than as the default: somebody who typed one meant to
    turn the rule off, and quietly restoring thirty days would delete files they had just asked to
    keep.
    """
    try:
        days = int(str(stored))
    except (TypeError, ValueError):
        return DEFAULT_KEEP_DAYS
    return max(0, days)


def listing(settings: Settings) -> list[Quarantined]:
    """Everything in the quarantine directory, newest first.

    Notes are not listed as files of their own: they describe the file beside them. A file with no
    note is still listed, because the point of this screen is that nothing sits here unexplained,
    and leaving out the ones it cannot explain would be the opposite of that.
    """
    directory = settings.quarantine_dir
    found: list[Quarantined] = []
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        # No directory yet means nothing has ever been refused, which is a clean answer rather than
        # a failure: a fresh install has no quarantine folder until the first file needs one.
        return []

    for entry in entries:
        if entry.name.endswith(NOTE_SUFFIX):
            continue
        try:
            on_disk = entry.stat()
        except OSError:
            # Nothing to read: a symlink pointing at something that is not there, or a file that
            # went between listing the directory and reading it. The prune job runs on a timer
            # and somebody can empty the folder by hand. Not listing it is the whole answer.
            continue
        # One stat rather than two. `is_file()` is another one, and this screen is drawn over a
        # directory that may sit on a network mount.
        if not S_ISREG(on_disk.st_mode):
            continue
        note_path = entry.with_name(entry.name + NOTE_SUFFIX)
        note = _read_note(note_path) if note_path.exists() else {}
        found.append(
            Quarantined(
                id=entry.name,
                original_name=_text(note, "original_name") or entry.name,
                reason=_text(note, "reason") or "unknown",
                detected=_text(note, "detected"),
                origin=_text(note, "origin") or "unknown",
                size_bytes=_whole(note.get("size_bytes")) or on_disk.st_size,
                quarantined_at=_whole(note.get("quarantined_at")) or int(on_disk.st_mtime),
                explained=bool(note),
            )
        )
    return sorted(found, key=lambda one: one.quarantined_at, reverse=True)


def resolve(settings: Settings, name: str) -> Path | None:
    """The file this id names, or None if it names nothing in the quarantine directory.

    **The id is a bare filename and this is what enforces that.** It arrives in a URL from a browser,
    and a name carrying a separator or a `..` would let a request delete a file anywhere the process
    can reach, with the one route in Sift whose whole job is deleting the file it is given. So the
    name is refused outright if it is not a single path component, and the resolved path is then
    checked to be a direct child of the directory, which also catches a symlink pointing out of it.
    """
    if not name or name != Path(name).name or name in (".", ".."):
        return None
    directory = settings.quarantine_dir
    try:
        candidate = (directory / name).resolve()
        if candidate.parent != directory.resolve() or not candidate.is_file():
            return None
    except (OSError, RuntimeError):
        # RuntimeError as well as OSError, and it is not defensive padding: a symlink that points
        # at itself is how `Path.resolve` reports a loop, and it is the one case in this function
        # that a filename alone can produce. Letting it out would turn a bad name into a failed
        # request rather than into "there is no such file", which is what it is.
        return None
    return candidate


def remove(settings: Settings, name: str) -> bool:
    """Delete one quarantined file and its note. True if there was one to delete."""
    target = resolve(settings, name)
    if target is None:
        return False
    note = target.with_name(target.name + NOTE_SUFFIX)
    try:
        target.unlink()
    except OSError as exc:
        log.error("quarantine.remove_failed", error=str(exc))
        return False
    note.unlink(missing_ok=True)
    log.info("quarantine.removed")
    return True


def prune(settings: Settings, *, keep_days: int, now: float | None = None) -> int:
    """Remove everything older than the retention rule. Returns how many went.

    Zero days means keep everything and removes nothing: an off switch rather than an instruction
    to empty the directory, which is what a literal reading of "keep for zero days" would be and is
    nobody's intention when they type it.

    Age is taken from the note where there is one and from the file otherwise, so a file quarantined
    before notes existed still ages out rather than sitting here for ever.
    """
    if keep_days <= 0:
        return 0
    cutoff = (time.time() if now is None else now) - keep_days * _SECONDS_A_DAY
    removed = 0
    for one in listing(settings):
        if one.quarantined_at < cutoff and remove(settings, one.id):
            removed += 1
    if removed:
        log.info("quarantine.pruned", removed=removed, keep_days=keep_days)
    return removed


# --- what each folder refused, read for every folder in one go ------------------------------------

#: Each folder's first refusals by path, every folder in one statement.
_REFUSED_IN_ROOTS = """
SELECT * FROM (
  SELECT r.*, ROW_NUMBER() OVER (PARTITION BY r.root_id ORDER BY r.rel_path) AS place
    FROM scan_rejections r
   WHERE r.root_id IN (?*)
) WHERE place <= ?
 ORDER BY root_id, rel_path
"""

_REFUSED_COUNTS = """
SELECT root_id, COUNT(*) AS n FROM scan_rejections WHERE root_id IN (?*) GROUP BY root_id
"""

#: Folders per statement: a bound list has a ceiling.
_ROOTS_PER_STATEMENT = 500


async def refused_in_roots(
    database: Database, root_ids: Sequence[str], *, limit: int
) -> tuple[dict[str, list[Row]], dict[str, int]]:
    """Each folder's first `limit` refusals by path, and how many it refuses in all, by folder.
    A folder refusing nothing is absent from both."""
    rows: dict[str, list[Row]] = {}
    counts: dict[str, int] = {}
    ids = list(dict.fromkeys(root_ids))
    for start in range(0, len(ids), _ROOTS_PER_STATEMENT):
        chunk = ids[start : start + _ROOTS_PER_STATEMENT]
        sql, params = in_clause(_REFUSED_IN_ROOTS, chunk)
        for row in await database.fetch_all(sql, [*params, limit]):
            rows.setdefault(str(row["root_id"]), []).append(row)
        sql, params = in_clause(_REFUSED_COUNTS, chunk)
        for row in await database.fetch_all(sql, params):
            counts[str(row["root_id"])] = int(row["n"])
    return rows, counts


__all__ = [
    "DEFAULT_KEEP_DAYS",
    "KEEP_DAYS_KEY",
    "Quarantined",
    "keep_days_from",
    "listing",
    "prune",
    "refused_in_roots",
    "remove",
    "resolve",
]
