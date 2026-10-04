# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serving a file, and answering "I already have this" without sending it again.

Unlike `http.py`, this needs the web framework: it reads request headers and may answer with no
body. Two halves of one problem. `no-cache` means "ask before reusing", so the ask must be answered
with a 304 or the whole file is sent again. And a picture whose address names its contents is not
asked about at all (`keeps`, `art_version`), sparing a grid a round trip per tile. Concealed
pictures and anything without a recorded digest keep asking, so re-locking takes effect at once.
"""

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

#: Keep it, but ask before reusing: everything not addressed by its contents, and everything
#: concealed however addressed.
CAREFUL = {"Cache-Control": "private, no-cache"}

#: A week: the whole of the exposure, since a kept copy is served with no permission check, so a
#: picture stays readable on a machine that long after its user is signed out or disabled. Hiding
#: does not wait (it raises the stamp and kills every tokened address); a session end cannot reach
#: that machine. A year would buy nothing: keeping does not extend a copy's life, only how often it
#: is re-checked. Not a setting: no library makes a week wrong.
KEEPABLE_SECONDS = 7 * 24 * 60 * 60

#: Do not ask again for a week: the address names the contents. `private`, never `public`: a shared
#: cache would sit before a permission check it cannot re-run and hand one viewer's picture to the
#: next.
KEEPABLE = {"Cache-Control": f"private, max-age={KEEPABLE_SECONDS}, immutable"}

#: The query key a picture's token rides on, named once: the client builds addresses with it, and a
#: token under another name is answered the careful way.
ART_KEY = "v"


def art_version(digests: str | None, stamp: int) -> str | None:
    """The token that goes on the end of a picture's address, or None if there is not one.

    What the picture is and whether the user may still see it are folded into one short string on
    an ordinary address. None when the contents are unknown, and the address stays bare and careful.
    A cache key, never a capability: nothing checks it on the way in, so an old or invented token
    is re-decided by the ordinary permission check rather than broken.
    """
    if not digests:
        return None
    return fingerprint(f"{digests}|{stamp}")


def art_carries(digests: str | None, kind: str) -> bool:
    """Whether the file's generated pictures include one of this kind.

    Read off the same `kind:digest|...` string `art_version` folds, so a row saying a clip exists
    always carries the token addressing it, and the wall never probes for a clip by its 404.
    """
    if not digests:
        return False
    return any(mark.startswith(f"{kind}:") for mark in digests.split("|"))


def face_version(stamp: int) -> str:
    """The token on the end of a face picture's address.

    Face crops and covers are never rewritten, so the token is the user's stamp alone, which must
    not be skipped or a face would stay readable from the browser's store after the person in it
    was hidden. In the kernel because the serving route and the address-building screen are
    separate features.
    """
    return art_version("face", stamp) or ""


def keeps(request: Request, *, version: str | None, concealed: bool) -> Mapping[str, str]:
    """How long the browser may keep this picture without asking.

    Careful without a version (nothing to promise), when concealed (every use must reach the server
    so shutting the vault takes effect at once), and when the ADDRESS carries no token: the promise
    is about the address, and a bare address stands still while the picture is rebuilt and the
    user's stamp moves, so `immutable` there would outlive concealment. The token is not compared
    with `version` (a cover composes its own); only whether one was named decides.
    """
    asked = request.query_params.get(ART_KEY)
    return KEEPABLE if asked and version is not None and not concealed else CAREFUL


def _tags(raw: str) -> list[str]:
    """The entity tags in an `If-None-Match` header, with any weakness marker dropped.

    Weak and strong compare alike: "the same as far as anybody watching is concerned" is exactly
    the question.
    """
    return [tag.removeprefix("W/").strip() for tag in raw.split(",") if tag.strip()]


def already_held(asked: Mapping[str, str] | Headers, holding: Mapping[str, str] | Headers) -> bool:
    """Whether the browser's copy is the one Sift is about to send.

    `If-None-Match` decides when present, as the specification orders: a tag is exact, while a date
    is truncated to the second.
    """
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


#: A file this size or smaller is read in the same thread hop that measures it: a picture costs
#: more in hops than in reading, and the streaming response takes a hop per 64 KB. Big files keep
#: streaming.
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
    """A file, or 304 and no body when the browser already has this exact copy.

    The validators are the file response's own, from one stat, so a 304 never disagrees with the 200
    it replaces and the small-file path sends the same tags as streaming (`SMALL_FILE_BYTES`).
    """
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
