# SPDX-License-Identifier: AGPL-3.0-or-later
"""The background work; each job checks the switch first and quietly stops when it is off."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel import changes
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About
from sift.kernel.jobs import registered_handlers
from sift.kernel.jobs.families import AGAIN
from sift.kernel.jobs.queue import JobBlocked, JobCanceled, WaitingForPassword
from sift.kernel.ml.weights import WeightError
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.slices.faces import jobs as face_jobs
from sift.slices.faces import service as face_service
from sift.slices.faces.folder_import import FACE_FOLDER_IMPORT, STAGED_PREFIX
from sift.slices.faces.models import Depth, ScanStatus
from sift.slices.faces.references import PersonReport
from sift.testing.logs import uncached_log

pytestmark = pytest.mark.unit


@dataclass
class Recording:
    """Stands in for the whole feature, recording what it was asked to do."""

    on: bool = True
    asked: list[tuple[str, Any]] = field(default_factory=list)
    #: The files the pass was asked whether a held entry of facial fingerprints matches.
    asked_fingerprints: list[str] = field(default_factory=list)
    #: What a scan reports finding. The default leaves nothing unclaimed, so a test that cares
    #: about the follow-up work has to say so.
    status: ScanStatus = ScanStatus.NO_FACES
    #: How many faces a re-match claimed.
    attributed: int = 3
    #: Running byte counts, chunk by chunk; ten so a stop part-way is distinguishable from the end.
    chunks: tuple[int, ...] = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
    expected: int = 100
    stopped_at: int | None = None
    #: How many turns pass before the first byte. See `install_models`.
    warmup: int = 0

    #: What `weights_problem` answers. A sentence means the models this install is set to use are
    #: not on disk, which is a wait rather than a failure.
    missing_models: str | None = None

    async def enabled(self) -> bool:
        return self.on

    async def weights_problem(self, *, run: str | None = None) -> str | None:
        # Kept out of `asked`, beside `runs` and for the same reason: a test about what gets
        # scanned does not have to mention it.
        self.asked_about_weights.append(run)
        return self.missing_models

    #: The run each weights check was made for, in order.
    asked_about_weights: list[str | None] = field(default_factory=list)

    async def scan(
        self,
        asset_id: str,
        *,
        depth: Depth | None = None,
        run: str | None = None,
        again: bool = False,
    ) -> ScanStatus:
        self.asked.append(("scan", (asset_id, depth)))
        self.runs.append(run)
        self.agains.append(again)
        return self.status

    #: Whether each scan was told it was a press ("look again"), in order. Beside `asked` for the
    #: reason `runs` is.
    agains: list[bool] = field(default_factory=list)

    #: The run each scan was told it belonged to, in order. Kept beside `asked` rather than in it
    #: so that every test about what gets scanned reads the same as it did.
    runs: list[str | None] = field(default_factory=list)
    #: The runs whose tuning was written down before anything was queued under them.
    started: list[str] = field(default_factory=list)

    async def start_run(self, run_id: str) -> None:
        self.started.append(run_id)

    #: What `needs_scanning_page` hands back, page by page, and the library total it reports.
    pages: tuple[tuple[str, ...], ...] = ()
    total: int = 0
    #: What it hands back when the sweep says to ignore what has already been looked at.
    forced_pages: tuple[tuple[str, ...], ...] = ()
    #: None makes `viewer_for` answer "that user has gone".
    viewer: Any = "viewer-1"

    #: Rows the access layer hands back at a time, None for no cap.
    cap: int | None = None

    async def viewer_for(self, user_id: str) -> Any:
        self.asked.append(("viewer_for", user_id))
        return SimpleNamespace(id=self.viewer) if self.viewer is not None else None

    async def needs_scanning_page(
        self, viewer: Any, *, offset: int, limit: int, force: bool = False
    ) -> Any:
        self.asked.append(("needs_scanning_page", (offset, limit, force)))
        # What a page actually walks: the smaller of what was asked for and what the layer will
        # give, and never past the end of the library.
        allowed = limit if self.cap is None else min(limit, self.cap)
        walked = max(0, min(allowed, self.total - offset))
        pages = self.forced_pages if force else self.pages
        index = offset // walked if walked else 0
        page = pages[index] if index < len(pages) else ()
        return list(page), self.total, walked

    async def fingerprints_match_file(self, asset_id: str) -> bool:
        """Whether a face in this file matches a held entry of facial fingerprints: none here."""
        self.asked_fingerprints.append(asset_id)
        return False

    async def rematch(self) -> int:
        self.asked.append(("rematch", None))
        return self.attributed

    async def regroup(self, *, full: bool = False) -> int:
        self.asked.append(("regroup", full))
        return 2

    #: What each page of measuring again reports is left, in order. Empty answers nothing left.
    left: list[int] = field(default_factory=list)
    #: How many files another model described, as the download's end asks before queueing the
    #: pass that measures them again.
    described_by_another: int = 0

    async def measured_by_another_model(self) -> int:
        return self.described_by_another

    #: The files a lower size floor can change, in id order, as `under_an_earlier_floor` pages them.
    floor_band: tuple[str, ...] = ()

    #: HEIF stills read from one tile, as `read_from_a_tile` walks them: pages of (tiles, last id).
    tile_pages: tuple[tuple[tuple[str, ...], str], ...] = ()

    async def read_from_a_tile(self, *, after: str = "", limit: int) -> tuple[list[str], str]:
        walked = [page for page in self.tile_pages if page[1] > after]
        if not walked:
            return [], ""
        tiles, last = walked[0]
        return list(tiles), last

    async def under_an_earlier_floor(self, *, after: str = "", limit: int) -> list[str]:
        self.asked.append(("under_an_earlier_floor", (after, limit)))
        return [one for one in self.floor_band if one > after][:limit]

    async def remeasure(self, *, limit: int) -> Any:
        self.asked.append(("remeasure", limit))
        return SimpleNamespace(
            files=2, references=1, remaining=self.left.pop(0) if self.left else 0
        )

    async def install_models(self, *, progress=None, session_factory=None, force=False):  # type: ignore[no-untyped-def]
        """Stands in for the download, driving the callback in several calls as a transfer would."""
        self.asked.append(("install_models", "force" if force else None))
        # Time passing before the first byte arrives: a connection being opened, a redirect being
        # followed. The ticker runs during it and has nothing yet to report.
        for _ in range(self.warmup):
            await asyncio.sleep(0)
        for written in self.chunks:
            if progress is not None and not progress(written, self.expected):
                self.stopped_at = written
                return []
            await asyncio.sleep(0)
        return ["accurate.detector"]


@dataclass
class RecordingQueue:
    """Stands in for the queue, recording what was asked for and which way: whole-library work
    must go through `enqueue_when_settled`."""

    queued: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    #: The wait each settled request asked for, in order; None leaves the queue's own.
    delays: list[int | None] = field(default_factory=list)

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        **_options: Any,
    ) -> str:
        self.queued.append(("enqueue", job_type, payload or {}))
        return f"job-{len(self.queued)}"

    async def enqueue_when_settled(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        **_options: Any,
    ) -> str:
        self.queued.append(("enqueue_when_settled", job_type, payload or {}))
        self.delays.append(_options.get("delay"))
        return f"job-{len(self.queued)}"

    #: The job types this queue was asked to release from their park, in order.
    unblocked: list[str | None] = field(default_factory=list)

    #: What a release finds parked. Empty when nothing was waiting.
    parked: list[str] = field(default_factory=lambda: ["parked-1"])

    async def unblock(self, *, job_type: str | None = None) -> list[str]:
        self.unblocked.append(job_type)
        return list(self.parked)

    @property
    def types(self) -> list[str]:
        return [one[1] for one in self.queued]

    @property
    def ways(self) -> list[str]:
        return [one[0] for one in self.queued]


@dataclass
class Job:
    """Just enough of a job row for a handler that needs its own id (a sweep's first page)."""

    id: str = "the-run"
    #: Who pressed for it, or None for work the machine queued. See `scan`'s bell.
    requested_by: str | None = None


@dataclass
class Context:
    job: Job = field(default_factory=Job)
    payload: dict[str, Any] = field(default_factory=dict)
    progress: float | None = None
    reported: list[float] = field(default_factory=list)
    #: Set to make `raise_if_canceled` raise, which is what a cancelled job looks like from inside.
    canceled: bool = False
    queue: RecordingQueue = field(default_factory=RecordingQueue)

    note: str | None = None

    async def set_progress(self, value: float) -> None:
        self.progress = value
        self.reported.append(value)

    async def set_note(self, value: str) -> None:
        self.note = value

    #: Every job this one started, in order.
    children: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def enqueue_child(
        self, job_type: str, payload: dict[str, Any] | None = None, **_options: Any
    ) -> str:
        self.children.append((job_type, payload or {}))
        return f"child-{len(self.children)}"

    async def raise_if_canceled(self) -> None:
        if self.canceled:
            raise JobCanceled("cancelled")


async def test_scanning_one_file_runs_it_and_reports_it_finished() -> None:
    service = Recording()
    context = Context(payload={"asset_id": "asset-1"})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert service.asked == [("scan", ("asset-1", None))]
    assert context.progress == 1.0


@dataclass
class Bell:
    """A stand-in for the change bus: what was rung, and for whom."""

    rung: list[tuple[object, object]] = field(default_factory=list)

    def publish(self, audience: object, about: object, *, picture: bool = False) -> None:
        self.rung.append((audience, about))


@pytest.mark.parametrize(("pressed_by", "rings"), [("an-admin", True), (None, False)])
async def test_a_scan_somebody_pressed_rings_the_screens_and_one_the_machine_queued_does_not(
    pressed_by: str | None, rings: bool
) -> None:
    """A press of "Look for faces again" rings the screens; a machine-queued scan does not."""
    bell = Bell()
    changes.listens(bell)  # type: ignore[arg-type]
    try:
        context = Context(job=Job(requested_by=pressed_by), payload={"asset_id": "asset-1"})
        await face_jobs.scan(context, service=Recording())  # type: ignore[arg-type]
    finally:
        changes.listens(None)

    assert bell.rung == ([(EVERY_ADMIN, About.LIBRARY)] if rings else [])


@pytest.mark.parametrize(("payload", "again"), [({"again": True}, True), ({}, False)])
async def test_a_pressed_scan_tells_the_pass_it_is_a_look_again(
    payload: dict[str, Any], again: bool
) -> None:
    """The press's `AGAIN` reaches the scan, or a finished pass would read on from its end."""
    service = Recording()
    context = Context(payload={"asset_id": "asset-1", **payload})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert service.agains == [again]


async def test_a_deeper_look_can_be_asked_for_on_one_file() -> None:
    service = Recording()
    context = Context(payload={"asset_id": "asset-1", "depth": "deep"})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert service.asked == [("scan", ("asset-1", Depth.DEEP))]


async def test_a_scan_with_no_depth_asked_for_uses_whatever_the_settings_say() -> None:
    """No default of fast, which would silently override the setting on every swept scan."""
    service = Recording()
    context = Context(payload={"asset_id": "asset-1"})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert service.asked == [("scan", ("asset-1", None))]


async def test_a_scan_with_no_models_yet_waits_rather_than_failing() -> None:
    """A scan waits for models that have not arrived rather than failing, costing no attempt."""
    service = Recording(missing_models="The recognition models have not been downloaded yet.")
    context = Context(payload={"asset_id": "asset-1", "run": "the-run"})

    with pytest.raises(JobBlocked, match="have not been downloaded"):
        await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert service.asked == [], "nothing was read and nothing was written"
    assert service.asked_about_weights == ["the-run"], "the RUN's family, not today's"
    assert context.progress is None


async def test_a_scan_whose_models_vanish_under_it_waits_too() -> None:
    """The check passed, then the load found no file: a download replacing the models, or their
    folder moved. The job waits for them like one that never had them, costing no attempt."""

    class Vanishing(Recording):
        async def weights_problem(self, *, run: str | None = None) -> str | None:
            self.asked_about_weights.append(run)
            return None if len(self.asked_about_weights) == 1 else "The models are not on disk."

        async def scan(
            self,
            asset_id: str,
            *,
            depth: Depth | None = None,
            run: str | None = None,
            again: bool = False,
        ) -> ScanStatus:
            raise WeightError("the detector model has not been installed yet")

    service = Vanishing()
    context = Context(payload={"asset_id": "asset-1", "run": "the-run"})

    with pytest.raises(JobBlocked, match="not on disk"):
        await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert service.asked_about_weights == ["the-run", "the-run"]


async def test_a_models_failure_with_the_models_present_is_a_failure() -> None:
    """A damaged file says so once, against the file, rather than waiting for a download that
    would change nothing."""

    class Damaged(Recording):
        async def scan(
            self,
            asset_id: str,
            *,
            depth: Depth | None = None,
            run: str | None = None,
            again: bool = False,
        ) -> ScanStatus:
            raise WeightError("the detector model on disk is not the one Sift expects.")

    with pytest.raises(WeightError, match="not the one"):
        await face_jobs.scan(Context(payload={"asset_id": "asset-1"}), service=Damaged())  # type: ignore[arg-type]


async def test_a_scan_asks_about_the_models_before_it_opens_anything() -> None:
    """The ordinary case: the models are there, so the check answers None and the scan proceeds."""
    service = Recording()
    context = Context(payload={"asset_id": "asset-1"})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert service.asked_about_weights == [None]
    assert service.asked == [("scan", ("asset-1", None))]


async def test_matching_again_and_grouping_report_what_they_did() -> None:
    service = Recording()

    await face_jobs.rematch(Context(), service=service)  # type: ignore[arg-type]
    await face_jobs.regroup(Context(), service=service)  # type: ignore[arg-type]

    assert service.asked == [("rematch", None), ("regroup", False)]


async def test_grouping_is_from_scratch_only_when_asked_for_that_way() -> None:
    """The button and the end of a sweep ask for it; a scan's follow-up never does."""
    service = Recording()

    await face_jobs.regroup(Context(payload={"full": True}), service=service)  # type: ignore[arg-type]

    assert service.asked == [("regroup", True)]


async def test_the_floor_pass_looks_again_at_every_file_a_lower_floor_can_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One scan per file in the band, page after page until a page comes back empty, and a note
    saying how many; nothing else in the library is offered."""
    monkeypatch.setattr(face_jobs, "SWEEP_PAGE", 2)
    service = Recording(floor_band=("a1", "a2", "a3"))
    context = Context()

    await face_jobs.floor_pass(context, service=service)  # type: ignore[arg-type]

    assert context.children == [
        (face_jobs.FACE_SCAN, {"asset_id": "a1"}),
        (face_jobs.FACE_SCAN, {"asset_id": "a2"}),
        (face_jobs.FACE_SCAN, {"asset_id": "a3"}),
    ]
    assert context.note is not None and context.note.startswith("3 files queued")


async def test_the_tile_pass_looks_again_from_the_start_at_every_photo_read_from_one_tile() -> None:
    """Through the path a press of "Look for faces again" takes, so the tile's faces go."""
    service = Recording(tile_pages=((("h1",), "h1"), ((), "h5"), (("h7", "h8"), "h8")))
    context = Context()

    await face_jobs.tile_pass(context, service=service)  # type: ignore[arg-type]

    assert context.children == [
        (face_jobs.FACE_SCAN, {"asset_id": one, AGAIN: True}) for one in ("h1", "h7", "h8")
    ]
    assert context.note is not None and context.note.startswith("3 photos queued")


async def test_a_grouping_from_scratch_asks_the_folder_pass_to_propose_the_new_groups() -> None:
    """A rebuilt group has a new id, so the proposal the old one carried is made again only by a
    pass that looks. The incremental grouping keeps every group's id and asks for nothing."""
    full = Context(payload={"full": True})
    await face_jobs.regroup(full, service=Recording(), settles_into=("folder_pass",))  # type: ignore[arg-type]
    little = Context()
    await face_jobs.regroup(little, service=Recording(), settles_into=("folder_pass",))  # type: ignore[arg-type]

    assert full.queue.queued == [("enqueue_when_settled", "folder_pass", {})]
    assert little.queue.queued == []


async def test_the_end_of_a_sweep_asks_for_one_grouping_from_scratch_and_one_rematch() -> None:
    """The re-match is for the faces stored before recognition was switched off: references taken
    in while it was off (a fingerprints file) were never compared with them."""
    service = Recording(pages=(("a", "b"),), total=2)
    context = Context(payload={"viewer": "viewer-1", "offset": 0, "force": False, "queued": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.queue.queued == [
        ("enqueue_when_settled", face_jobs.FACE_REGROUP, {"full": True}),
        ("enqueue_when_settled", face_jobs.FACE_REMATCH, {}),
    ]


async def test_a_rematch_after_every_face_was_measured_again_asks_for_a_grouping_from_scratch() -> (
    None
):
    """The piles' middles are the previous model's numbers; placing a new face against them
    would put it in the wrong pile. Asked for even when the re-match claimed nobody."""
    service = Recording(attributed=0)
    context = Context(payload={"regroup": "full"})

    await face_jobs.rematch(context, service=service)  # type: ignore[arg-type]

    assert context.queue.queued == [
        ("enqueue_when_settled", face_jobs.FACE_REGROUP, {"full": True})
    ]


# --- the follow-up work ----------------------------------------------------------------------------
# Asserted on what gets queued: the handlers can be right while nothing asks for them.


async def test_a_scan_that_left_a_face_unclaimed_asks_for_grouping() -> None:
    service = Recording(status=ScanStatus.NONE_IDENTIFIED)
    context = Context(payload={"asset_id": "asset-1"})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert context.queue.types == [face_jobs.FACE_REGROUP]


@pytest.mark.parametrize("status", [ScanStatus.NO_FACES, ScanStatus.ALL_IDENTIFIED])
async def test_a_scan_that_left_nothing_unclaimed_asks_for_no_grouping(
    status: ScanStatus,
) -> None:
    """Nothing to group is not a small grouping: it is a whole-library pass proving there is none."""
    service = Recording(status=status)
    context = Context(payload={"asset_id": "asset-1"})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert context.queue.types == []


async def test_the_grouping_a_scan_asks_for_waits_for_the_batch_to_settle() -> None:
    """Queued plainly, an import runs one whole-library grouping per file and never catches up."""
    service = Recording(status=ScanStatus.SOME_IDENTIFIED)
    context = Context(payload={"asset_id": "asset-1"})

    await face_jobs.scan(context, service=service)  # type: ignore[arg-type]

    assert context.queue.ways == ["enqueue_when_settled"]


async def test_measuring_again_does_nothing_while_the_feature_is_off() -> None:
    """A job queued before somebody switched the feature off does not measure anything."""
    service = Recording(left=[7, 0])
    service.on = False
    context = Context()

    await face_jobs.remeasure(context, service=service)  # type: ignore[arg-type]

    assert context.children == []
    assert context.queue.queued == []


async def test_measuring_again_asks_for_itself_while_any_remain_and_for_a_rematch_when_none_do() -> (
    None
):
    """A page at a time; after the last, the re-match and grouping are asked for immediately."""
    service = Recording(left=[7, 0])
    first = Context()

    await face_jobs.remeasure(first, service=service)  # type: ignore[arg-type]

    (job_type, payload) = first.children[0]
    assert (job_type, payload["done"]) == (face_jobs.FACE_REMEASURE, 3)
    assert first.queue.queued == [], "not yet: faces are still the other model's"
    assert first.progress is not None and 0 < first.progress < 1

    second = Context(payload={"done": 3})
    await face_jobs.remeasure(second, service=service)  # type: ignore[arg-type]

    assert second.children == []
    assert second.queue.queued == [
        ("enqueue_when_settled", face_jobs.FACE_REMATCH, {"regroup": "full"})
    ]
    assert second.progress == 1.0
    assert second.note == "Measured 6 faces again"


async def test_a_page_still_going_says_how_many_are_left_as_well_as_how_many_are_done() -> None:
    """A count that only ever goes up answers "is it moving" and not "how much longer"."""
    service = Recording(left=[7, 0])
    context = Context()

    await face_jobs.remeasure(context, service=service)  # type: ignore[arg-type]

    assert context.note == "Measured 3 faces again, 7 to go"


async def test_the_first_page_of_measuring_again_is_the_opening_size() -> None:
    """Nothing has been timed yet, so the only page nothing can size is the one that must be
    short."""
    service = Recording(left=[7, 0])
    context = Context()

    await face_jobs.remeasure(context, service=service)  # type: ignore[arg-type]

    assert service.asked == [("remeasure", face_service.REMEASURE_PAGE)]


async def test_a_page_that_took_minutes_hands_the_next_one_a_smaller_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each page's clock sizes the next."""
    monkeypatch.setattr(face_jobs, "monotonic", _clock(0.0, 635.0))
    service = Recording(left=[9999])
    context = Context(payload={"page": 100})

    await face_jobs.remeasure(context, service=service)  # type: ignore[arg-type]

    assert service.asked == [("remeasure", 100)], "it honours the size it was handed"
    (job_type, payload) = context.children[0]
    assert job_type == face_jobs.FACE_REMEASURE
    assert payload["page"] < 100


async def test_a_page_that_took_no_time_hands_the_next_one_a_bigger_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary case on an idle machine, and the reason the opening page being short is cheap:
    it costs a few extra turns through the queue and nothing else."""
    monkeypatch.setattr(face_jobs, "monotonic", _clock(0.0, 0.0))
    service = Recording(left=[9999])
    context = Context(payload={"page": face_service.REMEASURE_PAGE})

    await face_jobs.remeasure(context, service=service)  # type: ignore[arg-type]

    (_job_type, payload) = context.children[0]
    assert payload["page"] > face_service.REMEASURE_PAGE


def _clock(*readings: float) -> Any:
    """A stand-in for the wall clock that hands out the readings it was given, in order."""
    remaining = list(readings)

    def read() -> float:
        return remaining.pop(0)

    return read


async def test_fetching_the_models_asks_for_the_library_to_be_measured_again_when_another_model_described_it() -> (
    None
):
    """A family changed before its models were fetched had nothing to measure with. The fetch
    that brings them is the moment that changes, and nothing else would ask."""
    service = Recording(described_by_another=4)
    context = Context()

    await face_jobs.fetch_weights(context, service=service)  # type: ignore[arg-type]

    assert context.queue.queued == [("enqueue_when_settled", face_jobs.FACE_REMEASURE, {})]

    untouched = Context()
    await face_jobs.fetch_weights(untouched, service=Recording())  # type: ignore[arg-type]
    assert untouched.queue.queued == []


async def test_a_press_to_download_again_fetches_the_models_already_here_too() -> None:
    """The payload's `again` reaches the service as a forced fetch, and only a press sets it: an
    ordinary fetch still asks only for what is missing."""
    again = Recording()
    await face_jobs.fetch_weights(Context(payload={"again": True}), service=again)  # type: ignore[arg-type]
    plain = Recording()
    await face_jobs.fetch_weights(Context(), service=plain)  # type: ignore[arg-type]

    assert again.asked[0] == ("install_models", "force")
    assert plain.asked[0] == ("install_models", None)


async def test_matching_again_that_claimed_somebody_asks_for_grouping() -> None:
    """A claimed face has left the unclaimed pool, so the piles are no longer what they were."""
    context = Context()

    await face_jobs.rematch(context, service=Recording())  # type: ignore[arg-type]

    assert context.queue.types == [face_jobs.FACE_REGROUP]


async def test_matching_again_that_claimed_nobody_asks_for_nothing() -> None:
    context = Context()

    await face_jobs.rematch(context, service=Recording(attributed=0))  # type: ignore[arg-type]

    assert context.queue.types == []


async def test_grouping_does_not_ask_for_more_grouping() -> None:
    """The one shape that would never stop. Worth an assertion rather than a reading of the code."""
    context = Context()

    await face_jobs.regroup(context, service=Recording())  # type: ignore[arg-type]

    assert context.queue.types == []


async def test_asking_for_rematching_waits_for_the_naming_to_settle() -> None:
    """Naming six people in a row is one pass over the library, not six."""
    queue = RecordingQueue()

    await face_jobs.ask_for_rematching(queue)  # type: ignore[arg-type]

    assert queue.queued == [("enqueue_when_settled", face_jobs.FACE_REMATCH, {})]


@pytest.mark.parametrize(
    "handler",
    [
        face_jobs.scan,
        face_jobs.rematch,
        face_jobs.regroup,
        face_jobs.fetch_weights,
        face_jobs.floor_pass,
        face_jobs.tile_pass,
    ],
    ids=lambda fn: fn.__name__,
)
async def test_a_job_claimed_after_the_feature_was_switched_off_does_nothing(handler) -> None:  # type: ignore[no-untyped-def]
    service = Recording(on=False)
    context = Context(payload={"asset_id": "asset-1"})

    await handler(context, service=service)

    assert service.asked == []
    assert context.progress is None


def test_every_kind_of_work_is_registered_once_each(clean_handlers: None) -> None:
    face_jobs.register_handlers(service=Recording())  # type: ignore[arg-type]

    registered = registered_handlers()
    for name in (
        face_jobs.FACE_SCAN,
        face_jobs.FACE_REMATCH,
        face_jobs.FACE_REGROUP,
        face_jobs.FACE_FETCH_WEIGHTS,
        face_jobs.FACE_SWEEP,
        face_jobs.FACE_REMEASURE,
    ):
        assert name in registered


class _Importing(Context):
    """A folder import's job: never paused, its progress kept with the rest."""

    def stopping(self) -> str | None:
        return None

    async def report_progress(self, value: float) -> None:
        await self.set_progress(value)


class _Folders:
    """The feature as a folder import asks it: on, keeping one face of each person read."""

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch

    async def enabled(self) -> bool:
        return True

    def scratch_root(self) -> Path:
        return self.scratch

    async def import_person_folder(self, folder: Path, *, source: str | None) -> PersonReport:
        return PersonReport(name=folder.name, added=1)


async def test_a_folder_import_that_held_somebody_asks_for_the_fingerprints_pass_immediately(
    clean_handlers: None, tmp_path: Path
) -> None:
    """Somebody pressed and is watching the waiting list: the pass waits for no batch to settle."""
    staged = tmp_path / f"{STAGED_PREFIX}one"
    (staged / "Ada Lumen").mkdir(parents=True)
    (staged / "Ada Lumen" / "one.jpg").write_bytes(b"stand-in")
    face_jobs.register_handlers(service=_Folders(tmp_path))  # type: ignore[arg-type]
    context = _Importing(payload={"staged": staged.name})

    await registered_handlers()[FACE_FOLDER_IMPORT](context)  # type: ignore[arg-type]

    assert context.queue.queued == [("enqueue_when_settled", face_jobs.FACE_PEOPLE_FROM_FILES, {})]
    assert context.queue.delays == [0]


# --- fetching the models ------------------------------------------------------------------------
# The callback cannot wait, so a ticker turns its number and flag into progress and a cancel.


async def test_the_download_reports_where_it_got_to_and_finishes_at_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bar the dashboard draws is fed from the ticker, so a download that reported nothing
    until it finished would be several silent minutes on somebody's screen."""
    monkeypatch.setattr(face_jobs, "_PROGRESS_TICK", 0)
    service = Recording()
    context = Context()

    await face_jobs.fetch_weights(context, service=service)  # type: ignore[arg-type]

    assert service.asked == [("install_models", None)]
    assert context.progress == 1.0
    assert any(0 < value < 1 for value in context.reported), (
        "nothing was published until the very end, so the bar sat still while it downloaded"
    )


async def test_cancelling_stops_the_transfer_rather_than_tearing_it_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped transfer keeps its partial file, stopped by a flag, never torn down mid-write."""
    monkeypatch.setattr(face_jobs, "_PROGRESS_TICK", 0)
    service = Recording()
    context = Context(canceled=True)

    with pytest.raises(JobCanceled):
        await face_jobs.fetch_weights(context, service=service)  # type: ignore[arg-type]

    assert service.stopped_at is not None, "the transfer ran to the end after being cancelled"
    assert service.stopped_at < service.expected


async def test_a_release_that_found_nothing_parked_announces_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release is asked either way (what is parked is the queue's to know), and the count
    is written only when there was something to count."""
    monkeypatch.setattr(face_jobs, "_PROGRESS_TICK", 0)
    uncached_log(monkeypatch, face_jobs)
    with capture_logs() as written:
        await face_jobs.fetch_weights(Context(), service=Recording())  # type: ignore[arg-type]
    assert [line["count"] for line in written if line["event"] == "faces.scans.released"] == [1]

    context = Context()
    context.queue.parked = []
    with capture_logs() as written:
        await face_jobs.fetch_weights(context, service=Recording())  # type: ignore[arg-type]
    assert context.queue.unblocked == [face_jobs.FACE_SCAN]
    assert [line for line in written if line["event"] == "faces.scans.released"] == []


async def test_the_models_arriving_releases_the_scans_that_were_waiting_for_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Releasing the jobs parked on the models, by type, so others stay parked."""
    monkeypatch.setattr(face_jobs, "_PROGRESS_TICK", 0)
    context = Context()

    await face_jobs.fetch_weights(context, service=Recording())  # type: ignore[arg-type]

    assert context.queue.unblocked == [face_jobs.FACE_SCAN]


async def test_a_download_that_installed_nothing_releases_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled transfer leaves no model on disk, so the scans are still waiting for one."""
    monkeypatch.setattr(face_jobs, "_PROGRESS_TICK", 0)
    context = Context(canceled=True)

    with pytest.raises(JobCanceled):
        await face_jobs.fetch_weights(context, service=Recording())  # type: ignore[arg-type]

    assert context.queue.unblocked == []


async def test_nothing_is_published_before_the_first_byte_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before the first byte no total is known, so the tick publishes nothing."""
    monkeypatch.setattr(face_jobs, "_PROGRESS_TICK", 0)
    service = Recording(warmup=3)
    context = Context()

    await face_jobs.fetch_weights(context, service=service)  # type: ignore[arg-type]

    assert context.reported[0] > 0, "a fraction was published before anything had arrived"


# --- sweeping the library -------------------------------------------------------------------


async def test_a_sweep_queues_one_scan_per_file_it_has_not_seen() -> None:
    """The sweep scans nothing itself: everything it finds becomes an ordinary scan job."""
    service = Recording(pages=(("a", "b", "c"),), total=3)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.children == [
        (face_jobs.FACE_SCAN, {"asset_id": "a", "run": "the-run"}),
        (face_jobs.FACE_SCAN, {"asset_id": "b", "run": "the-run"}),
        (face_jobs.FACE_SCAN, {"asset_id": "c", "run": "the-run"}),
    ]
    assert context.progress == 1.0


async def test_a_sweep_asks_for_the_next_page_while_there_is_one() -> None:
    """The next page is queued only while the library is longer than what was reached."""
    service = Recording(pages=((), ()), total=face_jobs.SWEEP_PAGE * 2)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.children == [
        (
            face_jobs.FACE_SWEEP,
            {
                "viewer": "viewer-1",
                "offset": face_jobs.SWEEP_PAGE,
                "force": False,
                "queued": 0,
                "run": "the-run",
            },
        )
    ]


async def test_a_capped_page_moves_the_sweep_on_by_what_it_walked_not_by_what_it_asked() -> None:
    """The access layer caps a page, so the sweep steps by what came back."""
    service = Recording(pages=((), (), ()), total=600, cap=200)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.children == [
        (
            face_jobs.FACE_SWEEP,
            {
                "viewer": "viewer-1",
                "offset": 200,
                "force": False,
                "queued": 0,
                "run": "the-run",
            },
        )
    ]


async def test_a_sweep_over_capped_pages_still_reaches_every_file() -> None:
    """End to end: run as the queue would, every file the library holds is offered."""
    service = Recording(pages=(("a", "b"), ("c",), ("d", "e")), total=600, cap=200)
    payload: Any = {"viewer": "u1", "offset": 0}
    scanned: list[str] = []
    for _ in range(10):
        context = Context(payload=payload)
        await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]
        scanned += [
            body["asset_id"] for job, body in context.children if job == face_jobs.FACE_SCAN
        ]
        following = [body for job, body in context.children if job == face_jobs.FACE_SWEEP]
        if not following:
            break
        payload = following[0]

    assert scanned == ["a", "b", "c", "d", "e"]
    assert [asked for name, asked in service.asked if name == "needs_scanning_page"] == [
        (0, face_jobs.SWEEP_PAGE, False),
        (200, face_jobs.SWEEP_PAGE, False),
        (400, face_jobs.SWEEP_PAGE, False),
    ]


async def test_a_page_that_walks_nothing_ends_the_sweep_rather_than_asking_again() -> None:
    """A page that advanced nothing ends the sweep rather than re-queue the same offset."""
    service = Recording(pages=((),), total=600, cap=0)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.children == []


async def test_a_sweep_stops_at_the_end_of_the_library() -> None:
    service = Recording(pages=(("a",),), total=1)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert [job for job, _ in context.children] == [face_jobs.FACE_SCAN]


# --- what the sweep offers, and what it says about it --------------------------------------------


async def test_a_sweep_asks_only_for_what_the_current_settings_have_not_covered() -> None:
    service = Recording(pages=(("a", "b"),), total=2)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert ("needs_scanning_page", (0, face_jobs.SWEEP_PAGE, False)) in service.asked


async def test_a_forced_sweep_offers_everything_and_carries_that_to_the_next_page() -> None:
    """`force` must survive the hand-off between pages."""
    service = Recording(
        pages=((), ()), forced_pages=(("a", "b"), ()), total=face_jobs.SWEEP_PAGE * 2
    )
    context = Context(payload={"viewer": "u1", "offset": 0, "force": True})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert ("needs_scanning_page", (0, face_jobs.SWEEP_PAGE, True)) in service.asked
    assert [job for job, _ in context.children] == [
        face_jobs.FACE_SCAN,
        face_jobs.FACE_SCAN,
        face_jobs.FACE_SWEEP,
    ]
    assert context.children[-1][1]["force"] is True


async def test_a_sweep_that_queued_nothing_says_so_rather_than_finishing_silently() -> None:
    """A control that finishes in ten milliseconds having done nothing is indistinguishable from a
    broken one."""
    service = Recording(pages=((),), total=40)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    # The count of files walked is gone: the sweep opens nothing.
    assert context.note is not None
    assert "already been scanned" in context.note
    assert "40" not in context.note


async def test_a_sweep_says_how_much_it_queued_counting_across_every_page() -> None:
    """A page is an implementation detail. "Queued 40" reported four times is not an answer to how
    much work was started, so the running total travels with the sweep."""
    service = Recording(pages=(("a", "b"),), total=2)
    context = Context(payload={"viewer": "u1", "offset": 0, "queued": 7})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.note is not None
    assert "9" in context.note


async def test_a_sweep_with_more_pages_to_come_gives_a_running_total_and_says_it_is_one() -> None:
    """The running total while pages are still to come, said as provisional."""
    service = Recording(pages=(("a",),), total=face_jobs.SWEEP_PAGE * 2)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.note is not None
    # The count so far, which only ever grows: every page carries the running total forward.
    assert "1 file" in context.note
    # And it says plainly that this is not the answer yet.
    assert "so far" in context.note


async def test_a_sweep_for_an_account_that_has_gone_does_nothing() -> None:
    """A sweep whose user has gone stops rather than running as nobody."""
    service = Recording(viewer=None, pages=(("a",),), total=1)
    context = Context(payload={"viewer": "gone", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.children == []
    assert service.asked == [("viewer_for", "gone")]


async def test_a_sweep_does_nothing_at_all_while_recognition_is_off() -> None:
    """Off means off, and it is checked before the user is even resolved."""
    service = Recording(on=False, pages=(("a",),), total=1)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.children == []
    assert service.asked == []


async def test_an_empty_library_reports_finished_rather_than_dividing_by_it() -> None:
    """Nothing to do is a complete sweep, not a crash on the way to reporting how far it got."""
    service = Recording(pages=((),), total=0)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert context.progress == 1.0
    assert context.children == []


async def test_the_first_page_of_a_sweep_writes_down_what_the_run_is_running_under() -> None:
    """Before it queues anything, so nothing under the run can be scanned under later settings."""
    service = Recording(pages=(("a",),), total=1)
    context = Context(payload={"viewer": "u1", "offset": 0})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert service.started == ["the-run"]


async def test_a_later_page_carries_the_run_rather_than_starting_another() -> None:
    """Asking again a page later is the very thing pinning exists to stop: the second page would
    write down whatever the settings had become and the run would mean two things."""
    service = Recording(pages=(("a",),), total=1)
    context = Context(payload={"viewer": "u1", "offset": 200, "run": "the-first-page"})

    await face_jobs.sweep(context, service=service)  # type: ignore[arg-type]

    assert service.started == []
    assert context.children[0][1]["run"] == "the-first-page"


# --- what a sweep says while it is still going ----------------------------------------------------


def test_a_page_size_that_is_larger_than_the_cap_would_be_a_number_nobody_gets() -> None:
    """The page is the access layer's cap, so the screen counting pages counts right."""
    assert face_jobs.SWEEP_PAGE <= MAX_PAGE_SIZE


def test_a_sweep_still_walking_says_how_much_it_has_queued_so_far() -> None:
    """The sentence itself, apart from the job that writes it."""
    note = face_jobs._swept(queued=1400, done=False)

    assert "1,400" in note
    assert "so far" in note


def test_a_finished_sweep_gives_the_count_plainly() -> None:
    assert face_jobs._swept(queued=1400, done=True) == "1,400 files queued to scan."
    assert face_jobs._swept(queued=1, done=True) == "1 file queued to scan."


def test_a_sweep_that_found_nothing_to_do_says_so_rather_than_showing_a_zero() -> None:
    assert face_jobs._swept(queued=0, done=True) == (
        "Everything has already been scanned under these settings."
    )


async def test_a_starter_run_that_stops_part_way_still_records_what_it_filed() -> None:
    """An attempt stopped part-way (by the graphics card, say) still records what it filed (a line
    in History, an Undo and a re-match), and the failure still fails the attempt."""

    class Stopped(Exception):
        pass

    recorded: list[tuple[dict[str, list[str]], list[str]]] = []

    class Service:
        async def enabled(self) -> bool:
            return True

        async def weights_problem(self, *, run: str | None = None) -> str | None:
            return None

        async def wants_starters(self, people: list[str]) -> list[str]:
            return list(people)

        async def file_starters(self, person_id: str, pictures: Any) -> list[str]:
            if person_id == "third":
                raise Stopped("an NVIDIA graphics card stopped answering")
            return [f"{person_id}-picture"]

        async def record_starters(self, filed: dict[str, list[str]], sources: list[str]) -> None:
            recorded.append((dict(filed), list(sources)))

    class Door:
        async def pictures_of(self, person_id: str, key: bytes, *, most: int) -> Any:
            return [("FansDB", b"picture")]

    @dataclass
    class Keyed(Context):
        async def master_key(self) -> bytes:
            return b"key"

    context = Keyed(payload={"people": ["first", "second", "third", "fourth"]})

    with pytest.raises(Stopped):
        await face_jobs.starters(context, service=Service(), door=Door())  # type: ignore[arg-type]

    assert recorded == [({"first": ["first-picture"], "second": ["second-picture"]}, ["FansDB"])]
    assert context.queue.types == [face_jobs.FACE_REMATCH]


async def test_a_person_who_could_not_be_asked_about_is_left_for_the_next_run() -> None:
    """None (not asked) is not handed to `file_starters`; an empty list is, and is remembered."""
    handed: list[tuple[str, Any]] = []

    class Service:
        async def enabled(self) -> bool:
            return True

        async def weights_problem(self, *, run: str | None = None) -> str | None:
            return None

        async def wants_starters(self, people: list[str]) -> list[str]:
            return list(people)

        async def file_starters(self, person_id: str, pictures: Any) -> list[str]:
            handed.append((person_id, pictures))
            return []

        async def record_starters(self, filed: dict[str, list[str]], sources: list[str]) -> None:
            return None

    class Door:
        async def pictures_of(self, person_id: str, key: bytes, *, most: int) -> Any:
            return None if person_id == "unreachable" else []

    @dataclass
    class Keyed(Context):
        async def master_key(self) -> bytes:
            return b"key"

    context = Keyed(payload={"people": ["unreachable", "nothing-anywhere"]})

    await face_jobs.starters(context, service=Service(), door=Door())  # type: ignore[arg-type]

    assert handed == [("nothing-anywhere", [])]


def test_the_starters_press_is_not_a_run_of_identify(clean_handlers: None) -> None:
    """The starters press is not an Identify run: not a pass over files."""
    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.worker_pool import family_of
    from sift.slices.faces.service import FACE_STARTERS

    face_jobs.register_handlers(service=Recording())  # type: ignore[arg-type]

    assert FACE_STARTERS in registered_handlers()
    assert family_of(FACE_STARTERS) is Family.OTHER
    assert family_of(face_jobs.FACE_SCAN) is Family.IDENTIFY


# --- starters: the guards ----------------------------------------------------------------------


class _StarterService:
    """The feature as the starters job asks it, with each answer a test's to choose."""

    def __init__(
        self, *, on: bool = True, missing: str | None = None, wanting: set[str] | None = None
    ) -> None:
        self.on = on
        self.missing = missing
        self.wanting = wanting
        self.asked_for: list[list[str]] = []
        self.recorded: list[dict[str, list[str]]] = []

    async def enabled(self) -> bool:
        return self.on

    async def weights_problem(self, *, run: str | None = None) -> str | None:
        return self.missing

    async def starters_wanted(self, linked: list[str]) -> list[str]:
        self.asked_for.append(list(linked))
        return list(linked)

    async def wants_starters(self, people: list[str]) -> list[str]:
        return [one for one in people if self.wanting is None or one in self.wanting]

    async def file_starters(self, person_id: str, pictures: Any) -> list[str]:
        return []

    async def record_starters(self, filed: dict[str, list[str]], sources: list[str]) -> None:
        self.recorded.append(dict(filed))


class _Door:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def linked_people(self, *, with_picture_lists: bool) -> list[str]:
        return ["linked-one", "linked-two"]

    async def pictures_of(self, person_id: str, key: bytes, *, most: int) -> Any:
        self.asked.append(person_id)
        return []


@dataclass
class _Keyed(Context):
    key: bytes | None = b"key"

    async def master_key(self) -> bytes | None:
        return self.key


async def test_starters_do_nothing_while_the_feature_is_off() -> None:
    door = _Door()
    await face_jobs.starters(_Keyed(), service=_StarterService(on=False), door=door)  # type: ignore[arg-type]
    assert door.asked == []


async def test_starters_do_nothing_where_this_build_has_no_stash_box_door() -> None:
    service = _StarterService()
    await face_jobs.starters(_Keyed(), service=service, door=None)  # type: ignore[arg-type]
    assert service.recorded == []


async def test_starters_wait_for_the_models_rather_than_failing() -> None:
    """No number of attempts makes a model arrive, so the job is held on the sentence that says
    what is missing."""
    with pytest.raises(JobBlocked, match="not on disk"):
        await face_jobs.starters(
            _Keyed(),  # type: ignore[arg-type]
            service=_StarterService(missing="The models are not on disk"),  # type: ignore[arg-type]
            door=_Door(),  # type: ignore[arg-type]
        )


async def test_starters_wait_while_the_stash_box_keys_are_sealed() -> None:
    """Parked as a wait for the password, in the sentence Activity shows with the field beside it,
    rather than a run that says only that it is blocked."""
    door = _Door()
    with pytest.raises(
        WaitingForPassword, match=r"^Waiting for your password to unlock the stash-box keys\.$"
    ):
        await face_jobs.starters(_Keyed(key=None), service=_StarterService(), door=door)  # type: ignore[arg-type]
    assert door.asked == []


async def test_a_link_asks_after_everybody_linked_and_skips_who_no_longer_wants_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no People named, the door says who is linked, each asked again on her turn."""
    monkeypatch.setattr(face_jobs, "_PROGRESS_TICK", 0)
    service = _StarterService(wanting={"linked-two"})
    door = _Door()
    context = _Keyed()

    await face_jobs.starters(context, service=service, door=door)  # type: ignore[arg-type]

    assert service.asked_for == [["linked-one", "linked-two"]]
    assert door.asked == ["linked-two"]
    assert service.recorded == [{"linked-two": []}]
    assert 0.5 in context.reported and context.reported[-1] == 1.0
    assert context.note == "No stash-box picture passed the checks for a starter"


async def test_a_download_with_nothing_left_to_fetch_releases_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every model already on disk: nothing arrived, so nothing parked on a missing one is waiting
    on this and nothing is measured again."""
    monkeypatch.setattr(face_jobs, "_PROGRESS_TICK", 0)

    @dataclass
    class AllThere(Recording):
        async def install_models(self, *, progress=None, session_factory=None, force=False):  # type: ignore[no-untyped-def]
            self.asked.append(("install_models", "force" if force else None))
            return []

    context = Context()

    await face_jobs.fetch_weights(context, service=AllThere())  # type: ignore[arg-type]

    assert context.progress == 1.0
    assert context.queue.unblocked == []
    assert context.queue.types == []


def test_a_starter_run_says_how_many_pictures_it_added_and_for_how_many_people() -> None:
    """The same count History's line gives, in Activity's words."""
    assert face_jobs._started({"01P": ["a"]}) == "Added 1 starter picture for 1 person"
    assert face_jobs._started({"01P": ["a", "b"], "01Q": ["c"], "01R": []}) == (
        "Added 3 starter pictures for 2 people"
    )
    assert face_jobs._started({"01P": []}) == "No stash-box picture passed the checks for a starter"
