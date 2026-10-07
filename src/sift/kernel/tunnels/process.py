# SPDX-License-Identifier: AGPL-3.0-or-later
"""Named ways out to the internet, and the processes behind them.

A tunnel is a WireGuard configuration from a VPN provider, run by a userspace client that needs no
root, no network adapter and no driver install. The client exposes an ordinary HTTP proxy on a
loopback port, and that port is the whole of what the rest of Sift knows about it: a site routed
through a tunnel is fetched with that proxy set, by every one of the ways Sift reaches the network.

In the kernel rather than in the download slice because two features run through the same client:
downloads go OUT of it, and a swap is HOSTED on it: a listener inside the tunnel that the
VPN provider forwards one public port to (see `Listener`). One VPN key cannot run twice, so both
live in the one process, and the thing that owns that process cannot belong to either feature.

Three properties are the point of this module, and each is a way the feature can be worse than
useless if it is got wrong.

**Up means a handshake happened.** A process that started is not a tunnel that works: a wrong key,
a dead endpoint or a blocked port all leave the client running and answering. So a tunnel does not
read as up until the far end has actually replied, and it stops reading as up when the last reply
gets old.

**Turning one off drains rather than kills.** A transfer cannot be re-routed mid-stream, so stopping
a tunnel under a running download would either cut the download off or, far worse, leak the rest of
it out of another path. Disabling therefore stops new work from taking the tunnel and waits for what
is already using it. Stopping it outright is a separate, deliberate act for when the point is to
stop the traffic now.

**The configuration is a private key.** It is sealed in the secret store and only ever written to
disk while a tunnel is starting, into a file only this process can read, and removed once the client
has parsed it. That the client needs the key in a file at all is its interface; how long the file
exists is not.

Who owns what, where both sides could have an opinion: Sift owns which sites use which tunnel, and
when a tunnel starts and stops. The client owns the tunnel itself (the handshake, the rekeying,
the keepalive), because it is the side holding the socket. Sift asks it how it is doing and does
not second-guess the answer.
"""

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
from sift.kernel.ids import new_id
from sift.kernel.log import get_logger
from sift.kernel.subprocess import LongLivedChild, SubprocessError, start_long_lived
from sift.kernel.subprocess import run as run_once
from sift.kernel.tunnels.client import client_fault

log = get_logger(__name__)

#: The WireGuard client that carries a tunnel, found beside Sift's other vendored tools before it is
#: looked for on PATH, like every other program Sift ships. A bare name is right only where a
#: package puts it on PATH; on Windows nothing does, so starting a tunnel would raise
#: FileNotFoundError out of asyncio and reach the browser as "Something went wrong." See
#: scripts/vendor_manifest.json.
TUNNEL_BINARY = vendored_tool("wireproxy")

#: How long to wait for a tunnel's far end to answer before calling the tunnel dead. Generous: a
#: handshake is one round trip, but a machine that has just woken or a provider under load can take
#: several attempts, and the client retries on its own.
TUNNEL_HANDSHAKE_TIMEOUT_SECONDS = 20.0

#: How old the last answer may be before a tunnel stops reading as up. WireGuard rekeys every two
#: minutes under traffic and the keepalive is far shorter, so nothing older than this is a tunnel
#: still carrying anything.
TUNNEL_STALE_HANDSHAKE_SECONDS = 300.0

#: Where the VPN provider's port-forwarding service answers, INSIDE the tunnel: the gateway address
#: and the NAT-PMP port the protocol fixes (5351). It is the address the one provider Sift has
#: measured answers on through this client, and it is a fact about that provider rather than about
#: WireGuard: a provider that answers somewhere else simply never replies, and hosting then says the
#: tunnel cannot host rather than guessing.
NATPMP_GATEWAY = "10.2.0.1:5351"

