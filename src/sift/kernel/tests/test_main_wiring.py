# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rules that live in the composition root, and nowhere else.

The small closures `sift.wiring` defines because they alone may know that two slices exist: the
folder and archive groupers, which derivatives a scan builds (read per file), and what happens the
moment a setting changes, where a feature switched off gives its memory and disk back immediately.
"""

from __future__ import annotations

# The NAME only: `sqlite3.Error` is what the settings converger catches, raised below to prove that
# arm; nothing here opens a database.
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sift import main
from sift.kernel import lanes, lifecycle, media, sampling, wiring
from sift.kernel.config import ConfigError, Settings
from sift.kernel.db import DatabaseError, readers_for
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import BACKGROUND_PRIORITY, worker_pool
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.switchboard import Switchboard
from sift.slices import (
    auth,
    capture,
    dedup,
    delete,
    download,
    faces,
    importing,
    library_roots,
    loops,
    media_jobs,
    performance,
    photo_sets,
    search,
    semantic,
    stash_boxes,
    suggestions,
    tasks,
    vault,
    watermarks,
)
from sift.slices.importing.jobs import ARRIVED
from sift.slices.player import service as player_service
from sift.wiring import (
    catalog,
    catch_up,
    file_actions,
    imports,
    reactions,
    sign_in,
    staging,
    workers,
)

pytestmark = pytest.mark.unit


class _Hub:
    """The settings service, reduced to the one question these closures ask it."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def get_app(self, key: str) -> Any:
        self.asked.append(key)
        return self.answers.get(key, False)


class _Content:
    """The content store, reduced to the two calls the settings reaction makes."""

    def __init__(self, needing: list[str] | None = None) -> None:
        self.needing = needing or []
        self.dropped: list[Any] = []

    async def needing_remux(self, _threshold: int, _limit: int) -> list[str]:
        return self.needing

    async def drop_derivatives(self, kind: Any) -> None:
        self.dropped.append(kind)


class _QueueWithABoard:
    """The queue as the import wiring uses it: only the board the off switches are declared on."""

    def __init__(self) -> None:
        self.switchboard = Switchboard()


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, Any]]] = []
        self.settled: list[tuple[str, int | None]] = []

    async def enqueue_when_settled(self, job_type: str, *, delay: int | None = None) -> str:
        """Whole-library work, asked for once the queue is quiet, or immediately, with `delay=0`."""
        self.settled.append((job_type, delay))
        return "job-2"

    async def enqueue(
        self, job_type: str, payload: dict[str, Any] | None = None, **_kw: Any
    ) -> str:
        # Optional: a recurring job, like the quarantine sweep's next run, has no payload.
        self.enqueued.append((job_type, payload or {}))
        return "job-1"

    async def enqueue_many(
        self, job_type: str, payloads: list[dict[str, Any]], **_kw: Any
    ) -> list[str]:
        """One batch, one write: recorded payload by payload, and as a batch."""
        self.batches = [*getattr(self, "batches", []), len(payloads)]
        self.enqueued.extend((job_type, payload) for payload in payloads)
        return [f"job-{n}" for n in range(len(payloads))]

    async def list(self, **_kw: Any) -> Any:
        """Nothing is already queued, as for a fresh reaction."""
        return type("Page", (), {"total": 0})()

    def forget_quiet_hours(self) -> None:
        self.forgot_quiet_hours = True


class _TaskClock:
    """The one scheduler, recording what a settings change asked of it."""

    def __init__(self) -> None:
        self.reading: list[set[str]] = []
        self.everything = 0

    async def reschedule_reading(self, keys: set[str]) -> None:
        self.reading.append(set(keys))

    async def reschedule_all(self) -> None:
        self.everything += 1


def _storage(content: Any = None, library: Any = None) -> Any:
    """`Storage` with only the three fields read here."""
    return type("Storage", (), {"content": content, "database": None, "library": library})()


# --- the groupers: whichever way pictures arrive, whether they become a set is asked in one place


async def test_a_folder_of_pictures_becomes_a_set_when_the_preference_says_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[Any] = []

    async def set_from_folder(folder_id: str, **kwargs: Any) -> None:
        made.append((folder_id, kwargs["name"]))

    monkeypatch.setattr(photo_sets, "set_from_folder", set_from_folder)
    monkeypatch.setattr(wiring, "part_of_app", lambda app, part: None)
    hub = _Hub(**{photo_sets.FOLDER_SETS_KEY: True})

    settled = imports._folder_grouper(None, _storage(), hub)  # type: ignore[arg-type]
    await settled("folder-1", "Holiday")

    assert made == [("folder-1", "Holiday")]


async def test_a_folder_makes_no_set_when_the_preference_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked per folder, so the switch takes effect on the next scan, not the next restart."""

    async def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a set was made with the preference off")

    monkeypatch.setattr(photo_sets, "set_from_folder", refuse)
    hub = _Hub(**{photo_sets.FOLDER_SETS_KEY: False})

    settled = imports._folder_grouper(None, _storage(), hub)  # type: ignore[arg-type]
    await settled("folder-1", "Holiday")

    assert hub.asked == [photo_sets.FOLDER_SETS_KEY]


async def test_an_archive_becomes_a_set_when_the_preference_says_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[Any] = []

    async def set_from_archive(asset_ids: list[str], **kwargs: Any) -> None:
        made.append((asset_ids, kwargs["root_id"], kwargs["rel_path"], kwargs["name"]))

    monkeypatch.setattr(photo_sets, "set_from_archive", set_from_archive)
    monkeypatch.setattr(wiring, "part_of_app", lambda app, part: None)
    hub = _Hub(**{photo_sets.ARCHIVE_SETS_KEY: True})

    settled = imports._archive_grouper(None, _storage(), hub)  # type: ignore[arg-type]
    await settled("root-1", "galleries/shoot.zip", "shoot", ["a1", "a2"])

    assert made == [(["a1", "a2"], "root-1", "galleries/shoot.zip", "shoot")]


async def test_an_archive_makes_no_set_when_the_preference_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a set was made with the preference off")

    monkeypatch.setattr(photo_sets, "set_from_archive", refuse)
    hub = _Hub(**{photo_sets.ARCHIVE_SETS_KEY: False})

    settled = imports._archive_grouper(None, _storage(), hub)  # type: ignore[arg-type]
    await settled("root-1", "galleries/shoot.zip", "shoot", ["a1"])

    assert hub.asked == [photo_sets.ARCHIVE_SETS_KEY]


class _Model:
    """A loaded model that can give its memory back, and whether its weights are here."""

    def __init__(self, *, ready: bool = False) -> None:
        self.released = 0
        self.is_ready = ready

    def release(self) -> None:
        self.released += 1

    async def ready(self) -> bool:
        return self.is_ready


class _Understanding:
    def __init__(self, *, faces_ready: bool = False) -> None:
        self.faces = _Model(ready=faces_ready)
        self.semantic = _Model()


class _Watcher:
    def __init__(self) -> None:
        self.refreshed = 0

    async def refresh(self) -> None:
        self.refreshed += 1


def _capture_import_handlers(
    monkeypatch: pytest.MonkeyPatch, hub: _Hub, content: Any = None
) -> dict[str, Any]:
    """Build the import handlers and keep everything they were handed: the rules given to the media
    slice are reachable no other way. `content` answers where a file lives, for a file's own ask."""
    caught: dict[str, Any] = {}

    def register(**kwargs: Any) -> None:
        caught.update(kwargs)

    monkeypatch.setattr(media_jobs, "register_handlers", register)
    for slice_module in (library_roots, capture, search, faces):
        monkeypatch.setattr(slice_module, "register_handlers", lambda **_kw: None)
    monkeypatch.setattr(wiring, "part_of_app", lambda app, part: None)
    # The gate is a PART of the application, read by the screen and the catch-up pass; caught here.
    monkeypatch.setattr(
        imports, "provide", lambda _app, _part, value: caught.setdefault("policy", value)
    )
    monkeypatch.setattr(imports, "_folder_grouper", lambda *a: None)
    monkeypatch.setattr(imports, "_archive_grouper", lambda *a: None)

    # The job registry, restored as found: registering a type twice raises, and a test that wants
    # the closure for two settings builds the handlers twice.
    saved = dict(worker_pool._HANDLERS)
    try:
        imports.build_imports(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            _storage(content),
            None,  # type: ignore[arg-type]
            hub,  # type: ignore[arg-type]
            # The queue: the quarantine sweep queues its next run, and the off switches are declared
            # on its board.
            caught.setdefault("queue", _QueueWithABoard()),
            media.Accelerator(
                HardwareReport(
                    cpu_count=1,
                    total_ram_bytes=None,
                    worker_concurrency=1,
                    cuda=False,
                    rocm=False,
                    transcode_encoders=(),
                    warnings=(),
                )
            ),
        )
    finally:
        worker_pool._HANDLERS.clear()
        worker_pool._HANDLERS.update(saved)
    return caught


