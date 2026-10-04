# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnels somebody has set up, the routes sites take, and the processes behind both.

Three things that belong together because each is useless without the others. The store holds what
was configured: a name, a sealed configuration, and whether it is meant to be running. The routes
say which sites use which tunnel. The manager is what turns the first into actual processes and what
the second hands a download.

And a fourth, which is why this lives in the kernel: a tunnel can HOST a swap (`host_on`). The
client that carries downloads out is the one that must carry the swap in (one VPN key cannot run
twice), so hosting restarts that client with a listener and stopping restarts it without one. The
downloads routed through the tunnel pause for each restart rather than failing or leaving another
way: the restart drains what is running, and anything that asks for the tunnel meanwhile waits for
it (`ensure_started`).

Two rules shape all of it.

**A configuration is a private key, so it is sealed and only opened with the key that unwraps it.**
That key exists only while somebody is signed in, which means a tunnel cannot start before then.
That is not a limitation to work around: a download routed through a tunnel that cannot start
refuses rather than leaving by another path, so the worst case is a queue that waits.

**What is stored is the intent, never the state.** Whether a process is running is a fact about the
machine right now, and a stored copy of it is wrong the moment the process stops. So `enabled` means
"this should be running" and everything else is asked of the process itself.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.log import get_logger
from sift.kernel.secret_store import SecretStore
from sift.kernel.tunnels.client import client_fault
from sift.kernel.tunnels.egress import DIRECT
from sift.kernel.tunnels.natpmp import Mapped, NatPmp, NatPmpError, Renewal
from sift.kernel.tunnels.process import (
    Listener,
    TunnelClientLost,
    TunnelError,
    TunnelHealth,
    TunnelProcess,
    TunnelSpec,
    validate_config,
)
from sift.kernel.wiring import Part

log = get_logger(__name__)

#: Reads an admin's master key, or None when nobody has signed in since the process started. The
#: same seam the download job uses to open a saved login, and for the same reason: a tunnel's
#: configuration is sealed under it, so nothing can start before somebody signs in.
KeyReader = Callable[[], Awaitable[bytes | None]]


async def _no_key() -> bytes | None:
    return None


#: The scope of the route every site follows unless it has one of its own. Not a site key, and it
#: cannot become one: site keys are plain words, which a test holds them to.
DEFAULT_SCOPE = "*"

# No port anywhere in these. A tunnel's ports are the operating system's, asked for each time its
# client starts (`process.ListenPorts`). A stored port would be the same number in every library,
# and two Sifts on one device would end each other's clients; the download schema's step at
# version 29 drops the column.
_INSERT = (
    "INSERT INTO tunnels (id, name, secret_id, enabled, created_at, updated_at)"
    " VALUES (?, ?, ?, 0, ?, ?)"
)
_LIST = "SELECT id, name, secret_id, enabled, can_host FROM tunnels ORDER BY name COLLATE NOCASE"
_GET = "SELECT id, name, secret_id, enabled, can_host FROM tunnels WHERE id = ?"
_RENAME = "UPDATE tunnels SET name = ?, updated_at = ? WHERE id = ?"
_REPLACE_CONFIG = "UPDATE tunnels SET secret_id = ?, updated_at = ? WHERE id = ?"
_SET_ENABLED = "UPDATE tunnels SET enabled = ?, updated_at = ? WHERE id = ?"
_SET_CAN_HOST = "UPDATE tunnels SET can_host = ?, updated_at = ? WHERE id = ?"
_DELETE = "DELETE FROM tunnels WHERE id = ?"

_GET_ROUTE = "SELECT route FROM tunnel_routes WHERE scope = ?"
_LIST_ROUTES = "SELECT scope, route FROM tunnel_routes"
_SET_ROUTE = (
    "INSERT INTO tunnel_routes (scope, route, updated_at) VALUES (?, ?, ?)"
    " ON CONFLICT(scope) DO UPDATE SET route = excluded.route, updated_at = excluded.updated_at"
)
_CLEAR_ROUTE = "DELETE FROM tunnel_routes WHERE scope = ?"

