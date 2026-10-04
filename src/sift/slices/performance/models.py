# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the self-test looks like on the wire.

Declared rather than handed straight out of the dataclasses so the shape the client depends on is
written down in one place, and so adding a field to a measurement is a deliberate act rather than
something that leaks out of the server the moment somebody adds it.
"""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class LevelView(Wire):
    """One run at one many-at-once level."""

    at_once: int
    seconds: float
    finished: int
    per_second: float
    responsive: bool
    """False means the application stopped keeping up while this level ran, so it is not a level to
    recommend however much work it finished."""


class StorageLevelView(Wire):
    """One run against one storage: this many files read at once, and what came back."""

    at_once: int
    seconds: float
    megabytes: float
    megabytes_per_second: float


class DecodeView(Wire):
    """How fast this machine decodes and seeks, measured with the test clip at 720p."""

    frames_per_second: float
    seek_seconds: float


class StorageCurveView(Wire):
    """How one storage behaved as more files were read from it at once.

    `best_at_once` is the widest level still worth having, or None where nothing was measured.
    """

    storage: str
    label: str
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
    """Every network storage the library sits on, measured. Empty for a local library."""
    decode: DecodeView | None = None
    """The decoder's rates, or null where it could not be run."""


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
    recommendations: list[RecommendationView] = Field(default=[])
    finished: bool
    rounds: int = 0
    """How many rounds this machine's ladder can reach, as an UPPER bound.

    The screen counts the levels already measured against this to show progress. Sent from here
    because the ladder and the rule that trims it to the machine live on this side. An upper bound,
    shown as "up to": a run stops early when a level stops helping, but the number never moves.
    """
    measured: bool = False
    """Whether this machine has rates on file at all, from this run or from any earlier one.

    Not the same as `finished`, which is about this process's run: stored rates come back with
    `finished` false and `measured` true. The pane needs the difference, because until something
    has been measured every file is read by seeking.
    """
    share_reads_now: int = 0
    """How many files Sift reads at once from each network share, RIGHT NOW.

    The effective number, not the stored one: zero means "automatic", and the rule that resolves it
    lives on this side (`resolve_share_reads`) so the screen cannot disagree with the reads that
    run. Sent even when no share was measured, because the sentence it feeds is about the setting.
    """


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
    on the disk at once. The download is refused without it, and the screen says this figure."""
    version: str
    """Which set is pinned, so a screen can say what it would install."""
    job_id: str | None
    """The download in flight, when there is one, so the screen can follow it."""
    restart_needed: bool
    """Installed after the processor build had already been loaded in this process. The install is
    real and cannot take effect until Sift is opened again. See `ml.accel.restart_needed`."""


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
    """The benchmark Sift runs by itself on the first library folder, as the toasts read it.

    `state` is `none` until one was queued in this process; `job_id` is what a window compares, so
    one run is said once; `said` is the sentence Activity's note carries for the same moment, so
    the toast and the note are one author's. `measured` lets a window on a measured device stop
    asking: a measured device never queues one.
    """

    state: str
    job_id: str | None = None
    said: str | None = None
    changes: list[BenchmarkChangeView] = Field(default=[])
    measured: bool
    held: str | None = None
    """While the run waits or runs, the sentence for the folder whose files it holds back: what the
    wall says in place of its files, and what the toast of whoever added the folder says."""
