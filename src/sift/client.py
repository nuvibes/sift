# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serving the browser client.

The client is built to static files and served from here, on the same port and the same origin as
the API. There is no second process and no web server in front: one process, one port, and the
cookie the API sets is the cookie the pages already carry, with no cross-origin anything to arrange.

The files sit next to this module because the build puts them there. That is worth a sentence: the
alternative is a configured path, and a path that can be configured is a path that can be wrong
(in a checkout, in an install) for a thing whose location is never actually a choice.
"""

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

# --- what the client's own files are, said here rather than asked of the machine ----------------
#
# `mimetypes` starts from a small built-in table and then reads whatever the operating system
# offers: `/etc/mime.types` on most Linux systems, the registry on Windows. So the content type
# Sift serves its own files with is a property of the MACHINE, and the same build answers
# differently on two of them.
#
# Fonts are the ones that actually differ: Python's table has never carried woff or woff2, Linux
# fills them in and Windows does not, so the subset the interface is drawn with went out as
# `application/octet-stream` there. Declared here, the answer is the same everywhere, and a machine
# with an unhelpful registry cannot change what a shipped file claims to be.
for _suffix, _type in (
    (".woff2", "font/woff2"),
    (".woff", "font/woff"),
    (".mjs", "text/javascript"),
    (".webmanifest", "application/manifest+json"),
):
    mimetypes.add_type(_type, _suffix)

#: Where the build writes the client. Empty in a checkout until someone builds it.
CLIENT_DIR = Path(__file__).parent / "web"

#: The build puts everything whose name contains its own content hash under here.
#:
#: That is what makes the two cache rules below safe to be so far apart: a file in this directory
#: can never change without being renamed, so it can be kept forever, and everything outside it can
#: change under a name that stays the same, so it must be checked every time.
IMMUTABLE_PREFIX = "_app/immutable/"

#: Keep it for a year, and do not even ask: the name changes when the content does.
FOREVER = "public, max-age=31536000, immutable"

#: Ask every time. Not "do not store": the file is still cached, and the check is a cheap one that
#: usually answers "unchanged" without sending the file again.
#:
#: This is what makes a new version arrive. Without a Cache-Control header at all a browser is
#: allowed to guess how long a response stays fresh, and it does, so an upgraded Sift would serve
#: the previous page shell out of the cache, without asking, for as long as the guess lasted: an
#: application rebuilt, restarted and verified, and still showing yesterday's screens.
REVALIDATE = "no-cache"

#: The icons under `brand/`: unhashed names, so not forever, but kept a week without asking. Asked
#: every time, a tab icon is a request on every step through files.
BRAND_PREFIX = "brand/"
BRAND = "public, max-age=604800"

#: The page shell: never kept at all, so every load of the app fetches it from the server.
#:
#: `no-cache` is not enough for this one file. A browser answers a HISTORY load (a tab brought back
#: after the phone put it away, a restored session, the back button) from what it holds without
#: asking, whatever `no-cache` says; only `no-store` is outside that. A phone's browser restores
#: tabs all the time, and a tab restored across an upgrade would draw the previous release's screens
#: from its own copy of the shell and the previous release's scripts, which are kept for a year
#: under their own names. It would also defeat the stale-build banner: that window first asks the
#: server after the upgrade, so it takes the new build for its own. The shell is 8 KB, and a page of this app is
#: loaded once and then navigates without the server, so asking every time costs nothing.
PAGE = "no-store"

INDEX = CLIENT_DIR / "index.html"

#: The page names the hashes of its own inline scripts, and this reads them back out. They go into
#: the policy the server sends: see `script_hashes`.
_HASH = re.compile(r"'(sha(?:256|384|512)-[A-Za-z0-9+/=]+)'")


def is_built() -> bool:
    """Is there a client to serve?

    False in a checkout where nobody has built it. The server still runs: the API answers, and so
    does the health check. Refusing to boot would mean the tests could not run without Node.
    """
    return INDEX.is_file()


# --- reading the page that is on disk RIGHT NOW --------------------------------------------------
#
# Two things are read out of `index.html`: the hashes of the inline scripts the content policy has
# to name, and a digest saying which build a window is running. Neither can be read once for the
# life of the process: the file is rebuilt under a running server, and a header still naming the
# previous build's script hash makes the browser refuse the only script on the page: a blank
# application with nothing saying why.
#
# So a reading is kept against the file's IDENTITY, where it is, when it was last written, how
# big it is, and one `stat` decides whether what is remembered still describes the file. A
# settled reading costs about 3.5 us against about 36 us to read and digest the page again, on
# every response.
#
# The one rewrite this cannot see: a page replaced by one of exactly the same size inside the
# granularity of the filesystem's timestamp (two same-size writes well under a millisecond apart).
# A build writes the page once, takes seconds over it, and every name in it carries a content hash,
# so both halves of the identity move.


class _Page(NamedTuple):
    """One reading of the built page, and what identified the file it was read from."""

    identity: tuple[str, int, int] | None
    hashes: tuple[str, ...]
    build: str


#: Nothing built, or nothing readable. Both answers are the closed ones: a policy naming no inline
#: script allows none, and an empty build id reads as "there is nothing to compare against".
_NOTHING = _Page(identity=None, hashes=(), build="")

#: The last reading. A plain module-level name, rebound whole: the worst two threads can do is read
#: the page twice and agree. A lock would put every response behind it to save one 31 us read per
#: build.
_page: _Page = _NOTHING


def _identity() -> tuple[str, int, int] | None:
    """What tells this build of the page apart from the next one, in one `stat`.

    None where there is no page: a checkout nobody has built, or the moment during a rebuild when
    the file has been removed and not yet written.
    """
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
        # Caught mid-rebuild, or a page this process cannot read. Answer the closed way and do not
        # remember it: remembering would serve a blank app until something wrote the file a second
        # time, and the next request is a free retry.
        return _NOTHING
    fresh = _Page(
        identity=identity,
        hashes=tuple(dict.fromkeys(_HASH.findall(text))),
        build=hashlib.blake2b(data, digest_size=8).hexdigest(),
    )
    # No second stat to refuse a page that moved while it was read: a page caught half-written is
    # filed under the identity it was stat'ed at, the finished write leaves a different identity,
    # and the next request re-reads on that difference alone.
    _page = fresh
    return fresh


def script_hashes() -> tuple[str, ...]:
    """The hashes of the client's own inline scripts, read from the built page.

    The client bootstraps with a small inline script. The policy this server sends says `default-src
    'self'`, which forbids inline scripts, so unless that script is named, the browser refuses to
    start the app, and Sift serves a blank page to everyone.

    The build names it: it writes the hash of each inline script into a meta tag in the page. This
    reads them back so the header can allow exactly those, and nothing else. The alternative is
    'unsafe-inline', which allows every inline script, including one an injection put there, which
    is the attack the policy exists to stop.

    Follows the file. A page rebuilt under a running server is named by the next response; see the
    note above `_Page` for what that costs and for the one rewrite it cannot see.
    """
    return _current().hashes


def build_id() -> str:
    """Which BUILD of the client this server would serve, as a short digest of the page shell.

    ## The question this answers

    "Would reloading give me something different?" That is not the same question as the version
    number beside it. Two installs can carry the same version and different client builds, and in a
    browser the difference is invisible until somebody presses F5. In the desktop client there is no
    F5: the window keeps running the page it loaded until the application is restarted.

    ## Why the page shell is the right thing to hash

    Every file the build emits carries its own content hash in its NAME, and `index.html` is the one
    file that names them all. So its bytes change whenever any part of the client changes and stay
    identical when nothing has, which is exactly the property wanted, and it is a property of the
    build rather than a number somebody has to remember to bump.

    ONE source of truth, deliberately. The alternative is a build-time constant compiled into the
    client and a matching one computed here, which is two things that have to agree; this is the
    file itself, hashed by the only process that serves it.

    ## Read again when the page moves

    A client rebuilt under a running server is exactly the case this answers, so the id follows the
    file, on the same reading `script_hashes` uses.

    Not a secret and not a fingerprint worth hiding: it is a digest of a file every visitor has
    already downloaded in full. It is nonetheless behind the same signed-in check the version is,
    because it sits on that response.
    """
    return _current().build


def cache_control_for(path: str, asset: Path) -> str:
    """How long the browser may keep this one, without asking.

    Three answers, and which one a file gets is decided by where the build put it rather than by its
    extension. A hashed name is a promise that the content behind it is fixed; anything else is a
    name that will mean something different after the next upgrade, and has to be checked; and the
    page shell, which every address the client routes is answered with, is not kept at all (see
    `PAGE`). `asset` is the file the request is answered with, which is how an address like
    /browse is known to be the shell: an address the build has no file for is answered with
    `INDEX` itself. The shell asked for by its own name comes out of the listing, resolved.
    """
    if path.startswith(IMMUTABLE_PREFIX):
        return FOREVER
    if path.startswith(BRAND_PREFIX):
        return BRAND
    return PAGE if asset == INDEX or path == "index.html" else REVALIDATE


# --- which files the build produced, listed once per build -------------------------------------
#
# A request is answered from a list of the build's files made when the build was first seen, not by
# asking the filesystem where the requested path lands. Resolving a path reads the disk, on the
# event loop, for every script, style sheet and font of every page load; under heavy disk load one
# resolve would hold every request in the application for seconds. The list is made on a serving
# thread, it is made again only when the page shell changes (every build rewrites it, see `_Page`),
# and a request is then a lookup in memory.


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
    """Walk the build output and remember every file in it that stays inside it. Blocking.

    A link is neither listed nor walked into: a symlink or a junction in the output is how a name
    that reads as ordinary comes to point somewhere else. Every file that is listed is also resolved
    and proved inside the directory by `confine`, the one containment check, so whatever the walk
    misses the listing still cannot name a file outside the client.
    """
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
    """The file to serve for a request path, or None if the client should route it itself.

    Two kinds of request arrive here. One is for a file the build produced: a script, a font, the
    logo. The other is for a page: /browse, /asset/01HX..., anything the client's own router
    understands and the server has never heard of. Those get the page shell, and the client takes it
    from there. That is what makes a link to a filtered view survive being sent to someone and
    opened cold.

    The path arrives from the network and is only ever looked up, never joined: a string that is not
    exactly the name of a listed file, `..`, an absolute path, a drive, a name reached through a
    link, is not in the listing and gets the page shell, the same as any address the server does
    not recognize. See `list_files` for what is allowed into the listing. Nothing here reads the
    disk, so this is safe on the event loop.
    """
    if not listing.built:
        return None
    return listing.files.get(path, INDEX) if path else INDEX
