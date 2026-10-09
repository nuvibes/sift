# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files Sift would not take, and what became of them: the ones it moved, and the ones it left.
The moved pile is read from the quarantine directory, the only record that stays true."""

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

#: Zero keeps everything: these are the person's bytes, often refused only because Sift could not
#: read them.
DEFAULT_KEEP_DAYS = 0

KEEP_DAYS_KEY = "quarantine.keep_days"

_SECONDS_A_DAY = 86_400


@dataclass(frozen=True, slots=True)
class Quarantined:
    """One file Sift moved out of the way, as the screen shows it."""

    #: The filename on disk, never a path (see `resolve`).
    id: str
    original_name: str
    reason: str
    detected: str | None
    origin: str
    size_bytes: int | None
    quarantined_at: int
    #: False where the note is missing; the file is still listed, saying the reason is not known.
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
    """The retention rule in days, read in one place for the screen and the job; negative means
    zero."""
    try:
        days = int(str(stored))
    except (TypeError, ValueError):
        return DEFAULT_KEEP_DAYS
    return max(0, days)


def listing(settings: Settings) -> list[Quarantined]:
    """Everything in the quarantine directory, newest first, notes folded into their files."""
    directory = settings.quarantine_dir
    found: list[Quarantined] = []
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        # No directory yet: nothing has ever been refused.
        return []

    for entry in entries:
        if entry.name.endswith(NOTE_SUFFIX):
            continue
        try:
            on_disk = entry.stat()
        except OSError:
            # Gone or a dangling link between listing and reading.
            continue
        # One stat; the directory may be on a network mount.
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
    """The file this id names, or None. The id must be one bare filename directly inside the
    directory."""
    if not name or name != Path(name).name or name in (".", ".."):
        return None
    directory = settings.quarantine_dir
    try:
        candidate = (directory / name).resolve()
        if candidate.parent != directory.resolve() or not candidate.is_file():
            return None
    except (OSError, RuntimeError):
        # A self-referencing symlink raises RuntimeError from `resolve`.
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
    """Remove everything older than the retention rule; zero days removes nothing. Returns how many
    went."""
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

_ROOTS_PER_STATEMENT = 500


async def refused_in_roots(
    database: Database, root_ids: Sequence[str], *, limit: int
) -> tuple[dict[str, list[Row]], dict[str, int]]:
    """Each folder's first `limit` refusals by path, and its total; folders refusing nothing are
    absent."""
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
