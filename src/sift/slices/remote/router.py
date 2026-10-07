# SPDX-License-Identifier: AGPL-3.0-or-later
"""The phone as a remote: screens that offer themselves, and commands sent to them.

A tab with an open player or Theater wall says where it stands (`POST .../screens/{screen}`) and
stops offering itself (`DELETE`); the phone lists its user's screens (`GET`), says which one it is
driving (`PUT` and `DELETE .../controllers/{controller}`, which the desk reads back from the list
to say it is being controlled) and sends one of them a command (`POST .../commands`), which travels
down the one live connection the tab already holds. There is no second socket and no pairing code: **pairing is the sign-in**. A
phone signed in as the same user sees that user's screens and nobody else's, and a locked
session does neither, because every route here asks for the signed-in viewer and a locked
session is refused before it gets one.

Nothing here decides what a phone may be told about a file. The list names the file a screen is
playing only when the PHONE's own session may open it, through the same permission layer every
other read goes through; a file its session is keeping in Hidden is said to be Hidden and not
named, whatever the desktop's session has opened.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import Field

from sift.kernel.access import Repository, Viewer
from sift.kernel.audience import Audience
from sift.kernel.changes import About, RemoteAction, RemoteCommand, announce_now
from sift.kernel.ids import new_id
from sift.kernel.reach import conceals
from sift.kernel.wire import Wire
from sift.kernel.wiring import ACCESS, CHANGES, part_of
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.remote import tuning
from sift.slices.remote.screens import (
    SCREENS,
    SURFACES,
    Extras,
    Report,
    Screen,
    Screens,
    refusal,
)

router = APIRouter(prefix="/remote", tags=["remote"])

#: A screen's name: minted by its tab, unguessable in practice, and only ever meaningful beside the
#: user who offered it.
ScreenId = Annotated[str, Path(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]

#: One file, as a screen names it. The same shape every file id in Sift has.
FileId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z]+$")]

Seconds = Annotated[float, Field(ge=0, le=tuning.LONGEST_SECONDS, allow_inf_nan=False)]

#: One entry of a list a screen offers to choose from: a size's name, a layout's id, a preset's name.
Choice = Annotated[str, Field(min_length=1, max_length=80)]

#: What happens at the end of a file, in the desk's own three words for it.
Repeat = Literal["once", "loop_all", "loop_one"]

NO_SCREEN = "Sift couldn't find that screen."
"""What a phone is told about a screen it cannot reach, for every reason in one go.

