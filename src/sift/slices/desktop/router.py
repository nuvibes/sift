# SPDX-License-Identifier: AGPL-3.0-or-later
"""The desktop routes. Admin-only, and every one that changes anything is CSRF-guarded.

Each acts on the COMPUTER RUNNING SIFT, whichever computer the admin asking is sitting at: that is
the whole reason they are routes rather than the desktop app's own verbs. The page never names a
path, a port or a command; it says one word (on or off, private or any), and the app checks it
again on its side.

A backend with no app behind it answers `has_app: false` to the read and 409 to an act, in words: a
switch drawn off because nobody could be asked would say the opposite of what might be true.

Sharing, a storage move, an update and a library each restart Sift on that computer. The app
answers first (taken on, or refused in its own words before anything stopped) and acts after, so
these routes answer while this process still runs, and the page waits for a new run of the server.

Every act that CHANGES that computer writes a line in History (`kernel.machine_acts.record_act`):
who asked, and from which device, which the window names on the ask (`device`). The reads write
nothing.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError

from sift.kernel.access import Viewer
from sift.kernel.db import Database
from sift.kernel.log import get_logger
from sift.kernel.machine_acts import record_act
from sift.kernel.wire import Wire
from sift.kernel.wiring import DATABASE, SETTINGS, part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.desktop.models import (
    ActTaken,
    AppLog,
    DesktopView,
    FirewallView,
    MoveStorage,
    OpenFirewall,
    OpenRemembered,
    RememberedLibraries,
    SharingChange,
    ShellSharing,
    StartWithWindows,
    StorageView,
    UpdateTaken,
)
from sift.slices.desktop.shell import NoShell, ShellLink, ShellUnreachable

log = get_logger(__name__)

router = APIRouter(prefix="/desktop", tags=["desktop"])

#: What an act says where no desktop app started this backend.
NO_SHELL = (
    "Sift on the computer running your library isn't running in the Sift app, "
    "so this can't be changed from here."
)

#: What any ask says when the app there did not answer.
NO_ANSWER = "The Sift app on the computer running your library didn't answer. Try again."


#: What the window asking calls its own computer, where it knows (the Sift app does; a browser
#: does not). Bounded at the door; `machine_acts.device_words` decides what a line keeps of it.
Device = Annotated[str | None, Query(max_length=256)]


def _link(request: Request) -> ShellLink:
    return ShellLink.from_settings(part_of(request, SETTINGS))


def _database(request: Request) -> Database:
    return part_of(request, DATABASE)


def _unanswered(error: Exception) -> HTTPException:
    """No app behind this backend is a 409 (a fact about how it runs); an app that did not
    answer is a 503 (a fact about now, worth trying again)."""
    if isinstance(error, NoShell):
        return HTTPException(status.HTTP_409_CONFLICT, NO_SHELL)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_ANSWER)


def _described(facts: dict[str, Any]) -> DesktopView:
    """The app's answer, checked as it crosses: a shape it does not have is no answer at all."""
    try:
        return DesktopView(
            has_app=True,
            machine=facts.get("machine"),
            starts_with_windows=facts.get("startsWithWindows"),
            sharing=ShellSharing.model_validate(facts.get("sharing")),
        )
    except ValidationError as error:
        raise _unanswered(ShellUnreachable()) from error


def _checked[W: Wire](shape: type[W], answer: dict[str, Any]) -> W:
    """The app's answer in the shape it must have. An answer of another shape is no answer."""
    try:
        return shape.model_validate(answer)
    except ValidationError as error:
        raise _unanswered(ShellUnreachable()) from error


def _firewall(report: dict[str, Any]) -> FirewallView:
    return _checked(FirewallView, report)


