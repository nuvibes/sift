# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnels set up, the routes sites take, and the processes behind both, hosting included.

A configuration is a sealed private key; what is stored is the intent, never the running state."""

from __future__ import annotations

import asyncio
import secrets
import socket
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from functools import partial

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, in_clause
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

#: Reads an admin's master key, or None before anybody signs in; configurations are sealed by it.
KeyReader = Callable[[], Awaitable[bytes | None]]


async def _no_key() -> bytes | None:
    return None


#: The default route's scope; never a site key, which a test holds to plain words.
DEFAULT_SCOPE = "*"

# No port: two Sifts on one device would share a stored one and end each other's clients.
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
_NAMES = "SELECT id, name FROM tunnels WHERE id IN (?*)"

_GET_ROUTE = "SELECT route FROM tunnel_routes WHERE scope = ?"
_LIST_ROUTES = "SELECT scope, route FROM tunnel_routes"
_SET_ROUTE = (
    "INSERT INTO tunnel_routes (scope, route, updated_at) VALUES (?, ?, ?)"
    " ON CONFLICT(scope) DO UPDATE SET route = excluded.route, updated_at = excluded.updated_at"
)
_CLEAR_ROUTE = "DELETE FROM tunnel_routes WHERE scope = ?"

#: What a swap is told when the provider gives no port, naming the one fix.
CANNOT_HOST = (
    "This tunnel's provider didn't give it a port. A swap needs a tunnel made on a P2P VPN server with "
    "port forwarding on."
)

#: Ports inside the tunnel's own network stack, so nothing on the device can hold one.
_INSIDE_PORTS = range(20000, 60000)


@dataclass(frozen=True, slots=True)
class TunnelView:
    """One tunnel as a screen sees it: never the configuration or public key; the exit is shown."""

    id: str
    name: str
    enabled: bool
    running: bool
    up: bool
    draining: bool
    last_handshake_at: int | None
    #: The far server's address without its port; None while nothing has answered.
    endpoint: str | None = None
    #: Why the last start failed, in words, or None; cleared by the next success.
    problem: str | None = None
    #: Whether this tunnel can host a swap as last measured; None until tried.
    can_host: bool | None = None


@dataclass(frozen=True, slots=True)
class Hosting:
    """Where a swap can reach this device, as the provider gave it; for the session alone."""

    public_ipv4: str
    external_port: int
    internal_port: int
    natpmp_port: int


#: Told the new `Hosting` when a renewal moves the public port.
HostingMoved = Callable[[Hosting], Awaitable[None]]

#: Told why when the hosting ends on its own; not called for `stop_hosting`.
HostingLost = Callable[[TunnelError], Awaitable[None]]


@dataclass(slots=True)
class _Hosted:
    """One hosting in progress: what was handed out, what keeps it, and how to put things back."""

    spec: TunnelSpec
    hosting: Hosting
    renewal: Renewal
    #: Whether the tunnel carried downloads before, so stopping restores it as it was.
    was_running: bool
    #: The key, never the opened configuration, so the private key is not held for the swap.
    master_key: bytes
    on_moved: HostingMoved | None
    on_lost: HostingLost | None


def _inside_port() -> int:
    """A port for the listener inside the tunnel. See `_INSIDE_PORTS`."""
    return _INSIDE_PORTS.start + secrets.randbelow(len(_INSIDE_PORTS))


def _free_udp_port() -> int:
    """A free loopback UDP port for the client's NAT-PMP asks, let go before the client binds it."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


