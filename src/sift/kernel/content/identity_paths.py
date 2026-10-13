# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a file and everything built from it sit on disk, and the checks that keep a stored path inside its folder or the cache."""

from __future__ import annotations

import functools
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from sift.kernel.content.hashing import fingerprint
from sift.kernel.content.identity_models import DerivativeKind
from sift.kernel.content.mounts import storage_of
from sift.kernel.ids import is_id
from sift.kernel.paths import PathEscape, confine


def check_rel_path(rel_path: str) -> str:
    """A location's path, relative to its root, and nothing else: it stands between a request
    for an asset and any file on the machine. Judged under BOTH separators, whichever machine
    parses it; a `..` segment or a drive is refused, a backslash inside a name is allowed.
    """
    if not rel_path:
        raise ValueError("a location needs a path within its root")
    if rel_path.startswith(("/", "\\")):
        raise ValueError(f"{rel_path!r} is absolute; a location's path is relative to its root")
    if "\x00" in rel_path:
        raise ValueError("a path cannot contain a null byte")
    if PureWindowsPath(rel_path).drive:
        # `C:\...`, `C:...` and `\\host\share\...`. Joined on Windows, each throws the root away.
        raise ValueError(f"{rel_path!r} names a drive; a location's path is relative to its root")

    for segment in _SEPARATOR.split(rel_path):
        if segment in ("", ".", ".."):
            raise ValueError(f"{rel_path!r} is not a plain path within its root")

    return rel_path


@functools.lru_cache(maxsize=256)
def _resolved_root(root: str) -> tuple[Path, bool]:
    """A root's real path and whether it is on a share, once: a root does not move while Sift
    runs, and a share answers each resolve with round trips."""
    real = Path(root).resolve()
    return real, storage_of(real).remote


def _confine_to_root(candidate: Path, root: Path, location_id: str) -> Path:
    """The real, symlink-followed path, refused unless under the root; blocking, fail-closed.

    The caller opens the RESOLVED path, so a symlink swapped in after the check cannot move it.
    On a share the client does not follow the server's links, so resolving costs five round trips
    and proves nothing: the root once resolved, the file's own name, and one look at the file, a
    reparse point refused, stand for it."""
    real_root, remote = _resolved_root(str(root))
    if not remote:
        try:
            return confine(root, candidate)
        except PathEscape:
            raise ValueError(f"location {location_id} resolves outside its library root") from None
    inside = real_root.joinpath(*candidate.relative_to(root).parts)
    try:
        found = os.lstat(inside)
    except OSError:
        return inside
    linked = stat.S_ISLNK(found.st_mode) or bool(
        getattr(found, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    if linked:
        raise ValueError(f"location {location_id} resolves outside its library root")
    return inside


def _confine_to_cache(candidate: Path, cache_dir: Path) -> Path:
    """The same confinement for a derivative, whose root is the cache; blocking."""
    try:
        return confine(cache_dir, candidate)
    except PathEscape:
        raise ValueError("a derivative resolves outside the cache directory") from None


def _confined_cache_file(candidate: Path, cache_dir: Path) -> Path | None:
    """Confine the path AND answer whether the file is there, in ONE trip off the loop: the
    thread handoff costs more than the work, and both read the disk."""
    resolved = _confine_to_cache(candidate, cache_dir)
    return resolved if resolved.is_file() else None


# Both separators, always: the string came from another machine. See `check_rel_path`.
_SEPARATOR = re.compile(r"[/\\]")

_EXTENSION = re.compile(r"^[a-z0-9]{1,8}$")


def params_key(params: Mapping[str, Any] | None) -> str:
    """A derivative's settings, sorted and compact: half its unique key, so key order must not count."""
    return json.dumps(dict(params or {}), sort_keys=True, separators=(",", ":"))


def derivative_relpath(
    asset_id: str,
    kind: DerivativeKind,
    *,
    extension: str,
    params: Mapping[str, Any] | None = None,
) -> str:
    """Where a derivative belongs under the cache, `AB/CD/<asset id>/<kind>[-<settings>].<ext>`.

    Sharded on the END of the id, the random half of a ULID, so a bulk import spreads out.
    """
    if not is_id(asset_id):
        raise ValueError(f"{asset_id!r} is not an asset id")
    if not _EXTENSION.match(extension):
        raise ValueError(
            f"{extension!r} is not a file extension. It goes in a path, so it is deliberately "
            "restricted to lowercase letters and digits."
        )

    key = params_key(params)
    name = kind.value if key == "{}" else f"{kind.value}-{fingerprint(key)}"
    tail = asset_id[-4:]
    return f"{tail[:2]}/{tail[2:]}/{asset_id}/{name}.{extension}"