def _capture_should_generate(monkeypatch: pytest.MonkeyPatch, hub: _Hub) -> Any:
    """Whether a derivative of this kind is made at all."""
    return _capture_import_handlers(monkeypatch, hub)["should_generate"]


def _capture_chosen_shape(monkeypatch: pytest.MonkeyPatch, hub: _Hub) -> Any:
    """Which hover-clip recipe is in force."""
    return _capture_import_handlers(monkeypatch, hub)["chosen_shape"]


def _capture_settings_reaction(
    monkeypatch: pytest.MonkeyPatch,
    hub: _Hub,
    *,
    needing: list[str] | None = None,
    faces_ready: bool = False,
) -> tuple[Any, dict[str, Any]]:
    """Build the settings reaction; return it and the things it may touch."""
    caught: dict[str, Any] = {}

    def provide(_app: Any, part: Any, value: Any) -> None:
        caught["reaction"] = value

    monkeypatch.setattr(reactions, "provide", provide)
    parts = {
        "content": _Content(needing),
        "queue": _Queue(),
        "watcher": _Watcher(),
        "understanding": _Understanding(faces_ready=faces_ready),
        "clock": _TaskClock(),
    }
    reactions.build_settings_reactions(
        None,  # type: ignore[arg-type]
        _storage(parts["content"]),
        parts["queue"],  # type: ignore[arg-type]
        hub,  # type: ignore[arg-type]
        parts["watcher"],  # type: ignore[arg-type]
        parts["understanding"],  # type: ignore[arg-type]
        parts["clock"],  # type: ignore[arg-type]
    )
    return caught["reaction"], parts


# --- which derivatives a scan builds


async def test_a_job_type_with_no_switch_over_it_is_always_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anything not in the map of switchable things defaults to yes, so a renamed job type is not
    silently stopped."""
    decide = _capture_should_generate(monkeypatch, _Hub())

    assert await decide("a-job-nobody-put-a-switch-over") is True


#: Every job the probe can start, and EVERY switch that must be on for it, since a handler that
#: asks and returns is still written, claimed and recorded per file.
_GOVERNED = [
    # Not the thumbnail: it asks no switch, since it is how a file is drawn at all
    # (`test_every_file_gets_its_thumbnail_whatever_the_switches_say`).
    (media_jobs.PREVIEW, (importing.GENERATE_KEY, performance.GENERATE_PREVIEWS_KEY)),
    (media_jobs.SPRITE, (importing.GENERATE_KEY, performance.GENERATE_SPRITES_KEY)),
    (media_jobs.REMUX, (importing.GENERATE_KEY, performance.REPAIR_PLAYBACK_KEY)),
    # An arriving file's fingerprints, handed out by the read as a job of their own.
    (media_jobs.FINGERPRINT_FILE, (importing.GENERATE_KEY, performance.GENERATE_FINGERPRINTS_KEY)),
    (
        faces.FACE_SCAN,
        (importing.IDENTIFY_KEY, faces.ENABLED_KEY, performance.SCAN_FACES_ON_IMPORT_KEY),
    ),
    (
        semantic.SEMANTIC_DESCRIBE,
        (importing.IDENTIFY_KEY, semantic.ENABLED_KEY, semantic.DESCRIBE_ON_IMPORT_KEY),
    ),
    # A file arriving is read for a mark too, not only on Read now.
    (
        watermarks.WATERMARK_READ,
        (importing.IDENTIFY_KEY, watermarks.ENABLED_KEY, watermarks.READ_ON_IMPORT_KEY),
    ),
]


#: Every whole-library pass that can be switched off, and the setting that switches it.
_SWITCHED = [
    (library_roots.SCAN, importing.SCAN_KEY),
    (library_roots.LIBRARY_SCAN, importing.SCAN_KEY),
    (library_roots.RECONCILE, importing.SCAN_KEY),
    (library_roots.SCAN_COUNT, importing.SCAN_KEY),
    (dedup.DEDUP_SCAN, dedup.SCAN_KEY),
    (suggestions.SUGGESTION_SCAN, suggestions.SCAN_KEY),
    (stash_boxes.STASH_SWEEP, stash_boxes.ASK_NEW_FILES_KEY),
]


@pytest.mark.parametrize(("job_type", "key"), _SWITCHED)
async def test_each_whole_library_pass_declares_the_setting_that_switches_it_off(
    monkeypatch: pytest.MonkeyPatch, job_type: str, key: str
) -> None:
    """Declared at the composition root, the only place that knows both halves."""
    board = _capture_import_handlers(monkeypatch, _Hub())["queue"].switchboard

    switch = board.switch_of(job_type)

    assert switch is not None, f"{job_type} can still not be switched off"
    assert switch.key == key
    assert switch.refusal.strip(), "a refusal with no sentence tells a person nothing"


async def test_the_read_of_one_file_is_left_alone_by_the_scan_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping scans does not stop probing a file already taken in: the probe and the import are in
    the SCAN family too, and a file with no probe cannot be laid out."""
    board = _capture_import_handlers(monkeypatch, _Hub())["queue"].switchboard

    assert board.switch_of(media_jobs.PROBE) is None, "switching Scan off would stop the probe"
    assert board.switch_of(capture.IMPORT) is None, "switching Scan off would stop an import"


