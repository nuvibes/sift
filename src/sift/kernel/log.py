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
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
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


class JobCost:
    """Where one job's time went: its stages, its waits for the writer and for storage, its tools.

    Times are `time.perf_counter` seconds. Spans nest (a write inside a stage), so the time they
    account for is their union, never their sum. Fed from threads too, hence the lock.
    """

    def __init__(self, began: float | None = None) -> None:
        self.began = time.perf_counter() if began is None else began
        self.stages: dict[str, float] = {}
        self.writer_wait_ms = 0.0
        self.writer_held_ms = 0.0
        self.writes = 0
        self.read_ms = 0.0
        self.reads = 0
        self.storage_wait_ms = 0.0
        self.launches = 0
        self.tool_ms = 0.0
        self.tool_read_bytes = 0
        self._spans: list[tuple[float, float]] = []
        self._lock = threading.Lock()

    def _span(self, start: float, end: float) -> None:
        self._spans.append((start, end))
        if len(self._spans) > _KEPT_SPANS:
            self._spans = _union(self._spans)

    def staged(self, stage: str, start: float, end: float) -> None:
        """A timed block ended inside the job."""
        if stage in (_WRITE_STAGE, _JOB_STAGE):
            return
        spent = (end - start) * 1000
        with self._lock:
            if stage in _READ_STAGES:
                self.reads += 1
                self.read_ms += spent
            else:
                self.stages[stage] = self.stages.get(stage, 0.0) + spent
            self._span(start, end)

    def wrote(self, asked: float, got: float, ended: float) -> None:
        """A write block: asked for the writer, got it, let it go."""
        with self._lock:
            self.writes += 1
            self.writer_wait_ms += (got - asked) * 1000
            self.writer_held_ms += (ended - got) * 1000
            self._span(asked, ended)

    def waited_for_storage(self, start: float, end: float) -> None:
        """A wait for a place in a storage's lane."""
        with self._lock:
            self.storage_wait_ms += (end - start) * 1000
            self._span(start, end)

    def launched(self, start: float, end: float, read_bytes: int) -> None:
        """A tool ran from `start` to `end` and read `read_bytes`."""
        with self._lock:
            self.launches += 1
            self.tool_ms += (end - start) * 1000
            self.tool_read_bytes += read_bytes
            self._span(start, end)

    def summary(self, ended: float | None = None) -> dict[str, Any]:
        """The fields of the job's one summary line, up to `ended`."""
        ended = time.perf_counter() if ended is None else ended
        with self._lock:
            spans = _union(self._spans)
            stages = {name: round(spent) for name, spent in sorted(self.stages.items())}
        wall_ms = (ended - self.began) * 1000
        covered_ms = (
            sum(max(0.0, min(end, ended) - max(start, self.began)) for start, end in spans) * 1000
        )
        return {
            "wall_ms": round(wall_ms),
            "covered_ms": round(covered_ms),
            "covered_pct": round(100 * covered_ms / wall_ms, 1) if wall_ms > 0 else 100.0,
            "writer_wait_ms": round(self.writer_wait_ms),
            "writer_held_ms": round(self.writer_held_ms),
            "writes": self.writes,
            "read_ms": round(self.read_ms),
            "reads": self.reads,
            "storage_wait_ms": round(self.storage_wait_ms),
            "launches": self.launches,
            "tool_ms": round(self.tool_ms),
            "tool_read_bytes": self.tool_read_bytes,
            "stages": stages,
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
