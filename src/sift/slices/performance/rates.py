# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this machine measured, kept: the rates a Build's reader is shaped by.

The self-test's full measurement is held in memory and shown on the Performance screen, and that
is deliberate. See `selftest.SelfTest`. Two of its numbers are read by something other than a
person: how fast this machine decodes and what a seek costs it, on the local disk and on each
share. Those decide, per file, whether a Build seeks to the moments it wants or decodes the file
once, and they have to outlive the process: otherwise every restart would put the next Build back
to measuring before it could start.

One row per hardware profile. A machine that has lost its graphics card or gained a processor is a
different machine for anything that measures how fast it works, so a rate taken before the change
must not shape reads after it. `HardwareReport.profile` is the digest that draws that line.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from sift.kernel.db import Connection, Database, register_schema_initializer
from sift.kernel.log import get_logger
from sift.kernel.media import ReadRates, StorageRead
from sift.kernel.wiring import Part
from sift.slices.performance.selftest import (
    Decode,
    Level,
    Measurement,
    StorageCurve,
    StorageLevel,
)

log = get_logger(__name__)

COMPONENT = "performance"
VERSION = 2

_CREATE_MACHINE_RATES = """
CREATE TABLE IF NOT EXISTS machine_rates (
  profile      TEXT PRIMARY KEY,
  measured_at  INTEGER NOT NULL,
  decode_fps   REAL,
  seek_seconds REAL,
  storages     TEXT NOT NULL DEFAULT '{}',
  -- The whole measurement, kept beside the two rates read off it, so the screen's advice survives
  -- a restart. A row belongs to one hardware profile, so a changed machine finds nothing; how old
  -- the advice is stays the screen's to say, from `measured_at`. NULL where only the rates are
  -- known.
  measurement  TEXT
)
"""

_LOAD = "SELECT * FROM machine_rates WHERE profile = ?"

_SAVE = """
INSERT INTO machine_rates (profile, measured_at, decode_fps, seek_seconds, storages, measurement)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(profile) DO UPDATE SET
  measured_at = excluded.measured_at,
  decode_fps = excluded.decode_fps,
  seek_seconds = excluded.seek_seconds,
  storages = excluded.storages,
  measurement = excluded.measurement
"""


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_MACHINE_RATES)


def measurement_to_json(measurement: Measurement) -> str:
    """A finished measurement as the text kept in `machine_rates.measurement`."""
    return json.dumps(asdict(measurement))