async def test_a_switch_reads_its_setting_rather_than_holding_the_boot_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read per request: a change takes effect on the next job."""
    hub = _Hub(**{importing.SCAN_KEY: True})
    board = _capture_import_handlers(monkeypatch, hub)["queue"].switchboard

    assert await board.refusal(library_roots.SCAN) is None
    hub.answers[importing.SCAN_KEY] = False
    assert await board.refusal(library_roots.SCAN) is not None


async def test_the_stash_box_lookups_set_to_a_press_are_never_queued_on_their_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the lookup task set to Only when I press it, a settling fingerprint's ask is refused;
    a press never is, and moving the When back lets the next ask through."""
    hub = _Hub(**{stash_boxes.ASK_NEW_FILES_KEY: False})
    board = _capture_import_handlers(monkeypatch, hub)["queue"].switchboard

    assert await board.refusal(stash_boxes.STASH_SWEEP) is not None
    assert await board.refusal(stash_boxes.STASH_SWEEP, pressed=True) is None
    hub.answers[stash_boxes.ASK_NEW_FILES_KEY] = True
    assert await board.refusal(stash_boxes.STASH_SWEEP) is None


#: The three passes a landing file is read for, by the one task that reads it once.
_IDENTIFIED = (faces.FACE_SCAN, semantic.SEMANTIC_DESCRIBE, watermarks.WATERMARK_READ)


class _Nowhere:
    """The content store, asked where a file lives: in no folder, so the library's answer holds."""

    async def locations(self, _asset_id: str) -> list[Any]:
        return []


async def test_the_three_passes_ride_the_import_as_one_task_per_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Faces, meaning and the watermark read are handed out as one task that reads the file once,
    marked as a landing file's so it asks the import switches when it runs."""
    handed = _capture_import_handlers(monkeypatch, _Hub())

    assert importing.IDENTIFY_FILE in handed["follow_on"]
    assert handed["follow_on_payloads"][importing.IDENTIFY_FILE] == {ARRIVED: True}
    for job_type in _IDENTIFIED:
        assert job_type not in handed["follow_on"], f"{job_type} would read the file again"


async def test_an_arriving_files_one_read_is_priced_from_the_self_tests_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The media slice is handed the Build's own answer, asked of the self-test's runner when a
    file lands rather than when the wiring is built."""
    handed = _capture_import_handlers(monkeypatch, _Hub())
    asked: list[Path] = []

    class Machine:
        async def read_rates(self, path: Path) -> None:
            asked.append(path)

    monkeypatch.setattr(wiring, "part_of_app", lambda app, part: Machine())
    assert await handed["read_rates"](Path("clip.mp4")) is None
    assert asked == [Path("clip.mp4")]


@pytest.mark.parametrize("job_type", _IDENTIFIED)
async def test_the_one_task_is_handed_out_while_any_of_its_passes_is_wanted(
    monkeypatch: pytest.MonkeyPatch, job_type: str
) -> None:
    """One pass switched on is enough to hand the task out, and that pass is what it makes; with
    all three off no idle task is queued."""
    keys = dict(_GOVERNED)[job_type]
    hub = _Hub(**dict.fromkeys(keys, True))
    handed = _capture_import_handlers(monkeypatch, hub, content=_Nowhere())

    assert await handed["should_generate"](importing.IDENTIFY_FILE, "a1") is True
    wanted = await imports.arriving_products(handed["policy"], "a1")
    assert wanted == [key for key, kind in imports.ON_ARRIVAL if kind == job_type]
    assert await handed["should_generate"](importing.IDENTIFY_FILE, None) is False

    hub.answers[keys[-1]] = False
    assert await handed["should_generate"](importing.IDENTIFY_FILE, "a1") is False


async def test_the_music_follow_on_only_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan's music follow-on only files a fingerprint taken at staging, never reading a file:
    a press, the Build and the card send no such key."""
    from sift.slices import music

    handed = _capture_import_handlers(monkeypatch, _Hub())

    assert music.AUDIO_FINGERPRINT in handed["follow_on"]
    assert handed["follow_on_payloads"][music.AUDIO_FINGERPRINT] == {music.CLAIM_ONLY: True}


@pytest.mark.parametrize(("job_type", "keys"), _GOVERNED)
async def test_each_switch_governs_the_stage_it_names(
    monkeypatch: pytest.MonkeyPatch, job_type: str, keys: tuple[str, ...]
) -> None:
    """Read per file, so turning one off takes effect on the next file; the job types and settings
    belong to two slices, and only this closure knows both."""
    every = dict.fromkeys(keys, True)
    assert await _capture_should_generate(monkeypatch, _Hub(**every))(job_type) is True

    # One at a time: a job gated on the wrong key passes a test that turns everything off together.
    for missing in keys:
        decide = _capture_should_generate(monkeypatch, _Hub(**{**every, missing: False}))
        assert await decide(job_type) is False, f"{missing} was off and {job_type} ran anyway"


# --- what happens the moment a setting is saved


async def test_a_setting_nothing_reacts_to_moves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    react, parts = _capture_settings_reaction(monkeypatch, _Hub())

    await react({"appearance.theme"})

    assert parts["watcher"].refreshed == 0
    assert parts["understanding"].faces.released == 0
    assert parts["understanding"].semantic.released == 0
    assert parts["content"].dropped == []
    assert parts["queue"].enqueued == []


async def test_saving_a_timed_tasks_setting_hands_it_to_the_one_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed task whose settings changed has its waiting run taken back and placed again; the
    placing is the scheduler's (`test_task_timing.py`)."""
    react, parts = _capture_settings_reaction(
        monkeypatch, _Hub(**{library_roots.quarantine.KEEP_DAYS_KEY: 30})
    )

    await react({library_roots.quarantine.KEEP_DAYS_KEY})

    assert parts["clock"].reading == [{library_roots.quarantine.KEEP_DAYS_KEY}]
    assert parts["queue"].enqueued == [], "the reaction queues nothing of its own"


async def test_moving_quiet_hours_moves_every_timed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    react, parts = _capture_settings_reaction(monkeypatch, _Hub())

    await react({tasks.FROM_KEY})

    assert parts["clock"].everything == 1
    assert parts["queue"].forgot_quiet_hours is True, "and the next claim asks the range again"


