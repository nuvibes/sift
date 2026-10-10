# SPDX-License-Identifier: AGPL-3.0-or-later
"""Getting the models onto the machine, the sweep's work list, and a setting changed halfway
through a run."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from sift.kernel.access import Actionable, Role
from sift.kernel.access.viewer import Concealment
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, VerdictProduct
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sampling import FACE_SAMPLING_VERSION
from sift.slices.faces import service as service_module
from sift.slices.faces import (
    service_base,
    tuning,
    weights,
)
from sift.slices.faces import settings as face_settings
from sift.slices.faces.frames import Frame
from sift.slices.faces.models import (
    Appearance,
    Attribution,
    Depth,
    PileStatus,
    ScanStatus,
)
from sift.slices.faces.service import (
    FacesDisabled,
    FaceService,
    Recognition,
)
from sift.slices.faces.store import PassRecord, Store
from sift.slices.faces.tests import test_service
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    FakeRecognizer,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
)
from sift.slices.faces.tests.test_service import (
    Scripted,
    _two_files_of_one_stranger,
    by_shade,
    install_reader,
    three_people_frame,
)
from sift.slices.faces.tests.test_service_grouping import (
    _described,
)
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

#: The fixtures this file shares with the files they are defined in, found here by name.
clip = test_service.clip
library = test_service.library
other_clip = test_service.other_clip
restore_pipeline = test_service.restore_pipeline


# --- getting the models onto the machine ----------------------------------------------------


class OneShotSession:
    """A network that answers from memory, and says what it was asked for.

    **Nothing in this file reaches the internet, and that is the point rather than a convenience.**
    A gate whose result depends on whether the machine running it has a working connection to
    github is not a gate: it goes red on a train and green in an office, and after the second time
    that happens nobody reads it. The transfer takes its session from a parameter precisely so this
    can be true.
    """

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.asked: list[str] = []

    def get(self, url: str, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        self.asked.append(url)
        return _Body(self.bodies[url])

    async def __aenter__(self) -> OneShotSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Body:
    def __init__(self, payload: bytes) -> None:
        self.status = 200
        self._payload = payload
        self.headers = {"Content-Length": str(len(payload))}
        self.content = self

    async def iter_chunked(self, size: int):  # type: ignore[no-untyped-def]
        for start in range(0, len(self._payload), size):
            yield self._payload[start : start + size]

    async def __aenter__(self) -> _Body:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """The configured pair, replaced by two small files nobody has to download.

    The real catalog names two files of 2.5 MB and 175 MB on somebody else's servers. What is
    being tested is which of them this asks for and what it does with the answers, and neither
    question needs the real bytes.
    """
    from sift.slices.faces import weights
    from sift.slices.faces.crop import digest as digest_of

    bodies = {
        "https://example.test/detector.onnx": b"detector bytes" * 40,
        "https://example.test/recognizer.onnx": b"recognizer bytes" * 90,
    }
    replacements = {}
    for role, url in (
        ("detector", "https://example.test/detector.onnx"),
        ("recognizer", "https://example.test/recognizer.onnx"),
    ):
        payload = bodies[url]
        replacements[f"accurate.{role}"] = weights.Weight(
            id=f"accurate.{role}",
            role=role,
            family="accurate",
            revision=f"{role}-1",
            url=url,
            digest=digest_of(payload),
            size_bytes=len(payload),
            archive_member=None,
            licence="MIT",
        )
    monkeypatch.setattr(
        weights,
        "pairing",
        lambda family: (replacements["accurate.detector"], replacements["accurate.recognizer"]),
    )
    return bodies


async def test_a_pass_says_the_models_are_not_here_before_it_opens_a_file(
    service: FaceService, listed: dict[str, bytes]
) -> None:
    """Knowable from two names and two files, so it is answered before anything is read.

    Asked late, a family changed while a sweep is running leaves every scan the sweep had queued
    failing with "the detector model has not been installed yet", most of them dead after three
    attempts, for as long as the download takes, with nothing wrong with any of those files.
    """
    assert await service.weights_problem() is not None

    await service.install_models(session_factory=lambda: OneShotSession(listed))

    assert await service.weights_problem() is None


async def test_a_scan_belonging_to_a_run_asks_about_THAT_runs_models(
    service: FaceService, preferences: FakePreferences, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run's family is the one the scan is about to load, so it is the one that must be here.

    A sweep pins its tuning so that a setting changed halfway does not change the rest of it, and
    the family is part of what it pins. Asked about today's setting instead, a run that is perfectly
    able to carry on would be parked, and a run that cannot run would not be.
    """
    monkeypatch.setattr(weights, "installed", lambda _settings, weight: weight.family == "accurate")
    preferences.set(face_settings.MODEL_KEY, "accurate")
    await service.start_run("the-run")
    preferences.set(face_settings.MODEL_KEY, "permissive")

    assert await service.weights_problem() is not None, "today's family has no models"
    assert await service.weights_problem(run="the-run") is None, "the run's family does"


async def test_a_family_nothing_can_download_is_left_to_fail_rather_than_parked(
    service: FaceService, preferences: FakePreferences
) -> None:
    """A wait has to be for something that can arrive. No download makes a name that is not in the
    catalog resolve, so parking a job on one would park it for ever."""
    preferences.set(face_settings.MODEL_KEY, "no-such-family")

    with pytest.raises(weights.WeightError):
        await service.weights_problem()


def test_a_page_that_took_minutes_sizes_the_next_one_down() -> None:
    """One page of 100 items can take eleven minutes, 6.6 s an item.

    A page taking minutes is a job that cannot report where it is, so the next page is sized down.
    Shrinking is unbounded on purpose: being too small costs one extra turn through the queue,
    being too large costs a page nobody can watch.
    """
    measured = 660.0
    smaller = service_module.next_remeasure_page(size=100, seconds=measured)

    assert smaller < 100
    assert smaller == service_module.REMEASURE_PAGE_MIN, "the floor is what stops it here"
    # And the floor is only worth having if it lands near the target at a slow rate like this
    # one. At 6.6 seconds an item a page of five is 33 seconds, which is the point of the
    # number: shrinking further would buy a few seconds and cost a turn through the queue each time.
    assert smaller * (measured / 100) <= service_module.REMEASURE_TARGET_SECONDS * 1.5


def test_a_page_that_was_quick_grows_but_only_by_the_bound() -> None:
    """One noisy measurement must not be able to put the whole library in a single page."""
    grown = service_module.next_remeasure_page(size=20, seconds=0.01)

    assert grown == 20 * service_module.REMEASURE_GROWTH


def test_a_page_that_took_no_measurable_time_grows_rather_than_dividing_by_it() -> None:
    """The ordinary case on an idle machine with a short opening page."""
    assert service_module.next_remeasure_page(size=20, seconds=0.0) == (
        20 * service_module.REMEASURE_GROWTH
    )


