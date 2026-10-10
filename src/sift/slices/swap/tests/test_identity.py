# SPDX-License-Identifier: AGPL-3.0-or-later
"""The device id, the token, and the session's rows.

The id and the token are pure functions, tested as such. The device key's minting, opening and
reset, and the rows' one-way doors, are tested against a real database carrying Sift's schema.
"""

from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path

import pytest

import sift.slices.swap.schema
import sift.slices.workbench.schema  # the ledger a swap's start and end go on
from sift.kernel.access import Role
from sift.kernel.db import Database
from sift.kernel.secret_store import SecretStore
from sift.slices.swap import device, token
from sift.slices.swap.store import SessionStore
from sift.testing.fixtures import create_user

_NOW = 1_800_000_000
_MASTER = b"\x07" * 32
#: A host's id, from 20 raw bytes rather than a key: the token carries the bytes.
_HOST_RAW = bytes(range(20))
_HOST = device.id_from_bytes(_HOST_RAW)


# --- the device id -----------------------------------------------------------------------------


@pytest.mark.unit
def test_a_device_id_is_32_base32_characters_in_eight_fours() -> None:
    shown = device.device_id_of(bytes(range(32)))
    assert re.fullmatch(r"[A-Z2-7]{4}(-[A-Z2-7]{4}){7}", shown), shown
    assert len(shown.replace("-", "")) == 32
    assert device.id_from_bytes(device.id_bytes(shown)) == shown
    # A different key is a different id; the same key is always the same one.
    assert device.device_id_of(bytes(range(1, 33))) != shown
    assert device.device_id_of(bytes(range(32))) == shown


@pytest.mark.unit
def test_an_id_that_is_not_one_is_refused() -> None:
    for bad in (
        "",
        "ABCD",
        "abcd-efgh-ijkl-mnop-qrst-uvwx-yz23-4567",
        "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4561",
    ):
        with pytest.raises(ValueError):
            device.id_bytes(bad)


# --- the token ---------------------------------------------------------------------------------


@pytest.mark.unit
def test_a_token_round_trips_every_field() -> None:
    made = token.mint("8.8.4.4", 41234, _HOST, _NOW, secret=b"\x05" * 32, server="9.9.9.9")
    text = made.text
    assert re.fullmatch(r"[A-Z2-7]{4}(-[A-Z2-7]{1,4})+", text)
    assert len(text.replace("-", "")) == 108  # 67 bytes of base32, unpadded
    read = token.parse(text.lower().replace("-", " - ") + "\n", _NOW + 60)
    assert read == made
    assert read.server == "9.9.9.9"
    assert read.expires == _NOW + token.LIFETIME_SECONDS
    # The secret is left out of the repr, so a stray log of a token cannot print it.
    assert "secret" not in repr(read)


@pytest.mark.unit
def test_a_token_carries_its_endpoint_or_says_it_does_not_know() -> None:
    """The host tunnel's server rides in the token as an address; one it cannot carry (none, a
    name, an IPv6 or private address) is written as unknown, never refused, and reads back as
    None."""
    for given, carried in (
        ("9.9.9.9", "9.9.9.9"),
        ("[9.9.9.9]", "9.9.9.9"),
        (None, None),
        ("", None),
        ("vpn.example", None),
        ("2606:4700::1111", None),
        ("10.2.0.1", None),
    ):
        made = token.mint("8.8.4.4", 41234, _HOST, _NOW, server=given)
        assert made.server == carried, given
        assert token.parse(made.text, _NOW).server == carried, given


@pytest.mark.unit
def test_a_version_one_token_still_reads_with_its_endpoint_unknown() -> None:
    """A token from the version before the server was carried joins as it did."""
    read = token.parse(_packed(), _NOW)
    assert (read.address, read.port, read.server) == ("8.8.4.4", 41234, None)


@pytest.mark.unit
def test_the_sentence_is_the_one_the_screen_shows() -> None:
    assert token.SENTENCE == (
        "This token holds your VPN's address, not your home's, and it works once."
    )


def _packed(**fields: object) -> str:
    values = {
        "version": 1,
        "ip": bytes([8, 8, 4, 4]),
        "port": 41234,
        "secret": b"\x05" * 32,
        "expires": _NOW + 3600,
        "device": _HOST_RAW,
    }
    values.update(fields)
    raw = struct.pack(
        ">B4sH32sI20s",
        values["version"],
        values["ip"],
        values["port"],
        values["secret"],
        values["expires"],
        values["device"],
    )
    return base64.b32encode(raw).decode().rstrip("=")


