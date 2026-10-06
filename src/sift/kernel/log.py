# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structured logging and timing; redaction is `kernel/redaction.py`. The only module that touches
the standard library's logging primitives. Lines written before the library opens hide names."""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager, suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TypeVar, cast

import structlog

from sift.kernel import redaction
from sift.kernel.redaction import REDACTED as REDACTED
from sift.kernel.redaction import hide_identity as hide_identity
from sift.kernel.redaction import hide_identity_in_path as hide_identity_in_path

F = TypeVar("F", bound=Callable[..., Any])

#: What the log file is called, inside whatever directory it is put in.
#:
#: Named here once, because `main.py` and `wiring/lifespan.py` (the two places logging is
#: configured) and the route that reads it back must agree on it: three copies of a filename is two
#: chances for a reader to look in the wrong place.
LOG_FILENAME = "sift.log"


# Set at boot, and then by the library's `logs.hide_personal` (`apply_log_preferences`). Secrets
# stay hidden regardless.
_redact_personal = True


def redacts_personal() -> bool:
    """Whether personal detail is being hidden, so a process started by this one can be set the
    same way."""
    return _redact_personal


def level_name() -> str:
    """The level the pipeline is at, by name, for the same reason."""
    return logging.getLevelName(logging.getLogger().level)


def path_facts(path: str | Path) -> dict[str, object]:
    """The things about a file you cannot tell by looking at its path.

    The path itself is in the log (with the names removed), so extension and depth are already
    visible and are not repeated here. What is not visible is whether the file is actually there,
    whether Sift can read it, and how big it is, and that is usually the whole question.

    `path_readable=False` with `path_exists=True` is the one that saves an afternoon: the file is
    present but the process cannot open it, which is a permissions or ownership problem, not a
    missing file.
    """
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
    """A stable, opaque handle for a sensitive value.

    Two log lines about the same URL carry the same handle and can be correlated; neither
    reveals the URL. Truncated, because the goal is correlation within one log file, not
    cryptographic commitment, and a full digest is just noise to read past.
    """
    digest = hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()
    return digest[:12]


def redact(value: Any, _key: str = "", *, always_personal: bool = False) -> Any:
    """`redaction.redact` at the setting, or always hiding names for text that leaves the machine."""
    return redaction.redact(value, _key, personal=_redact_personal or always_personal)


