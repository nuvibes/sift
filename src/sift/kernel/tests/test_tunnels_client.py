# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnel program, checked where the pack put it: gone, altered, restored, and no pack at all.

A virus scanner can take the program off an install that finished cleanly, and the screen must not
then say a tunnel "did not connect", or refuse a download over a tunnel that "no longer exists".
These read the one sentence on the tunnel rows and on a download's row, over a pack made in a
temporary folder, and hold the digest the check compares with to the program the vendor manifest
pins.
"""

from __future__ import annotations

import hashlib
import json
from base64 import b64encode
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from sift.kernel.db import Database
from sift.kernel.secret_store import SecretStore
from sift.kernel.tunnels import client
from sift.kernel.tunnels import process as process_module
from sift.kernel.tunnels import store as store_module
from sift.kernel.tunnels.client import (
    CLIENT_CHANGED,
    CLIENT_REMOVED,
    PROGRAM,
    ClientCheck,
)
from sift.kernel.tunnels.egress import EgressRouter
from sift.kernel.tunnels.process import (
    TunnelClientLost,
    TunnelConfigInvalid,
    TunnelError,
    TunnelProcess,
    TunnelSpec,
)
from sift.kernel.tunnels.store import TunnelStore
from sift.testing.logs import uncached_log

REPO = Path(__file__).resolve().parents[4]

#: What the pack's program holds in these tests: bytes of their own, never the real program.
_SHIPPED = b"the tunnel program as the pack shipped it"

_CONFIG = "[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = y\n"


def _pack(tmp_path: Path) -> Path:
    """A pack's tool folder holding the program as it shipped."""
    folder = tmp_path / "vendor" / "bin"
    folder.mkdir(parents=True)
    (folder / PROGRAM).write_bytes(_SHIPPED)
    return folder


def _check_over(folder: Path | None) -> ClientCheck:
    return ClientCheck(lambda: folder, digest=hashlib.sha256(_SHIPPED).hexdigest())


def test_a_program_removed_changed_and_restored_is_said_each_way_and_logged_once_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uncached_log(monkeypatch, client)
    folder = _pack(tmp_path)
    check = _check_over(folder)
    program = folder / PROGRAM

    with capture_logs() as logs:
        assert check.fault() is None
        program.unlink()
        assert check.fault() == CLIENT_REMOVED
        assert check.fault() == CLIENT_REMOVED
        program.write_bytes(b"something else entirely, and longer than the program was")
        assert check.fault() == CLIENT_CHANGED
        program.write_bytes(_SHIPPED)
        assert check.fault() is None

    assert [(entry["event"], entry.get("reason")) for entry in logs] == [
        ("tunnel.client_lost", "removed"),
        ("tunnel.client_lost", "changed"),
        ("tunnel.client_restored", None),
    ]


def test_with_no_pack_there_is_nothing_to_compare_with() -> None:
    """Linux, or a checkout that has not fetched the tools: the machine's own copy is the right
    one, and the launch's own refusal says when it is missing."""
    assert _check_over(None).fault() is None


def test_the_digest_is_read_again_only_when_the_file_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked before every start and on every read of the tunnel list, so it must cost a `stat`."""
    folder = _pack(tmp_path)
    check = _check_over(folder)
    read: list[Path] = []
    real = client._digest_of

    def counting(path: Path) -> str:
        read.append(path)
        return real(path)

    monkeypatch.setattr(client, "_digest_of", counting)
    for _ in range(3):
        assert check.fault() is None
    assert len(read) == 1


async def test_a_start_with_the_program_removed_says_so_before_anything_is_launched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _pack(tmp_path)
    (folder / PROGRAM).unlink()
    monkeypatch.setattr(client, "CHECK", _check_over(folder))
    launched: list[object] = []

    async def launch(argv: list[str], **_kwargs: object) -> object:
        launched.append(argv)
        raise AssertionError("launched a program the check said was gone")

    monkeypatch.setattr("sift.kernel.tunnels.process.start_long_lived", launch)

    with pytest.raises(TunnelClientLost, match="antivirus removed"):
        await TunnelProcess(TunnelSpec(id="t1", name="Sweden")).start(_CONFIG)
    assert launched == []


async def test_every_tunnel_row_says_the_program_was_removed_and_clears_when_it_is_restored(
    tmp_path: Path, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def accepts(_config: str, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(store_module, "validate_config", accepts)
    folder = _pack(tmp_path)
    monkeypatch.setattr(client, "CHECK", _check_over(folder))
    await temp_db.initialize_schema()
    store = TunnelStore(temp_db, SecretStore(temp_db))
    key = b"k" * 32
    await store.add(name="Sweden", config=_CONFIG, master_key=key)
    await store.add(name="Norway", config=_CONFIG, master_key=key)

    (folder / PROGRAM).unlink()
    assert [view.problem for view in await store.list()] == [CLIENT_REMOVED, CLIENT_REMOVED]

    (folder / PROGRAM).write_bytes(_SHIPPED)
    assert [view.problem for view in await store.list()] == [None, None]


async def test_a_download_through_a_tunnel_whose_program_was_removed_fails_with_the_same_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The way out every download takes. The tunnel could not start, so it is not in the running
    set, and the refusal is the program's sentence rather than "no longer exists"."""
    folder = _pack(tmp_path)
    (folder / PROGRAM).unlink()
    monkeypatch.setattr(client, "CHECK", _check_over(folder))

    async def routed() -> str:
        return "t1"

    router = EgressRouter({}, read_default=routed)
    with pytest.raises(TunnelError) as refused:
        async with router.take("https://example.com/a"):
            pass
    assert str(refused.value) == CLIENT_REMOVED