One sentence for a screen that went quiet, one that was never offered and one belonging to somebody
else, because telling those apart is telling a stranger which ids are real."""

CANNOT_DO = "That screen can't do that."


class ScreenReport(Wire):
    """What a tab says about the player or wall it is offering."""

    #: What the phone calls it: the machine and the surface, in the tab's own words.
    label: str = Field(min_length=1, max_length=80)
    surface: Literal["player", "theater"]
    #: Whether this is Sift's own desktop application rather than a browser tab.
    app: bool
    playing: bool
    position: Seconds
    length: Seconds | None = None
    file: FileId | None = None
    volume: int = Field(ge=0, le=100)
    muted: bool
    #: The commands this screen can answer. Held to its surface's verbs.
    supports: list[RemoteAction] = Field(max_length=len(RemoteAction))
    #: The last command this screen acted on, which is how the phone knows one landed.
    acted_on: str | None = Field(default=None, max_length=64)
    #: A wall's cells, and which of them is being talked to. Zero and null for a player.
    cells: int = Field(default=0, ge=0, le=tuning.MOST_CELLS)
    focused: int | None = Field(default=None, ge=0, lt=tuning.MOST_CELLS)
    #: What the drawer stands at (a wall's: the cell being talked to), so the phone lights the
    #: same controls the desk does. Absent from a screen that has no such control.
    repeat: Repeat | None = None
    shuffle: bool | None = None
    #: How many ends of the A-B loop are marked: none, the start, or both (it is running).
    loop_marks: int = Field(default=0, ge=0, le=2)
    #: The sizes the file comes in, and which is playing. A `player.quality` counts into this.
    qualities: list[Choice] = Field(default_factory=list, max_length=tuning.MOST_CHOICES)
    quality: int | None = Field(default=None, ge=0, lt=tuning.MOST_CHOICES)
    #: The file's favourite and its O counter, where the viewer offers them.
    favorite: bool | None = None
    count: int | None = Field(default=None, ge=0)
    #: How long a wall's cell holds a file before it moves on, in seconds; null for no limit.
    timer: int | None = Field(default=None, ge=0, le=tuning.LONGEST_TIMER_SECONDS)
    #: Whether a wall is talking to every cell together, and whether the cell the bar is drawing
    #: is held or muted on its own (the wall's own hold and silence are `playing` and `muted`).
    every_cell: bool = False
    cell_held: bool | None = None
    cell_muted: bool | None = None
    #: A wall's layouts by id and which is up, and its saved presets by name. What
    #: `theater.layout` and `theater.preset` count into.
    layouts: list[Choice] = Field(default_factory=list, max_length=tuning.MOST_CHOICES)
    layout: int | None = Field(default=None, ge=0, lt=tuning.MOST_CHOICES)
    presets: list[Choice] = Field(default_factory=list, max_length=tuning.MOST_CHOICES)
    #: What each of a wall's cells is showing, in the wall's order; null for an empty cell.
    cell_files: list[FileId | None] = Field(default_factory=list, max_length=tuning.MOST_CELLS)


class CellOut(Wire):
    """One of a wall's cells, as the phone may be told about it: the same rule as the screen's file."""

    file: str | None
    hidden: bool


class ScreenOut(Wire):
    """One of the user's screens, as their phone draws it."""

    screen: str
    label: str
    surface: Literal["player", "theater"]
    app: bool
    playing: bool
    #: Where it has got to NOW, carried on from its last report by this server's own clock. The
    #: phone carries it on from the moment it read this, on its own.
    position: float
    length: float | None
    #: The file, only when this phone's own session may open it. Null otherwise.
    file: str | None
    #: True when the file is one this phone's session is keeping in Hidden. The phone says that
    #: something Hidden is playing, and names nothing.
    hidden: bool
    volume: int
    muted: bool
    supports: list[RemoteAction]
    acted_on: str | None
    cells: int
    focused: int | None
    repeat: Repeat | None
    shuffle: bool | None
    loop_marks: int
    qualities: list[str]
    quality: int | None
    favorite: bool | None
    count: int | None
    timer: int | None
    every_cell: bool
    cell_held: bool | None
    cell_muted: bool | None
    layouts: list[str]
    layout: int | None
    presets: list[str]
    #: Each cell's file, named only where this phone's session may open it, as `file` is.
    cell_files: list[CellOut]
    #: How long ago the screen last spoke, in seconds.
    heard_seconds_ago: float
    #: What the phones driving this screen right now call themselves, the most recently heard
    #: first. The desk reads its own entry to say it is being controlled, and by what.
    controlled_by: list[str] = Field(default_factory=list)


class RemoteScreens(Wire):
    """This user's screens, the most recently heard first."""

    screens: list[ScreenOut]
    #: How long a screen that stops speaking stays listed. A phone re-reads within this.
    listed_for_seconds: float
    #: How many of this user's browser tabs hold a player or a wall and do not offer it, because
    #: that browser's own switch is off. The phone says where the switch is; Sift's desktop
    #: application always offers, so it is never counted here.
    not_offering: int = 0


class CommandBody(Wire):
    """One command for one screen."""

    action: RemoteAction
    value: float | None = Field(
        default=None, ge=-tuning.LONGEST_SECONDS, le=tuning.LONGEST_SECONDS, allow_inf_nan=False
    )


class ControllerIn(Wire):
    """A phone saying it is driving one screen."""

    #: What the desk calls it: the phone's browser and system, in the phone's own words.
    label: str = Field(min_length=1, max_length=80)


class CommandSent(Wire):
    """A command accepted.

    Accepted is not done. Whether the screen heard it is known only from the screen itself, which
    reports this id as `acted_on` once it has acted: a phone that does not see that within a few
    seconds says the screen did not answer. Counting the connections it was handed to would not
    say it: the phone's own connection is one of them.
    """

    #: What the screen reports as `acted_on` once it has done it.
    id: str


def _screens(request: Request) -> Screens:
    return part_of(request, SCREENS)


def _spend(allowance_wait: float) -> None:
    """Refuse with 429 when the allowance is spent. `Retry-After` in whole seconds, at least one."""
    if allowance_wait > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many presses too quickly. Try again in a moment.",
            headers={"Retry-After": str(max(1, round(allowance_wait + 0.5)))},
        )


