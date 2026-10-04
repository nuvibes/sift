# SPDX-License-Identifier: AGPL-3.0-or-later
"""Named tunnels: validating one, starting one, knowing whether it is really up, and stopping it,
and the two sections a tunnel carries while it hosts a swap.

The client itself is not run here (it is a binary the image ships, exercised against the image),
so what is stood in for is the process and the status endpoint. Everything this module decides on
top of those is real: when a tunnel counts as up, what a lease refuses, and what draining waits for.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
import socket
import tempfile
import time
from base64 import b64encode
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel import ports
from sift.kernel.subprocess import SubprocessError
from sift.kernel.tunnels import process as tunnels
from sift.kernel.tunnels.process import (
    Listener,
    ListenPorts,
    TunnelConfigInvalid,
    TunnelError,
    TunnelProcess,
    TunnelSpec,
    validate_config,
)

# Whole, because the check names the part that is missing before it asks the client, so a
# fixture short of one would be refused here rather than reaching the thing under test.
#: What a WireGuard key looks like: 32 bytes written base64, which comes to 44 characters ending in
#: one '='. Built from a counting pattern rather than written out, so nothing in this file is a
#: string of high-entropy characters for a secret scanner to find and a reader to wonder about.
_A_KEY = b64encode(bytes(range(32))).decode()
_ANOTHER_KEY = b64encode(bytes(range(32, 64))).decode()

#: Whole, and with keys shaped like keys: the check names a missing part or a value that is
#: not a key before the client is asked, so a fixture short of either never reaches it.
_CONFIG = (
    f"[Interface]\nPrivateKey = {_A_KEY}\n\n"
    f"[Peer]\nPublicKey = {_ANOTHER_KEY}\nEndpoint = 198.51.100.7:51820\n"
)


class _Result:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


class _FakeProcess:
    """A process that is alive until it is asked to stop, and can be told to ignore the first ask.

    `exited` makes one that has already gone by the time anybody looks: the client that could not
    bind a port it was given, which leaves within milliseconds."""

    _next_pid = 9000

    def __init__(self, *, ignores_terminate: bool = False, exited: int | None = None) -> None:
        _FakeProcess._next_pid += 1
        self.pid = _FakeProcess._next_pid
        self.returncode: int | None = exited
        self.terminated = False
        self.killed = False
        self._ignores_terminate = ignores_terminate
        self._exited = asyncio.Event()
        if exited is not None:
            self._exited.set()

    def terminate(self) -> None:
        self.terminated = True
        if not self._ignores_terminate:
            self._exit(0)

    def kill(self) -> None:
        self.killed = True
        self._exit(-9)

    def _exit(self, code: int) -> None:
        self.returncode = code
        self._exited.set()

    async def wait(self, *, time_limit: float | None = None) -> int:
        await asyncio.wait_for(self._exited.wait(), time_limit)
        assert self.returncode is not None
        return self.returncode


def _spawns(process: _FakeProcess) -> Callable[..., Any]:
    async def spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
        return process

    return spawn


class _Holders:
    """Who the operating system says is on each port, as a test sets it, and what was ended.

    Stood in for every test here, because the real lookup starts a program per port (most of a
    second each on Windows), and nothing in this file listens on the ports it hands out. The real
    lookup is proved against a real listener in `kernel/tests/test_ports.py`."""

    def __init__(self) -> None:
        self.on: dict[int, ports.PortHolder] = {}
        self.asked: list[int] = []
        self.ended: list[int] = []


@pytest.fixture(autouse=True)
def holders(monkeypatch: pytest.MonkeyPatch) -> _Holders:
    found = _Holders()

    async def holder_of(port: int) -> ports.PortHolder | None:
        found.asked.append(port)
        return found.on.get(port)

    async def end_process(pid: int) -> bool:
        found.ended.append(pid)
        return True

    monkeypatch.setattr(ports, "holder_of", holder_of)
    monkeypatch.setattr(ports, "end_process", end_process)
    # A known pair, so what a test reads back is a number it wrote. The real choice is proved on
    # its own below, through `_REAL_FREE_PORTS`.
    monkeypatch.setattr(tunnels, "_free_ports", lambda: ListenPorts(45000, 45001))
    return found


_REAL_FREE_PORTS = tunnels._free_ports


@pytest.fixture
def spec() -> TunnelSpec:
    return TunnelSpec(id="t1", name="Sweden")


# --- validating a configuration before it is ever stored ----------------------------------------


async def test_a_configuration_the_client_accepts_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Path] = {}

    async def fake_run(argv: list[str], **_kwargs: object) -> _Result:
        seen["path"] = Path(argv[-1])
        # The file exists while the check runs, and only this person can read it.
        assert seen["path"].is_file()
        # POSIX ONLY. Windows accepts a mode and ignores it (the bits come back 0o666 whatever was
        # asked for), so this assertion would fail there while asserting nothing about a real
        # fault. What carries the same property on Windows is
        # the per-user temporary directory the file is made in, which is checked below.
        if os.name != "nt":
            assert seen["path"].stat().st_mode & 0o077 == 0
        else:
            assert seen["path"].parent.parent == Path(tempfile.gettempdir())
        assert "[http]" in seen["path"].read_text()
        return _Result(0)

    monkeypatch.setattr(tunnels, "run_once", fake_run)
    await validate_config(_CONFIG)
    # And it is gone afterwards: the configuration is a private key, so it lives on disk only for
    # as long as the thing that has to read it is reading it.
    assert not seen["path"].exists()
    assert not seen["path"].parent.exists()


async def test_a_configuration_the_client_rejects_says_what_to_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(_argv: list[str], **_kwargs: object) -> _Result:
        return _Result(1)

    monkeypatch.setattr(tunnels, "run_once", fake_run)
    # A WHOLE configuration the client still refuses: the parts are all present, so the answer is
    # about the file being malformed rather than about a missing half.
    with pytest.raises(TunnelConfigInvalid, match="exactly as it came"):
        await validate_config(_CONFIG)


@pytest.mark.parametrize(
    "section",
    [
        "[http]",
        "[socks5]",
        "[HTTP]",
        "  [Socks5]  ",
        "[TCPServerTunnel]",
        # Three read out of the vendored client itself. See
        # `tests/gates/test_tunnel_sections_match_the_client.py`, which keeps the list and the
        # client in step.
        "[UDPProxyTunnel]",
        "[STDIOTunnel]",
        "[SNI]",
    ],
)
async def test_a_configuration_that_opens_its_own_port_is_refused(
    monkeypatch: pytest.MonkeyPatch, section: str
) -> None:
    """Sift appends one proxy listener, on loopback, and that binding is the security property:
    anything else is an open proxy on the network the machine sits on, forwarding through the
    account paying for the tunnel.

    The promise covers the pasted half as well as the half Sift writes: passed to the client
    unread, a file carrying `BindAddress = 0.0.0.0` would open exactly that. Refused before the
    client is ever asked, which is why the runner here raises if it is called.
    """

    async def never_asked(_argv: list[str], **_kwargs: object) -> _Result:
        raise AssertionError("the client is not asked about a file that opens its own port")

    monkeypatch.setattr(tunnels, "run_once", never_asked)
    carrying = _CONFIG + f"\n{section}\nBindAddress = 0.0.0.0:8080\n"

    with pytest.raises(TunnelConfigInvalid, match="does more than connect"):
        await validate_config(carrying)


async def test_a_file_that_names_a_second_file_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `WGConfig = <path>` line makes the client read another file on this device as the
    WireGuard half of the configuration, so a pasted configuration could name any file the
    process can open. Refused before the client is asked, like a listener section."""

    async def never_asked(_argv: list[str], **_kwargs: object) -> _Result:
        raise AssertionError("the client is not asked about a file that names another file")

    monkeypatch.setattr(tunnels, "run_once", never_asked)
    carrying = _CONFIG + "\nWGConfig = C:/somewhere/else.conf\n"

    with pytest.raises(TunnelConfigInvalid, match="second file"):
        await validate_config(carrying)


