# SPDX-License-Identifier: AGPL-3.0-or-later
"""Named ways out to the internet, and the WireGuard client processes behind them.

Up means a handshake happened; turning one off drains; the configuration is a private key."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import shutil
import socket
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import aiohttp

from sift.kernel import ports
from sift.kernel.config import vendored_tool
from sift.kernel.fetch import outbound_session
from sift.kernel.ids import new_id
from sift.kernel.log import get_logger
from sift.kernel.subprocess import LongLivedChild, SubprocessError, start_long_lived
from sift.kernel.subprocess import run as run_once
from sift.kernel.tunnels.client import client_fault

log = get_logger(__name__)

#: Found beside Sift's other vendored tools first; on Windows nothing puts it on PATH.
TUNNEL_BINARY = vendored_tool("wireproxy")

#: Generous: a woken machine or a loaded provider can take several attempts.
TUNNEL_HANDSHAKE_TIMEOUT_SECONDS = 20.0

#: WireGuard rekeys every two minutes under traffic, so anything older carries nothing.
TUNNEL_STALE_HANDSHAKE_SECONDS = 300.0

#: The provider's NAT-PMP service inside the tunnel; a provider elsewhere simply never replies.
NATPMP_GATEWAY = "10.2.0.1:5351"

_CONFIGTEST_TIMEOUT = 15.0
_HEALTH_TIMEOUT = 3.0
_POLL_INTERVAL = 0.5
#: A second pair remedies a race for a port; a third failure means something takes them all.
_PORT_ATTEMPTS = 3
#: Every client's configuration folder starts with this (see `TunnelProcess._token`).
_FOLDER_PREFIX = "sift-tunnel-"
#: How long a client gets to close its socket after being asked to stop, before it is killed.
_STOP_GRACE_SECONDS = 5.0
#: The client redacts its keys; only two fields are read, and nothing else reaches a log.
_HANDSHAKE_FIELD = "last_handshake_time_sec="

#: The server this tunnel is talking to, as `address:port`. Only the address half is kept.
_ENDPOINT_FIELD = "endpoint="

#: An echo answering with the IPv4 address a request came from: the provider's server.
EXIT_ECHO_URL = "https://api.ipify.org"
#: How long the echo is given, and the most of its answer read: an IPv4 address is 15 characters.
_EXIT_TIMEOUT = 5.0
_EXIT_MOST_BYTES = 64


class TunnelError(Exception):
    """A tunnel could not be started, or is not usable."""


class TunnelConfigInvalid(TunnelError):
    """The configuration is not one the client can read."""


class TunnelClientLost(TunnelError):
    """The tunnel program is gone or altered; the check already logged it once."""


#: A sentence, not a stack trace, naming the antivirus as the other way the program goes.
_NO_CLIENT = (
    "Sift cannot find the tunnel program it runs, so no tunnel can be started. It is shipped with "
    "Sift, so either the installation is incomplete or your antivirus removed it; installing Sift "
    "again over the top replaces it."
)


def _program() -> str:
    """The client as found when run, so a program restored since is found with no restart."""
    return vendored_tool("wireproxy")


def _client_is_present() -> bool:
    """Whether the tunnel program is on the machine at all; blocking."""
    program = _program()
    return Path(program).is_file() or shutil.which(program) is not None


def _client_is_missing(exc: Exception) -> TunnelError:
    """Turn "the program is not there" into something a person can act on, and log the cause."""
    log.warning("tunnel.client_missing", binary=TUNNEL_BINARY, detail=str(exc.__cause__ or exc))
    return TunnelError(_NO_CLIENT)


def _is_the_client_program(holder: ports.PortHolder) -> bool:
    """Whether a process runs the shipped tunnel program, by name, which says nothing of whose."""
    return Path(holder.name).stem.lower() == Path(TUNNEL_BINARY).stem.lower()


@dataclass(frozen=True, slots=True)
class TunnelSpec:
    """What identifies one tunnel; no port, as two Sifts on one device would share it."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ListenPorts:
    """The two loopback ports one run of a client listens on: its proxy and its status."""

    proxy: int
    status: int


