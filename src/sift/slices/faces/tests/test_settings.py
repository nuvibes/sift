# SPDX-License-Identifier: AGPL-3.0-or-later
"""The switches, and the two things about them that are not preferences.

**The first switch is a consent gate.** It starts off, and off means nothing runs, nothing is
fetched and nothing is written, because what this feature measures is people's faces.

**The named choices mean something.** A preset that no longer maps to numbers, or a section that
does not exist on the settings screen, is a control that either does nothing or cannot be drawn.
"""

from __future__ import annotations

import pytest

from sift.kernel.jobs.quiet_hours import WHEN_QUIET, WHEN_WORK
from sift.kernel.jobs.schedules import registered_schedules, when_key
from sift.kernel.sampling import face_frames
from sift.kernel.settings_registry import (
    SECTIONS,
    SettingError,
    get_registered,
    get_removed,
    get_retired,
)
from sift.slices.faces import settings as face_settings
from sift.slices.faces import tuning

pytestmark = pytest.mark.unit

EVERY_KEY = [
    face_settings.ENABLED_KEY,
    face_settings.MODEL_KEY,
    face_settings.DEVICE_KEY,
    face_settings.EFFORT_KEY,
    face_settings.QUALITY_KEY,
    face_settings.FILE_BUDGET_KEY,
    face_settings.BUDGET_KEY,
    face_settings.CORE_SHARE_KEY,
    face_settings.THREAD_COUNT_KEY,
]


def test_recognizing_faces_starts_switched_off() -> None:
    """A consent gate, not a default that happens to start off.

    It sits on Identify rather than on Privacy: the switch and the twelve settings it governs are
    one pane, and a consent gate on a different screen from the thing it consents to is a switch
    somebody turns on without seeing what it turns on. What makes it a consent gate is the default,
    which is asserted here and is the half that matters.
    """
    declared = get_registered(face_settings.ENABLED_KEY)

    assert declared is not None
    assert declared.default is False
    assert declared.section == "Identify"


@pytest.mark.parametrize("key", EVERY_KEY)
def test_every_switch_is_declared_and_can_be_drawn(key: str) -> None:
    declared = get_registered(key)

    assert declared is not None
    assert declared.scope.value == "app"
    assert declared.section in SECTIONS
    assert declared.label.strip()
    assert declared.help.strip()


def test_the_switches_are_global_because_this_is_a_capability_rather_than_a_taste() -> None:
    assert all(get_registered(key).scope.value == "app" for key in EVERY_KEY)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "key",
    [
        face_settings.REMOVED_SUGGEST_KEY,
        face_settings.REMOVED_ATTACH_KEY,
        face_settings.REMOVED_REFERENCE_GROUPS_KEY,
    ],
)
def test_how_sure_a_match_must_be_is_not_a_setting(key: str) -> None:
    """The lines a match is judged against, and how many samples describe a person, are fixed in
    the code. Each old key is declared removed, so History still names a change made to it."""
    assert get_registered(key) is None
    removed = get_removed(key)
    assert removed is not None and removed.label.strip()


def test_every_named_choice_means_something() -> None:
    """A preset with no numbers behind it is a control that does nothing."""
    assert set(face_settings.EFFORT_LEVELS) == set(face_settings.EFFORTS)
    assert set(face_settings.QUALITY_BARS) == set(face_settings.QUALITY_LEVELS)
    assert all(sampling > 0 for _, sampling in face_settings.EFFORT_LEVELS.values())


def test_each_effort_looks_at_more_than_the_one_before_it() -> None:
    """The whole of what the control offers, so the order has to be real."""
    moments = [
        sampling * (face_settings.DEEP_FACTOR if depth == "deep" else 1.0)
        for depth, sampling in (face_settings.EFFORT_LEVELS[name] for name in face_settings.EFFORTS)
    ]

    assert moments == sorted(moments)
    assert len(set(moments)) == len(moments)


def test_a_stricter_level_refuses_more_than_a_lenient_one() -> None:
    """On blur and angle, and on size: strict keeps the recognizer's input size."""
    strict = face_settings.QUALITY_BARS["strict"]
    lenient = face_settings.QUALITY_BARS["lenient"]

    assert strict[0] > lenient[0]
    assert strict[1] > lenient[1]
    assert strict[2] > lenient[2]


def test_the_size_floor_is_112_on_strict_and_96_on_balanced_and_lenient() -> None:
    """112 is the recognizer's input size; a sharp, square-on face 96 across is stretched 1.17
    times onto it, which is as far as any level goes."""
    floors = {name: bar[0] for name, bar in face_settings.QUALITY_BARS.items()}

    assert floors == {"strict": 112, "balanced": 96, "lenient": 96}
    assert tuning.MIN_PIXELS == 112
    assert tuning.MIN_PIXELS_ACCEPTED == 96


