# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structured logging and timing; redaction is `kernel/redaction.py`. The only module that touches
the standard library's logging primitives. Lines written before the library opens hide names."""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TypeVar, cast

import structlog

from sift.kernel import redaction
from sift.kernel.redaction import REDACTED as REDACTED
from sift.kernel.redaction import hide_identity as hide_identity
from sift.kernel.redaction import hide_identity_in_path as hide_identity_in_path

F = TypeVar("F", bound=Callable[..., Any])

#: Named once: the boot, the lifespan and the route that reads the log must agree on it.
LOG_FILENAME = "sift.log"


# Set at boot, then by the library's `logs.hide_personal`. Secrets stay hidden regardless.
_redact_personal = True


def redacts_personal() -> bool:
    """Whether personal detail is hidden, so a child process can be set the same way."""
    return _redact_personal


def level_name() -> str:
    """The level the pipeline is at, by name, for the same reason."""
    return logging.getLevelName(logging.getLogger().level)


def path_facts(path: str | Path) -> dict[str, object]:
    """What a path cannot say: whether the file is there, readable, and how big."""
    p = Path(path)
    try:
        exists = p.exists()
        readable = os.access(p, os.R_OK) if exists else False
        size = p.stat().st_size if exists and p.is_file() else None
    except OSError:
        exists = readable = False
        size = None

    return {
        "path_exists": exists,
        "path_readable": readable,
        "path_size": size,
    }


def hashed(value: object) -> str:
    """A stable, opaque handle for a sensitive value, so two lines can be matched up."""
    digest = hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()
    return digest[:12]


def redact(value: Any, _key: str = "", *, always_personal: bool = False) -> Any:
    """`redaction.redact` at the setting, or always hiding names for text leaving the machine."""
    return redaction.redact(value, _key, personal=_redact_personal or always_personal)


def _redaction_processor(
    _logger: object, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Applied to every event, so nothing reaches a handler unscrubbed."""
    return {k: redact(v, str(k)) for k, v in event_dict.items()}


#: The level the process was started at; kept rather than re-read, since tests set it directly.
_boot_level = logging.INFO


def tell_the_shell(line: str) -> None:
    """One fixed word on stdout, which the desktop shell reads for the moment the socket opens."""
    print(line, flush=True)


def _pin_driver_logger() -> None:
    """The driver's debug lines carry bound values (a file's name), so they never reach the file."""
    logging.getLogger("aiosqlite").setLevel(logging.INFO)


def apply_log_preferences(
    *, detailed: bool, per_file_bytes: int, hide_personal: bool | None = None
) -> None:
    """Move the running handlers to the stored level and per-file cap, without a restart."""
    global _redact_personal
    if hide_personal is not None:
        _redact_personal = hide_personal
    root = logging.getLogger()
    # Never louder than the boot level, so SIFT_LOG_LEVEL holds once the library opens.
    root.setLevel(logging.DEBUG if detailed else max(_boot_level, logging.INFO))
    _pin_driver_logger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler):
            lowered = per_file_bytes < handler.maxBytes
            handler.maxBytes = per_file_bytes
            # The handler only watches the file it writes, so the older ones are trimmed here.
            if lowered:
                from sift.kernel.log_settings import fit_within

                handler.acquire()
                try:
                    fit_within(
                        Path(handler.baseFilename),
                        handler.backupCount,
                        per_file_bytes * (handler.backupCount + 1),
                    )
                finally:
                    handler.release()


class _CappedRotatingFileHandler(RotatingFileHandler):
    """A rotating handler whose files together never exceed `maxBytes * (backupCount + 1)`."""

    def doRollover(self) -> None:
        super().doRollover()
        from sift.kernel.log_settings import fit_within

        fit_within(
            Path(self.baseFilename), self.backupCount, self.maxBytes * (self.backupCount + 1)
        )


# --- Lines kept at Detailed, counted at Normal -----------------------------------------------------

#: A job's own claim and settle: its `job.summary` carries every field they give, and its timing
#: record's (`_JOB_STAGE`) time as `ran_ms`.
SAID_BY_THE_SUMMARY = frozenset({"job.claimed", "job.done"})

#: Lines written once per file of a batch: counted into the job's summary (`folded`), or into a
#: minute's `log.folded` line outside a job.
COUNTED_PER_BATCH = frozenset(
    {"content.location_missing", "importing.skipped", "job.enqueue_deduped"}
)

#: How often the counts of lines written outside any job are said.
FOLDED_EVERY_SECONDS = 60.0

_folded_outside: dict[str, int] = {}
_folded_since = time.monotonic()
_folded_lock = threading.Lock()


def _detailed() -> bool:
    return logging.getLogger().getEffectiveLevel() <= logging.DEBUG


def _fold(
    _logger: object, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """At Normal, a line the job's summary already says is dropped, and a per-file line counted."""
    event = event_dict.get("event")
    if event in COUNTED_PER_BATCH:
        cost = _JOB_COST.get()
        if cost is not None:
            cost.counted(event)
        else:
            with _folded_lock:
                _folded_outside[event] = _folded_outside.get(event, 0) + 1
    elif _folded_outside and time.monotonic() - _folded_since >= FOLDED_EVERY_SECONDS:
        _say_folded()
    said = event in SAID_BY_THE_SUMMARY or (
        event == "timing" and event_dict.get("stage") == _JOB_STAGE
    )
    if (said or event in COUNTED_PER_BATCH) and not _detailed():
        raise structlog.DropEvent
    return event_dict


