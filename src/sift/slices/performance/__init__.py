# SPDX-License-Identifier: AGPL-3.0-or-later
"""Performance: how hard Sift works the machine, exposed as preferences, and measured.

It declares the tuning knobs on the Performance screen and provides the resolvers that turn a stored
number into the effective one. Everything that acts on those numbers lives where the thing being
tuned lives: the worker pool reconfigures itself from them (wired in `sift/wiring/workers.py`), and
the library watcher reads the polling ones.

It also owns the self-test: the one thing here that is not a preference. Sift sizes itself from the
core count, which is a guess; the self-test works the machine and recommends numbers from what it
actually did. It recommends and never writes: applying goes through the ordinary settings route.

Importing the package registers the settings, the same as every other slice.
"""

from __future__ import annotations

from sift.slices.performance import selftest
from sift.slices.performance.benchmark import (
    BENCHMARK,
    BENCHMARK_NAME,
    FIRST_BENCHMARK,
    WHEN_QUIET,
    BenchmarkReceipts,
    FirstBenchmark,
    FirstFolder,
    ThenScan,
    WhenQuiet,
    run_benchmark,
)
from sift.slices.performance.jobs import ACCEL_INSTALL, register_handlers
from sift.slices.performance.rates import MACHINE_RATES, MachineRates, RatesStore, StorageRate
from sift.slices.performance.router import router
from sift.slices.performance.runner import (
    SELF_TEST_RUNNER,
    SelfTestRunner,
    current_settings,
    storages_to_measure,
)
from sift.slices.performance.settings import (
    AUTOMATIC,
    BUSY_STEP_BACK_KEY,
    GENERATE_FINGERPRINTS_KEY,
    GENERATE_PREVIEWS_KEY,
    GENERATE_SPRITES_KEY,
    GENERATE_THUMBNAILS_KEY,
    GENERATION_LIMIT_KEY,
    MAX_MANUAL_WORKERS,
    MIN_POLL_SECONDS,
    PREVIEW_SHAPE_KEY,
    REPAIR_PLAYBACK_KEY,
    SCAN_FACES_ON_IMPORT_KEY,
    SCAN_LIMIT_KEY,
    SHARE_READS_KEY,
    STEP_BACK_KEY,
    STEP_BACK_SHARE_KEY,
    WORKER_COUNT_KEY,
    register,
    resolve_compress_share,
    resolve_describe_share,
    resolve_fingerprint_share,
    resolve_generation_limit,
    resolve_probe_share,
    resolve_scan_limit,
    resolve_scan_share,
    resolve_share_reads,
    resolve_step_back_share,
    resolve_thumbnail_share,
    resolve_worker_count,
    scan_limit_is_fixed,
)

register()

__all__ = [
    "ACCEL_INSTALL",
    "AUTOMATIC",
    "BENCHMARK",
    "BENCHMARK_NAME",
    "BUSY_STEP_BACK_KEY",
    "FIRST_BENCHMARK",
    "GENERATE_FINGERPRINTS_KEY",
    "GENERATE_PREVIEWS_KEY",
    "GENERATE_SPRITES_KEY",
    "GENERATE_THUMBNAILS_KEY",
    "GENERATION_LIMIT_KEY",
    "MACHINE_RATES",
    "MAX_MANUAL_WORKERS",
    "MIN_POLL_SECONDS",
    "PREVIEW_SHAPE_KEY",
    "REPAIR_PLAYBACK_KEY",
    "SCAN_FACES_ON_IMPORT_KEY",
    "SCAN_LIMIT_KEY",
    "SELF_TEST_RUNNER",
    "SHARE_READS_KEY",
    "STEP_BACK_KEY",
    "STEP_BACK_SHARE_KEY",
    "WHEN_QUIET",
    "WORKER_COUNT_KEY",
    "BenchmarkReceipts",
    "FirstBenchmark",
    "FirstFolder",
    "MachineRates",
    "RatesStore",
    "SelfTestRunner",
    "StorageRate",
    "ThenScan",
    "WhenQuiet",
    "current_settings",
    "register_handlers",
    "resolve_compress_share",
    "resolve_describe_share",
    "resolve_fingerprint_share",
    "resolve_generation_limit",
    "resolve_probe_share",
    "resolve_scan_limit",
    "resolve_scan_share",
    "resolve_share_reads",
    "resolve_step_back_share",
    "resolve_thumbnail_share",
    "resolve_worker_count",
    "router",
    "run_benchmark",
    "scan_limit_is_fixed",
    "selftest",
    "storages_to_measure",
]