_CONFIGTEST_TIMEOUT = 15.0
_HEALTH_TIMEOUT = 3.0
_POLL_INTERVAL = 0.5
#: How many pairs of ports one start tries before it gives up. A pair is free when the operating
#: system hands it over and is taken a moment later only if another program binds that exact port
#: in the few milliseconds before the client does, so a second pair is a remedy, and a third
#: failure says something on this device is taking ports as fast as they are freed.
_PORT_ATTEMPTS = 3
#: What every tunnel client's configuration folder is named from. The rest of the name is the
#: store's token and a random tail (see `TunnelProcess._token`).
_FOLDER_PREFIX = "sift-tunnel-"
#: How long a client gets to close its socket after being asked to stop, before it is killed.
_STOP_GRACE_SECONDS = 5.0
#: The client redacts its own keys before answering. Two fields are read out of what is left; the
#: rest (public keys, byte counters, allowed ranges) reaches no screen and no log.
_HANDSHAKE_FIELD = "last_handshake_time_sec="

#: The server this tunnel is talking to, as `address:port`. Only the address half is kept.
_ENDPOINT_FIELD = "endpoint="

#: Where a tunnel's exit is asked, through the tunnel's own proxy: an echo that answers with the
#: IPv4 address the request arrived from and nothing else. The provider's server is what it sees,
#: never this device.
EXIT_ECHO_URL = "https://api.ipify.org"
#: How long the echo is given, and the most of its answer read: an IPv4 address is 15 characters.
_EXIT_TIMEOUT = 5.0
_EXIT_MOST_BYTES = 64


class TunnelError(Exception):
    """A tunnel could not be started, or is not usable."""


class TunnelConfigInvalid(TunnelError):
    """The configuration is not one the client can read."""


class TunnelClientLost(TunnelError):
    """The tunnel program is gone from where the pack put it, or is not the file it shipped. The
    words are `client.CLIENT_REMOVED` or `client.CLIENT_CHANGED`, already logged once by the check,
    so a caller that logs its failures leaves this one out."""


#: What to say when the WireGuard client itself is not on the machine.
#:
#: A SENTENCE AND NOT A STACK TRACE. Sift launches the client as a process, and a process that is
#: not there raises FileNotFoundError from deep inside asyncio, which, uncaught, reaches the
#: browser as a 500 and the words "Something went wrong.". That is the least actionable thing a
#: screen can say about a fault with exactly one cause and one fix.
#:
#: AND IT NAMES THE ANTIVIRUS, because that is the other way the program goes. A tunnel client is
#: the kind of small network program a virus scanner can flag and take off a disk without
#: asking, and "the installation is incomplete" alone sends somebody looking for a fault in an
#: install that was complete the day it finished. Both causes have the one fix, so the sentence
#: names both and gives it once. Said with a semicolon and no dash: this is drawn on screen.
_NO_CLIENT = (
    "Sift cannot find the tunnel program it runs, so no tunnel can be started. It is shipped with "
    "Sift, so either the installation is incomplete or your antivirus removed it; installing Sift "
    "again over the top replaces it."
)


def _program() -> str:
    """The client as it is found at the moment it is run, rather than when Sift started: a program
    an antivirus took before the start and that somebody restored since is found again, with
    nothing restarted, which is what the sentence telling them to restore it promises."""
    return vendored_tool("wireproxy")


def _client_is_present() -> bool:
    """Whether the tunnel program is on the machine at all. Blocking, so it is called off the loop."""
    program = _program()
    return Path(program).is_file() or shutil.which(program) is not None


def _client_is_missing(exc: Exception) -> TunnelError:
    """Turn "the program is not there" into something a person can act on, and record which one.

    The operating system's own words are the cause the launch was raised from, when there is one.
    """
    log.warning("tunnel.client_missing", binary=TUNNEL_BINARY, detail=str(exc.__cause__ or exc))
    return TunnelError(_NO_CLIENT)