async def test_an_old_switch_saved_reaches_the_when_it_became(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A screen still writing the retired key reaches the task through its When."""
    react, parts = _capture_settings_reaction(monkeypatch, _Hub())

    await react({importing.GENERATE_KEY})

    assert "tasks.generate.when" in parts["clock"].reading[0]


async def test_turning_face_recognition_off_gives_the_memory_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching faces off drops the models held for the life of the process, immediately."""
    react, parts = _capture_settings_reaction(monkeypatch, _Hub(**{faces.ENABLED_KEY: False}))

    await react({faces.ENABLED_KEY})

    assert parts["understanding"].faces.released == 1


async def test_changing_the_face_model_family_measures_every_face_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new face family measures every face again from the kept pictures, immediately, but only once
    its weights are present: the fetch asks for it itself."""
    react, parts = _capture_settings_reaction(
        monkeypatch, _Hub(**{faces.ENABLED_KEY: True}), faces_ready=True
    )

    await react({faces.MODEL_KEY})

    assert parts["queue"].settled == [(faces.FACE_REMEASURE, 0)]


async def test_turning_people_from_files_on_makes_the_held_names_people_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turning on making people from facial fingerprints, or recognition, runs the pass immediately; off
    asks for nothing, since a person made stays."""
    react, parts = _capture_settings_reaction(
        monkeypatch, _Hub(**{faces.PEOPLE_FROM_FILES_KEY: True})
    )
    await react({faces.PEOPLE_FROM_FILES_KEY})
    assert parts["queue"].settled == [(faces.FACE_PEOPLE_FROM_FILES, 0)]

    react, parts = _capture_settings_reaction(
        monkeypatch, _Hub(**{faces.PEOPLE_FROM_FILES_KEY: False})
    )
    await react({faces.PEOPLE_FROM_FILES_KEY})
    assert parts["queue"].settled == []

    react, parts = _capture_settings_reaction(monkeypatch, _Hub(**{faces.ENABLED_KEY: True}))
    await react({faces.ENABLED_KEY})
    assert (faces.FACE_PEOPLE_FROM_FILES, 0) in parts["queue"].settled


async def test_a_model_family_whose_weights_are_not_here_measures_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    react, parts = _capture_settings_reaction(monkeypatch, _Hub(**{faces.ENABLED_KEY: True}))

    await react({faces.MODEL_KEY})

    assert parts["queue"].settled == []


async def test_turning_face_recognition_on_does_not_drop_the_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only on the way off; turning on loads them when first needed."""
    react, parts = _capture_settings_reaction(monkeypatch, _Hub(**{faces.ENABLED_KEY: True}))

    await react({faces.ENABLED_KEY})

    assert parts["understanding"].faces.released == 0


async def test_turning_searching_by_meaning_off_gives_its_memory_back_and_keeps_the_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The models are dropped when Smart Search is switched off; the index is kept."""
    react, parts = _capture_settings_reaction(monkeypatch, _Hub(**{semantic.ENABLED_KEY: False}))

    await react({semantic.ENABLED_KEY})

    assert parts["understanding"].semantic.released == 1
    assert parts["content"].dropped == []


async def test_turning_the_playback_repair_off_keeps_every_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The switch stops Sift MAKING copies and deletes none; `Settings > Maintenance` deletes them
    (`kernel.tidy.RepackagedCopies`)."""
    react, parts = _capture_settings_reaction(
        monkeypatch, _Hub(**{performance.REPAIR_PLAYBACK_KEY: False})
    )

    await react({performance.REPAIR_PLAYBACK_KEY})

    assert parts["content"].dropped == []
    assert parts["queue"].enqueued == []


async def test_turning_the_playback_repair_back_on_asks_for_the_work_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turning repair back on asks for it: the finding pass looks only at unmeasured files."""
    react, parts = _capture_settings_reaction(
        monkeypatch,
        _Hub(**{performance.REPAIR_PLAYBACK_KEY: True}),
        needing=["a1", "a2"],
    )

    await react({performance.REPAIR_PLAYBACK_KEY})

    assert parts["queue"].enqueued == [
        (media_jobs.REMUX, {"asset_id": "a1"}),
        (media_jobs.REMUX, {"asset_id": "a2"}),
    ]
    assert parts["content"].dropped == []


async def test_turning_the_playback_repair_on_asks_for_every_file_in_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every file the rule covers whose copy is missing, one write per queue-sized batch."""
    from sift.wiring import reactions

    monkeypatch.setattr(reactions, "_REPAIRS_AT_ONCE", 2)
    react, parts = _capture_settings_reaction(
        monkeypatch,
        _Hub(**{performance.REPAIR_PLAYBACK_KEY: True}),
        needing=["a1", "a2", "a3", "a4", "a5"],
    )

    await react({performance.REPAIR_PLAYBACK_KEY})

    assert parts["queue"].batches == [2, 2, 1]
    assert [payload["asset_id"] for _type, payload in parts["queue"].enqueued] == [
        "a1",
        "a2",
        "a3",
        "a4",
        "a5",
    ]


async def test_turning_photo_details_on_asks_for_the_pass_over_the_waiting_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching username numbers on reads the files already waiting, not at the next import."""
    react, parts = _capture_settings_reaction(
        monkeypatch, _Hub(**{suggestions.READ_METADATA_KEY: True})
    )

    await react({suggestions.READ_METADATA_KEY})

    assert parts["queue"].settled == [(suggestions.SUGGESTION_SCAN, 0)]


async def test_turning_photo_details_off_asks_for_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    react, parts = _capture_settings_reaction(
        monkeypatch, _Hub(**{suggestions.READ_METADATA_KEY: False})
    )

    await react({suggestions.READ_METADATA_KEY})

    assert parts["queue"].settled == []


# --- work an older library needs and a new one does not


class _CatchUpContent:
    def __init__(self, **owed: bool) -> None:
        self.owed = owed

    async def legacy_identities_remain(self) -> bool:
        return self.owed.get("legacy", False)

    async def any_unread(self) -> bool:
        return self.owed.get("unread", False)

    async def assets_lacking_probe_rows(self, _limit: int) -> list[str]:
        return ["01HX0000000000000000000002"] if self.owed.get("probes", False) else []

    async def previews_of_another_recipe_count(self, _params: object) -> int:
        return 1 if self.owed.get("previews", False) else 0

    async def any_unclassified(self) -> bool:
        return self.owed.get("unclassified", False)


class _Recognition:
    """The faces service at boot: whether anything is owed for an older model, a size floor, a
    tiled HEIF read, or a stash-box filing."""

    def __init__(
        self, owed: bool = False, floor: bool = False, tiles: bool = False, boxed: bool = False
    ) -> None:
        self.owed = owed
        self.floor = floor
        self.tiles = tiles
        self.boxed = boxed

    async def measured_by_another_model(self) -> bool:
        return self.owed

    async def floor_pass_owed(self) -> bool:
        return self.floor

    async def tile_pass_owed(self) -> bool:
        return self.tiles

    async def box_questions_owed(self) -> bool:
        return self.boxed


class _Tiles:
    """Smart Search at boot: whether any HEIF photo was described from one tile."""

    def __init__(self, owed: bool = False) -> None:
        self.owed = owed

    async def tile_pass_owed(self) -> bool:
        return self.owed


class _Segments:
    """The player's segment cache at boot, re-read from the disk."""

    def __init__(self) -> None:
        self.reloaded = 0

    def reload(self) -> None:
        self.reloaded += 1


class _Marks:
    def __init__(self, owed: bool = False) -> None:
        self.owed = owed

    async def marks_without_still(self, _limit: int) -> bool:
        return self.owed


class _ChosenShape:
    """A settings hub that answers with one preference: which shape a preview has."""

    def __init__(self, shape: str = sampling.DEFAULT_PREVIEW_SHAPE) -> None:
        self.shape = shape

    async def get_app(self, _key: str) -> str:
        return self.shape

    async def tell_changed_defaults(self, _running: str) -> int:
        return 0


class _SettledQueue:
    def __init__(self) -> None:
        self.asked: list[str] = []
        self.priorities: list[int | None] = []

    async def enqueue_when_settled(self, job_type: str, *, priority: int | None = None) -> str:
        self.asked.append(job_type)
        self.priorities.append(priority)
        return "job-1"


async def test_a_library_with_nothing_owing_queues_nothing_at_boot(tmp_path: Path) -> None:
    """A library with nothing owing queues nothing at boot, so the dashboard stays honest."""
    queue = _SettledQueue()

    await catch_up.catch_up(
        _CatchUpContent(),  # type: ignore[arg-type]
        queue,  # type: ignore[arg-type]
        _Marks(),  # type: ignore[arg-type]
        _Fetches(),  # type: ignore[arg-type]
        _ChosenShape(),  # type: ignore[arg-type]
        tmp_path,
        _Recognition(),  # type: ignore[arg-type]
        _Segments(),  # type: ignore[arg-type]
        _Sets(),  # type: ignore[arg-type]
        _Tiles(),  # type: ignore[arg-type]
    )

    assert queue.asked == []


class _Sets:
    """The photo-set service at boot: which of Sift's own sets are short."""

    def __init__(self, short: int = 0) -> None:
        self.short = short

    async def under_floor(self, _floor: int) -> list[str]:
        return [f"set-{index}" for index in range(self.short)]


class _Fetches:
    """The download ledger's orphan sweep, counted: always asked, being one indexed read."""

    def __init__(self, stuck: int = 0) -> None:
        self.stuck = stuck
        self.asked = 0

    async def settle_orphans(self) -> int:
        self.asked += 1
        return self.stuck


async def test_a_download_left_running_by_a_restart_is_settled_at_boot(tmp_path: Path) -> None:
    """A download left `running` by a restart is settled at boot: a killed worker runs no code, and
    every row found at boot was left by a stopped process."""
    fetches = _Fetches(stuck=2)

    await catch_up.catch_up(
        _CatchUpContent(),  # type: ignore[arg-type]
        _SettledQueue(),  # type: ignore[arg-type]
        _Marks(),  # type: ignore[arg-type]
        fetches,  # type: ignore[arg-type]
        _ChosenShape(),  # type: ignore[arg-type]
        tmp_path,
        _Recognition(),  # type: ignore[arg-type]
        _Segments(),  # type: ignore[arg-type]
        _Sets(),  # type: ignore[arg-type]
        _Tiles(),  # type: ignore[arg-type]
    )

    assert fetches.asked == 1


async def test_staged_files_no_job_can_ask_for_are_swept_at_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed drop's bytes go at boot once the queue no longer holds the job, off the loop."""
    swept: list[Path] = []

    def sweep(data_dir: Path) -> int:
        swept.append(data_dir)
        return 2

    monkeypatch.setattr(capture, "sweep_staging", sweep)

    await catch_up.catch_up(
        _CatchUpContent(),  # type: ignore[arg-type]
        _SettledQueue(),  # type: ignore[arg-type]
        _Marks(),  # type: ignore[arg-type]
        _Fetches(),  # type: ignore[arg-type]
        _ChosenShape(),  # type: ignore[arg-type]
        tmp_path,
        _Recognition(),  # type: ignore[arg-type]
        _Segments(),  # type: ignore[arg-type]
        _Sets(),  # type: ignore[arg-type]
        _Tiles(),  # type: ignore[arg-type]
    )

    assert swept == [tmp_path]


async def test_every_kind_of_catching_up_is_asked_for_when_it_is_owed(tmp_path: Path) -> None:
    """Every catching-up is asked for at boot when owed: a scan skips unchanged files."""
    queue = _SettledQueue()

    await catch_up.catch_up(
        _CatchUpContent(  # type: ignore[arg-type]
            legacy=True,
            unread=True,
            unclassified=True,
            previews=True,
            probes=True,
        ),
        queue,  # type: ignore[arg-type]
        _Marks(owed=True),  # type: ignore[arg-type]
        _Fetches(),  # type: ignore[arg-type]
        _ChosenShape(),  # type: ignore[arg-type]
        tmp_path,
        _Recognition(owed=True, floor=True, tiles=True, boxed=True),  # type: ignore[arg-type]
        _Segments(),  # type: ignore[arg-type]
        _Sets(),  # type: ignore[arg-type]
        _Tiles(owed=True),  # type: ignore[arg-type]
    )

    assert queue.asked == [
        media_jobs.REIDENTIFY,
        media_jobs.READ_UNREAD,
        faces.FACE_REMEASURE,
        faces.FACE_FLOOR,
        faces.FACE_BOX_QUESTIONS,
        faces.FACE_WHOLE_PICTURE,
        semantic.SEMANTIC_WHOLE_PICTURE,
        media_jobs.KEEP_PROBES,
        media_jobs.RECLASSIFY,
        media_jobs.REBUILD_PREVIEWS,
        loops.LOOP_STILLS,
    ]
    # All queue behind a press.
    assert queue.priorities == [BACKGROUND_PRIORITY] * len(queue.asked)


# --- one feature's refusals, said in the words of the feature that asked


class _Deleter:
    def __init__(self, raising: Exception | None = None) -> None:
        self.raising = raising
        self.calls: list[tuple[str, Any]] = []

    async def remove(self, asset_id: str, **kwargs: Any) -> None:
        self.calls.append((asset_id, kwargs))
        if self.raising is not None:
            raise self.raising


async def test_a_removal_asked_for_by_the_duplicate_finder_reaches_the_deleter() -> None:
    deleter = _Deleter()
    seam = file_actions._RemovalSeam(deleter)  # type: ignore[arg-type]

    await seam.remove("a1", mode="sift", actor=None, location_id="loc-1")  # type: ignore[arg-type]

    assert deleter.calls == [("a1", {"mode": "sift", "actor": None, "location_id": "loc-1"})]


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (delete.NotFound("gone"), dedup.NotFound),
        (delete.NotAllowed("not yours"), dedup.NotAllowed),
        (delete.DeleteRefused("read-only"), dedup.RemovalRefused),
    ],
)
async def test_a_refusal_arrives_as_something_the_asking_feature_understands(
    raised: Exception, expected: type[Exception]
) -> None:
    """The delete feature's refusals are translated, or a read-only folder answers 500, not 403."""
    seam = file_actions._RemovalSeam(_Deleter(raised))  # type: ignore[arg-type]

    with pytest.raises(expected, match=str(raised)):
        await seam.remove("a1", mode="sift", actor=None)  # type: ignore[arg-type]