def _redaction_processor(
    _logger: object, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Applied to every event, so nothing reaches a handler unscrubbed."""
    return {k: redact(v, str(k)) for k, v in event_dict.items()}


#: The level the process was STARTED at, which is what `SIFT_LOG_LEVEL` says.
#:
#: A module-level record rather than a re-read of the environment, because the environment is not
#: the only caller: the tests configure a level directly, and a re-read would answer about a
#: variable that had nothing to do with the logger actually running. INFO until something says
#: otherwise, which is what `configure_logging` defaults to.
_boot_level = logging.INFO


def tell_the_shell(line: str) -> None:
    """One fixed word to the process that started this one, on its stdout, which the desktop shell
    reads for the moment the socket is open. No payload, so nothing to redact."""
    print(line, flush=True)


def _pin_driver_logger() -> None:
    """The database driver's debug lines print every statement with its bound values (a file's
    name, a search's words), so they never reach the file, whatever the detail switch says."""
    logging.getLogger("aiosqlite").setLevel(logging.INFO)


def apply_log_preferences(
    *, detailed: bool, per_file_bytes: int, hide_personal: bool | None = None
) -> None:
    """Move the running logger onto what the settings say, without a restart.

    ## Why this is not simply `configure_logging` again

    Because logging is set up before there is a database to ask. The environment decides what the
    boot lines are written with (there is nowhere else the answer could come from at that moment)
    and the stored preference takes over once the library is open. Tearing the handlers down and
    rebuilding them would close and reopen the file mid-run and lose whatever a rotation was part
    way through; the two things somebody is actually choosing are a level and a cap, and both are
    attributes of objects that are already there.

    A file handler that was never attached (no log file, or a boot value of zero) stays absent.
    Making one here would put a file on disk somebody never asked for, from a screen about how
    large it may get.

    PER FILE, not the total: the setting is what the whole log may take, and `log_settings` divides
    it by the number of files that will exist. The handler has only ever understood one file.

    `hide_personal` is the library's `Hide personal details in the log`, taken from the next line
    written. None leaves the answer as it is.
    """
    global _redact_personal
    if hide_personal is not None:
        _redact_personal = hide_personal
    root = logging.getLogger()
    # NEVER LOUDER THAN THE MACHINE WAS TOLD TO BE.
    #
    # Not `logging.INFO` flat, which would make `SIFT_LOG_LEVEL` a setting that holds until the
    # database opens and is then thrown away: a server started at WARNING would go back to INFO a
    # second later, with nothing said.
    #
    # The switch stays what it says it is. `Detailed` is a request for MORE, so it still takes the
    # level down to DEBUG; turning it off returns to whatever the environment asked for, and where
    # that is louder than INFO (a boot at DEBUG) that level is kept exactly.
    root.setLevel(logging.DEBUG if detailed else max(_boot_level, logging.INFO))
    _pin_driver_logger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler):
            # `maxBytes` is read on every emit, so this takes effect on the next line written.
            lowered = per_file_bytes < handler.maxBytes
            handler.maxBytes = per_file_bytes
            # A lowered size trims the OLDER files at once: the handler only ever looks at the one
            # it writes, so a 27 MB rotated file would have kept the total over the setting until
            # five more rotations pushed it out.
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
    """A rotating handler whose files, taken together, never exceed the setting.

    The standard handler rotates by the size of the one file it writes and keeps `backupCount`
    older ones whatever their size, so a setting lowered while big rotated files exist is exceeded
    for as long as they last. Every rollover here ends by trimming the oldest files until the whole
    set fits in `maxBytes * (backupCount + 1)`, the total the setting stands for.
    """

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
) -> None:
    """Install the logging pipeline. Called once, at boot.

    EVERY log record goes through the scrubber, including records from libraries that know
    nothing about it. That is not belt-and-braces, it is the requirement: uvicorn's access log
    prints the raw request line, and a Sift URL can carry a library path. Redacting only Sift's
    own logger while the web server writes the same data to the same file, one line below, would
    be pure theatre.

    `redact_personal=False` reveals paths and usernames: an admin's own filesystem, which was
    never hidden from them anywhere else. Secrets stay hidden either way.

    A `log_file` gets the same lines as the stream, through the same formatter, so the two cannot
    disagree about what happened or about what is scrubbed out of it. It exists because the stream
    goes to the container's output and dies with the container: an image rebuilt after a problem
    takes the record of that problem with it. A file under the data directory survives that, and it
    is capped and rotated so it can never be the reason a disk fills up.
    """
    global _redact_personal
    _redact_personal = redact_personal

    level_name = level.upper()

    # Runs on Sift's events and on records forwarded from the standard library alike.
    #
    # `format_exc_info` renders an exception to a traceback *string* under an `exception` key,
    # and it sits BEFORE the scrubber on purpose: a traceback names the path that caused the
    # failure, and left as an `exc_info` tuple it would be turned into text later, by the
    # formatter, downstream of the one processor that redacts. Stringifying it here puts it in
    # front of the scrubber like every other field. `format_exc_info`, not `dict_tracebacks`,
    # which would also capture each frame's local variables, and a local is exactly where a
    # password or an unwrapped key would be sitting when the thing blew up. A traceback string is
    # everything an operator needs and nothing a redactor cannot reduce.
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
        # `foreign_pre_chain` is what puts third-party records through the scrubber.
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
        # A log that cannot be written is not a reason to refuse to boot. A read-only data
        # directory, a full disk and a permission the image does not have all end up here, and in
        # every one of them the right answer is to carry on with the stream and say so once.
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            rotating = _CappedRotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
            rotating.setFormatter(formatter)
            handlers.append(rotating)
        except OSError as exc:
            # Printed rather than logged: the logger is what is being set up here, and a handler
            # that could not be attached cannot carry the news that it could not be attached.
            print(f"sift: could not open the log file {log_file}: {exc}", file=sys.stderr)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level_name)
    # Kept so the stored preference cannot make the log LOUDER than this. See
    # `apply_log_preferences`, which reads it.
    global _boot_level
    _boot_level = logging.getLevelNamesMapping().get(level_name, logging.INFO)

    # uvicorn installs its own handlers on these; leaving them attached would emit a second,
    # unscrubbed copy of every line straight past the formatter above.
    for name in ("uvicorn", "uvicorn.error"):
        third_party = logging.getLogger(name)
        third_party.handlers = []
        third_party.propagate = True
    _pin_driver_logger()
    # The access log is silenced HERE, and `access_log=False` at the call to uvicorn is not what
    # does it.
    #
    # **uvicorn IGNORES that flag.** It decides whether to log a request with
    # `self.access_log = self.access_logger.hasHandlers()` (the flag is never read on this path)
    # and `hasHandlers()` walks up the tree. So the two lines above, applied to `uvicorn.access`,
    # would RE-ENABLE it: the handlers cleared, propagation left on, the logger reaches the root
    # handlers installed a few lines up, and uvicorn logs every request after all.
    #
    # Why that matters: Sift's own middleware records each request with the route TEMPLATE
    # (`/assets/{asset_id}/thumb`), precisely so an id never lands in a file somebody pastes into a
    # bug report. uvicorn's line is the raw request target (`/api/assets/<the id>/thumb`), which
    # beside it would defeat the redaction rather than duplicate it.
    #
    # `propagate = False` rather than a level or a filter: it is the one setting that makes
    # `hasHandlers()` false, which is the actual question being asked.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

    if not redact_personal:
        # Said plainly and at every boot. The person who turned this on six months ago is not
        # the person pasting a log into an issue today, even when they are.
        get_logger(__name__).warning(
            "log.unredacted",
            detail=(
                "Logs now contain file paths and usernames. They are no longer safe to share, "
                "post in a bug report, or attach to a support request. Unset SIFT_LOG_UNREDACTED "
                "to turn this back off."
            ),
        )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """The logger. Events are named, with structured fields:

        log.info("download.started", asset_id=asset_id, host=hashed(url))

    Log the event, not the payload.
    """
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