def _is_the_client_program(holder: ports.PortHolder) -> bool:
    """Whether a process is running the tunnel program Sift ships. By its program name, which says
    WHAT is running and nothing at all about whose it is."""
    return Path(holder.name).stem.lower() == Path(TUNNEL_BINARY).stem.lower()


@dataclass(frozen=True, slots=True)
class TunnelSpec:
    """What identifies one tunnel: its row and the name a person gave it.

    NO PORT. A fixed port from the database row, handed out from the same first number in every
    library, would give two Sifts on one device (a second install, a copy made for testing) the same
    port for their first tunnels, and each would take the other's client for its own leftover and
    end it. Ports are asked of the operating system each time the client starts (`ListenPorts`), and
    nothing needs them stable: a download is handed the proxy address when it takes the tunnel,
    never before.
    """

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ListenPorts:
    """The two loopback ports one run of a client listens on: the proxy, and its status beside it."""

    proxy: int
    status: int


def _free_ports() -> ListenPorts:
    """Two loopback ports nothing is listening on, chosen by the operating system.

    Both are held open until both are chosen, so the two cannot be the same port, and then let go
    for the client to bind. Between the letting go and the binding another program could take one;
    `TunnelProcess.start` proves which process is listening before it trusts either, and takes a
    fresh pair when it is not the client.
    """
    with socket.socket() as proxy, socket.socket() as status:
        proxy.bind(("127.0.0.1", 0))
        status.bind(("127.0.0.1", 0))
        return ListenPorts(proxy=proxy.getsockname()[1], status=status.getsockname()[1])


@dataclass(frozen=True, slots=True)
class TunnelHealth:
    """What a screen may know about a tunnel.

    Not the public key the client also reports: it names the provider account, it is the same for
    every install using that account, and nobody acts on it.

    The endpoint IS reported: "which
    server am I actually on" is the question somebody asks of a tunnel they are relying on, and it
    is the one thing that tells a working tunnel from a working tunnel to the wrong country. It is
    admin-only like everything else here, and the screen shows only the first part of it until it is
    asked to show the rest.
    """

    name: str
    running: bool
    up: bool
    draining: bool
    last_handshake_at: int | None
    #: The address of the server on the far side, without its port. None while nothing has answered.
    endpoint: str | None = None


def _http_section(port: int) -> str:
    """The proxy listener appended to a provider's configuration. Loopback only: a tunnel bound to
    anything else is an open proxy on the network the machine sits on."""
    return f"\n[http]\nBindAddress = 127.0.0.1:{port}\n"


def _is_a_port(value: object) -> bool:
    """A real port number: an int (never a bool, never a string) from 1 to 65535."""
    return type(value) is int and 0 < value < 65536


@dataclass(frozen=True, slots=True)
class Listener:
    """What a tunnel needs to HOST a swap: a port inside the tunnel, and a way to ask for it.

    Two sections, both Sift's, appended after its own `[http]` only while a swap is being hosted and
    gone with the process that carried them:

    - `[TCPServerTunnel]` listens on `internal_port` INSIDE the tunnel (in the client's own
      network stack, never on this device's), and hands each connection to `127.0.0.1:target_port`,
      the swap's own listener on loopback. The provider forwards one public port to that inside
      port, which is how a connection reaches Sift with nothing opened on the home router.
    - `[UDPProxyTunnel]` binds `127.0.0.1:natpmp_port` on loopback and carries what is sent there to
      the provider's port-forwarding service inside the tunnel (`NATPMP_GATEWAY`), which is how Sift
      asks for that public port (see `natpmp`).

    Every field is checked to be a port number, because each is written into the client's
    configuration as text: a value that was anything else could add a line, and a line is a section.
    """

    internal_port: int
    natpmp_port: int
    target_port: int

    def __post_init__(self) -> None:
        for field in ("internal_port", "natpmp_port", "target_port"):
            if not _is_a_port(getattr(self, field)):
                raise ValueError(f"{field} is not a port number")


