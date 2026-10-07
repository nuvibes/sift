# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the self-test looks like on the wire: declared, so a new field reaches the client only
when it is added here."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire
from sift.slices.performance.measure_encoder import CardCurve
from sift.slices.performance.measure_models import ModelCurve


class LevelView(Wire):
    """One run at one many-at-once level."""

    at_once: int
    seconds: float
    finished: int
    per_second: float
    responsive: bool
    """False: Sift stopped keeping up at this level, so it is never recommended."""


class StorageLevelView(Wire):
    """One run against one storage: this many files read at the same time, and what came back."""

    at_once: int
    seconds: float
    megabytes: float
    megabytes_per_second: float


class DecodeView(Wire):
    """How fast this machine decodes and seeks, measured with the test clip at 720p."""

    frames_per_second: float
    seek_seconds: float


class StorageCurveView(Wire):
    """One storage as more files were read at the same time; `best_at_once` None where nothing was."""

    storage: str
    label: str
    folders: str = ""
    """The library folders on it now; `label` is the storage's own short name."""
    remote: bool
    levels: list[StorageLevelView] = Field(default=[])
    failed: str | None = None
    best_at_once: int | None = None


class MeasurementView(Wire):
    cores: int
    levels: list[LevelView] = Field(default=[])
    failed: str | None = None
    """Why nothing was measured, so a test that could not run does not read as a slow machine."""
    storages: list[StorageCurveView] = Field(default=[])
    """Every storage the library sits on, local disks included."""
    decode: DecodeView | None = None
    """The decoder's rates, or null where it could not be run."""


def _megabytes(count: int | None) -> int | None:
    return None if count is None else round(count / 1_000_000)


class CardLevelView(Wire):
    """Previews built on the GPU at one width, with the command previews use."""

    at_once: int
    seconds: float
    finished: int
    per_second: float
    responsive: bool
    busy: bool = False
    card_megabytes: int | None = None


class CardView(Wire):
    encoder: str
    decodes_on_card: bool
    levels: list[CardLevelView] = Field(default=[])
    failed: str | None = None
    best_at_once: int | None = None


def card_view(curve: CardCurve | None) -> CardView | None:
    if curve is None:
        return None
    best = curve.best
    return CardView(
        encoder=curve.encoder,
        decodes_on_card=curve.decodes_on_card,
        failed=curve.failed,
        best_at_once=None if best is None else best.at_once,
        levels=[
            CardLevelView(
                at_once=one.at_once,
                seconds=round(one.seconds, 2),
                finished=one.finished,
                per_second=round(one.throughput, 3),
                responsive=one.responsive,
                busy=one.busy,
                card_megabytes=_megabytes(one.card_memory_bytes),
            )
            for one in curve.levels
        ],
    )


class ModelLevelView(Wire):
    at_once: int
    files_per_second: float
    failed: int = 0
    busy: bool = False
    megabytes: int | None = None
    card_megabytes: int | None = None


class ModelView(Wire):
    name: str
    device: str
    levels: list[ModelLevelView] = Field(default=[])
    failed: str | None = None
    best_at_once: int | None = None
    seconds_per_file: float | None = None
    """One worker's seconds for one file at the chosen width: the price before any history."""
    megabytes: int | None = None
    card_megabytes: int | None = None


def model_views(curves: tuple[ModelCurve, ...]) -> list[ModelView]:
    views = []
    for curve in curves:
        best, each = curve.best, curve.seconds_per_file
        views.append(
            ModelView(
                name=curve.name,
                device=curve.device,
                failed=curve.failed,
                best_at_once=None if best is None else best.at_once,
                seconds_per_file=None if each is None else round(each, 2),
                megabytes=_megabytes(curve.memory_bytes),
                card_megabytes=_megabytes(curve.card_memory_bytes),
                levels=[
                    ModelLevelView(
                        at_once=one.at_once,
                        files_per_second=round(one.files_per_second, 3),
                        failed=one.failed,
                        busy=one.busy,
                        megabytes=_megabytes(one.memory_bytes),
                        card_megabytes=_megabytes(one.card_memory_bytes),
                    )
                    for one in curve.levels
                ],
            )
        )
    return views


class RecommendationView(Wire):
    """One setting, and what the measurement says it should be. Applied by an admin, never here."""

    key: str
    label: str
    current: int
    suggested: int
    reason: str
    changes_anything: bool