#: What a swap is told when the provider gives the tunnel no port: the screen's own words.
#:
#: The one fault with one cause and one fix: a provider forwards a port only from a server that
#: offers it, to a configuration made with forwarding turned on. So it names both, and names nothing
#: about the tunnel's address, which a screen has no use for.
CANNOT_HOST = (
    "This tunnel's provider didn't give it a port. A swap needs a tunnel made on a P2P VPN server with "
    "port forwarding on."
)

#: The range an inside port is drawn from. INSIDE THE TUNNEL: the client's own network stack, not
#: this device's, so nothing else on the device can be holding it and nothing needs asking. The
#: provider forwards its public port to whichever one is asked for, and honours the one it is given.
_INSIDE_PORTS = range(20000, 60000)


@dataclass(frozen=True, slots=True)
class TunnelView:
    """One tunnel as a screen sees it. Never the configuration, and never the public key: that
    names the provider account and nobody acts on it.

    The exit address is carried: it is what tells a working tunnel from a working tunnel to the
    wrong country, which is the question somebody actually asks of one."""

    id: str
    name: str
    enabled: bool
    running: bool
    up: bool
    draining: bool
    last_handshake_at: int | None
    #: The server on the far side, without its port. None while nothing has answered.
    endpoint: str | None = None
    #: Why the last attempt to start it failed, in words, or None: what a bare "Not connecting"
    #: would hide: a port held by another program, a configuration the client refused, a client that
    #: never connected. Cleared by the next start that succeeds.
    problem: str | None = None
    #: Whether this tunnel can host a swap: True or False as last measured, None until somebody has
    #: tried. See `TunnelStore.can_host`.
    can_host: bool | None = None


@dataclass(frozen=True, slots=True)
class Hosting:
    """Where a swap can reach this device, as the provider gave it.

    The public address is the VPN server's, never this device's, and it and the port go to the swap
    session and nowhere else: not a log line, not a row. The inside port and the port Sift asks the
    provider through are carried because the session holds them for as long as it runs.
    """

    public_ipv4: str
    external_port: int
    internal_port: int
    natpmp_port: int


#: Told the new `Hosting` when the provider moves the public port at a renewal. A token already
#: handed out names the old one, so the session is the thing that has to know.
HostingMoved = Callable[[Hosting], Awaitable[None]]

#: Told why when the hosting ends on its own: a renewal the provider refused, or the tunnel turned
#: off underneath it. Not called for `stop_hosting`: the caller of that already knows.
HostingLost = Callable[[TunnelError], Awaitable[None]]


@dataclass(slots=True)
class _Hosted:
    """One hosting in progress: what was handed out, what keeps it, and how to put things back."""

    spec: TunnelSpec
    hosting: Hosting
    renewal: Renewal
    #: Whether the tunnel was carrying downloads when the hosting began. Stopping puts it back the
    #: way it was: restarted plain if it was running, stopped if it was not.
    was_running: bool
    #: The key the configuration was opened with, so stopping can open it again for the plain
    #: restart. The key, never the opened configuration: the private key it seals is not kept in
    #: this process for the length of a swap.
    master_key: bytes
    on_moved: HostingMoved | None
    on_lost: HostingLost | None


def _inside_port() -> int:
    """A port for the listener inside the tunnel. See `_INSIDE_PORTS`."""
    return _INSIDE_PORTS.start + secrets.randbelow(len(_INSIDE_PORTS))


