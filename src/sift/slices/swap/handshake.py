# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hello and the code: who is on the other end of a swap, proved over the session's secret."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from blake3 import blake3
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sift.slices.swap.device import (
    Device,
    device_id_of,
    id_bytes,
)
from sift.slices.swap.frames import Chunk as Chunk
from sift.slices.swap.frames import Conn as Conn
from sift.slices.swap.frames import ProtocolError as ProtocolError

#: The job type a session runs as, on both sides.
SWAP_SESSION = "swap_session"

#: Silence from the other side for this long cuts the session off. See `session.py`.
WATCHDOG_SECONDS = 10.0

#: How often each side says it is still there, on the control connection.
PING_SECONDS = 2.0

#: The streams a transfer starts with, the most it will use, and the window the rate is read over.
STREAMS_START = 8
STREAMS_CAP = 32
RATE_WINDOW_SECONDS = 10.0

#: How the climb is judged (`Ladder`): the trend (the middle rate of the last three windows) holds
#: under nine tenths of the best, rises a twentieth over it and starts again a tenth over it; four
#: streams added with no rise end the climb.
TREND_WINDOWS = 3
TREND_HOLDS = 0.9
TREND_RISES = 1.05
TREND_RESUMES = 1.10
STREAMS_WITHOUT_A_RISE = 4

#: How many windows the estimate's pace is the mean of: a minute at ten seconds a window.
PACE_WINDOWS = 6

#: The wanted files the host makes ready before a stream asks for them, and how many at a time.
READY_AHEAD = 8
READYING_AT_A_TIME = 2

#: Chunks one stream may have sent and not yet had acknowledged.
WINDOW = 2

#: The most chunks one share holds (32 MiB): small enough that a file's end is spread over every
#: stream, large enough that a share's two answers cost a fraction of what it carries.
SHARE_MOST = 8

#: How long a guest waits for the host's hello: the host's task may be waiting for a worker.
HELLO_WAIT_SECONDS = 60.0

#: A guest gives up redialling a stream after this many failures in a row: the session is cut off.
REDIAL_LIMIT = 5

#: How often a side that was cut off tries again: the host to host on its tunnel, the guest to dial.
RETRY_SECONDS = 10.0

#: What the session's task says of itself on Activity: before the other side is there, and cut off.
WAITING_NOTE = "Waiting for them"
CUT_OFF_NOTE = "Cut off, waiting for them"

#: The code is this many characters of base32.
CODE_LENGTH = 6

#: The reasons a session ends with, as the row records them.
REASON_DONE = "done"
ENDED_BY_YOU = "ended by you"
ENDED_BY_THEM = "ended by them"
LOST = "lost"
REFUSED = "refused"
#: The token named one device and another answered: no code was ever compared, so its own reason.
WRONG_DEVICE = "wrong device"
#: The host's token ran out with nobody joining: nothing was connected, so nothing was lost.
EXPIRED = "expired"
#: The guest's token had been used already: the host takes one hello, and this was a second.
USED = "used"
#: The host started a swap both ways and the guest's Sift cannot send back: an older one, whose
#: hello does not say `both` (see "Both ways"). Nothing was offered either way.
OLDER = "older"
REASONS = (
    REASON_DONE,
    ENDED_BY_YOU,
    ENDED_BY_THEM,
    LOST,
    REFUSED,
    WRONG_DEVICE,
    EXPIRED,
    USED,
    OLDER,
)

#: Which state each reason leaves the row in.
_STATE_OF = {
    REASON_DONE: "done",
    ENDED_BY_YOU: "ended",
    ENDED_BY_THEM: "ended",
    REFUSED: "ended",
    WRONG_DEVICE: "ended",
    EXPIRED: "ended",
    USED: "ended",
    OLDER: "ended",
    LOST: "failed",
}

#: What a session told to end says to the other side, and what the other side then records.
_TELL = {ENDED_BY_YOU: "ended", REFUSED: "refused"}
_HEARD = {"ended": ENDED_BY_THEM, "refused": REFUSED}

_HELLO_CONTEXT = b"sift-swap-hello-1"
_CODE_CONTEXT = b"sift-swap-code"
_ROLES = ("host", "guest")

#: What the screens say when a swap cannot start. Each is shown as it is.
NO_TUNNEL = (
    "A swap goes through a tunnel on your side too. Choose one under Tunnel, then join again."
)
LOCKED = (
    "Your saved keys are locked. Enter your password in the box at the top of this page to unlock "
    "them, then try again."
)
#: A guest tunnel on the server the token names, or leaving from its address, has its dial answered
#: by that server and never reaches them. Refused before dialling (`SwapSessions.join`).
SAME_SERVER = (
    "Your tunnel and theirs leave from the same server. Choose a tunnel on another server."
)

#: A swap both ways asks for a folder beside Start, and the guest's files with its They match.
NO_FOLDER = "Choose a folder to put received files in."
CHOOSE_FIRST = "Choose what to send them first."

