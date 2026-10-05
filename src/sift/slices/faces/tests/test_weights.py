# SPDX-License-Identifier: AGPL-3.0-or-later
"""Obtaining the models, and proving what arrived is what was described.

**No model file ships with Sift**, so all of this is about getting one safely: it never happens on
its own, it resumes, it can be stopped, a local file is always an alternative, and nothing is used
until its digest matches. That last one is not belt and braces: a truncated model file usually
loads perfectly well and then returns numbers that are quietly wrong.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.slices.faces import weights
from sift.slices.faces.weights import CATALOG, Weight, WeightError

pytestmark = pytest.mark.unit


def make_weight(tmp_path: Path, payload: bytes, member: str | None = None) -> Weight:
    from sift.slices.faces.crop import digest as digest_of

    return Weight(
        id="test.detector",
        role="detector",
        family="test",
        revision="test-1",
        url="https://example.test/model.onnx",
        digest=digest_of(payload),
        size_bytes=len(payload),
        archive_member=member,
        licence="MIT",
    )


# --- the catalog -------------------------------------------------------------------------------------


def test_every_model_in_the_stash_box_names_a_licence_and_a_place_to_get_it() -> None:
    """Adding one means reading that file's actual licence (vendor documentation is not a
    licence) and recording it in the repo."""
    for weight in CATALOG.values():
        assert weight.licence
        assert weight.url.startswith("https://")
        assert len(weight.digest) == 64
        assert weight.size_bytes > 0


def test_a_detector_and_a_recognizer_come_as_a_pair() -> None:
    """They have to agree about where a face's features sit. Mixing two families measurably
    degrades matching without anything erroring."""
    for family in ("accurate", "permissive"):
        detector, recognizer = weights.pairing(family)
        assert detector.role == "detector"
        assert recognizer.role == "recognizer"
        assert detector.family == recognizer.family == family
        assert recognizer.dimension > 0


def test_the_download_is_counted_in_the_bytes_that_come_down() -> None:
    """Not the 177 MB of models: the archives that come down for the default family are 416 MB,
    and a bar drawn against the models would sit at 100% for the last 165 MB."""
    from sift.kernel.settings_registry import get_registered
    from sift.slices.faces.settings import ENABLED_KEY

    accurate = weights.pairing("accurate")

    assert weights.download_bytes(accurate) == 127607557 + 288621354
    assert weights.download_bytes(accurate) > sum(weight.size_bytes for weight in accurate)
    declared = get_registered(ENABLED_KEY)
    assert declared is not None and declared.disclosure is not None
    assert "416 MB" in declared.disclosure


def test_a_file_fetched_as_it_is_counts_as_itself() -> None:
    permissive = weights.pairing("permissive")
    assert weights.download_bytes(permissive) == sum(weight.size_bytes for weight in permissive)


def test_the_recognizers_copy_under_its_old_spelling_goes_once_the_current_one_verifies(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename that moves the columns leaves the file: 174 MB nothing reads. Removed only once
    the copy under the current name proves intact, because until then it is the one thing that
    could put the library back."""
    directory = weights.store(settings).directory()
    directory.mkdir(parents=True)
    stale = directory / "accurate.recogniser.onnx"
    stale.write_bytes(b"the old copy")

    def refuses(_settings: Settings, _weight: Weight) -> None:
        raise WeightError("not there")

    monkeypatch.setattr(weights, "verify", refuses)
    assert weights.retire_misspelt(settings) is False
    assert stale.exists(), "kept while the current copy does not verify"

    monkeypatch.setattr(weights, "verify", lambda _settings, _weight: None)
    assert weights.retire_misspelt(settings) is True
    assert not stale.exists()
    assert weights.retire_misspelt(settings) is False, "nothing left to retire"


def test_a_set_of_models_that_does_not_exist_is_refused() -> None:
    with pytest.raises(WeightError, match="no set of models"):
        weights.pairing("imaginary")


def test_models_live_where_a_container_being_recreated_will_not_lose_them(
    settings: Settings,
) -> None:
    """In the device's one model store, which every library on the machine shares, and never under
    the cache, which a clean empties. `models_folder` says where that store is and why."""
    assert weights.directory(settings) == settings.models_dir / weights.NAMESPACE
    assert not weights.directory(settings).is_relative_to(settings.cache_dir)


