# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serving the built browser client on the API's own port and origin."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from sift.kernel.paths import PathEscape, confine
from sift.kernel.threads import on_serving_thread

# Declared, not read from the machine: Windows' registry serves fonts as octet-stream.
for _suffix, _type in (
    (".woff2", "font/woff2"),
    (".woff", "font/woff"),
    (".mjs", "text/javascript"),
    (".webmanifest", "application/manifest+json"),
):
    mimetypes.add_type(_type, _suffix)

#: Where the build writes the client. Empty in a checkout until someone builds it.
CLIENT_DIR = Path(__file__).parent / "web"

#: Every name under here carries its content hash, so it can be kept forever.
IMMUTABLE_PREFIX = "_app/immutable/"

FOREVER = "public, max-age=31536000, immutable"

#: Without a Cache-Control a browser guesses freshness and serves an upgrade's old files.
REVALIDATE = "no-cache"

#: Unhashed, so a week: asked every time, a tab icon is a request on every step through files.
BRAND_PREFIX = "brand/"
BRAND = "public, max-age=604800"

#: A history load (a restored tab) ignores `no-cache`, and would draw the previous release.
PAGE = "no-store"

INDEX = CLIENT_DIR / "index.html"

_HASH = re.compile(r"'(sha(?:256|384|512)-[A-Za-z0-9+/=]+)'")


def is_built() -> bool:
    """Is there a client to serve? An unbuilt checkout still boots, so tests need no Node."""
    return INDEX.is_file()


# The page is rebuilt under a running server, so a reading is kept against the file's identity:
# a header naming the previous build's script hash would leave a blank app.


class _Page(NamedTuple):
    """One reading of the built page, and what identified the file it was read from."""

    identity: tuple[str, int, int] | None
    hashes: tuple[str, ...]
    build: str


#: The closed answer: no inline script allowed, and nothing to compare a build against.
_NOTHING = _Page(identity=None, hashes=(), build="")

#: Rebound whole, no lock: the worst two threads can do is read the page twice and agree.
_page: _Page = _NOTHING


def _identity() -> tuple[str, int, int] | None:
    """What tells this build of the page apart from the next one, in one `stat`; None if absent."""
    try:
        stat = INDEX.stat()
    except OSError:
        return None
    return (str(INDEX), stat.st_mtime_ns, stat.st_size)


def _current() -> _Page:
    """The page as it is on disk now, read again only when the file has moved underneath us."""
    global _page
    remembered = _page
    identity = _identity()
    if identity is None:
        return _NOTHING
    if remembered.identity == identity:
        return remembered
    try:
        data = INDEX.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        # Not remembered: the next request is a free retry.
        return _NOTHING
    fresh = _Page(
        identity=identity,
        hashes=tuple(dict.fromkeys(_HASH.findall(text))),
        build=hashlib.blake2b(data, digest_size=8).hexdigest(),
    )
    # A half-written page is filed under its old identity, so the finished write re-reads it.
    _page = fresh
    return fresh


def script_hashes() -> tuple[str, ...]:
    """The client's inline script hashes, so the policy names them rather than 'unsafe-inline'."""
    return _current().hashes


def build_id() -> str:
    """Which client build this server would serve: a digest of the shell that names every file."""
    return _current().build


def cache_control_for(path: str, asset: Path) -> str:
    """How long the browser may keep this; `asset` is `INDEX` for an address the client routes."""
    if path.startswith(IMMUTABLE_PREFIX):
        return FOREVER
    if path.startswith(BRAND_PREFIX):
        return BRAND
    return PAGE if asset == INDEX or path == "index.html" else REVALIDATE


# Listed once per build off the loop: resolving per request read the disk on the event loop.


class ClientFiles(NamedTuple):
    """The build's files by the path a request names them with, and which build they came from."""

    identity: tuple[str, tuple[str, int, int] | None]
    files: Mapping[str, Path]
    built: bool


_files: ClientFiles | None = None


def _build_identity() -> tuple[str, tuple[str, int, int] | None]:
    """Which directory, and which page shell in it: what a listing is only good for."""
    return (str(CLIENT_DIR), _identity())


def list_files() -> ClientFiles:
    """Walk the build output, links skipped and every file `confine`d, and remember it. Blocking."""
    global _files
    identity = _build_identity()
    found: dict[str, Path] = {}
    if identity[1] is not None:
        root = CLIENT_DIR
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_symlink() or entry.is_junction():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    resolved = confine(root, Path(entry.path))
                except (OSError, PathEscape):
                    continue
                found[Path(entry.path).relative_to(root).as_posix()] = resolved
    listing = ClientFiles(identity=identity, files=found, built=identity[1] is not None)
    _files = listing
    return listing


def current_files() -> ClientFiles | None:
    """The remembered listing if it still describes the build on disk, else None. One `stat`."""
    remembered = _files
    if remembered is not None and remembered.identity == _build_identity():
        return remembered
    return None


def _files_now() -> ClientFiles:
    """The remembered listing, or a fresh one; the one `stat` it costs runs off the loop."""
    return current_files() or list_files()


async def files() -> ClientFiles:
    """The build's files, listed on a serving thread when the build has changed since last time."""
    return await on_serving_thread(_files_now)


def asset_for(path: str, listing: ClientFiles) -> Path | None:
    """The listed file for a request path, else the page shell; looked up, never joined."""
    if not listing.built:
        return None
    return listing.files.get(path, INDEX) if path else INDEX