async def test_a_section_name_inside_a_comment_is_not_a_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matched as a whole line, so a file that merely mentions one is not turned away.

    Refusing on a substring would reject a perfectly ordinary configuration whose comment explains
    what the proxy is for, and the refusal would name a section the reader cannot find.
    """

    async def fake_run(_argv: list[str], **_kwargs: object) -> _Result:
        return _Result(0)

    monkeypatch.setattr(tunnels, "run_once", fake_run)
    await validate_config(_CONFIG + "\n# provider note: no [http] section needed here\n")


async def test_a_key_that_is_not_a_key_says_what_one_looks_like(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated paste, a line broken in half by an email client, a placeholder somebody meant to
    replace: all of them reach the client as `invalid base64 string`, which names the field and
    nothing else. Refused here with the shape to compare against instead.
    """

    async def never_asked(_argv: list[str], **_kwargs: object) -> _Result:
        raise AssertionError("a key of the wrong shape is refused before the client is asked")

    monkeypatch.setattr(tunnels, "run_once", never_asked)
    with pytest.raises(TunnelConfigInvalid, match="is not a key"):
        await validate_config(_CONFIG.replace(_A_KEY, "PUT-YOUR-KEY-HERE"))


async def test_a_key_that_was_masked_on_a_website_is_named_as_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The masked key, as a provider's page shows it: `PrivateKey = *****`.

    Providers hide the private key on the page and reveal it on a click, so text copied off that
    page carries the mask. Every other line is perfect, the file is whole, and the client's answer
    is `invalid base64 string`, which names the field and says nothing about where the file came
    from. Refused here instead, with the thing to do.
    """

    async def never_asked(_argv: list[str], **_kwargs: object) -> _Result:
        raise AssertionError("a masked key is refused before the client is asked")

    monkeypatch.setattr(tunnels, "run_once", never_asked)
    with pytest.raises(TunnelConfigInvalid, match="hidden rather than given"):
        await validate_config(_CONFIG.replace(_A_KEY, "*****"))


async def test_a_key_shaped_like_a_key_is_left_for_the_client_to_judge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape check must not become a second opinion about which keys are good ones.

    Forty-four characters of base64 is what a key LOOKS like; whether it opens anything is the
    client's to say, and the whole reason the client is the authority here.
    """
    asked = False

    async def fake_run(_argv: list[str], **_kwargs: object) -> _Result:
        nonlocal asked
        asked = True
        return _Result(0)

    monkeypatch.setattr(tunnels, "run_once", fake_run)
    await validate_config(_CONFIG)
    assert asked