# --- a Loop asks for its own still


async def test_a_new_mark_asks_for_the_picture_that_stands_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deduped, so the sweep and a fresh save asking for the same moment queue one job; the picture
    is keyed so a second would only rewrite the row."""
    queue = _Queue()
    caught: dict[str, Any] = {}

    class _Service:
        def __init__(self, _database: Any, *, wants_still: Any) -> None:
            caught["wants_still"] = wants_still

    monkeypatch.setattr(loops, "LoopService", _Service)
    monkeypatch.setattr(loops, "register_handlers", lambda **_kw: None)
    monkeypatch.setattr(catalog, "provide", lambda *_a: None)

    catalog.build_loops(None, _storage(), queue)  # type: ignore[arg-type]
    await caught["wants_still"]("a1", 1_900)

    assert queue.enqueued == [(media_jobs.LOOP_THUMBNAIL, {"asset_id": "a1", "at_ms": 1_900})]


# --- starting the process
#
# What a self-hoster can fix (a mistyped path, an unwritable directory, a SQLite without the search
# extension) arrives as a sentence before the server starts, not buried in its startup traceback.


class _Uvicorn:
    """Enough of uvicorn to see its configuration and whether it started; `Config` and `Server`,
    since a server built by hand can be asked to stop by the shell."""

    def __init__(self) -> None:
        self.ran: dict[str, Any] = {}
        self.started = 0

    def Config(self, target: str, **kwargs: Any) -> dict[str, Any]:
        self.ran = {"target": target, **kwargs}
        return self.ran

    def Server(self, config: dict[str, Any]) -> _Uvicorn:
        assert config is self.ran, "the server was built from some other configuration"
        return self

    async def startup(self, sockets: Any = None) -> None:
        """uvicorn's own: the lifespan, then the socket; `started` says the socket is open."""
        self.started += 1

    def run(self) -> None:
        self.started += 1


