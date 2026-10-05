# SPDX-License-Identifier: AGPL-3.0-or-later
"""The application and the process that serves it.

`create_app` is the HTTP face: the rules every route inherits by being mounted (the security
headers, the body limit, moved addresses, the error answers) and then the routes. Everything built
at start-up is built by the lifespan in `sift.wiring`, one module per concern.

`main` is the entry point the desktop shell starts, and it answers the library questions the shell
asks before anything is started.
"""

from __future__ import annotations

# First, before any native library loads: a crash in one then leaves every thread's stack.
from sift.kernel import crash_record  # noqa: F401

# isort: split

import json
import os

# The NAME only, never a connection. `sqlite3.Error` is what a library question catches when the
# copy it was asked for cannot be made. The rule exists because a connection opened outside the
# kernel misses the pragmas and the single-writer lock; nothing here opens one.
#
# The suppression has to be on the IMPORT LINE: semgrep honours `nosemgrep` on the line it
# flags or the one directly above, and a reason written five lines up is not read at all.
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Imported so the component registers: it creates nothing now, but a library records a
# "benchmarks" version, and a component nothing imports is one the boot would not know.
import sift.kernel.benchmarks  # noqa: F401
from sift import client
from sift.kernel import lifecycle
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
from sift.kernel.log import LOG_FILENAME, configure_logging, get_logger
from sift.kernel.wire import Refused

# Imported for its side effect: it declares the look-and-feel preferences, gone without it.
from sift.slices import theming  # noqa: F401
from sift.slices.backup import COPIED_NOTE, leave_origin_note, make_its_own_library
from sift.wiring.lifespan import lifespan
from sift.wiring.routes import build_routes
from sift.wiring.tasks import retire_the_switches_into_whens

log = get_logger(__name__)


retire_the_switches_into_whens()