def test_a_program_there_but_unreadable_is_said_as_removed_and_read_again_next_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scanner holding the file, or taking it: the program cannot run, so it is as good as
    gone, and nothing is remembered from the read that failed."""
    folder = _pack(tmp_path)
    check = _check_over(folder)
    real = client._digest_of
    reads: list[Path] = []

    def held(path: Path) -> str:
        reads.append(path)
        raise PermissionError("held by another process")

    monkeypatch.setattr(client, "_digest_of", held)
    assert check.fault() == CLIENT_REMOVED
    monkeypatch.setattr(client, "_digest_of", real)
    assert check.fault() is None, "the next look reads the digest again"
    assert len(reads) == 1


#: Whole, with keys shaped like keys, so nothing but the program stands between it and the check.
_WHOLE_CONFIG = (
    f"[Interface]\nPrivateKey = {b64encode(bytes(range(32))).decode()}\n\n"
    f"[Peer]\nPublicKey = {b64encode(bytes(range(32, 64))).decode()}\n"
    "Endpoint = 198.51.100.7:51820\n"
)


async def test_a_configuration_pasted_with_the_program_removed_is_refused_with_the_programs_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checking a configuration needs the program, so the refusal sends somebody to the scanner
    rather than to the file they pasted."""
    folder = _pack(tmp_path)
    (folder / PROGRAM).unlink()
    monkeypatch.setattr(client, "CHECK", _check_over(folder))

    async def never(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the configuration was checked by a program that is not there")

    monkeypatch.setattr(process_module, "run_once", never)

    with pytest.raises(TunnelConfigInvalid) as refused:
        await process_module.validate_config(_WHOLE_CONFIG)
    assert str(refused.value) == CLIENT_REMOVED


async def test_with_the_program_removed_no_tunnel_is_started_and_the_check_says_it_once(
    tmp_path: Path, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At sign-in and on a download's ask alike: the program's sentence is the check's to say,
    once, and not a start failure logged per tunnel and per download."""
    uncached_log(monkeypatch, store_module)

    async def accepts(_config: str, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(store_module, "validate_config", accepts)
    folder = _pack(tmp_path)
    monkeypatch.setattr(client, "CHECK", _check_over(folder))
    await temp_db.initialize_schema()
    key = b"k" * 32

    async def read_key() -> bytes:
        return key

    store = TunnelStore(temp_db, SecretStore(temp_db), read_key=read_key)
    await store.add(name="Sweden", config=_CONFIG, master_key=key)
    await store.add(name="Norway", config=_CONFIG, master_key=key)
    # Both meant to be running, as a start before the program went would have left them.
    await temp_db.execute("UPDATE tunnels SET enabled = 1")
    (folder / PROGRAM).unlink()
    first = (await store.list())[0].id

    with capture_logs() as logs:
        await store.start_enabled(key)
        await store.ensure_started(first)

    assert store.processes == {}
    assert [entry["event"] for entry in logs if entry["event"] == "tunnel.start_failed"] == []
    assert await store.check_client() == CLIENT_REMOVED


def test_the_digest_is_the_program_the_manifest_pins() -> None:
    """The program is built here, and the manifest pins the digest of the file its recipe makes.
    Moving that pin without moving `client.SHA256` would call every install's program altered."""
    manifest = json.loads((REPO / "scripts" / "vendor_manifest.json").read_text(encoding="utf-8"))
    (entry,) = [one for one in manifest["built"] if one["name"] == "wireproxy"]
    assert entry["version"] == client.VERSION
    assert entry["sha256"] == client.SHA256
    assert entry["file"] == f"bin/{PROGRAM}"
    built = REPO / "vendor" / str(entry["file"])
    if not built.is_file():
        pytest.skip("the tunnel program has not been built in this checkout")
    assert hashlib.sha256(built.read_bytes()).hexdigest() == client.SHA256
