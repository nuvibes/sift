# SPDX-License-Identifier: AGPL-3.0-or-later
"""A swap's frames on one locked connection: a JSON message, or a chunk with its own BLAKE3."""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sift.slices.swap import transfer
from sift.slices.swap.transfer import CHUNK_SIZE

#: A JSON frame's body is at most this long. A message longer than one frame travels in parts.
JSON_CAP = 1 << 20

#: The most a message in parts may add up to. An offer of a very large library is well under it.
MESSAGE_CAP = 128 * 1024 * 1024

#: A chunk frame: 8-byte file index, 4-byte chunk index, 32-byte BLAKE3, then the bytes.
CHUNK_PREFIX = struct.Struct(">QI32s")


class ProtocolError(Exception):
    """The other side sent something a Sift does not send. The connection is dropped."""


@dataclass(frozen=True, slots=True)
class Chunk:
    file: int
    index: int
    digest: bytes
    data: bytes = field(repr=False)


def encode_json(message: Mapping[str, Any]) -> list[bytes]:
    """A message as one frame, or as several `{"part": ...}` frames when it is longer than one."""
    text = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
    body = text.encode("utf-8")
    if len(body) <= JSON_CAP:
        return [struct.pack(">I", len(body)) + body]
    if len(body) > MESSAGE_CAP:
        raise ValueError("a message is too long to send")
    # Split the TEXT, so each part is a whole JSON document of its own; its escaped length is
    # bounded by the slice's, and 700,000 characters stays under the cap even fully escaped.
    step = 150_000
    pieces = [text[i : i + step] for i in range(0, len(text), step)]
    frames = []
    for number, piece in enumerate(pieces):
        part = json.dumps(
            {"part": number, "of": len(pieces), "text": piece}, separators=(",", ":")
        ).encode("utf-8")
        frames.append(struct.pack(">I", len(part)) + part)
    return frames


def encode_chunk(file_index: int, chunk_index: int, data: bytes) -> bytes:
    """A chunk frame: its prefix, then its bytes. The digest is computed here, of these bytes."""
    if not 0 <= file_index < 1 << 56:
        raise ValueError("a file index is under 2**56, so a chunk frame starts with a zero byte")
    body = CHUNK_PREFIX.pack(file_index, chunk_index, transfer.chunk_digest(data)) + data
    return struct.pack(">I", len(body)) + body


def _decode(body: bytes, chunk_cap: int) -> dict[str, Any] | Chunk:
    """One frame's body. A JSON frame opens with `{`; a chunk frame with the zero byte of its file
    index (capped under 2**56 by `encode_chunk`). Anything else is not a Sift frame."""
    if not body:
        raise ProtocolError("an empty frame")
    if body[0] == 0x7B:
        if len(body) > JSON_CAP:
            raise ProtocolError("a message frame past the cap")
        try:
            value: dict[str, Any] = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ProtocolError("a message that isn't JSON") from error
        # An object and nothing else: JSON that opens with `{` and parses is one.
        return value
    if body[0] == 0x00:
        if len(body) < CHUNK_PREFIX.size or len(body) > CHUNK_PREFIX.size + chunk_cap:
            raise ProtocolError("a chunk frame of the wrong size")
        file_index, chunk_index, digest = CHUNK_PREFIX.unpack(body[: CHUNK_PREFIX.size])
        return Chunk(file_index, chunk_index, digest, body[CHUNK_PREFIX.size :])
    raise ProtocolError("a frame of no kind Sift sends")


class Conn:
    """One locked connection: frames in, frames out, one writer at a time."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._write = asyncio.Lock()
        self.closed = False

    async def send(self, message: Mapping[str, Any]) -> None:
        frames = encode_json(message)
        async with self._write:
            for frame in frames:
                self.writer.write(frame)
                await self.writer.drain()

    async def send_chunk(self, file_index: int, chunk_index: int, data: bytes) -> None:
        frame = encode_chunk(file_index, chunk_index, data)
        async with self._write:
            self.writer.write(frame)
            await self.writer.drain()

    async def _frame(self) -> dict[str, Any] | Chunk:
        head = await self.reader.readexactly(4)
        (length,) = struct.unpack(">I", head)
        if length > max(JSON_CAP, CHUNK_PREFIX.size + CHUNK_SIZE):
            raise ProtocolError("a frame past every cap")
        return _decode(await self.reader.readexactly(length), CHUNK_SIZE)

    async def read(self, within: float | None = None) -> dict[str, Any] | Chunk:
        """The next message or chunk, within `within` seconds when given. A message sent in parts
        is put back together here."""
        return await asyncio.wait_for(self._read(), within)

    async def _read(self) -> dict[str, Any] | Chunk:
        frame = await self._frame()
        if isinstance(frame, Chunk) or "part" not in frame:
            return frame
        parts: list[str] = []
        total = frame.get("of")
        if not isinstance(total, int) or not 1 < total <= MESSAGE_CAP // 1000:
            raise ProtocolError("a message in parts with no count")
        while True:
            if frame.get("part") != len(parts) or frame.get("of") != total:
                raise ProtocolError("a message's parts out of order")
            text = frame.get("text")
            if not isinstance(text, str):
                raise ProtocolError("a message part with no text")
            parts.append(text)
            if sum(len(one) for one in parts) > MESSAGE_CAP:
                raise ProtocolError("a message past the cap")
            if len(parts) == total:
                break
            nxt = await self._frame()
            if isinstance(nxt, Chunk):
                raise ProtocolError("a chunk inside a message")
            frame = nxt
        return _object_of(parts)

    def close(self) -> None:
        self.closed = True
        with contextlib.suppress(Exception):
            self.writer.close()


def _object_of(parts: list[str]) -> dict[str, Any]:
    """A message sent in parts, joined and read as the JSON object it must be."""
    try:
        value = json.loads("".join(parts))
    except ValueError as error:
        raise ProtocolError("a message in parts that isn't JSON") from error
    if not isinstance(value, dict):
        raise ProtocolError("a message that isn't an object")
    return value