#: What a hosting tunnel answers a switch, a removal or a new configuration with.
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
        #: The last start failure per tunnel, for the screen.
        self._problems: dict[str, str] = {}
        #: The token every client this store starts carries; one per run, no other Sift's.
        self._owner = new_id()
        self._hosted: dict[str, _Hosted] = {}
        #: A restart in progress per tunnel; a download asking meanwhile waits on it.
        self._restarts: dict[str, asyncio.Event] = {}
        #: One hosting change at a time per tunnel.
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def processes(self) -> dict[str, TunnelProcess]:
        """The live processes by id; a tunnel missing here is one a routed download refuses over."""
        return self._processes

    async def add(self, *, name: str, config: str, master_key: bytes) -> str:
        """Check a configuration with the client's own parser, seal it, and record the tunnel."""
        await validate_config(config)
        secret_id = await self._secrets.seal(config.encode("utf-8"), master_key)
        now = int(time.time())
        tunnel_id = new_id()
        await self._db.execute(_INSERT, (tunnel_id, name, secret_id, now, now))
        return tunnel_id

    async def rename(self, tunnel_id: str, name: str) -> None:
        await self._db.execute(_RENAME, (name, int(time.time()), tunnel_id))

    async def replace_config(self, tunnel_id: str, *, config: str, master_key: bytes) -> None:
        """Seal a new configuration, forget the old, and restart the tunnel if it was running."""
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
        """Stop and forget a tunnel; sites routed through it keep refusing by its name."""
        row = await self._db.fetch_one(_GET, (tunnel_id,))
        if row is None:
            return
        await self.stop(tunnel_id, drain=False)
        await self._db.execute(_DELETE, (tunnel_id,))
        if row["secret_id"] is not None:
            await self._secrets.forget(str(row["secret_id"]))

    async def list(self) -> list[TunnelView]:
        """Every configured tunnel with its process state; a lost client is every row's problem."""
        lost = await client_fault()
        views: list[TunnelView] = []
        for row in await self._db.fetch_all(_LIST):
            process = self._processes.get(str(row["id"]))
            health = await process.health() if process is not None else None
            views.append(_view(row, health, lost or self._problems.get(str(row["id"]))))
        return views

    async def why_down(self, tunnel_id: str) -> str | None:
        """Why a configured tunnel is not running, as a refused download says; None if no tunnel."""
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
        """Check the tunnel program once at start-up, so a missing one is logged early."""
        return await client_fault()

    async def _open(self, tunnel_id: str, master_key: bytes) -> tuple[TunnelSpec, str]:
        """The tunnel's name and opened configuration, or a refusal saying which is missing."""
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
        """Open the tunnel's configuration and run it, marking it meant to be running."""
        # Never beside a restart: two starts together would be two clients.
        await self._after_any_restart(tunnel_id)
        spec, config = await self._open(tunnel_id, master_key)
        process = self._processes.setdefault(spec.id, TunnelProcess(spec, owner=self._owner))
        try:
            await process.start(config)
        except TunnelError as exc:
            # A tunnel that did not come up must not stay where the router reads.
            self._processes.pop(spec.id, None)
            self._problems[spec.id] = str(exc)
            raise
        self._problems.pop(spec.id, None)
        await self._db.execute(_SET_ENABLED, (1, int(time.time()), tunnel_id))

    async def stop(self, tunnel_id: str, *, drain: bool = True) -> None:
        """Stop a tunnel, draining by default; refused while it hosts a swap."""
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
        """Start a tunnel meant to be running, if a key is there: how a restart recovers."""
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
            # Said once by the check.
            return
        except TunnelError as exc:
            log.warning("tunnel.start_failed", tunnel=str(row["name"]), detail=str(exc))

    async def start_enabled(self, master_key: bytes) -> None:
        """Run every tunnel meant to be running; one that fails is logged and the rest go on."""
        for row in await self._db.fetch_all(_LIST):
            if not row["enabled"] or str(row["id"]) in self._processes:
                continue
            try:
                await self.start(str(row["id"]), master_key)
            except TunnelClientLost:
                # Said once by the check.
                continue
            except TunnelError as exc:
                log.warning("tunnel.start_failed", tunnel=str(row["name"]), detail=str(exc))

    async def stop_all(self) -> None:
        """Stop every running tunnel for shutdown, keeping what is meant to be running."""
        for hosted in list(self._hosted.values()):
            await hosted.renewal.stop()
        self._hosted.clear()
        for tunnel_id in list(self._processes):
            process = self._processes.pop(tunnel_id)
            await process.stop_now()

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
        """Every route set, keyed by site, with the default under its own scope."""
        return {
            str(row["scope"]): str(row["route"]) for row in await self._db.fetch_all(_LIST_ROUTES)
        }

    async def can_host(self, tunnel_id: str) -> bool | None:
        """Whether this tunnel can host a swap as last measured; only the provider can say."""
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
        """Restart the tunnel with a listener to `target_port` and ask the provider for a port.

        Renewed every 45 seconds; refused or lost, the tunnel is put back as it was found."""
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
            # The name and the fact, never the address or the port.
            log.info("tunnel.hosting", tunnel=spec.name)
            return hosting

    async def stop_hosting(self, tunnel_id: str) -> None:
        """End the hosting and put the tunnel back as it was; nothing when not hosting."""
        async with self._lock_for(tunnel_id):
            hosted = self._hosted.pop(tunnel_id, None)
            if hosted is None:
                return
            await self._stop_hosting(hosted)

    async def exit_address(self, tunnel_id: str) -> str | None:
        """The public IPv4 this tunnel leaves from, or None; for the caller alone, never logged."""
        hosted = self._hosted.get(tunnel_id)
        if hosted is not None:
            return hosted.hosting.public_ipv4
        await self.ensure_started(tunnel_id)
        process = self._processes.get(tunnel_id)
        return None if process is None else await process.exit_address()

    async def server_address(self, tunnel_id: str) -> str | None:
        """The VPN server's address as the client reports it, or None; never logged."""
        await self.ensure_started(tunnel_id)
        process = self._processes.get(tunnel_id)
        return None if process is None else await process.server_address()

    def hosting(self, tunnel_id: str) -> Hosting | None:
        """What the tunnel is hosting on now, or None; a renewal can move it."""
        hosted = self._hosted.get(tunnel_id)
        return hosted.hosting if hosted is not None else None

    async def _stop_hosting(self, hosted: _Hosted) -> None:
        """Stop the renewal, then put the tunnel back; called holding the tunnel's lock."""
        await hosted.renewal.stop()
        try:
            spec, config = await self._open(hosted.spec.id, hosted.master_key)
        except TunnelError:
            # The configuration is gone, so no listener may outlive the swap: the client goes down.
            await self._take_down(hosted.spec.id)
        else:
            await self._put_back(spec, config, was_running=hosted.was_running)
        log.info("tunnel.stopped_hosting", tunnel=hosted.spec.name)

    async def _moved(self, tunnel_id: str, mapped: Mapped) -> None:
        """A renewal returned another public port or address; the session is told."""
        hosted = self._hosted.get(tunnel_id)
        if hosted is None:
            return
        hosted.hosting = replace(
            hosted.hosting, public_ipv4=mapped.public, external_port=mapped.external_port
        )
        if hosted.on_moved is not None:
            await _guarded(hosted, hosted.on_moved(hosted.hosting))

    async def _renewal_failed(self, tunnel_id: str, error: NatPmpError) -> None:
        """The provider stopped keeping the port: tell the session, then put the tunnel back."""
        async with self._lock_for(tunnel_id):
            hosted = self._hosted.pop(tunnel_id, None)
            if hosted is None:
                return
            # A fixed phrase from the provider, never an address.
            log.warning("tunnel.hosting_lost", tunnel=hosted.spec.name, detail=str(error))
        # Told outside the lock, as the session's end takes the same lock.
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
        """Drain and rerun the tunnel's client, with or without a listener; downloads wait."""
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
            # Logged and left; the next download retries.
            log.warning("tunnel.start_failed", tunnel=spec.name, detail=str(exc))

    async def _after_any_restart(self, tunnel_id: str) -> None:
        """Wait out a restart of this tunnel: the pause routed downloads take at a swap's ends."""
        restarting = self._restarts.get(tunnel_id)
        if restarting is not None:
            await restarting.wait()

    async def _take_down(self, tunnel_id: str) -> None:
        """Stop the tunnel's client, if it has one, without changing what it is meant to do."""
        process = self._processes.pop(tunnel_id, None)
        if process is not None:
            await process.drain()

    async def _set_can_host(self, tunnel_id: str, *, can: bool) -> None:
        # Measured by a swap, so the settings bell redraws where a swap's tunnel is chosen.
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_SET_CAN_HOST, (int(can), int(time.time()), tunnel_id))

    def _lock_for(self, tunnel_id: str) -> asyncio.Lock:
        return self._locks.setdefault(tunnel_id, asyncio.Lock())


async def _tell_lost(hosted: _Hosted, error: TunnelError) -> None:
    if hosted.on_lost is not None:
        await _guarded(hosted, hosted.on_lost(error))


async def _guarded(hosted: _Hosted, telling: Awaitable[None]) -> None:
    """Tell the session something; the put-back carries on whatever its handler does."""
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


async def tunnels_said(connection: Connection, ids: Iterable[object]) -> dict[str, str]:
    wanted = sorted({one for one in ids if isinstance(one, str) and one})
    if not wanted:
        return {}
    asked, values = in_clause(_NAMES, wanted)
    rows = await connection.execute_fetchall(asked, values)
    named = {str(row["id"]): str(row["name"]) for row in rows}
    return {one: named.get(one, "a tunnel since removed") for one in wanted}


__all__ = [
    "CANNOT_HOST",
    "DEFAULT_SCOPE",
    "Hosting",
    "HostingLost",
    "HostingMoved",
    "TunnelStore",
    "TunnelView",
    "tunnels_said",
]


#: The tunnels a download can be routed through and a swap hosted on.
TUNNELS: Part[TunnelStore] = Part("tunnels")
