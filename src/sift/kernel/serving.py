# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serving a file, and answering "I already have this" without sending it again."""

from __future__ import annotations

import os
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from pathlib import Path

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import FileResponse, Response

from sift.kernel.content.hashing import fingerprint
from sift.kernel.threads import on_serving_thread

NOT_MODIFIED = 304

#: Ask before reusing: everything not addressed by contents, and everything concealed.
CAREFUL = {"Cache-Control": "private, no-cache"}

#: A week is the whole exposure: a kept copy is served with no permission check.
KEEPABLE_SECONDS = 7 * 24 * 60 * 60

#: `private`, never `public`: a shared cache would hand one viewer's picture to the next.
KEEPABLE = {"Cache-Control": f"private, max-age={KEEPABLE_SECONDS}, immutable"}

#: The client builds addresses with it; a token under another name is answered carefully.
ART_KEY = "v"


def art_version(digests: str | None, stamp: int) -> str | None:
    """A picture's address token from its contents and stamp; a cache key, never a capability."""
    if not digests:
        return None
    return fingerprint(f"{digests}|{stamp}")


def art_carries(digests: str | None, kind: str) -> bool:
    """Whether the file's generated pictures include one of this kind."""
    if not digests:
        return False
    return any(mark.startswith(f"{kind}:") for mark in digests.split("|"))


def face_version(stamp: int) -> str:
    """The token on a face picture's address: the user's stamp alone, as crops never change."""
    return art_version("face", stamp) or ""


def keeps(request: Request, *, version: str | None, concealed: bool) -> Mapping[str, str]:
    """How long the browser may keep this picture; careful unless the address names a token."""
    asked = request.query_params.get(ART_KEY)
    return KEEPABLE if asked and version is not None and not concealed else CAREFUL


def _tags(raw: str) -> list[str]:
    """The entity tags in an `If-None-Match` header, weak and strong alike."""
    return [tag.removeprefix("W/").strip() for tag in raw.split(",") if tag.strip()]


def already_held(asked: Mapping[str, str] | Headers, holding: Mapping[str, str] | Headers) -> bool:
    """Whether the browser's copy is the one about to be sent; `If-None-Match` decides first."""
    tags = asked.get("if-none-match")
    if tags is not None:
        return "*" in _tags(tags) or holding.get("etag", "").removeprefix("W/") in _tags(tags)

    since = asked.get("if-modified-since")
    modified = holding.get("last-modified")
    if since is None or modified is None:
        return False
    try:
        return parsedate_to_datetime(modified) <= parsedate_to_datetime(since)
    except (TypeError, ValueError):
        # An unparseable date is no date: send the file rather than guess at a stale copy.
        return False


#: Read in the same hop that measures it: a picture costs more in hops than in reading.
SMALL_FILE_BYTES = 512 * 1024


def _measured(path: Path, limit: int) -> tuple[os.stat_result, bytes | None]:
    """The file's stat, and its bytes when it is small enough to carry back in one trip."""
    found = path.stat()
    if found.st_size <= limit:
        return found, path.read_bytes()
    return found, None


async def serve_file(
    request: Request, path: Path, *, media_type: str, headers: Mapping[str, str]
) -> Response:
    """A file, or 304 with no body when the browser holds this exact copy, from one stat."""
    try:
        stat, body = await on_serving_thread(_measured, path, SMALL_FILE_BYTES)
    except OSError:
        return FileResponse(path, media_type=media_type, headers=dict(headers))
    response = FileResponse(path, media_type=media_type, headers=dict(headers), stat_result=stat)
    if already_held(request.headers, response.headers):
        return Response(
            status_code=NOT_MODIFIED,
            headers={
                "etag": response.headers["etag"],
                "last-modified": response.headers["last-modified"],
                **dict(headers),
            },
        )
    if body is None:
        return response
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "etag": response.headers["etag"],
            "last-modified": response.headers["last-modified"],
            **dict(headers),
        },
    )