@pytest.mark.parametrize(
    ("config", "says"),
    [
        ("PrivateKey = x\n[Peer]\nEndpoint = a:1\n", "[Interface]"),
        ("[Interface]\n[Peer]\nEndpoint = a:1\n", "PrivateKey"),
        ("[Interface]\nPrivateKey = x\nEndpoint = a:1\n", "[Peer]"),
        ("[Interface]\nPrivateKey = x\n[Peer]\n", "Endpoint"),
    ],
)
async def test_half_a_configuration_says_which_half_is_missing(
    monkeypatch: pytest.MonkeyPatch, config: str, says: str
) -> None:
    """The commonest way to arrive here is part of a file: part of it selected, part of it pasted.

    The client answers all four of these with the same "not a valid config", which is true and
    useless: knowing WHICH part is absent is the difference between fixing it and trying the same
    thing again. Checked before the client is asked, so it never runs on a file that cannot work.
    """

    async def never_asked(_argv: list[str], **_kwargs: object) -> _Result:
        raise AssertionError("the client must not be asked about a file that is already incomplete")

    monkeypatch.setattr(tunnels, "run_once", never_asked)
    with pytest.raises(TunnelConfigInvalid, match=re.escape(says)):
        await validate_config(config)


async def test_a_client_that_will_not_run_is_not_a_valid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing or wedged binary must not read as "your file is fine"."""

    async def fake_run(_argv: list[str], **_kwargs: object) -> _Result:
        raise SubprocessError("no such binary")

    monkeypatch.setattr(tunnels, "run_once", fake_run)
    with pytest.raises(TunnelConfigInvalid):
        await validate_config(_CONFIG)


# --- starting one, and what "up" means ----------------------------------------------------------


async def test_starting_waits_for_the_far_end_to_answer(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(tunnels, "start_long_lived", _spawns(process))
    answers = iter([None, None, 1_700_000_000])
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _next(answers))

    tunnel = TunnelProcess(spec)
    await tunnel.start(_CONFIG)

    assert tunnel.running()
    assert tunnel.proxy_url == "http://127.0.0.1:45000"


async def test_a_client_that_runs_but_never_connects_is_not_a_tunnel(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec, holders: _Holders
) -> None:
    """The failure this is all for. A wrong key or a blocked port leaves the client running and
    answering, so a tunnel that reported itself up on the process alone would be a green control
    over nothing, and a site told to use it would go out unprotected or fail unexplained.

    And nobody is asked who holds its ports: a client that ran the whole budget held them, so the
    question could only cost a lookup and never change the answer."""
    process = _FakeProcess()
    monkeypatch.setattr(tunnels, "start_long_lived", _spawns(process))
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _none())
    monkeypatch.setattr(tunnels, "TUNNEL_HANDSHAKE_TIMEOUT_SECONDS", 0.05)

    tunnel = TunnelProcess(spec)
    with pytest.raises(TunnelError, match="Sweden"):
        await tunnel.start(_CONFIG)
    assert not tunnel.running()
    assert process.terminated
    assert holders.asked == []


async def test_starting_one_that_is_already_running_does_nothing(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    spawns = 0

    async def spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
        nonlocal spawns
        spawns += 1
        return _FakeProcess()

    monkeypatch.setattr(tunnels, "start_long_lived", spawn)
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _value(1_700_000_000))

    tunnel = TunnelProcess(spec)
    await tunnel.start(_CONFIG)
    await tunnel.start(_CONFIG)
    assert spawns == 1


async def test_a_process_that_dies_while_starting_stops_the_wait(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    process = _FakeProcess(exited=1)
    monkeypatch.setattr(tunnels, "start_long_lived", _spawns(process))
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _none())

    tunnel = TunnelProcess(spec)
    with pytest.raises(TunnelError):
        await tunnel.start(_CONFIG)


async def test_health_needs_a_recent_answer_not_merely_a_live_process(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(tunnels, "start_long_lived", _spawns(process))
    monkeypatch.setattr(
        TunnelProcess, "_read_metrics", lambda _self: _pair(int(time.time()), "198.51.100.7")
    )

    tunnel = TunnelProcess(spec)
    await tunnel.start(_CONFIG)
    healthy = await tunnel.health()
    assert healthy.up
    # Which server answered, which is the difference between a working tunnel and a working tunnel
    # to the wrong country.
    assert healthy.endpoint == "198.51.100.7"

    # The same live process, with the far end last heard from an hour ago.
    monkeypatch.setattr(
        TunnelProcess, "_read_metrics", lambda _self: _pair(int(time.time()) - 3600, "198.51.100.7")
    )
    stale = await tunnel.health()
    assert stale.running and not stale.up


# --- ports from the operating system, and whose client is whose --------------------------------
#
# Ports come from the operating system, a client is proved to be this store's by a token on its
# command line, and anything else holding a port is left alone, so two Sifts on one device never
# end each other's clients.


def test_the_ports_come_from_the_operating_system_and_nothing_is_on_them() -> None:
    """The real call, not a stand-in: two different ports, and both are free to bind this moment."""
    chosen = _REAL_FREE_PORTS()
    assert chosen.proxy != chosen.status
    for port in (chosen.proxy, chosen.status):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))


async def test_the_client_is_started_contained_on_the_ports_it_was_given_carrying_its_token(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """What the client is actually told: its configuration binds the proxy port, `-i` names the
    status port, and the configuration's folder carries this store's token, which is the one thing
    that later proves the client was started here. Through `start_long_lived`, the contained
    launcher: the stand-in below is only ever reached if the tunnel spawns through it."""
    seen: dict[str, Any] = {}

    async def spawn(argv: list[str], **_kwargs: object) -> _FakeProcess:
        seen["argv"] = argv
        seen["config"] = Path(argv[argv.index("-c") + 1]).read_text(encoding="utf-8")
        return _FakeProcess()

    monkeypatch.setattr(tunnels, "start_long_lived", spawn)
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _value(1_700_000_000))
    tunnel = TunnelProcess(spec, owner="01OWNERA")
    await tunnel.start(_CONFIG)

    argv = seen["argv"]
    assert argv[argv.index("-i") + 1] == "127.0.0.1:45001"
    assert "BindAddress = 127.0.0.1:45000" in seen["config"]
    folder = Path(argv[argv.index("-c") + 1]).parent.name
    assert folder.startswith("sift-tunnel-01OWNERA-")


async def _config_written_for(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec, listener: Listener | None
) -> str:
    """The whole configuration file a start hands the client, read while the client has it."""
    seen: dict[str, str] = {}

    async def spawn(argv: list[str], **_kwargs: object) -> _FakeProcess:
        seen["config"] = Path(argv[argv.index("-c") + 1]).read_text(encoding="utf-8")
        return _FakeProcess()

    monkeypatch.setattr(tunnels, "start_long_lived", spawn)
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _value(1_700_000_000))
    await TunnelProcess(spec).start(_CONFIG, listener=listener)
    return seen["config"]


async def test_a_hosting_tunnel_carries_exactly_the_two_listener_sections_after_its_proxy(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """The exact text, because the text IS the security property. The listener inside the tunnel
    hands its connections to loopback and nowhere else; the NAT-PMP relay binds loopback and reaches
    only the provider's gateway inside the tunnel. Both come after Sift's own `[http]`, and after
    the pasted half, which was refused if it carried either of them itself."""
    written = await _config_written_for(
        monkeypatch, spec, Listener(internal_port=41234, natpmp_port=52001, target_port=53002)
    )

    assert written == (
        _CONFIG
        + "\n[http]\nBindAddress = 127.0.0.1:45000\n"
        + "\n[TCPServerTunnel]\nListenPort = 41234\nTarget = 127.0.0.1:53002\n"
        + "\n[UDPProxyTunnel]\nBindAddress = 127.0.0.1:52001\nTarget = 10.2.0.1:5351\n"
    )


async def test_a_tunnel_that_is_not_hosting_carries_no_listener(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """The ordinary tunnel: the pasted half and Sift's proxy, and not a section more."""
    written = await _config_written_for(monkeypatch, spec, None)

    assert written == _CONFIG + "\n[http]\nBindAddress = 127.0.0.1:45000\n"


