# SPDX-License-Identifier: AGPL-3.0-or-later
"""The routes: the health answer, every slice's router, and the browser client."""

from __future__ import annotations

from mimetypes import guess_type
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from sift import client
from sift.kernel import lifecycle, wiring
from sift.kernel.access import Viewer
from sift.kernel.serving import serve_file
from sift.slices import (
    auth,
    backup,
    browse,
    capture,
    collections,
    dedup,
    delete,
    desktop,
    download,
    faces,
    importing,
    insights,
    library_roots,
    live,
    logs,
    loops,
    media_edit,
    media_jobs,
    music,
    organize,
    people,
    performance,
    photo_sets,
    player,
    records,
    related,
    remote,
    search,
    semantic,
    settings_hub,
    sharing,
    shoots,
    songs,
    stash_boxes,
    stash_migration,
    suggestions,
    swap,
    tags_ratings,
    tasks,
    theater,
    tidy,
    update_notify,
    vault,
    watermarks,
    workbench,
)

# Every slice router mounts under this. The application answers two kinds of request on one port,
# the JSON API and the pages of the browser client, and they would otherwise compete for the same
# names: /settings is a section of the interface as much as it is an endpoint, and /jobs, /library
# and /people will each be both. Splitting them by prefix is what lets the client own plain,
# linkable URLs and keeps a new endpoint from silently shadowing a page.
#
# /health is deliberately outside it: a container's health probe and an uptime monitor are not the
# API, the interface has no page by that name, and the address is baked into deployments.
API_PREFIX = "/api"


def _database_reading(request: Request) -> dict[str, Any]:
    # The third reading, which the loop's and the threads' could not have shown. Every connection
    # held by a background pass reads as a perfectly healthy loop and a perfectly healthy thread
    # pool, because neither of them is what the request is waiting for.
    reads = wiring.part_of(request, wiring.READS)
    database = wiring.part_of(request, wiring.DATABASE)
    return {
        "worst_wait_seconds": round(reads.worst_wait_seconds, 3),
        "full_count": reads.full_count,
        "waiting_seconds": round(reads.waiting_seconds, 3),
        # What is holding the one-at-a-time lane, when anything is. The name of the pass is
        # what turns a number into somewhere to go: "the wait is eight seconds" is another
        # question, and "a duplicate scan is holding the lane" is an answer.
        "sweeping": reads.holding,
        "readers": database.readers,
        # Which of the two ways this install answers a single-row lookup, and the reading
        # that decided it. Timed at start-up rather than configured, because the same
        # statement is far slower with the database on a network share than on a local disk,
        # so it is a fact about where somebody put their data, and one install answering
        # differently from another has to be visible rather than a mystery. See
        # `time_a_point_read`.
        "point_reads": "inline" if database.point_reads_inline else "threaded",
        "point_read_us": round(database.point_read_seconds * 1_000_000, 1),
    }


