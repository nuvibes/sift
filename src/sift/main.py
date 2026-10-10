# SPDX-License-Identifier: AGPL-3.0-or-later
"""The application, the rules every route inherits, and the process that serves it."""

from __future__ import annotations

# First, before any native library loads: a crash in one then leaves every thread's stack.
from sift.kernel import crash_record  # noqa: F401

# isort: split

import json
import os

# Only for `sqlite3.Error`; nothing here opens a connection outside the kernel.
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Registers the component: a library records a "benchmarks" version.
import sift.kernel.benchmarks  # noqa: F401
from sift import client
from sift.kernel import foreground, lifecycle
from sift.kernel.config import ConfigError, Settings, ensure_directories, get_settings
from sift.kernel.db import (
    DatabaseError,
    adopt_database,
    check_sqlite_capabilities,
    copy_database_aside,
    inspect_database,
    library_database,
)
from sift.kernel.http import is_https
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobSwitchedOff
from sift.kernel.log import LOG_FILENAME, configure_logging, get_logger, tell_the_shell
from sift.kernel.version import app_version
from sift.kernel.wire import Refused

# Imported for its side effect: it declares the look-and-feel preferences, gone without it.
from sift.slices import theming  # noqa: F401
from sift.slices.backup import COPIED_NOTE, leave_origin_note, make_its_own_library
from sift.wiring.lifespan import lifespan
from sift.wiring.routes import build_routes
from sift.wiring.tasks import retire_the_switches_into_whens

log = get_logger(__name__)


retire_the_switches_into_whens()


# script-src names the inline bootstrap by hash, so an injected script never runs. style-src allows
# inline styles because the framework's screen-reader announcer writes a style attribute, and
# img-src leaves injected CSS nowhere to send data.
def content_security_policy(script_hashes: tuple[str, ...] = ()) -> str:
    """The policy, given the hashes of the client's own inline scripts."""
    inline = "".join(f" '{h}'" for h in script_hashes)
    return (
        "default-src 'self'; "
        f"script-src 'self'{inline}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "worker-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )


# The content policy is not here: a rebuilt client changes the hash it names.
STATIC_SECURITY_HEADERS: dict[str, str] = {
    "x-content-type-options": "nosniff",
    # A self-hoster's address must not leak to the sites a link leads to.
    "referrer-policy": "no-referrer",
    # The desktop shell trusts an address only when this answers, sign-in refusals included.
    "x-sift": "1",
}


def security_headers() -> dict[str, str]:
    """Every security header this response gets, the policy read per response from the page."""
    return {
        "content-security-policy": content_security_policy(client.script_hashes()),
        **STATIC_SECURITY_HEADERS,
    }


# Sent only over HTTPS; no preload, a commitment no self-hoster should have made for them.
HSTS_HEADER_NAME = "strict-transport-security"
HSTS_HEADER_VALUE = "max-age=63072000; includeSubDomains"

# FastAPI buffers a body before validating it, so an unbounded one could exhaust memory.
MAX_REQUEST_BODY_BYTES = 1024 * 1024


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Report which field was wrong without echoing it back: a rejected password would leak."""
        errors = exc.errors()
        for error in errors:
            said = error.get("ctx", {}).get("error")
            if isinstance(said, Refused):
                where = error.get("loc", ())
                field = str(where[1]) if len(where) > 1 and where[0] == "body" else None
                return JSONResponse(
                    status_code=422,
                    content={"detail": str(said)},
                    headers={"Sift-Field": field} if field else None,
                )
        cleaned = [
            {key: value for key, value in error.items() if key != "input"} for error in errors
        ]
        return JSONResponse(status_code=422, content=jsonable_encoder({"detail": cleaned}))

    @app.exception_handler(JobSwitchedOff)
    async def _switched_off(request: Request, exc: JobSwitchedOff) -> JSONResponse:
        """Work that is switched off: 409, not 403, since the way through is a setting."""
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """A bug's 500 escapes the header middleware, so the headers are reattached here."""
        correlation_id = new_id()
        log.exception(
            "http.error",
            method=request.method,
            route=getattr(request.scope.get("route"), "path", "unmatched"),
            correlation_id=correlation_id,
        )
        response = JSONResponse(status_code=500, content={"detail": "Something went wrong."})
        for name, value in security_headers().items():
            response.headers[name] = value
        if is_https(request.url.scheme, request.headers.get("x-forwarded-proto", "")):
            response.headers[HSTS_HEADER_NAME] = HSTS_HEADER_VALUE
        response.headers["x-correlation-id"] = correlation_id
        return response