# --- installing from a file --------------------------------------------------------------------------


async def test_a_model_can_be_installed_from_a_file_the_operator_already_has(
    tmp_path: Path, settings: Settings
) -> None:
    """The offline answer, and the only one on a machine with no route out."""
    payload = b"a model, of sorts" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    weight = make_weight(tmp_path, payload)

    assert weights.installed(settings, weight) is False
    await weights.install_from_file(settings, weight, source)

    assert weights.installed(settings, weight) is True
    weights.verify(settings, weight)


async def test_a_file_that_is_not_the_model_described_is_refused(
    tmp_path: Path, settings: Settings
) -> None:
    payload = b"the real model" * 100
    weight = make_weight(tmp_path, payload)
    wrong = tmp_path / "wrong.onnx"
    wrong.write_bytes(b"something else entirely")

    with pytest.raises(WeightError, match=r"not the .* model"):
        await weights.install_from_file(settings, weight, wrong)

    assert weights.installed(settings, weight) is False


async def test_a_file_that_is_not_there_is_reported(tmp_path: Path, settings: Settings) -> None:
    weight = make_weight(tmp_path, b"payload")

    with pytest.raises(WeightError, match="no file at"):
        await weights.install_from_file(settings, weight, tmp_path / "absent.onnx")


async def test_a_model_distributed_inside_an_archive_is_taken_out_of_it(
    tmp_path: Path, settings: Settings
) -> None:
    payload = b"the model inside" * 50
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("wanted.onnx", payload)
        bundle.writestr("not-wanted.onnx", b"a much larger thing nobody asked for")
    weight = make_weight(tmp_path, payload, member="wanted.onnx")

    await weights.install_from_file(settings, weight, archive)

    assert weights.path_of(settings, weight).read_bytes() == payload


async def test_an_archive_without_the_named_model_in_it_is_refused(
    tmp_path: Path, settings: Settings
) -> None:
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("something-else.onnx", b"nope")
    weight = make_weight(tmp_path, b"the model inside", member="wanted.onnx")

    with pytest.raises(WeightError, match="does not contain"):
        await weights.install_from_file(settings, weight, archive)


# --- proving what is on disk -------------------------------------------------------------------------


def test_a_model_that_has_not_been_installed_is_reported_as_such(
    tmp_path: Path, settings: Settings
) -> None:
    with pytest.raises(WeightError, match="not been installed"):
        weights.verify(settings, make_weight(tmp_path, b"payload"))


async def test_a_model_that_changed_on_disk_is_refused_rather_than_loaded(
    tmp_path: Path, settings: Settings
) -> None:
    """A truncated model usually loads and then returns numbers that are quietly wrong, so this is
    checked before every load rather than only when it was installed."""
    payload = b"a model, of sorts" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    weight = make_weight(tmp_path, payload)
    await weights.install_from_file(settings, weight, source)

    installed = weights.path_of(settings, weight)
    installed.write_bytes(payload[:-20])

    with pytest.raises(WeightError, match="not the one Sift expects"):
        weights.verify(settings, weight)


def test_the_digest_of_a_file_is_read_in_pieces(tmp_path: Path) -> None:
    """A model is hundreds of megabytes; it does not have to fit in memory to be checked."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (3 << 20))

    from sift.slices.faces.crop import digest as digest_of

    assert weights.digest_of(big) == digest_of(b"x" * (3 << 20))


# --- downloading ---------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: bytes, *, total: int | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = {"Content-Length": str(total if total is not None else len(body))}
        self.content = self

    async def iter_chunked(self, size: int):  # type: ignore[no-untyped-def]
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    """Answers one request from memory, and records what was asked for."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {}

    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.headers = dict(headers or {})
        return self.response

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_a_model_is_downloaded_verified_and_put_in_place(
    tmp_path: Path, settings: Settings
) -> None:
    payload = b"downloaded model" * 200
    weight = make_weight(tmp_path, payload)
    session = FakeSession(FakeResponse(200, payload))

    await weights.fetch(settings, weight, session_factory=lambda: session)

    assert weights.path_of(settings, weight).read_bytes() == payload
    weights.verify(settings, weight)