@router.get("/screens")
async def list_screens(
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    screens: Annotated[Screens, Depends(_screens)],
) -> RemoteScreens:
    """The signed-in user's screens: every open player or wall offering itself to their phone.

    A file is named only when this session may open it; one this session keeps in Hidden is
    marked Hidden and not named.
    """
    access = part_of(request, ACCESS)
    now = screens.now()
    return RemoteScreens(
        screens=[
            await _out(access, viewer, one, now, screens.controlled_by(viewer.id, one.screen_id))
            for one in screens.of(viewer.id)
        ],
        listed_for_seconds=tuning.SCREEN_SECONDS,
        not_offering=screens.not_offering(viewer.id),
    )


async def _out(
    access: Repository, viewer: Viewer, screen: Screen, now: float, controlled_by: list[str]
) -> ScreenOut:
    report = screen.report
    extras = report.extras
    file, hidden = await _what_it_plays(access, viewer, report.file)
    cells: list[CellOut] = []
    for one in extras.cell_files:
        named, concealed = await _what_it_plays(access, viewer, one)
        cells.append(CellOut(file=named, hidden=concealed))
    return ScreenOut(
        screen=screen.screen_id,
        label=report.label,
        surface="theater" if report.surface == "theater" else "player",
        app=report.app,
        playing=report.playing,
        position=round(screen.position_at(now), 3),
        length=report.length,
        file=file,
        hidden=hidden,
        volume=report.volume,
        muted=report.muted,
        supports=sorted(report.supports),
        acted_on=report.acted_on,
        cells=report.cells,
        focused=report.focused,
        repeat=_repeat(extras.repeat),
        shuffle=extras.shuffle,
        loop_marks=extras.loop_marks,
        qualities=list(extras.qualities),
        quality=extras.quality,
        favorite=extras.favorite,
        count=extras.count,
        timer=extras.timer,
        every_cell=extras.every_cell,
        cell_held=extras.cell_held,
        cell_muted=extras.cell_muted,
        layouts=list(extras.layouts),
        layout=extras.layout,
        presets=list(extras.presets),
        cell_files=cells,
        heard_seconds_ago=round(max(0.0, now - screen.heard_at), 1),
        controlled_by=controlled_by,
    )


#: The repeat words the wire carries, each as itself: what a stored report's word is read back by.
_REPEATS: dict[str, Repeat] = {"once": "once", "loop_all": "loop_all", "loop_one": "loop_one"}


def _repeat(said: str | None) -> Repeat | None:
    """The repeat word as the wire spells it, from the one the report was validated against."""
    return None if said is None else _REPEATS.get(said)


async def _what_it_plays(
    access: Repository, viewer: Viewer, file: str | None
) -> tuple[str | None, bool]:
    """The file to name, and whether it is Hidden, asked as the PHONE's session.

    Through `open_asset`, the same door the player itself goes through, so the phone is told
    exactly what it could open by pressing on it and nothing more. The desktop's session may have
    opened Hidden and the phone's may not have: that is the case this exists for, and the answer is
    the phone's. A file the phone cannot reach for any other reason is simply not named.
    """
    if file is None:
        return None, False
    if await access.open_asset(viewer, file) is not None:
        return file, False
    return None, await conceals(access, viewer, file)


