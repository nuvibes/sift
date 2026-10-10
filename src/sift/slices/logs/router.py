# SPDX-License-Identifier: AGPL-3.0-or-later
"""The end of the log, and a copy fit to leave the machine; admin-only, read-only."""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.log import LOG_FILENAME
from sift.kernel.wiring import SETTINGS, part_of
from sift.logbundle import write_archive
from sift.slices.auth import require_admin
from sift.slices.logs.models import LogLine, LogPage
from sift.slices.logs.tail import MOST_LINES, SEARCH_BUDGET, newest_matching

router = APIRouter(tags=["logs"])

DEFAULT_LINES = 200

#: A filter keeps its level and everything louder.
LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "critical")

Level = Literal["debug", "info", "warning", "error", "critical"]

#: A bound past any real `log_backups`, so a folder filled by hand is never an endless walk.
_MOST_ROTATIONS = 50

#: Never searched: every line carries them, so "info" would match everything.
_NOT_SEARCHED = frozenset({"timestamp", "level"})

_MOST_SEARCH = 200

#: A search that is a job's id: the whole kept log is searched, and the job's summary leads.
_A_JOB_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}", re.IGNORECASE)

#: A search made only of these is in the raw line exactly as typed, so a block can be passed over.
_AS_WRITTEN = re.compile(r"[A-Za-z0-9_.:-]+")

#: The line that says the whole of a job (`kernel.jobs.worker_pool._summarize`).
_SUMMARY = "job.summary"


def _parsed(line: str) -> LogLine:
    """One line, taken apart if it will come apart; otherwise its raw text alone."""
    try:
        held: Any = json.loads(line)
    except ValueError:
        return LogLine(raw=line)
    if not isinstance(held, dict):
        return LogLine(raw=line)
    return LogLine(
        at=_text(held.get("timestamp")),
        level=_text(held.get("level")),
        event=_text(held.get("event")),
        raw=line,
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


@router.get("/logs", response_model=LogPage)
async def recent(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
    lines: Annotated[int, Query(ge=1, le=MOST_LINES)] = DEFAULT_LINES,
    level: Annotated[Level | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=_MOST_SEARCH)] = None,
) -> LogPage:
    """The end of the log, oldest first, narrowed here rather than on the screen."""
    settings = part_of(request, SETTINGS)
    path = Path(settings.data_dir) / LOG_FILENAME
    found = await asyncio.to_thread(_read, path, lines, level, search)
    return found


def _read(path: Path, lines: int, level: str | None = None, search: str | None = None) -> LogPage:
    """The blocking half, so the route above is one `to_thread` and nothing else."""
    try:
        size = path.stat().st_size
    except OSError:
        return LogPage(lines=[], path=str(path), present=False)
    keep = _keeps(level, search)
    needle = (search or "").strip()
    job = needle.upper() if _A_JOB_ID.fullmatch(needle) else None
    tail = newest_matching(
        _with_rotations(path),
        lines,
        keep,
        # Bounded only when filtered: an unfiltered read has its lines within a few blocks. A job
        # is looked for in the whole log: its id is in few lines, and those are anywhere.
        budget=None if keep is None or job is not None else SEARCH_BUDGET,
        within=needle.casefold().encode() if _AS_WRITTEN.fullmatch(needle) else None,
    )
    found = [_parsed(one) for one in tail.lines]
    if job is not None:
        found = _summary_first(found, job)
    return LogPage(
        lines=found,
        path=str(path),
        size_bytes=size,
        present=True,
        searched_bytes=tail.read_bytes,
        whole=tail.whole,
    )


def _summary_first(lines: list[LogLine], job: str) -> list[LogLine]:
    """A job's lines with its summary first: where its time went, before what it did."""
    led = [one for one in lines if one.event == _SUMMARY and _record_job(one.raw) == job]
    return led + [one for one in lines if one not in led]


def _record_job(raw: str) -> str | None:
    record = _record(raw)
    said = None if record is None else record.get("job_id")
    return said.upper() if isinstance(said, str) else None


def _with_rotations(path: Path) -> list[Path]:
    """The log and the rotated files beside it, newest first: an error may be in `.1`."""
    return [path, *(path.with_name(f"{path.name}.{n}") for n in range(1, _MOST_ROTATIONS + 1))]


def _keeps(level: str | None, search: str | None) -> Callable[[str], bool] | None:
    """A filtered read's test of one raw line; a line with no level passes only at Debug."""
    floor = LEVELS.index(level) if level in LEVELS else 0
    needle = (search or "").strip().casefold()
    if floor == 0 and not needle:
        return None

    def keep(raw: str) -> bool:
        record = _record(raw)
        if floor > 0:
            said = record.get("level") if record is not None else None
            rank = LEVELS.index(said) if isinstance(said, str) and said in LEVELS else -1
            if rank < floor:
                return False
        if not needle:
            return True
        return needle in (raw if record is None else _searched_text(record)).casefold()

    return keep


def _record(raw: str) -> dict[str, Any] | None:
    try:
        held: Any = json.loads(raw)
    except ValueError:
        return None
    return held if isinstance(held, dict) else None


def _searched_text(record: dict[str, Any]) -> str:
    """The event and the fields as the screen draws them, so a match is a visible one."""
    event = record.get("event")
    fields = (
        f"{key}={value if isinstance(value, str) else json.dumps(value)}"
        for key, value in record.items()
        if key not in _NOT_SEARCHED and key != "event"
    )
    return "  ".join([event if isinstance(event, str) else "", *fields])


@router.get(
    "/logs/archive",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def archive(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """`Download log`: the library's log whole, redacted, zipped as the desktop app does."""
    settings = part_of(request, SETTINGS)
    places = [("library", Path(settings.data_dir))]
    # The app's own logs too: a browser window has no other way to reach them.
    if (app := await asyncio.to_thread(app_logs, settings)) is not None:
        places.append(("app", app))
    made = io.BytesIO()
    await asyncio.to_thread(write_archive, made, places)
    return Response(made.getvalue(), media_type="application/zip")


def app_logs(settings: Settings) -> Path | None:
    """The desktop app's log folder, as the app that started this backend said; else None."""
    folder = settings.app_log_dir
    return folder if folder is not None and folder.is_dir() else None