def _packed_v2(server: bytes, **fields: object) -> str:
    """A version 2 token: the version 1 fields and the server after them."""
    raw = base64.b32decode(_packed(**fields) + "=" * (-len(_packed(**fields)) % 8))
    return base64.b32encode(raw + server).decode().rstrip("=")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "words"),
    [
        (_packed(version=3), "different version"),
        (_packed_v2(bytes([9, 9, 9, 9]), version=3), "different version"),
        (_packed(version=2), "isn't a swap token"),
        (_packed_v2(bytes([9, 9, 9, 9])), "isn't a swap token"),
        (_packed_v2(bytes([192, 168, 1, 1]), version=2), "isn't a swap token"),
        (_packed_v2(bytes([224, 0, 0, 1]), version=2), "isn't a swap token"),
        (_packed(expires=_NOW - 1), "run out"),
        (_packed(expires=_NOW), "run out"),
        (_packed(expires=_NOW + 3 * 86400), "isn't a swap token"),
        (_packed(ip=bytes([192, 168, 1, 10])), "address"),
        (_packed(ip=bytes([127, 0, 0, 1])), "address"),
        (_packed(ip=bytes([10, 2, 0, 1])), "address"),
        (_packed(port=0), "address"),
        (_packed()[:-8], "isn't a swap token"),
        (_packed() + "AAAAAAAA", "isn't a swap token"),
        ("not a token at all!", "isn't a swap token"),
        ("", "isn't a swap token"),
    ],
)
def test_a_bad_token_is_refused_with_words(text: str, words: str) -> None:
    with pytest.raises(token.TokenRefused, match=words):
        token.parse(text, _NOW)


@pytest.mark.unit
def test_a_good_packed_token_parses() -> None:
    """The refusals above are refusals of THESE fields: the same packing with nothing wrong reads."""
    assert token.parse(_packed(), _NOW).port == 41234
    assert token.parse(_packed_v2(bytes(4), version=2), _NOW).server is None
    assert token.parse(_packed_v2(bytes([9, 9, 9, 9]), version=2), _NOW).server == "9.9.9.9"


@pytest.mark.unit
def test_a_host_cannot_mint_a_token_for_a_private_address() -> None:
    with pytest.raises(token.TokenRefused):
        token.mint("100.64.0.9", 41234, _HOST, _NOW)
    with pytest.raises(ValueError):
        token.mint("8.8.4.4", 0, _HOST, _NOW)
    # An address that is not an IPv4 address at all is refused in the same words, and a secret
    # that is not thirty-two bytes is a bug in the caller rather than a token.
    with pytest.raises(token.TokenRefused, match="address Sift can dial"):
        token.mint("tunnel.example", 41234, _HOST, _NOW)
    with pytest.raises(ValueError, match="32 bytes"):
        token.mint("8.8.4.4", 41234, _HOST, _NOW, secret=b"\x05" * 31)


# --- the device key and the rows -----------------------------------------------------------------


async def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "swap.sqlite3")
    await database.connect()
    await database.initialize_schema()
    return database