def _add_middleware(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def limit_request_body(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Refuse an oversized non-upload body before it is read; innermost, so still logged."""
        content_type = request.headers.get("content-type", "")
        declared = request.headers.get("content-length")
        if (
            not content_type.startswith("multipart/form-data")
            and declared is not None
            and declared.isdigit()
            and int(declared) > MAX_REQUEST_BODY_BYTES
        ):
            return JSONResponse(status_code=413, content={"detail": "Request body too large."})
        return await call_next(request)

    @app.middleware("http")
    async def time_every_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Time every request by its route template, never the path, which carries ids."""
        correlation_id = new_id()
        started = time.perf_counter()

        foreground.request_began()
        try:
            response = await call_next(request)
        finally:
            foreground.request_ended()

        route = request.scope.get("route")
        log.info(
            "http.request",
            method=request.method,
            route=getattr(route, "path", "unmatched"),
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            correlation_id=correlation_id,
        )
        response.headers["x-correlation-id"] = correlation_id
        return response

    @app.middleware("http")
    async def set_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach the security headers to every response, HSTS only over HTTPS."""
        response = await call_next(request)
        for name, value in security_headers().items():
            response.headers[name] = value
        if is_https(request.url.scheme, request.headers.get("x-forwarded-proto", "")):
            response.headers[HSTS_HEADER_NAME] = HSTS_HEADER_VALUE
        return response

    # An allowlist, empty by default; the setting refuses "*", which with credentials hands out
    # a logged-in session.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def create_app() -> FastAPI:
    settings = get_settings()

    # Off by default, and then not mounted at all: they list every endpoint to anyone.
    docs_kwargs: dict[str, str | None] = (
        {} if settings.enable_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    )

    app = FastAPI(
        title="Sift",
        description=(
            "Sift is a feature-rich, intelligent media manager that lets you browse, download, "
            "watch, and organize your content."
        ),
        lifespan=lifespan,
        **docs_kwargs,  # type: ignore[arg-type]
    )

    _add_error_handlers(app)
    _add_middleware(app, settings)
    build_routes(app)

    return app


app = create_app()


#: Questions the library switcher asks without starting anything. Flags on this module, because
#: the schema registry is complete only once every slice is imported, which this module does.
INSPECT_LIBRARY = "--inspect-library"
BACK_UP_LIBRARY = "--back-up-library"
ADOPT_LIBRARY = "--adopt-library"


def answer_about_a_library(argv: Sequence[str], say: Callable[[str], None]) -> int | None:
    """Answer one question as JSON, exit 0 even for a refused library; None if not asked."""
    if len(argv) < 1 or argv[0] not in (INSPECT_LIBRARY, BACK_UP_LIBRARY, ADOPT_LIBRARY):
        return None
    if len(argv) < 2 or not argv[1]:
        say(json.dumps({"error": f"{argv[0]} needs the path of a library folder or database"}))
        return 2
    if argv[0] == ADOPT_LIBRARY and (len(argv) < 3 or not argv[2]):
        say(json.dumps({"error": f"{argv[0]} needs a database file and a folder to put it in"}))
        return 2

    target = Path(argv[1])
    if argv[0] == INSPECT_LIBRARY:
        say(json.dumps(inspect_database(library_database(target)).as_dict()))
        return 0

    try:
        if argv[0] == ADOPT_LIBRARY:
            made = adopt_database(target, Path(argv[2]))
            # A copy that could not be settled is taken back, so it is never opened as it stands.
            try:
                make_its_own_library(made, now=int(time.time()), note=COPIED_NOTE)
                leave_origin_note(made.parent, Path(argv[2]).name)
            except BaseException:
                # nosemgrep: sift-no-file-removal-outside-delete-trash (the copy this call just made and could not settle; never a library anybody opened)
                made.unlink(missing_ok=True)
                raise
            say(json.dumps({"ok": True, "database": str(made)}))
            return 0
        snapshot = copy_database_aside(library_database(target))
    except (OSError, sqlite3.Error) as refused:
        # Said, not raised: the shell reads stdout, and a traceback reaches it as an empty answer.
        say(json.dumps({"ok": False, "detail": str(refused)}))
        return 0
    say(json.dumps({"ok": True, "copy": str(snapshot)}))
    return 0


#: The shell loads the window on this line instead of polling.
READY_LINE = "sift.listening"


#: A name rather than the test inline, so the type checker keeps both branches.
_WINDOWS = sys.platform == "win32"


def since_the_process_began_ms() -> int | None:
    """Wall time since this process was created, or None where the operating system will not say."""
    if _WINDOWS:  # pragma: no cover (the other operating system's branch)
        import ctypes
        from ctypes import wintypes

        times = [wintypes.FILETIME() for _ in range(4)]
        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined, unused-ignore]
        # Declared, so the process handle is passed whole on a 64-bit machine.
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        if not kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(), *(ctypes.byref(one) for one in times)
        ):
            return None
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return round((time.time() - (ticks / 10_000_000 - 11_644_473_600)) * 1000)
    try:
        fields = Path("/proc/self/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
        began = int(fields[19]) / os.sysconf("SC_CLK_TCK")  # type: ignore[attr-defined, unused-ignore]
        uptime = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return int((uptime - began) * 1000)


def say_when_listening(server: Any, say: Callable[[str], None]) -> None:
    """Say `READY_LINE` once uvicorn's socket is listening, which is after the lifespan's start."""
    startup = server.startup

    async def startup_then_say(sockets: Any = None) -> None:
        await startup(sockets=sockets)
        if server.started:
            say(READY_LINE)

    server.startup = startup_then_say


def _settings_or_a_sentence() -> Settings:
    """Settings, directories and SQLite checked before the server, so an error is one plain line."""
    try:
        settings = get_settings()
        ensure_directories(settings)
        # Quiet: logging is not configured yet.
        check_sqlite_capabilities(announce=False)
    except (ConfigError, DatabaseError) as exc:
        if os.environ.get("SIFT_LOG_LEVEL", "").upper() == "DEBUG":
            detail = "\n" + "".join(traceback.format_exception(exc))
        else:
            detail = "\nSet SIFT_LOG_LEVEL=DEBUG for the full technical detail.\n"
        raise SystemExit(f"\n{exc}\n{detail}") from None
    return settings


def main() -> None:
    # Before the settings: a question names its own path and must not need SIFT_DATA_DIR.
    answered = answer_about_a_library(sys.argv[1:], print)
    if answered is not None:
        raise SystemExit(answered)

    import uvicorn

    settings = _settings_or_a_sentence()
    # Before uvicorn, so its own startup lines are scrubbed too.
    configure_logging(
        settings.log_level,
        redact_personal=not settings.log_unredacted,
        log_file=settings.data_dir / LOG_FILENAME,
        max_bytes=settings.log_max_bytes,
        backups=settings.log_backups,
    )
    log.info(
        "boot.imported",
        version=app_version(),
        since_process_ms=since_the_process_began_ms(),
        modules=len(sys.modules),
    )

    # The app object, not "sift.main:app", which would import this module a second time.
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        # A second, unredacted copy of the request log.
        access_log=False,
        # Keeps uvicorn from replacing the redacting handlers with its own.
        log_config=None,
    )
    server = uvicorn.Server(config)
    if settings.stop_on_stdin_eof:
        stop_when_the_parent_lets_go(server)
        # A restart is honest only where a parent holds this process and will start it again.
        lifecycle.stops_with(lambda: setattr(server, "should_exit", True))
        say_when_listening(server, tell_the_shell)
    server.run()
    if lifecycle.was_asked_to_restart():
        raise SystemExit(lifecycle.RESTART_EXIT_CODE)


def stop_when_the_parent_lets_go(server: object) -> None:
    """Shut down cleanly when the parent lets go of stdin; on Windows, Node can only kill."""
    # The raw descriptor: a buffered reader's lock held at shutdown aborts the interpreter.
    try:
        descriptor = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return

    def watch() -> None:
        try:
            while os.read(descriptor, 1):
                pass
        except (OSError, ValueError):
            pass
        # What Ctrl-C sets: the server finishes, runs the shutdown, and returns from `run`.
        server.should_exit = True  # type: ignore[attr-defined]

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


# Last: a function defined below this would not exist while the server is up.
if __name__ == "__main__":
    main()