def _say_folded() -> None:
    """One line with the counts of the per-file lines written outside a job since the last."""
    global _folded_since
    with _folded_lock:
        counts = dict(sorted(_folded_outside.items()))
        _folded_outside.clear()
        seconds = round(time.monotonic() - _folded_since)
        _folded_since = time.monotonic()
    if counts:
        get_logger(__name__).info("log.folded", counts=counts, seconds=seconds)


def configure_logging(
    level: str = "INFO",
    *,
    redact_personal: bool = True,
    log_file: Path | None = None,
    max_bytes: int = 0,
    backups: int = 0,
    warn_unredacted: bool = True,
) -> None:
    """Install the logging pipeline at boot; every record, a library's too, is scrubbed.
    `warn_unredacted` is False for a child, whose parent has already said it."""
    global _redact_personal
    _redact_personal = redact_personal

    level_name = level.upper()

    # Tracebacks become text before the scrubber, so the paths in them are redacted too; no locals.
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        _redaction_processor,
    ]

    structlog.configure(
        processors=[_fold, *shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [handler]

    if log_file is not None and max_bytes > 0:
        # A log file that cannot be opened is no reason to refuse to boot.
        from sift.kernel.log_settings import largest_file_bytes

        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            # Not rolled at the start's own size: that would roll a long log at its first line and
            # trim every older file to fit. The library's setting replaces this once it is read.
            rotating = _CappedRotatingFileHandler(
                log_file,
                maxBytes=max(max_bytes, largest_file_bytes(backups)),
                backupCount=backups,
                encoding="utf-8",
            )
            rotating.setFormatter(formatter)
            handlers.append(rotating)
        except OSError as exc:
            # Printed: the logger being set up cannot carry this.
            print(f"sift: could not open the log file {log_file}: {exc}", file=sys.stderr)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level_name)
    global _boot_level
    _boot_level = logging.getLevelNamesMapping().get(level_name, logging.INFO)
    _quiet_library_loggers()
    if not redact_personal and warn_unredacted:
        # Said at every boot: whoever pastes this log later may not know.
        get_logger(__name__).warning(
            "log.unredacted",
            detail=(
                "Logs now contain file paths and usernames. They are no longer safe to share, "
                "post in a bug report, or attach to a support request. Unset SIFT_LOG_UNREDACTED "
                "to turn this back off."
            ),
        )


def _quiet_library_loggers() -> None:
    # uvicorn's own handlers would write a second, unscrubbed copy of every line.
    for name in ("uvicorn", "uvicorn.error"):
        third_party = logging.getLogger(name)
        third_party.handlers = []
        third_party.propagate = True
    _pin_driver_logger()
    # uvicorn logs requests whenever `hasHandlers()` is true, whatever `access_log` says, and its
    # raw request target carries ids; no propagation is what makes that false.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """The logger: a named event with structured fields, never the payload."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


# --- Security events ---------------------------------------------------------------------

_security_log = None


def security_event(kind: str, **fields: Any) -> None:
    """Record that a security event happened, to whom and when, never what the content was."""
    global _security_log
    if _security_log is None:
        _security_log = get_logger("sift.security")
    _security_log.warning(f"security.{kind}", **fields)


def user_safe_message(error: Exception) -> str:
    """What an error may say to someone who is not an admin: error text leaks paths and URLs."""
    if isinstance(error, PermissionError):
        return "You do not have access to that."
    if isinstance(error, FileNotFoundError):
        return "That is no longer available."
    if isinstance(error, TimeoutError):
        return "That took too long. Try again."
    return "Something went wrong. If it keeps happening, ask the person running this Sift."


# --- Timing ------------------------------------------------------------------------------


class Timing:
    """What a running timed block tells its record: when its wait ended, and how much it moved."""

    __slots__ = ("_acquired", "_measured", "_started", "ran_ms")

    def __init__(self, started: float) -> None:
        self._started = started
        self._acquired: float | None = None
        self._measured: dict[str, Any] = {}
        #: The time the block ran, readable afterwards so a caller need not time it twice.
        self.ran_ms = 0.0

    def measured(self, **facts: Any) -> None:
        """Something the block learned about its work, merged into the record it will emit."""
        self._measured.update(facts)

    def acquired(self) -> None:
        """The wait is over; a second call is part of the work, so the first stands."""
        if self._acquired is None:
            self._acquired = time.perf_counter()

    def _split(self, ended: float) -> tuple[float, dict[str, float]]:
        """The total, and the two numbers that only exist when a wait was declared."""
        total_ms = round((ended - self._started) * 1000, 2)
        if self._acquired is None:
            self.ran_ms = total_ms
            return total_ms, {}
        waited_ms = round((self._acquired - self._started) * 1000, 2)
        self.ran_ms = round(total_ms - waited_ms, 2)
        return total_ms, {
            "waited_ms": waited_ms,
            "ran_ms": self.ran_ms,
        }


#: Where the record asks how backlogged the event loop is, in seconds. Filled in at boot.
_backlog_source: Callable[[], float] | None = None

#: Above this much loop backlog, a slow reading measures the queue, not the work.
BACKLOGGED_SECONDS = 0.25


def set_loop_backlog(source: Callable[[], float] | None) -> None:
    """Tell the records where to ask how backlogged the loop is. Called once, at boot."""
    global _backlog_source
    _backlog_source = source


#: Where a finished record is also handed for the "what is costing the time" report. Filled at boot.
_work_sink: Callable[[str, float], None] | None = None


def set_work_sink(sink: Callable[[str, float], None] | None) -> None:
    """Tell the records where to report what they measured. Called once, at boot."""
    global _work_sink
    _work_sink = sink


#: The ledger's reader of the same measurements, set and cleared at its own moments.
_stage_sink: Callable[[str, float], None] | None = None


def set_stage_sink(sink: Callable[[str, float], None] | None) -> None:
    """Tell the records where the ledger is, or that there is none."""
    global _stage_sink
    _stage_sink = sink


def _report_work(stage: str, milliseconds: float) -> None:
    # A diagnostic must never be the reason a request fails.
    if _work_sink is not None:
        with suppress(Exception):
            _work_sink(stage, milliseconds)
    if _stage_sink is not None:
        with suppress(Exception):
            _stage_sink(stage, milliseconds)


#: Rows one read may hand back before its record is escalated: a latency risk under load.
WIDE_READ_ROWS = 500

#: Where a slow or wide timing is raised to: in the ordinary log, never among the warnings.
SLOW_LEVEL = "info"

#: Where a finished record reports how many rows it moved. Filled at boot, like `_work_sink`.
_rows_sink: Callable[[str, int, str | None], None] | None = None


def set_rows_sink(sink: Callable[[str, int, str | None], None] | None) -> None:
    """Tell the records where to report how wide their reads were. Called once, at boot."""
    global _rows_sink
    _rows_sink = sink


def _report_rows(stage: str, rows: int, sql: str | None) -> None:
    if _rows_sink is None:
        return
    # A diagnostic must never be the reason a request fails.
    with suppress(Exception):
        _rows_sink(stage, rows, sql)


_TIMING_LOGGER = "sift.timing"


# --- What one job cost ----------------------------------------------------------------------------

#: Statement records inside a write block: the block's own wait and hold already count them.
_WRITE_STAGE = "db.write"
#: Statement records of reads, a reader's wait included, kept as one figure rather than stages.
_READ_STAGES = frozenset({"db.read", "db.sweep"})
#: The job's own record, which is the whole and not a part.
_JOB_STAGE = "job"
#: Spans kept before they are merged, so a long job's record stays small.
_KEPT_SPANS = 2048


def _union(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The same time with every overlap merged, in order."""
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


#: The figures of a job's summary that are times, beside its stages, each its own spans.
_WRITER_WAIT, _WRITER_HELD, _READ, _STORAGE_WAIT, _TOOL, _SCREENS_WAIT = (
    "writer_wait_ms",
    "writer_held_ms",
    "read_ms",
    "storage_wait_ms",
    "tool_ms",
    "screens_wait_ms",
)


class _Spent:
    """One figure's time: the sum of its parts, and the spans they took, merged as they grow."""

    __slots__ = ("closed", "spans", "summed")

    def __init__(self) -> None:
        self.spans: list[tuple[float, float]] = []
        self.summed = 0.0
        #: Seconds of gap closed to keep the spans few; taken off what the spans say.
        self.closed = 0.0

    def add(self, start: float, end: float) -> None:
        self.summed += end - start
        self.spans.append((start, end))
        if len(self.spans) > _KEPT_SPANS:
            self.spans, gap = _coarsened(_union(self.spans), _KEPT_SPANS // 2)
            self.closed += gap

    def within(self, began: float, ended: float) -> float:
        return max(0.0, _within(self.spans, began, ended) - self.closed)


def _coarsened(
    spans: list[tuple[float, float]], keep: int
) -> tuple[list[tuple[float, float]], float]:
    """At most `keep` merged spans, the narrowest gaps between neighbours closed, and the seconds
    those gaps held: a long job's record stays bounded and each add stays cheap."""
    extra = len(spans) - keep
    if extra <= 0:
        return spans, 0.0
    gaps = sorted(spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1))
    widest_closed = gaps[extra - 1]
    out = [spans[0]]
    closed = 0
    held = 0.0
    for start, end in spans[1:]:
        gap = start - out[-1][1]
        if closed < extra and gap <= widest_closed:
            out[-1] = (out[-1][0], max(out[-1][1], end))
            closed += 1
            held += gap
        else:
            out.append((start, end))
    return out, held


def _within(spans: list[tuple[float, float]], began: float, ended: float) -> float:
    """Seconds of `spans`, merged, between `began` and `ended`."""
    return sum(max(0.0, min(end, ended) - max(start, began)) for start, end in _union(spans))


class JobCost:
    """Where one job's time went: its stages, its waits for the writer and for storage, its tools.

    Times are `time.perf_counter` seconds. Spans nest (a write inside a stage) and a job's parts
    run together (a walk's take-ins), so each figure is the time its spans took, never more than
    the job's; where its parts overlapped, their sum is said beside it (`parts_ms`). Fed from
    threads too, hence the lock.
    """

    def __init__(self, began: float | None = None) -> None:
        self.began = time.perf_counter() if began is None else began
        self.stages: dict[str, _Spent] = {}
        self.timed: dict[str, _Spent] = {
            name: _Spent()
            for name in (_WRITER_WAIT, _WRITER_HELD, _READ, _STORAGE_WAIT, _TOOL, _SCREENS_WAIT)
        }
        self.writes = 0
        self.reads = 0
        self.launches = 0
        self.tool_read_bytes = 0
        #: The handler's own time, the job record's (`_JOB_STAGE`).
        self.ran_ms = 0.0
        self.folded: dict[str, int] = {}
        self._covered = _Spent()
        self._lock = threading.Lock()

    def staged(self, stage: str, start: float, end: float) -> None:
        """A timed block ended inside the job."""
        if stage == _JOB_STAGE:
            self.ran_ms = (end - start) * 1000
            return
        if stage == _WRITE_STAGE:
            return
        with self._lock:
            if stage in _READ_STAGES:
                self.reads += 1
                self.timed[_READ].add(start, end)
            else:
                self.stages.setdefault(stage, _Spent()).add(start, end)
            self._covered.add(start, end)

    def wrote(self, asked: float, got: float, ended: float) -> None:
        """A write block: asked for the writer, got it, let it go."""
        with self._lock:
            self.writes += 1
            self.timed[_WRITER_WAIT].add(asked, got)
            self.timed[_WRITER_HELD].add(got, ended)
            self._covered.add(asked, ended)

    def yielded(self, start: float, end: float) -> None:
        """A statement stood aside for a screen's (`foreground.screens_first`)."""
        with self._lock:
            self.timed[_SCREENS_WAIT].add(start, end)
            self._covered.add(start, end)

    def waited_for_storage(self, start: float, end: float) -> None:
        """A wait for a place in a storage's lane."""
        with self._lock:
            self.timed[_STORAGE_WAIT].add(start, end)
            self._covered.add(start, end)

    def launched(self, start: float, end: float, read_bytes: int) -> None:
        """A tool ran from `start` to `end` and read `read_bytes`."""
        with self._lock:
            self.launches += 1
            self.timed[_TOOL].add(start, end)
            self.tool_read_bytes += read_bytes
            self._covered.add(start, end)

    def counted(self, event: str) -> None:
        """A per-file line written inside the job (`COUNTED_PER_BATCH`)."""
        with self._lock:
            self.folded[event] = self.folded.get(event, 0) + 1

    def summary(self, ended: float | None = None) -> dict[str, Any]:
        """The fields of the job's one summary line, up to `ended`."""
        ended = time.perf_counter() if ended is None else ended
        parts: dict[str, int] = {}

        def took(name: str, spent: _Spent) -> int:
            union = round(spent.within(self.began, ended) * 1000)
            if round(spent.summed * 1000) > union + 1:
                parts[name] = round(spent.summed * 1000)
            return union

        with self._lock:
            covered_ms = self._covered.within(self.began, ended) * 1000
            timed = {name: took(name, spent) for name, spent in self.timed.items()}
            stages = {name: took(name, spent) for name, spent in sorted(self.stages.items())}
            folded = dict(sorted(self.folded.items()))
        wall_ms = (ended - self.began) * 1000
        return {
            "wall_ms": round(wall_ms),
            "ran_ms": round(self.ran_ms),
            "covered_ms": round(covered_ms),
            "covered_pct": round(100 * covered_ms / wall_ms, 1) if wall_ms > 0 else 100.0,
            "writer_wait_ms": timed[_WRITER_WAIT],
            "writer_held_ms": timed[_WRITER_HELD],
            "writes": self.writes,
            "read_ms": timed[_READ],
            "reads": self.reads,
            "storage_wait_ms": timed[_STORAGE_WAIT],
            "screens_wait_ms": timed[_SCREENS_WAIT],
            "launches": self.launches,
            "tool_ms": timed[_TOOL],
            "tool_read_bytes": self.tool_read_bytes,
            "stages": stages,
            "parts_ms": dict(sorted(parts.items())),
            "folded": folded,
            "loop_backlog_ms": _loop_backlog_ms(),
        }


_JOB_COST: ContextVar[JobCost | None] = ContextVar("sift_job_cost", default=None)


def job_cost() -> JobCost | None:
    """The cost of the job this code runs inside, or None outside a job."""
    return _JOB_COST.get()


@contextmanager
def costing(cost: JobCost | None) -> Iterator[JobCost | None]:
    """Run the block, and every task and thread it starts, as part of `cost` (None: of no job)."""
    token = _JOB_COST.set(cost)
    try:
        yield cost
    finally:
        _JOB_COST.reset(token)


def note_storage_wait(start: float, end: float) -> None:
    """A wait for a place in a storage's lane, `time.perf_counter` seconds, filed to its job."""
    cost = _JOB_COST.get()
    if cost is not None:
        cost.waited_for_storage(start, end)


#: The level names `timing_hook` accepts, so a record's survival is asked before it is built.
_LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _loop_backlog_ms() -> float | None:
    """How far behind the loop is right now, in milliseconds, or None when nothing is watching."""
    if _backlog_source is None:
        return None
    try:
        return round(_backlog_source() * 1000, 1)
    except Exception:
        return None


@contextmanager
def timing_hook(
    stage: str,
    *,
    level: str = "info",
    slow_ms: float | None = None,
    sql: str | None = None,
    **fields: Any,
) -> Iterator[Timing]:
    """Time a block, failures too, and emit a record; `slow_ms` judges the time it ran, not waited.

    `sql` goes only to the wide-read report, never into the record.
    """
    log = get_logger(_TIMING_LOGGER)
    started = time.perf_counter()
    timing = Timing(started)
    failed = False
    try:
        yield timing
    except BaseException:
        failed = True
        raise
    finally:
        ended = time.perf_counter()
        cost = _JOB_COST.get()
        if cost is not None:
            cost.staged(stage, started, ended)
        elapsed_ms, split = timing._split(ended)
        judged_ms = split.get("ran_ms", elapsed_ms)
        # The loop's backlog is a second wait hiding inside `ran_ms`.
        backlog_ms = _loop_backlog_ms()
        behind = backlog_ms is not None and backlog_ms > BACKLOGGED_SECONDS * 1000
        if backlog_ms is not None:
            split = {**split, "loop_backlog_ms": backlog_ms}
        chosen = level
        if (slow_ms is not None and not failed and judged_ms > slow_ms and not behind) or (
            failed and level == "debug"
        ):
            chosen = "warning" if failed else max(level, SLOW_LEVEL, key=_LOG_LEVELS.__getitem__)
        _report_work(stage, elapsed_ms)
        measured = timing._measured
        rows = measured.get("rows")
        if isinstance(rows, int):
            _report_rows(stage, rows, sql)
            # Escalated on the count: the clock only shows it once the machine is busy.
            if rows >= WIDE_READ_ROWS and not failed:
                chosen = max(chosen, SLOW_LEVEL, key=_LOG_LEVELS.__getitem__)
        # The bound logger runs the whole chain before dropping a record, so a discarded one costs
        # 0.04 ms; asked of `chosen` and of the standard library, which applies the level. A
        # condition, not a `return`, which would swallow the exception in a `finally` (B012).
        if logging.getLogger(_TIMING_LOGGER).isEnabledFor(_LOG_LEVELS[chosen]):
            getattr(log, chosen)(
                "timing",
                stage=stage,
                duration_ms=elapsed_ms,
                failed=failed,
                **split,
                **measured,
                **fields,
            )


def timed(stage: str) -> Callable[[F], F]:
    """Decorator form of `timing_hook`. Works on sync and async callables."""

    def decorate(fn: F) -> F:
        import inspect

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with timing_hook(stage):
                    return await fn(*args, **kwargs)

            return cast(F, async_wrapper)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with timing_hook(stage):
                return fn(*args, **kwargs)

        return cast(F, wrapper)

    return decorate
