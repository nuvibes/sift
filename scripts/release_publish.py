# SPDX-License-Identifier: AGPL-3.0-or-later
"""The publish stage's reads: the GitHub CLI, its API answers, and a minisign signature's test."""

from __future__ import annotations

import base64
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from release_common import ReleaseFailed, _sha256


def signature_is_good(content: bytes, signature: str, key_base64: str) -> bool:
    """The shell's own test of a plain minisign signature by this key; prehashed is refused."""
    lines = [line for line in signature.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    try:
        raw = base64.b64decode(lines[1].strip(), validate=True)
        key = base64.b64decode(key_base64, validate=True)
    except ValueError:
        return False
    if len(raw) != 74 or raw[:2] != b"Ed" or len(key) != 42 or raw[2:10] != key[2:10]:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(key[10:]).verify(raw[10:], content)
    except InvalidSignature:
        return False
    return True


class GhRun(NamedTuple):
    """What one run of the GitHub CLI answered."""

    exit_code: int
    output: str
    error: str


#: How the publish step runs the GitHub CLI: its arguments, and what goes to its standard input. A
#: seam, so the publish step is tested with a stand-in that records every call.
Gh = Callable[[list[str], str | None], GhRun]


def gh_program() -> str:
    """The GitHub CLI that publishes: `RELEASE_GH` (one program), else `gh` on PATH."""
    named = os.environ.get("RELEASE_GH", "").strip() or "gh"
    found = shutil.which(named)
    if found is None:
        raise ReleaseFailed(f"{named} cannot be found. Install the GitHub CLI or set RELEASE_GH.")
    return found


def _github(gh: Gh, path: str) -> dict[str, object] | None:
    """One read of GitHub's API: the document, or None when GitHub answers 404."""
    # A query goes as fields: a `&` on the line would end the command in a Windows shell.
    route, _, query = path.partition("?")
    fields = [part for pair in query.split("&") if pair for part in ("-f", pair)]
    done = gh(["api", *(["--method", "GET"] if fields else []), route, *fields], None)
    if done.exit_code != 0:
        if "HTTP 404" in done.error or "Not Found" in done.error:
            return None
        raise ReleaseFailed(f"gh api {path} failed ({done.exit_code}):\n{done.error.strip()}")
    try:
        answer = json.loads(done.output)
    except json.JSONDecodeError:
        answer = None
    if not isinstance(answer, dict):
        raise ReleaseFailed(f"gh api {path} answered something that is not a JSON object.")
    return answer


def missing_from(existing: dict[str, object], files: list[Path], tag: str) -> list[str]:
    """What a release on GitHub lacks of `files`; a file there with other bytes stops everything."""
    held: dict[str, dict[str, object]] = {}
    assets = existing.get("assets")
    for asset in assets if isinstance(assets, list) else []:
        if isinstance(asset, dict) and isinstance(asset.get("name"), str):
            held[str(asset["name"])] = asset
    missing: list[str] = []
    for one in files:
        asset = held.get(one.name)
        if asset is None:
            missing.append(one.name)
            continue
        recorded = asset.get("digest")
        if not isinstance(recorded, str) or not recorded.startswith("sha256:"):
            raise ReleaseFailed(
                f"{tag} already carries {one.name} and GitHub records no SHA-256 for it, so it "
                "cannot be compared with this build. Compare it by hand or remove it."
            )
        if recorded.removeprefix("sha256:").lower() != _sha256(one):
            raise ReleaseFailed(
                f"{tag} already carries {one.name} with different bytes. A published file is "
                "never replaced; a different build is a different version."
            )
    return missing