def _free_ports() -> ListenPorts:
    """Two free loopback ports chosen by the system; `start` proves the client then holds them."""
    with socket.socket() as proxy, socket.socket() as status:
        proxy.bind(("127.0.0.1", 0))
        status.bind(("127.0.0.1", 0))
        return ListenPorts(proxy=proxy.getsockname()[1], status=status.getsockname()[1])


@dataclass(frozen=True, slots=True)
class TunnelHealth:
    """What a screen may know about a tunnel: the endpoint, never the public key."""

    name: str
    running: bool
    up: bool
    draining: bool
    last_handshake_at: int | None
    #: The far server's address without its port; None while nothing has answered.
    endpoint: str | None = None


def _http_section(port: int) -> str:
    """The proxy listener appended to a configuration; loopback only, or it is an open proxy."""
    return f"\n[http]\nBindAddress = 127.0.0.1:{port}\n"


def _is_a_port(value: object) -> bool:
    """A real port number: an int (never a bool, never a string) from 1 to 65535."""
    return type(value) is int and 0 < value < 65536


@dataclass(frozen=True, slots=True)
class Listener:
    """What a tunnel needs to host a swap: a port inside the tunnel, and a way to ask for it.

    Each field is checked to be a port, as it is written into the configuration as text."""

    internal_port: int
    natpmp_port: int
    target_port: int

    def __post_init__(self) -> None:
        for field in ("internal_port", "natpmp_port", "target_port"):
            if not _is_a_port(getattr(self, field)):
                raise ValueError(f"{field} is not a port number")


def _listener_sections(listener: Listener) -> str:
    """The two sections a hosting tunnel carries after its `[http]`, both bound on loopback."""
    return (
        f"\n[TCPServerTunnel]\nListenPort = {listener.internal_port}\n"
        f"Target = 127.0.0.1:{listener.target_port}\n"
        f"\n[UDPProxyTunnel]\nBindAddress = 127.0.0.1:{listener.natpmp_port}\n"
        f"Target = {NATPMP_GATEWAY}\n"
    )


#: The lines a configuration needs, checked first so the refusal can say which is missing.
_MUST_CONTAIN: tuple[tuple[str, str], ...] = (
    (
        "[interface]",
        "The file is missing its [Interface] section, so it is not a whole configuration \u2014 "
        "copy the file from your provider again, all of it.",
    ),
    (
        "privatekey",
        "The file has no PrivateKey line. That is the part that identifies you to the provider, so "
        "a configuration without it cannot connect \u2014 copy the file again, all of it.",
    ),
    (
        "[peer]",
        "The file is missing its [Peer] section, the part that says which server to connect to. "
        "Copy the whole file again.",
    ),
    (
        "endpoint",
        "The file has no Endpoint line, so there is no server address to connect to \u2014 copy "
        "the file again, all of it.",
    ),
)


#: Sections that open a listener or move data, read out of the vendored binary and checked by
#: a gate; a pasted file carrying one is refused, never rewritten. `WGConfig` reads a second
#: file from disk.
_SECOND_FILE = re.compile(r"(?i)^wgconfig\s*=")

_LISTENER_SECTIONS: tuple[str, ...] = (
    "[http]",
    "[socks5]",
    "[tcpclienttunnel]",
    "[tcpservertunnel]",
    "[udpproxytunnel]",
    "[stdiotunnel]",
    "[sni]",
)


#: A key line, and what it was given.
_KEY_LINE = re.compile(
    r"^[ \t]*(PrivateKey|PublicKey)[ \t]*=[ \t]*(\S*)", re.IGNORECASE | re.MULTILINE
)

#: A placeholder copied off a provider's page that masks the key until clicked.
_MASKED = re.compile(r"^[*.x_-]{3,}$|hidden|redact|your.?key", re.IGNORECASE)