def _listener_sections(listener: Listener) -> str:
    """The two sections a hosting tunnel carries after its `[http]`, exactly as the client reads
    them. Both bind loopback on this device; the only thing reachable from outside is the port the
    provider forwards into the tunnel, and that reaches nothing but `target_port`."""
    return (
        f"\n[TCPServerTunnel]\nListenPort = {listener.internal_port}\n"
        f"Target = 127.0.0.1:{listener.target_port}\n"
        f"\n[UDPProxyTunnel]\nBindAddress = 127.0.0.1:{listener.natpmp_port}\n"
        f"Target = {NATPMP_GATEWAY}\n"
    )


#: The lines a WireGuard configuration cannot do without, and what to say when one is absent.
#:
#: Checked before the client is asked, for one reason: the client answers "this is not a valid
#: config" to every one of these, which is true and useless. The commonest way to arrive here is
#: half a file (part of it selected, part of it pasted), and knowing WHICH half is missing is the
#: difference between fixing it and trying the same thing again.
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


#: The sections that open a LISTENER, which a provider's configuration has no business carrying.
#:
#: Sift appends its own `[http]` bound to loopback, and the comment on `_http_section` says why:
#: a tunnel bound to anything else is an open proxy on the network the machine sits on. That is a
#: promise about the file Sift writes, and the pasted half must keep it too: a configuration
#: carrying `[socks5]` or its own `[http]` on `0.0.0.0` would produce a listener nobody here chose
#: the address for, reachable by anything on the LAN and forwarding through somebody's VPN account.
#:
#: A tunnel hosting a swap carries two of these sections too (`[TCPServerTunnel]` and
#: `[UDPProxyTunnel]`), and that changes nothing here. Those are SIFT'S, written by
#: `_listener_sections` with loopback addresses Sift chose, after the pasted half has been checked;
#: the same two arriving in a pasted file are still somebody else's listener and still refused.
#:
#: Refused rather than rewritten. Editing somebody's configuration to remove a section is a change
#: they did not make and cannot see, and the honest answer to "this file does more than connect" is
#: to say so.
#:
#: **THE NAMES ARE READ OUT OF THE VENDORED BINARY, not guessed.** A guessed list can name sections
#: the client has never had and miss ones it does (`[udpproxytunnel]`, `[stdiotunnel]`, `[sni]`),
#: and a pasted file carrying a missed one on `0.0.0.0` opens exactly the listener this exists to
#: refuse. `tests/gates/test_tunnel_sections_match_the_client.py` reads them again on every run: a
#: client that gains a section a later build has not heard of turns that gate red rather than
#: letting the section through.
#:
#: The seven are every section the client understands that is not one of the three describing the
#: link itself (`[interface]`, `[peer]`, `[resolve]`: the gate says why each of those
#: three opens nothing). `[stdiotunnel]` binds no port and is refused all the same: it hands the
#: client's own standard input and output to a TCP target through the tunnel, and this process's
#: standard output is the client log Sift reads, so a file carrying one both moves data Sift did
#: not send and corrupts the only record of what the client said.
#: A `WGConfig = <path>` line makes the client read a SECOND file from the disk as the WireGuard
#: half of the configuration: any file the process can open, named by whoever pasted it. A
#: configuration is the text that was pasted and nothing on the disk beside it.
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


#: A key line, and what it was given. Both key fields, because the same thing goes wrong with both.
_KEY_LINE = re.compile(
    r"^[ \t]*(PrivateKey|PublicKey)[ \t]*=[ \t]*(\S*)", re.IGNORECASE | re.MULTILINE
)

#: A value that is a placeholder rather than a key: asterisks, dots, or the word for what was done
#: to it. Providers show the private key hidden on their website and reveal it on a click, so text
#: copied off that page carries the mask instead of the key, and every character of it is wrong in
#: a way that reads, at a glance, exactly like a key.
_MASKED = re.compile(r"^[*.x_-]{3,}$|hidden|redact|your.?key", re.IGNORECASE)