@pytest.mark.integration
async def test_the_device_is_made_once_opened_and_reset(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    try:
        store, secrets = SessionStore(database), SecretStore(database)
        with pytest.raises(device.DeviceLocked):
            await device.ensure_device(store, secrets, None)
        assert await device.device_id(store) is None
        made = await device.ensure_device(store, secrets, _MASTER, now=_NOW)
        assert device.DEVICE_ID.match(made)
        # Once: the id does not change on the next ask, with or without the key.
        assert await device.ensure_device(store, secrets, None) == made
        opened = await device.device_of(store, secrets, _MASTER)
        assert opened.id == made
        assert device.device_id_of(opened.public) == made
        with pytest.raises(device.DeviceLocked):
            await device.device_of(store, secrets, b"\x08" * 32)
        before = await store.device()
        assert before is not None
        again = await device.reset(store, secrets, _MASTER, now=_NOW + 1)
        assert again != made
        # The old key is forgotten, not left behind in the secrets table.
        assert await secrets.open(before.key_secret_id, _MASTER) is None
        assert (await device.device_of(store, secrets, _MASTER)).id == again
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_session_ends_once_and_a_manifest_resumes(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    try:
        store = SessionStore(database)
        who = await create_user(database, Role.ADMIN)
        await store.create(
            "01HOLDSESSION000000000001", role="guest", started_at=_NOW, started_by=who.id
        )
        assert await store.move("01HOLDSESSION000000000001", "connected", peer_device=_HOST)
        assert await store.end(
            "01HOLDSESSION000000000001", state="failed", reason="lost", now=_NOW + 5
        )
        # The start and the end are on the ledger, each in the transaction that wrote the row:
        # who pressed, and Sift's account of how it ended.
        events = await database.fetch_all(
            "SELECT verb, actor_kind, actor_id, payload FROM workbench_decisions"
            " WHERE verb IN ('swap_started', 'swap_ended') ORDER BY id"
        )
        assert [(row["verb"], row["actor_kind"], row["actor_id"]) for row in events] == [
            ("swap_started", "user", who.id),
            ("swap_ended", "sift", "swap"),
        ]
        assert json.loads(str(events[0]["payload"])) == {"role": "guest"}
        assert json.loads(str(events[1]["payload"])) == {
            "role": "guest",
            "device": _HOST,
            "reason": "lost",
            "files": 0,
        }
        # THE FIRST END IS KEPT: a late end, or a late move, changes nothing.
        assert not await store.end(
            "01HOLDSESSION000000000001", state="done", reason="done", now=_NOW + 6
        )
        assert not await store.move("01HOLDSESSION000000000001", "transferring")
        row = await store.get("01HOLDSESSION000000000001")
        assert row is not None
        assert (row.state, row.end_reason, row.peer_device) == ("failed", "lost", _HOST)
        assert row.short_id == "00000001"

        staged = str(tmp_path / "staging" / "x.part")
        await store.put_manifest(
            "01HOLDSESSION000000000001",
            "key-1",
            size=10,
            chunk_size=4,
            digest="d" * 64,
            staged_path=staged,
            now=_NOW,
        )
        for done in ([0], [0, 2, 0]):
            await store.record_done("01HOLDSESSION000000000001", "key-1", done, _NOW)
        kept = await store.manifest("01HOLDSESSION000000000001", "key-1")
        assert kept is not None
        assert kept.done == (0, 2)

        # A later session with the same device adopts it: the same bytes, so the same chunks.
        await store.create(
            "01HOLDSESSION000000000002", role="guest", started_at=_NOW + 10, started_by=who.id
        )
        await store.move("01HOLDSESSION000000000002", "connected", peer_device=_HOST)
        found = await store.adoptable(
            "01HOLDSESSION000000000002",
            peer_device=_HOST,
            file_key="key-1",
            size=10,
            chunk_size=4,
            digest="d" * 64,
        )
        assert found is not None
        assert (
            await store.adoptable(
                "01HOLDSESSION000000000002",
                peer_device=_HOST,
                file_key="key-1",
                size=10,
                chunk_size=4,
                digest="e" * 64,
            )
            is None
        )
        moved = await store.adopt(found, "01HOLDSESSION000000000002", _NOW + 11)
        assert (moved.session_id, moved.done, moved.staged_path) == (
            "01HOLDSESSION000000000002",
            (0, 2),
            staged,
        )
        assert await store.manifest("01HOLDSESSION000000000001", "key-1") is None
    finally:
        await database.close()


@pytest.mark.unit
def test_a_stored_list_that_cannot_be_read_is_read_as_empty() -> None:
    """A row can outlive the version that wrote it: a list a later reader cannot parse is nothing
    chosen and nothing done, never a failed page."""
    from sift.slices.swap import store as store_module

    assert store_module._chosen("{not json") == []
    assert store_module._chosen('{"kind": "person"}') == []
    assert store_module._done("not json") == ()
    assert store_module._done('{"0": true}') == ()
    assert store_module._done('[3, 1, -1, 1, true, "2"]') == (1, 3)


@pytest.mark.integration
async def test_a_session_moves_only_between_live_states_and_ends_only_in_a_final_one(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    try:
        store = SessionStore(database)
        assert store.database is database
        who = await create_user(database, Role.ADMIN)
        await store.create(
            "01HOLDSESSION000000000003", role="host", started_at=_NOW, started_by=who.id
        )

        with pytest.raises(ValueError, match="not a live state"):
            await store.move("01HOLDSESSION000000000003", "done")
        with pytest.raises(ValueError, match="not a state a session ends in"):
            await store.end("01HOLDSESSION000000000003", state="waiting", reason="x", now=_NOW)
        await store.set_rate("01HOLDSESSION000000000003", -5)
        await store.set_rate("01HOLDSESSION000000000003", 8_000_000)

        row = await store.get("01HOLDSESSION000000000003")
        assert row is not None and row.rate_bps == 8_000_000
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_history_line_names_the_other_device_only_when_one_session_answers(
    tmp_path: Path,
) -> None:
    from sift.slices.swap.store import peer_of

    database = await _database(tmp_path)
    try:
        store = SessionStore(database)
        who = await create_user(database, Role.ADMIN)
        await store.create(
            "01HOLDSESSION000000000004",
            role="guest",
            started_at=_NOW,
            started_by=who.id,
            peer_device=_HOST,
        )

        assert await peer_of(database, "00000004") == _HOST
        assert await peer_of(database, "99999999") is None
    finally:
        await database.close()


@pytest.mark.integration
async def test_asking_about_nobody_reads_nothing(tmp_path: Path) -> None:
    from sift.slices.swap.store import aliases_of_people, box_ids_of_people, people_by_box

    database = await _database(tmp_path)
    try:
        assert await aliases_of_people(database, []) == {}
        assert await box_ids_of_people(database, []) == {}
        assert await people_by_box(database, ["", ""]) == {}
    finally:
        await database.close()


@pytest.mark.unit
def test_a_key_or_an_id_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        device.device_id_of(bytes(31))
    with pytest.raises(ValueError, match="20 bytes"):
        device.id_from_bytes(bytes(19))


@pytest.mark.integration
async def test_two_first_presses_racing_keep_one_device_and_no_orphaned_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both presses read no device and both mint; the row keeps the first, and the loser forgets
    the key it sealed rather than leaving it in the secrets table."""
    database = await _database(tmp_path)
    try:
        store, secrets = SessionStore(database), SecretStore(database)
        first = await device.ensure_device(store, secrets, _MASTER, now=_NOW)
        (kept,) = await database.fetch_all("SELECT COUNT(*) AS n FROM secrets")
        real = store.device
        # The loser's first read, made before the winner's insert landed; the row after that.
        stale_reads: list[None] = [None]

        async def stale() -> object:
            if stale_reads:
                return stale_reads.pop()
            return await real()

        monkeypatch.setattr(store, "device", stale)

        assert await device.ensure_device(store, secrets, _MASTER, now=_NOW) == first
        (after,) = await database.fetch_all("SELECT COUNT(*) AS n FROM secrets")
        assert after["n"] == kept["n"]
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_device_key_that_is_locked_or_not_its_ids_is_not_handed_out(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    try:
        store, secrets = SessionStore(database), SecretStore(database)
        made = await device.ensure_device(store, secrets, _MASTER, now=_NOW)
        with pytest.raises(device.DeviceLocked, match="sealed"):
            await device.device_of(store, secrets, None)

        # The row names another id than the key it holds: refused rather than signing as it.
        row = await store.device()
        assert row is not None
        await store.replace_device(device.device_id_of(bytes(32)), row.key_secret_id, _NOW)
        with pytest.raises(device.DeviceLocked, match="does not match its id"):
            await device.device_of(store, secrets, _MASTER)
        assert made
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_reset_needs_the_keys_and_on_a_fresh_install_makes_the_first_id(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    try:
        store, secrets = SessionStore(database), SecretStore(database)
        with pytest.raises(device.DeviceLocked):
            await device.reset(store, secrets, None)

        made = await device.reset(store, secrets, _MASTER, now=_NOW)

        assert await device.device_id(store) == made
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_library_already_at_the_swap_tables_version_is_left_as_it_is(
    tmp_path: Path,
) -> None:
    """Called directly, because the kernel would not call it at all in this case: the step told
    the library is already at its version creates nothing, so a later shape is never overwritten."""
    database = await _database(tmp_path)
    try:
        await database.execute("DROP TABLE swap_manifests")
        async with database.write() as connection:
            await sift.slices.swap.schema.initialize(
                connection, on_disk=sift.slices.swap.schema.VERSION
            )
        rows = await database.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'swap_manifests'"
        )
        assert rows == []
    finally:
        await database.close()