def _free_udp_port() -> int:
    """A loopback UDP port the operating system says is free, for the client to ask NAT-PMP from.

    Let go before the client binds it, like the proxy's pair (`process._free_ports`). A program
    that took it in the gap would receive what Sift asks the provider, and the worst it could
    answer is a wrong port, which a guest then fails to complete the key handshake on.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


#: What a tunnel hosting a swap answers a switch, a removal or a new configuration with.
END_THE_SWAP_FIRST = "End the swap first."


class TunnelHosting(TunnelError):
    """A tunnel hosting a swap was asked to stop. The words are `END_THE_SWAP_FIRST`."""


class TunnelStore:
    """The tunnels table and the routes table, and the processes they describe."""

    def __init__(
        self, database: Database, secrets: SecretStore, *, read_key: KeyReader = _no_key
    ) -> None:
        self._db = database
        self._secrets = secrets
        self._read_key = read_key
        self._processes: dict[str, TunnelProcess] = {}
        #: The last start failure per tunnel, for the screen. See `TunnelView.problem`.
        self._problems: dict[str, str] = {}
        #: What every client this store starts carries on its command line, and nothing else has.
        #: Minted here, once per run, so it is this store's and no other Sift's, not even one
        #: opened on a copy of this same library. See `TunnelProcess`.
        self._owner = new_id()
        #: The tunnels hosting a swap now, by id.
        self._hosted: dict[str, _Hosted] = {}
        #: A restart in progress, by tunnel id, set when it is over. What a download asking for the
        #: tunnel meanwhile waits on: the pause, rather than a refusal.
        self._restarts: dict[str, asyncio.Event] = {}
        #: One hosting change at a time per tunnel: starting, stopping and a lost renewal.
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def processes(self) -> dict[str, TunnelProcess]:
        """The live processes, by tunnel id. The router reads this and nothing else: a tunnel that
        is not in here is one a routed download refuses over, which is the intended answer."""
        return self._processes

    # --- what is configured ---------------------------------------------------------------

    async def add(self, *, name: str, config: str, master_key: bytes) -> str:
        """Check a configuration, seal it, and record the tunnel. Returns its id.

        Checked before it is stored, with the client's own parser, so a file that was never going to
        work is refused here (where somebody is looking at the form they just filled in) rather
        than at the next download from a site they routed through it days later.
        """
        await validate_config(config)
        secret_id = await self._secrets.seal(config.encode("utf-8"), master_key)
        now = int(time.time())
        tunnel_id = new_id()
        await self._db.execute(_INSERT, (tunnel_id, name, secret_id, now, now))
        return tunnel_id

    async def rename(self, tunnel_id: str, name: str) -> None:
        await self._db.execute(_RENAME, (name, int(time.time()), tunnel_id))

    async def replace_config(self, tunnel_id: str, *, config: str, master_key: bytes) -> None:
        """Swap in a new configuration and restart the tunnel if it was running.

        A provider's config expires and gets reissued, and the old sealed one is forgotten rather
        than left behind. Restarting is not optional: the running process is still holding the old
        key, so a tunnel left alone would keep using the configuration that was just replaced.
        """
        await validate_config(config)
        row = await self._db.fetch_one(_GET, (tunnel_id,))
        if row is None:
            raise TunnelError("That tunnel is not there any more.")
        secret_id = await self._secrets.seal(config.encode("utf-8"), master_key)
        await self._db.execute(_REPLACE_CONFIG, (secret_id, int(time.time()), tunnel_id))
        if row["secret_id"] is not None:
            await self._secrets.forget(str(row["secret_id"]))
        if tunnel_id in self._processes:
            await self.stop(tunnel_id, drain=True)
            await self.start(tunnel_id, master_key)

    async def remove(self, tunnel_id: str) -> None:
        """Stop a tunnel and forget it, along with its sealed configuration.

        Sites routed through it are deliberately left pointing at it. Their downloads then refuse
        and say the tunnel is gone, which is a thing somebody can act on, where quietly moving
        them to the machine's own address would send traffic out of the one place they were routed
        away from, and nothing would say so.
        """
        row = await self._db.fetch_one(_GET, (tunnel_id,))
        if row is None:
            return
        await self.stop(tunnel_id, drain=False)
        await self._db.execute(_DELETE, (tunnel_id,))
        if row["secret_id"] is not None:
            await self._secrets.forget(str(row["secret_id"]))

    async def list(self) -> list[TunnelView]:
        """Every configured tunnel, with what its process is actually doing folded in.

        A tunnel program an antivirus removed or altered is every row's problem, whatever each
        row's last start said: it is the one fault no row can be fixed without, and it is asked of
        the file on every read, so a program restored from quarantine clears every row at once.
        """
        lost = await client_fault()
        views: list[TunnelView] = []
        for row in await self._db.fetch_all(_LIST):
            process = self._processes.get(str(row["id"]))
            health = await process.health() if process is not None else None
            views.append(_view(row, health, lost or self._problems.get(str(row["id"]))))
        return views

    async def why_down(self, tunnel_id: str) -> str | None:
        """Why a configured tunnel is not running, as a download refused over it says; None when
        no tunnel has this id.

        Read after the download has asked for it to be started (`ensure_started`), so a start
        that failed has left its own words here, and those are the reason given: a download over a
        tunnel whose key or configuration is at fault is told that, never that the tunnel is gone.
        """
        row = await self._db.fetch_one(_GET, (tunnel_id,))
        if row is None:
            return None
        name = str(row["name"])
        if not row["enabled"]:
            return (
                f"The tunnel {name} is turned off, so nothing was sent. Turn it on, or route this "
                "directly."
            )
        problem = self._problems.get(tunnel_id)
        if problem is not None:
            return f"The tunnel {name} couldn't start, so nothing was sent. {problem}"
        return f"The tunnel {name} can't start until an admin signs in, so nothing was sent."

    async def check_client(self) -> str | None:
        """Look at the tunnel program once at start-up, so a program removed while Sift was not
        running is in the log before anything asks for a tunnel. The sentence, or None."""
        return await client_fault()

    # --- turning them on and off ------------------------------------------------------------

    async def _open(self, tunnel_id: str, master_key: bytes) -> tuple[TunnelSpec, str]:
        """The tunnel's name and its opened configuration, or a refusal that says which is missing."""
        row = await self._db.fetch_one(_GET, (tunnel_id,))
        if row is None or row["secret_id"] is None:
            raise TunnelError("That tunnel has no configuration saved for it.")
        config = await self._secrets.open(str(row["secret_id"]), master_key)
        if config is None:
            raise TunnelError(
                f"The configuration for {row['name']} could not be read. Import it again."
            )
        return TunnelSpec(id=str(row["id"]), name=str(row["name"])), config.decode(
            "utf-8", "replace"
        )

    async def start(self, tunnel_id: str, master_key: bytes) -> None:
        """Open the tunnel's configuration and run it. Marks it as meant to be running."""
        # Never beside a restart: two starts of one client at once is two clients.
        await self._after_any_restart(tunnel_id)
        spec, config = await self._open(tunnel_id, master_key)
        process = self._processes.setdefault(spec.id, TunnelProcess(spec, owner=self._owner))
        try:
            await process.start(config)
        except TunnelError as exc:
            # A tunnel that did not come up must not be left registered. The router reads this
            # mapping, and a dead entry in it is a tunnel that looks configured and carries nothing.
            self._processes.pop(spec.id, None)
            self._problems[spec.id] = str(exc)
            raise
        self._problems.pop(spec.id, None)
        await self._db.execute(_SET_ENABLED, (1, int(time.time()), tunnel_id))

    async def stop(self, tunnel_id: str, *, drain: bool = True) -> None:
        """Stop a tunnel. Draining lets the downloads already on it finish first.

        REFUSED while the tunnel hosts a swap (`TunnelHosting`), and so is removing it or giving
        it a new configuration, which stop it first. The swap is ended by the person, on purpose,
        and then the tunnel is free: a switch pressed on the wrong tunnel would otherwise end a
        swap two people had set up between them, with nothing saying the tunnel was carrying one.
        """
        if tunnel_id in self._hosted:
            raise TunnelHosting(END_THE_SWAP_FIRST)
        await self._db.execute(_SET_ENABLED, (0, int(time.time()), tunnel_id))
        process = self._processes.pop(tunnel_id, None)
        if process is None:
            return
        if drain:
            await process.drain()
        else:
            await process.stop_now()

    async def ensure_started(self, tunnel_id: str) -> None:
        """Start a tunnel that is meant to be running and is not, if there is a key to open it with.

        This is what makes a restart recover on its own. Nothing can run before somebody signs in
        (the configuration is sealed under their key), so rather than a tunnel staying down until it
        is turned on by hand, the first download that wants it brings it up. With no key it stays
        down, the download refuses by name, and the queue waits, which is the same answer a saved
        login gets in the same situation.
        """
        await self._after_any_restart(tunnel_id)
        if tunnel_id in self._processes:
            return
        row = await self._db.fetch_one(_GET, (tunnel_id,))
        if row is None or not row["enabled"]:
            return
        key = await self._read_key()
        if key is None:
            return
        try:
            await self.start(tunnel_id, key)
        except TunnelClientLost:
            # Said once by the check, not once per download that asks.
            return
        except TunnelError as exc:
            log.warning("tunnel.start_failed", tunnel=str(row["name"]), detail=str(exc))

    async def start_enabled(self, master_key: bytes) -> None:
        """Run every tunnel that is meant to be running. Called once a key becomes available.

        One that will not start is logged and skipped rather than stopping the rest: three tunnels
        where one provider's configuration has expired should leave two working, and the sites on
        the third refuse by name, which is how somebody finds out.
        """
        for row in await self._db.fetch_all(_LIST):
            if not row["enabled"] or str(row["id"]) in self._processes:
                continue
            try:
                await self.start(str(row["id"]), master_key)
            except TunnelClientLost:
                # Said once by the check, not once per tunnel.
                continue
            except TunnelError as exc:
                log.warning("tunnel.start_failed", tunnel=str(row["name"]), detail=str(exc))

    async def stop_all(self) -> None:
        """Stop every running tunnel without touching what is meant to be running, for shutdown.

        A hosting's renewal stops with it and nobody is told: the swap's session is being shut down
        by the same act, and a message to it now would arrive at a process on its way out.
        """
        for hosted in list(self._hosted.values()):
            await hosted.renewal.stop()
        self._hosted.clear()
        for tunnel_id in list(self._processes):
            process = self._processes.pop(tunnel_id)
            await process.stop_now()

    # --- which way out a site takes -----------------------------------------------------------

    async def default_route(self) -> str:
        row = await self._db.fetch_one(_GET_ROUTE, (DEFAULT_SCOPE,))
        return str(row["route"]) if row is not None else DIRECT

    async def site_route(self, site_key: str) -> str | None:
        """The route a site was given, or None when it follows the default."""
        row = await self._db.fetch_one(_GET_ROUTE, (site_key,))
        return str(row["route"]) if row is not None else None

    async def set_route(self, scope: str, route: str) -> None:
        await self._db.execute(_SET_ROUTE, (scope, route, int(time.time())))

    async def clear_route(self, site_key: str) -> None:
        """Put a site back to following the default."""
        await self._db.execute(_CLEAR_ROUTE, (site_key,))

    async def routes(self) -> dict[str, str]:
        """Every route that has been set, keyed by site, with the default under its own scope."""
        return {
            str(row["scope"]): str(row["route"]) for row in await self._db.fetch_all(_LIST_ROUTES)
        }

    # --- hosting a swap -------------------------------------------------------------------------

    async def can_host(self, tunnel_id: str) -> bool | None:
        """Whether this tunnel can host a swap, as last measured: None until somebody has tried.

        Measured rather than asked of the configuration, because nothing in a WireGuard file says
        whether the provider forwards a port to it: only asking the provider does, and the first
        attempt to host is that asking. Remembered on the row, so a screen listing the tunnels that
        can host a swap does not have to try each one.
        """
        row = await self._db.fetch_one(_GET, (tunnel_id,))
        if row is None or row["can_host"] is None:
            return None
        return bool(row["can_host"])

    async def host_on(
        self,
        tunnel_id: str,
        *,
        target_port: int,
        master_key: bytes,
        on_moved: HostingMoved | None = None,
        on_lost: HostingLost | None = None,
    ) -> Hosting:
        """Restart the tunnel with a listener that reaches `127.0.0.1:target_port`, and ask the
        provider for the public port that reaches the listener.

        The restart drains first: downloads already on the tunnel finish, and one that asks for it
        meanwhile waits (`ensure_started`). Sift asks for its own inside port and for NO particular
        public one, and advertises what comes back: a provider picks the public port itself. The
        answer is renewed every 45 seconds for as long as the hosting lasts; a renewal the provider
        refuses ends the hosting, and `on_lost` is told why.

        Raises `TunnelError` with the screen's words when the tunnel cannot host. That is measured
        here and remembered (`can_host`); a tunnel that did not start at all is not a measurement,
        and leaves the answer as it was. Either way the tunnel is put back as it was found.
        """
        async with self._lock_for(tunnel_id):
            if tunnel_id in self._hosted:
                raise TunnelError("That tunnel is already hosting a swap.")
            spec, config = await self._open(tunnel_id, master_key)
            process = self._processes.get(tunnel_id)
            was_running = process is not None and process.running()
            listener = Listener(
                internal_port=_inside_port(),
                natpmp_port=_free_udp_port(),
                target_port=target_port,
            )
            try:
                await self._restart(spec, config, listener)
            except TunnelError:
                await self._put_back(spec, config, was_running=was_running)
                raise
            client = NatPmp(listener.natpmp_port)
            try:
                mapped = await client.map_tcp(listener.internal_port)
            except NatPmpError:
                await self._set_can_host(tunnel_id, can=False)
                await self._put_back(spec, config, was_running=was_running)
                raise TunnelError(CANNOT_HOST) from None
            await self._set_can_host(tunnel_id, can=True)
            hosting = Hosting(
                public_ipv4=mapped.public,
                external_port=mapped.external_port,
                internal_port=listener.internal_port,
                natpmp_port=listener.natpmp_port,
            )
            renewal = Renewal(
                client,
                listener.internal_port,
                mapped,
                on_moved=partial(self._moved, tunnel_id),
                on_failed=partial(self._renewal_failed, tunnel_id),
            )
            self._hosted[tunnel_id] = _Hosted(
                spec=spec,
                hosting=hosting,
                renewal=renewal,
                was_running=was_running,
                master_key=master_key,
                on_moved=on_moved,
                on_lost=on_lost,
            )
            renewal.start()
            # The name and the fact. Never the address or the port: those are the session's.
            log.info("tunnel.hosting", tunnel=spec.name)
            return hosting

    async def stop_hosting(self, tunnel_id: str) -> None:
        """End the hosting and put the tunnel back as it was: restarted plain if it was carrying
        downloads, stopped if it was not. Nothing when the tunnel is not hosting."""
        async with self._lock_for(tunnel_id):
            hosted = self._hosted.pop(tunnel_id, None)
            if hosted is None:
                return
            await self._stop_hosting(hosted)

    async def exit_address(self, tunnel_id: str) -> str | None:
        """The public IPv4 address this tunnel leaves from, or None when it cannot be read.

        A hosting tunnel's is the address its provider gave (`Hosting.public_ipv4`); any other is
        read once through the tunnel itself (`TunnelProcess.exit_address`), starting it if it is
        meant to be running and is not, as the swap about to dial through it would. The VPN
        server's address, never this device's: for the caller alone, never logged.
        """
        hosted = self._hosted.get(tunnel_id)
        if hosted is not None:
            return hosted.hosting.public_ipv4
        await self.ensure_started(tunnel_id)
        process = self._processes.get(tunnel_id)
        return None if process is None else await process.exit_address()

    async def server_address(self, tunnel_id: str) -> str | None:
        """The address of the VPN server this tunnel connects to, or None when it cannot be read.

        The configuration's Endpoint as the tunnel's own client reports it, the reading Settings
        shows beside the tunnel (`TunnelProcess.server_address`), so a name in the Endpoint line
        arrives as the address the client dialled. The tunnel is started first if it is meant
        to be running and is not, as the swap about to use it would. Two configurations for one
        server share this even when they leave from different exits. For the caller alone, never
        logged.
        """
        await self.ensure_started(tunnel_id)
        process = self._processes.get(tunnel_id)
        return None if process is None else await process.server_address()

    def hosting(self, tunnel_id: str) -> Hosting | None:
        """What the tunnel is hosting on now, or None. The current answer: a renewal can move it."""
        hosted = self._hosted.get(tunnel_id)
        return hosted.hosting if hosted is not None else None

    async def _stop_hosting(self, hosted: _Hosted) -> None:
        """The renewal stops, then the tunnel is put back. Called holding the tunnel's lock."""
        await hosted.renewal.stop()
        try:
            spec, config = await self._open(hosted.spec.id, hosted.master_key)
        except TunnelError:
            # The configuration went while the swap ran (a password reset, a tunnel removed). A
            # listener must not outlive the swap it was for, so the client goes down with nothing
            # to restart it plain.
            await self._take_down(hosted.spec.id)
        else:
            await self._put_back(spec, config, was_running=hosted.was_running)
        log.info("tunnel.stopped_hosting", tunnel=hosted.spec.name)

    async def _moved(self, tunnel_id: str, mapped: Mapped) -> None:
        """A renewal came back with another public port or address. The session is told."""
        hosted = self._hosted.get(tunnel_id)
        if hosted is None:
            return
        hosted.hosting = replace(
            hosted.hosting, public_ipv4=mapped.public, external_port=mapped.external_port
        )
        if hosted.on_moved is not None:
            await _guarded(hosted, hosted.on_moved(hosted.hosting))

    async def _renewal_failed(self, tunnel_id: str, error: NatPmpError) -> None:
        """The provider stopped keeping the port. The hosting is over: the session is told first,
        so it stops waiting on a port nobody can reach, and then the tunnel is put back."""
        async with self._lock_for(tunnel_id):
            hosted = self._hosted.pop(tunnel_id, None)
            if hosted is None:
                return
            # The name and what the provider said, which is a fixed phrase and never an address.
            log.warning("tunnel.hosting_lost", tunnel=hosted.spec.name, detail=str(error))
        # Told outside the lock: the session's own end asks this store to stop hosting, which
        # takes the same lock, and a lock held across that call never opens. The tunnel is put
        # back under it again afterwards, since the session's own stop finds nothing to stop.
        await _tell_lost(
            hosted,
            TunnelError(
                f"The tunnel {hosted.spec.name} lost the port its provider gave it, so the "
                "swap was cut off."
            ),
        )
        async with self._lock_for(tunnel_id):
            await self._stop_hosting(hosted)

    async def _restart(self, spec: TunnelSpec, config: str, listener: Listener | None) -> None:
        """Drain the tunnel's client if it has one, and run it again, with a listener or without.

        While this runs, a download asking for the tunnel waits on it (`ensure_started`) instead of
        being refused. A client that does not come back is taken off the map with its reason, the
        same as a failed `start`.
        """
        restarted = asyncio.Event()
        self._restarts[spec.id] = restarted
        try:
            process = self._processes.get(spec.id)
            if process is None:
                process = TunnelProcess(spec, owner=self._owner)
                self._processes[spec.id] = process
            else:
                await process.drain()
            try:
                await process.start(config, listener=listener)
            except TunnelError as exc:
                self._processes.pop(spec.id, None)
                self._problems[spec.id] = str(exc)
                raise
            self._problems.pop(spec.id, None)
        finally:
            del self._restarts[spec.id]
            restarted.set()

    async def _put_back(self, spec: TunnelSpec, config: str, *, was_running: bool) -> None:
        """The tunnel as it was before a hosting: running plain, or not running at all."""
        if not was_running:
            await self._take_down(spec.id)
            return
        try:
            await self._restart(spec, config, None)
        except TunnelError as exc:
            # Logged and left, like any start that fails: the next download that wants the tunnel
            # tries again, and the screen carries the reason.
            log.warning("tunnel.start_failed", tunnel=spec.name, detail=str(exc))

    async def _after_any_restart(self, tunnel_id: str) -> None:
        """Wait out a restart of this tunnel, if one is running.

        A swap starting or ending on the tunnel. A download asking for it waits for the client to
        come back rather than being refused for the few seconds it is gone, or for as long as the
        downloads already on it take to finish, since a restart drains first. That wait IS the
        pause routed downloads take at each swap's start and end.
        """
        restarting = self._restarts.get(tunnel_id)
        if restarting is not None:
            await restarting.wait()

    async def _take_down(self, tunnel_id: str) -> None:
        """Stop the tunnel's client, if it has one, without touching what it is meant to be doing."""
        process = self._processes.pop(tunnel_id, None)
        if process is not None:
            await process.drain()

    async def _set_can_host(self, tunnel_id: str, *, can: bool) -> None:
        # Measured by a swap, not pressed by anybody, so no route is there to say so: the tunnel's
        # "Can host" is drawn where a swap's tunnel is chosen, which reads again on this bell.
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_SET_CAN_HOST, (int(can), int(time.time()), tunnel_id))

    def _lock_for(self, tunnel_id: str) -> asyncio.Lock:
        return self._locks.setdefault(tunnel_id, asyncio.Lock())