@pytest.mark.parametrize(
    "fields",
    [
        {"internal_port": 0, "natpmp_port": 52001, "target_port": 53002},
        {"internal_port": 41234, "natpmp_port": 65536, "target_port": 53002},
        {"internal_port": 41234, "natpmp_port": 52001, "target_port": "53002\n[socks5]"},
        {"internal_port": True, "natpmp_port": 52001, "target_port": 53002},
    ],
)
def test_a_listener_is_made_of_port_numbers_and_nothing_else(fields: dict[str, Any]) -> None:
    """Each field is written into the client's configuration as text, so a value that is not a
    port number could add a line, and a line can be a section."""
    with pytest.raises(ValueError, match="not a port number"):
        Listener(**fields)


def test_the_client_is_found_through_the_vendored_tool_search() -> None:
    """A bare name would make an install run whatever copy the machine had, or none.
    Read from the source rather than the value, because on a machine that has not fetched the
    tools the two answers are the same string."""
    tree = ast.parse(Path(tunnels.__file__).read_text(encoding="utf-8"))
    (value,) = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "TUNNEL_BINARY" for t in node.targets)
    ]
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name) and value.func.id == "vendored_tool"
    assert [ast.literal_eval(arg) for arg in value.args] == ["wireproxy"]


async def test_two_owners_never_end_each_others_client(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec, holders: _Holders
) -> None:
    """THE FAULT: two Sifts on one device, and the second is handed a port the first one's client
    is on. The second's client cannot bind it and leaves at once; the port's holder is the same
    program, but it carries the OTHER store's token, so it is left exactly where it is, named in
    the log by its process id, and the second tunnel comes up on a fresh pair instead."""
    first = TunnelProcess(spec, owner="01OWNERA")
    holders.on[45000] = ports.PortHolder(
        pid=111,
        name="wireproxy.exe",
        command_line=f"wireproxy.exe -c C:\\t\\{first._token()}x1\\tunnel.conf -i 127.0.0.1:45001",
    )
    second = TunnelProcess(TunnelSpec(id="t2", name="Norway"), owner="01OWNERB")
    with capture_logs() as logs:
        await _collide_once_then_start(monkeypatch, second)

    assert holders.ended == [], "the other Sift's client was ended"
    assert second.running() and second.proxy_url == "http://127.0.0.1:46000"
    events = [(entry["event"], entry.get("pid")) for entry in logs]
    assert ("tunnel.foreign_client_left", 111) in events
    assert not [event for event, _ in events if event == "tunnel.orphan_ended"]
    # Its process id, never its command line: that is a path into somebody's temporary folder.
    assert not [entry for entry in logs if "tunnel.conf" in str(entry)]


