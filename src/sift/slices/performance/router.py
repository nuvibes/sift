# SPDX-License-Identifier: AGPL-3.0-or-later
"""Three endpoints: start the self-test, read what it found, and describe the machine.

Admin-only. It works the machine hard for about a minute and it hands back recommendations for
instance-wide settings; neither is a guest's business, and hiding the panel in the client is a
courtesy rather than the control.

Starting is separate from reading because the test takes far longer than a request should. The last
run is kept per hardware profile (`rates`), so a changed machine is offered nothing stale.

Nothing here writes a setting. A recommendation names a setting and the value it should have;
applying it goes through the ordinary settings route, with its validation and its History line.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sift.kernel import lifecycle
from sift.kernel.access import Viewer
from sift.kernel.jobs.ledger import report_text
from sift.kernel.log import get_logger
from sift.kernel.machine_acts import record_act
from sift.kernel.ml import accel
from sift.kernel.wiring import (
    DATABASE,
    HARDWARE,
    LEDGER,
    QUEUE,
    SETTINGS,
    SETTINGS_HUB,
    part_of,
)
from sift.slices import performance
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.performance import selftest
from sift.slices.performance.benchmark import FIRST_BENCHMARK, HELD
from sift.slices.performance.jobs import ACCEL_INSTALL
from sift.slices.performance.models import (
    AcceleratorTestView,
    AcceleratorView,
    BenchmarkChangeView,
    DecodeView,
    FirstBenchmarkView,
    GpuCard,
    HardwareView,
    LevelView,
    MeasurementView,
    RecommendationView,
    RestartView,
    RunReportView,
    SelfTestView,
    StorageCurveView,
    StorageLevelView,
)
from sift.slices.performance.runner import SELF_TEST_RUNNER, current_settings

log = get_logger(__name__)

router = APIRouter(tags=["performance"])


def _view(
    state: selftest.SelfTest,
    *,
    current: dict[str, int],
    rounds: int,
    measured: bool,
) -> SelfTestView:
    """The run, in the shape the client depends on.

    Built field by field rather than splatted from the dataclass, so a field reaches the wire only
    when it is added here.

    `current` is read now, not when the test ran, so an applied recommendation reads as one that
    changes nothing and the screen stops offering it.
    """
    measurement = state.measurement
    return SelfTestView(
        running=state.running,
        finished=state.finished_at is not None,
        measured=measured,
        rounds=rounds,
        share_reads_now=performance.resolve_share_reads(
            current.get(performance.SHARE_READS_KEY, 0)
        ),
        measurement=None
        if measurement is None
        else MeasurementView(
            cores=measurement.cores,
            failed=measurement.failed,
            levels=[
                LevelView(
                    at_once=level.at_once,
                    seconds=round(level.seconds, 2),
                    finished=level.finished,
                    per_second=round(level.throughput, 3),
                    responsive=level.responsive,
                )
                for level in measurement.levels
            ],
            storages=[
                StorageCurveView(
                    storage=curve.storage,
                    label=curve.label,
                    remote=curve.remote,
                    failed=curve.failed,
                    best_at_once=curve.best.at_once if curve.best is not None else None,
                    levels=[
                        StorageLevelView(
                            at_once=level.at_once,
                            seconds=round(level.seconds, 2),
                            megabytes=round(level.bytes_read / 1_000_000, 1),
                            megabytes_per_second=round(level.megabytes_per_second, 1),
                        )
                        for level in curve.levels
                    ],
                )
                for curve in measurement.storages
            ],
            decode=None
            if measurement.decode is None
            else DecodeView(
                frames_per_second=round(measurement.decode.frames_per_second, 1),
                seek_seconds=round(measurement.decode.seek_seconds, 4),
            ),
        ),
        recommendations=[
            RecommendationView(
                key=one.key,
                label=one.label,
                current=current.get(one.key, one.current),
                suggested=one.suggested,
                reason=one.reason,
                changes_anything=current.get(one.key, one.current) != one.suggested,
            )
            for one in state.recommendations
        ],
    )


def _state(request: Request) -> selftest.SelfTest:
    """The one run there may be at a time: the runner's, which the Build shares."""
    return part_of(request, SELF_TEST_RUNNER).state


async def _current_settings(request: Request) -> dict[str, int]:
    """What the settings say now. See `runner.current_settings` for why the hub and not a resolver."""
    return await current_settings(part_of(request, SETTINGS_HUB))


