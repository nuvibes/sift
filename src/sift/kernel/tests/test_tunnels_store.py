# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storing tunnels and the routes sites take, and hosting a swap on one.

The client itself is stood in for: it is a binary the image ships. What is real here is everything
around it: that a configuration is checked before it is stored and never stored readable, that
turning a tunnel on and off moves the process and the intent together, and that removing a tunnel
leaves the sites routed through it refusing rather than quietly going out the front door.
"""

from __future__ import annotations

import asyncio
import functools
import secrets
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel.db import Database
from sift.kernel.secret_store import SecretStore
from sift.kernel.tunnels import store as store_module
from sift.kernel.tunnels.egress import DIRECT, EgressRouter
from sift.kernel.tunnels.natpmp import Mapped, NatPmpError, Renewal
from sift.kernel.tunnels.process import Listener, TunnelError, TunnelProcess
from sift.kernel.tunnels.store import (
    CANNOT_HOST,
    DEFAULT_SCOPE,
    Hosting,
    TunnelHosting,
    TunnelStore,
)

_CONFIG = "[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = y\n"


def _site_of(url: str) -> str | None:
    """The download slice's catalog, stood in for: a `reddit.com` URL is the `reddit` Site."""
    return "reddit" if "reddit.com" in url else None


@pytest.fixture
def master_key() -> bytes:
    """A 256-bit key, the kind a sign-in unwraps."""
    return secrets.token_bytes(32)


@pytest.fixture
async def secret_store(temp_db: Database) -> SecretStore:
    await temp_db.initialize_schema()
    return SecretStore(temp_db)


@pytest.fixture
def store(temp_db: Database, secret_store: SecretStore) -> TunnelStore:
    return TunnelStore(temp_db, secret_store)


@pytest.fixture(autouse=True)
def _client_stands_in(monkeypatch: pytest.MonkeyPatch) -> None:
    async def accepts(_config: str, **_kwargs: object) -> None:
        return None

    async def starts(self: TunnelProcess, _config: str, **_kwargs: object) -> None:
        monkeypatch.setattr(self, "running", lambda: True, raising=False)

    monkeypatch.setattr(store_module, "validate_config", accepts)
    monkeypatch.setattr(TunnelProcess, "start", starts)
    monkeypatch.setattr(TunnelProcess, "stop_now", _nothing)
    monkeypatch.setattr(TunnelProcess, "drain", _nothing)


async def _nothing(self: Any, *_args: object, **_kwargs: object) -> None:
    return None


# --- what is stored ------------------------------------------------------------------------------


async def test_a_configuration_is_never_stored_readable(
    store: TunnelStore, temp_db: Database, master_key: bytes
) -> None:
    """A backup of the database is a locked box beside no key, exactly as a saved site login is."""
    await store.add(name="Sweden", config=_CONFIG, master_key=master_key)

    rows = await temp_db.fetch_all("SELECT * FROM tunnels")
    assert "PrivateKey" not in str([dict(row) for row in rows])
    sealed = await temp_db.fetch_all("SELECT ciphertext FROM secrets")
    assert sealed and b"PrivateKey" not in bytes(sealed[0]["ciphertext"])