async def test_an_interrupted_download_continues_from_where_it_stopped(
    tmp_path: Path, settings: Settings
) -> None:
    """Hundreds of megabytes: a connection dropping at ninety per cent must not mean starting
    again."""
    payload = b"downloaded model" * 200
    weight = make_weight(tmp_path, payload)
    partial = weights.path_of(settings, weight).with_suffix(".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(payload[:500])

    session = FakeSession(FakeResponse(206, payload[500:]))
    await weights.fetch(settings, weight, session_factory=lambda: session)

    assert session.headers["Range"] == "bytes=500-"
    assert weights.path_of(settings, weight).read_bytes() == payload


async def test_a_server_that_ignores_where_we_stopped_starts_the_file_again(
    tmp_path: Path, settings: Settings
) -> None:
    payload = b"downloaded model" * 200
    weight = make_weight(tmp_path, payload)
    partial = weights.path_of(settings, weight).with_suffix(".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"stale rubbish")

    session = FakeSession(FakeResponse(200, payload))
    await weights.fetch(settings, weight, session_factory=lambda: session)

    assert weights.path_of(settings, weight).read_bytes() == payload


async def test_a_download_can_be_stopped_and_leaves_something_to_continue_from(
    tmp_path: Path, settings: Settings
) -> None:
    """Stopping is not a failure. What arrived stays, and the next attempt asks for the rest."""
    payload = b"downloaded model" * 200_000
    weight = make_weight(tmp_path, payload)
    session = FakeSession(FakeResponse(200, payload))
    seen: list[tuple[int, int]] = []

    def stop_after_the_first_piece(written: int, total: int) -> bool:
        seen.append((written, total))
        return False

    await weights.fetch(
        settings, weight, session_factory=lambda: session, progress=stop_after_the_first_piece
    )

    assert seen == [(1 << 20, len(payload))]
    assert weights.installed(settings, weight) is False
    partial = weights.path_of(settings, weight).with_suffix(".part")
    assert partial.stat().st_size == 1 << 20


def _recorder(seen: list[tuple[int, int]]):  # type: ignore[no-untyped-def]
    def progress(written: int, total: int) -> bool:
        seen.append((written, total))
        return True

    return progress


async def test_progress_is_reported_while_a_download_runs(
    tmp_path: Path, settings: Settings
) -> None:
    payload = b"downloaded model" * 200_000
    weight = make_weight(tmp_path, payload)
    session = FakeSession(FakeResponse(200, payload))
    seen: list[tuple[int, int]] = []

    await weights.fetch(
        settings,
        weight,
        session_factory=lambda: session,
        progress=_recorder(seen),
    )

    assert len(seen) > 1
    assert seen[-1] == (len(payload), len(payload))
    assert weights.installed(settings, weight) is True


async def test_a_server_that_refuses_is_reported_in_words_somebody_can_act_on(
    tmp_path: Path, settings: Settings
) -> None:
    weight = make_weight(tmp_path, b"payload")
    session = FakeSession(FakeResponse(404, b""))

    with pytest.raises(WeightError, match="couldn't be downloaded"):
        await weights.fetch(settings, weight, session_factory=lambda: session)


async def test_downloading_again_starts_afresh_rather_than_from_what_arrived_before(
    tmp_path: Path, settings: Settings
) -> None:
    payload = b"downloaded model" * 200
    weight = make_weight(tmp_path, payload)
    partial = weights.path_of(settings, weight).with_suffix(".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"left from before")
    session = FakeSession(FakeResponse(200, payload))

    await weights.fetch(settings, weight, session_factory=lambda: session, fresh=True)

    assert "Range" not in session.headers
    assert weights.path_of(settings, weight).read_bytes() == payload


async def test_a_server_saying_there_is_nothing_more_completes_what_was_already_there(
    tmp_path: Path, settings: Settings
) -> None:
    payload = b"downloaded model" * 200
    weight = make_weight(tmp_path, payload)
    partial = weights.path_of(settings, weight).with_suffix(".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(payload)

    session = FakeSession(FakeResponse(416, b""))
    await weights.fetch(settings, weight, session_factory=lambda: session)

    assert weights.path_of(settings, weight).read_bytes() == payload