#: What a WireGuard key is: 32 bytes, written base64. Always 44 characters ending in one '='.
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _check_no_listeners(flattened: str, config: str) -> None:
    """Refuse a configuration that carries a service of its own.

    A provider's WireGuard file describes one thing: how to reach their server. A section that opens
    a listener describes something else (a proxy on this machine), and Sift adds exactly one of
    those itself, on loopback, deliberately. A second one from the file is a port Sift did not
    choose the address for, so `BindAddress = 0.0.0.0` in a pasted file is an open proxy on the
    network, offering anybody on it a way out through the account paying for the tunnel.

    The sentence says "does more than connect" rather than "opens a port", because one of the seven
    does not open one: `[stdiotunnel]` sends the client's own standard input and output through the
    tunnel instead. Refused with the rest, and a refusal that named a port it had not found would be
    a sentence somebody could go and check and find false.

    Matched at the start of a line so a section name inside a comment or a value is not mistaken for
    a section. The section names reach the log; no value from the file does.
    """
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
    """Refuse a key that is not one, and say which way it is not one.

    The client's answer to all of these is `invalid base64 string`, which names the field it was
    reading and not what to do about it. The masked case is worth telling apart because it is not a
    typo: the file is exactly as it was copied, and what is wrong happened on the website it was
    copied from.
    """
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
    """What a configuration CONTAINS, with nothing of what it says.

    Section names, the field names present, and how many lines there are. No value is read and none
    is recorded: the file is a private key, and this exists to make a refusal diagnosable rather
    than to inspect anybody's provider account.
    """
    lines = [line.strip() for line in config.splitlines() if line.strip()]
    sections = [line.lower() for line in lines if line.startswith("[")]
    fields = sorted(
        {line.split("=", 1)[0].strip().lower() for line in lines if "=" in line and line[0] != "#"}
    )
    return {"lines": len(lines), "sections": sections, "fields": fields, "bytes": len(config)}


