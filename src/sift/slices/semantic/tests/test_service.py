# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the feature can do right now, and what it refuses to do.

The guarantees proved here are the ones that do nothing visible when they work:

**Nothing runs while the switch is off.** Not a model load, not a download, not a row. A job queued
before somebody switched it off finds it off and stops rather than doing the work its payload
describes, which is the only thing standing between "I turned that off" and a machine that goes
on reading every file for another hour.

**The four ways it cannot run are told apart.** No add-on, switched off, no models yet, or a device
that is not there. They need four different actions from whoever reads them, and collapsing them
into "unavailable" is how somebody restarts a container to fix a switch.

**Removing the index removes both halves of it.** The numbers, and the record of which files they
came from. Dropping one leaves either an index that reports itself complete and finds nothing, or a
work list that will not run because every file is marked done.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sift.kernel.access import Repository, Role
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, VerdictProduct
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.slices.semantic import records as semantic_records
from sift.slices.semantic import settings as semantic_settings
from sift.slices.semantic import store as semantic_store
from sift.slices.semantic.embed import FRAME_SIZE
from sift.slices.semantic.frames import Moment
from sift.slices.semantic.records import Records
from sift.slices.semantic.service import Coverage, SemanticService
from sift.slices.semantic.store import DIMENSION, VectorStore

pytestmark = pytest.mark.integration

REVISION = "test-revision"


def machine(*, cuda: bool = False) -> HardwareReport:
    return HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 << 30,
        worker_concurrency=3,
        cuda=cuda,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


class Preferences:
    """The settings, from memory. The real one reads the database on every call and so does this."""

    def __init__(self, **values: Any) -> None:
        self.values: dict[str, Any] = {
            semantic_settings.ENABLED_KEY: False,
            semantic_settings.MODEL_KEY: "compact",
            semantic_settings.DEVICE_KEY: "cpu",
            **values,
        }

    async def get_app(self, key: str) -> Any:
        return self.values[key]


class StubEmbedder:
    """Stands in for the models, which are several hundred megabytes and are not shipped."""

    def __init__(self, *, installed: bool = True) -> None:
        self._installed = installed
        self.family = "compact"
        self.device_name = "cpu"
        self.revision = REVISION
        self.unloaded = False
        self.broken: str | None = None
        self.words_refused: str | None = None
        self.described: list[int] = []

    def installed(self) -> bool:
        return self._installed

    def unload(self) -> None:
        self.unloaded = True

    async def describe_pictures(self, frames: Any) -> list[list[float]]:
        self.described.append(len(frames))
        # Distinguishable but valid: each frame gets its own direction.
        return [
            [1.0 if position == index % DIMENSION else 0.0 for position in range(DIMENSION)]
            for index in range(len(frames))
        ]


class StubReader:
    """Stands in for the decoder, so no test needs a real video to prove the wiring."""

    def __init__(self, moments: list[Moment] | None = None) -> None:
        self.moments = moments if moments is not None else []
        self.asked: list[str] = []

    async def read(self, path: Path, *, media_type: str, duration_ms: int) -> list[Moment]:
        self.asked.append(media_type)
        return self.moments


def picture() -> np.ndarray:
    return np.zeros((FRAME_SIZE, FRAME_SIZE, 3), dtype=np.uint8)


async def described(
    database: Database, service: SemanticService, asset_id: str, *, revision: str = REVISION
) -> None:
    """A file in the library with its frames kept AND the work list told, the way a pass leaves it.

    Both halves, because reading a file's description asks the work list first. See
    `SemanticService._frames_of`. Frames without a row there are either a deleted file's leftovers
    (nothing cascades off a virtual table) or a stop between the two writes, and neither is a
    described file. Writing only the frames would be a state the pass itself never leaves behind:
    `_describe_one` marks the file on the line after it keeps them.
    """
    await database.execute(
        "INSERT OR IGNORE INTO assets (id, identity, media_type, size_bytes, added_at) "
        "VALUES (?, ?, 'image', 1, 1700000000)",
        (asset_id, f"digest-{asset_id}"),
    )
    await service.describe_frames(asset_id, [(0, picture())])
    await Records(database).mark(asset_id, revision=revision, frames=1, at_ms=1)


@pytest.fixture
async def wired(
    temp_db: Database, content_store: ContentStore, settings: Settings
) -> tuple[SemanticService, Preferences, StubEmbedder, StubReader]:
    await temp_db.initialize_schema()
    preferences = Preferences()
    reader = StubReader()
    service = SemanticService(
        store=VectorStore(temp_db),
        records=Records(temp_db),
        content=content_store,
        repository=Repository(temp_db, content_store),
        preferences=preferences,
        settings=settings,
        hardware=machine(),
        reader=reader,  # type: ignore[arg-type]
    )
    embedder = StubEmbedder()

    async def use_the_stub() -> StubEmbedder:
        return embedder

    service.embedder = use_the_stub  # type: ignore[assignment,method-assign]
    return service, preferences, embedder, reader


# --- the switch ------------------------------------------------------------------------------