async def test_a_configuration_that_will_not_parse_is_refused_before_it_is_stored(
    store: TunnelStore, temp_db: Database, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At the form, not at the next download from a site routed through it days later."""

    async def refuses(_config: str, **_kwargs: object) -> None:
        raise TunnelError("no")

    monkeypatch.setattr(store_module, "validate_config", refuses)
    with pytest.raises(TunnelError):
        await store.add(name="Bad", config="nonsense", master_key=master_key)
    assert await temp_db.fetch_all("SELECT id FROM tunnels") == []


async def test_a_tunnel_row_carries_no_port_and_has_not_been_measured_for_hosting(
    store: TunnelStore, temp_db: Database, master_key: bytes
) -> None:
    """There is no `port` column: a fixed number shared by every library would let two Sifts on
    one device end each other's clients, and a client's ports come from the operating system at
    each start. `can_host` starts unmeasured."""
    await store.add(name="A", config=_CONFIG, master_key=master_key)
    await store.add(name="B", config=_CONFIG, master_key=master_key)
    columns = {
        str(row["name"])
        for row in await temp_db.fetch_all("SELECT name FROM pragma_table_info('tunnels')")
    }
    assert "port" not in columns
    assert [view.can_host for view in await store.list()] == [None, None]


async def test_two_stores_hand_their_clients_different_owners(
    temp_db: Database, secret_store: SecretStore, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second Sift on the same device (even one opened on a copy of the same library) must
    never take the first one's client for its own. Ownership is proved by a token only the store
    that started a client has, so two stores must never hand out the same one; and every tunnel of
    ONE store shares it, so a client that store lost is still recognised as its own."""
    owners: list[str] = []

    async def starts(self: TunnelProcess, _config: str, **_kwargs: object) -> None:
        owners.append(self._owner)
        monkeypatch.setattr(self, "running", lambda: True, raising=False)

    monkeypatch.setattr(TunnelProcess, "start", starts)
    first, second = TunnelStore(temp_db, secret_store), TunnelStore(temp_db, secret_store)
    one = await first.add(name="One", config=_CONFIG, master_key=master_key)
    two = await first.add(name="Two", config=_CONFIG, master_key=master_key)
    await first.start(one, master_key)
    await first.start(two, master_key)
    await second.start(one, master_key)
    assert owners[0] == owners[1], "one store's tunnels do not share its token"
    assert owners[2] != owners[0], "a second store handed out the first one's token"


async def test_a_new_tunnel_is_not_running_until_it_is_turned_on(
    store: TunnelStore, master_key: bytes
) -> None:
    await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    (view,) = await store.list()
    assert not view.enabled and not view.running


# --- turning them on and off -----------------------------------------------------------------------


async def test_starting_records_the_intent_and_stopping_clears_it(
    store: TunnelStore, master_key: bytes
) -> None:
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)

    await store.start(tunnel_id, master_key)
    (running,) = await store.list()
    assert running.enabled and running.running

    await store.stop(tunnel_id)
    (stopped,) = await store.list()
    assert not stopped.enabled and not stopped.running


async def test_what_is_meant_to_be_running_starts_when_a_key_arrives(
    store: TunnelStore, master_key: bytes
) -> None:
    """The consequence of sealing the configuration: nothing can run before somebody signs in, and
    everything that should be running starts the moment they do."""
    first = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.add(name="Germany", config=_CONFIG, master_key=master_key)
    await store.start(first, master_key)
    await store.stop_all()  # a restart: the intent survives, the processes do not
    assert store.processes == {}

    await store.start_enabled(master_key)
    assert list(store.processes) == [first]