# --- Security events ---------------------------------------------------------------------

_security_log = None


def security_event(kind: str, **fields: Any) -> None:
    """Record something a security investigation would need: failed logins, lockouts, access
    denials, quarantined uploads, rejected outbound requests.

    Kept to one shape so the whole class is greppable and no slice invents its own. Redaction
    and auditability are not in tension here: the answer to both is to log *that* it happened,
    who to, and when, and never *what* the content was. An access denial is worth recording; the
    path they were denied is not, and recording it would put the very thing they could not see
    into a file that is easier to leak than the database.
    """
    global _security_log
    if _security_log is None:
        _security_log = get_logger("sift.security")
    _security_log.warning(f"security.{kind}", **fields)


def user_safe_message(error: Exception) -> str:
    """What an error is allowed to say to someone who is not an admin.

    Error text leaks: a stack trace names paths, a database error quotes the query, a downloader
    error quotes the URL. Guests get a generic sentence and the detail goes to the log, where
    only the operator reads it.
    """
    if isinstance(error, PermissionError):
        return "You do not have access to that."
    if isinstance(error, FileNotFoundError):
        return "That is no longer available."
    if isinstance(error, TimeoutError):
        return "That took too long. Try again."
    return "Something went wrong. If it keeps happening, ask the person running this Sift."


# --- Timing ------------------------------------------------------------------------------