def _health_detail(request: Request) -> dict[str, Any]:
    """The admin's half of the health answer: the hardware and how well it is keeping up."""
    report = wiring.part_of(request, wiring.HARDWARE)
    # What the machine's SQLite turned out to be capable of rides along on the same terms and
    # for the same reason: it is the answer to why a feature is or is not available here.
    sqlite = wiring.part_of(request, wiring.SQLITE)
    # And how well the event loop has been kept free, which is the answer to "why did playback
    # stutter" and the only place that answer has ever been visible without a stack trace.
    watchdog = wiring.part_of(request, wiring.WATCHDOG)
    # And how long work is waiting for a thread, which is the same question about the other
    # half of the machine. A held loop and a full pool feel alike to whoever is watching a
    # video and are fixed by opposite things, so both readings are here rather than one.
    threads = wiring.part_of(request, wiring.THREADS)
    storage = wiring.part_of(request, wiring.LANES)
    # And the fourth, which all three of the above read healthy through: a loop never held, no
    # pool full, no connection queued, and requests still slow because the loop's ready queue is
    # thousands of callbacks deep.
    backlog = wiring.part_of(request, wiring.BACKLOG)
    # And what the time actually went on, which is the question every one of the others leaves
    # unanswered: they say the machine is not short of anything, not what it is busy with.
    slowest = wiring.part_of(request, wiring.SLOWEST)
    # And how much the reads are moving, which is the only figure here that does not change
    # with how busy the machine happens to be while somebody is looking at it.
    widest = wiring.part_of(request, wiring.WIDEST)
    # And how many workers are ACTUALLY running, with the caps actually in force. Everything
    # else here is a fact about the hardware, and `hardware` answers "eight workers" from the
    # processor count while the pool may be running a configured twelve. The pool is the
    # authority on both: it re-reads the settings every few seconds and converges on them, so
    # this is what is in force at the moment of the question rather than what was chosen at
    # start-up. The limits ride along for the same reason: a pass that is not moving is
    # nearly always a pass whose cap has resolved to zero.
    pool = wiring.part_of(request, wiring.POOL)
    return {
        "status": "ok",
        "workers": {"running": pool.concurrency, "limits": dict(pool.limits)},
        # WHICH RUN OF THE SERVER THIS IS, and it is admin-only along with everything else here.
        #
        # What needs it is a screen waiting out a restart it asked for: the server answers the
        # ask BEFORE it goes and keeps answering for a second afterwards, so a poll for "does it
        # reply" decides it has come back before it has left. Only an admin can ask for a
        # restart, so only an admin needs this, and the anonymous answer stays exactly the two
        # words it has always been, which two tests hold it to.
        "boot": lifecycle.BOOT_ID,
        "hardware": report.as_dict(),
        "sqlite": sqlite.as_dict(),
        "loop": {
            "worst_lag_seconds": round(watchdog.worst_lag_seconds, 3),
            "held_count": watchdog.held_count,
        },
        "threads": {
            "worst_wait_seconds": round(threads.worst_wait_seconds, 3),
            "full_count": threads.full_count,
            "waiting_seconds": round(threads.waiting_seconds, 3),
        },
        # And the fifth: how many files are being read from each storage, and how long a read
        # has waited for a place. The one figure that says a share is the bottleneck.
        "storage": storage.readings(),
        "database": _database_reading(request),
        # A loop that is never held and still never gets to you. `worst` is what it has been at
        # its worst; `latest` is what it is now, which is the one that moves while somebody is
        # watching a screen not load.
        "loop_queue": {
            "worst_seconds": round(backlog.worst_seconds, 3),
            "latest_seconds": round(backlog.latest_seconds, 3),
            "over_count": backlog.over_count,
        },
        # Ordered by TOTAL, not by the worst single run. The fault this exists to show is a
        # cheap thing done far too often, and ordering by the worst run is what hides it.
        "slowest_work": slowest.worst(),
        # Ranked by the WIDEST single read rather than by a total, which is the opposite of the
        # line above and deliberate: a wide read is a fault in one statement, and a total would
        # hide it behind a narrow one that runs often.
        "widest_reads": widest.widest(),
    }


def _mount_slices(app: FastAPI) -> None:
    # Slice routers are mounted here, one line each. The prefix is applied at this single point
    # rather than written into each router, so a slice declares only its own name and cannot forget
    # to be under /api: see API_PREFIX.
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(settings_hub.router, prefix=API_PREFIX)
    app.include_router(media_jobs.router, prefix=API_PREFIX)
    app.include_router(live.router, prefix=API_PREFIX)
    app.include_router(library_roots.router, prefix=API_PREFIX)
    app.include_router(download.router, prefix=API_PREFIX)
    app.include_router(stash_boxes.router, prefix=API_PREFIX)
    app.include_router(capture.router, prefix=API_PREFIX)
    app.include_router(browse.router, prefix=API_PREFIX)
    app.include_router(watermarks.router, prefix=API_PREFIX)
    app.include_router(player.router, prefix=API_PREFIX)
    app.include_router(delete.router, prefix=API_PREFIX)
    app.include_router(tags_ratings.router, prefix=API_PREFIX)
    app.include_router(people.router, prefix=API_PREFIX)
    app.include_router(dedup.router, prefix=API_PREFIX)
    app.include_router(suggestions.router, prefix=API_PREFIX)
    app.include_router(swap.router, prefix=API_PREFIX)
    app.include_router(importing.router, prefix=API_PREFIX)
    app.include_router(importing.files_router, prefix=API_PREFIX)
    app.include_router(logs.router, prefix=API_PREFIX)
    app.include_router(tidy.router, prefix=API_PREFIX)
    app.include_router(organize.router, prefix=API_PREFIX)
    app.include_router(media_edit.router, prefix=API_PREFIX)
    app.include_router(collections.router, prefix=API_PREFIX)
    app.include_router(photo_sets.router, prefix=API_PREFIX)
    app.include_router(songs.router, prefix=API_PREFIX)
    app.include_router(shoots.router, prefix=API_PREFIX)
    app.include_router(music.router, prefix=API_PREFIX)
    app.include_router(loops.router, prefix=API_PREFIX)
    app.include_router(records.router, prefix=API_PREFIX)
    app.include_router(related.router, prefix=API_PREFIX)
    app.include_router(vault.router, prefix=API_PREFIX)
    app.include_router(search.router, prefix=API_PREFIX)
    app.include_router(theater.router, prefix=API_PREFIX)
    app.include_router(remote.router, prefix=API_PREFIX)
    app.include_router(insights.router.router, prefix=API_PREFIX)
    app.include_router(insights.recaps_router.router, prefix=API_PREFIX)
    app.include_router(insights.path_router.router, prefix=API_PREFIX)
    app.include_router(insights.capture_router.router, prefix=API_PREFIX)
    app.include_router(backup.router, prefix=API_PREFIX)
    app.include_router(backup.libraries_router, prefix=API_PREFIX)
    app.include_router(stash_migration.router, prefix=API_PREFIX)
    app.include_router(update_notify.router, prefix=API_PREFIX)
    app.include_router(tasks.router, prefix=API_PREFIX)
    app.include_router(sharing.router, prefix=API_PREFIX)
    app.include_router(faces.router, prefix=API_PREFIX)
    app.include_router(semantic.router, prefix=API_PREFIX)
    app.include_router(performance.router, prefix=API_PREFIX)
    app.include_router(desktop.router, prefix=API_PREFIX)
    app.include_router(workbench.router, prefix=API_PREFIX)
    # The whole-install record, at an address of its own. It is the workbench slice's table widened
    # (see `kernel/ledger.py`), and it is not the Organize board: that screen's promise is that it
    # empties, and this one only ever grows.
    app.include_router(workbench.ledger_router, prefix=API_PREFIX)