def test_the_page_never_climbs_past_its_ceiling_or_falls_below_its_floor() -> None:
    at_the_top = service_module.next_remeasure_page(
        size=service_module.REMEASURE_PAGE_MAX, seconds=0.0
    )
    at_the_bottom = service_module.next_remeasure_page(size=10, seconds=10_000.0)

    assert at_the_top == service_module.REMEASURE_PAGE_MAX
    assert at_the_bottom == service_module.REMEASURE_PAGE_MIN


async def test_fetching_the_models_gets_both_and_records_what_it_installed(
    service: FaceService, store: Store, listed: dict[str, bytes]
) -> None:
    """Sift ships no models, so this is what makes a fresh install able to recognize anything."""
    session = OneShotSession(listed)

    installed = await service.install_models(session_factory=lambda: session)

    assert installed == ["accurate.detector", "accurate.recognizer"]
    assert await service.ready() is True
    assert {str(row["id"]) for row in await store.weights()} == set(installed)


async def test_a_model_already_on_the_machine_is_not_fetched_again(
    service: FaceService, listed: dict[str, bytes]
) -> None:
    """One already on disk was either downloaded and checked, or put there by hand by somebody who
    could not reach the internet, and fetching it again would undo the second case for nothing."""
    first = OneShotSession(listed)
    await service.install_models(session_factory=lambda: first)

    second = OneShotSession(listed)
    installed = await service.install_models(session_factory=lambda: second)

    assert installed == []
    assert second.asked == [], "a model already here was asked for again"


async def test_a_forced_fetch_gets_both_again_even_when_they_are_here(
    service: FaceService, listed: dict[str, bytes]
) -> None:
    """ "Already here" only means the file exists, and a damaged model refuses to load. Forced, the
    fetch asks for both files again and records both; unforced, it still asks for nothing."""
    first = OneShotSession(listed)
    await service.install_models(session_factory=lambda: first)

    again = OneShotSession(listed)
    installed = await service.install_models(session_factory=lambda: again, force=True)

    assert installed == ["accurate.detector", "accurate.recognizer"]
    assert len(again.asked) >= 2, "a forced fetch skipped a model that was already here"
    assert await service.ready() is True


