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

# Every slice router mounts under this so a page and an endpoint never share a name; /health is
# outside it.
API_PREFIX = "/api"


def _database_reading(request: Request) -> dict[str, Any]:
    # Connections: a held one reads healthy on both the loop and the thread pool.
    reads = wiring.part_of(request, wiring.READS)
    database = wiring.part_of(request, wiring.DATABASE)
    return {
        "worst_wait_seconds": round(reads.worst_wait_seconds, 3),
        "full_count": reads.full_count,
        "waiting_seconds": round(reads.waiting_seconds, 3),
        # What is holding the one-at-a-time lane, by the name of the pass.
        "sweeping": reads.holding,
        "readers": database.readers,
        # Which of two ways this install answers a single-row lookup, timed at start-up
        # (`time_a_point_read`).
        "point_reads": "inline" if database.point_reads_inline else "threaded",
        "point_read_us": round(database.point_read_seconds * 1_000_000, 1),
    }


def _health_detail(request: Request) -> dict[str, Any]:
    """The admin's half of the health answer: the hardware and how well it is keeping up."""
    report = wiring.part_of(request, wiring.HARDWARE)
    sqlite = wiring.part_of(request, wiring.SQLITE)
    watchdog = wiring.part_of(request, wiring.WATCHDOG)
    threads = wiring.part_of(request, wiring.THREADS)
    storage = wiring.part_of(request, wiring.LANES)
    backlog = wiring.part_of(request, wiring.BACKLOG)
    slowest = wiring.part_of(request, wiring.SLOWEST)
    widest = wiring.part_of(request, wiring.WIDEST)
    # The workers really running and the caps in force, as the pool converges on the settings.
    pool = wiring.part_of(request, wiring.POOL)
    return {
        "status": "ok",
        "workers": {"running": pool.concurrency, "limits": dict(pool.limits)},
        # Which run of the server this is, so a screen can tell a restart has happened; admin only.
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
        "storage": storage.readings(),
        "database": _database_reading(request),
        # `worst` is the loop's worst wait; `latest` is what it is now.
        "loop_queue": {
            "worst_seconds": round(backlog.worst_seconds, 3),
            "latest_seconds": round(backlog.latest_seconds, 3),
            "over_count": backlog.over_count,
        },
        # Ordered by total: the fault is a cheap thing done far too often.
        "slowest_work": slowest.worst(),
        # Ranked by the widest single read: a wide read is a fault in one statement.
        "widest_reads": widest.widest(),
    }


def _mount_slices(app: FastAPI) -> None:
    # Slice routers mount here, one line each, under the single prefix (see API_PREFIX).
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
    app.include_router(insights.video_router.router, prefix=API_PREFIX)
    app.include_router(insights.keep_router.router, prefix=API_PREFIX)
    app.include_router(insights.session_router.router, prefix=API_PREFIX)
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
    # The whole-install record, at an address of its own.
    app.include_router(workbench.ledger_router, prefix=API_PREFIX)


def build_routes(app: FastAPI) -> None:
    """Every route the application answers, in the order they are matched; the client is last."""

    @app.get("/health")
    async def health(
        request: Request,
        viewer: Annotated[Viewer | None, Depends(auth.optional_viewer)],
    ) -> dict[str, Any]:
        """Liveness for anyone; the hardware detail, a fingerprint, for an admin only."""
        if viewer is None or not viewer.is_admin:
            return {"status": "ok"}
        return _health_detail(request)

    _mount_slices(app)

    # The browser client, last, as a declared route rather than an unseen mount.
    @app.get("/{path:path}", include_in_schema=False)
    async def browser_client(path: str, request: Request) -> Response:
        # An unknown /api path gets an API answer, never the client's HTML with a 200.
        if path == "api" or path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found."})

        # A lookup in the build's own file list: nothing about the request touches the disk here.
        asset = client.asset_for(path, await client.files())
        if asset is None:
            # Nobody built the client, ordinary in a checkout: 503, since the address is fine and
            # the app missing.
            return JSONResponse(
                status_code=503,
                content={"detail": "The browser client is not built. The API is at /api."},
            )
        # Revalidated on every load, so "unchanged" saves the file; the page shell is never kept.
        return await serve_file(
            request,
            asset,
            media_type=guess_type(asset.name)[0] or "application/octet-stream",
            headers={"Cache-Control": client.cache_control_for(path, asset)},
        )