def _stub_start(monkeypatch: pytest.MonkeyPatch, settings: Any, **over: Any) -> _Uvicorn:
    """Everything `main` touches before handing over to the server."""
    uvicorn = _Uvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    monkeypatch.setattr(main, "get_settings", over.get("get_settings", lambda: settings))
    monkeypatch.setattr(main, "ensure_directories", over.get("ensure_directories", lambda _s: None))
    monkeypatch.setattr(
        main,
        "check_sqlite_capabilities",
        over.get("check_sqlite_capabilities", lambda announce: None),
    )
    monkeypatch.setattr(main, "configure_logging", lambda *_a, **_kw: None)
    return uvicorn


def test_the_server_is_handed_the_host_and_port_that_were_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    uvicorn = _stub_start(monkeypatch, settings)

    main.main()

    # THE APP OBJECT: named "sift.main:app", uvicorn would import the module a second time under
    # `python -m sift.main`.
    assert uvicorn.ran["target"] is main.app
    assert uvicorn.ran["host"] == settings.host
    assert uvicorn.ran["port"] == settings.port
    # And started: a server built and never run boots, says nothing and exits.
    assert uvicorn.started == 1


def test_every_start_says_which_version_of_sift_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel.version import app_version

    said: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def __getattr__(self, _level: str) -> Any:
            return lambda event, **fields: said.append((event, fields))

    _stub_start(monkeypatch, Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"))
    monkeypatch.setattr(main, "log", _Log())
    main.main()

    started = [fields for event, fields in said if event == "boot.imported"]
    assert [one["version"] for one in started] == [app_version()]
    assert app_version()


def test_the_servers_own_access_log_is_off_and_its_logging_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No access log (it writes the raw request line) and no server log handlers (they would replace
    the redacting ones)."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    uvicorn = _stub_start(monkeypatch, settings)

    main.main()

    assert uvicorn.ran["access_log"] is False
    assert uvicorn.ran["log_config"] is None


def test_a_setting_that_is_wrong_stops_the_process_with_a_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mistyped path is a sentence, with no traceback and no server."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    def refuse(_settings: Any) -> None:
        raise ConfigError("Sift cannot create the directory /nope.")

    uvicorn = _stub_start(monkeypatch, settings, ensure_directories=refuse)
    monkeypatch.delenv("SIFT_LOG_LEVEL", raising=False)

    with pytest.raises(SystemExit) as exit_code:
        main.main()

    said = str(exit_code.value)
    assert "cannot create the directory" in said
    assert "Traceback" not in said
    assert "SIFT_LOG_LEVEL=DEBUG" in said, "it must say where the technical detail is"
    assert uvicorn.ran == {}, "the server started after a refusal"


def test_a_database_that_cannot_do_what_sift_needs_stops_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked before the server starts."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    def refuse(announce: bool) -> None:
        raise DatabaseError("this SQLite was built without FTS5.")

    uvicorn = _stub_start(monkeypatch, settings, check_sqlite_capabilities=refuse)
    monkeypatch.delenv("SIFT_LOG_LEVEL", raising=False)

    with pytest.raises(SystemExit, match="without FTS5"):
        main.main()

    assert uvicorn.ran == {}


def test_asking_for_debug_gets_the_whole_technical_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detail behind the plain sentence is one variable away."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    def refuse(_settings: Any) -> None:
        raise ConfigError("Sift cannot create the directory /nope.")

    _stub_start(monkeypatch, settings, ensure_directories=refuse)
    monkeypatch.setenv("SIFT_LOG_LEVEL", "debug")

    with pytest.raises(SystemExit) as exit_code:
        main.main()

    said = str(exit_code.value)
    assert "Traceback" in said
    assert "ConfigError" in said


# --- the numbers the worker pool converges on


class _Database:
    """The two things the pool configuration asks the database."""

    def __init__(self, *, resizes: bool) -> None:
        self.resizes = resizes
        self.readers = 12
        self.asked: list[int] = []

    async def resize_readers(self, wanted: int) -> bool:
        self.asked.append(wanted)
        return self.resizes

    async def execute(self, _sql: str, _params: object = ()) -> None:
        # No interrupted runs to settle.
        return None

    async def fetch_all(self, _sql: str, _params: object = ()) -> list[object]:
        return []

    def write(self) -> _Writing:
        # The ledger keeps the long passes' prices on the first tick; nothing reads them here.
        return _Writing()


class _Writing:
    async def __aenter__(self) -> _Writing:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, _sql: str, _params: object = ()) -> None:
        return None

    async def executemany(self, _sql: str, _rows: object = ()) -> None:
        return None


class _PoolQueue:
    async def unfinished_by_type(self) -> dict[str, int]:
        return {}

    async def due_by_type(self) -> dict[str, int]:
        # No work waiting for a later moment.
        return await self.unfinished_by_type()

    async def demand_by_type(self) -> dict[str, int]:
        # No quiet-hours work.
        return await self.unfinished_by_type()


def _capture_pool_config(
    monkeypatch: pytest.MonkeyPatch, hub: _Hub, *, resizes: bool = False
) -> tuple[Any, _Database]:
    """Build the workers and keep what the pool is handed, without waiting on the timer."""
    caught: dict[str, Any] = {}
    database = _Database(resizes=resizes)

    class _Pool:
        def __init__(self, _queue: Any, **kwargs: Any) -> None:
            caught["read_config"] = kwargs["read_config"]
            caught["initial_limits"] = kwargs["limits"]
            caught["initial_concurrency"] = kwargs["concurrency"]
            caught["ledger"] = kwargs["ledger"]

        async def start(self) -> None:
            return None

    monkeypatch.setattr(workers, "WorkerPool", _Pool)
    monkeypatch.setattr(workers, "SystemCapabilities", lambda **_kw: None)
    monkeypatch.setattr(workers, "provide", lambda *_a: None)
    monkeypatch.setattr(download, "AdminMasterKey", lambda *_a: None)

    return caught, database


class _Runner:
    def __init__(self, prices: Mapping[str, float]) -> None:
        self.state = SimpleNamespace(running=False)
        self.kept = dict(prices)

    async def prices(self) -> dict[str, float]:
        return self.kept

    async def rates(self) -> None:
        return None