async def test_one_tunnel_that_will_not_start_does_not_hold_back_the_others(
    store: TunnelStore, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider's configuration expires. Two working tunnels should still come up, and the sites
    on the third refuse by name, which is how anybody finds out."""
    broken = await store.add(name="Broken", config=_CONFIG, master_key=master_key)
    fine = await store.add(name="Fine", config=_CONFIG, master_key=master_key)
    await store.start(broken, master_key)
    await store.start(fine, master_key)
    await store.stop_all()

    async def start(self: TunnelProcess, _config: str, **_kwargs: object) -> None:
        if self.name == "Broken":
            raise TunnelError("Broken did not connect")
        monkeypatch.setattr(self, "running", lambda: True, raising=False)

    monkeypatch.setattr(TunnelProcess, "start", start)
    await store.start_enabled(master_key)
    assert list(store.processes) == [fine]
    # And the one that would not start says why on the screen, rather than a bare "Not connecting"
    # that sends somebody to the log; the one that started carries nothing.
    problems = {view.id: view.problem for view in await store.list()}
    assert problems[broken] == "Broken did not connect"
    assert problems[fine] is None


async def test_a_configuration_that_cannot_be_opened_is_a_refusal_not_a_crash(
    store: TunnelStore, master_key: bytes
) -> None:
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    wrong_key = bytes(len(master_key))
    with pytest.raises(TunnelError, match="Sweden"):
        await store.start(tunnel_id, wrong_key)


async def test_starting_a_tunnel_that_is_not_there(store: TunnelStore, master_key: bytes) -> None:
    with pytest.raises(TunnelError):
        await store.start("nothing", master_key)


async def test_replacing_a_configuration_restarts_a_running_tunnel(
    store: TunnelStore, temp_db: Database, master_key: bytes
) -> None:
    """The running process is still holding the old key, so leaving it alone would keep using the
    configuration that was just replaced."""
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)
    before = await temp_db.fetch_all("SELECT id FROM secrets")

    await store.replace_config(tunnel_id, config=_CONFIG + "\n", master_key=master_key)

    after = await temp_db.fetch_all("SELECT id FROM secrets")
    assert len(after) == len(before), "the replaced configuration was left behind"
    assert {row["id"] for row in after} != {row["id"] for row in before}
    (view,) = await store.list()
    assert view.running and view.enabled


async def test_replacing_the_configuration_of_a_tunnel_that_is_gone(
    store: TunnelStore, master_key: bytes
) -> None:
    with pytest.raises(TunnelError):
        await store.replace_config("nothing", config=_CONFIG, master_key=master_key)


async def test_renaming_a_tunnel(store: TunnelStore, master_key: bytes) -> None:
    tunnel_id = await store.add(name="Sweeden", config=_CONFIG, master_key=master_key)
    await store.rename(tunnel_id, "Sweden")
    assert [view.name for view in await store.list()] == ["Sweden"]


# --- removing one, and what happens to the sites on it ---------------------------------------------


async def test_removing_a_tunnel_forgets_its_configuration(
    store: TunnelStore, temp_db: Database, master_key: bytes
) -> None:
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)

    await store.remove(tunnel_id)

    assert await store.list() == []
    assert await temp_db.fetch_all("SELECT id FROM secrets") == []
    assert store.processes == {}


async def test_removing_one_that_is_already_gone(store: TunnelStore) -> None:
    await store.remove("nothing")


async def test_a_tunnel_is_said_by_its_name_and_a_removed_one_as_removed(
    store: TunnelStore, temp_db: Database, master_key: bytes
) -> None:
    kept = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    gone = await store.add(name="Norway", config=_CONFIG, master_key=master_key)
    await store.remove(gone)

    async with temp_db.read() as connection:
        said = await store_module.tunnels_said(connection, [kept, gone, None, "", kept])
        nothing = await store_module.tunnels_said(connection, [None, ""])

    assert said == {kept: "Sweden", gone: "a tunnel since removed"}
    assert nothing == {}


async def test_a_site_on_a_removed_tunnel_refuses_rather_than_going_direct(
    store: TunnelStore, master_key: bytes
) -> None:
    """The route is deliberately left pointing at the tunnel that is gone. Moving it to the
    machine's own address would send traffic out of the one place it was routed away from, and
    nothing anywhere would say so."""
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.set_route("reddit", tunnel_id)
    await store.remove(tunnel_id)

    assert await store.site_route("reddit") == tunnel_id
    router = EgressRouter(
        store.processes,
        read_default=store.default_route,
        read_site=store.site_route,
        site_of=_site_of,
    )
    with pytest.raises(TunnelError):
        async with router.route_for("https://www.reddit.com/r/x/comments/1/t/"):
            pytest.fail("a site routed through a deleted tunnel went out anyway")


async def test_a_download_over_a_tunnel_that_is_down_is_told_why_not_that_it_is_gone(
    temp_db: Database, secret_store: SecretStore, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tunnel that is configured and not running must not refuse its downloads as one that "no
    longer exists", or a locked key, a configuration a provider expired and a switch turned off all
    read as a deleted tunnel. The router is built as the composition root builds it, and each
    state says its own reason; only a tunnel with no row is gone."""
    keys: list[bytes | None] = [master_key]

    async def read_key() -> bytes | None:
        return keys[0]

    store = TunnelStore(temp_db, secret_store, read_key=read_key)
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)
    await store.stop_all()
    await store.set_route("reddit", tunnel_id)
    router = EgressRouter(
        store.processes,
        read_default=store.default_route,
        read_site=store.site_route,
        ensure_started=store.ensure_started,
        site_of=_site_of,
        why_down=store.why_down,
    )
    url = "https://www.reddit.com/r/x/comments/1/t/"

    async def refusal() -> str:
        with pytest.raises(TunnelError) as refused:
            async with router.route_for(url):
                pytest.fail("a download went ahead over a tunnel that is down")
        return str(refused.value)

    async def fails(self: TunnelProcess, _config: str, **_kwargs: object) -> None:
        raise TunnelError("Sweden did not connect.")

    monkeypatch.setattr(TunnelProcess, "start", fails)
    assert await refusal() == (
        "The tunnel Sweden couldn't start, so nothing was sent. Sweden did not connect."
    )

    keys[0] = None
    store._problems.clear()
    assert await refusal() == (
        "The tunnel Sweden can't start until an admin signs in, so nothing was sent."
    )

    await store.stop(tunnel_id)
    assert "is turned off" in await refusal()

    await store.remove(tunnel_id)
    assert "no longer exists" in await refusal()


def test_the_composition_root_hands_the_router_the_stores_reason() -> None:
    from pathlib import Path

    wiring = Path(__file__).resolve().parents[2] / "wiring" / "downloads.py"
    assert "why_down=tunnels.why_down," in wiring.read_text(encoding="utf-8")


# --- the routes themselves ---------------------------------------------------------------------------