def measurement_from_json(text: str) -> Measurement | None:
    """The measurement back again, or None for text this version cannot read.

    None rather than an error: a kept reading is a convenience (the screen can always measure
    again) and a row a later version wrote in a shape this one does not know must not take the
    Performance screen down with it.
    """
    try:
        raw: dict[str, Any] = json.loads(text)
        return Measurement(
            cores=int(raw["cores"]),
            levels=tuple(Level(**one) for one in raw.get("levels", ())),
            failed=raw.get("failed"),
            storages=tuple(
                StorageCurve(
                    storage=str(curve["storage"]),
                    label=str(curve["label"]),
                    remote=bool(curve["remote"]),
                    levels=tuple(StorageLevel(**one) for one in curve.get("levels", ())),
                    failed=curve.get("failed"),
                )
                for curve in raw.get("storages", ())
            ),
            decode=None if raw.get("decode") is None else Decode(**raw["decode"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("performance.selftest.kept_unreadable", error=str(exc))
        return None


register_schema_initializer(COMPONENT, VERSION, initialize, baseline=2)


@dataclass(frozen=True)
class StorageRate:
    """What one storage measured at the widest level still worth having."""

    at_once: int
    """How many files it serves at once before delivering less."""
    megabytes_per_second: float
    """What it delivered at that width, to all the readers together."""
    seek_seconds: float
    """What one reader paid per seek at that width."""


@dataclass(frozen=True)
class MachineRates:
    """The rates of one hardware profile, as last measured."""

    profile: str
    measured_at: int
    """When, as seconds since the epoch."""
    decode_fps: float | None
    """Frames of 720p decoded a second by one task, or None where the decoder could not run."""
    seek_seconds: float | None
    """What one moment costs when taken by seeking on the local disk, or None as above."""
    storages: Mapping[str, StorageRate] = field(default_factory=dict)
    """Each network storage that was measured, by the key the operating system names it with."""
    measurement: Measurement | None = None
    """The whole reading these rates came from, for the screen's advice after a restart. None on
    a row kept before v2, or one this version cannot read."""

    def for_reading(self, storage: str | None) -> ReadRates | None:
        """The rates the way the kernel's read rule takes them, for a file on `storage`: the
        share's key, or None for a local disk. None where the decoder was never measured: a
        rule with no rate seeks, which needs none."""
        if self.decode_fps is None or self.seek_seconds is None:
            return None
        share = self.storages.get(storage) if storage is not None else None
        return ReadRates(
            decode_fps=self.decode_fps,
            seek_seconds=self.seek_seconds,
            storage=None
            if share is None
            else StorageRead(
                megabytes_per_second=share.megabytes_per_second, seek_seconds=share.seek_seconds
            ),
        )

    @classmethod
    def from_measurement(cls, profile: str, measurement: Measurement, *, now: int) -> MachineRates:
        """Read the rates off a finished measurement. What could not be measured is None or
        absent, never zero: a zero would read as a machine that decodes nothing."""
        storages: dict[str, StorageRate] = {}
        for curve in measurement.storages:
            best = curve.best
            if best is None:
                continue
            storages[curve.storage] = StorageRate(
                at_once=best.at_once,
                megabytes_per_second=best.megabytes_per_second,
                seek_seconds=best.seconds_per_seek,
            )
        decode = measurement.decode
        return cls(
            profile=profile,
            measured_at=now,
            decode_fps=decode.frames_per_second if decode is not None else None,
            seek_seconds=decode.seek_seconds if decode is not None else None,
            storages=storages,
            measurement=measurement,
        )


class RatesStore:
    """Where the rates live between runs of the application."""

    def __init__(self, database: Database) -> None:
        self._db = database
        #: What each profile last answered. The runner asks per task (once per file of a
        #: Build) and the answer moves only when `save` writes one, so it is kept here and
        #: dropped there.
        self._known: dict[str, MachineRates | None] = {}

    async def load(self, profile: str) -> MachineRates | None:
        """What was last measured for this profile, or None where nothing was."""
        if profile in self._known:
            return self._known[profile]
        rates = await self._read(profile)
        self._known[profile] = rates
        return rates

    async def _read(self, profile: str) -> MachineRates | None:
        row = await self._db.fetch_one(_LOAD, (profile,))
        if row is None:
            return None
        stored = json.loads(str(row["storages"]))
        storages = {
            str(key): StorageRate(
                at_once=int(one["at_once"]),
                megabytes_per_second=float(one["megabytes_per_second"]),
                seek_seconds=float(one["seek_seconds"]),
            )
            for key, one in stored.items()
        }
        return MachineRates(
            profile=str(row["profile"]),
            measured_at=int(row["measured_at"]),
            decode_fps=None if row["decode_fps"] is None else float(row["decode_fps"]),
            seek_seconds=None if row["seek_seconds"] is None else float(row["seek_seconds"]),
            storages=storages,
            measurement=None
            if row["measurement"] is None
            else measurement_from_json(str(row["measurement"])),
        )

    async def save(self, rates: MachineRates) -> None:
        """Keep these, in place of whatever this profile had."""
        storages = {
            key: {
                "at_once": one.at_once,
                "megabytes_per_second": one.megabytes_per_second,
                "seek_seconds": one.seek_seconds,
            }
            for key, one in rates.storages.items()
        }
        await self._db.execute(
            _SAVE,
            (
                rates.profile,
                rates.measured_at,
                rates.decode_fps,
                rates.seek_seconds,
                json.dumps(storages),
                None if rates.measurement is None else measurement_to_json(rates.measurement),
            ),
        )
        self._known.pop(rates.profile, None)


def now() -> int:
    return int(time.time())


#: The rates store, on the application.
MACHINE_RATES: Part[RatesStore] = Part("machine_rates")
