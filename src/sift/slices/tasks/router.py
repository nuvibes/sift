# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tasks: every one of them with its When, and the one door for running one.

Two routes, both admin-only. A task's When and quiet hours are ordinary settings and are written
through the settings route: the row IS the setting's own row, so there is no second path to the
stored value. What this adds is the read the Tasks screen and each owning pane draw from, and Run
now | Run during quiet hours, which every other button that starts the same work is meant to point at.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ConfigDict, Field

from sift.kernel.access import Viewer
from sift.kernel.jobs import unlisted_job_types
from sift.kernel.jobs.schedules import get_schedule
from sift.kernel.settings_registry import get_registered
from sift.kernel.wire import Wire
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.tasks.parts import DryReport, NotAPart
from sift.slices.tasks.service import (
    SERVICE,
    LastRun,
    TaskRefused,
    TasksService,
    TaskState,
    UnknownTask,
)
from sift.slices.tasks.settings import KEEP_AWAKE_KEY

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _service(request: Request) -> TasksService:
    return part_of(request, SERVICE)


class PlanLineView(Wire):
    """One part of a dry run's plan."""

    label: str = Field(description="What the part is called.")
    count: int = Field(description="How many it would do.")


class DryReportView(Wire):
    """What a dry run said, as fields the row lays out."""

    headline: str = Field(description="What it would do, in one sentence.")
    parts: list[PlanLineView] = Field(description="How many each part would do, in order.")
    named: str = Field(description="What `names` are: the first files, or what it would delete.")
    names: list[str] = Field(description="The first of them by name; hidden files are never named.")
    more: int = Field(description="How many more there are than the names.")
    cannot: list[str] = Field(description="Why a part cannot run on this device now, one each.")


class LastRunView(Wire):
    """How a task's last run ended."""

    ended_at: int = Field(description="When it ended, in seconds since the epoch.")
    outcome: str = Field(description="done, failed or canceled.")
    seconds: int | None = Field(description="How long it took, or null where that is not known.")
    said: str | None = Field(
        description="What it did in its own sentence, or why it failed; null where it said nothing."
    )
    report: DryReportView | None = Field(
        description="A dry run's report as fields; null for any other run."
    )


class WhenChoice(Wire):
    """One answer a task's When can take."""

    value: str = Field(description="work, quiet or press: what is stored.")
    label: str = Field(description="What it's called on screen.")


class SwitchedOff(Wire):
    """The switch a task's feature is turned off by, so the row can say where to turn it on."""

    key: str = Field(description="The setting that turns the feature on.")
    section: str = Field(description="The settings section that setting is filed under.")


class PartView(Wire):
    """One part of a task that can run on its own: a sub-task, or a library folder."""

    key: str = Field(description="What a press names it by.")
    label: str = Field(description="What it's called on screen.")
    path: str = Field(description="For a library folder, where it is on this device; else empty.")


class TaskView(Wire):
    """One task, as the Tasks screen draws its row and an owning pane draws its When."""

    id: str = Field(description="The task's address, and what Run now names.")
    title: str
    explain: str
    press: str = Field(description="What its press says: Run now, or a stage's own verb.")
    reads: list[str] = Field(
        description="For a stage that is several tasks' work, the tasks its When reads; else empty."
    )
    when: str = Field(description="work, quiet or press; mixed for a stage whose tasks disagree.")
    when_key: str = Field(description="The setting that holds the When; written through settings.")
    whens: list[WhenChoice] = Field(description="The Whens this task offers, in reading order.")
    on: bool = Field(description="Whether it starts on its own at all.")
    cadence: str = Field(description="When it runs, in words.")
    setting_keys: list[str] = Field(
        description="Settings that decide what it does and how often, drawn beside it."
    )
    drawn_keys: list[str] = Field(
        description=(
            "Of `setting_keys`, those that mean something under its When now, in order: the ones "
            "drawn beside it."
        )
    )
    set_in: str = Field(description="The settings section that owns it, or empty.")
    last: LastRunView | None = Field(description="How its last run ended, or null if none has.")
    next_run: int | None = Field(
        description="When it next starts on its own, or null: press-only, or waiting for work."
    )
    waiting: int = Field(description="Its work not started yet, counted in `unit`.")
    unit: str = Field(description="What one of `waiting` is, said once: file, or run.")
    units: str = Field(description="The same, said of several: files, or runs.")
    held: int = Field(description="Of that, the work held to quiet hours.")
    running: bool = Field(description="Whether any of its work is running this moment.")
    off: SwitchedOff | None = Field(
        description="The switch its feature is turned off by, or null when nothing is switched off."
    )
    parts: list[PartView] = Field(
        description="Its sub-tasks a press may run on their own, in order; empty when it has none."
    )
    locations: bool = Field(
        description="Whether a press may run it for some of the library folders (`folders`)."
    )
    dry: bool = Field(description="Whether it has a dry run: a pass that changes nothing.")
    dry_run: LastRunView | None = Field(
        description="How its last dry run ended, its report as what it said; null if none has."
    )
    dry_running: bool = Field(description="Whether a dry run of it is queued or working now.")