async def test_with_nothing_set_everything_is_direct(store: TunnelStore) -> None:
    assert await store.default_route() == DIRECT
    assert await store.site_route("reddit") is None


async def test_a_site_route_wins_over_the_default_and_can_be_put_back(store: TunnelStore) -> None:
    await store.set_route(DEFAULT_SCOPE, "t1")
    await store.set_route("reddit", DIRECT)
    router = EgressRouter(
        {}, read_default=store.default_route, read_site=store.site_route, site_of=_site_of
    )

    assert await router.route_of("https://www.reddit.com/r/x/comments/1/t/") == DIRECT
    assert await router.route_of("https://youtu.be/abc") == "t1"

    await store.clear_route("reddit")
    assert await router.route_of("https://www.reddit.com/r/x/comments/1/t/") == "t1"


async def test_setting_a_route_twice_replaces_it(store: TunnelStore) -> None:
    await store.set_route("reddit", "t1")
    await store.set_route("reddit", "t2")
    assert await store.site_route("reddit") == "t2"
    assert await store.routes() == {"reddit": "t2"}


async def test_replacing_the_configuration_of_a_stopped_tunnel_leaves_it_stopped(
    store: TunnelStore, master_key: bytes
) -> None:
    """Importing a fresh config for a tunnel that is off is not a request to turn it on."""
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.replace_config(tunnel_id, config=_CONFIG + "\n", master_key=master_key)
    (view,) = await store.list()
    assert not view.running and not view.enabled


async def test_a_tunnel_whose_configuration_is_already_gone(
    store: TunnelStore, temp_db: Database, master_key: bytes
) -> None:
    """A password reset clears the sealed secrets and leaves the tunnels behind. Neither starting
    nor removing one may fall over on that."""
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await temp_db.execute("UPDATE tunnels SET secret_id = NULL WHERE id = ?", (tunnel_id,))

    with pytest.raises(TunnelError):
        await store.start(tunnel_id, master_key)
    await store.remove(tunnel_id)
    assert await store.list() == []


async def test_a_tunnel_meant_to_be_running_comes_back_on_its_own(
    temp_db: Database, secret_store: SecretStore, master_key: bytes
) -> None:
    """What makes a restart recover without anybody visiting a settings screen. Nothing can start
    before somebody signs in, so the first download that wants the tunnel brings it up."""

    async def key() -> bytes | None:
        return master_key

    store = TunnelStore(temp_db, secret_store, read_key=key)
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)
    await store.stop_all()  # the restart
    assert store.processes == {}

    await store.ensure_started(tunnel_id)
    assert list(store.processes) == [tunnel_id]


async def test_nothing_starts_before_anybody_has_signed_in(
    store: TunnelStore, master_key: bytes
) -> None:
    """With no key the configuration cannot be opened, so the tunnel stays down and the downloads
    routed through it refuse and wait: the same answer a saved login gets in the same state."""
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)
    await store.stop_all()

    await store.ensure_started(tunnel_id)  # the default reader has no key
    assert store.processes == {}


async def test_bringing_up_a_tunnel_nobody_asked_for(
    temp_db: Database, secret_store: SecretStore, master_key: bytes
) -> None:
    """One that is off stays off, and one that is already running is left alone."""

    async def key() -> bytes | None:
        return master_key

    store = TunnelStore(temp_db, secret_store, read_key=key)
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)

    await store.ensure_started(tunnel_id)  # never enabled
    assert store.processes == {}

    await store.start(tunnel_id, master_key)
    await store.ensure_started(tunnel_id)  # already up
    assert list(store.processes) == [tunnel_id]

    await store.ensure_started("nothing")
    assert list(store.processes) == [tunnel_id]


