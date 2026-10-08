# SPDX-License-Identifier: AGPL-3.0-or-later
"""The device's model store is where every reader looks, and never a library's own data folder.

The one-time move of a library's own copies into the store is retired (see `kernel.ml.store`);
what stays is the rule it served: the face, Smart Search and watermark models and the graphics-card
runtime are read from `Settings.models_dir`, once per device, whichever library asks.
"""

from __future__ import annotations

import ssl
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from blake3 import blake3

from sift.kernel import fetch
from sift.kernel.config import Settings
from sift.kernel.jobs import JobFailedPermanently
from sift.kernel.ml import accel, store
from sift.kernel.ml.weights import Weight, WeightError, WeightStore


def test_every_reader_reads_the_store(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    for feature in ("faces", "semantic", "watermarks"):
        where = WeightStore(settings, feature).directory()
        assert where == settings.models_dir / feature
        assert settings.data_dir / feature / store.LIBRARY_MODELS not in (where, *where.parents)
    runtime = accel.directory(settings)
    assert runtime.parent.parent == settings.models_dir
    assert runtime.parent.name == accel.FOLDER


# --- a partial download that is wrong, or unwanted -----------------------------------------------


class Answer:
    """One answer from memory: a status and its body."""

    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.headers = {"Content-Length": str(len(body))}
        self.content = self
        self._body = body

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        yield self._body

    async def __aenter__(self) -> Answer:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class Server:
    """Answers each request in turn, and keeps the range each one asked for."""

    def __init__(self, *answers: Answer | BaseException) -> None:
        self.answers = list(answers)
        self.ranges: list[str | None] = []

    def get(self, _url: str, headers: dict[str, str] | None = None) -> Answer:
        self.ranges.append((headers or {}).get("Range"))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def __aenter__(self) -> Server:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def _weight(payload: bytes, *, member: str | None = None) -> Weight:
    return Weight(
        id="probe.reader",
        role="reader",
        family="probe",
        revision="1",
        url="https://models.example.test/reader.onnx",
        digest=blake3(payload).hexdigest(),
        size_bytes=len(payload),
        archive_member=member,
        licence="MIT",
    )


@pytest.fixture
def probe(tmp_path: Path) -> WeightStore:
    return WeightStore(Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"), "probe")


async def test_a_partial_that_fails_its_check_is_removed_so_the_next_attempt_starts_clean(
    probe: WeightStore,
) -> None:
    payload = b"the real model" * 50
    weight = _weight(payload)
    partial = probe.path_of(weight).with_suffix(".part")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"something else entirely" * 50)
    server = Server(Answer(416), Answer(200, payload))

    with pytest.raises(WeightError, match="didn't arrive intact, so it was removed"):
        await probe.fetch(weight, session_factory=lambda: server)
    assert not partial.exists()

    await probe.fetch(weight, session_factory=lambda: server)
    assert server.ranges == ["bytes=1150-", None]
    assert probe.path_of(weight).read_bytes() == payload


async def test_starting_afresh_throws_away_what_arrived_before(probe: WeightStore) -> None:
    payload = b"the real model" * 50
    weight = _weight(payload)
    partial = probe.path_of(weight).with_suffix(".part")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(payload[:100])
    server = Server(Answer(200, payload))

    await probe.fetch(weight, session_factory=lambda: server, fresh=True)

    assert server.ranges == [None]
    assert probe.path_of(weight).read_bytes() == payload


async def test_a_damaged_archive_is_refused_and_leaves_nothing_behind(
    probe: WeightStore, tmp_path: Path
) -> None:
    member = b"the real model" * 50
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("reader.onnx", member)
    data = bytearray(archive.read_bytes())
    data[45] ^= 0xFF  # inside the member's compressed bytes, so reading it fails its check
    archive.write_bytes(bytes(data))
    weight = _weight(member, member="reader.onnx")

    with pytest.raises(WeightError, match="that archive is damaged"):
        await probe.install_from_file(weight, archive)

    assert sorted(path.name for path in probe.directory().iterdir()) == []


async def test_a_connection_that_fails_offers_the_file_by_hand(probe: WeightStore) -> None:
    weight = _weight(b"never arrives")
    refused = aiohttp.ClientConnectorError(
        ConnectionKey("models.example.test", 443, True, True, None, None, None),
        ConnectionRefusedError(61, "refused"),
    )

    with pytest.raises(WeightError) as failed:
        await probe.fetch(weight, session_factory=lambda: Server(refused))

    # The client's own words last, after everything a person reads.
    assert str(failed.value) == (
        "The reader model couldn't be downloaded: "
        + fetch.REFUSED.format(host="models.example.test")
        + " Or copy the file to this device yourself. "
        + f"ClientConnectorError: {refused}"
    )
    assert not isinstance(failed.value, JobFailedPermanently), "a refused connection is retried"


async def test_a_refused_certificate_fails_its_task_without_a_retry(probe: WeightStore) -> None:
    """The same certificate is refused on the next ask a second later, so no attempt is spent on it."""
    check = ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")
    check.verify_code = 20
    refused = aiohttp.ClientConnectorCertificateError(
        ConnectionKey("models.example.test", 443, True, True, None, None, None), check
    )

    with pytest.raises(WeightError) as failed:
        await probe.fetch(_weight(b"never arrives"), session_factory=lambda: Server(refused))

    assert isinstance(failed.value, JobFailedPermanently)
    assert str(failed.value).startswith(
        "The reader model couldn't be downloaded: a secure connection to models.example.test"
        " couldn't be made, because its certificate was issued by an authority Sift doesn't trust."
        " Or copy the file to this device yourself. ClientConnectorCertificateError: "
    )