class QuietHoursView(Wire):
    """The install's one range, as it stands now."""

    starts: str = Field(description="HH:MM, on this device's clock.")
    ends: str = Field(description="HH:MM. Earlier than the start runs past midnight.")
    open: bool
    opens_at: int = Field(description="When it next opens; now while it is open.")
    closes_at: int | None = Field(description="When it next closes; null for the whole day.")


class Tasks(Wire):
    """Every task, with quiet hours at the top."""

    quiet_hours: QuietHoursView
    keep_awake: bool = Field(description="Whether this device is kept awake for quiet hours.")
    awake_now: bool = Field(description="Whether that request stands this moment.")
    tasks: list[TaskView] = Field(
        description="Every task a settings pane draws. Upkeep nobody times is left out."
    )
    folders: list[PartView] = Field(
        description="The library folders a task that runs over some of them may be pressed for."
    )


class RunTask(Wire):
    """Run a task now, or when quiet hours open."""

    model_config = ConfigDict(extra="forbid")

    at: Literal["now", "quiet"] = "now"
    parts: list[str] | None = Field(
        default=None,
        max_length=64,
        description="Run only these of the task's parts, by key; absent runs every part.",
    )
    locations: list[str] | None = Field(
        default=None,
        max_length=256,
        description="Run it only for these library folders, by id; absent runs it for all.",
    )
    dry: bool = Field(
        default=False,
        description="Work out what the run would do and report it, changing nothing.",
    )


class TaskStarted(Wire):
    """What one press queued."""

    job_ids: list[str] = Field(description="The work queued; empty when there was nothing to do.")
    starts_at: int | None = Field(
        description=(
            "For Run during quiet hours, when the range opens; null when the work it landed on is not "
            "waiting for the range (already running, or pulled forward by a Run now)."
        )
    )
    waits: bool = Field(
        default=False,
        description=(
            "For Run during quiet hours, whether the work waits for the range to open; false when "
            "the range is open and it starts at once. Said here because only this device's clock "
            "can say it: a browser comparing `starts_at` with its own clock is wrong by however "
            "far the two clocks are apart."
        ),
    )
    named: str | None = Field(
        default=None,
        description="The parts and folders a part run was for, in words; null for the whole task.",
    )
    dry: bool = Field(default=False, description="Whether what was queued is a dry run.")
    on_activity: bool = Field(
        default=True,
        description=(
            "Whether the work it queued is drawn on Activity's list; false for upkeep (the "
            "backup), whose run is read on the task's own row."
        ),
    )


def _switched_off(state: TaskState) -> SwitchedOff | None:
    """Where the switch that turned a task's feature off is, or None while it is on."""
    declared = get_registered(state.task.switch) if state.switched_off else None
    return None if declared is None else SwitchedOff(key=declared.key, section=declared.section)


