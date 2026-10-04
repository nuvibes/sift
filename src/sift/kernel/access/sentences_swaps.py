# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines for a swap with another Sift, and for a file taken back."""

from __future__ import annotations

from collections.abc import Mapping

from sift.kernel.access.sentences_pieces import (
    SIFT,
    Line,
    capitalized,
    counted_line,
    files,
    listed,
    many,
    said,
)
from sift.kernel.access.sentences_who import _active, from_pass
from sift.kernel.text import non_empty_str

# --- a swap with another Sift: the other install is named by its device id and nothing else -----

#: The task word a swap's own acts are recorded under (`vocabulary.VIA_SWAP`).
SWAP = "swap"

#: How each way a swap can end is said: `{swap}` the swap and its device, `{moved}` what crossed,
#: `{after}` how far a lost connection got, `{word}` "exchange" or "swap".
SWAP_ENDED: Mapping[str, str] = {
    "done": "{swap} ended: {moved}",
    # This side pressed End the swap, or Cancel on Activity. "This device" rather than "You": the
    # record keeps that THIS side ended it, not which of this install's users pressed it.
    "ended by you": "This device ended {swap}: {moved}",
    "ended by them": "{them} ended the {word}: {moved}",
    "lost": "{swap} lost the connection {after}",
    # The code is compared before anything is offered, so nothing crosses on a refusal.
    "refused": "{swap} ended with nothing sent: the codes didn't match",
    # The token named one device and another answered. No code was compared, so this is not a
    # refusal; it is a stranger, and the line says so.
    "wrong device": "{swap} ended with nothing sent: a different device answered",
    # The host's token ran out with nobody joining, and the guest's had been used already. Nothing
    # was connected in either case, so neither is a connection lost.
    "expired": "{swap} ended with nothing sent: nobody joined in time",
    "used": "{swap} ended with nothing sent: that swap had been joined already",
    # A swap that sends and receives, and the other device's Sift cannot send back.
    "older": "{swap} ended with nothing sent: their Sift can't send files back",
}


def _files_moved(payload: Mapping[str, object]) -> int | None:
    """How many files a swap moved, where the payload says; never a flag read as a number."""
    count = payload.get("files")
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def _files_back(payload: Mapping[str, object]) -> int | None:
    """How many files crossed the other way, in a swap that sends and receives. None for a swap
    that goes one way: its payload carries no such count."""
    count = payload.get("back")
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def _both_ways(sent: int | None, received: int | None) -> str:
    """What a swap that sends and receives moved, from this side: "38 files sent and 4 received",
    "nothing sent and 4 files received", "nothing sent or received"."""
    if not sent and not received:
        return "nothing sent or received"
    if not sent:
        return f"nothing sent and {files(received or 0)} received"
    if not received:
        return f"{files(sent)} sent and nothing received"
    return f"{files(sent)} sent and {many(received)} received"


def _from_device(payload: Mapping[str, object]) -> str:
    """ " from device ABCD-EFGH-...", or nothing where the payload names no device."""
    device = non_empty_str(payload.get("device"))
    return f" from device {device}" if device else ""


def swap_started(by: str | None, payload: Mapping[str, object]) -> Line:
    """ "You started a swap" (or "an exchange") on the host's side, "You joined a swap from device
    ABCD-EFGH-..." on the guest's."""
    if payload.get("role") == "guest":
        whence = _from_device(payload)
        return _active(by, said("joined a swap", whence), said("a swap was joined", whence))
    if payload.get("two_way") is True:
        return _active(by, "started an exchange", "an exchange was started")
    return _active(by, "started a swap", "a swap was started")


def swap_ended(payload: Mapping[str, object]) -> Line:
    """How a swap ended and what crossed, from this device's side. A swap that sent and received is
    an exchange; a reason this build has no words for says only that it ended."""
    device = non_empty_str(payload.get("device"))
    count = _files_moved(payload)
    guest = payload.get("role") == "guest"
    direction = "received" if guest else "sent"
    moved = f"{files(count)} {direction}" if count else f"nothing {direction}"
    after = f"after {files(count)}" if count else f"with nothing {direction}"
    # A swap that sends and receives says both: `files` crossed from the host to the guest and
    # `back` from the guest to the host, so which is "sent" depends on whose record this is.
    back = _files_back(payload)
    if back is not None:
        moved = _both_ways(back, count) if guest else _both_ways(count, back)
        after = f"with {moved}"
    word = "swap" if back is None else "exchange"
    template = SWAP_ENDED.get(str(payload.get("reason")), "{swap} ended")
    return capitalized(
        said(
            template.format(
                swap=f"the {word} with device {device}" if device else f"the {word}",
                them=f"Device {device}" if device else "The other device",
                word=word,
                moved=moved,
                after=after,
            )
        )
    )


def added_by(task: str, payload: Mapping[str, object]) -> Line:
    """A file's arrival where the ledger says which task brought it in: "Sift added this file to the
    library by swap from device ABCD-EFGH-..."."""
    return said(SIFT, " added this file to the library", from_pass(task), _from_device(payload))


def _arrived_by_swap(
    line: Line, by: str | None, verb: str, task: str | None, payload: Mapping[str, object]
) -> Line:
    """An arrival by swap, with the device it came from said at the back of the line."""
    if verb != "added" or by != SIFT or task != SWAP:
        return line
    return said(line, _from_device(payload))


#: The payload key a taken-back stash-box answer carries, and its reason: matched on the picture
#: alone, or taken back by a person (`BY_HAND`); `files` counts a take-back over many.
TOOK_BACK = "took_back"

PICTURE_ALONE = "picture"

BY_HAND = "hand"


def took_back_said(
    payload: Mapping[str, object], file: str | None = None
) -> tuple[list[str], str | None] | None:
    """The boxes a take-back names and the reason it gives, or None where the payload is not one;
    on one file's page, only the boxes that spoke about that file (`by_file`)."""
    reason = payload.get(TOOK_BACK)
    if reason not in (PICTURE_ALONE, BY_HAND):
        return None
    raw = payload.get("boxes")
    by_file = payload.get("by_file")
    if file is not None and isinstance(by_file, Mapping) and isinstance(by_file.get(file), list):
        raw = by_file[file]
    boxes = [str(one) for one in raw if one] if isinstance(raw, list) else []
    each = "each" if len(boxes) > 1 else "it"
    why = f", because {each} matched on the picture alone" if reason == PICTURE_ALONE else None
    return boxes, why


def _took_back_line(by: str, payload: Mapping[str, object], about: Line) -> Line | None:
    """A stash-box answer taken back off a file, with the boxes it came from, or None where the
    payload is not one."""
    took = took_back_said(payload)
    if took is None:
        return None
    boxes, why = took
    which = listed([said(one) for one in boxes]) or said("a stash-box")
    return said(by, " took back what ", which, " said about ", about, why)


def _took_back_from(payload: Mapping[str, object], first: Line) -> Line:
    """What a take-back's feed line is about: the one file it names, or how many it took back."""
    many = payload.get("files")
    return counted_line(many, "files") if isinstance(many, int) and many > 1 else first