async def test_a_leftover_carrying_its_owners_token_is_ended(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec, holders: _Holders
) -> None:
    """The one client this owner may end: its own, which it started and is no longer tracking.
    The token proves it; a fresh pair is taken all the same, since nothing waits for the port."""
    tunnel = TunnelProcess(spec, owner="01OWNERA")
    holders.on[45001] = ports.PortHolder(
        pid=222,
        name="wireproxy.exe",
        command_line=f"wireproxy.exe -c C:\\t\\{tunnel._token()}x2\\tunnel.conf -i 127.0.0.1:45001",
    )
    with capture_logs() as logs:
        await _collide_once_then_start(monkeypatch, tunnel)

    assert holders.ended == [222]
    assert tunnel.running()
    assert ("tunnel.orphan_ended", 222) in [(entry["event"], entry.get("pid")) for entry in logs]


async def test_another_program_on_a_port_is_left_and_another_pair_taken(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec, holders: _Holders
) -> None:
    """Not the client at all: somebody's own VPN, a development server. Left alone, named by its
    program and process id, and the tunnel goes elsewhere."""
    holders.on[45000] = ports.PortHolder(pid=333, name="othervpn.exe", command_line="othervpn")
    tunnel = TunnelProcess(spec)
    with capture_logs() as logs:
        await _collide_once_then_start(monkeypatch, tunnel)

    assert holders.ended == []
    assert tunnel.running() and tunnel.proxy_url == "http://127.0.0.1:46000"
    taken = [entry for entry in logs if entry["event"] == "tunnel.port_taken"]
    assert taken and taken[0]["pid"] == 333 and taken[0]["program"] == "othervpn.exe"


async def test_an_answer_from_a_stranger_on_the_status_port_is_not_the_tunnel_coming_up(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec, holders: _Holders
) -> None:
    """The race the proof exists for. The client is still alive in the few milliseconds before it
    notices it could not bind, and a status read in that window reaches whatever DID bind the port,
    which, if it is another tunnel client, answers with a perfectly good handshake. So a tunnel
    is up only once the process behind both ports is shown to be its own client."""
    stranger = ports.PortHolder(pid=444, name="wireproxy.exe", command_line="wireproxy.exe")
    holders.on[45001] = stranger
    pairs = iter([ListenPorts(45000, 45001), ListenPorts(46000, 46001)])
    monkeypatch.setattr(tunnels, "_free_ports", lambda: next(pairs))
    spawned: list[_FakeProcess] = []

    async def spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
        spawned.append(_FakeProcess())
        return spawned[-1]

    monkeypatch.setattr(tunnels, "start_long_lived", spawn)
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _value(1_700_000_000))
    tunnel = TunnelProcess(spec)
    await tunnel.start(_CONFIG)

    assert len(spawned) == 2 and spawned[0].terminated, "the first client was trusted"
    assert tunnel.proxy_url == "http://127.0.0.1:46000"
    assert holders.ended == []


async def test_a_lookup_that_cannot_tell_does_not_stop_a_tunnel_that_answered(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec, holders: _Holders
) -> None:
    """None is "nothing listening, or it cannot be told". A client that is running and answered is
    what was trusted before the question could be asked, so an unanswerable lookup does not turn a
    working tunnel off, and it asked about both ports."""
    tunnel = await _started(monkeypatch, spec)
    assert tunnel.running()
    assert sorted(holders.asked) == [45000, 45001]


async def test_ports_that_are_taken_every_time_give_up_and_say_so(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec, holders: _Holders
) -> None:
    """Three pairs, each taken before the client could bind it: something on the device is taking
    ports as fast as they are freed, and the refusal says that rather than trying for ever."""
    pairs = iter([ListenPorts(45000 + n, 45100 + n) for n in range(3)])
    monkeypatch.setattr(tunnels, "_free_ports", lambda: next(pairs))
    for n in range(3):
        holders.on[45000 + n] = ports.PortHolder(pid=500 + n, name="x.exe", command_line="x")

    async def spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
        return _FakeProcess(exited=2)

    monkeypatch.setattr(tunnels, "start_long_lived", spawn)
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _none())
    tunnel = TunnelProcess(spec)
    with pytest.raises(TunnelError, match="another program on this device"):
        await tunnel.start(_CONFIG)
    assert not tunnel.running() and holders.ended == []