@router.post("/screens/{screen}", status_code=status.HTTP_204_NO_CONTENT)
async def report_screen(
    screen: ScreenId,
    body: ScreenReport,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    screens: Annotated[Screens, Depends(_screens)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Say where one open player or wall stands, offering it to this user's phone.

    Sent whenever anything changes and every few seconds while nothing does; a screen that stops
    sending is let go of within half a minute.
    """
    _spend(screens.reports.spend(viewer.id))
    offered = frozenset(body.supports)
    if not offered <= SURFACES[body.surface]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, CANNOT_DO)
    if body.focused is not None and body.focused >= body.cells:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That cell isn't on the wall.")
    if len(body.cell_files) > body.cells:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That cell isn't on the wall.")
    for chosen, among in ((body.quality, body.qualities), (body.layout, body.layouts)):
        if chosen is not None and chosen >= len(among):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That isn't one it offers.")
    report = Report(
        label=body.label,
        surface=body.surface,
        app=body.app,
        playing=body.playing,
        position=body.position,
        length=body.length,
        file=body.file,
        volume=body.volume,
        muted=body.muted,
        supports=offered,
        acted_on=body.acted_on,
        cells=body.cells,
        focused=body.focused,
        extras=Extras(
            repeat=body.repeat,
            shuffle=body.shuffle,
            loop_marks=body.loop_marks,
            qualities=tuple(body.qualities),
            quality=body.quality,
            favorite=body.favorite,
            count=body.count,
            timer=body.timer,
            every_cell=body.every_cell,
            cell_held=body.cell_held,
            cell_muted=body.cell_muted,
            layouts=tuple(body.layouts),
            layout=body.layout,
            presets=tuple(body.presets),
            cell_files=tuple(body.cell_files),
        ),
    )
    if screens.report(viewer.id, screen, report):
        announce_now(Audience.of_user(viewer.id), About.SCREENS)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/screens/{screen}", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw_screen(
    screen: ScreenId,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    screens: Annotated[Screens, Depends(_screens)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Stop offering one screen: the player closed, or the offer was switched off.

    Scoped to the asker, so an id that is not theirs is a no-op and answered the same way.
    """
    if screens.withdraw(viewer.id, screen):
        announce_now(Audience.of_user(viewer.id), About.SCREENS)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/quiet/{screen}", status_code=status.HTTP_204_NO_CONTENT)
async def keep_quiet(
    screen: ScreenId,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    screens: Annotated[Screens, Depends(_screens)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Say this browser tab holds a player or a wall that it does not offer to the phone.

    Only that it exists, and nothing about what it plays: its own switch is off. Sent while the
    player or wall is open, every few seconds, so the phone can say where the switch is; a tab that
    stops sending stops counting within half a minute.
    """
    _spend(screens.reports.spend(viewer.id))
    if screens.keep_quiet(viewer.id, screen):
        announce_now(Audience.of_user(viewer.id), About.SCREENS)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/quiet/{screen}", status_code=status.HTTP_204_NO_CONTENT)
async def speak_up(
    screen: ScreenId,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    screens: Annotated[Screens, Depends(_screens)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """This tab no longer holds anything back: its player closed, or its switch went on.

    Scoped to the asker, so an id that is not theirs is a no-op and answered the same way.
    """
    if screens.speak_up(viewer.id, screen):
        announce_now(Audience.of_user(viewer.id), About.SCREENS)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/screens/{screen}/controllers/{controller}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": NO_SCREEN}},
)
async def hold_screen(
    screen: ScreenId,
    controller: ScreenId,
    body: ControllerIn,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    screens: Annotated[Screens, Depends(_screens)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Say this phone is driving one of this user's screens, so the desk can say so.

    Sent when the phone picks the screen and again every few seconds while it stays on it; a
    phone that stops sending is let go of within half a minute. Answered 404 for a screen that is
    not this user's, has gone quiet, or was never offered, alike.
    """
    _spend(screens.reports.spend(viewer.id))
    news = screens.hold(viewer.id, screen, controller, body.label)
    if news is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SCREEN)
    if news:
        announce_now(Audience.of_user(viewer.id), About.SCREENS)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/screens/{screen}/controllers/{controller}", status_code=status.HTTP_204_NO_CONTENT)
async def let_go_of_screen(
    screen: ScreenId,
    controller: ScreenId,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    screens: Annotated[Screens, Depends(_screens)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """This phone has stopped driving a screen: it left the Remote, or picked another screen.

    Scoped to the asker, so an id that is not theirs is a no-op and answered the same way.
    """
    if screens.let_go(viewer.id, screen, controller):
        announce_now(Audience.of_user(viewer.id), About.SCREENS)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/screens/{screen}/commands",
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"description": NO_SCREEN}, 409: {"description": CANNOT_DO}},
)
async def send_command(
    screen: ScreenId,
    body: CommandBody,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    screens: Annotated[Screens, Depends(_screens)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> CommandSent:
    """Ask one of this user's screens to do something: pause, seek, the next file.

    Answered 404 for a screen that is not this user's, has gone quiet, or was never offered,
    alike. 409 for a command the screen did not say it can do.
    """
    _spend(screens.commands.spend(viewer.id))
    target = screens.find(viewer.id, screen)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SCREEN)
    if body.action not in target.report.supports:
        raise HTTPException(status.HTTP_409_CONFLICT, CANNOT_DO)
    why = refusal(body.action, body.value, target)
    if why is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, why)
    command = RemoteCommand(id=new_id(), screen=screen, action=body.action, value=body.value)
    part_of(request, CHANGES).publish_command(viewer.id, command)
    return CommandSent(id=command.id)