class Timing:
    """What a timed block can tell its own record, while it is still running.

    A block that has to WAIT for something before it can start (a connection out of a pool, a
    lock) spends that wait inside the timed region, so the record would blame the work for time
    the work never had: an eighteen-second queue for a database connection logged as an
    eighteen-second query of a statement that takes milliseconds on its own.

    So a block that queues says when its wait ended, and the record then carries three numbers that
    cannot be confused with one another: how long it waited, how long it ran, and the total.

    It can also report **how much it moved**, which is the other thing a duration alone cannot say.
    A read that hands back three thousand rows and one that hands back one look identical in a
    timing record and are not remotely the same piece of work: a row leaving SQLite for Python costs
    a turn of the interpreter, so the row count is what decides whether a query survives the machine
    being busy: the same scan returning thousands of rows can cost milliseconds on an idle machine
    and over a minute beside two busy threads, while COUNTing it stays cheap.
    """

    __slots__ = ("_acquired", "_measured", "_started", "ran_ms")

    def __init__(self, started: float) -> None:
        self._started = started
        self._acquired: float | None = None
        self._measured: dict[str, Any] = {}
        #: What the block was judged on, once it has ended: the time it RAN, which is the total
        #: where nothing declared a wait. Readable after the block, and it is there so a caller
        #: can learn what its own work usually costs without timing it a second time: two clock
        #: reads are cheap, but a second measurement of the same block is a second number that can
        #: disagree with the one in the record.
        self.ran_ms = 0.0

    def measured(self, **facts: Any) -> None:
        """Something the block learned about its own work, for the record it will emit.

        Merged rather than replaced, so two calls both land and neither has to know about the other.
        """
        self._measured.update(facts)

    def acquired(self) -> None:
        """Whatever this block was waiting for, it has it now.

        Called once, from inside the block, the moment the wait is over. Called twice, the first
        answer stands: the wait ended when it ended, and a second call is a nested acquisition that
        is part of the work rather than part of the queue.
        """
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
#:
#: Empty by default so nothing depends on it having been wired: with no source the records carry
#: no backlog reading.
_backlog_source: Callable[[], float] | None = None

#: Above this much backlog, a slow reading is not evidence about the work.
#:
#: When the loop's ready queue takes this long to drain, everything waiting on it is late by about
#: that much, including the moment a finished query's result is handed back. The reading is then
#: mostly a measure of the queue, and escalating it names the wrong component.
#:
#: A lookup costing a fraction of a millisecond can be recorded at seconds while the queue drains,
#: and read as a slow query; tuning it would achieve nothing at all.
BACKLOGGED_SECONDS = 0.25


def set_loop_backlog(source: Callable[[], float] | None) -> None:
    """Tell the records where to ask how backlogged the loop is. Called once, at boot."""
    global _backlog_source
    _backlog_source = source


#: Where a finished record is also handed for the "what is costing the time" report. Filled at boot.
#:
#: A seam rather than an import: the record keeps no state of its own and the thing that does lives
#: with the other watches, which already import this module.
_work_sink: Callable[[str, float], None] | None = None


def set_work_sink(sink: Callable[[str, float], None] | None) -> None:
    """Tell the records where to report what they measured. Called once, at boot."""
    global _work_sink
    _work_sink = sink


#: A second reader of the same measurements, beside the one above: the ledger, which files each
#: stage's time against the run it belongs to. Two names rather than one list because the two
#: are set at different moments of the boot and cleared at different moments of the shutdown.
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


#: How many rows one read may hand back before the record is escalated to a warning.
#:
#: Not a page size and not a limit on what the application may do: it is the line above which a
#: read stops being a read and becomes a latency risk that only shows up under load. A page of the
#: grid is 200; the sweeps that legitimately walk the library go through the lane and are counted
#: there too, which is the point: if something is above this on a request path, it wants finding.
WIDE_READ_ROWS = 500

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