def test_the_size_floor_is_not_in_the_fingerprint_a_scan_is_settled_by() -> None:
    """A lowered floor is looked at by the floor pass over the files it can change; in the
    fingerprint it would offer the whole library again. A preset's other floors still move it."""
    from sift.slices.faces.models import Depth
    from sift.slices.faces.service_base import Configured

    def configured(level: tuple[int, float, float]) -> Configured:
        return Configured(
            family="accurate",
            device="cpu",
            depth=Depth.FAST,
            level=level,
            sampling=1.0,
            suggest_above=0.5,
            attach_above=0.6,
            groups=1,
            budget_seconds=None,
        )

    balanced = face_settings.QUALITY_BARS["balanced"]
    as_it_was = (tuning.MIN_PIXELS, balanced[1], balanced[2])

    assert configured(balanced).digest == configured(as_it_was).digest
    assert configured(balanced).digest != configured(face_settings.QUALITY_BARS["strict"]).digest


def test_the_two_ways_of_sharing_the_machine_are_all_offered() -> None:
    """Two, and only two. Waiting for the machine to be quiet is a budget that never starts on a
    desktop in use, so nothing offers it; and WHEN recognition runs is its task's When ("In quiet
    hours"), so this answers only how much of the device it uses."""
    declared = get_registered(face_settings.BUDGET_KEY)

    assert declared is not None
    assert set(declared.choices or ()) == {"share", "threads"}
    assert declared.default == "share"


def test_the_processor_is_the_default_device() -> None:
    declared = get_registered(face_settings.DEVICE_KEY)

    assert declared is not None
    assert declared.default == "cpu"


def test_the_more_accurate_models_are_the_ones_preselected() -> None:
    declared = get_registered(face_settings.MODEL_KEY)

    assert declared is not None
    assert declared.default == "accurate"
    assert set(declared.choices or ()) == set(face_settings.MODELS)


# --- the budget ----------------------------------------------------------------------------------


def test_idle_is_no_longer_offered_and_a_stored_idle_reads_as_the_default() -> None:
    """ "Idle" is not a choice `scans_at_once` has a branch for, so it is not offered, and a value
    stored under it reads back as the default through the hub's own repair."""
    from sift.kernel.settings_registry import get_registered
    from sift.slices.faces import settings as face_settings

    assert "idle" not in face_settings.BUDGETS
    declared = get_registered(face_settings.BUDGET_KEY)
    assert declared is not None
    with pytest.raises(SettingError, match="must be one of"):
        declared.validate("idle")
    assert face_settings.scans_at_once(
        budget="idle", core_share=50, threads=0, workers=8
    ) == face_settings.scans_at_once(budget="share", core_share=50, threads=0, workers=8)


def test_the_match_again_switch_is_removed() -> None:
    """ "Match again when People change" is removed.

    Every change to the people Sift knows re-matches, once, so the key decides nothing: nothing
    reads it, and History still names a change made to it by what it was called.
    """
    removed = get_removed(face_settings.RETIRED_REMATCH_KEY)

    assert get_registered(face_settings.RETIRED_REMATCH_KEY) is None
    assert get_retired(face_settings.RETIRED_REMATCH_KEY) is None
    assert removed is not None and removed.label == "Match again when People change"


def test_declaring_the_switches_twice_is_refused(clean_settings_registry: None) -> None:
    """Two features each believing they own a key is exactly the drift one registry prevents."""
    face_settings.register()

    with pytest.raises(SettingError, match="registered twice"):
        face_settings.register()


# --- how much of the machine a sweep may take -----------------------------------------------------
#
# "What share of the processor to use" is drawn on the Identify screen and stored when somebody
# moves it, and it has to be READ: otherwise a sweep uses every worker there is, the whole library
# goes sluggish while it runs, and turning the control down does not help.


@pytest.mark.parametrize(
    ("share", "workers", "expected"),
    [(50, 8, 4), (100, 8, 8), (25, 8, 2), (10, 8, 1), (1, 8, 1), (50, 1, 1)],
    ids=["half", "all", "a-quarter", "a-tenth", "almost-none", "one-worker-machine"],
)
def test_the_share_decides_how_many_files_are_examined_at_a_time(
    share: int, workers: int, expected: int
) -> None:
    assert (
        face_settings.scans_at_once(budget="share", core_share=share, threads=0, workers=workers)
        == expected
    )