async def test_a_start_that_is_cancelled_stops_the_client_it_made(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """A start abandoned part-way (the request that asked for it gone) must not leave the client
    running and registered with no handshake, which the next start would take as up."""
    process = _FakeProcess()
    monkeypatch.setattr(tunnels, "start_long_lived", _spawns(process))
    waiting = asyncio.Event()

    async def never(_self: TunnelProcess) -> None:
        waiting.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(TunnelProcess, "_read_handshake", never)
    tunnel = TunnelProcess(spec)
    starting = asyncio.create_task(tunnel.start(_CONFIG))
    await waiting.wait()
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert process.terminated and not tunnel.running()


async def test_health_of_something_that_is_not_running(spec: TunnelSpec) -> None:
    health = await TunnelProcess(spec).health()
    assert not health.running and not health.up and health.last_handshake_at is None


# --- the status endpoint, over a real request ----------------------------------------------------


@pytest.fixture
async def status_server() -> AsyncIterator[Callable[[str], Awaitable[int]]]:
    """A server standing in for a tunnel's status port, answering with whatever body a test wants.
    The port is one the system hands out, and is what `serve` answers: a number written here would
    be bound twice when two of these run at once."""
    servers: list[asyncio.Server] = []

    async def serve(body: str) -> int:
        async def handle(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await _reader.readuntil(b"\r\n\r\n")
            payload = body.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        servers.append(server)
        return int(server.sockets[0].getsockname()[1])

    yield serve
    for server in servers:
        server.close()
        await server.wait_closed()


async def test_the_server_address_is_read_without_its_port(
    status_server: Callable[[str], Awaitable[int]],
) -> None:
    """The port is the provider's and nobody acts on it; the address is what "which server am I on"
    means. Split from the right, so an IPv6 endpoint keeps its colons and loses only the port."""
    port = await status_server("endpoint=198.51.100.7:51820\nlast_handshake_time_sec=1700000000\n")
    handshake, endpoint = await _with_status_on(port)._read_metrics()
    assert (handshake, endpoint) == (1_700_000_000, "198.51.100.7")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("last_handshake_time_sec=1700000000\ntx_bytes=1\n", 1_700_000_000),
        # Never connected: the client reports the field as zero rather than omitting it.
        ("last_handshake_time_sec=0\n", None),
        ("tx_bytes=1\nrx_bytes=2\n", None),
        ("last_handshake_time_sec=not-a-number\n", None),
    ],
)
async def test_the_handshake_is_read_from_what_the_client_answers(
    status_server: Callable[[str], Awaitable[int]], body: str, expected: int | None
) -> None:
    assert await _with_status_on(await status_server(body))._read_handshake() == expected


async def test_a_status_endpoint_that_does_not_answer_is_not_a_handshake() -> None:
    """Nothing listening yet is the ordinary case while a tunnel is starting."""
    with socket.socket() as held:
        # Bound and never listening: a port nothing answers on, and nothing else can take.
        held.bind(("127.0.0.1", 0))
        assert await _with_status_on(held.getsockname()[1])._read_handshake() is None


async def test_a_tunnel_with_no_client_reads_no_status_and_hands_out_no_proxy() -> None:
    """Between runs there are no ports at all, so there is nothing to read and nothing to hand a
    download, rather than the port the last run happened to have."""
    tunnel = TunnelProcess(TunnelSpec(id="t", name="Test"))
    assert await tunnel._read_metrics() == (None, None)
    with pytest.raises(TunnelError, match="not available"):
        _ = tunnel.proxy_url


def _with_status_on(status: int) -> TunnelProcess:
    """A tunnel whose client was given `status` as its status port, without running one."""
    tunnel = TunnelProcess(TunnelSpec(id="t", name="Test"))
    tunnel._ports = ListenPorts(proxy=status, status=status)
    return tunnel


# --- leases, draining, and stopping ---------------------------------------------------------------