#: The standard-library number behind each level name this module uses, so the hook can ask whether
#: a record would survive before it pays to build one. A dict rather than `getattr(logging, name)`:
#: the names here are the ones `timing_hook` accepts, and a typo should be a KeyError in a test
#: rather than an AttributeError in production at the moment something slow finally happens.
#: The logger every timing record is written to. Named once so the hook's level check and the hook's
#: writer can never end up asking about two different loggers.
_TIMING_LOGGER = "sift.timing"

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
        # A diagnostic must never be the reason a request fails.
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
    """Time a block and emit a structured record.

    Instrumentation goes in from the start, not when something turns out to be slow. Retrofitting
    it means editing every slice at exactly the moment the answer is needed, and the code that
    matters is always the code nobody instrumented.

    Recorded on failure too: the duration before something gave up is usually the interesting
    number.

    `level` is the level a normal completion is logged at; it defaults to info. A caller that
    fires many times a request (the per-statement
    database timings) passes `level="debug"` so it is off by default and one
    `SIFT_LOG_LEVEL=DEBUG` away when a query needs chasing, rather than a wall of SQL in the
    ordinary log. `slow_ms` escalates a run past that many milliseconds to warning regardless, so a
    genuinely slow one still shows even when the stage is quiet; and a run that *failed* is
    escalated the same way when the stage was demoted, because a failed query hidden at debug is a
    failure nobody sees.

    **A block that queues should say so**. See `Timing.acquired`. `slow_ms` is then measured
    against the time it RAN rather than the time it took, so a fast statement that waited a long
    while for a connection is never escalated as a slow statement. What that wait means is a
    question about the pool, and the pool has its own watch; answering it here would blame
    queries that measure single-digit milliseconds.

    **`sql` is named rather than left among the fields, and it never reaches the record.** It is
    what the wide-read report groups by; written into every escalated line it would fill the log
    with thousands of warnings each carrying forty lines of SQL. A statement is identified in the
    log by its NAME, which the database layer passes beside it, and the text goes only to the
    report on the health screen where somebody has asked to see it.
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
        elapsed_ms, split = timing._split(time.perf_counter())
        # What `slow_ms` judges: the work, whenever the work can be told apart from the wait.
        judged_ms = split.get("ran_ms", elapsed_ms)
        # And how far behind the loop was, because that is the second wait hiding inside `ran_ms`.
        # A finished statement's result is handed back through the loop like everything else, so a
        # drained-queue time of seconds lands in the reading and reads as a slow statement.
        backlog_ms = _loop_backlog_ms()
        behind = backlog_ms is not None and backlog_ms > BACKLOGGED_SECONDS * 1000
        if backlog_ms is not None:
            split = {**split, "loop_backlog_ms": backlog_ms}
        chosen = level
        if (slow_ms is not None and not failed and judged_ms > slow_ms and not behind) or (
            failed and level == "debug"
        ):
            chosen = "warning"
        _report_work(stage, elapsed_ms)
        measured = timing._measured
        rows = measured.get("rows")
        if isinstance(rows, int):
            _report_rows(stage, rows, sql)
            # A read wide enough to be a latency problem whatever it costs today. Escalated on the
            # count rather than on the clock, because the clock only says so once the machine is
            # busy, by which time it is somebody reporting the application is unusable, not a log
            # line. See `WIDE_READ_ROWS`.
            if rows >= WIDE_READ_ROWS and not failed:
                chosen = "warning"
        # Nothing above this line may be skipped: the health readings and the escalation to warning
        # are what this exists for, and they are the same whether or not anybody is reading the log.
        # Everything BELOW it is the record itself, and building one that is about to be discarded
        # is the most-repeated waste in the application.
        #
        # **Sift's logger is not a filtering one.** `configure_logging` sets
        # `wrapper_class=structlog.stdlib.BoundLogger`, which runs the whole processor chain,
        # including the redaction scrubber, and only then hands a record to the standard library,
        # which drops it for being below the level. So a `debug` call at INFO is not free and is not
        # nearly free: about **0.04 ms**, against **0.0001 ms** for the two clock reads that are the
        # actual measurement. Every database statement carries one, so a scan making a hundred
        # thousand reads would pay four seconds for records nobody would ever see.
        #
        # Asked of `chosen` rather than of `level`, and that ordering is load-bearing: a statement
        # escalated to warning by `slow_ms` or by `WIDE_READ_ROWS` must still be written even though
        # its ordinary level is off. Guarding on `level` would silence exactly the lines worth
        # having.
        #
        # Asked of the STANDARD LIBRARY logger rather than of the structlog one, and that is not a
        # detail either. structlog hands the record to the standard library, which is what actually
        # applies the level, so this asks the thing that decides. The bound logger cannot be
        # asked: before `configure_logging` runs, structlog's default wrapper is a filtering one
        # with no `isEnabledFor` at all, so calling it there is an AttributeError rather than a
        # false, which a test run as a subset would meet, where a whole-file run configures
        # logging in an earlier test and leaves it configured.
        #
        # Written as a condition and never as an early `return`. This is a `finally` block, and a
        # `return` in one DISCARDS the exception on its way out, so the version of this that reads
        # more nicely would silently swallow every database error the hook is wrapped around (Ruff
        # B012).
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