#: How long a Join or a Start waits to read its tunnel's addresses, its start included. Past it the
#: join goes ahead unchecked, and the token says the server is unknown.
EXIT_WAIT_SECONDS = 30.0


#: The parts of the Join form a refusal can be about, so the form says it under that part.
PASTED_FIELD = "token"
TUNNEL_FIELD = "tunnel_id"
FOLDER_FIELD = "dest_folder_id"


class SwapRefused(Exception):
    """A swap cannot start or join, for a reason a person can act on: the message is the words, and
    `field` the part of the form it is about (`PASTED_FIELD`, `TUNNEL_FIELD`), where there is one."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True, slots=True)
class Peer:
    """The other side, as its hello proved it."""

    device: str
    nonce: bytes
    session: str | None = None
    #: The face model the other side uses, or None. Not signed: another model than the sender's has
    #: the face pictures sent beside the facial fingerprints (`offer.FaceDescriptions`).
    model: str | None = None
    #: Whether the guest takes a file in shares over several streams. Not signed: see `sending.py`.
    striped: bool = False
    #: From a guest, that it can send back; from a host, that this session goes both ways. Not
    #: signed: it decides only whether the guest is asked for an offer too (`host.py`).
    both: bool = False
    #: That this side takes a file's song in an offer. Not signed: see `Offer.as_sent`.
    songs: bool = False
    #: That this side checks a file whole after its last piece (`pieces`). Not signed.
    once: bool = False
    #: That this side takes how many confirmed faces an offered person has (`OfferedFaces`). Not
    #: signed: see `Offer.as_sent`.
    counts: bool = False


#: The longest face model name a hello may carry.
_MODEL_NAME = 128


def _hello_message(role: str, secret: bytes, nonce: bytes) -> bytes:
    """What a hello's signature covers: the side, the session's secret (by digest) and the nonce.

    The secret is what makes it this session's: a hello copied out of an earlier session, by a peer
    who saw it there, carries a signature over a different secret and is refused here.
    """
    return _HELLO_CONTEXT + role.encode("ascii") + blake3(secret).digest() + nonce


def hello(
    device: Device, role: str, secret: bytes, nonce: bytes, **extra: str | int
) -> dict[str, Any]:
    """This side's first frame: `{"v": 1, "device", "nonce", "key", "sig"}` and any extra fields."""
    if role not in _ROLES or len(nonce) != 32:
        raise ValueError("a hello is from the host or the guest, with a 32-byte nonce")
    signature = device.signer.sign(_hello_message(role, secret, nonce))
    return {
        "v": 1,
        "device": device.id,
        "nonce": nonce.hex(),
        "key": device.public.hex(),
        "sig": signature.hex(),
        **extra,
    }


def _hex(value: object, length: int) -> bytes:
    if not isinstance(value, str) or len(value) != 2 * length:
        raise ProtocolError("a field of the wrong length")
    try:
        return bytes.fromhex(value)
    except ValueError as error:
        raise ProtocolError("a field that isn't hex") from error


def read_hello(frame: Mapping[str, Any], role: str, secret: bytes) -> Peer:
    """The other side's hello, checked: the version, that its id is its key's, that it signed THIS
    session. `role` is the side it claims to be. Raises `ProtocolError`."""
    if frame.get("v") != 1:
        raise ProtocolError("a hello of another version")
    public = _hex(frame.get("key"), 32)
    nonce = _hex(frame.get("nonce"), 32)
    signature = _hex(frame.get("sig"), 64)
    device = frame.get("device")
    if not isinstance(device, str) or device != device_id_of(public):
        raise ProtocolError("a hello whose device id isn't its key's")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            signature, _hello_message(role, secret, nonce)
        )
    except (InvalidSignature, ValueError) as error:
        raise ProtocolError("a hello not signed for this session") from error
    session = frame.get("session")
    model = frame.get("model")
    return Peer(
        device,
        nonce,
        session if isinstance(session, str) else None,
        model if isinstance(model, str) and 0 < len(model) <= _MODEL_NAME else None,
        frame.get("striped") == 1,
        frame.get("both") == 1,
        frame.get("songs") == 1,
        frame.get("once") == 1,
        frame.get("counts") == 1,
    )


def code_of(
    secret: bytes, host_device: str, guest_device: str, host_nonce: bytes, guest_nonce: bytes
) -> str:
    """The six characters both screens show. Every input is fixed-length, so the concatenation
    cannot be read two ways: the 32-byte secret, the two 20-byte ids, the two 32-byte nonces."""
    material = (
        _CODE_CONTEXT
        + secret
        + id_bytes(host_device)
        + id_bytes(guest_device)
        + host_nonce
        + guest_nonce
    )
    return base64.b32encode(blake3(material).digest()).decode("ascii")[:CODE_LENGTH]