def _last_view(last: LastRun | None) -> LastRunView | None:
    return (
        None
        if last is None
        else LastRunView(
            ended_at=last.ended_at,
            outcome=last.outcome,
            seconds=last.seconds,
            said=last.said,
            report=_report_view(last.report),
        )
    )


def _report_view(report: DryReport | None) -> DryReportView | None:
    if report is None:
        return None
    return DryReportView(
        headline=report.headline,
        parts=[PlanLineView(label=one.label, count=one.count) for one in report.lines],
        named=report.named,
        names=list(report.names),
        more=report.more,
        cannot=list(report.refusals),
    )


def _parts_of(service: TasksService, task_id: str) -> list[PartView]:
    return [
        PartView(key=one.key, label=one.label, path=one.path)
        for one in service.parts_of(task_id).subtasks
    ]


@router.get("", response_model=Tasks)
async def read_tasks(
    viewer: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[TasksService, Depends(_service)],
) -> Tasks:
    """Every task a settings pane draws, when it runs, how its last run ended and when next.

    A task declared unshown (the prunes, the update check) runs and is recorded like any other and
    is left out here, so no pane can draw a When for it.
    """
    quiet = await service.quiet_hours()
    states = await service.states(viewer)
    return Tasks(
        quiet_hours=QuietHoursView(
            starts=quiet.starts,
            ends=quiet.ends,
            open=quiet.open,
            opens_at=quiet.opens_at,
            closes_at=quiet.closes_at,
        ),
        keep_awake=bool(await service.setting(KEEP_AWAKE_KEY)),
        awake_now=service.awake_now,
        tasks=[
            TaskView(
                id=state.task.id,
                title=state.task.title,
                explain=state.task.explain,
                press=state.task.press,
                reads=list(state.task.reads),
                when=state.when,
                when_key=state.task.when_key,
                whens=[WhenChoice(value=one, label=label) for one, label in state.labels.items()],
                on=state.on,
                cadence=state.cadence,
                setting_keys=list(state.task.setting_keys),
                drawn_keys=list(state.drawn_keys),
                set_in=state.task.set_in,
                last=_last_view(state.last),
                next_run=state.next_run,
                waiting=state.waiting,
                unit=state.task.unit[0],
                units=state.task.unit[1],
                held=state.held,
                running=state.running,
                off=_switched_off(state),
                parts=_parts_of(service, state.task.id),
                locations=service.parts_of(state.task.id).locations,
                dry=service.rehearses(state.task.id),
                dry_run=_last_view(state.dry_run),
                dry_running=state.dry_running,
            )
            for state in states
            if state.task.shown
        ],
        folders=[
            PartView(key=one.key, label=one.label, path=one.path) for one in await service.folders()
        ],
    )


@router.post(
    "/{task_id}/run",
    response_model=TaskStarted,
    dependencies=[Depends(csrf_protect)],
)
async def run_task(
    task_id: str,
    body: RunTask,
    viewer: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[TasksService, Depends(_service)],
) -> TaskStarted:
    """Run a task now, or queue it for when quiet hours open.

    A press always runs: the task's When and every switch over its work answer whether it starts
    on its OWN, and somebody pressing this has just answered that for themselves. Run during quiet hours
    waits for the range and pauses with it, whatever the task's When is. A second press queues a
    second run, which is what pressing Run now twice means.

    `parts` and `locations` run part of it, each checked against what the task declares (a 400
    names what is wrong); `dry` queues its dry run instead, which changes nothing.
    """
    try:
        only = await service.selection(task_id, body.parts, body.locations)
        ids, starts = await service.run(task_id, at=body.at, viewer=viewer, only=only, dry=body.dry)
    except UnknownTask as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no task by that name.") from exc
    except NotAPart as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except TaskRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    task = get_schedule(task_id)
    return TaskStarted(
        job_ids=ids,
        starts_at=starts,
        waits=starts is not None and not (await service.quiet_hours()).open,
        named=await service.described(task_id, only),
        dry=body.dry,
        on_activity=task is None or task.job_type not in unlisted_job_types(),
    )