def _camel(answer: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    """The app speaks the page's camelCase; these routes speak the wire's snake_case."""
    return {wire: answer.get(app) for wire, app in names.items()}


_STORAGE = {
    "data_dir": "dataDir",
    "cache_dir": "cacheDir",
    "data_bytes": "dataBytes",
    "cache_bytes": "cacheBytes",
    "last_move": "lastMove",
}
_LIBRARY = {
    "data_dir": "dataDir",
    "cache_dir": "cacheDir",
    "name": "name",
    "last_opened": "lastOpened",
}


@router.get("", response_model=DesktopView)
async def describe(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> DesktopView:
    """The computer running Sift: its name, whether Sift starts with Windows there, its sharing."""
    try:
        facts = await _link(request).facts()
    except NoShell:
        return DesktopView(has_app=False, machine=None, starts_with_windows=None, sharing=None)
    except ShellUnreachable as error:
        raise _unanswered(error) from error
    return _described(facts)


@router.put("/start-with-windows", response_model=DesktopView, dependencies=[Depends(csrf_protect)])
async def start_with_windows(
    body: StartWithWindows,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    device: Device = None,
) -> DesktopView:
    """Say whether Sift starts when somebody signs in to the computer running it. Answers what is
    true there afterwards, which is Windows' answer rather than the word that was sent, and History
    says it only where Windows took it."""
    link = _link(request)
    try:
        await link.start_with_windows(body.on)
        facts = await link.facts()
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    log.info("desktop.start_with_windows", on=body.on)
    described = _described(facts)
    if described.starts_with_windows is body.on:
        await record_act(
            _database(request),
            admin,
            "start_with_windows_on" if body.on else "start_with_windows_off",
            device,
        )
    return described


@router.get("/firewall", response_model=FirewallView)
async def read_firewall(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> FirewallView:
    """Says whether Windows there lets other computers reach Sift. A plain read; it opens nothing."""
    try:
        report = await _link(request).firewall()
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    return _firewall(report)


@router.post("/firewall", response_model=FirewallView, dependencies=[Depends(csrf_protect)])
async def open_firewall(
    body: OpenFirewall,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    device: Device = None,
) -> FirewallView:
    """Windows there is asked to let other computers through.

    THE PROMPT APPEARS ON THE COMPUTER RUNNING SIFT, and only somebody there can approve it: it is
    Windows' own administrator prompt, which nothing reached over a network can answer. So this
    waits while they walk over, and answers the state afterwards, which is the same true answer
    for an approval, a refusal and nobody coming.
    """
    try:
        report = await _link(request).open_firewall(body.scope)
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    log.info("desktop.open_firewall", scope=body.scope, state=report.get("state"))
    opened = _firewall(report)
    # Only where it is open afterwards: a prompt nobody approved changed nothing there.
    if opened.state == "open":
        await record_act(_database(request), admin, "firewall_opened", device)
    return opened


@router.put("/sharing", response_model=ActTaken, dependencies=[Depends(csrf_protect)])
async def change_sharing(
    body: SharingChange,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    device: Device = None,
) -> ActTaken:
    """Offer the library to the network there, or stop. Sift restarts there to listen on the
    other address, so turning it off cuts off every other computer, the one asking included."""
    try:
        answer = await _link(request).set_sharing(body.on)
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    log.info("desktop.sharing_asked", on=body.on, taken=answer.get("ok"))
    taken = _checked(ActTaken, answer)
    if taken.ok:
        verb = "sharing_turned_on" if body.on else "sharing_turned_off"
        await record_act(_database(request), admin, verb, device)
    return taken


@router.get("/storage", response_model=StorageView)
async def read_storage(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> StorageView:
    """Where Sift keeps its two folders on the computer running it, and how big they are."""
    try:
        answer = await _link(request).storage()
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    return _checked(StorageView, _camel(answer, _STORAGE))


@router.post("/storage/move", response_model=ActTaken, dependencies=[Depends(csrf_protect)])
async def move_storage(
    body: MoveStorage,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    device: Device = None,
) -> ActTaken:
    """Move both storage folders into an empty folder on the computer running Sift.

    The app refuses a folder that is not empty, not writable, on a network drive or inside the one
    in use BEFORE it stops anything. Taken on, Sift stops there, moves, and starts again, which
    takes as long as copying the library does across drives.
    """
    try:
        answer = await _link(request).move_storage(body.folder)
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    log.info("desktop.move_asked", taken=answer.get("ok"))
    taken = _checked(ActTaken, answer)
    # The folder is not written down: a path says where the library lives, and the line needs only
    # that the data moved, and who asked from which device.
    if taken.ok:
        await record_act(_database(request), admin, "storage_moved", device)
    return taken


@router.post("/update", response_model=UpdateTaken, dependencies=[Depends(csrf_protect)])
async def install_update(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    device: Device = None,
) -> UpdateTaken:
    """Install a newer Sift on the computer running it.

    Takes nothing: the app there reads its own release feed, checks the signature, refuses anything
    not newer than itself, and opens the installer ON THAT COMPUTER'S SCREEN, where somebody agrees
    to it. It answers once the installer is checked, before Sift stops for it.
    """
    try:
        answer = await _link(request).update()
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    log.info("desktop.update_asked", ok=answer.get("ok"), reason=answer.get("reason"))
    taken = _checked(
        UpdateTaken,
        {
            "ok": answer.get("ok"),
            "version": answer.get("version"),
            "reason": answer.get("reason"),
        },
    )
    if taken.ok:
        named = {"version": taken.version} if taken.version else {}
        await record_act(_database(request), admin, "update_started", device, **named)
    return taken


@router.get("/log", response_model=AppLog)
async def read_app_log(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
    lines: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> AppLog:
    """The end of the Sift app's own log on the computer running Sift."""
    try:
        answer = await _link(request).log(lines)
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    return _checked(AppLog, answer)


@router.get("/libraries", response_model=RememberedLibraries)
async def read_remembered(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> RememberedLibraries:
    """Every library the Sift app on the computer running Sift has opened, and the one open."""
    try:
        answer = await _link(request).libraries()
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    listed = answer.get("libraries")
    return _checked(
        RememberedLibraries,
        {
            "current": answer.get("current"),
            "libraries": (
                [_camel(one, _LIBRARY) for one in listed if isinstance(one, dict)]
                if isinstance(listed, list)
                else None
            ),
        },
    )


@router.post("/libraries/open", response_model=ActTaken, dependencies=[Depends(csrf_protect)])
async def open_remembered(
    body: OpenRemembered,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    device: Device = None,
) -> ActTaken:
    """Open a library the app there has opened before. The app refuses any other folder, and an
    older library, whose upgrade question is asked on that computer's screen, in words.

    History names the library by the name the app lists it under, read BEFORE the ask: once the
    app has taken it on, Sift there stops, and a read after would reach nobody."""
    link = _link(request)
    try:
        called = await _library_called(link, body.data_dir)
        answer = await link.open_library(body.data_dir)
    except (NoShell, ShellUnreachable) as error:
        raise _unanswered(error) from error
    log.info("desktop.library_asked", taken=answer.get("ok"))
    taken = _checked(ActTaken, answer)
    if taken.ok:
        named = {"library": called} if called else {}
        await record_act(_database(request), admin, "library_opened", device, **named)
    return taken


async def _library_called(link: ShellLink, data_dir: str) -> str | None:
    """The name the app there lists a library under, or None where it lists no such library."""
    listed = (await link.libraries()).get("libraries")
    for one in listed if isinstance(listed, list) else ():
        if isinstance(one, dict) and one.get("dataDir") == data_dir:
            name = one.get("name")
            return name if isinstance(name, str) and name else None
    return None