async def test_a_tunnel_that_will_not_come_back_up_does_not_stop_the_download_refusing(
    temp_db: Database, secret_store: SecretStore, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configuration that has expired since the last run. Bringing it up fails, and the route
    then refuses by name rather than the failure escaping into the download as something else."""

    async def key() -> bytes | None:
        return master_key

    store = TunnelStore(temp_db, secret_store, read_key=key)
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)
    await store.stop_all()

    async def start(_self: TunnelProcess, _config: str, **_kwargs: object) -> None:
        raise TunnelError("Sweden did not connect")

    monkeypatch.setattr(TunnelProcess, "start", start)
    router = EgressRouter(
        store.processes,
        read_default=store.default_route,
        read_site=store.site_route,
        ensure_started=store.ensure_started,
        site_of=_site_of,
    )
    await store.set_route(DEFAULT_SCOPE, tunnel_id)
    with pytest.raises(TunnelError):
        async with router.route_for("https://www.reddit.com/r/x/comments/1/t/"):
            pytest.fail("a download went out over a tunnel that could not be brought up")


async def test_replacing_a_configuration_when_the_old_one_is_already_gone(
    store: TunnelStore, temp_db: Database, master_key: bytes
) -> None:
    """A password reset clears the sealed secrets. Importing a fresh configuration afterwards has
    to work, not fall over forgetting one that is not there."""
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await temp_db.execute("UPDATE tunnels SET secret_id = NULL WHERE id = ?", (tunnel_id,))

    await store.replace_config(tunnel_id, config=_CONFIG, master_key=master_key)

    row = await temp_db.fetch_one("SELECT secret_id FROM tunnels WHERE id = ?", (tunnel_id,))
    assert row is not None and row["secret_id"] is not None


# --- hosting a swap --------------------------------------------------------------------------------
#
# The client and the provider are both stood in for. What is real is everything the store decides:
# which restart happens when, what is remembered about the tunnel, what the session is told, and
# that the tunnel is always put back the way it was found.

_PUBLIC = "198.51.100.20"


class _Provider:
    """Stands in for the provider's NAT-PMP, as the class the store builds its client from.

    `answers` is what each mapping returns in turn; the last one repeats. A `NatPmpError` in it is
    the provider not giving a port."""

    def __init__(self, *answers: Mapped | NatPmpError) -> None:
        self.answers = list(answers) or [Mapped(_PUBLIC, 61000, 60)]
        self.asked: list[tuple[int, int]] = []
        self.relay_ports: list[int] = []

    def __call__(self, port: int) -> _Provider:
        self.relay_ports.append(port)
        return self

    async def map_tcp(
        self, internal_port: int, external_requested: int = 0, lifetime: int = 60
    ) -> Mapped:
        self.asked.append((internal_port, external_requested))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, NatPmpError):
            raise answer
        return answer


class _Starts:
    """Records every start of a client: which tunnel, and with which listener (None = plain)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.listeners: list[Listener | None] = []
        self.fail_with_listener = False
        self.fail_plain = False
        self.gate: asyncio.Event | None = None
        starts = self

        async def start(
            process: TunnelProcess, _config: str, *, listener: Listener | None = None
        ) -> None:
            starts.listeners.append(listener)
            if starts.gate is not None:
                await starts.gate.wait()
            if listener is not None and starts.fail_with_listener:
                raise TunnelError("Sweden did not connect")
            if listener is None and starts.fail_plain:
                raise TunnelError("Sweden did not connect")
            monkeypatch.setattr(process, "running", lambda: True, raising=False)

        monkeypatch.setattr(TunnelProcess, "start", start)


def _renewing_without_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """The renewal's 45 seconds made a moment, so a test sees what a renewal does."""

    async def no_wait(_seconds: float) -> None:
        await asyncio.sleep(0.01)

    monkeypatch.setattr(store_module, "Renewal", functools.partial(Renewal, sleep=no_wait))


async def _until(condition: Any, tries: int = 500) -> None:
    """Wait for what a background renewal does, a hundredth of a second at a time, up to five."""
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("it never happened")


@pytest.fixture
async def running(store: TunnelStore, master_key: bytes) -> str:
    """A tunnel carrying downloads, as it is when somebody starts a swap."""
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)
    return tunnel_id