async def test_a_lease_hands_out_the_proxy_of_the_tunnel_it_is_for(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    tunnel = await _started(monkeypatch, spec)
    async with tunnel.lease() as proxy:
        assert proxy == "http://127.0.0.1:45000"


async def test_a_tunnel_that_is_not_running_refuses_a_lease_by_name(spec: TunnelSpec) -> None:
    """Fail closed. The alternative (carrying on without the tunnel) sends the traffic out of
    the address the tunnel was chosen to avoid, on the one site that was asked about."""
    tunnel = TunnelProcess(spec)
    with pytest.raises(TunnelError, match="Sweden"):
        async with tunnel.lease():
            pass


async def test_a_draining_tunnel_refuses_new_work_but_finishes_what_it_has(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    tunnel = await _started(monkeypatch, spec)
    started = asyncio.Event()
    release = asyncio.Event()

    async def in_flight() -> None:
        async with tunnel.lease():
            started.set()
            await release.wait()

    task = asyncio.create_task(in_flight())
    await started.wait()

    drain = asyncio.create_task(tunnel.drain())
    await asyncio.sleep(0)
    assert tunnel.draining
    assert not drain.done(), "draining stopped the tunnel while a download was still using it"

    with pytest.raises(TunnelError):
        async with tunnel.lease():
            pass

    release.set()
    await task
    await drain
    assert not tunnel.running()


async def test_draining_waits_for_the_last_download_not_the_first(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """Two downloads can share a tunnel. Stopping when the first of them finishes would cut the
    others off, which is the drain doing exactly what draining exists to avoid."""
    tunnel = await _started(monkeypatch, spec)
    running = asyncio.Event()
    first = asyncio.Event()
    second = asyncio.Event()

    async def holder(release: asyncio.Event, announce: bool) -> None:
        async with tunnel.lease():
            if announce:
                running.set()
            await release.wait()

    tasks = [
        asyncio.create_task(holder(first, False)),
        asyncio.create_task(holder(second, True)),
    ]
    await running.wait()

    drain = asyncio.create_task(tunnel.drain())
    first.set()
    await tasks[0]
    await asyncio.sleep(0)
    assert not drain.done(), "the tunnel stopped while a second download was still on it"

    second.set()
    await tasks[1]
    await drain
    assert not tunnel.running()


async def test_stopping_now_does_not_wait_for_anything(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    tunnel = await _started(monkeypatch, spec)
    async with tunnel.lease():
        await tunnel.stop_now()
        assert not tunnel.running()


async def test_a_client_that_ignores_being_asked_is_killed(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    process = _FakeProcess(ignores_terminate=True)
    monkeypatch.setattr(tunnels, "start_long_lived", _spawns(process))
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _value(int(time.time())))
    tunnel = TunnelProcess(spec)
    await tunnel.start(_CONFIG)

    monkeypatch.setattr(tunnels, "_STOP_GRACE_SECONDS", 0.05)
    await tunnel.stop_now()
    assert process.terminated and process.killed


async def test_stopping_one_that_is_already_stopped_is_nothing(spec: TunnelSpec) -> None:
    tunnel = TunnelProcess(spec)
    await tunnel.stop_now()
    assert not tunnel.running()


# --- small helpers -------------------------------------------------------------------------------


async def _started(monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec) -> TunnelProcess:
    monkeypatch.setattr(tunnels, "start_long_lived", _spawns(_FakeProcess()))
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _value(int(time.time())))
    tunnel = TunnelProcess(spec)
    await tunnel.start(_CONFIG)
    return tunnel


async def _collide_once_then_start(monkeypatch: pytest.MonkeyPatch, tunnel: TunnelProcess) -> None:
    """Start `tunnel` where its first pair of ports is somebody else's.

    The first client cannot bind and has left by the time anybody looks, exactly as the vendored
    client does (measured: exit 1 or 2 within about 20 ms). The second pair is free."""
    pairs = iter([ListenPorts(45000, 45001), ListenPorts(46000, 46001)])
    monkeypatch.setattr(tunnels, "_free_ports", lambda: next(pairs))
    spawned = iter([_FakeProcess(exited=2), _FakeProcess()])

    async def spawn(*_args: object, **_kwargs: object) -> _FakeProcess:
        return next(spawned)

    monkeypatch.setattr(tunnels, "start_long_lived", spawn)
    monkeypatch.setattr(TunnelProcess, "_read_handshake", lambda _self: _value(1_700_000_000))
    await tunnel.start(_CONFIG)


async def _value(seconds: int) -> int:
    return seconds


async def _none() -> None:
    return None


async def _pair(seconds: int | None, endpoint: str | None) -> tuple[int | None, str | None]:
    return seconds, endpoint


async def _next(answers: Any) -> int | None:
    value: int | None = next(answers)
    return value


async def test_a_client_that_is_not_on_the_machine_is_an_installation_problem(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """The one failure that is about the INSTALLATION rather than about the tunnel.

    A program named as a bare `wireproxy` is correct for a container image and true nowhere else,
    so on an install the program can simply not be there. That must not escape as a 500: what
    comes back names the one cause and the one fix.
    """

    async def missing(argv: list[str], **_kwargs: object) -> object:
        # What the contained launcher raises for a program that is not there: the operating
        # system's own error, as the cause.
        try:
            raise OSError(2, "The system cannot find the file specified")
        except OSError as exc:
            raise SubprocessError(f"could not run {argv[0]!r}") from exc

    monkeypatch.setattr(tunnels, "start_long_lived", missing)

    tunnel = TunnelProcess(spec)
    with pytest.raises(TunnelError, match="installation is incomplete") as raised:
        await tunnel.start(_CONFIG)
    _names_both_causes_and_one_fix(str(raised.value))


def _names_both_causes_and_one_fix(said: str) -> None:
    """The missing-program sentence, as the Sites screen draws it.

    Two causes remove the program and one fix restores it. A virus scanner taking it off the disk is
    the one people do not think of, because the install finished cleanly the day it ran, so the
    sentence names it rather than leaving "the installation is incomplete" to send somebody hunting
    for a fault in an install that was whole. No spaced double hyphen either: the words are shown
    on screen, and a double hyphen there is a typing habit, not punctuation.
    """
    assert "your antivirus removed it" in said, said
    assert "installing Sift again over the top replaces it" in said, said
    assert " -- " not in said, said


async def test_a_configuration_that_cannot_be_checked_because_the_client_is_gone_says_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two faults produce the same exception and need different sentences.

    A program that will not start and one that started and hung both arrive here as the same
    failure, so they are told apart by asking whether the program is on the machine at all, and a
    missing one is not something "try saving it again" can ever fix.
    """

    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise SubprocessError("could not run it")

    monkeypatch.setattr(tunnels, "run_once", refuse)
    monkeypatch.setattr(tunnels, "_client_is_present", lambda: False)

    with pytest.raises(TunnelConfigInvalid, match="installation is incomplete") as raised:
        await validate_config(_CONFIG)
    _names_both_causes_and_one_fix(str(raised.value))


async def test_a_configuration_that_cannot_be_checked_with_the_client_present_says_try_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half. The program is there, so this is about the check rather than the install."""

    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise SubprocessError("it hung")

    monkeypatch.setattr(tunnels, "run_once", refuse)
    monkeypatch.setattr(tunnels, "_client_is_present", lambda: True)

    with pytest.raises(TunnelConfigInvalid, match="Try saving it again"):
        await validate_config(_CONFIG)


# --- where a tunnel leaves from -------------------------------------------------------------------


async def test_a_tunnels_exit_is_read_through_its_own_proxy_once_a_run(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """The address its traffic leaves from, asked of an echo through the tunnel's own proxy and
    kept while the run lasts. A new run asks again; an answer that is not a public IPv4 address,
    or a tunnel with no client, is no address at all."""
    asked: list[bytes] = []
    answer = ["8.8.4.4"]

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        asked.append(head.split(b"\r\n", 1)[0])
        body = answer[0].encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (len(body), body)
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    monkeypatch.setattr(tunnels, "EXIT_ECHO_URL", "http://echo.example/")
    tunnel = await _started(monkeypatch, spec)
    try:
        tunnel._ports = ListenPorts(proxy=port, status=port + 1)
        assert await tunnel.exit_address() == "8.8.4.4"
        assert await tunnel.exit_address() == "8.8.4.4"
        assert asked == [b"GET http://echo.example/ HTTP/1.1"], "once, through the tunnel's proxy"

        tunnel._ports = ListenPorts(proxy=port, status=port + 1)
        answer[0] = "10.2.0.2"
        assert await tunnel.exit_address() is None, "a private address is not an exit"
        answer[0] = "not an address"
        assert await tunnel.exit_address() is None
        assert len(asked) == 3, "a new run of the client asks again"

        await tunnel.stop_now()
        assert await tunnel.exit_address() is None
        assert len(asked) == 3
    finally:
        server.close()


async def test_an_echo_that_refuses_or_is_not_there_is_no_exit_and_is_asked_again(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """A refusal is not an address, and it is not kept: the next ask of the same run asks again."""
    asked: list[int] = []

    async def refusing(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        asked.append(1)
        writer.write(b"HTTP/1.1 503 Busy\r\nContent-Length: 7\r\nConnection: close\r\n\r\n8.8.4.4")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(refusing, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    monkeypatch.setattr(tunnels, "EXIT_ECHO_URL", "http://echo.example/")
    tunnel = await _started(monkeypatch, spec)
    try:
        tunnel._ports = ListenPorts(proxy=port, status=port + 1)
        assert await tunnel.exit_address() is None, "a refusal's body is not read as an address"
        assert await tunnel.exit_address() is None
        assert len(asked) == 2

        server.close()
        await server.wait_closed()
        assert await tunnel.exit_address() is None, "a proxy that is not listening is no exit"
    finally:
        server.close()
        await tunnel.stop_now()


async def test_an_exit_read_across_a_restart_of_the_client_is_answered_and_not_kept(
    monkeypatch: pytest.MonkeyPatch, spec: TunnelSpec
) -> None:
    """The client restarted while the echo answered: the address belongs to the run that asked,
    so it is not kept for the new run, which asks for its own."""
    tunnel = await _started(monkeypatch, spec)
    asked: list[int] = []

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        asked.append(1)
        tunnel._ports = ListenPorts(proxy=port, status=port + 1)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\n8.8.4.4")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    monkeypatch.setattr(tunnels, "EXIT_ECHO_URL", "http://echo.example/")
    try:
        tunnel._ports = ListenPorts(proxy=port, status=port + 1)
        assert await tunnel.exit_address() == "8.8.4.4"
        assert tunnel._exit is None, "nothing kept for a run that did not ask"
        assert await tunnel.exit_address() == "8.8.4.4"
        assert len(asked) == 2
    finally:
        server.close()
        await tunnel.stop_now()