async def test_nothing_is_described_while_the_switch_is_off(wired: Any) -> None:
    """The whole of the consent this feature asks for. A job queued before somebody switched it
    off finds it off and does nothing at all."""
    service, _preferences, embedder, _reader = wired

    kept = await service.describe_frames("asset-1", [(0, picture())])

    assert kept == 0
    assert embedder.described == []


async def test_with_the_switch_on_frames_are_described_and_kept(wired: Any) -> None:
    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True

    kept = await service.describe_frames("asset-1", [(0, picture()), (1000, picture())])

    assert kept == 2
    assert embedder.described == [2]
    assert await service.indexed_frames() == 2


async def test_a_file_with_no_moments_costs_nothing(wired: Any) -> None:
    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True

    assert await service.describe_frames("asset-1", []) == 0
    assert embedder.described == []


# --- the four ways it cannot run -------------------------------------------------------------


async def test_a_machine_without_the_add_on_says_which_of_the_four_it_is(wired: Any) -> None:
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    service._store._database._extensions = frozenset()

    readiness = await service.readiness()

    assert readiness.supported is False
    assert readiness.ready is False
    assert readiness.problem is not None
    assert "Everything else in Sift works without it" in readiness.problem


async def test_switched_off_is_not_a_problem_to_report(wired: Any) -> None:
    """It is a decision somebody made, and a screen that reported it as a fault would send them
    looking for one."""
    service, _preferences, _embedder, _reader = wired

    readiness = await service.readiness()

    assert readiness.supported is True
    assert readiness.enabled is False
    assert readiness.ready is False
    assert readiness.problem is None


async def test_switched_on_with_no_models_reads_as_fetch_them(wired: Any) -> None:
    """The ordinary state one second after somebody turns it on. A fresh install that read as
    broken would send people looking for a fault that is not there."""
    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    embedder._installed = False

    readiness = await service.readiness()

    assert readiness.ready is False
    assert readiness.problem is not None
    assert "have not been obtained" in readiness.problem


async def test_a_device_that_is_not_there_is_named_rather_than_ignored(wired: Any) -> None:
    """A card that is not there would make every scan fail into the job log while the settings
    screen said everything was fine. Asked here, before any work is queued against it."""
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    preferences.values[semantic_settings.DEVICE_KEY] = "nvidia"

    readiness = await service.readiness()

    assert readiness.ready is False
    assert readiness.problem is not None
    assert "graphics card" in readiness.problem


async def test_a_card_that_died_underneath_a_session_is_named_until_a_restart(
    wired: Any,
) -> None:
    """The runtime would happily open the card again; the runner knows it is dead for the life
    of the process, and the screen says what to do rather than reporting the card as fine."""
    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    embedder.broken = "an NVIDIA graphics card stopped answering. Restart Sift to use it again."

    readiness = await service.readiness()

    assert readiness.ready is False
    assert readiness.problem is not None and "Restart Sift" in readiness.problem


async def test_everything_in_place_is_ready(wired: Any) -> None:
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True

    readiness = await service.readiness()

    assert readiness.ready is True
    assert readiness.problem is None


# --- describing one file ---------------------------------------------------------------------


