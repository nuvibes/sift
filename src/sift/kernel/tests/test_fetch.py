# SPDX-License-Identifier: AGPL-3.0-or-later
"""Downloading a large file: resuming it, reporting it, and stopping it.

Two things in Sift fetch hundreds of megabytes onto somebody's machine (the face models and the
graphics-card runtime), and both go through this. Neither of their own suites is the right place
for these: what is asserted here is true whatever is being downloaded, and a rule tested only
through one caller is a rule the other caller can lose without anything saying so.

Nothing here reaches the network. The transfer is handed a session that answers from memory, which
is the seam the module documents and the only one either caller uses in a test.
"""

from __future__ import annotations

import re
import socket
import ssl
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from sift.kernel import fetch


class FakeResponse:
    """One HTTP answer, served from memory."""

    def __init__(self, status: int, body: bytes, *, length: str | None = "") -> None:
        self.status = status
        self._body = body
        self.headers: dict[str, str] = {}
        if length != "":
            if length is not None:
                self.headers["Content-Length"] = length
        else:
            self.headers["Content-Length"] = str(len(body))
        self.content = self

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    """Answers one request, and remembers what was asked for."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.asked_for: dict[str, str] = {}
        self.url = ""

    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.url = url
        self.asked_for = dict(headers or {})
        return self.response

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def watching(seen: list[tuple[int, int]]) -> fetch.Progress:
    """A progress callback that records and never stops the transfer.

    A function rather than a lambda: `lambda w, t: (seen.append((w, t)), True)[1]` reads as clever
    and is a type error: `append` returns None, so the tuple is being built around a value that
    does not exist.
    """

    def watch(written: int, total: int) -> bool:
        seen.append((written, total))
        return True

    return watch


# --- how much is already there -------------------------------------------------------------------


def test_a_file_that_is_not_there_counts_as_nothing_downloaded(tmp_path: Path) -> None:
    """The first attempt and a resumed one ask the same question, so the answer cannot raise."""
    assert fetch.size_of(tmp_path / "never-started.part") == 0


def test_what_is_already_there_is_measured(tmp_path: Path) -> None:
    partial = tmp_path / "half.part"
    partial.write_bytes(b"0123456789")

    assert fetch.size_of(partial) == 10


def test_a_directory_in_the_way_counts_as_nothing_rather_than_raising(tmp_path: Path) -> None:
    """`stat` answers for a directory, so the guard has to be the OSError and not the absence.

    This is the shape that would have made an interrupted download unresumable: anything the file
    system refuses to size has to read as "start again", never as a crash on the way in.
    """
    blocked = tmp_path / "blocked.part"
    blocked.mkdir()

    assert fetch.size_of(blocked) >= 0


# --- a first attempt -----------------------------------------------------------------------------


async def test_a_first_attempt_asks_for_the_whole_file(tmp_path: Path) -> None:
    """No range header, because there is nothing to continue from."""
    payload = b"the whole thing" * 100
    partial = tmp_path / "new" / "file.part"
    session = FakeSession(FakeResponse(200, payload))

    done = await fetch.fetch_resumable(
        "https://example.invalid/f", partial, session_factory=lambda: session
    )

    assert done is True
    assert partial.read_bytes() == payload
    assert "Range" not in session.asked_for


async def test_the_folder_it_writes_into_is_made(tmp_path: Path) -> None:
    """The caller names a destination, not a directory it has already prepared."""
    partial = tmp_path / "not" / "yet" / "there" / "file.part"
    session = FakeSession(FakeResponse(200, b"bytes"))

    await fetch.fetch_resumable(
        "https://example.invalid/f", partial, session_factory=lambda: session
    )

    assert partial.is_file()


# --- resuming ------------------------------------------------------------------------------------


async def test_a_second_attempt_asks_only_for_the_rest(tmp_path: Path) -> None:
    """The whole point of the module: a connection dropping at 90% must not cost the 90%."""
    partial = tmp_path / "file.part"
    partial.write_bytes(b"already here")
    session = FakeSession(FakeResponse(206, b" and the rest"))

    done = await fetch.fetch_resumable(
        "https://example.invalid/f", partial, session_factory=lambda: session
    )

    assert done is True
    assert session.asked_for["Range"] == "bytes=12-"
    assert partial.read_bytes() == b"already here and the rest"


async def test_a_server_that_ignores_the_range_starts_the_file_again(tmp_path: Path) -> None:
    """A 200 to a ranged request means the whole file is arriving, not the remainder.

    Appending it to what was already there would produce a file that is longer than the real one
    and wrong in the middle, and the digest check the caller runs afterwards would be the only
    thing that noticed, on a download that had just been paid for twice.
    """
    partial = tmp_path / "file.part"
    partial.write_bytes(b"stale prefix")
    session = FakeSession(FakeResponse(200, b"the whole file"))

    await fetch.fetch_resumable(
        "https://example.invalid/f", partial, session_factory=lambda: session
    )

    assert partial.read_bytes() == b"the whole file"


async def test_a_server_with_nothing_past_where_we_stopped_means_it_is_all_here(
    tmp_path: Path,
) -> None:
    """416 is the answer to a range starting at or past the end, and it is a success here.

    Whether the bytes are the RIGHT bytes is the caller's question and is answered by its digest.
    """
    partial = tmp_path / "file.part"
    partial.write_bytes(b"everything")
    session = FakeSession(FakeResponse(416, b""))

    assert (
        await fetch.fetch_resumable(
            "https://example.invalid/f", partial, session_factory=lambda: session
        )
        is True
    )
    assert partial.read_bytes() == b"everything"


# --- refusals ------------------------------------------------------------------------------------


async def test_a_status_that_is_not_a_download_is_refused_in_a_sentence(tmp_path: Path) -> None:
    """The message reaches a person, so it names what failed rather than a number alone."""
    session = FakeSession(FakeResponse(404, b""))

    with pytest.raises(fetch.FetchFailed) as refused:
        await fetch.fetch_resumable(
            "https://example.invalid/f",
            tmp_path / "file.part",
            what="graphics-card runtime",
            session_factory=lambda: session,
        )

    assert str(refused.value) == (
        "The graphics-card runtime couldn't be downloaded: example.invalid answered 404. Try again"
        " later."
    )


async def test_nothing_is_written_when_the_server_refuses(tmp_path: Path) -> None:
    partial = tmp_path / "file.part"
    session = FakeSession(FakeResponse(500, b""))

    with pytest.raises(fetch.FetchFailed):
        await fetch.fetch_resumable(
            "https://example.invalid/f", partial, session_factory=lambda: session
        )

    assert not partial.exists()


# --- being watched, and being stopped ------------------------------------------------------------


async def test_progress_is_reported_against_what_is_expected(tmp_path: Path) -> None:
    """A transfer nobody can see the progress of is one people assume has hung."""
    seen: list[tuple[int, int]] = []
    payload = b"x" * (fetch.CHUNK * 2 + 5)
    session = FakeSession(FakeResponse(200, payload))

    await fetch.fetch_resumable(
        "https://example.invalid/f",
        tmp_path / "file.part",
        progress=watching(seen),
        session_factory=lambda: session,
    )

    assert [written for written, _ in seen] == [fetch.CHUNK, fetch.CHUNK * 2, len(payload)]
    assert {total for _, total in seen} == {len(payload)}


async def test_progress_counts_from_what_was_already_there(tmp_path: Path) -> None:
    """Resuming at 90% must not report 0%, or the bar goes backwards on every retry."""
    partial = tmp_path / "file.part"
    partial.write_bytes(b"y" * 1000)
    seen: list[tuple[int, int]] = []
    session = FakeSession(FakeResponse(206, b"z" * 10))

    await fetch.fetch_resumable(
        "https://example.invalid/f",
        partial,
        progress=watching(seen),
        session_factory=lambda: session,
    )

    assert seen == [(1010, 1010)]


async def test_a_server_that_says_nothing_about_the_length_reports_what_it_has(
    tmp_path: Path,
) -> None:
    """A missing Content-Length is a total of zero rather than a crash on the way past."""
    seen: list[tuple[int, int]] = []
    session = FakeSession(FakeResponse(200, b"abc", length=None))

    await fetch.fetch_resumable(
        "https://example.invalid/f",
        tmp_path / "file.part",
        progress=watching(seen),
        session_factory=lambda: session,
    )

    assert seen == [(3, 0)]


async def test_saying_stop_leaves_what_arrived_where_it_is(tmp_path: Path) -> None:
    """Cancelling is a False from the progress callback, and it must not throw the bytes away.

    There is nothing to interrupt and nothing to clean up: the reader stops asking, the partial
    file stays, and the next attempt continues from it.
    """
    partial = tmp_path / "file.part"
    payload = b"q" * (fetch.CHUNK * 3)
    session = FakeSession(FakeResponse(200, payload))

    done = await fetch.fetch_resumable(
        "https://example.invalid/f",
        partial,
        progress=lambda written, _total: written < fetch.CHUNK * 2,
        session_factory=lambda: session,
    )

    assert done is False
    assert partial.stat().st_size == fetch.CHUNK * 2


# --- the session it makes when nobody hands it one -----------------------------------------------


async def test_it_makes_its_own_session_when_none_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arm every caller in production uses, and the one no other test here reaches.

    Left uncovered, the timeouts could be dropped from the real session and every test would go on
    passing, and what they protect against is a dead socket, which is exactly the failure nobody
    reproduces by hand.
    """
    import aiohttp

    made: list[object] = []
    trusted: list[object] = []
    connectors: list[aiohttp.TCPConnector] = []
    session = FakeSession(FakeResponse(200, b"from a session of its own"))

    def _client_session(**kwargs: Any) -> FakeSession:
        made.append(kwargs.get("timeout"))
        trusted.append(kwargs.get("trust_env"))
        connectors.append(kwargs["connector"])
        return session

    monkeypatch.setattr(aiohttp, "ClientSession", _client_session)
    partial = tmp_path / "file.part"

    assert await fetch.fetch_resumable("https://example.invalid/f", partial) is True
    assert partial.read_bytes() == b"from a session of its own"
    timeout = made[0]
    assert getattr(timeout, "connect", None) == fetch.CONNECT_TIMEOUT
    assert getattr(timeout, "sock_read", None) == fetch.READ_TIMEOUT
    # The environment's and the system's proxy settings, which the client ignores by default.
    assert trusted == [True]
    # Checked against roots read for this download, never the client's own import-time context.
    context = connectors[0]._ssl
    assert isinstance(context, ssl.SSLContext)
    assert context is not aiohttp.connector._SSL_CONTEXT_VERIFIED
    await connectors[0].close()