class SelfTestView(Wire):
    running: bool
    measurement: MeasurementView | None = None
    """The result on screen: the last run that ended, never the one going."""
    progress: MeasurementView | None = None
    """How far the run going has measured; None while it waits for the work it paused."""
    recommendations: list[RecommendationView] = Field(default=[])
    finished: bool
    rounds: int = 0
    """How many rounds this device's ladder can reach, at most, the step back included: the "up to"."""
    step: str | None = None
    """The stage the run going is in: encoding, decoder, storage, previews, models or together."""
    seconds_left: int | None = None
    """The run going's time left, from each stage still to run at its length here; revised as it goes."""
    left_timed: bool = False
    """Whether that comes from this device's last run of its kind; false, it's the run's limit."""
    whole_seconds: int = 0
    """How long a whole run takes on this device, worked out the same way before it starts."""
    whole_timed: bool = False
    """Whether that comes from this device's last whole run; false, it's the limit, an "up to"."""
    measured: bool = False
    """Whether this machine has rates on file at all, from this run or from any earlier one.

    Not the same as `finished`, which is about this process's run: stored rates come back with
    `finished` false and `measured` true. The pane needs the difference, because until something
    has been measured every file is read by seeking.
    """
    share_reads_now: int = 0
    """How many files Sift reads at the same time from each network share, RIGHT NOW.

    The effective number, not the stored one: zero means each share reads as measured, and the rule
    that resolves it lives on this side (`resolve_share_reads`) so the screen cannot disagree with
    the reads that run. Sent even when no share was measured, because the sentence it feeds is about
    the setting.
    """
    notes: list[str] = Field(default=[])
    """What the last run could not measure, or paused while it measured, in sentences to show."""
    card: CardView | None = None
    models: list[ModelView] = Field(default=[])
    held_while_measuring: int = 0
    full_while_measuring: int = 0
    """The loop's and the threads' stalls since Sift started that the benchmark caused."""
    whole_to_come: bool = False
    whole_due: bool = False
    """Only the first part was measured, and whether Sift still runs the rest by itself."""


class GpuCard(Wire):
    """One graphics adapter. Every field independently absent: an older driver reports less."""

    name: str | None = None
    vram_bytes: int | None = None
    can_compute: bool = False
    """Whether Sift could put a model on it, which today means an NVIDIA card with CUDA.

    A chip built into the processor and a card in a slot is the ordinary shape of a machine, and
    both belong in a description of it, but only one of them answers "which card will Smart
    Search run on"."""


class HardwareView(Wire):
    """The machine, as the startup probe found it.

    Admin-only because it names the processor and the graphics card, which `/health` does not.
    """

    cpu_count: int
    cpu_model: str | None
    total_ram_bytes: int | None
    installed_ram_bytes: int | None = None
    """What the machine HAS, where `total_ram_bytes` is what the operating system can address. The
    difference is what the firmware and the hardware reserve, which can be gigabytes, so
    the addressable figure alone reads as Sift being unable to count."""
    worker_concurrency: int
    gpu_name: str | None
    """The card that would do the work: the first one. Kept beside `gpu_cards` because most of the
    screen asks about that one, and reading `[0]` in four places is four places to get it wrong."""
    gpu_driver: str | None
    gpu_vram_bytes: int | None = None
    """Its memory. The figure that decides what will FIT: a model too big for the card fails at the
    point of loading rather than running slowly."""
    gpu_cards: list[GpuCard] = Field(default=[])
    """Every card the driver reports. More than one is ordinary on a machine built for this, and
    naming only the first is a description of somebody's computer that is quietly wrong."""
    cuda: bool
    rocm: bool
    transcode_encoders: list[str]
    warnings: list[str]


class AcceleratorView(Wire):
    """Whether the graphics card can be used, and what it would take.

    ONE SHAPE FOR FOUR STATES, because the screen has to tell them apart and they are easy to
    confuse: no card in the machine at all; a card and no runtime; a runtime downloading; a runtime
    installed. The fourth splits again into working and not, and that last distinction is the one
    that cannot be guessed: a runtime lists every provider it was compiled with, whether or not
    the hardware behind it can be reached.
    """

    card: str | None
    """What the card is called, or null when the machine has none. From the startup probe."""
    installed: bool
    already_capable: bool
    """The runtime this Sift can already import drives a card without any of this: somebody who
    installed it themselves, at a version they chose. Then there is nothing to offer, and offering
    anyway would spend a gigabyte replacing something that works."""
    supported: bool
    """Whether this machine could use it at all: an NVIDIA card has to be there and visible."""
    download_bytes: int
    """How much would be fetched. Shown BEFORE anybody agrees to it: 1.3 GB is not a thing to
    start on somebody's connection and mention afterwards."""
    peak_bytes: int = 0
    """How much room the install needs at its widest: the wheels and what comes out of them, both
    on the disk together. The download is refused without it, and the screen says this figure."""
    version: str
    """Which set is pinned, so a screen can say what it would install."""
    job_id: str | None
    """The download in flight, when there is one, so the screen can follow it."""


class AcceleratorTestView(Wire):
    """What happened when the card was actually asked to run a model."""

    works: bool
    problem: str | None


class RestartView(Wire):
    """That a restart has been arranged. Not that it has happened. See the route."""

    restarting: bool


class RunReportView(Wire):
    """One run as the block of plain text a person copies and passes on."""

    text: str


class BenchmarkChangeView(Wire):
    """One setting the automatic benchmark set: its key, its name on the screen, and both values.
    0 is automatic, as everywhere on the Performance screen."""

    key: str
    label: str
    before: int
    after: int


class FirstBenchmarkView(Wire):
    """The benchmark Sift runs by itself, as the toasts read it.

    `state` is `none` until one was queued in this process; `job_id` is what a window compares, so
    one run is said once; `said` is Activity's note for the same moment. `measured` lets a window
    stop asking: it's true once nothing more is run by itself."""

    state: str
    job_id: str | None = None
    said: str | None = None
    changes: list[BenchmarkChangeView] = Field(default=[])
    measured: bool
    held: str | None = None
    """While the run waits or runs, the sentence for the folder whose files it holds back: what the
    wall says in place of its files, and what the toast of whoever added the folder says."""