async def test_describing_a_file_records_which_model_did_it(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    reader.moments = [Moment(pixels=picture(), at_ms=0), Moment(pixels=picture(), at_ms=2000)]
    asset_id = await _an_asset(content_store, settings, tmp_path)

    kept = await service.describe_asset(asset_id)

    assert kept == 2
    described = await service._records.described(asset_id)
    assert described is not None
    assert described.revision == REVISION
    assert described.frames == 2


async def test_a_file_with_nothing_readable_is_a_verdict_and_not_recorded_as_done(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """Not marked described with no frames: that would take it off every later sweep, which is
    right, by way of a permanent yes about a file nothing had looked at, which is not. It is a
    verdict on the file: the sweep and the Build leave it out, and the count of what is left does
    too."""
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    asset_id = await _an_asset(content_store, settings, tmp_path)
    reader.moments = []

    assert await service.describe_asset(asset_id) == 0

    verdict = await content_store.verdict_of(asset_id, VerdictProduct.MEANING)
    assert verdict is not None and verdict.code == "no_frame_decoded" and not verdict.transient
    assert await service.indexed_frames() == 0


async def test_a_later_read_that_yields_nothing_keeps_what_the_earlier_one_held(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """The second half of the property above, on the file it is actually about.

    Its own asset, because `describe_asset` skips a file described since it was last read, so a
    second call on the asset above would be the skip rather than the empty read. So the description
    is backdated to before the file's own read, which is what a file REREAD after an edit looks
    like, and the empty read that follows must leave what is on record alone.
    """
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    asset_id = await _an_asset(content_store, settings, tmp_path)
    reader.moments = [Moment(pixels=picture(), at_ms=0)]
    assert await service.describe_asset(asset_id) == 1
    await service._records.mark(asset_id, revision=REVISION, frames=1, at_ms=0)
    await content_store.record_probe(asset_id, keep_fingerprints=True)

    reader.moments = []
    assert await service.describe_asset(asset_id) == 0

    described = await service._records.described(asset_id)
    assert described is not None and described.frames == 1, "what the earlier read held stays"
    assert await service.indexed_frames() == 1


async def test_a_file_described_since_it_was_last_read_is_not_described_again(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """Thirty moments out of a file and a model pass over each, for an answer already stored.

    Two probes of one file hand out two of these, and a retry after a failure later in the method
    starts again from the top, so the redundant ask is ordinary. The count that comes back is
    what the file has, so a caller logging "described N" is not made to say nought.
    """
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    asset_id = await _an_asset(content_store, settings, tmp_path)
    reader.moments = [Moment(pixels=picture(), at_ms=0), Moment(pixels=picture(), at_ms=2000)]
    assert await service.describe_asset(asset_id) == 2
    reader.asked.clear()

    assert await service.describe_asset(asset_id) == 2
    assert reader.asked == [], "the file was read again for an answer already on record"


async def test_a_file_read_again_since_it_was_described_is_described_again(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """The condition that is easy to leave out, and the one that makes the guard safe.

    An edit that replaces a clip's audio or compresses it rewrites the bytes under the same asset
    id, and nothing anywhere forgets the description, so what refreshes the index afterwards is
    the re-probe's fan-out reaching `describe_asset`. A guard on the model revision alone would
    freeze every edited file's description for ever, quietly.
    """
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    asset_id = await _an_asset(content_store, settings, tmp_path)
    reader.moments = [Moment(pixels=picture(), at_ms=0)]
    assert await service.describe_asset(asset_id) == 1
    # What a re-probe leaves behind: the file has been read since it was described.
    await service._records.mark(asset_id, revision=REVISION, frames=1, at_ms=0)
    await content_store.record_probe(asset_id, keep_fingerprints=True)
    reader.asked.clear()

    assert await service.describe_asset(asset_id) == 1
    assert reader.asked, "an edited file kept its old description for ever"


async def test_describing_a_file_does_nothing_when_it_cannot_run(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    service, _preferences, _embedder, reader = wired
    reader.moments = [Moment(pixels=picture(), at_ms=0)]
    asset_id = await _an_asset(content_store, settings, tmp_path)

    assert await service.describe_asset(asset_id) == 0
    assert reader.asked == []


async def test_a_file_with_no_readable_copy_is_a_transient_verdict(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """A drive unplugged or a share away is written on the file and left for the next scan to
    clear, rather than failed three times over."""
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    asset_id = await _an_asset(content_store, settings, tmp_path)
    (tmp_path / "library" / "still.jpg").unlink()

    assert await service.describe_asset(asset_id) == 0

    assert reader.asked == []
    verdict = await content_store.verdict_of(asset_id, VerdictProduct.MEANING)
    assert verdict is not None and verdict.code == "no_copy" and verdict.transient


async def test_what_is_waiting_is_nothing_while_the_feature_cannot_run(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """Off, or on without its models, nothing is going to be described, so nothing is waiting
    and there is no term for the Build to count. Ready, the undescribed files are the answer."""
    service, preferences, embedder, _reader = wired
    asset_id = await _an_asset(content_store, settings, tmp_path)

    assert await service.waiting_among([asset_id]) == set()
    assert await service.lack() is None

    preferences.values[semantic_settings.ENABLED_KEY] = True
    embedder._installed = False
    assert await service.waiting_among([asset_id]) == set()
    assert await service.lack() is None

    embedder._installed = True
    assert await service.waiting_among([asset_id]) == {asset_id}
    assert await service.lack() is not None


async def test_the_counts_are_of_the_configured_model(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    reader.moments = [Moment(pixels=picture(), at_ms=0)]
    asset_id = await _an_asset(content_store, settings, tmp_path)
    await service.describe_asset(asset_id)

    assert await service.described_count() == 1


async def test_the_term_the_build_counts_by_is_the_configured_models_and_nothing_while_off(
    wired: Any,
) -> None:
    service, preferences, embedder, _reader = wired
    assert await service.lack() is None, "off means nothing is lacking, on the Build's sheet too"

    preferences.values[semantic_settings.ENABLED_KEY] = True
    lack = await service.lack()
    assert lack is not None
    assert lack.params == (embedder.revision,)


async def test_removing_the_index_removes_both_halves(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    reader.moments = [Moment(pixels=picture(), at_ms=0)]
    asset_id = await _an_asset(content_store, settings, tmp_path)
    await service.describe_asset(asset_id)

    await service.clear_index()

    assert await service.indexed_frames() == 0
    assert await service.described_count() == 0


async def test_removing_the_index_goes_a_batch_per_write_to_the_end(
    wired: Any,
    content_store: ContentStore,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One row a write, so every table is walked to empty rather than stopping after a batch."""
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    reader.moments = [Moment(pixels=picture(), at_ms=at) for at in (0, 1000, 2000)]
    await service.describe_asset(await _an_asset(content_store, settings, tmp_path))
    assert await service.indexed_frames() > 1
    monkeypatch.setattr(semantic_store, "CLEAR_BATCH", 1)
    monkeypatch.setattr(semantic_records, "_FORGET_BATCH", 1)

    await service.clear_index()

    assert await service.indexed_frames() == 0
    assert await service.described_count() == 0


async def test_switching_off_gives_the_memory_back(wired: Any) -> None:
    service, _preferences, embedder, _reader = wired
    service._embedder = embedder

    service.release()

    assert embedder.unloaded is True
    assert service._embedder is None


async def test_releasing_when_nothing_is_loaded_is_not_an_error(wired: Any) -> None:
    service, _preferences, _embedder, _reader = wired

    service.release()

    assert service._embedder is None


async def test_the_nearest_files_come_back_through_the_service(wired: Any) -> None:
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await service.describe_frames("asset-1", [(0, picture())])

    found = await service.nearest([1.0] + [0.0] * (DIMENSION - 1), limit=5)

    assert [neighbour.asset_id for neighbour in found] == ["asset-1"]


# --- obtaining the models ----------------------------------------------------------------------


async def test_a_model_nobody_has_heard_of_is_refused(wired: Any, tmp_path: Path) -> None:
    from sift.kernel.ml.weights import WeightError

    service, _preferences, _embedder, _reader = wired

    with pytest.raises(WeightError, match="no model called"):
        await service.install_from_file("imaginary", tmp_path / "nothing")


async def test_files_already_here_are_not_fetched_again(wired: Any, monkeypatch: Any) -> None:
    """The two sets share a vocabulary, so switching between them is one file's difference and not
    a second gigabyte."""

    service, _preferences, _embedder, _reader = wired
    asked: list[str] = []

    def already_here(self: Any, weight: Any) -> bool:
        return bool(weight.id == "vocabulary")

    async def record(self: Any, weight: Any, **_: Any) -> None:
        asked.append(weight.id)

    from sift.kernel.ml.weights import WeightStore

    monkeypatch.setattr(WeightStore, "installed", already_here, raising=True)
    monkeypatch.setattr(WeightStore, "fetch", record, raising=True)

    installed = await service.install_models()

    assert "vocabulary" not in asked
    assert installed == ["compact.pictures", "compact.words"]


async def _an_asset(store: ContentStore, settings: Settings, tmp_path: Path) -> str:
    """One real asset, so that resolving a readable copy of it is the real code path."""
    from sift.kernel.ingress import Origin, verify_ingress

    root = tmp_path / "library"
    root.mkdir(exist_ok=True)
    target = root / "still.jpg"
    corpus = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"
    target.write_bytes((corpus / "accepted.jpg").read_bytes())

    from sift.kernel.content import LibraryStore

    library = LibraryStore(store._db, settings)
    created = await library.create_root(name="Clips", abs_path=root)
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    ingested = await store.ingest(checked, root_id=created.id, rel_path="still.jpg")
    return str(ingested.asset.id)


async def test_changing_the_models_rebuilds_what_is_loaded(
    temp_db: Database, content_store: ContentStore, settings: Settings
) -> None:
    """The set and the device are baked into a prepared session. Quietly keeping the old one is
    how a change takes effect for everything except the thing already running."""
    await temp_db.initialize_schema()
    preferences = Preferences()
    service = SemanticService(
        store=VectorStore(temp_db),
        records=Records(temp_db),
        content=content_store,
        repository=Repository(temp_db, content_store),
        preferences=preferences,
        settings=settings,
        hardware=machine(),
    )

    first = await service.embedder()
    preferences.values[semantic_settings.MODEL_KEY] = "full"
    second = await service.embedder()

    assert first is not second
    assert second.family == "full"


async def test_asking_twice_with_nothing_changed_hands_back_the_same_models(
    temp_db: Database, content_store: ContentStore, settings: Settings
) -> None:
    """Preparing a session costs a hundred times what running it does."""
    await temp_db.initialize_schema()
    service = SemanticService(
        store=VectorStore(temp_db),
        records=Records(temp_db),
        content=content_store,
        repository=Repository(temp_db, content_store),
        preferences=Preferences(),
        settings=settings,
        hardware=machine(),
    )

    assert await service.embedder() is await service.embedder()


async def test_a_model_can_be_installed_from_a_file_the_operator_already_has(
    wired: Any, tmp_path: Path
) -> None:
    """The offline answer, and the only one on a machine with no route out."""
    from sift.kernel.ml.weights import WeightError
    from sift.slices.semantic import weights as semantic_weights

    service, _preferences, _embedder, _reader = wired
    source = tmp_path / "vocabulary.model"
    source.write_bytes(b"not the real vocabulary")

    with pytest.raises(WeightError, match=r"not the .* Sift expects"):
        await service.install_from_file("vocabulary", source)

    assert not semantic_weights.store(service.settings).installed(
        semantic_weights.CATALOG["vocabulary"]
    )


# --- the work list, through the access layer -----------------------------------------------------


async def test_what_is_left_is_what_is_LEFT_and_not_the_size_of_the_library(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """The number a person reads as "still to do" on the settings screen.

    Not the size of the page walk, which is how many files there are ALTOGETHER: a library that had
    just been fully described would read "508 described, 505 still to do": a screen reporting
    that nothing had happened at the exact moment everything had, with no way to tell that from a
    sweep that had genuinely stalled.
    """
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    asset_id = await _an_asset(content_store, settings, tmp_path)
    # Read first: what is left to describe is counted over files Sift has read, the same rule
    # the Build's sheet counts by. A file nobody has read is the read's to offer, not this one's.
    await content_store.record_probe(asset_id, width=16, height=16, duration_ms=None)
    viewer = await service.viewer_for(await _an_admin(service, tmp_path))

    assert await service.waiting_count(viewer) == 1

    reader.moments = [Moment(pixels=picture(), at_ms=0)]
    await service.describe_asset(asset_id)

    assert await service.waiting_count(viewer) == 0


async def test_how_far_it_has_got_counts_both_numbers_over_the_same_files(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """The fraction drawn under a set of results found by meaning.

    A search by meaning can only answer out of what has been described, and on a library the
    background pass has not been round yet that is a small part of it, so a thin answer to a good
    phrase reads as the files not being there. The sentence needs a denominator, and the
    denominator has to be the SAME population as the numerator or the fraction is nonsense.

    So: the files this user can see that Sift has READ. A file nobody has read yet is in
    neither, which is the first assertion: counting it in the denominator alone would make an
    import look like a description that had failed.
    """
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    asset_id = await _an_asset(content_store, settings, tmp_path)
    viewer = await service.viewer_for(await _an_admin(service, tmp_path))

    assert await service.coverage(viewer) == Coverage(described=0, library=0)

    await content_store.record_probe(asset_id, width=16, height=16, duration_ms=None)

    assert await service.coverage(viewer) == Coverage(described=0, library=1)

    reader.moments = [Moment(pixels=picture(), at_ms=0)]
    await service.describe_asset(asset_id)

    assert await service.coverage(viewer) == Coverage(described=1, library=1)


async def test_an_account_that_has_gone_resolves_to_nobody(wired: Any) -> None:
    """A job outlives the request that started it, so the user it names may have been deleted."""
    service, _preferences, _embedder, _reader = wired

    assert await service.viewer_for("01HX0000000000000000000099") is None


async def _an_admin(service: SemanticService, tmp_path: Path) -> str:
    """One real admin user, so resolving a viewer is the real code path."""
    from sift.kernel.ids import new_id

    user_id = new_id()
    await service._repository._db.execute(
        "INSERT INTO users (id, username, password_hash, role, mk_wrapped, mk_nonce, "
        "mk_kdf_salt, created_at, disabled) VALUES (?, 'sweeper', 'x', 'admin', x'00', "
        "x'00', x'00', 1700000000, 0)",
        (user_id,),
    )
    return user_id


# --- what looks like this, by whichever tier can answer ----------------------------------------


class Fingerprints:
    """The cheap tier, standing in for the content layer's fingerprint read."""

    def __init__(self, *rows: tuple[str, str], scoped_to: tuple[str, ...] = ()) -> None:
        self._rows = rows
        # What a read within a viewer's files answers: the statement scopes in the real layer.
        self._scoped_to = scoped_to

    async def fingerprints(self, *, within: object = None) -> list[Any]:
        from dataclasses import make_dataclass

        Row = make_dataclass(
            "Row", ["asset_id", "identity", "media_type", "phash", "videohash"], frozen=True
        )
        rows = self._rows if within is None else [r for r in self._rows if r[0] in self._scoped_to]
        return [Row(asset_id, "digest", "video", phash, None) for asset_id, phash in rows]


async def test_a_described_file_gets_the_better_tier(wired: Any, temp_db: Database) -> None:
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await described(temp_db, service, "mine")
    await described(temp_db, service, "other")

    found = await service.similar_to("mine")

    assert found.tier.value == "looks"
    assert [asset_id for asset_id, _ in found.neighbours] == ["other"]


async def test_the_better_tier_ranks_only_what_the_asker_may_see(
    wired: Any, temp_db: Database
) -> None:
    from sift.testing.fixtures import create_user

    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await described(temp_db, service, "mine")
    await described(temp_db, service, "other")
    guest = await create_user(temp_db, Role.GUEST)

    assert (await service.similar_to("mine", asker=guest)).neighbours == ()
    assert await service.nearest([1.0] + [0.0] * (DIMENSION - 1), limit=5, asker=guest) == []


async def test_a_file_the_pass_has_not_reached_gets_the_cheap_tier(
    temp_db: Database, content_store: ContentStore, settings: Settings
) -> None:
    """The ordinary state on a library part-way through, and an empty screen would be wrong."""
    from sift.slices.semantic.similar import SimilarFinder

    await temp_db.initialize_schema()
    service = SemanticService(
        store=VectorStore(temp_db),
        records=Records(temp_db),
        content=content_store,
        repository=Repository(temp_db, content_store),
        preferences=Preferences(),
        settings=settings,
        hardware=machine(),
        similar=SimilarFinder(
            Fingerprints(("mine", "0000000000000000"), ("other", "0000000000000001")),  # type: ignore[arg-type]
            None,
        ),
    )

    found = await service.similar_to("mine")

    assert found.tier.value == "matches"
    assert [asset_id for asset_id, _ in found.neighbours] == ["other"]


async def test_the_cheap_tier_ranks_only_what_the_asker_may_see(
    temp_db: Database, content_store: ContentStore, settings: Settings
) -> None:
    from sift.slices.semantic.similar import SimilarFinder
    from sift.testing.fixtures import create_user

    await temp_db.initialize_schema()
    # The guest's own files hold only "mine": the scoped read answers nothing else.
    rows: Any = Fingerprints(
        ("mine", "0000000000000000"), ("other", "0000000000000001"), scoped_to=("mine",)
    )
    service = SemanticService(
        store=VectorStore(temp_db),
        records=Records(temp_db),
        content=content_store,
        repository=Repository(temp_db, content_store),
        preferences=Preferences(),
        settings=settings,
        hardware=machine(),
        similar=SimilarFinder(rows, temp_db),
    )
    guest = await create_user(temp_db, Role.GUEST)
    admin = await create_user(temp_db, Role.ADMIN)

    assert (await service.similar_to("mine", asker=guest)).neighbours == ()
    assert [one for one, _ in (await service.similar_to("mine", asker=admin)).neighbours] == [
        "other"
    ]


async def test_a_file_only_the_previous_model_described_gets_the_cheap_tier_and_the_screen_says_so(
    wired: Any, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a model change a file's numbers are the previous model's: not comparable with the
    question, so not an answer. It falls to the tier every file can answer by, and the readiness
    the screen draws says how many files are in that state."""
    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    for asset_id in ("mine", "other"):
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
            "VALUES (?, ?, 'image', 1, 1700000000)",
            (asset_id, f"digest-{asset_id}"),
        )
        await service.describe_frames(asset_id, [(0, picture())])
        await Records(temp_db).mark(asset_id, revision=REVISION, frames=1, at_ms=1)
    embedder.revision = "siglip2-newer"

    found = await service.similar_to("mine")
    counted = Records.described_by_others

    async def never(*_: object) -> int:
        raise AssertionError("readiness counted every file a previous model described")

    # Asked by every search phrase, so it asks whether any, never how many.
    monkeypatch.setattr(Records, "described_by_others", never)
    readiness = await service.readiness()
    monkeypatch.setattr(Records, "described_by_others", counted)

    assert found.tier.value == "matches"
    assert readiness.by_another_model is True
    assert await service.described_by_others() == 2
    assert await service.nearest([1.0] + [0.0] * (DIMENSION - 1), limit=5) == []


async def test_with_the_switch_off_a_described_file_gets_the_cheap_tier(
    temp_db: Database, content_store: ContentStore, settings: Settings
) -> None:
    """Smart Search off means the description model answers nothing, the index included.

    Both files are described while the switch is on, so the better tier COULD answer; with it
    off, "Similar to this" answers by the fingerprints every file carries, exactly as a file the
    pass has not reached does.
    """
    from sift.slices.semantic.similar import SimilarFinder

    await temp_db.initialize_schema()
    preferences = Preferences()
    service = SemanticService(
        store=VectorStore(temp_db),
        records=Records(temp_db),
        content=content_store,
        repository=Repository(temp_db, content_store),
        preferences=preferences,
        settings=settings,
        hardware=machine(),
        similar=SimilarFinder(
            Fingerprints(("mine", "0000000000000000"), ("other", "0000000000000001")),  # type: ignore[arg-type]
            None,
        ),
    )
    embedder = StubEmbedder()

    async def use_the_stub() -> StubEmbedder:
        return embedder

    service.embedder = use_the_stub  # type: ignore[assignment,method-assign]
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await described(temp_db, service, "mine")
    await described(temp_db, service, "other")
    assert (await service.similar_to("mine")).tier.value == "looks"

    preferences.values[semantic_settings.ENABLED_KEY] = False
    found = await service.similar_to("mine")

    assert found.tier.value == "matches"
    assert [asset_id for asset_id, _ in found.neighbours] == ["other"]


async def test_with_the_switch_off_the_index_answers_nothing_to_any_reader(
    wired: Any, temp_db: Database
) -> None:
    """Every read of the index passes the one check, so no reader can go on answering by the
    description model after somebody turned Smart Search off."""
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await described(temp_db, service, "clip")
    preferences.values[semantic_settings.ENABLED_KEY] = False

    assert await service.describes("clip") == []
    assert await service.describes_many(["clip"]) == {}
    assert await service.describes_anything() is False
    assert await service.nearest([1.0] + [0.0] * (DIMENSION - 1), limit=5) == []


async def test_an_install_with_neither_tier_finds_nothing_rather_than_failing(
    wired: Any,
) -> None:
    service, _preferences, _embedder, _reader = wired

    found = await service.similar_to("mine")

    assert found.neighbours == ()


# --- describing a typed query ------------------------------------------------------------------


async def test_an_empty_query_is_not_worth_a_forward_pass(wired: Any) -> None:
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True

    assert await service.describe_query("   ") is None


async def test_a_query_on_an_install_that_cannot_answer_comes_back_as_nothing(
    wired: Any,
) -> None:
    """None rather than an exception: a search box asking for something the install cannot do is an
    ordinary state, and the caller falls back to the ordinary order."""
    service, _preferences, _embedder, _reader = wired

    assert await service.describe_query("a dog on a beach") is None


async def test_a_query_is_described_when_everything_is_in_place(wired: Any) -> None:
    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True

    async def describe_words(text: str) -> list[float]:
        return [1.0] * DIMENSION

    embedder.describe_words = describe_words

    described = await service.describe_query("a dog on a beach")

    assert described is not None
    assert len(described) == DIMENSION


async def test_what_a_file_looks_like_comes_through_the_service(
    wired: Any, temp_db: Database
) -> None:
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await described(temp_db, service, "clip")

    assert len(await service.describes("clip")) == DIMENSION


async def test_a_file_the_work_list_does_not_know_is_not_read_out_of_the_index(
    wired: Any, temp_db: Database
) -> None:
    """The read that saves the pass its scans, and it is a correctness rule before it is a cost.

    Frames sit in a virtual table that takes no foreign key, so a deleted file leaves its numbers
    behind for ever, and reading them by file id alone answers with a description of something
    that is no longer in the library. Asking the work list first refuses that, and costs a
    primary-key lookup where reading the frames costs a scan of the whole revision (over a hundred
    milliseconds on a large library, the same whether the file has frames or none).
    """
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await described(temp_db, service, "clip")
    await Records(temp_db).forget("clip")

    assert await service.describes("clip") == []


async def test_a_file_the_previous_model_described_is_not_read_out_of_the_index(
    wired: Any, temp_db: Database
) -> None:
    """Another model's numbers are the same length as this one's and mean nothing to it."""
    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await described(temp_db, service, "clip")
    embedder.revision = "siglip2-newer"

    assert await service.describes("clip") == []


async def test_many_files_are_described_by_the_model_in_use_and_only_it(
    wired: Any, temp_db: Database
) -> None:
    """Keyed by file, read at the model in use's revision: a file only another model described is
    absent, because its numbers compared with this model's would be noise."""
    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    await described(temp_db, service, "clip")

    found = await service.describes_many(["clip", "never"])
    assert list(found) == ["clip"]
    assert len(found["clip"]) == DIMENSION

    embedder.revision = "siglip2-newer"
    assert await service.describes_many(["clip"]) == {}


async def test_an_install_says_whether_it_has_described_anything(
    wired: Any, temp_db: Database
) -> None:
    """One read for a pass with thousands of files to ask about. See `SemanticSeam.can_answer`."""
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True

    assert await service.describes_anything() is False
    await described(temp_db, service, "clip")
    assert await service.describes_anything() is True


async def test_a_machine_that_cannot_hold_the_index_still_gets_the_cheap_tier(
    temp_db: Database, content_store: ContentStore, settings: Settings
) -> None:
    """The whole point of having two. An install whose SQLite cannot load the add-on can never use
    the better one, and "what looks like this" still has an answer there."""
    from sift.slices.semantic.similar import SimilarFinder

    await temp_db.initialize_schema()
    temp_db._extensions = frozenset()
    service = SemanticService(
        store=VectorStore(temp_db),
        records=Records(temp_db),
        content=content_store,
        repository=Repository(temp_db, content_store),
        preferences=Preferences(),
        settings=settings,
        hardware=machine(),
        similar=SimilarFinder(
            Fingerprints(("mine", "0000000000000000"), ("other", "0000000000000001")),  # type: ignore[arg-type]
            None,
        ),
    )

    found = await service.similar_to("mine")

    assert found.tier.value == "matches"
    assert [asset_id for asset_id, _ in found.neighbours] == ["other"]


# --- the descriptions of files that have gone --------------------------------------------------


async def test_the_sweep_drops_the_vectors_of_files_that_have_left_the_library(
    wired: Any, content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """The one thing in Sift a delete does not cascade away.

    Every other table naming an asset carries a foreign key back to it. The vectors cannot: they
    live in a virtual table and SQLite takes no foreign key on one, so a deleted file leaves its
    descriptions behind, invisible and growing. Swept rather than hooked onto the delete, because
    the feature that deletes files may not import this one.
    """
    service, preferences, _embedder, reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    asset_id = await _an_asset(content_store, settings, tmp_path)
    reader.moments = [Moment(pixels=picture(), at_ms=0)]
    await service.describe_asset(asset_id)

    assert await service.prune_index() == 0  # still here, so nothing to drop

    await content_store._db.execute("DELETE FROM assets WHERE id = ?", (asset_id,))

    assert await service.prune_index() == 1
    assert await service.nearest([1.0] + [0.0] * (DIMENSION - 1), limit=5) == []
    # And running it again finds nothing left to do rather than failing on an empty list.
    assert await service.prune_index() == 0


async def test_an_index_that_holds_nothing_is_pruned_without_asking_the_library(
    wired: Any,
) -> None:
    """The first thing every sweep does, on installs that have never described a file. Reaching
    past the empty answer would build the index merely to find it empty."""
    service, _preferences, _embedder, _reader = wired

    assert await service.prune_index() == 0


# --- fetching the models again -----------------------------------------------------------------


async def test_asking_again_fetches_files_that_are_already_here(
    wired: Any, monkeypatch: Any
) -> None:
    """A file is called installed if it EXISTS, and whether it is the RIGHT file is a separate
    question answered by reading the whole of it. So a truncated or replaced model is installed,
    refuses to load, and says "delete it and fetch it again": an instruction that could only be
    followed at a shell. This is that instruction as a button, and without it the repair would
    do nothing at all: every file is present, every one is skipped, and the job finishes instantly
    reporting success."""
    service, _preferences, _embedder, _reader = wired
    asked: list[str] = []
    afresh: list[bool] = []

    def already_here(self: Any, weight: Any) -> bool:
        return True

    async def record(self: Any, weight: Any, **kwargs: Any) -> None:
        asked.append(weight.id)
        afresh.append(kwargs["fresh"])

    from sift.kernel.ml.weights import WeightStore

    monkeypatch.setattr(WeightStore, "installed", already_here, raising=True)
    monkeypatch.setattr(WeightStore, "fetch", record, raising=True)

    assert await service.install_models() == []
    assert asked == []

    installed = await service.install_models(force=True)

    assert asked == installed != []
    assert set(afresh) == {True}, "a partial left from before is not resumed"


async def test_each_transfer_says_which_of_the_set_it_is_before_it_starts(
    wired: Any, monkeypatch: Any
) -> None:
    """A working set is three files of wildly different sizes, so a single fraction runs to one and
    back to zero three times, which without this reads as a download that keeps failing and
    starting again. Announced BEFORE the transfer rather than after, so a screen can name the file
    before the first byte rather than after the last."""
    service, _preferences, _embedder, _reader = wired
    announced: list[tuple[int, int, str]] = []
    fetched: list[str] = []

    def nothing_here(self: Any, weight: Any) -> bool:
        return False

    async def record(self: Any, weight: Any, **_: Any) -> None:
        # Every announcement must already have been made when its own transfer starts.
        fetched.append(weight.id)
        assert len(announced) == len(fetched)

    from sift.kernel.ml.weights import WeightStore

    monkeypatch.setattr(WeightStore, "installed", nothing_here, raising=True)
    monkeypatch.setattr(WeightStore, "fetch", record, raising=True)

    installed = await service.install_models(
        on_file=lambda index, of, role: announced.append((index, of, role))
    )

    assert [index for index, _of, _role in announced] == list(range(len(installed)))
    assert {of for _index, of, _role in announced} == {len(installed)}
    assert all(role for _index, _of, role in announced)


async def test_a_runtime_that_cannot_start_is_said_in_words_rather_than_raised(
    wired: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel.ml import child as ml_child
    from sift.kernel.ml.runtime import DeviceUnavailable

    def crashed(_settings: Settings) -> tuple[str, ...]:
        raise DeviceUnavailable("it stopped with code 0xC0000005")

    monkeypatch.setattr(ml_child, "DEVICES", ml_child.DeviceQuestion(crashed))
    service, preferences, _embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True

    readiness = await service.readiness()

    assert readiness.ready is False
    assert readiness.problem == (
        "Search by meaning can't run on this device: the model runtime couldn't start "
        "(it stopped with code 0xC0000005). Restart Sift to try again."
    )


async def test_a_vocabulary_that_cannot_load_is_said_and_the_search_takes_the_ordinary_order(
    wired: Any,
) -> None:
    from sift.kernel.ml.runtime import DeviceUnavailable

    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True
    refused = (
        "Search by meaning can't run on this device: the model runtime couldn't start "
        "(it stopped with code 0xC0000005). Restart Sift to try again."
    )

    async def describe_words(text: str) -> list[float]:
        embedder.words_refused = refused
        raise DeviceUnavailable(refused)

    embedder.describe_words = describe_words

    assert await service.describe_query("a red car") is None
    readiness = await service.readiness()
    assert readiness.ready is False
    assert readiness.problem == refused


async def test_a_lost_card_while_reading_words_is_raised_as_before(wired: Any) -> None:
    from sift.kernel.ml.runtime import DeviceLost

    service, preferences, embedder, _reader = wired
    preferences.values[semantic_settings.ENABLED_KEY] = True

    async def describe_words(text: str) -> list[float]:
        raise DeviceLost("an NVIDIA graphics card stopped answering")

    embedder.describe_words = describe_words

    with pytest.raises(DeviceLost):
        await service.describe_query("a red car")