async def test_a_forced_fetch_starts_each_file_afresh(
    service: FaceService, listed: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial left from before is thrown away, so a wrong one cannot be resumed."""
    from sift.slices.faces import weights

    real = weights.fetch
    fresh: list[bool] = []

    async def fetch(*args: Any, **kwargs: Any) -> None:
        fresh.append(kwargs["fresh"])
        await real(*args, **kwargs)

    monkeypatch.setattr(weights, "fetch", fetch)
    await service.install_models(session_factory=lambda: OneShotSession(listed))
    await service.install_models(session_factory=lambda: OneShotSession(listed), force=True)

    assert fresh == [False, False, True, True]


async def test_progress_is_reported_across_the_whole_set_not_per_file(
    service: FaceService, listed: dict[str, bytes]
) -> None:
    """What somebody is watching is one download of two things.

    Reported per file, the bar would run to the end and jump back to the start when the second
    began, which reads as the download restarting, on the screen of somebody who has been waiting
    several minutes.
    """
    seen: list[tuple[int, int]] = []
    session = OneShotSession(listed)

    def note(written: int, total: int) -> bool:
        seen.append((written, total))
        return True

    await service.install_models(session_factory=lambda: session, progress=note)

    assert len({total for _, total in seen}) == 1, "the total changed part-way through"
    assert seen == sorted(seen), "the count went backwards, which reads as a restart"
    assert seen[-1][0] == seen[-1][1] == sum(len(body) for body in listed.values())


async def test_stopping_the_transfer_leaves_what_arrived_so_the_next_attempt_resumes(
    service: FaceService, listed: dict[str, bytes]
) -> None:
    """Cancelling is a pause, not a failure.

    The reader stops asking for the next chunk and the partial file stays, so pressing it again
    costs the remainder rather than the whole thing. Reported as "nothing installed" rather than
    raising, because nothing went wrong.
    """
    session = OneShotSession(listed)

    installed = await service.install_models(
        session_factory=lambda: session, progress=lambda written, _total: False
    )

    assert installed == []
    assert await service.ready() is False
    partials = list((service.settings.models_dir / "faces").rglob("*.part"))
    assert partials, "nothing was kept, so pressing it again would start from the beginning"


async def test_nothing_is_fetched_while_the_feature_is_switched_off(
    service: FaceService, preferences: FakePreferences, listed: dict[str, bytes]
) -> None:
    """The consent gate, at the one route that can make this machine reach the internet.

    Downloading a model for a feature nobody has consented to is exactly the network call the gate
    exists to prevent, and this is where it would happen.
    """
    preferences.set("faces.enabled", False)
    session = OneShotSession(listed)

    with pytest.raises(FacesDisabled):
        await service.install_models(session_factory=lambda: session)

    assert session.asked == []


# --- the sweep's work list ---------------------------------------------------------------------


async def test_the_sweep_list_comes_from_what_that_account_may_see(
    service: FaceService,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """Through the access layer, not the assets table.

    A slice reading that table writes a second copy of the scoping rule, and this one runs in a
    background job where nobody would notice the two disagreeing. So the sweep asks the layer that
    already knows, and covers what the user who asked for it can see.
    """
    admin = await create_user(temp_db, Role.ADMIN)

    waiting, total, _ = await service.needs_scanning_page(admin, offset=0, limit=50)

    assert clip.asset.id in waiting
    assert total >= 1


async def test_a_file_already_looked_at_is_not_swept_again(
    service: FaceService,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """A scan row is written for every file examined, including one with no faces in it, and it
    records the tuning it was produced under, so a file looked at under the settings in force
    right now is not offered again, and a sweep run twice does not do the library twice."""
    admin = await create_user(temp_db, Role.ADMIN)
    await service.scan(clip.asset.id)

    waiting, _, _ = await service.needs_scanning_page(admin, offset=0, limit=50)

    assert clip.asset.id not in waiting


async def test_every_floor_a_face_was_judged_against_is_in_the_tag_that_marks_a_scan_stale(
    service: FaceService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A floor left out of the tag is a floor nothing re-scans for.

    The tag is what a sweep compares a stored scan against to decide whether it is still current.
    The fourth floor (how much of the square came from inside the frame) is a constant rather
    than a preset. Left out of the tag, raising it would make no file stale: the sweep would find
    the whole library settled and the squares the floor exists to reject would stay exactly where
    they were.

    Checked by moving the floor and watching the tag move, rather than by reading the string it is
    built from, because the string is the thing that would be wrong.
    """
    before = (await service.configuration()).digest
    monkeypatch.setattr(tuning, "MIN_CONTAINMENT", tuning.MIN_CONTAINMENT + 0.1)

    assert (await service.configuration()).digest != before


async def test_changing_the_model_family_marks_nothing_stale_and_changes_what_faces_are_compared_by(
    service: FaceService, preferences: FakePreferences
) -> None:
    """The family is not in the tuning shape: in it, changing it would make every scan stale and
    the next sweep read the whole library, the opposite of what the setting's own words promise.
    Which model described a file is on its scan row, and a file described by another model
    is measured again from its stored pictures instead."""
    before = await service.configuration()
    preferences.set(face_settings.MODEL_KEY, "permissive")
    after = await service.configuration()

    assert after.shape == before.shape
    assert after.digest == before.digest
    assert after.recognizer != before.recognizer


async def test_changing_how_a_face_is_judged_marks_the_library_stale(
    service: FaceService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The floors are in the tag, and so is a version for the MEASURE underneath them.

    Reading the size floor off the long side of the box rather than the short one changes what is
    accepted while every number in the tag stays exactly where it was, so without a version the
    whole library would go on reading as settled and nothing would ever be looked at again under
    the better measure. A fix nothing re-scans is not a fix.
    """
    before = (await service.configuration()).shape
    monkeypatch.setattr(tuning, "QUALITY_VERSION", tuning.QUALITY_VERSION + 1)

    assert (await service.configuration()).shape != before


async def test_changing_which_moments_a_pass_looks_at_marks_the_library_stale(
    service: FaceService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same hole one step further out, and the one nothing else would catch.

    What is recorded against a scan is the density it was ASKED for: a multiplier. How many
    moments that turns into is the sampler's, so improving the sampler changes what a pass finds
    while every stored scan still reads as produced under identical settings. The library would
    then never be looked at with the better sampler at all.

    In the shape rather than beside the density on purpose: depth has an order to it and this does
    not, so a file sampled by an older sampler is not "less thorough", it is incomparable.
    """
    before = (await service.configuration()).shape
    monkeypatch.setattr(service_base, "FACE_SAMPLING_VERSION", FACE_SAMPLING_VERSION + 1)

    assert (await service.configuration()).shape != before


async def _a_pass_that_gave_up(
    service: FaceService,
    store: Store,
    asset_id: str,
    *,
    reached_ms: int,
    cut_short: bool | None = None,
) -> str:
    """One appearance recorded under the current tuning, from a pass that did not finish.

    Written directly rather than by cutting a real pass short, because what is being tested is
    what happens to a file in that state, not the arithmetic that puts it there, which is the
    pipeline's own and tested there.
    """
    configured = await service.configuration()
    track_ids = await store.replace_pass(
        asset_id,
        [
            Appearance(
                started_ms=0,
                ended_ms=0,
                seen_in=1,
                quality=0.9,
                faces=(_described(0, quality=0.9, vector=person_vector(0)),),
            )
        ],
        [[b"\xff\xd8\xff first"]],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth=configured.depth.value,
            coverage=0.25,
            frames_sampled=4,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest=configured.digest,
            reached_ms=reached_ms,
            cut_short=cut_short,
        ),
    )
    return track_ids[0]


async def test_a_resumed_pass_with_nothing_stored_starts_every_appearance_afresh(
    service: FaceService,
    store: Store,
    clip: Ingested,
) -> None:
    """The tail of a file whose head found nobody. Everything in it is new by definition."""
    appearance = Appearance(
        started_ms=0,
        ended_ms=0,
        seen_in=1,
        quality=0.9,
        faces=(_described(0, quality=0.9, vector=person_vector(0)),),
    )

    assert await service._rejoin(clip.asset.id, [appearance]) == [None]
    assert await service._rejoin(clip.asset.id, []) == []


async def test_a_resumed_pass_joins_the_appearance_it_is_a_continuation_of(
    service: FaceService,
    store: Store,
    clip: Ingested,
) -> None:
    """Somebody on screen either side of the point the last pass stopped is one appearance.

    And two people alike enough to clear the bar do not both fold into the same stored one: the
    file would lose one of them, silently, which is the failure this pairing exists to avoid.
    """
    first = await _a_pass_that_gave_up(service, store, clip.asset.id, reached_ms=1)
    same_person = Appearance(
        started_ms=2,
        ended_ms=2,
        seen_in=1,
        quality=0.8,
        faces=(_described(2, quality=0.8, vector=person_vector(0)),),
    )
    somebody_else = Appearance(
        started_ms=3,
        ended_ms=3,
        seen_in=1,
        quality=0.7,
        faces=(_described(3, quality=0.7, vector=person_vector(4)),),
    )

    joined = await service._rejoin(clip.asset.id, [same_person, somebody_else])

    assert joined[0] == first
    assert joined[1] is None

    # And the same face twice: the first takes the stored appearance, the second finds it spoken
    # for and becomes its own. Two people alike enough to clear the bar must not both fold into one.
    twice = await service._rejoin(clip.asset.id, [same_person, same_person])
    assert twice[0] == first
    assert twice[1] is None


async def test_a_card_that_died_underneath_a_session_is_named_on_the_screen_until_a_restart(
    service: FaceService, settings: Settings, hardware: Any
) -> None:
    """The runtime would happily say the card can be opened again. The runner knows it is dead
    for the life of the process, and the settings screen (the one place somebody is looking)
    says so and says what to do, instead of "Ready" over a sweep that fails every file."""
    from sift.slices.faces.runner import Runner

    assert await service.device_problem() is None
    runner = Runner(settings, hardware, device="cpu")
    runner._broken = "an NVIDIA graphics card stopped answering. Restart Sift to use it again."
    service._runner = runner

    problem = await service.device_problem()

    assert problem is not None and "Restart Sift" in problem


async def test_a_file_none_of_whose_moments_decoded_is_a_verdict_and_keeps_what_it_had(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    content_store: ContentStore,
) -> None:
    """A pass with moments to look at that gets nothing back from any of them is a verdict on the
    file, not "no faces" at full coverage replacing what an earlier pass found: a share stalled
    past its timeout and a corrupt file both produce it. Nothing is replaced, the sweep leaves the
    file out, and a retry is one press away."""
    from sift.slices.faces import pipeline as pipeline_module

    # `install_reader` wraps whatever `Pipeline.run` is, and the helper below installs one: a
    # second install would wrap the first and read ITS frames. Put the real one back between.
    unwrapped = pipeline_module.Pipeline.run
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    before = await store.tracks_of(clip.asset.id)
    assert before, "the earlier pass found a face to keep"
    pipeline_module.Pipeline.run = unwrapped  # type: ignore[method-assign]

    # A file with a length plans moments to look at; a reader that hands none of them back is the
    # decoder returning nothing, which is the case. A file with no length plans nothing at all.
    await content_store.record_probe(clip.asset.id, width=400, height=300, duration_ms=10_000)
    await install_reader(service, Scripted([]))
    await service.scan(clip.asset.id)

    verdict = await content_store.verdict_of(clip.asset.id, VerdictProduct.FACES)
    assert verdict is not None and verdict.code == "no_frame_decoded"
    assert await store.tracks_of(clip.asset.id) == before, "what the earlier pass found stays"
    admin = await create_user(temp_db, Role.ADMIN)
    waiting, _, _ = await service.needs_scanning_page(admin, offset=0, limit=50)
    assert clip.asset.id not in waiting, "a verdicted file is not offered by the sweep"
    assert other_clip.asset.id not in waiting, "and a settled one still is not"


async def test_a_damaged_file_is_one_verdict_in_the_decoders_words_and_no_retry(
    service: FaceService,
    clip: Ingested,
    content_store: ContentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decoder saying the bytes are broken is a verdict carrying its words, never a failure
    the queue would try again; a refusal of any other kind is still raised."""
    from sift.kernel import media
    from sift.slices.faces import pipeline as pipeline_module

    await content_store.record_probe(clip.asset.id, width=400, height=300, duration_ms=10_000)

    async def damaged(self: object, path: object, **_kwargs: object) -> object:
        raise media.FFmpegError("failed: [h264 @ 0x5e] Invalid NAL unit size (512 > 30)")

    monkeypatch.setattr(pipeline_module.Pipeline, "run", damaged)
    await service.scan(clip.asset.id)
    verdict = await content_store.verdict_of(clip.asset.id, VerdictProduct.FACES)
    assert verdict is not None and verdict.code == "no_frame_decoded" and not verdict.transient
    assert (
        verdict.reason == "The decoder refused the picture: [h264] Invalid NAL unit size (512 > 30)"
    )

    async def stalled(self: object, path: object, **_kwargs: object) -> object:
        raise media.FFmpegError("the share stopped answering")

    monkeypatch.setattr(pipeline_module.Pipeline, "run", stalled)
    with pytest.raises(media.FFmpegError, match="stopped answering"):
        await service.scan(clip.asset.id)


async def test_a_file_read_and_found_to_have_no_picture_is_a_verdict_not_no_faces(
    service: FaceService,
    clip: Ingested,
    content_store: ContentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read, and with no size: looked at, it would be a two pixel square filed as nobody in it."""
    from sift.slices.faces import pipeline as pipeline_module

    await content_store.record_probe(clip.asset.id, duration_ms=10_000)

    async def opened(self: object, path: object, **_kwargs: object) -> object:
        raise AssertionError("a file with no picture was looked at")

    monkeypatch.setattr(pipeline_module.Pipeline, "run", opened)
    await service.scan(clip.asset.id)
    verdict = await content_store.verdict_of(clip.asset.id, VerdictProduct.FACES)
    assert verdict is not None and verdict.code == "no_picture"


async def test_a_file_whose_pass_did_not_finish_is_offered_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """The other half of the settings-digest rule, and the same shape.

    A pass cut short by the time limit recorded under the current tuning like any other would never
    be offered again, and the part nobody had looked at would never be looked at.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    await _a_pass_that_gave_up(service, store, clip.asset.id, reached_ms=1000)

    waiting, _, _ = await service.needs_scanning_page(admin, offset=0, limit=50)

    assert clip.asset.id in waiting


async def test_carrying_on_from_where_a_pass_gave_up_keeps_what_it_had_found(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A resumed pass adds; it does not replace.

    Replacing would throw away every face found in the part of the file this pass did not re-read,
    which would make resuming worse than starting again: the file would end up holding only
    whatever its last fragment contained.
    """
    first = await _a_pass_that_gave_up(service, store, clip.asset.id, reached_ms=1)
    # A tail with somebody NEW in it. Without this the pass finds nothing, and the assertion below
    # would hold for a resume that never happened, proving nothing at all.
    frame, boxes = three_people_frame()
    detector.placed = {0: [(boxes[0], 0.9)]}
    recognizer.rule = by_shade({210: 6})
    await install_reader(service, Scripted([frame]))

    await service.scan(clip.asset.id)

    held = await store.tracks_of(clip.asset.id)
    assert {track.id for track in held} >= {first}
    assert len(held) > 1, "the tail found nobody, so this proved nothing about resuming"


async def test_a_finished_pass_leaves_nothing_for_a_later_one_to_carry_on_from(
    service: FaceService,
    store: Store,
    clip: Ingested,
) -> None:
    """Otherwise a file that has been looked at end to end keeps a stale position, and the next
    pass over it under the same settings reads only its tail."""
    await service.scan(clip.asset.id)

    scan = await store.scan_of(clip.asset.id)

    assert scan is not None
    assert scan.coverage >= 1.0
    assert scan.reached_ms is None


@pytest.mark.parametrize(
    ("cut_short", "pressed", "carries_on"),
    [
        # A press on a pass that read every moment it could: looks again from the first.
        (False, True, False),
        # A press on a row older than the record of why it stopped: looking again is the press.
        (None, True, False),
        # A press on a pass the time limit stopped: reads on, or a long file is never read through.
        (True, True, True),
        # Nobody pressed (a sweep, an arriving file): carries on from any position, as before.
        (False, False, True),
    ],
)
async def test_a_press_looks_again_unless_the_last_pass_was_cut_short(
    service: FaceService,
    store: Store,
    clip: Ingested,
    cut_short: bool | None,
    pressed: bool,
    carries_on: bool,
) -> None:
    """A press of "Look for faces again" on a deep pass that had read 58 of a video's 60 moments
    must not carry on from the last of them, read one frame and keep the old appearances. Coverage
    below one and a stored position are both true of a pass that read everything it could; only
    WHY it stopped tells a finished pass from one the limit cut short."""
    first = await _a_pass_that_gave_up(
        service, store, clip.asset.id, reached_ms=1, cut_short=cut_short
    )

    await service.scan(clip.asset.id, again=pressed)

    kept = first in {track.id for track in await store.tracks_of(clip.asset.id)}
    assert kept is carries_on


async def test_a_resumed_pass_adds_its_moments_to_the_count_rather_than_replacing_it(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The count is added to, not replaced like the columns describing the pass, or 58 moments
    would become 1 after the resume that read the last one while the coverage beside it summed.
    The earlier stretch's faces stay on the file, so its moments stay in the count."""
    await _a_pass_that_gave_up(service, store, clip.asset.id, reached_ms=1)
    frame, boxes = three_people_frame()
    detector.placed = {0: [(boxes[0], 0.9)]}
    recognizer.rule = by_shade({210: 6})
    await install_reader(service, Scripted([frame]))

    await service.scan(clip.asset.id)

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None and scan.frames_sampled == 4 + 1


@pytest.mark.parametrize(
    ("stopped_early", "cut_short"),
    [(True, True), (False, False)],
)
async def test_a_pass_records_whether_the_time_limit_cut_it_short(
    service: FaceService,
    store: Store,
    clip: Ingested,
    stopped_early: bool,
    cut_short: bool,
) -> None:
    """The time limit is the one stop that leaves work in the file, and the only early stop."""
    from sift.slices.faces import pipeline as pipeline_module

    original = pipeline_module.Pipeline.run

    async def run(self, path, **kwargs):  # type: ignore[no-untyped-def]
        outcome = await original(self, path, **kwargs)
        return replace(outcome, stopped_early=stopped_early)

    pipeline_module.Pipeline.run = run  # type: ignore[method-assign]

    await service.scan(clip.asset.id)

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None and scan.cut_short is cut_short


async def test_a_press_is_refused_naming_the_chosen_models_when_they_are_not_downloaded(
    service: FaceService, preferences: FakePreferences, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Faces screen says not ready, so a press must not start a library pass whose every
    file would then wait on models nobody was fetching. The press asks the models as well as the
    device, and answers in words that do not promise a run nobody started."""
    preferences.set(face_settings.MODEL_KEY, "permissive")
    monkeypatch.setattr(weights, "installed", lambda _settings, weight: False)

    refused = await service.cannot_scan()

    assert refused is not None
    assert refused.startswith("Sift hasn't downloaded the Permissive recognition models yet")
    assert "nothing was started" in refused

    monkeypatch.setattr(weights, "installed", lambda _settings, weight: True)
    assert await service.cannot_scan() is None


async def test_a_pass_under_different_settings_starts_again_rather_than_carrying_on(
    service: FaceService,
    store: Store,
    preferences: FakePreferences,
    clip: Ingested,
) -> None:
    """There is nothing to carry on from: the moments would not be the same moments, and the faces
    already stored were measured against a different rule."""
    first = await _a_pass_that_gave_up(service, store, clip.asset.id, reached_ms=1)
    preferences.set(face_settings.EFFORT_KEY, "deep")

    await service.scan(clip.asset.id)

    assert first not in {track.id for track in await store.tracks_of(clip.asset.id)}


async def test_a_hidden_file_is_swept_like_any_other(
    service: FaceService,
    access: Any,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """A job has no session, and whether the vault is open is a fact about a session.

    So a sweep rebuilt from a user id alone would run with the vault shut, every time, and a hidden
    file would never be scanned: unlocking before pressing the button could not help, because by
    the time the job runs the session is gone. Recognition is machinery rather than a view: what
    it finds is still settled against whoever is asking on every screen that shows it.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    unlocked = replace(admin, show_hidden=True)
    assert await access.set_asset_vault(unlocked, clip.asset.id, vault=True)

    viewer = await service.viewer_for(admin.id)

    assert viewer is not None
    waiting, _, _ = await service.needs_scanning_page(viewer, offset=0, limit=50)
    assert clip.asset.id in waiting


async def test_a_hidden_file_is_still_hidden_from_whoever_is_looking(
    service: FaceService,
    access: Any,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The half that must not change. Scanning a hidden file must not make it visible anywhere.

    The vault-open case is asserted first and asserted to be non-empty, because an absence check
    over a screen that drew nothing proves nothing: the faces have to be there for the shut case
    to be evidence of anything.
    """
    frame, boxes = three_people_frame()
    detector.placed = {0: [(box, 0.9) for box in boxes]}
    recognizer.rule = by_shade({210: 0, 170: 2, 130: 4})
    await install_reader(service, Scripted([frame]))

    admin = await create_user(temp_db, Role.ADMIN)
    unlocked = replace(admin, show_hidden=True)
    assert await access.set_asset_vault(unlocked, clip.asset.id, vault=True)
    await service.scan(clip.asset.id)
    await service.regroup()

    open_vault, _ = await service.piles(unlocked, PileStatus.OPEN)
    shut, _ = await service.piles(admin, PileStatus.OPEN)

    assert open_vault != []
    assert shut == []


async def test_a_placeholder_tile_marks_its_faces_locked_rather_than_showing_them(
    service: FaceService,
    access: Any,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The mode that keeps a locked tile keeps a locked FACE: it does not hand the picture over.

    Placeholder mode is "show me a gap where something is", and this screen has to say the same
    thing the grid says. The crop behind a face is a piece of the file and is refused exactly as
    the file is, so a card that asked for one would draw a broken picture; a card that dropped
    it would disagree with every other screen the same viewer was looking at. It is marked instead.
    """
    frame, boxes = three_people_frame()
    detector.placed = {0: [(box, 0.9) for box in boxes]}
    recognizer.rule = by_shade({210: 0, 170: 2, 130: 4})
    await install_reader(service, Scripted([frame]))

    admin = await create_user(temp_db, Role.ADMIN)
    unlocked = replace(admin, show_hidden=True)
    keeps_the_tile = replace(admin, concealment=Concealment.PLACEHOLDER)
    assert await access.set_asset_vault(unlocked, clip.asset.id, vault=True)
    await service.scan(clip.asset.id)
    await service.regroup()

    open_piles, _ = await service.piles(unlocked, PileStatus.OPEN)
    piles, total = await service.piles(keeps_the_tile, PileStatus.OPEN)

    # An unlocked vault marks nothing: the pictures are theirs to see, and the flag says so.
    assert open_piles != []
    assert all(not face.locked for pile in open_piles for face in pile.faces)

    # Shut, the same faces are still counted (that is what the placeholder is), and every one of
    # them is marked, so no screen asks for a crop the route is going to refuse.
    assert total > 0
    assert piles != []
    assert all(face.locked for pile in piles for face in pile.faces)


async def test_a_locked_face_is_not_named_where_somebody_is_asked_who_this_is(
    service: FaceService,
    store: Store,
    access: Any,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A padlock beside a name says a hidden file exists and that person is in it.

    With "Show a locked tile" on and the vault shut, a person whose every face is on a locked file
    is gathered under the nameless card on the wall of people Sift knows, and the locked face
    carries no name, while her own page, which is about her, keeps it. Unlocked, she is named.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    unlocked = replace(admin, show_hidden=True)
    keeps_the_tile = replace(admin, concealment=Concealment.PLACEHOLDER)
    person_id = await make_person(temp_db, "Anouk Vestergaard")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    track = (await store.tracks_of(clip.asset.id))[0]
    await store.attribute(track.id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED)
    assert await access.set_asset_vault(unlocked, clip.asset.id, vault=True)

    named, _ = await service.identified_people(unlocked)
    assert [card.person_id for card in named] == [person_id]

    locked, total = await service.identified_people(keeps_the_tile)
    assert total == 1
    assert [card.person_id for card in locked] == [None]
    assert all(face.person_name is None and face.locked for card in locked for face in card.faces)

    # Her own page keeps the name on the same locked face.
    own = await service.identified_for(keeps_the_tile, person_id)
    assert [(face.person_name, face.locked) for face in own.items] == [("Anouk Vestergaard", True)]

    # A face of hers on an open file makes her nameable again, and the locked chip under her own
    # card still carries no name, which is the rule living in the one place every screen reads it.
    opened = (await store.tracks_of(other_clip.asset.id))[0]
    await store.attribute(opened.id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED)
    mixed, _ = await service.identified_people(keeps_the_tile)
    assert [card.person_id for card in mixed] == [person_id]
    assert sorted((face.locked, face.person_name) for face in mixed[0].faces) == [
        (False, "Anouk Vestergaard"),
        (True, None),
    ]


async def test_a_locked_face_cannot_be_acted_on(
    service: FaceService,
    access: Any,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Seeing the padlock is not the same as being able to answer for what is behind it.

    The placeholder says there is something here; the decision needs the picture, which is exactly
    what is being withheld. So the gate on acting stays strict while the drawing goes soft, and
    these are two different questions asked of the same viewer.
    """
    frame, boxes = three_people_frame()
    detector.placed = {0: [(box, 0.9) for box in boxes]}
    recognizer.rule = by_shade({210: 0, 170: 2, 130: 4})
    await install_reader(service, Scripted([frame]))

    admin = await create_user(temp_db, Role.ADMIN)
    unlocked = replace(admin, show_hidden=True)
    keeps_the_tile = replace(admin, concealment=Concealment.PLACEHOLDER)
    assert await access.set_asset_vault(unlocked, clip.asset.id, vault=True)
    await service.scan(clip.asset.id)
    await service.regroup()

    piles, _ = await service.piles(keeps_the_tile, PileStatus.OPEN)
    tracks = [face.track_id for pile in piles for face in pile.faces]

    assert tracks != []
    assert await service.touchable_faces(unlocked, tracks) == Actionable(
        allowed=tuple(tracks), concealed=(), refused=()
    )
    # Placeholder mode keeps the TILE and withholds the picture, so these are concealed rather than
    # refused: the user can reach every one of them by unlocking, and the reply says so
    # instead of failing the whole call.
    assert await service.touchable_faces(keeps_the_tile, tracks) == Actionable(
        allowed=(), concealed=tuple(tracks), refused=()
    )

    # SOMEBODY ELSE GETS THE THIRD ANSWER, and it is a different answer rather than a shade of the
    # second. A guest the file was never shared with cannot see it for a reason no PIN would fix, so
    # these are REFUSED: "concealed" carries an offer to unlock, and made to a user with no
    # vault of their own that offer would both fail and confirm that the ids name real faces.
    # Every case above is this user's own vault, which lands in `concealed` whichever concealment
    # mode it is in, so only a guest reaches this branch.
    guest = await create_user(temp_db, Role.GUEST)
    assert await service.touchable_faces(guest, tracks) == Actionable(
        allowed=(), concealed=(), refused=tuple(tracks)
    )


async def test_the_sweep_list_is_empty_while_recognition_is_off(
    service: FaceService,
    preferences: FakePreferences,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """Off means off. A sweep that quietly built a list of the whole library would be doing the one
    thing the consent gate exists to prevent, just without having written it down yet."""
    admin = await create_user(temp_db, Role.ADMIN)
    preferences.set("faces.enabled", False)

    assert await service.needs_scanning_page(admin, offset=0, limit=50) == ([], 0, 0)
    assert await service.lack() is None, "off means nothing is lacking, on the Build's sheet too"

    preferences.set("faces.enabled", True)
    configured = await service.configuration()
    lack = await service.lack()
    assert lack is not None
    assert lack.params == (
        tuning.QUALITY_VERSION,
        FACE_SAMPLING_VERSION,
        configured.digest,
        configured.shape,
        configured.density,
    )


async def test_a_file_scanned_under_different_settings_is_offered_again(
    service: FaceService,
    preferences: FakePreferences,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """A change of depth or quality bar reaches files already in the library.

    The sweep asks whether a file's scan was made under the tuning now in force, not merely whether
    a scan row exists: the tuning that produced each result is stored beside it for exactly this
    comparison.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    await service.scan(clip.asset.id)
    assert clip.asset.id not in (await service.needs_scanning_page(admin, offset=0, limit=50))[0]

    preferences.set(face_settings.EFFORT_KEY, "deep")

    waiting, _, _ = await service.needs_scanning_page(admin, offset=0, limit=50)

    assert clip.asset.id in waiting


@pytest.mark.parametrize("scanned_under", face_settings.EFFORTS)
@pytest.mark.parametrize("now_set_to", face_settings.EFFORTS)
async def test_a_file_is_offered_again_only_when_more_of_it_would_be_looked_at(
    service: FaceService,
    preferences: FakePreferences,
    temp_db: Database,
    clip: Ingested,
    scanned_under: str,
    now_set_to: str,
) -> None:
    """Every pair of efforts, in both directions, against the one rule that governs all of them.

    Turning the effort DOWN must leave what was already looked at more closely alone: it says "do
    not spend that much from now on", not "go back and un-look at everything", and re-reading a
    file at a shallower setting can only lose what the fuller pass found. Turning it UP must offer
    the file again, because that is the whole point of asking.

    Driven off the list of efforts rather than written out pair by pair, so a fourth step cannot be
    added without being covered here. Fast against the others is the pair that matters, because
    those differ in the sampling as well as the depth: an ordering applied to the depth alone would
    make going from deep to fast re-queue an entire library, and look exactly like a sweep
    working.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    preferences.set(face_settings.EFFORT_KEY, scanned_under)
    await service.scan(clip.asset.id)

    preferences.set(face_settings.EFFORT_KEY, now_set_to)
    waiting, _, _ = await service.needs_scanning_page(admin, offset=0, limit=50)

    def moments(effort: str) -> float:
        depth, sampling = face_settings.EFFORT_LEVELS[effort]
        return sampling * (face_settings.DEEP_FACTOR if depth == "deep" else 1.0)

    asks_for_more = moments(now_set_to) > moments(scanned_under)
    assert (clip.asset.id in waiting) is asks_for_more


async def test_a_different_quality_bar_restages_however_deep_the_old_pass_was(
    service: FaceService,
    preferences: FakePreferences,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """Depth is the only axis with an order to it.

    Everything else changes what a pass would ACCEPT rather than how much of the file it sees, so a
    deep result under one quality bar says nothing about what a pass under another would find. It
    restages even though the stored pass was the deeper of the two, which is the case that would be
    silently wrong if "deep beats fast" were applied without checking the rest of the tuning first.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    preferences.set(face_settings.EFFORT_KEY, "deep")
    await service.scan(clip.asset.id)

    preferences.set(face_settings.EFFORT_KEY, "balanced")
    preferences.set(face_settings.QUALITY_KEY, "lenient")
    waiting, _, _ = await service.needs_scanning_page(admin, offset=0, limit=50)

    assert clip.asset.id in waiting


async def test_a_forced_sweep_offers_a_file_that_the_current_settings_already_covered(
    service: FaceService,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """For everything the comparison cannot see: a model swapped underneath, bad crops, or a scan
    that failed so often it was given up on. Nothing else re-queues those."""
    admin = await create_user(temp_db, Role.ADMIN)
    await service.scan(clip.asset.id)

    waiting, _, _ = await service.needs_scanning_page(admin, offset=0, limit=50, force=True)

    assert clip.asset.id in waiting


async def test_the_account_a_queued_sweep_names_is_resolved_or_reported_gone(
    service: FaceService,
    temp_db: Database,
) -> None:
    """A job outlives the request that started it, so the user may have been deleted since."""
    admin = await create_user(temp_db, Role.ADMIN)

    assert (await service.viewer_for(admin.id)) is not None
    assert (await service.viewer_for("01HXNOSUCHACCOUNTATALL0000")) is None


async def test_the_roster_is_empty_while_recognition_is_off(
    service: FaceService, preferences: FakePreferences
) -> None:
    """Off means off, even for a read. The reference gallery is what the feature collected, so
    answering from it while nobody has consented is answering from the thing being consented to."""
    preferences.set("faces.enabled", False)

    assert await service.roster() == []


# --- a setting changed halfway through a run ------------------------------------------------------


async def test_a_run_scans_under_the_settings_it_started_with(
    service: FaceService, preferences: FakePreferences, clip: Ingested, store: Store
) -> None:
    """Changing a setting mid-sweep does not change the rest of that sweep, file by file.

    Were every scan to read the settings for itself, a run started deep and turned down would finish
    as a library part deep and part not, and the list of files the sweep decided to queue, decided
    under the settings at the moment it started, would be the wrong list. Nothing would be corrupted
    by it; the run would simply stop meaning one thing.
    """
    preferences.set(face_settings.EFFORT_KEY, "deep")
    await service.start_run("run-1")

    preferences.set(face_settings.EFFORT_KEY, "fast")
    await service.scan(clip.asset.id, run="run-1")

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert scan.depth == Depth.DEEP


async def test_a_file_scanned_on_its_own_uses_the_settings_as_they_are_now(
    service: FaceService, preferences: FakePreferences, clip: Ingested, store: Store
) -> None:
    """Only a run is pinned. Somebody asking for one file now means now."""
    preferences.set(face_settings.EFFORT_KEY, "deep")
    await service.start_run("run-1")
    preferences.set(face_settings.EFFORT_KEY, "fast")

    await service.scan(clip.asset.id)

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert scan.depth == Depth.FAST


async def test_a_run_whose_tuning_was_never_kept_falls_back_to_the_settings(
    service: FaceService, preferences: FakePreferences, clip: Ingested, store: Store
) -> None:
    """A snapshot older than a week is dropped, and a run written by an older Sift has none.
    Neither is a reason to refuse to scan: the live settings are read instead."""
    preferences.set(face_settings.EFFORT_KEY, "deep")

    await service.scan(clip.asset.id, run="a-run-nobody-wrote-down")

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert scan.depth == Depth.DEEP


async def test_the_machine_limits_are_not_pinned_and_take_effect_immediately(
    service: FaceService, preferences: FakePreferences
) -> None:
    """ "Stop taking my whole processor" is not a request about some future run.

    The tuning is frozen for the length of a run; the limits on how hard the machine may be worked
    are deliberately left out of the snapshot, and the pool re-reads them on its own timer for the
    same reason.
    """
    preferences.set(face_settings.FILE_BUDGET_KEY, 30)
    await service.start_run("run-1")

    preferences.set(face_settings.FILE_BUDGET_KEY, 90)
    now = await service.configuration()
    pinned = now.with_pinned({"family": now.family, "device": now.device})

    assert pinned.budget_seconds == 90.0


class _Clock:
    """The pass's own clock, moved only by the frames it reads."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _TenSecondsAFrame(Scripted):
    """Frames that each take ten seconds to arrive, by the clock the pass reads."""

    def __init__(self, frames: list[Frame], clock: _Clock) -> None:
        super().__init__(frames)
        self.clock = clock
        self.given = 0

    async def stream(self, path, **_: object):  # type: ignore[no-untyped-def]
        self.opens += 1
        for frame in self.frames:
            self.clock.now += 10.0
            self.given += 1
            yield frame


@pytest.mark.parametrize(("stored", "read"), [(30, 3), (90, 9), (face_settings.AUTOMATIC, 20)])
async def test_the_longest_time_stored_for_one_file_is_where_a_scan_stops(
    service: FaceService,
    preferences: FakePreferences,
    clip: Ingested,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    stored: int,
    read: int,
) -> None:
    """The stored number of seconds, not a number handed in: the time limit the pass is built
    with is read from the settings as the scan starts, and Automatic is no limit at all."""
    from sift.slices.faces import pipeline as pipeline_module

    clock = _Clock()
    monkeypatch.setattr(pipeline_module, "time", clock)
    frames = [
        Frame(pixels=noisy_frame(640, 360, seed=at), timestamp_ms=at * 100) for at in range(20)
    ]
    reader = _TenSecondsAFrame(frames, clock)
    await install_reader(service, reader)
    preferences.set(face_settings.FILE_BUDGET_KEY, stored)

    await service.scan(clip.asset.id)

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert scan.cut_short is (stored != face_settings.AUTOMATIC)
    assert reader.given == read


@pytest.mark.parametrize(("stored", "refused"), [("strict", 5), ("balanced", 0), ("lenient", 0)])
async def test_the_lowest_quality_stored_is_the_floor_a_fresh_scan_judges_each_face_against(
    service: FaceService,
    preferences: FakePreferences,
    clip: Ingested,
    store: Store,
    detector: FakeDetector,
    stored: str,
    refused: int,
) -> None:
    """A face between the two size floors, on a file never scanned before: Strict refuses it at
    every moment and the other two keep it, as the setting's own words say."""
    frames = []
    placed = {}
    for index in range(5):
        stamp = index * 100
        frame = noisy_frame(640, 360, seed=index)
        box = draw_face(frame, x=100 + index, y=60, size=104)
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
        placed[stamp] = [(box, 0.9)]
    detector.placed = placed
    await install_reader(service, Scripted(frames))
    preferences.set(face_settings.QUALITY_KEY, stored)

    await service.scan(clip.asset.id)

    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert scan.refused_small == refused
    assert (scan.track_count > 0) is (refused == 0)


async def test_when_the_library_was_last_looked_through(
    service: FaceService, store: Store, temp_db: Database
) -> None:
    """When the last Identify pass ENDED, and whether it was stopped, never when one started
    (or the pane would say "Last looked through" over a pass cancelled seconds in)."""
    assert await service.last_run_at() is None

    await service.start_run("run-1")
    assert await store.last_run_at() is None, "a pass that has only STARTED has not looked through"

    write = (
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, stopped)"
        " VALUES (?, 'identify', ?, ?, ?, ?)"
    )
    await temp_db.execute(write, ("finished", 1_000, 1_900, 1_900, 0))
    assert await store.last_run_at() == 1_900_000
    assert await store.last_run_canceled() is False

    await temp_db.execute(write, ("stopped", 2_000, 2_028, 2_028, 1))
    assert await store.last_run_at() == 2_028_000
    assert await store.last_run_canceled() is True, "and a stopped one says it was canceled"


async def test_the_last_look_through_is_a_run_for_faces_not_any_identify_run(
    store: Store, temp_db: Database
) -> None:
    """Smart Search and the watermarks are Identify too. Read by family, a run of the watermarks
    alone would be this screen's "Last scan", while the Tasks row beside it reads a different
    moment. The runs for faces, by the statement that row reads."""
    write = (
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, stopped, made_for)"
        " VALUES (?, 'identify', ?, ?, ?, ?, ?)"
    )
    await temp_db.execute(write, ("faces-run", 1_000, 1_900, 1_900, 1, '["faces"]'))
    await temp_db.execute(write, ("marks-run", 2_000, 2_100, 2_100, 0, '["watermarks"]'))
    assert await store.last_run_at() == 1_900_000, "the faces run, not the newer watermarks one"
    assert await store.last_run_canceled() is True


async def test_agreeing_to_one_appearance_files_only_a_couple_of_references(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """Filing every frame of the appearance is fine for an appearance a few seconds long, and that
    is not what an appearance always is.

    Somebody on screen through a four-minute video is tracked as many short runs and merged back
    into ONE appearance carrying a frame from each, so a single press would file dozens of
    references, most of somebody's, all from one video, one outfit, one light. References are
    blended into a single description per person, so what was held would describe that video
    rather than that person.

    The appearance is written directly with eight distinct faces, because that is the state under
    test: a real pass reaching it needs a video long enough to be tracked in pieces, which is the
    arithmetic of the tracker rather than of this. Every face is a different description, so none
    is skipped as a near-duplicate: what stops at two is the cap and nothing else.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    configured = await service.configuration()
    faces = tuple(
        _described(moment * 1000, quality=0.9 - moment / 100, vector=person_vector(moment))
        for moment in range(8)
    )
    track_ids = await store.replace_pass(
        clip.asset.id,
        [Appearance(started_ms=0, ended_ms=7000, seen_in=8, quality=0.9, faces=faces)],
        [[b"\xff\xd8\xff picture " + bytes([moment]) for moment in range(8)]],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth=configured.depth.value,
            coverage=1.0,
            frames_sampled=8,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest=configured.digest,
            settings_shape=configured.shape,
            settings_density=configured.density,
        ),
    )
    assert len(await store.faces_of(track_ids[0])) == 8, "the fixture no longer makes one long one"

    await service.confirm(track_ids[0], person_id)

    assert await store.reference_count(person_id) == tuning.REFERENCES_PER_APPEARANCE


async def test_a_poor_crop_is_filed_anyway_when_somebody_confirms_it(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """A crop below the quality floor is filed as evidence all the same: a trade, not a fix.

    Every reference is blended into one averaged description, and a poor one pulls that average
    towards a phone or a blur; that cost is accepted. What it buys is that a press does what it
    appears to: declining silently would let agreeing to a group raise the reference count by less
    than the number agreed to, with nowhere on the screen to find out which had been declined, and
    filtering before naming a group is the person's job rather than Sift's. `teachable` still says
    which crops are poor, and a face that became a reference is marked, so the judgement is offered
    rather than applied.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    configured = await service.configuration()
    poor = tuning.REFERENCE_QUALITY / 2
    faces = tuple(
        _described(moment * 1000, quality=poor, vector=person_vector(moment)) for moment in range(3)
    )
    track_ids = await store.replace_pass(
        clip.asset.id,
        [Appearance(started_ms=0, ended_ms=2000, seen_in=3, quality=poor, faces=faces)],
        [[b"\xff\xd8\xff picture " + bytes([moment]) for moment in range(3)]],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth=configured.depth.value,
            coverage=1.0,
            frames_sampled=3,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest=configured.digest,
            settings_shape=configured.shape,
            settings_density=configured.density,
        ),
    )

    await service.confirm(track_ids[0], person_id)

    # The name stuck.
    named = await store.track(track_ids[0])
    assert named is not None
    assert named.person_id == person_id
    # And so did the picture, poor as it is. The per-appearance cap still bounds how many of one
    # appearance's frames are kept, which is why this is not simply "all three".
    assert await store.reference_count(person_id) == tuning.REFERENCES_PER_APPEARANCE


async def test_a_run_whose_tuning_cannot_be_read_falls_back_to_the_live_settings(
    service: FaceService, store: Store
) -> None:
    """A snapshot that will not parse is not a reason to refuse to scan.

    It means the row was written by an older Sift or has been damaged, and the answer is to read
    the settings as they are now.
    """
    run = new_id()
    await store.remember_run(run, "{ this is not the json it was")
    configured = await service.configuration()

    assert await service._as_the_run_started(configured, run) == configured


async def test_the_work_left_counts_a_queued_file_it_cannot_see_as_no_moments(
    service: FaceService, temp_db: Database
) -> None:
    """The count is exact and the estimate is not, and they come from different places.

    Files left is read off the queue. Moments are read through the access layer, so a queued file
    this user may not see (or one that has since gone) contributes nothing to the estimate
    while still being counted as work. Zero is the honest answer there, rather than an estimate
    built on no sample at all.
    """
    await temp_db.execute(
        "INSERT INTO jobs (id, type, state, priority, payload, max_attempts, attempts, progress, "
        "created_at, updated_at) VALUES ('unseen-1', 'face_scan', 'queued', 0, ?, 3, 0, 0, 0, 0)",
        ('{"asset_id": "no-such-asset"}',),
    )
    admin = await create_user(temp_db, Role.ADMIN)

    assert await service.work_left(admin) == (1, 0)


async def test_a_queued_job_naming_no_file_is_still_counted_as_work(store: Store) -> None:
    """A payload with no asset id in it.

    The count is read off the queue and the sample is read off the payloads, so the two can
    disagree, and the count is the one a bar counts down. Dropping the row from the count because
    its payload said nothing would leave a bar that never reaches zero.
    """
    await store._db.execute(
        "INSERT INTO jobs (id, type, state, priority, payload, max_attempts, attempts, progress, "
        "created_at, updated_at) VALUES ('empty-1', 'face_scan', 'queued', 0, '{}', 3, 0, 0, 0, 0)",
        (),
    )

    assert await store.waiting_asset_ids(50) == ([], 1)


async def test_releasing_when_nobody_was_deleted_does_no_work(
    service: FaceService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grouping the whole library again is expensive, and this runs whenever a person is deleted.

    With nothing stranded there is nothing to put back, so the rebuild has to be skipped rather
    than run over an unchanged library, which is the part worth asserting, since returning zero
    while having rebuilt anyway looks identical from the outside.
    """
    rebuilt = []
    monkeypatch.setattr(service, "regroup", lambda *a, **k: rebuilt.append(True))

    assert await service.release_deleted() == 0
    assert rebuilt == []


async def test_a_person_deleted_on_an_install_with_recognition_off_is_simply_deleted(
    service: FaceService, preferences: FakePreferences
) -> None:
    """The observer the People slice calls, which must not know whether recognition exists.

    An install that never switched the feature on still deletes people, and a refusal here would
    surface as a delete that failed for a reason the person deleting cannot act on.
    """
    preferences.set("faces.enabled", False)

    # The service itself refuses, which is what makes the observer's silence the thing under test.
    with pytest.raises(FacesDisabled):
        await service.release_deleted()

    await Recognition(service).released()