async def validate_config(config: str, *, port: int = 9000) -> None:
    """Refuse a configuration the client cannot read, before it is ever stored.

    The client's own check is the authority rather than a parser written here: it is the thing that
    has to accept the file, and a second opinion about the format is a second thing to keep correct.
    What happens first is only a check for the parts that are missing outright, so the refusal can
    say which one. It reads a file, so one is written and removed; the check never opens a socket.
    """
    flattened = config.lower()
    for needle, sentence in _MUST_CONTAIN:
        if needle not in flattened:
            log.warning("tunnel.config_incomplete", missing=needle, **_shape_of(config))
            raise TunnelConfigInvalid(sentence)
    _check_no_listeners(flattened, config)
    _check_the_keys(config)
    # The program first: a configuration cannot be checked by a program that is not there, and
    # "the tunnel could not be checked" would send somebody to the file rather than to the scanner.
    lost = await client_fault()
    if lost is not None:
        raise TunnelConfigInvalid(lost)

    async with _config_file(config, port, prefix=_FOLDER_PREFIX) as path:
        try:
            result = await run_once(
                [_program(), "--configtest", "-c", str(path)], time_limit=_CONFIGTEST_TIMEOUT
            )
        except SubprocessError as exc:
            # Two different faults arrive here and they have different answers. `run_once` turns a
            # program that will not start into this same exception as one that started and hung, so
            # the two are told apart by asking whether the program is there at all, and a missing
            # one is not something "try saving it again" can ever fix.
            if not await asyncio.to_thread(_client_is_present):
                raise TunnelConfigInvalid(_NO_CLIENT) from exc
            raise TunnelConfigInvalid(
                "The tunnel could not be checked. Try saving it again."
            ) from exc
    if result.returncode != 0:
        # The client's own words, recorded and never shown. They name the line it choked on, which
        # is what makes this answerable at all; without them the only way to find out is to
        # guess.
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
    """A configuration written where only this person can read it, removed on the way out.

    What is written is the pasted half, then Sift's own `[http]`, then (only while a swap is being
    hosted) the listener's two sections (`_listener_sections`). Sift's sections always come AFTER
    the pasted half, which has already been refused if it carried one of its own.

    Created inside the caller's own temporary directory with the mode set as the file is made rather
    than after: a file created readable and narrowed a moment later is readable in that moment.

    ON WINDOWS THE MODE IS ACCEPTED AND IGNORED, and the sentence above is carried by something
    else: a per-user temporary directory whose access list already admits only its owner. The
    property holds on both, by different means, which is worth saying, because the code reads as
    though one line were doing all the work and on Windows it does none.

    The folder's name starts with `prefix`, and for a running client that is not decoration: the
    path is on the client's command line for as long as it runs, so the prefix is how Sift can later
    prove a client is its own (see `TunnelProcess._token`). The file inside is gone within seconds;
    the name on the command line stays.
    """
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
    """One running client: its process, the two ports it listens on, and how it says it is doing.

    **Sift ends a tunnel client only when it can prove it started it.** `owner` names whoever keeps
    this tunnel (the tunnel store passes one token for every tunnel it runs, minted fresh each
    time Sift starts), and it is written into the client's command line (see `_token`). No other
    process has that value, so a client carrying it is this store's beyond argument, and one without
    it is somebody else's however much it looks like Sift's. A rule matching on the port would not
    do: two Sifts on one device can be handed the same port, and each would take the other's client
    for its own leftover and end it.
    """

    def __init__(self, spec: TunnelSpec, *, owner: str | None = None) -> None:
        self._spec = spec
        #: A tunnel made on its own owns itself; the store passes the token all of its tunnels
        #: share.
        self._owner = owner or new_id()
        self._process: LongLivedChild | None = None
        #: The ports this run of the client listens on. None whenever no client is running.
        self._ports: ListenPorts | None = None
        #: Where the client's own complaints go while it runs, and the last of them once it has
        #: stopped. Not DEVNULL, or a client that died on startup would leave no trace anywhere at
        #: all (see `start`).
        self._client_log: Path | None = None
        self._client_output: IO[bytes] | None = None
        self._client_said = ""
        self._leases = 0
        self._draining = False
        self._drained = asyncio.Event()
        self._drained.set()
        #: The exit address this run of the client was read to leave from, beside the ports of
        #: the run it was read on: a new run can leave from another address.
        self._exit: tuple[ListenPorts, str] | None = None

    @property
    def proxy_url(self) -> str:
        """The proxy this run of the client listens on.

        Handed out by `lease` and by nothing else, so nothing holds it across a restart of the
        client, which is what lets the port be a new one every time.
        """
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
        """Run the client and wait for the far end to answer.

        With a `listener`, the client also hosts: it carries the two sections that let a swap reach
        this device through the tunnel (see `Listener`). Without one it is a way out and nothing
        else. Which of the two a running client is was decided when it started, so changing it is a
        restart: `TunnelStore.host_on` and `stop_hosting` are the two that do that.

        Returning without a handshake would leave a control reading as up over a tunnel carrying
        nothing, which is the one thing this must not do: a site routed through it would then be
        refused with no explanation, or (if the refusal were ever softened) go out unprotected.

        Each attempt takes a fresh pair of ports from the operating system (`_free_ports`), and a
        pair another program turned out to hold is given up for the next one rather than fought
        over. Whatever stops a start part-way (a failure, or the start being cancelled) stops
        the client it made, so no start leaves a client running that nothing is tracking.
        """
        if self.running():
            return
        # Before a port is taken or a key written to disk: a program an antivirus removed or
        # altered is said in the one sentence every tunnel row and download shares, never as a
        # tunnel that "did not connect".
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
        """One attempt, on one pair of ports. True when the tunnel is up on them.

        False when another program turned out to be holding one of them, which a fresh pair can
        fix. Every other failure raises, because a fresh pair would not change it.
        """
        self._ports = chosen
        # Off the loop: it makes a temp file and opens it, and a `stat` on a temp directory
        # that has gone away is the kind of wait nothing else here may be made to share.
        output = await asyncio.to_thread(self._open_client_log)
        async with _config_file(
            config, chosen.proxy, prefix=self._token(), listener=listener
        ) as path:
            try:
                child = await start_long_lived(
                    [_program(), "-c", str(path), "-i", f"127.0.0.1:{chosen.status}", "-s"],
                    # NOT DEVNULL. The client explains itself here, and throwing that away would
                    # leave a client that died on the first second nothing behind to read.
                    stderr=output,
                )
            except SubprocessError as exc:
                # The one failure that is about the INSTALLATION rather than about the tunnel; said,
                # not left to escape as a 500.
                raise _client_is_missing(exc) from exc
            self._process = child
            handshake = await self._await_handshake()
        # `running()` as well as the handshake, and the order matters. A handshake can be read from
        # a client that is already on its way out (or, when the client could not bind its status
        # port, from whatever did), so a tunnel is not up until the process that is meant to be
        # carrying it is still there when the answer arrives, AND is the one listening.
        exited = not self.running()
        strangers: list[ports.PortHolder] = []
        if handshake is not None and not exited:
            strangers = await self._strangers_on(chosen, child.pid)
            if not strangers:
                return True
        await self.stop_now()
        if exited:
            # The client refuses to run on a port it cannot bind and leaves within milliseconds
            # (the vendored build exits 1 or 2 in about 20 ms). So an early exit is
            # the one case worth asking who holds the ports; a client that ran the whole budget
            # without an answer held both and simply never connected.
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
        """What this store's clients carry on their command line, and nobody else's do.

        The start of the name of the folder the configuration is written into, whose path is an
        argument to the client, so it needs no argument of its own, which the client would refuse.
        """
        return f"{_FOLDER_PREFIX}{self._owner}-"

    async def _strangers_on(self, chosen: ListenPorts, own_pid: int) -> list[ports.PortHolder]:
        """Whatever other than this run's client is listening on the ports it was given.

        Asked of the operating system rather than inferred, because it is the only proof there is
        that the proxy a download will be handed and the status that says "up" both belong to this
        client. A lookup that cannot tell (None) names no stranger: the client is running and
        answered, which is all that was trusted before this could be asked, and the lookup logs
        that it could not say.
        """
        found = await asyncio.gather(ports.holder_of(chosen.proxy), ports.holder_of(chosen.status))
        others = {
            holder.pid: holder for holder in found if holder is not None and holder.pid != own_pid
        }
        return list(others.values())

    async def _leave_or_end(self, holder: ports.PortHolder) -> None:
        """End a client this owner started and lost track of; leave anything else exactly alone.

        A client with another store's token is another Sift's (a second install, a copy made for
        testing), and ending it would cut that Sift's downloads off mid-transfer. It is named in
        the log by its process id and never by its command line,
        which carries a path into somebody's temporary folder.
        """
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
        """Whether a process is a client this store started: the program Sift ships, carrying this
        store's token. The port it holds is not evidence of anything: two Sifts on one device can
        be handed the same one."""
        return _is_the_client_program(holder) and self._token() in holder.command_line

    def _not_available(self) -> TunnelError:
        return TunnelError(
            f"The tunnel {self._spec.name} is not available, so nothing was sent. "
            "Turn the tunnel on, or route this site directly."
        )

    def _open_client_log(self) -> IO[bytes]:
        """A file for the client's own output, for as long as it runs. Answers the open file.

        A file rather than a pipe nobody drains: a pipe fills, and a client blocked writing to a
        full pipe is a tunnel that stops carrying traffic for a reason nothing would ever explain.
        """
        handle, name = tempfile.mkstemp(prefix="sift-tunnel-log-")
        os.close(handle)
        self._client_log = Path(name)
        self._client_output = self._client_log.open("wb")
        return self._client_output

    def _close_client_log(self) -> None:
        """Keep the last of what the client said, then take the file away.

        Kept rather than read on demand, because the only moment it can be read safely is after the
        writer has gone, and by then the caller that needs the words is one frame further on.
        """
        if self._client_output is not None:
            with contextlib.suppress(OSError):
                self._client_output.close()
            self._client_output = None
        if self._client_log is not None:
            with contextlib.suppress(OSError):
                # The tail only. This reaches a person on a screen, and a wall of client output is
                # not a sentence; the whole of it is a debug log away.
                self._client_said = self._client_log.read_text(errors="replace").strip()[-300:]
            with contextlib.suppress(OSError):
                # Sift's OWN scratch file, in the temp directory, opened by `_open_client_log` a few
                # seconds ago and read one line above. The rule this waives is about files somebody
                # else put in a library folder; there is nothing here to undo, and no folder to
                # check was handed over read-write.
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
        """When the far end last answered, and which server answered. Both None while none has.

        This is the one request in the slice that must NOT go through the guarded session: the guard
        refuses a private address, and this address is deliberately loopback, in this machine, to a
        process Sift started itself.
        """
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
                # The port is the provider's and says nothing anybody acts on; the address is the
                # whole of what "which server am I on" means. `rsplit` rather than `split`, so an
                # IPv6 endpoint keeps its colons and loses only the port.
                endpoint = line.removeprefix(_ENDPOINT_FIELD).rsplit(":", 1)[0].strip() or None
        return handshake, endpoint

    async def _read_handshake(self) -> int | None:
        """Just the handshake, for the wait that decides whether a tunnel came up.

        The same one read: starting a tunnel asks this many times a second and has no use for the
        server address, so it takes the half it wants rather than a second request for both.
        """
        return (await self._read_metrics())[0]

    async def server_address(self) -> str | None:
        """The address of the server this tunnel is connected to now, or None if it has not said.

        Asked when a download takes the tunnel, so the row can keep the value it had THEN: the
        provider can move a tunnel to another server between one download and the next, and a row
        that looked the address up when it was drawn would rewrite where an old download went.
        The same reading `health` gives Settings, so the two screens cannot show different things
        for the same moment.
        """
        return (await self._read_metrics())[1] if self.running() else None

    async def exit_address(self) -> str | None:
        """The public IPv4 address this tunnel's traffic leaves from, or None when it cannot be
        read: the client not running, the echo not answering, or an answer that is not one.

        Asked once through the tunnel's own proxy and kept for as long as this run of the client
        lasts. It is the VPN server's address, never this device's, and it goes to the caller
        and nowhere else: not a log line, not a row.
        """
        chosen = self._ports
        if chosen is None or not self.running():
            return None
        if self._exit is not None and self._exit[0] is chosen:
            return self._exit[1]
        timeout = aiohttp.ClientTimeout(total=_EXIT_TIMEOUT)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
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
        """Hold the tunnel for the length of one download, and yield the proxy to use.

        A tunnel that is draining refuses. That is what makes turning one off safe: the transfers
        already using it finish on it, and the next one is told the tunnel is unavailable rather
        than being quietly sent out some other way.
        """
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
        """Stop the client immediately, whatever is using it.

        The transfers riding it fail, which is the point: this is the control for when the reason to
        stop is the traffic itself. Terminate first and kill only if it will not go, so the client
        gets the chance to close its socket.
        """
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
        # Off the loop for the same reason as the open, and one more: it READS the file to keep
        # the tail of what the client said, so its cost is the size of whatever the client wrote.
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
