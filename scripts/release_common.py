# SPDX-License-Identifier: AGPL-3.0-or-later
"""The paths, declarations and error every stage of `release.py` shares."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "vendor_manifest.json"
DESKTOP = ROOT / "desktop"
RUNTIME = ROOT / "build" / "runtime"


def _declared_python() -> str:
    """The interpreter version `.python-version` declares; a copy here would drift from it."""
    declared = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    if not declared:
        raise SystemExit(".python-version is empty, and it declares the interpreter to bundle")
    return declared


#: The interpreter the application ships and develops on.
PYTHON_VERSION = _declared_python()


class ReleaseFailed(RuntimeError):
    """A step did not succeed, said in one sentence. Nothing later is attempted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_key(version: str) -> tuple[int, int, int, int, str]:
    """Order versions as numbers, a pre-release below its release; `1.0.9` sorts after `1.0.10`."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?", version)
    if match is None:  # Unreachable from ARTEFACT, which has already matched the same shape.
        raise ReleaseFailed(f"{version!r} is not a version this script knows how to order")
    major, minor, patch, extra = match.groups()
    return (int(major), int(minor), int(patch), 0 if extra else 1, extra or "")