#: What a WireGuard key is: 32 bytes, written base64. Always 44 characters ending in one '='.
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _check_no_listeners(flattened: str, config: str) -> None:
    """Refuse a configuration that carries a listener of its own or names a second file."""
    for line in flattened.splitlines():
        heading = line.strip()
        if _SECOND_FILE.match(heading):
            log.warning("tunnel.config_names_a_second_file", **_shape_of(config))
            raise TunnelConfigInvalid(
                "That file points at a second file (its WGConfig line), which the tunnel program "
                "would read from this device in its place. Paste the provider's configuration "
                "itself, with nothing but its [Interface] and [Peer] sections."
            )
        if heading in _LISTENER_SECTIONS:
            log.warning("tunnel.config_has_listener", section=heading, **_shape_of(config))
            raise TunnelConfigInvalid(
                f"That file does more than connect (its {heading} section). Sift adds the one "
                "proxy it needs, which only Sift can reach. Anything else in the file would carry "
                "other traffic on your network, through the account that pays for the tunnel. Use the plain WireGuard configuration from your provider, with "
                "nothing but its [Interface] and [Peer] sections."
            )


def _check_the_keys(config: str) -> None:
    """Refuse a key that is not one, telling a masked key apart from a malformed one."""
    for found in _KEY_LINE.finditer(config):
        field, value = found.group(1), found.group(2)
        if _MASKED.match(value):
            raise TunnelConfigInvalid(
                f"The {field} in that file is hidden rather than given \u2014 it reads {value!r}. "
                "Providers mask the key on their website until you click to reveal it, so text "
                "copied off the page carries the mask. Download the configuration FILE from your "
                "provider instead, or reveal the key first and copy it then."
            )
        if not _KEY_SHAPE.match(value):
            raise TunnelConfigInvalid(
                f"The {field} in that file is not a key. It should be 44 characters ending in "
                "'=' \u2014 download the configuration file from your provider again and use it "
                "exactly as it came."
            )


def _shape_of(config: str) -> dict[str, object]:
    """What a configuration contains, never what it says: the file is a private key."""
    lines = [line.strip() for line in config.splitlines() if line.strip()]
    sections = [line.lower() for line in lines if line.startswith("[")]
    fields = sorted(
        {line.split("=", 1)[0].strip().lower() for line in lines if "=" in line and line[0] != "#"}
    )
    return {"lines": len(lines), "sections": sections, "fields": fields, "bytes": len(config)}


async def validate_config(config: str, *, port: int = 9000) -> None:
    """Refuse a configuration the client cannot read, before it is ever stored."""
    flattened = config.lower()
    for needle, sentence in _MUST_CONTAIN:
        if needle not in flattened:
            log.warning("tunnel.config_incomplete", missing=needle, **_shape_of(config))
            raise TunnelConfigInvalid(sentence)
    _check_no_listeners(flattened, config)
    _check_the_keys(config)
    # The program first, so a missing one is not blamed on the file.
    lost = await client_fault()
    if lost is not None:
        raise TunnelConfigInvalid(lost)

    async with _config_file(config, port, prefix=_FOLDER_PREFIX) as path:
        try:
            result = await run_once(
                [_program(), "--configtest", "-c", str(path)], time_limit=_CONFIGTEST_TIMEOUT
            )
        except SubprocessError as exc:
            # A missing program and a hung one raise alike; only one is fixed by saving again.
            if not await asyncio.to_thread(_client_is_present):
                raise TunnelConfigInvalid(_NO_CLIENT) from exc
            raise TunnelConfigInvalid(
                "The tunnel could not be checked. Try saving it again."
            ) from exc
    if result.returncode != 0:
        # The client's own words name the line it choked on; logged, never shown.
        said = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        log.warning("tunnel.config_rejected", detail=said[:400], **_shape_of(config))
        raise TunnelConfigInvalid(
            "The tunnel client wouldn't read that configuration. It has all the parts it needs, so "
            "something in it is malformed. Download the file from your provider again, and use it "
            "exactly as it came."
        )