# Set on every response. Sift is open-source and internet-exposed, so the browser is told to trust
# nothing it is not explicitly handed.
#
#   - default-src 'self' confines scripts, styles and connections to Sift's own origin, which is
#     the anti-XSS control that survives a templating mistake; media and images also allow blob:
#     and data: because thumbnails and streamed segments are built into object URLs in the page.
#   - frame-ancestors 'none' is the anti-clickjacking control (it replaces X-Frame-Options): no
#     other site may embed Sift in a frame and trick a logged-in user into clicking through it.
#   - object-src 'none' and base-uri 'self' remove two old injection footguns (plugins, and
#     rewriting the document base so relative URLs resolve to an attacker's host).
#
# Two directives are not simply 'self', and they are not the same kind of exception.
#
# script-src: the client bootstraps with a small inline script, and an inline script has no origin,
# so `default-src 'self'` forbids it and the browser would refuse to start the app at all. It is
# named by the hash of its own contents, which the build writes into the page and `script_hashes`
# reads back. Exactly that script runs and no other: a script an injection writes into a page hashes
# to something else and does not run, which is the whole point. **This is the XSS control and it
# stays strict.** 'unsafe-inline' here would permit the injected script as readily as the real one.
#
# style-src: 'unsafe-inline', and that is a real loosening, so here is what it buys and what it
# costs. The client framework builds its screen-reader announcer (the element that tells a screen
# reader the page changed) with a style attribute written into its own compiled code. There is no
# setting for it. Refused, the announcer loses the styling that keeps it off screen and its text
# appears in the middle of the app. The alternatives are worse: a hash for a style attribute needs
# 'unsafe-hashes', and the hash is of a string inside somebody else's library that changes whenever
# it is upgraded, so the app would break visually on a routine dependency bump and nobody would know
# why.
#
# What it costs: someone who can inject markup can inject CSS. That is bounded here: the classic
# use for injected CSS is to send data out through url(), and img-src allows no host but this one,
# so there is nowhere to send it to. Scripts, the thing worth stopping, are unaffected. What it does
# not cost, and this is the part worth being clear about: nothing about script-src, frame-ancestors
# or object-src moves, and a test asserts 'unsafe-inline' never appears in script-src.
def content_security_policy(script_hashes: tuple[str, ...] = ()) -> str:
    """The policy, given the hashes of the client's own inline scripts."""
    inline = "".join(f" '{h}'" for h in script_hashes)
    return (
        "default-src 'self'; "
        f"script-src 'self'{inline}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )


# Sent on every response regardless of scheme, and the same on every response of every build.
#
# The content policy is NOT here. It names the hash of the page's inline script, and a client
# rebuilt under a running server changes that hash; a header frozen at import would make the browser
# refuse the only script on the page. It is assembled per response by `security_headers` below, from
# a reading that follows the file.
STATIC_SECURITY_HEADERS: dict[str, str] = {
    # Stops a browser from guessing that a JSON error or an uploaded file is really HTML or a
    # script and running it. The ingress gate decides what a file is; this keeps the browser from
    # second-guessing that from the other side.
    "x-content-type-options": "nosniff",
    # Do not put Sift's address in the Referer header of any link a user follows out. The address
    # is the one thing an operator who runs Sift anonymously does not want leaking to a third-party
    # site, and no feature needs the referrer sent.
    "referrer-policy": "no-referrer",
    # The mark the desktop shell looks for before it trusts an address, so that any other host
    # answering 200 or 401 on `/health` (a mistyped address on the LAN) does not become a
    # trusted origin with the shell's bridge attached. Sift's answers carry this on every response,
    # sign-in refusals included, and the shell requires it.
    "x-sift": "1",
}


def security_headers() -> dict[str, str]:
    """Every security header this response gets, the policy included.

    A function rather than a table because one of the headers is a reading of the built page rather
    than a constant: the policy names the hashes of the inline scripts the page BEING SERVED
    carries, and that page is replaced whenever the client is rebuilt. Asked per response, the
    header and the page can never disagree; a header frozen at import leaves a blank application
    after a rebuild.

    It costs about 4 us a response, most of it the one `stat` behind `script_hashes`.
    """
    return {
        "content-security-policy": content_security_policy(client.script_hashes()),
        **STATIC_SECURITY_HEADERS,
    }


# Only meaningful, and only sent, over HTTPS. It tells the browser to refuse plain HTTP to this
# host for two years, so a later downgrade attempt cannot strip TLS. Sent over plain HTTP it would
# be ignored by the browser anyway, and Sift is reached over http on the LAN by design, so it is
# gated on the request actually having arrived over HTTPS, the same signal that marks the cookie
# Secure. includeSubDomains stays scoped to Sift's own host; there is no preload, which would be a
# commitment no self-hoster should have made for them.
HSTS_HEADER_NAME = "strict-transport-security"
HSTS_HEADER_VALUE = "max-age=63072000; includeSubDomains"

# The most a non-upload request body may declare. FastAPI buffers the whole body into memory before
# a validator ever runs, so without this an unauthenticated caller could post a multi-gigabyte JSON
# body and exhaust memory. A control body (a login, a settings change) is a few hundred bytes; a
# megabyte is generous headroom. Multipart uploads are exempt: they are admin-only and streamed to
# disk by the endpoint that takes them, and a reverse proxy is the place to bound those.
MAX_REQUEST_BODY_BYTES = 1024 * 1024


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Report which field was wrong without echoing what was submitted for it.

        The default validation reply quotes the offending input back: fine for a mistyped port,
        but a rejected password (too long, wrong type) would come back in the response body. The
        client learns which field and why, and nothing it typed is reflected.
        """
        errors = exc.errors()
        # A refusal written for the person who typed the value is said as that sentence, with the
        # body field it is about, so a form can put it under that field.
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
        """Somebody asked for work that is switched off. 409, with the sentence the switch carries.

        Handled once here rather than per route, so a route that queues something cannot forget to
        say why nothing happened, which is the failure this shape exists to stop. 409 and not 403:
        nothing was refused on who is asking, and the way through is to change a setting.
        """
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """A genuine bug, anything that is not an HTTPException, still leaves with the security
        headers and a correlation id.

        The header middleware runs *inside* Starlette's error boundary, so a 500 synthesized when a
        handler raises would otherwise escape past it: no CSP, no nosniff, and no request-log line
        either. This is the one place those are reattached. The body stays a fixed generic string;
        the detail, with its traceback and any path in it, goes to the log, redacted, and never
        into the response, so a stack trace never reaches a client.
        """
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
        """Refuse an oversized non-upload body before it is read into memory.

        Defined first, so it is the innermost middleware: a rejection still passes back out through
        the logging and the security-header layers. Multipart uploads are exempt (they are
        streamed to disk by an admin-only endpoint), so this bounds the JSON and form path, where a
        body is small and a huge one is an attempt to exhaust memory. A body with no declared length
        is left to the reverse proxy, which the deployment guide already requires in front of Sift.
        """
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
        """Times every request and tags it with a correlation id.

        Mounted here rather than left to each slice, so a slice gets instrumentation by existing
        and cannot forget to add it. The route template is logged, never the resolved path: the
        path contains ids and query terms.
        """
        correlation_id = new_id()
        started = time.perf_counter()

        response = await call_next(request)

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
        """Attaches the security headers to every response.

        Here rather than on each route so a new route is protected by existing, and cannot forget
        to opt in. HSTS is added only when the request arrived over HTTPS: see the constant.
        """
        response = await call_next(request)
        for name, value in security_headers().items():
            response.headers[name] = value
        if is_https(request.url.scheme, request.headers.get("x-forwarded-proto", "")):
            response.headers[HSTS_HEADER_NAME] = HSTS_HEADER_VALUE
        return response

    # Cross-origin access is locked to an explicit allowlist, empty by default: with nothing listed
    # the browser's same-origin policy stands and no other site can call the API. allow_credentials
    # is on so that a listed origin can carry the login cookie; the setting refuses "*", which with
    # credentials would hand every site on the internet a logged-in session. Added last so it wraps
    # the stack and can answer a preflight before any handler runs.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def create_app() -> FastAPI:
    settings = get_settings()

    # The interactive API docs enumerate every endpoint to whoever can open them, and Sift is meant
    # to be reachable from the internet. Off unless the operator turns them on; when off, the three
    # routes are not mounted at all rather than merely hidden.
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


#: The two questions that can be asked of this module WITHOUT starting anything.
#:
#: They exist for the desktop shell's library switcher. Before it stops the backend that is running
#: and starts one on a different folder, it has to know whether that folder holds a database this
#: build can read, and it cannot find out by opening it, because opening it IS the migration it
#: is trying to warn somebody about first.
#:
#: A FLAG ON THIS MODULE rather than a script of its own, and that is the whole reason it is here.
#: The answer is a comparison against the schema registry, and the registry is only complete once
#: every slice has been imported, which is what importing this module does and what nothing else
#: does. A separate entry point would have to import the same world to be right, and a smaller one
#: that imported less would answer confidently from half a registry.
#:
#: Each takes a library FOLDER or a database FILE (see `library_database`): the folder form is what
#: the switcher asks about a library it knows, the file form what it asks about a database somebody
#: has just chosen.
#:
#: The third makes a library from a database file that is not one's own (a backup copy, a file
#: somebody was given) by copying it into a new library folder. It takes the file and the new
#: folder's data directory, and never touches the file it copies from.
INSPECT_LIBRARY = "--inspect-library"
BACK_UP_LIBRARY = "--back-up-library"
ADOPT_LIBRARY = "--adopt-library"


def answer_about_a_library(argv: Sequence[str], say: Callable[[str], None]) -> int | None:
    """Answer one of the questions above and say what to exit with, or None for "not asked".

    One JSON object on stdout, and exit 0 even for a library this build refuses. The verdict IS the
    answer, and an exit code would be a second, coarser copy of it that the shell would then have to
    keep in step. A non-zero exit is kept for the one thing that is genuinely a fault: being asked a
    question with no path to ask it about.
    """
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
            # A copy of another library: its sign-ins and its waiting work stay with that one. A
            # copy that could not be settled is taken back, so it is never opened as it stands.
            try:
                make_its_own_library(made, now=int(time.time()), note=COPIED_NOTE)
                # Its first start then says where it came from, as the Import button's door does.
                leave_origin_note(made.parent, Path(argv[2]).name)
            except BaseException:
                # nosemgrep: sift-no-file-removal-outside-delete-trash (the copy this call just made and could not settle; never a library anybody opened)
                made.unlink(missing_ok=True)
                raise
            say(json.dumps({"ok": True, "database": str(made)}))
            return 0
        snapshot = copy_database_aside(library_database(target))
    except (OSError, sqlite3.Error) as refused:
        # Said rather than raised: the caller is a shell reading stdout, and a traceback on stderr
        # would reach it as an empty answer with no reason in it.
        say(json.dumps({"ok": False, "detail": str(refused)}))
        return 0
    say(json.dumps({"ok": True, "copy": str(snapshot)}))
    return 0


def main() -> None:
    # BEFORE the settings are read, deliberately. Every question takes its path as an argument and
    # answers about THAT path, so requiring a valid SIFT_DATA_DIR in the environment first would
    # make the preflight fail on exactly the machines it exists to help.
    answered = answer_about_a_library(sys.argv[1:], print)
    if answered is not None:
        raise SystemExit(answered)

    import uvicorn

    # Settings are read, the directories proven writable, and SQLite checked BEFORE the server
    # starts. Anything raised inside the server's startup instead comes back out as a traceback,
    # and a traceback is not an error message: the one line that says what to do gets buried under
    # twenty that do not. These are the failures a self-hoster can actually fix, so they are caught
    # where they can still be phrased as a sentence.
    try:
        settings = get_settings()
        ensure_directories(settings)
        # Quiet: logging is not configured yet, so a line written here would be formatted
        # differently from every other one. The application's own startup logs it.
        check_sqlite_capabilities(announce=False)
    except (ConfigError, DatabaseError) as exc:
        # The plain sentence is what a self-hoster needs: they have mistyped a path, and a stack
        # trace does not tell them that. But it is not the whole story for whoever wants more,
        # so the technical detail is one environment variable away rather than discarded.
        detail = ""
        if os.environ.get("SIFT_LOG_LEVEL", "").upper() == "DEBUG":
            detail = "\n" + "".join(traceback.format_exception(exc))
        else:
            detail = "\nSet SIFT_LOG_LEVEL=DEBUG for the full technical detail.\n"
        raise SystemExit(f"\n{exc}\n{detail}") from None

    # Before uvicorn starts, so its own startup lines are scrubbed and formatted like everything
    # else. Configured inside the lifespan as well, for the case where Sift is served by another
    # ASGI server that never calls this.
    configure_logging(
        settings.log_level,
        redact_personal=not settings.log_unredacted,
        log_file=settings.data_dir / LOG_FILENAME,
        max_bytes=settings.log_max_bytes,
        backups=settings.log_backups,
    )

    # THE APP THIS MODULE BUILT, not its own name. Named as "sift.main:app", uvicorn imports the
    # module a second time (`python -m sift.main` has already run it as `__main__`), and every
    # line at module level runs twice: two apps built, and a retirement declared at module level
    # refused as "retired twice", and the boot fails. The name form exists for reload and workers,
    # and Sift uses neither.
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        # uvicorn's access log writes the raw request line, which carries the query string and
        # any ids in the path. The middleware above already records every request, redacted and
        # with a correlation id, so this would only ever be a second, leakier copy.
        access_log=False,
        # Stops uvicorn replacing the handlers installed above with its own, which do not redact.
        log_config=None,
    )
    server = uvicorn.Server(config)
    # A Server built by hand rather than `uvicorn.run`, only so that something else can ask it to
    # stop. See below for who asks, and why there is no other way for them to.
    if settings.stop_on_stdin_eof:
        stop_when_the_parent_lets_go(server)
        # WHY THE SAME FLAG DECIDES BOTH. It means one thing: something started this process, is
        # holding on to it, and will notice when it ends. That is exactly the condition under which
        # asking to be restarted is honest. Anywhere else the backend would stop and stay stopped,
        # and `can_restart` answers no so the screen says so instead of taking the library off the
        # air to find out. See sift/kernel/lifecycle.py.
        lifecycle.stops_with(lambda: setattr(server, "should_exit", True))
    server.run()
    if lifecycle.was_asked_to_restart():
        # The clean shutdown has already run: the database is closed and the log is folded. All that
        # is left is to end with a code the supervisor can tell apart from a crash.
        raise SystemExit(lifecycle.RESTART_EXIT_CODE)


def stop_when_the_parent_lets_go(server: object) -> None:
    """Shut down cleanly when whoever started this lets go of its stdin.

    WHY THIS EXISTS, AND WHY IT IS NOT A SIGNAL.

    `child.kill()` from the desktop shell is SIGTERM on POSIX, and the process gets to close its
    files; ON WINDOWS NODE HAS NO SUCH THING: every signal name it accepts becomes
    TerminateProcess, which stops the process where it stands, leaving the database unclosed and the
    write-ahead log unfolded on every quit.

    A pipe is stronger than a signal.
    Only the process that spawned this one holds the other end, so nothing on the machine can ask
    Sift to stop by pretending; there is no port to reach, no token to leak and no endpoint to
    protect. When the shell closes it, or dies holding it, the read below ends and uvicorn is asked
    to stop the way it would for Ctrl-C.
    """
    # THE RAW FILE DESCRIPTOR, NOT `sys.stdin.buffer`, AND THAT IS NOT A STYLE CHOICE.
    #
    # A buffered reader has a lock, and a daemon thread parked inside `read` is holding it when the
    # interpreter starts to finalize. CPython cannot flush a stream whose lock it cannot take, so it
    # aborts, printing
    #
    #     Fatal Python error: _enter_buffered_busy: could not acquire lock for
    #     <_io.BufferedReader name='<stdin>'> at interpreter shutdown
    #
    # after a clean shutdown, so every stop would end with "Fatal Python error" in the log.
    #
    # `os.read` goes at the descriptor directly. There is no buffer, so there is no lock, so there
    # is nothing for finalization to wait on.
    try:
        descriptor = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        # No stdin to watch: a service manager gave this process none. Nothing to do.
        return

    def watch() -> None:
        try:
            while os.read(descriptor, 1):
                pass
        except (OSError, ValueError):
            # The pipe went away rather than closing politely. Same meaning: the parent is gone.
            pass
        # `should_exit` is uvicorn's own flag and is what Ctrl-C sets. The server finishes what it
        # is serving, runs the lifespan's shutdown, and returns from `run`.
        server.should_exit = True  # type: ignore[attr-defined]

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


# LAST, AND NOTHING BELOW IT. `main()` serves the app this module built, so it runs before any
# line that follows: a function defined under here is undefined while the server is up.
if __name__ == "__main__":
    main()
