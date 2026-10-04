# SPDX-License-Identifier: AGPL-3.0-or-later
"""This install's name to a peer: an Ed25519 key, sealed, and the device id made from it.

## The id

The first 20 bytes of BLAKE3 over the raw public key, in base32 without padding (exactly 32
characters, since 20 bytes are 160 bits and base32 carries five a character), grouped in eight
fours: `ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567`. It is what History says a file arrived from and
what Settings > Privacy > Swaps shows, and it changes only when somebody presses Reset device id.

Twenty bytes rather than all thirty-two, because the id is read and compared by people, and 160
bits is still far past anything a peer could search for: finding a second key with the same id is
a 2**160 search.

## Why a key at all, when every session has a new token

The token's secret is what locks a session, so the key is not the lock. It is what makes the id
TRUE: each side's hello carries its public key and a signature bound to the session's secret
(`session.hello`), so a peer cannot claim another install's id: History's "from device ABCD-EFGH"
names a device that proved it holds the key. The key is sealed under an admin's master key
through the secret store, like a stash-box's key, and unsealed only by the session task for its
own length (`session.py`), never by a route.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field

from blake3 import blake3
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from sift.kernel.log import get_logger
from sift.kernel.secret_store import SecretStore
from sift.slices.swap.store import SessionStore

log = get_logger(__name__)

#: How many bytes of the digest the id keeps. 20 bytes = 32 base32 characters, exactly.
ID_BYTES = 20

#: The groups the id is shown in.
GROUP = 4

#: The shape of a device id as shown: eight groups of four base32 characters.
DEVICE_ID = re.compile(r"^[A-Z2-7]{4}(?:-[A-Z2-7]{4}){7}$")


class DeviceLocked(Exception):
    """The device key cannot be made or opened: nobody has entered an admin's password since
    this device started, or the sealed key is no longer readable."""


@dataclass(frozen=True, slots=True)
class Device:
    """This install's id and its unsealed key. Held by a session task for its length only."""

    id: str
    #: Named for what it does. A field called `key` of a typed value reads as a hardcoded
    #: secret to the scanner that guards every commit, and a name is cheaper than an excuse.
    signer: Ed25519PrivateKey = field(repr=False)

    @property
    def public(self) -> bytes:
        return public_bytes(self.signer.public_key())


def public_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def group(text: str, size: int = GROUP) -> str:
    """Text in hyphenated groups of `size`: how an id and a token are shown and pasted."""
    return "-".join(text[i : i + size] for i in range(0, len(text), size))


def device_id_of(public: bytes) -> str:
    """The id a raw 32-byte Ed25519 public key is known by."""
    if len(public) != 32:
        raise ValueError("an Ed25519 public key is 32 bytes")
    digest = blake3(public).digest()[:ID_BYTES]
    return group(base64.b32encode(digest).decode("ascii"))


def id_bytes(device_id: str) -> bytes:
    """The 20 bytes behind a shown id. Raises ValueError for anything that is not one."""
    if not DEVICE_ID.match(device_id):
        raise ValueError("not a device id")
    return base64.b32decode(device_id.replace("-", ""))


def id_from_bytes(raw: bytes) -> str:
    """The shown id for 20 raw bytes: how a token carries the host's id."""
    if len(raw) != ID_BYTES:
        raise ValueError("a device id is 20 bytes")
    return group(base64.b32encode(raw).decode("ascii"))


def _key_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


async def device_id(store: SessionStore) -> str | None:
    """The id, if this install has made one. Needs no key: the id is on the row."""
    row = await store.device()
    return None if row is None else row.device_id


async def ensure_device(
    store: SessionStore, secrets: SecretStore, master_key: bytes | None, *, now: int | None = None
) -> str:
    """The id, minting the key the first time. Raises `DeviceLocked` when a first mint has no key.

    Two presses racing on a fresh install both mint; the row keeps one (`ON CONFLICT DO NOTHING`)
    and the loser forgets the secret it sealed, so no orphaned key is left in the secrets table.
    """
    row = await store.device()
    if row is not None:
        return row.device_id
    if master_key is None:
        raise DeviceLocked("the device key cannot be made until the admin signs in")
    key = Ed25519PrivateKey.generate()
    minted = device_id_of(public_bytes(key.public_key()))
    secret_id = await secrets.seal(_key_bytes(key), master_key)
    kept = await store.put_device_if_absent(minted, secret_id, int(now or time.time()))
    if not kept:
        await secrets.forget(secret_id)
        row = await store.device()
        if row is None:  # pragma: no cover (the insert above conflicted with a row)
            raise RuntimeError("the device row vanished")
        return row.device_id
    log.info("swap.device_made")
    return minted


async def device_of(store: SessionStore, secrets: SecretStore, master_key: bytes | None) -> Device:
    """The id and the unsealed key, minting on first use. For a session task only."""
    await ensure_device(store, secrets, master_key)
    row = await store.device()
    if row is None or master_key is None:
        raise DeviceLocked("the device key is sealed until the admin signs in")
    opened = await secrets.open(row.key_secret_id, master_key)
    if opened is None or len(opened) != 32:
        raise DeviceLocked("the device key cannot be read")
    key = Ed25519PrivateKey.from_private_bytes(opened)
    if device_id_of(public_bytes(key.public_key())) != row.device_id:
        raise DeviceLocked("the device key does not match its id")
    return Device(row.device_id, key)


async def reset(
    store: SessionStore, secrets: SecretStore, master_key: bytes | None, *, now: int | None = None
) -> str:
    """Mint a new key and id, and forget the old key. Old sessions keep the old id in their rows."""
    if master_key is None:
        raise DeviceLocked("the device id cannot be reset until the admin signs in")
    before = await store.device()
    if before is None:
        return await ensure_device(store, secrets, master_key, now=now)
    key = Ed25519PrivateKey.generate()
    minted = device_id_of(public_bytes(key.public_key()))
    secret_id = await secrets.seal(_key_bytes(key), master_key)
    await store.replace_device(minted, secret_id, int(now or time.time()))
    await secrets.forget(before.key_secret_id)
    log.info("swap.device_reset")
    return minted