async def _pool_config(
    monkeypatch: pytest.MonkeyPatch,
    hub: _Hub,
    *,
    resizes: bool = False,
    encoders: tuple[str, ...] = (),
    queue: Any = None,
    prices: Mapping[str, float] | None = None,
) -> tuple[dict[str, Any], _Database]:
    caught, database = _capture_pool_config(monkeypatch, hub, resizes=resizes)
    hardware = HardwareReport(
        cpu_count=8,
        total_ram_bytes=16 << 30,
        worker_concurrency=4,
        cuda=bool(encoders),
        rocm=False,
        transcode_encoders=encoders,
        warnings=(),
    )
    store = type(
        "Storage",
        (),
        {"content": None, "library": None, "access": None, "database": database},
    )()
    accelerator = media.Accelerator(hardware)
    caught["accelerator"] = accelerator
    # Which product each maker's job is for; none here.
    parts = {
        importing.PRODUCTS.name: [],
        performance.SELF_TEST_RUNNER.name: _Runner(prices or {}),
    }
    app = SimpleNamespace(state=SimpleNamespace(**parts))
    await workers.build_workers(
        app,  # type: ignore[arg-type]
        store,
        queue if queue is not None else _PoolQueue(),  # type: ignore[arg-type]
        hardware,
        hub,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        accelerator,
    )
    return caught, database


class _Importing(_PoolQueue):
    """A queue mid first import: files to read, and thumbnails of those read."""

    def __init__(self, demand: dict[str, int]) -> None:
        self.demand = demand

    async def unfinished_by_type(self) -> dict[str, int]:
        return dict(self.demand)


async def test_the_read_makes_room_for_the_thumbnails_it_hands_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thumbnail waiting counts as waited-on work, halving the read's share and never capped, so
    a first import's thumbnails do not wait behind every read."""
    alone, _ = await _pool_config(monkeypatch, _Hub(), queue=_Importing({media_jobs.PROBE: 200}))
    waiting, _ = await _pool_config(
        monkeypatch, _Hub(), queue=_Importing({media_jobs.PROBE: 200, media_jobs.THUMBNAIL: 3})
    )

    workers = alone["initial_concurrency"]
    assert alone["initial_limits"][media_jobs.PROBE] == workers
    assert waiting["initial_limits"][media_jobs.PROBE] == workers // 2
    assert media_jobs.THUMBNAIL not in waiting["initial_limits"]

    # The fingerprints a read hands out, likewise cut to their share by a waiting thumbnail.
    hashing, _ = await _pool_config(
        monkeypatch, _Hub(), queue=_Importing({media_jobs.FINGERPRINT_FILE: 190})
    )
    both, _ = await _pool_config(
        monkeypatch,
        _Hub(),
        queue=_Importing({media_jobs.FINGERPRINT_FILE: 190, media_jobs.THUMBNAIL: 3}),
    )
    assert hashing["initial_limits"][media_jobs.FINGERPRINT_FILE] == workers
    assert both["initial_limits"][media_jobs.FINGERPRINT_FILE] == workers // 2


async def test_the_read_pool_size_follows_the_worker_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising the worker count raises the read connections, or they leave the browser nothing."""
    caught, database = await _pool_config(monkeypatch, _Hub(), resizes=True)

    assert database.asked, "the read pool was never asked to follow the worker count"
    assert database.asked[0] == readers_for(caught["initial_concurrency"])


async def test_a_fresh_library_is_priced_from_the_benchmark_as_a_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no run in the history, the time left is the benchmark's price, as the least it takes."""
    caught, _database = await _pool_config(monkeypatch, _Hub(), prices={"identify": 15.8})

    found = await caught["ledger"].estimate(Family.IDENTIFY, ["face_scan"], left=100, at_once=4)

    assert found is not None
    assert (found.quick_seconds, found.slow_seconds, found.floor) == (395, 395, True)


async def test_a_read_pool_already_the_right_size_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-op unless the number moved."""
    _caught, database = await _pool_config(monkeypatch, _Hub(), resizes=False)

    assert len(database.asked) == 1


class _Lanes:
    """The installed storage lanes, reduced to the one setting the pool pushes."""

    def __init__(self, *, changes: bool) -> None:
        self.changes = changes
        self.asked: list[int] = []

    async def configure(
        self, *, network_reads_at_once: int, measured: Mapping[str, int] | None = None
    ) -> bool:
        self.asked.append(network_reads_at_once)
        return self.changes


async def test_the_share_read_cap_reaches_the_installed_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network share's read cap is pushed on the pool's own timer, since it can change while Sift
    runs."""
    installed = _Lanes(changes=True)
    monkeypatch.setattr(lanes, "installed", lambda: installed)

    await _pool_config(monkeypatch, _Hub(**{performance.SHARE_READS_KEY: 3}))

    assert installed.asked == [performance.resolve_share_reads(3)]


async def test_a_cap_on_downloads_at_the_same_time_reaches_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Downloads are outside the processor budget: they wait on somebody else's server."""
    caught, _database = await _pool_config(
        monkeypatch, _Hub(**{download.AT_ONCE_KEY: 3, download.PAUSED_KEY: False})
    )

    assert caught["initial_limits"][download.DOWNLOAD] == 3


async def test_downloads_left_automatic_add_no_cap_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no entry, downloads share the worker count."""
    caught, _database = await _pool_config(
        monkeypatch, _Hub(**{download.AT_ONCE_KEY: 0, download.PAUSED_KEY: False})
    )

    assert download.DOWNLOAD not in caught["initial_limits"]


async def test_the_segment_cap_follows_the_card_being_given_up_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three segments together on a card, one on a processor, asked of the accelerator on the pool's
    timer, so a card given up mid-session lowers the cap within a tick."""
    caught, _database = await _pool_config(monkeypatch, _Hub(), encoders=("h264_nvenc",))
    accelerator = caught["accelerator"]
    assert caught["initial_limits"][player_service.TRANSCODE] > 1, (
        "a working card was capped at one"
    )

    async def refuse(encoder: media.Encoder, _decode: tuple[str, ...]) -> str:
        if encoder is not media.Encoder.CPU:
            raise media.FFmpegError("the driver refused")
        return "rendered"

    for _ in range(media.GIVE_UP_AFTER):
        await accelerator.run(refuse)

    # Asked of what the POOL polls, so the call site reading the accelerator is what is tested.
    _concurrency, limits = await caught["read_config"]()

    assert limits[player_service.TRANSCODE] == 1


# --- two features that must not import each other, asked one question here


class _UserHub:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    async def get_user(self, user_id: str, key: str) -> Any:
        self.asked.append((user_id, key))
        return self.answer


async def _capture_pin_question(monkeypatch: pytest.MonkeyPatch, hub: Any) -> Any:
    """Build auth and keep the vault question it is handed, assembled here where both are in
    scope."""
    caught: dict[str, Any] = {}

    def service(_database: Any, **kwargs: Any) -> Any:
        caught["may_reopen_with_pin"] = kwargs["may_reopen_with_pin"]
        return None

    monkeypatch.setattr(auth, "AuthService", service)
    monkeypatch.setattr(auth, "Hasher", lambda _params: None)
    monkeypatch.setattr(auth, "resolve_argon2_params", lambda _ram: None)
    monkeypatch.setattr(auth, "VaultUnlockStore", lambda: None)
    monkeypatch.setattr(sign_in, "provide", lambda *_a: None)

    hardware = HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 << 30,
        worker_concurrency=2,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )
    settings = type("Settings", (), {"session_ttl_seconds": 60})()
    tunnels = type("Tunnels", (), {"start_enabled": None})()

    sign_in.build_auth(
        None,  # type: ignore[arg-type]
        settings,
        hardware,
        _storage(),
        None,  # type: ignore[arg-type]
        hub,
        None,  # type: ignore[arg-type]
        tunnels,
    )
    return caught["may_reopen_with_pin"]


