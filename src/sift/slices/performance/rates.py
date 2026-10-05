# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this machine measured, kept per hardware profile, so a changed machine finds nothing."""

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
from sift.slices.performance.measure_encoder import CardCurve, CardLevel
from sift.slices.performance.measure_models import ModelCurve, ModelLevel
from sift.slices.performance.measure_together import Plan, Reads, Together, Window
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


def measurement_to_json(
    measurement: Measurement,
    *,
    card: CardCurve | None = None,
    models: tuple[ModelCurve, ...] = (),
    together: Together | None = None,
    lengths: Mapping[str, float] | None = None,
) -> str:
    """A finished run's curves, the combined run's included, as `machine_rates.measurement`."""
    kept = asdict(measurement)
    kept["card"] = None if card is None else asdict(card)
    kept["models"] = [asdict(one) for one in models]
    kept["together"] = None if together is None else asdict(together)
    kept["lengths"] = dict(lengths or {})
    return json.dumps(kept)


def lengths_from_json(text: str) -> dict[str, float]:
    try:
        raw = json.loads(text).get("lengths") or {}
        return {str(kind): float(seconds) for kind, seconds in raw.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def together_from_json(text: str) -> Together | None:
    """The combined run back again; None where none was kept or it is unreadable."""
    try:
        raw = json.loads(text).get("together")
        if raw is None:
            return None
        windows = []
        for one in raw["windows"]:
            plan = one["plan"]
            kept = Plan(
                **{
                    **plan,
                    "widths": tuple((str(name), int(n)) for name, n in plan["widths"]),
                    "reads": tuple(Reads(**each) for each in plan["reads"]),
                }
            )
            windows.append(Window(**{**one, "plan": kept}))
        return Together(windows=tuple(windows), failed=raw.get("failed"), seconds=raw["seconds"])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("performance.selftest.kept_unreadable", error=str(exc))
        return None


def more_from_json(text: str) -> tuple[CardCurve | None, tuple[ModelCurve, ...]]:
    """The card's curve and the models' back again; nothing where none was kept or unreadable."""
    try:
        raw: dict[str, Any] = json.loads(text)
        card = raw.get("card")
        return (
            None
            if card is None
            else CardCurve(**{**card, "levels": tuple(CardLevel(**one) for one in card["levels"])}),
            tuple(
                ModelCurve(**{**one, "levels": tuple(ModelLevel(**at) for at in one["levels"])})
                for one in raw.get("models", ())
            ),
        )
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("performance.selftest.kept_unreadable", error=str(exc))
        return None, ()


def measurement_from_json(text: str) -> Measurement | None:
    """The measurement back, or None where unreadable; a field added since takes its default."""
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
                    unmeasured=curve.get("unmeasured"),
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
    """Each storage that was measured, by the key the operating system names it with."""
    measurement: Measurement | None = None
    """The whole reading, for the screen's advice after a restart; None before v2 or unreadable."""
    card: CardCurve | None = None
    models: tuple[ModelCurve, ...] = ()
    together: Together | None = None
    lengths: Mapping[str, float] = field(default_factory=dict)

    def for_reading(self, storage: str | None) -> ReadRates | None:
        """The rates for the kernel's read rule, for a file on `storage` (None for a local disk);
        None where the decoder was never measured, as a rule with no rate seeks."""
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

    def reads_at_once(self) -> dict[str, int]:
        """How many files each measured storage serves at once, for the lanes."""
        return {key: one.at_once for key, one in self.storages.items()}

    @classmethod
    def from_measurement(
        cls,
        profile: str,
        measurement: Measurement,
        *,
        now: int,
        card: CardCurve | None = None,
        models: tuple[ModelCurve, ...] = (),
        together: Together | None = None,
        lengths: Mapping[str, float] | None = None,
    ) -> MachineRates:
        """The rates off a finished measurement; what was not measured is None, never zero."""
        decode = measurement.decode
        return cls(
            profile=profile,
            measured_at=now,
            decode_fps=decode.frames_per_second if decode is not None else None,
            seek_seconds=decode.seek_seconds if decode is not None else None,
            storages=storage_rates(measurement),
            measurement=measurement,
            card=card,
            models=models,
            together=together,
            lengths=dict(lengths or {}),
        )


def storage_rates(measurement: Measurement) -> dict[str, StorageRate]:
    """Each storage's rate at the level the curve's rule chooses, where one was measured."""
    return {
        curve.storage: StorageRate(
            at_once=best.at_once,
            megabytes_per_second=best.megabytes_per_second,
            seek_seconds=best.seconds_per_seek,
        )
        for curve in measurement.storages
        if (best := curve.best) is not None
    }


class RatesStore:
    """Where the rates live between runs of the application."""

    def __init__(self, database: Database) -> None:
        self._db = database
        #: What each profile last answered: asked per file of a Build, moved only by `save`.
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
        text = None if row["measurement"] is None else str(row["measurement"])
        measurement = None if text is None else measurement_from_json(text)
        card, models = (None, ()) if text is None else more_from_json(text)
        together = None if text is None else together_from_json(text)
        lengths = {} if text is None else lengths_from_json(text)
        if measurement is not None:
            # Judged again by today's rule, so the lanes and the screen read one number.
            storages.update(storage_rates(measurement))
        return MachineRates(
            profile=str(row["profile"]),
            measured_at=int(row["measured_at"]),
            decode_fps=None if row["decode_fps"] is None else float(row["decode_fps"]),
            seek_seconds=None if row["seek_seconds"] is None else float(row["seek_seconds"]),
            storages=storages,
            measurement=measurement,
            card=card,
            models=models,
            together=together,
            lengths=lengths,
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
                None
                if rates.measurement is None
                else measurement_to_json(
                    rates.measurement,
                    card=rates.card,
                    models=rates.models,
                    together=rates.together,
                    lengths=rates.lengths,
                ),
            ),
        )
        self._known.pop(rates.profile, None)


def now() -> int:
    return int(time.time())


#: The rates store, on the application.
MACHINE_RATES: Part[RatesStore] = Part("machine_rates")