def build_routes(app: FastAPI) -> None:
    """Every route the application answers, in the order they are matched.

    The health answer, then one line per slice router, then the browser client, which is last so it
    takes only what nothing above it claimed.
    """

    @app.get("/health")
    async def health(
        request: Request,
        viewer: Annotated[Viewer | None, Depends(auth.optional_viewer)],
    ) -> dict[str, Any]:
        """Liveness for anyone; the hardware detail for an admin.

        The status line has to answer an unauthenticated caller: a container's health probe and an
        uptime monitor cannot log in, and "is it up" is not a secret. The hardware report is a
        different thing (core count, RAM, GPU vendor, the exact encoder list), which identifies
        nothing about the machine but is a fingerprint a stranger would use to size it up. So it
        rides along only for an admin, whose question "which encoder am I actually using when this
        is slow" it exists to answer, and is simply absent for everyone else.
        """
        if viewer is None or not viewer.is_admin:
            return {"status": "ok"}
        return _health_detail(request)

    _mount_slices(app)

    # The browser client. Last, so it takes only what nothing above it claimed.
    #
    # A route rather than a mounted directory, and that is deliberate: a mount is invisible to the
    # check that every route the app answers has been declared, so the whole client would be served
    # by something no one had to say anything about. This has a line in that table like everything
    # else.
    #
    # It is public, and that costs nothing. The client is HTML and JavaScript: it holds no data,
    # and the API behind it refuses whatever the person loading it may not have. Anyone can read the
    # source anyway; it is open. Being logged out means the app renders and asks you to sign in.
    @app.get("/{path:path}", include_in_schema=False)
    async def browser_client(path: str, request: Request) -> Response:
        # An unknown path under /api is a caller getting the API wrong, and it gets an API answer.
        # Handing back the client's HTML with a 200 would tell a script that its mistyped endpoint
        # worked, and leave it to discover otherwise while parsing a web page as JSON.
        if path == "api" or path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found."})

        # A lookup in the build's own file list: nothing about the request touches the disk here.
        asset = client.asset_for(path, await client.files())
        if asset is None:
            # Nobody has built the client. Ordinary in a checkout: the API is what is being worked
            # on, and building it needs Node, which the tests do not.
            #
            # 503 and not 404, and the difference matters. 404 says "there is nothing at this
            # address", which is a refusal: it is the same answer this app gives for something you
            # may not see. The address is fine and the app is missing, which is a different sentence
            # and a temporary one. A refusal would also make the route look, to the check that every
            # route serves who it says it serves, like a public one turning people away, and the
            # whole test suite would fail on any checkout without Node.
            return JSONResponse(
                status_code=503,
                content={"detail": "The browser client is not built. The API is at /api."},
            )
        # Anything outside the immutable directory is revalidated on every load, so answering
        # "unchanged" is what keeps that from costing the file each time; the page shell is not kept
        # at all (see `client.PAGE`).
        return await serve_file(
            request,
            asset,
            media_type=guess_type(asset.name)[0] or "application/octet-stream",
            headers={"Cache-Control": client.cache_control_for(path, asset)},
        )