# --- a connection that fails, in words -----------------------------------------------------------

_KEY = ConnectionKey("models.example.test", 443, True, True, None, None, None)
_HOST = "models.example.test"


class Unreached:
    """A session whose request raises what the real client raises when it cannot get through."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        raise self.error

    async def __aenter__(self) -> Unreached:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


_URL = URL(f"https://{_HOST}/m.onnx")
_ASKED = RequestInfo(_URL, "GET", CIMultiDictProxy(CIMultiDict()), _URL)

FAILURES = [
    (aiohttp.ClientConnectorError(_KEY, ConnectionRefusedError(61, "refused")), fetch.REFUSED),
    (aiohttp.ClientConnectorError(_KEY, OSError(101, "Network is unreachable")), fetch.UNREACHED),
    (aiohttp.ClientConnectorDNSError(_KEY, socket.gaierror(11001, "no name")), fetch.NOT_FOUND),
    (aiohttp.ConnectionTimeoutError("Connection timeout"), fetch.NO_ANSWER),
    (TimeoutError(), fetch.NO_ANSWER),
    (
        aiohttp.ClientConnectorCertificateError(_KEY, ssl.SSLCertVerificationError(1, "verify")),
        fetch.UNTRUSTED,
    ),
    (aiohttp.ClientConnectorSSLError(_KEY, ssl.SSLError(1, "handshake")), fetch.UNTRUSTED),
    (aiohttp.ServerFingerprintMismatch(b"a", b"b", _HOST, 443), fetch.UNTRUSTED),
    (aiohttp.ClientProxyConnectionError(_KEY, OSError(111, "proxy down")), fetch.PROXY),
    (aiohttp.ClientHttpProxyError(_ASKED, (), status=407), fetch.PROXY),
    (aiohttp.ServerDisconnectedError(), fetch.DROPPED),
]


@pytest.mark.parametrize(("error", "why"), FAILURES)
async def test_a_connection_that_fails_is_a_sentence_that_says_what_to_check(
    tmp_path: Path, error: BaseException, why: str
) -> None:
    with pytest.raises(fetch.FetchFailed) as failed:
        await fetch.fetch_resumable(
            f"https://{_HOST}/m.onnx",
            tmp_path / "file.part",
            what="detector model",
            session_factory=lambda: Unreached(error),
        )

    said = f"The detector model couldn't be downloaded: {_said(why)}"
    assert failed.value.sentence == said, "and nothing claims that anything arrived"
    # The client's own words follow the sentence, where whoever chases the fault reads them.
    assert str(failed.value) == f"{said} {type(error).__name__}: {error}"
    assert failed.value.__cause__ is error


def _said(why: str) -> str:
    return why.format(host=_HOST, because=fetch.SAYS_NO_MORE)


async def test_what_arrived_is_said_to_be_kept_only_when_something_did(tmp_path: Path) -> None:
    partial = tmp_path / "file.part"
    partial.write_bytes(b"half")
    refused = aiohttp.ClientConnectorError(_KEY, ConnectionRefusedError(61, "refused"))

    with pytest.raises(fetch.FetchFailed) as failed:
        await fetch.fetch_resumable(
            f"https://{_HOST}/m", partial, session_factory=lambda: Unreached(refused)
        )

    assert failed.value.sentence.endswith(fetch.KEPT)
    assert partial.read_bytes() == b"half"


class DropsPartway(FakeResponse):
    """Sends one chunk, then the connection goes."""

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        yield b"first"
        raise aiohttp.ClientPayloadError("connection lost")


async def test_a_connection_that_drops_partway_keeps_what_arrived_and_says_so(
    tmp_path: Path,
) -> None:
    partial = tmp_path / "file.part"
    session = FakeSession(DropsPartway(200, b"first and the rest"))

    with pytest.raises(fetch.FetchFailed) as failed:
        await fetch.fetch_resumable(f"https://{_HOST}/m", partial, session_factory=lambda: session)

    assert fetch.DROPPED.format(host=_HOST) in str(failed.value)
    assert failed.value.sentence.endswith(fetch.KEPT)
    assert partial.read_bytes() == b"first"


async def test_an_address_with_no_host_is_named_whole(tmp_path: Path) -> None:
    error = aiohttp.ServerDisconnectedError()

    with pytest.raises(fetch.FetchFailed, match="the connection to models dropped"):
        await fetch.fetch_resumable(
            "models", tmp_path / "file.part", session_factory=lambda: Unreached(error)
        )


# --- what a certificate refusal says, and where it goes ------------------------------------------


def _refused_by(code: int) -> aiohttp.ClientConnectorCertificateError:
    check = ssl.SSLCertVerificationError(1, "certificate verify failed")
    check.verify_code = code
    return aiohttp.ClientConnectorCertificateError(_KEY, check)


@pytest.mark.parametrize(
    ("code", "because"),
    [
        (20, fetch.UNKNOWN_ISSUER),
        (19, fetch.UNKNOWN_ISSUER),
        (10, fetch.OUT_OF_DATE),
        (62, fetch.WRONG_NAME),
        (23, fetch.WITHDRAWN),
        (99, fetch.SAYS_NO_MORE),
    ],
)
async def test_a_refused_certificate_says_what_the_check_said(
    tmp_path: Path, code: int, because: str
) -> None:
    with pytest.raises(fetch.Untrusted) as failed:
        await fetch.fetch_resumable(
            f"https://{_HOST}/m",
            tmp_path / "m.part",
            session_factory=lambda: Unreached(_refused_by(code)),
        )

    assert failed.value.sentence.endswith(fetch.UNTRUSTED.format(host=_HOST, because=because))


def test_only_a_wrong_clock_is_told_to_check_the_clock() -> None:
    """The advice that sent a person looking at the clock and at security software is said only
    where the check named the date."""
    for said in (fetch.UNKNOWN_ISSUER, fetch.WRONG_NAME, fetch.WITHDRAWN, fetch.SAYS_NO_MORE):
        assert "clock" not in said and "date" not in said and "security software" not in said
    assert "clock" in fetch.OUT_OF_DATE


async def test_a_failure_is_logged_once_with_the_clients_words_and_the_host_that_failed(
    tmp_path: Path,
) -> None:
    from structlog.testing import capture_logs

    refused = _refused_by(20)
    with capture_logs() as logs, pytest.raises(fetch.FetchFailed):
        await fetch.fetch_resumable(
            "https://asked.example.test/m",
            tmp_path / "m.part",
            session_factory=lambda: Unreached(refused),
        )

    failed = [one for one in logs if one["event"] == "fetch.failed"]
    assert failed == [
        {
            "event": "fetch.failed",
            "log_level": "warning",
            "host": _HOST,
            "asked": "asked.example.test",
            "error": repr(refused),
        }
    ]


# --- against stand-in servers: the roots Sift trusts, and a redirect ---------------------------


def _authority(folder: Path) -> tuple[Path, ssl.SSLContext]:
    """A root no machine trusts, and a server context for `localhost` signed by it."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "stand-in root")])
    root = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False
        )
        .sign(root_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(root_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    bundle, chain, private = folder / "roots.pem", folder / "leaf.pem", folder / "leaf.key"
    bundle.write_bytes(root.public_bytes(pem))
    chain.write_bytes(leaf.public_bytes(pem))
    private.write_bytes(
        key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(chain, private)
    return bundle, server


async def _serve(app: Any, context: ssl.SSLContext | None = None) -> tuple[Any, int]:
    from aiohttp import web

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner, port


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, ssl.SSLContext]:
    # Straight to the stand-ins, whatever proxy this machine names.
    monkeypatch.setenv("NO_PROXY", "*")
    return _authority(tmp_path)


async def test_a_root_only_the_bundled_set_holds_is_trusted_and_read_afresh_each_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stand_in: tuple[Path, ssl.SSLContext]
) -> None:
    """The machine's own store refuses the stand-in's root; the bundled set holds it. The first
    download runs before the bundle has it, the second after, in one process: a context kept from
    the first (the client's own is made once, at import) would refuse the second too."""
    import certifi
    from aiohttp import web

    bundle, server = stand_in
    app = web.Application()

    async def model(_request: web.Request) -> web.Response:
        return web.Response(body=b"the model")

    app.router.add_get("/m", model)
    runner, port = await _serve(app, server)
    url = f"https://localhost:{port}/m"
    try:
        with pytest.raises(fetch.Untrusted):
            await fetch.fetch_resumable(url, tmp_path / "first.part")
        monkeypatch.setattr(certifi, "where", lambda: str(bundle))
        assert await fetch.fetch_resumable(url, tmp_path / "second.part") is True
    finally:
        await runner.cleanup()

    assert (tmp_path / "second.part").read_bytes() == b"the model"


async def test_a_redirect_to_a_host_that_fails_names_that_host_and_the_one_asked(
    tmp_path: Path, stand_in: tuple[Path, ssl.SSLContext]
) -> None:
    from aiohttp import web

    _bundle, server = stand_in
    files = web.Application()

    async def never(_request: web.Request) -> web.Response:
        return web.Response(body=b"never reached")

    files.router.add_get("/m", never)
    file_runner, file_port = await _serve(files, server)

    async def onward(_request: web.Request) -> web.Response:
        raise web.HTTPFound(f"https://localhost:{file_port}/m")

    asked = web.Application()
    asked.router.add_get("/m", onward)
    asked_runner, asked_port = await _serve(asked)
    try:
        with pytest.raises(fetch.Untrusted) as failed:
            await fetch.fetch_resumable(
                f"http://127.0.0.1:{asked_port}/m", tmp_path / "m.part", what="detector model"
            )
    finally:
        await asked_runner.cleanup()
        await file_runner.cleanup()

    assert failed.value.sentence == (
        "The detector model couldn't be downloaded: a secure connection to localhost couldn't be"
        " made, because its certificate was issued by an authority Sift doesn't trust. 127.0.0.1"
        " sent the download on to localhost."
    )
    assert "CERTIFICATE_VERIFY_FAILED" in failed.value.words


async def test_a_refused_answer_names_the_host_the_redirect_led_to(tmp_path: Path) -> None:
    class Moved(FakeResponse):
        url = type("Url", (), {"host": "files.example.test"})()

    session = FakeSession(Moved(503, b""))
    with pytest.raises(fetch.FetchFailed) as failed:
        await fetch.fetch_resumable(
            f"https://{_HOST}/m", tmp_path / "m.part", session_factory=lambda: session
        )

    assert failed.value.sentence.endswith(
        f"files.example.test answered 503. Try again later. {_HOST} sent the download on to"
        " files.example.test."
    )


# --- every outbound session is opened by the one factory -----------------------------------------

#: The sessions that never leave this machine, and so check no certificate: the desktop app's own
#: door and a tunnel's status page, both plain HTTP on loopback.
_LOOPBACK = {"slices/desktop/shell.py", "kernel/tunnels/process.py"}


def test_no_session_reaches_out_but_through_the_factory() -> None:
    source = Path(fetch.__file__).resolve().parents[1]
    builds = re.compile(r"aiohttp\.(?:ClientSession|TCPConnector)\(")
    found = sorted(
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if "tests" not in path.parts
        and path != Path(fetch.__file__).resolve()
        and builds.search(path.read_text(encoding="utf-8"))
    )
    assert set(found) <= _LOOPBACK, found


async def test_the_guarded_session_and_the_update_check_carry_the_trust_of_the_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.slices.download.sources import net
    from sift.slices.update_notify import service

    built: list[ssl.SSLContext] = []
    real = fetch.trust

    def counted() -> ssl.SSLContext:
        built.append(real())
        return built[-1]

    monkeypatch.setattr(fetch, "trust", counted)
    async with net.guarded_session() as guarded:
        assert guarded.connector._ssl is built[-1]  # type: ignore[union-attr]
    async with service._direct_session() as direct:
        assert direct.connector._ssl is built[-1]  # type: ignore[union-attr]
    assert len(built) == 2