async def _tell_lost(hosted: _Hosted, error: TunnelError) -> None:
    if hosted.on_lost is not None:
        await _guarded(hosted, hosted.on_lost(error))


async def _guarded(hosted: _Hosted, telling: Awaitable[None]) -> None:
    """Tell the session something, and carry on whatever it does with it.

    The tunnel has to be put back whether or not the session's own handler worked; a fault in the
    handler is the session's, and is logged by name without anything it was told.
    """
    try:
        await telling
    except Exception:  # the put-back must not depend on it; see the docstring
        log.warning("tunnel.hosting_watcher_failed", tunnel=hosted.spec.name)


def _view(row: object, health: TunnelHealth | None, problem: str | None = None) -> TunnelView:
    mapping = dict(row)  # type: ignore[call-overload]
    measured = mapping["can_host"]
    return TunnelView(
        id=str(mapping["id"]),
        name=str(mapping["name"]),
        enabled=bool(mapping["enabled"]),
        running=health.running if health is not None else False,
        up=health.up if health is not None else False,
        draining=health.draining if health is not None else False,
        last_handshake_at=health.last_handshake_at if health is not None else None,
        endpoint=health.endpoint if health is not None else None,
        problem=problem,
        can_host=None if measured is None else bool(measured),
    )


__all__ = [
    "CANNOT_HOST",
    "DEFAULT_SCOPE",
    "Hosting",
    "HostingLost",
    "HostingMoved",
    "TunnelStore",
    "TunnelView",
]


#: The tunnels a download can be routed through and a swap can be hosted on.
TUNNELS: Part[TunnelStore] = Part("tunnels")