@pytest.mark.parametrize("enabled", [True, False])
async def test_whether_a_short_pin_may_reopen_a_session_is_the_users_own_setting(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    hub = _UserHub(enabled)

    question = await _capture_pin_question(monkeypatch, hub)
    answer = await question("user-1")

    assert answer is enabled
    assert hub.asked == [("user-1", vault.APP_LOCK_ENABLED_KEY)]


async def test_a_setting_that_has_never_been_saved_reads_as_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset preference is a no, not a None."""
    question = await _capture_pin_question(monkeypatch, _UserHub(None))

    assert await question("user-1") is False


async def test_the_hover_clip_recipe_is_read_per_file_rather_than_bound_at_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preview shape is read when a clip is cut, not at boot, or a rebuild would use the old
    recipe."""
    hub = _Hub(**{performance.PREVIEW_SHAPE_KEY: "full"})

    shape = _capture_chosen_shape(monkeypatch, hub)

    assert await shape() == "full"
    assert hub.asked == [performance.PREVIEW_SHAPE_KEY]


def test_a_backend_that_was_asked_to_restart_ends_with_a_code_a_supervisor_can_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A requested restart ends with the code the shell reads as "start me again", neither 0 nor a
    crash, which repeated would stop the backend restarting."""
    settings = Settings(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", stop_on_stdin_eof=True
    )
    uvicorn = _stub_start(monkeypatch, settings)
    monkeypatch.setattr(main, "stop_when_the_parent_lets_go", lambda _server: None)

    def _serve_then_be_asked() -> None:
        uvicorn.started += 1
        # As the route does: while still serving, before the process goes.
        assert lifecycle.ask_to_restart(), "nothing was holding the backend to ask"

    monkeypatch.setattr(uvicorn, "run", _serve_then_be_asked)

    lifecycle.forget()
    try:
        with pytest.raises(SystemExit) as ended:
            main.main()
    finally:
        lifecycle.forget()

    assert ended.value.code == lifecycle.RESTART_EXIT_CODE
    assert uvicorn.started == 1


def test_a_backend_that_stopped_on_its_own_ends_the_ordinary_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary shutdown does NOT carry the restart code."""
    settings = Settings(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", stop_on_stdin_eof=True
    )
    uvicorn = _stub_start(monkeypatch, settings)
    monkeypatch.setattr(main, "stop_when_the_parent_lets_go", lambda _server: None)

    lifecycle.forget()
    try:
        main.main()
    finally:
        lifecycle.forget()

    assert uvicorn.started == 1


def test_a_backend_told_to_watch_its_stdin_starts_the_watch_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing stdin asks for a clean stop (Windows has no polite signal), and the watch is armed
    BEFORE the server runs, since `run` returns only when stopping."""
    settings = Settings(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", stop_on_stdin_eof=True
    )
    uvicorn = _stub_start(monkeypatch, settings)
    watched: list[object] = []
    monkeypatch.setattr(
        main,
        "stop_when_the_parent_lets_go",
        lambda server: watched.append((server, uvicorn.started)),
    )

    main.main()

    assert watched == [(uvicorn, 0)], "the watch was not armed before the server was run"
    assert uvicorn.started == 1


def test_a_backend_nobody_is_holding_does_not_watch_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stdin watch is off by default: a container without a terminal starts at EOF."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    uvicorn = _stub_start(monkeypatch, settings)
    watched: list[object] = []
    monkeypatch.setattr(main, "stop_when_the_parent_lets_go", watched.append)

    main.main()

    assert settings.stop_on_stdin_eof is False
    assert watched == []
    assert uvicorn.started == 1


# --- where a file with no target lands


def _capture_default_destination(monkeypatch: pytest.MonkeyPatch, app: Any) -> Any:
    """The closure handed to the importer for "nobody said where"."""
    caught: dict[str, Any] = {}

    class _Capture:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            caught.update(kwargs)

        @staticmethod
        def staging_root(data_dir: Path) -> Path:
            return data_dir / "staging"

    monkeypatch.setattr(capture, "CaptureService", _Capture)
    staging.build_capture(
        app,
        Settings(data_dir=Path("data"), cache_dir=Path("cache")),
        _storage(),
        None,  # type: ignore[arg-type]
    )
    return caught["default_destination"]


async def test_nowhere_to_put_it_is_an_answer_before_the_download_feature_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default destination survives the download part not existing yet, being a closure over the
    application read when asked."""
    app = SimpleNamespace(state=SimpleNamespace())

    assert await _capture_default_destination(monkeypatch, app)() is None


async def test_where_a_dropped_file_lands_is_read_at_the_moment_of_the_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder chosen while something is queued reaches the next drop."""
    app = SimpleNamespace(state=SimpleNamespace())
    destination = _capture_default_destination(monkeypatch, app)

    class _Options:
        def __init__(self, folder_id: str | None) -> None:
            self._folder_id = folder_id

        async def resolve(self, _site_key: str | None) -> Any:
            return SimpleNamespace(dest_folder_id=self._folder_id)

    setattr(app.state, download.SITE_OPTIONS.name, _Options("first-folder"))
    assert await destination() == "first-folder"

    setattr(app.state, download.SITE_OPTIONS.name, _Options("moved-since"))
    assert await destination() == "moved-since"


def test_the_processs_age_is_read_from_proc_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Proc:
        """The kernel's two files, as a Path reads them."""

        def __init__(self, path: object) -> None:
            self.path = str(path)

        def read_text(self, encoding: str = "ascii") -> str:
            if self.path.endswith("stat"):
                return "1 (py thon) S " + " ".join(["0"] * 18) + " 100 0 0"
            return "250.0 100.0"

    monkeypatch.setattr(main, "_WINDOWS", False)
    monkeypatch.setattr(main, "Path", _Proc)
    monkeypatch.setattr(os, "sysconf", lambda _name: 100, raising=False)
    assert main.since_the_process_began_ms() == 249_000

    class _Gone(_Proc):
        def read_text(self, encoding: str = "ascii") -> str:
            raise OSError("no proc here")

    monkeypatch.setattr(main, "Path", _Gone)
    assert main.since_the_process_began_ms() is None


async def test_the_listening_word_is_said_once_the_socket_is_up() -> None:
    said: list[str] = []

    class _Server:
        started = False

        async def startup(self, sockets: object = None) -> None:
            self.started = True

    server = _Server()
    main.say_when_listening(server, said.append)
    await server.startup()
    assert said == [main.READY_LINE]

    class _Never(_Server):
        async def startup(self, sockets: object = None) -> None:
            return None

    quiet = _Never()
    said.clear()
    main.say_when_listening(quiet, said.append)
    await quiet.startup()
    assert said == []