@asynccontextmanager
async def _config_file(
    config: str, port: int, *, prefix: str, listener: Listener | None = None
) -> AsyncIterator[Path]:
    """A configuration written readable only by this person, removed on the way out."""
    # On Windows the mode is ignored; the per-user temp folder's access list does the work.
    # `prefix` stays on the client's command line, which is how Sift proves a client is its own.
    workspace = await asyncio.to_thread(tempfile.TemporaryDirectory, "", prefix)
    path = Path(workspace.name) / "tunnel.conf"
    try:
        sections = _http_section(port) + (
            _listener_sections(listener) if listener is not None else ""
        )
        await asyncio.to_thread(_write_private, path, config + sections)
        yield path
    finally:
        await asyncio.to_thread(workspace.cleanup)


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


class TunnelProcess:
    """One running client, ended only when its command line carries its own token."""

    def __init__(self, spec: TunnelSpec, *, owner: str | None = None) -> None:
        self._spec = spec
        #: The store passes the token all its tunnels share.
        self._owner = owner or new_id()
        self._process: LongLivedChild | None = None
        self._ports: ListenPorts | None = None
        #: Not DEVNULL, or a client that died on startup would leave no trace.
        self._client_log: Path | None = None
        self._client_output: IO[bytes] | None = None
        self._client_said = ""
        self._leases = 0
        self._draining = False
        self._drained = asyncio.Event()
        self._drained.set()
        #: Kept beside the run's ports: a new run can leave from another address.
        self._exit: tuple[ListenPorts, str] | None = None

    @property
    def proxy_url(self) -> str:
        """The proxy this run listens on, handed out by `lease` alone, so its port may change."""
        if self._ports is None:
            raise self._not_available()
        return f"http://127.0.0.1:{self._ports.proxy}"

    @property
    def name(self) -> str:
        """The name somebody gave this tunnel. What a download's row says it went out through."""
        return self._spec.name

    @property
    def draining(self) -> bool:
        return self._draining

    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self, config: str, *, listener: Listener | None = None) -> None:
        """Run the client and wait for the far end to answer; a failed start stops what it made."""
        if self.running():
            return
        # Before a port or a key: a removed client is said in its one shared sentence.
        lost = await client_fault()
        if lost is not None:
            raise TunnelClientLost(lost)
        try:
            for _attempt in range(_PORT_ATTEMPTS):
                if await self._start_on(config, _free_ports(), listener):
                    self._draining = False
                    log.info("tunnel.up", tunnel=self._spec.name)
                    return
        except BaseException:
            await self.stop_now()
            raise
        raise TunnelError(
            f"The tunnel {self._spec.name} didn't start. Each time, another program on this device "
            "took one of its ports before the tunnel could use it. Try turning it on again."
        )

    async def _start_on(
        self, config: str, chosen: ListenPorts, listener: Listener | None = None
    ) -> bool:
        """One attempt on one pair of ports; False when another program holds one of them."""
        self._ports = chosen
        # Off the loop: it makes a temp file and opens it.
        output = await asyncio.to_thread(self._open_client_log)
        async with _config_file(
            config, chosen.proxy, prefix=self._token(), listener=listener
        ) as path:
            try:
                child = await start_long_lived(
                    [_program(), "-c", str(path), "-i", f"127.0.0.1:{chosen.status}", "-s"],
                    # Not DEVNULL: the client explains itself here.
                    stderr=output,
                )
            except SubprocessError as exc:
                # A missing installation, said rather than escaping as a 500.
                raise _client_is_missing(exc) from exc
            self._process = child
            handshake = await self._await_handshake()
        # Running as well as handshaken, and the one listening: a dying client can answer.
        exited = not self.running()
        strangers: list[ports.PortHolder] = []
        if handshake is not None and not exited:
            strangers = await self._strangers_on(chosen, child.pid)
            if not strangers:
                return True
        await self.stop_now()
        if exited:
            # A client that cannot bind its port exits immediately; only then are holders asked.
            strangers = await self._strangers_on(chosen, child.pid)
        if strangers:
            for holder in strangers:
                await self._leave_or_end(holder)
            return False
        said = f" The client said: {self._client_said}" if self._client_said else ""
        raise TunnelError(
            f"The tunnel {self._spec.name} did not connect. Check that its configuration is "
            f"current and that the machine can reach the internet.{said}"
        )

    def _token(self) -> str:
        """The config folder's name start, on the client's command line: this store's mark."""
        return f"{_FOLDER_PREFIX}{self._owner}-"

    async def _strangers_on(self, chosen: ListenPorts, own_pid: int) -> list[ports.PortHolder]:
        """Whatever other than this run's client listens on its ports, asked of the system."""
        found = await asyncio.gather(ports.holder_of(chosen.proxy), ports.holder_of(chosen.status))
        others = {
            holder.pid: holder for holder in found if holder is not None and holder.pid != own_pid
        }
        return list(others.values())

    async def _leave_or_end(self, holder: ports.PortHolder) -> None:
        """End a client this owner lost track of; leave another Sift's or anything else alone."""
        if self._is_own_client(holder):
            log.warning("tunnel.orphan_ended", tunnel=self._spec.name, pid=holder.pid)
            await ports.end_process(holder.pid)
        elif _is_the_client_program(holder):
            log.warning("tunnel.foreign_client_left", tunnel=self._spec.name, pid=holder.pid)
        else:
            log.warning(
                "tunnel.port_taken", tunnel=self._spec.name, pid=holder.pid, program=holder.name
            )

    def _is_own_client(self, holder: ports.PortHolder) -> bool:
        """Whether a process is the shipped client with this store's token; ports prove nothing."""
        return _is_the_client_program(holder) and self._token() in holder.command_line

    def _not_available(self) -> TunnelError:
        return TunnelError(
            f"The tunnel {self._spec.name} is not available, so nothing was sent. "
            "Turn the tunnel on, or route this site directly."
        )

    def _open_client_log(self) -> IO[bytes]:
        """A file for the client's output while it runs, as a full pipe would stall the tunnel."""
        handle, name = tempfile.mkstemp(prefix="sift-tunnel-log-")
        os.close(handle)
        self._client_log = Path(name)
        self._client_output = self._client_log.open("wb")
        return self._client_output

    def _close_client_log(self) -> None:
        """Keep the last of what the client said, then take the file away."""
        if self._client_output is not None:
            with contextlib.suppress(OSError):
                self._client_output.close()
            self._client_output = None
        if self._client_log is not None:
            with contextlib.suppress(OSError):
                # The tail only: it reaches a person on a screen.
                self._client_said = self._client_log.read_text(errors="replace").strip()[-300:]
            with contextlib.suppress(OSError):
                # Sift's own scratch file in the temp directory, opened above.
                self._client_log.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash
            self._client_log = None

    async def _await_handshake(self) -> int | None:
        """The time the far end last answered, or None if it never did inside the budget."""
        deadline = time.monotonic() + TUNNEL_HANDSHAKE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not self.running():
                return None
            handshake = await self._read_handshake()
            if handshake:
                return handshake
            await asyncio.sleep(_POLL_INTERVAL)
        return None

    async def _read_metrics(self) -> tuple[int | None, str | None]:
        """When the far end last answered and which server; loopback, so not the guarded session."""
        if self._ports is None:
            return None, None
        url = f"http://127.0.0.1:{self._ports.status}/metrics"
        timeout = aiohttp.ClientTimeout(total=_HEALTH_TIMEOUT)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(url) as response,
            ):
                body = await response.text()
        except (aiohttp.ClientError, TimeoutError):
            return None, None
        handshake: int | None = None
        endpoint: str | None = None
        for line in body.splitlines():
            if line.startswith(_HANDSHAKE_FIELD):
                with contextlib.suppress(ValueError):
                    handshake = int(line.removeprefix(_HANDSHAKE_FIELD)) or None
            elif line.startswith(_ENDPOINT_FIELD):
                # The address alone; `rsplit` keeps an IPv6 endpoint's colons.
                endpoint = line.removeprefix(_ENDPOINT_FIELD).rsplit(":", 1)[0].strip() or None
        return handshake, endpoint

    async def _read_handshake(self) -> int | None:
        """Just the handshake, from the same one read, for the wait that decides a start."""
        return (await self._read_metrics())[0]

    async def server_address(self) -> str | None:
        """The server this tunnel is connected to now, read when a download takes it, or None."""
        return (await self._read_metrics())[1] if self.running() else None

    async def exit_address(self) -> str | None:
        """The public IPv4 address this tunnel leaves from, kept per run, never logged; or None."""
        chosen = self._ports
        if chosen is None or not self.running():
            return None
        if self._exit is not None and self._exit[0] is chosen:
            return self._exit[1]
        timeout = aiohttp.ClientTimeout(total=_EXIT_TIMEOUT)
        try:
            async with (
                await outbound_session(timeout=timeout) as session,
                session.get(EXIT_ECHO_URL, proxy=self.proxy_url) as response,
            ):
                if response.status != 200:
                    return None
                body = await response.content.read(_EXIT_MOST_BYTES)
        except (aiohttp.ClientError, TimeoutError, OSError):
            return None
        try:
            address = ipaddress.IPv4Address(body.decode("ascii").strip())
        except (UnicodeDecodeError, ValueError):
            return None
        if not address.is_global:
            return None
        if self._ports is chosen:
            self._exit = (chosen, str(address))
        return str(address)

    async def health(self) -> TunnelHealth:
        """What the tunnel is doing now. Up needs a recent handshake, not merely a live process."""
        handshake, endpoint = await self._read_metrics() if self.running() else (None, None)
        fresh = handshake is not None and (time.time() - handshake) < TUNNEL_STALE_HANDSHAKE_SECONDS
        return TunnelHealth(
            name=self._spec.name,
            running=self.running(),
            up=self.running() and fresh,
            draining=self._draining,
            last_handshake_at=handshake,
            endpoint=endpoint,
        )

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[str]:
        """Hold the tunnel for one download and yield its proxy; a draining tunnel refuses."""
        if self._draining or not self.running():
            raise self._not_available()
        self._leases += 1
        self._drained.clear()
        try:
            yield self.proxy_url
        finally:
            self._leases -= 1
            if self._leases == 0:
                self._drained.set()

    async def drain(self) -> None:
        """Stop taking new work, let what is running finish, then stop the client."""
        self._draining = True
        await self._drained.wait()
        await self.stop_now()

    async def stop_now(self) -> None:
        """Stop the client now, whatever rides it; terminate first, kill if it will not go."""
        self._draining = True
        process = self._process
        self._process = None
        self._ports = None
        if process is None or process.returncode is not None:
            if process is not None:
                # Already gone: reaped immediately, and its job let go.
                await process.wait()
            await asyncio.to_thread(self._close_client_log)
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await process.wait(time_limit=_STOP_GRACE_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        # Off the loop: it reads the file to keep the tail of what the client said.
        await asyncio.to_thread(self._close_client_log)
        log.info("tunnel.stopped", tunnel=self._spec.name)


__all__ = [
    "NATPMP_GATEWAY",
    "TUNNEL_BINARY",
    "ListenPorts",
    "Listener",
    "TunnelClientLost",
    "TunnelConfigInvalid",
    "TunnelError",
    "TunnelHealth",
    "TunnelProcess",
    "TunnelSpec",
    "validate_config",
]