def _rounds(request: Request) -> int:
    """How many rounds a run on THIS machine can reach. See `planned_levels`."""
    return len(selftest.planned_levels(part_of(request, HARDWARE).cpu_count))


@router.post(
    "/performance/self-test", response_model=SelfTestView, status_code=status.HTTP_202_ACCEPTED
)
async def start(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> SelfTestView:
    """Begin measuring. Answers straight away; the result is read back below.

    A second request while one is running is not an error and does not start a second test: it
    answers with the run already in flight. Two of these at once would measure each other.
    """
    runner = part_of(request, SELF_TEST_RUNNER)
    runner.start()
    return _view(
        runner.state,
        current=await _current_settings(request),
        rounds=_rounds(request),
        measured=await runner.measured(),
    )


@router.get("/performance/self-test", response_model=SelfTestView)
async def result(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> SelfTestView:
    """What the last run found, or that one is still going. Never blocks on the run.

    After a restart, the last run kept for this hardware. See `SelfTestRunner.recall`.
    """
    await part_of(request, SELF_TEST_RUNNER).recall()
    return _view(
        _state(request),
        current=await _current_settings(request),
        rounds=_rounds(request),
        measured=await part_of(request, SELF_TEST_RUNNER).measured(),
    )


@router.get("/performance/benchmark", response_model=FirstBenchmarkView)
async def first_benchmark(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> FirstBenchmarkView:
    """The benchmark Sift runs by itself when the first library folder is added, if one was.

    What every admin window's toasts read on the jobs bell (`benchmark.FirstBenchmark`): one
    in-memory read and the one point read `measured` costs, so following the bell is cheap.
    """
    run = part_of(request, FIRST_BENCHMARK).run
    measured = await part_of(request, SELF_TEST_RUNNER).measured()
    if run is None:
        return FirstBenchmarkView(state="none", measured=measured)
    return FirstBenchmarkView(
        state=run.state,
        job_id=run.job_id,
        said=run.said,
        changes=[
            BenchmarkChangeView(key=one.key, label=one.label, before=one.before, after=one.after)
            for one in run.changes
        ],
        measured=measured,
        held=HELD if run.going else None,
    )


@router.get("/performance/runs/{run_id}/report", response_model=RunReportView)
async def run_report(
    request: Request,
    run_id: str,
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RunReportView:
    """One run as the block of plain text a person copies and passes on.

    Made on the server rather than assembled by the screen, so a pasted report is the same words
    whichever screen it was copied from, and so the words can be tested once.
    """
    _ = viewer
    run = await part_of(request, LEDGER).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no run with that id")
    return RunReportView(text=report_text(run))


@router.get("/performance/hardware", response_model=HardwareView)
async def hardware(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> HardwareView:
    """What this machine turned out to be, as the startup probe found it.

    Its own route rather than a wider `/health`: `/health` answers anyone who can reach the port,
    and the processor and graphics card identify a machine. This route is admin-only.
    """
    report = part_of(request, HARDWARE)
    return HardwareView(
        cpu_count=report.cpu_count,
        cpu_model=report.cpu_model,
        total_ram_bytes=report.total_ram_bytes,
        worker_concurrency=report.worker_concurrency,
        gpu_name=report.gpu_name,
        gpu_driver=report.gpu_driver,
        gpu_vram_bytes=report.gpu_vram_bytes,
        gpu_cards=[
            GpuCard(name=one.name, vram_bytes=one.vram_bytes, can_compute=one.can_compute)
            for one in report.gpu_cards
        ],
        installed_ram_bytes=report.installed_ram_bytes,
        cuda=report.cuda,
        rocm=report.rocm,
        transcode_encoders=list(report.transcode_encoders),
        warnings=list(report.warnings),
    )


# --- the graphics card ---------------------------------------------------------------------------


def _accelerator(request: Request, job_id: str | None = None) -> AcceleratorView:
    report = part_of(request, HARDWARE)
    settings = part_of(request, SETTINGS)
    return AcceleratorView(
        card=report.gpu_name,
        installed=accel.installed(settings),
        # Asked only where there is a card, because the question costs an import of the inference
        # runtime and a machine with no card has no use for the answer.
        already_capable=report.cuda and not accel.installed(settings) and accel.already_capable(),
        supported=report.cuda,
        download_bytes=accel.TOTAL_BYTES,
        peak_bytes=accel.PEAK_BYTES,
        version=accel.PIN,
        job_id=job_id,
        restart_needed=accel.installed(settings) and accel.restart_needed(settings),
    )


@router.get("/performance/accelerator", response_model=AcceleratorView)
async def read_accelerator(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> AcceleratorView:
    """Whether the card can be used, and what it would take to make it usable.

    Answers on every machine, including one with no card at all. A screen that could not ask would
    have to infer it from the two device settings being refused, which is how somebody ends up
    believing their card is broken.
    """
    return _accelerator(request)


@router.post("/performance/accelerator", dependencies=[Depends(csrf_protect)])
async def install_accelerator(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> AcceleratorView:
    """Start fetching the card's runtime. Hands back the job doing it.

    Refused where the machine has no card Sift can drive: downloading a gigabyte of libraries for
    hardware that is not there is the one outcome nobody would want, and it is the outcome a button
    that always worked would eventually produce.
    """
    report = part_of(request, HARDWARE)
    if not report.cuda:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Sift can't see a graphics card it knows how to use on this machine, so there is "
            "nothing for this to be installed against. An NVIDIA card with a working driver is "
            "what it needs.",
        )
    settings = part_of(request, SETTINGS)
    if accel.installed(settings):
        return _accelerator(request)
    if accel.already_capable():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This machine already has a runtime that can drive the card, so there's nothing to "
            "download. Sift won't replace it: which version is installed there is your "
            "decision, not Sift's.",
        )
    # The room it needs, checked before a byte comes down. The wheels and what they unpack to
    # are both on the disk while the last one is unpacked; a download that fills the disk three
    # quarters of the way through is the worst of the outcomes, and the cheapest to prevent.
    free = (await asyncio.to_thread(shutil.disk_usage, settings.data_dir)).free
    if free < accel.PEAK_BYTES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Installing this needs about {accel.PEAK_BYTES / 1_000_000_000:.1f} GB free on the "
            f"drive Sift keeps its data on while it installs, and there is "
            f"{free / 1_000_000_000:.1f} GB. Free some room and try again.",
        )
    queue = part_of(request, QUEUE)
    job_id = await queue.enqueue(ACCEL_INSTALL, {})
    log.info("performance.accel.requested", job_id=job_id)
    return _accelerator(request, job_id=job_id)


@router.post("/performance/accelerator/test", dependencies=[Depends(csrf_protect)])
async def test_accelerator(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> AcceleratorTestView:
    """Load a model onto the card and run it, in a process of its own.

    Not "are the providers listed": that question has a reassuring answer on a machine where every
    call would fail. This one really runs something. The separate process is because a CUDA context
    that fails once is dead for the whole process afterwards, silently, so the question cannot
    safely be asked in the process that serves requests.
    """
    problem = await accel.works(part_of(request, SETTINGS))
    return AcceleratorTestView(works=problem is None, problem=problem)


@router.delete("/performance/accelerator", dependencies=[Depends(csrf_protect)])
async def remove_accelerator(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> AcceleratorView:
    """Delete it. Over a gigabyte somebody may want back is a gigabyte they can see and remove."""
    settings = part_of(request, SETTINGS)
    await asyncio.to_thread(accel.remove, settings)
    return _accelerator(request)


@router.post(
    "/performance/restart",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def restart_server(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    device: Annotated[str | None, Query(max_length=256)] = None,
) -> RestartView:
    """Stop this backend cleanly so that whatever started it starts it again.

    IT RESTARTS THE COMPUTER RUNNING THE LIBRARY, whichever one the person asking is sitting at.
    That is the point of it being here rather than in the desktop shell: the process that has to
    start again is the one the graphics-card runtime is loaded into, and somebody looking at those
    settings may be in a browser on another machine or in a second copy of Sift in client mode. A
    button that restarted the application in front of them would restart the wrong computer, say it
    had worked, and leave the card exactly as it was.

    ADMIN-ONLY, and it takes nothing but the name of the window's own computer (`device`), for the
    line History keeps of it (`machine_acts.record_act`). There is no argument to get wrong: it is
    the switch a person standing at the machine could throw by closing the window, offered to the
    person who is allowed to administer the library and to nobody else.

    Answered 202 rather than 200 and rather than nothing at all. The work has been ARRANGED, not
    done: the server finishes what it is serving first, and this very response is part of that.
    A screen that got a 200 would be entitled to think the restart had already happened.
    """
    if not lifecycle.ask_to_restart():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Nothing is watching this copy of Sift, so stopping it would leave it stopped. "
            "Restart it the way it was started.",
        )
    log.info("performance.restart_asked")
    # Written while this request is still being served, which the restart waits for.
    await record_act(part_of(request, DATABASE), admin, "restarted", device)
    return RestartView(restarting=True)