def test_a_share_that_rounds_to_nothing_still_examines_one() -> None:
    """Somebody dragging the slider to its floor is asking for it to be slow, not to stop.

    Zero would switch recognition off through a control that does not say it can, and nothing on
    the screen would explain why a scan never finishes.
    """
    assert face_settings.scans_at_once(budget="share", core_share=1, threads=0, workers=2) == 1


def test_the_share_multiplies_with_the_step_back_and_never_adds() -> None:
    """While the pool steps back to a quarter of twelve, recognition's quarter is taken of the
    three that run, not of the twelve: one file at a time, never three."""
    from sift.kernel.budget import STEP_BACK_SHARE, stepped_workers

    running = stepped_workers(12, STEP_BACK_SHARE)
    stepped = face_settings.scans_at_once(budget="share", core_share=25, threads=0, workers=running)
    alone = face_settings.scans_at_once(budget="share", core_share=25, threads=0, workers=12)
    assert (running, stepped, alone) == (3, 1, 3)
    assert stepped <= running


def test_the_cap_is_never_more_than_the_machine_has() -> None:
    """A cap above the pool is not a cap."""
    assert face_settings.scans_at_once(budget="threads", core_share=50, threads=99, workers=4) == 4


def test_an_exact_thread_count_is_used_exactly() -> None:
    assert face_settings.scans_at_once(budget="threads", core_share=50, threads=2, workers=8) == 2


def test_an_unset_share_falls_back_to_half_rather_than_to_everything() -> None:
    """The safe direction. A library that stays usable while it scans is worth more than a sweep
    that finishes sooner and makes everything else unusable while it does."""
    assert face_settings.scans_at_once(budget="share", core_share=0, threads=0, workers=8) == 4


# --- only overnight ------------------------------------------------------------------------
#
# The window's arithmetic (through midnight, inside one day, both ends equal meaning always) is
# quiet hours', and is held in `kernel/tests/test_job_schedules.py` against the one range every
# task reads. Recognition's share does not change with the clock: quiet hours hold its unpressed
# work back at the claim (`kernel/tests/test_task_timing.py`), and a press is never held.


# --- when it runs, how closely it looks, and where its share of the device is set ----------------


def test_recognition_runs_as_files_arrive_once_somebody_turns_it_on() -> None:
    """The When a value never written reads as. The switch in front of it is the consent and
    starts off, so this answer counts only once recognition is on; a When somebody chose is a
    stored value and stays what they chose."""
    task = registered_schedules()["faces"]

    assert task.when({}) == WHEN_WORK
    assert task.when({when_key("faces"): WHEN_QUIET}) == WHEN_QUIET


def test_the_switch_stands_beside_the_when_and_is_never_read_through_it() -> None:
    """Off is a refusal: nothing runs, a press included. Read through the When, "Only when I press
    it" would read as off and take the feature away from somebody who only wants to press."""
    declared = get_registered(face_settings.ENABLED_KEY)

    assert get_retired(face_settings.ENABLED_KEY) is None
    assert declared is not None and declared.default is False


def test_the_effort_sentence_says_how_often_a_pass_looks() -> None:
    """Every figure in the sentence is the plan the sampler makes, at the file lengths people have,
    and the dial's own ratios are the words "half" and "three times"."""
    declared = get_registered(face_settings.EFFORT_KEY)
    assert declared is not None and declared.disclosure is not None
    said = declared.disclosure
    balanced = face_settings.EFFORT_LEVELS["balanced"][1]
    fast = face_settings.EFFORT_LEVELS["fast"][1]

    assert len(face_frames(10_000, density=balanced)) == 10
    assert len(face_frames(20_000, density=balanced)) == 20
    assert "once a second through a clip up to 20 seconds long" in said
    assert len(face_frames(300_000, density=balanced)) == 30
    assert "20 to 30 times through a video up to 5 minutes long" in said
    assert len(face_frames(3_600_000, density=balanced)) == 30
    assert "30 times through anything longer" in said
    assert fast == balanced / 2
    assert len(face_frames(20_000, density=fast)) == 10
    assert "Fast looks half as often" in said
    assert face_settings.DEEP_FACTOR == 3.0
    assert len(face_frames(3_600_000, density=face_settings.DEEP_FACTOR)) == 60
    assert "Deep looks three times as often, up to 60 times" in said
    assert "moment" not in f"{said} {declared.help}"


def test_how_much_of_the_device_recognition_uses_is_filed_under_performance() -> None:
    """On Concurrency's page, beside the other answers about how hard Sift may push this device."""
    for key in (
        face_settings.BUDGET_KEY,
        face_settings.CORE_SHARE_KEY,
        face_settings.THREAD_COUNT_KEY,
    ):
        declared = get_registered(key)
        assert declared is not None and declared.section == "Performance", key