async def test_hosting_restarts_the_tunnel_with_a_listener_and_hands_back_what_the_provider_gave(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = _Starts(monkeypatch)
    provider = _Provider(Mapped(_PUBLIC, 61000, 60))
    monkeypatch.setattr(store_module, "NatPmp", provider)

    with capture_logs() as logs:
        hosting = await store.host_on(running, target_port=53002, master_key=master_key)
        await store.stop_hosting(running)

    (listener, plain) = starts.listeners
    assert listener is not None and listener.target_port == 53002
    assert plain is None, "stopping did not restart the tunnel plain"
    assert (hosting.public_ipv4, hosting.external_port) == (_PUBLIC, 61000)
    assert (hosting.internal_port, hosting.natpmp_port) == (
        listener.internal_port,
        listener.natpmp_port,
    )
    # Asked through the relay port the listener opened, for its inside port and no public port.
    assert provider.relay_ports == [listener.natpmp_port]
    assert provider.asked == [(listener.internal_port, 0)]
    assert await store.can_host(running) is True
    assert running in store.processes
    # The tunnel's name and the fact, and never the address or the port.
    events = [
        (entry["event"], entry.get("tunnel")) for entry in logs if "hosting" in entry["event"]
    ]
    assert events == [("tunnel.hosting", "Sweden"), ("tunnel.stopped_hosting", "Sweden")]
    assert not [entry for entry in logs if _PUBLIC in str(entry) or "61000" in str(entry)]


async def test_a_tunnel_whose_provider_gives_no_port_says_so_and_is_put_back(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = _Starts(monkeypatch)
    monkeypatch.setattr(
        store_module, "NatPmp", _Provider(NatPmpError("no answer from the provider"))
    )

    with pytest.raises(TunnelError) as refused:
        await store.host_on(running, target_port=53002, master_key=master_key)

    assert str(refused.value) == CANNOT_HOST
    assert CANNOT_HOST == (
        "This tunnel's provider didn't give it a port. A swap needs a tunnel made on a P2P VPN "
        "server with port forwarding on."
    )
    assert await store.can_host(running) is False
    assert [view.can_host for view in await store.list()] == [False]
    assert starts.listeners[-1] is None and running in store.processes
    assert store.hosting(running) is None


async def test_a_tunnel_that_was_off_is_off_again_after_hosting(
    store: TunnelStore, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hosting does not turn a tunnel on: one that was not carrying downloads is stopped again, and
    what an admin asked for (`enabled`) is never touched."""
    starts = _Starts(monkeypatch)
    monkeypatch.setattr(store_module, "NatPmp", _Provider())
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)

    await store.host_on(tunnel_id, target_port=53002, master_key=master_key)
    assert tunnel_id in store.processes
    await store.stop_hosting(tunnel_id)

    assert len(starts.listeners) == 1
    assert store.processes == {}
    (view,) = await store.list()
    assert not view.enabled


async def test_a_tunnel_that_will_not_start_to_host_is_not_measured_and_is_put_back(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client that did not come up says nothing about the provider, so `can_host` is left as it
    was; the tunnel goes back to carrying downloads."""
    starts = _Starts(monkeypatch)
    starts.fail_with_listener = True
    monkeypatch.setattr(store_module, "NatPmp", _Provider())

    with pytest.raises(TunnelError, match="did not connect"):
        await store.host_on(running, target_port=53002, master_key=master_key)

    assert await store.can_host(running) is None
    assert starts.listeners[-1] is None and running in store.processes


async def test_a_tunnel_that_will_not_come_back_plain_after_hosting_says_why_and_the_stop_ends(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The swap is over and the tunnel was carrying downloads, but it does not connect again. The
    stop still ends (a swap's close must not fail on a tunnel): the failure is logged, and the
    screen carries the reason like any other start that failed; the next download tries again."""
    starts = _Starts(monkeypatch)
    monkeypatch.setattr(store_module, "NatPmp", _Provider())
    await store.host_on(running, target_port=53002, master_key=master_key)
    starts.fail_plain = True

    with capture_logs() as logs:
        await store.stop_hosting(running)

    assert store.hosting(running) is None
    assert running not in store.processes
    (view,) = await store.list()
    assert view.problem == "Sweden did not connect"
    failed = [entry for entry in logs if entry["event"] == "tunnel.start_failed"]
    assert [(entry["tunnel"], entry["detail"]) for entry in failed] == [
        ("Sweden", "Sweden did not connect")
    ]


async def test_a_tunnel_hosts_one_swap_at_a_time(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Starts(monkeypatch)
    monkeypatch.setattr(store_module, "NatPmp", _Provider())
    await store.host_on(running, target_port=53002, master_key=master_key)

    with pytest.raises(TunnelError, match="already hosting"):
        await store.host_on(running, target_port=53003, master_key=master_key)
    await store.stop_hosting(running)
    await store.stop_hosting(running)  # and a second stop is nothing


async def test_a_failed_renewal_ends_the_hosting_and_tells_the_session(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mapping nobody renews lapses within the minute; the session hears it from the store rather
    than from a guest that has gone quiet, and the tunnel goes back to carrying downloads."""
    starts = _Starts(monkeypatch)
    _renewing_without_the_wait(monkeypatch)
    monkeypatch.setattr(
        store_module,
        "NatPmp",
        _Provider(Mapped(_PUBLIC, 61000, 60), NatPmpError("no answer from the provider")),
    )
    lost: list[TunnelError] = []

    async def on_lost(error: TunnelError) -> None:
        lost.append(error)

    await store.host_on(running, target_port=53002, master_key=master_key, on_lost=on_lost)
    await _until(lambda: store.hosting(running) is None and len(starts.listeners) == 2)

    assert [str(error) for error in lost] == [
        "The tunnel Sweden lost the port its provider gave it, so the swap was cut off."
    ]
    assert starts.listeners[-1] is None and running in store.processes


async def test_a_session_told_its_port_lapsed_may_ask_to_stop_hosting_without_hanging(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The twin of the switch-off case: a real session's end asks the store to stop hosting, and
    that ask must find the lock free while the store is telling it."""
    starts = _Starts(monkeypatch)
    _renewing_without_the_wait(monkeypatch)
    monkeypatch.setattr(
        store_module,
        "NatPmp",
        _Provider(Mapped(_PUBLIC, 61000, 60), NatPmpError("no answer from the provider")),
    )
    told: list[str] = []

    async def on_lost(error: TunnelError) -> None:
        told.append(str(error))
        await store.stop_hosting(running)

    await store.host_on(running, target_port=53002, master_key=master_key, on_lost=on_lost)
    await asyncio.wait_for(
        _until(lambda: store.hosting(running) is None and len(starts.listeners) == 2), 2.0
    )

    assert len(told) == 1 and starts.listeners[-1] is None and running in store.processes


async def test_a_port_moved_at_a_renewal_is_passed_to_the_session(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Starts(monkeypatch)
    _renewing_without_the_wait(monkeypatch)
    provider = _Provider(Mapped(_PUBLIC, 61000, 60), Mapped(_PUBLIC, 62000, 60))
    monkeypatch.setattr(store_module, "NatPmp", provider)
    moved: list[Hosting] = []

    async def on_moved(hosting: Hosting) -> None:
        moved.append(hosting)

    first = await store.host_on(
        running, target_port=53002, master_key=master_key, on_moved=on_moved
    )
    await _until(lambda: bool(moved))
    await store.stop_hosting(running)

    assert [hosting.external_port for hosting in moved] == [62000]
    assert moved[0].internal_port == first.internal_port
    # Every renewal asked to keep the port it held.
    assert provider.asked[1] == (first.internal_port, 61000)


async def test_a_session_whose_handler_fails_does_not_stop_the_tunnel_being_put_back(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = _Starts(monkeypatch)
    _renewing_without_the_wait(monkeypatch)
    monkeypatch.setattr(
        store_module,
        "NatPmp",
        _Provider(Mapped(_PUBLIC, 61000, 60), Mapped(_PUBLIC, 62000, 60), NatPmpError("x")),
    )

    async def breaks(_told: object) -> None:
        raise RuntimeError("the session's own fault")

    with capture_logs() as logs:
        await store.host_on(
            running, target_port=53002, master_key=master_key, on_moved=breaks, on_lost=breaks
        )
        await _until(lambda: store.hosting(running) is None and len(starts.listeners) == 2)

    assert starts.listeners[-1] is None
    assert [entry["event"] for entry in logs].count("tunnel.hosting_watcher_failed") == 2


async def test_a_tunnel_hosting_a_swap_refuses_to_be_turned_off_or_removed(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch, the removal and a new configuration all stop the tunnel first, and each is
    refused while it hosts a swap, with the words to show. The swap is ended first, on purpose."""
    _Starts(monkeypatch)
    monkeypatch.setattr(store_module, "NatPmp", _Provider())
    lost: list[str] = []

    async def on_lost(error: TunnelError) -> None:
        lost.append(str(error))

    await store.host_on(running, target_port=53002, master_key=master_key, on_lost=on_lost)
    for press in (store.stop(running), store.stop(running, drain=False), store.remove(running)):
        with pytest.raises(TunnelHosting, match="End the swap first"):
            await press

    assert lost == [], "nothing was ended, so nothing is told"
    assert store.hosting(running) is not None and running in store.processes
    assert (await store.list())[0].enabled

    await store.stop_hosting(running)
    await store.stop(running)
    assert store.hosting(running) is None and store.processes == {}


async def test_a_download_asking_for_the_tunnel_mid_restart_waits_for_it(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE PAUSE. For the length of the restart a download that wants the tunnel waits for it to
    come back, rather than being refused, or sent some other way."""
    starts = _Starts(monkeypatch)
    starts.gate = asyncio.Event()
    monkeypatch.setattr(store_module, "NatPmp", _Provider())

    hosting = asyncio.create_task(store.host_on(running, target_port=53002, master_key=master_key))
    await _until(lambda: bool(starts.listeners))
    asking = asyncio.create_task(store.ensure_started(running))
    await asyncio.sleep(0.05)
    assert not asking.done(), (
        "a download asking mid-restart was answered before the tunnel was back"
    )

    starts.gate.set()
    await hosting
    await asyncio.wait_for(asking, 5)
    starts.gate = None
    await store.stop_hosting(running)


async def test_stopping_everything_stops_the_renewals_and_tells_nobody(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Starts(monkeypatch)
    monkeypatch.setattr(store_module, "NatPmp", _Provider())
    lost: list[TunnelError] = []

    async def on_lost(error: TunnelError) -> None:
        lost.append(error)

    await store.host_on(running, target_port=53002, master_key=master_key, on_lost=on_lost)
    await store.stop_all()

    assert store.hosting(running) is None and lost == []


async def test_a_configuration_gone_mid_swap_takes_the_listener_down_with_it(
    store: TunnelStore,
    running: str,
    temp_db: Database,
    master_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A password reset clears the sealed configurations. With nothing to restart it plain from, the
    client still goes: a listener must not outlive the swap it was opened for."""
    starts = _Starts(monkeypatch)
    monkeypatch.setattr(store_module, "NatPmp", _Provider())
    await store.host_on(running, target_port=53002, master_key=master_key)
    await temp_db.execute("UPDATE tunnels SET secret_id = NULL WHERE id = ?", (running,))

    await store.stop_hosting(running)

    assert len(starts.listeners) == 1 and store.processes == {}


async def test_a_tunnel_nobody_has_tried_to_host_on_is_not_measured(store: TunnelStore) -> None:
    assert await store.can_host("nothing") is None


async def test_a_tunnel_that_was_off_and_will_not_start_to_host_is_left_off(
    store: TunnelStore, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = _Starts(monkeypatch)
    starts.fail_with_listener = True
    monkeypatch.setattr(store_module, "NatPmp", _Provider())
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)

    with pytest.raises(TunnelError, match="did not connect"):
        await store.host_on(tunnel_id, target_port=53002, master_key=master_key)

    assert store.processes == {} and len(starts.listeners) == 1
    assert await store.can_host(tunnel_id) is None


async def test_a_session_that_asked_to_hear_nothing_is_told_nothing(
    store: TunnelStore, running: str, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both handlers are optional: a moved port is still taken, and a renewal the provider refuses
    still ends the hosting, with nobody to tell."""
    _Starts(monkeypatch)
    _renewing_without_the_wait(monkeypatch)
    monkeypatch.setattr(
        store_module,
        "NatPmp",
        _Provider(Mapped(_PUBLIC, 61000, 60), Mapped(_PUBLIC, 62000, 60), NatPmpError("x")),
    )
    await store.host_on(running, target_port=53002, master_key=master_key)
    await _until(lambda: store.hosting(running) is None)


async def test_a_renewal_that_answers_after_its_hosting_ended_changes_nothing(
    store: TunnelStore,
) -> None:
    """A renewal can be mid-question when the hosting ends. Its answer, or its failure, arrives for
    a tunnel no longer hosting, and is dropped."""
    await store._moved("nothing", Mapped(_PUBLIC, 62000, 60))
    await store._renewal_failed("nothing", NatPmpError("no answer from the provider"))
    assert store.hosting("nothing") is None


# --- where a tunnel leaves from -------------------------------------------------------------------


async def test_a_tunnels_exit_is_its_hosting_address_else_read_through_it(
    store: TunnelStore, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)

    async def read(_self: TunnelProcess) -> str:
        return "8.8.4.4"

    monkeypatch.setattr(TunnelProcess, "exit_address", read)
    assert await store.exit_address(tunnel_id) == "8.8.4.4"

    hosted = SimpleNamespace(hosting=Hosting(_PUBLIC, 62000, 45000, 5351))
    store._hosted[tunnel_id] = hosted  # type: ignore[assignment]
    assert await store.exit_address(tunnel_id) == _PUBLIC, "the provider's word while hosting"
    del store._hosted[tunnel_id]
    assert await store.exit_address("no-such-tunnel") is None


async def test_a_tunnels_endpoint_is_the_server_its_client_reports(
    store: TunnelStore, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server a tunnel connects to, as its own client reports it: what a swap token carries
    and a Join compares. None for a tunnel that is not there or cannot start."""
    tunnel_id = await store.add(name="Sweden", config=_CONFIG, master_key=master_key)
    await store.start(tunnel_id, master_key)

    async def read(_self: TunnelProcess) -> str:
        return "9.9.9.9"

    monkeypatch.setattr(TunnelProcess, "server_address", read)
    assert await store.server_address(tunnel_id) == "9.9.9.9"
    assert await store.server_address("no-such-tunnel") is None
