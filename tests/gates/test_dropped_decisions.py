# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two things Sift deliberately does not do, kept out by a test rather than by memory.

Both were argued and dropped for reasons still true, and a dropped feature leaves no trace in the
code, so somebody thinking about the vault would reach for them again in good faith. A grep, to
make the whole area noisy to enter.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]


def _root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "sift").is_dir():
            return parent
    raise AssertionError("could not find the repository root from the test file")


#: Where an addition would land: the server, the browser client and their dependency manifests.
_SEARCHED = (
    "src/sift",
    "frontend/src",
    "frontend/e2e",
    "pyproject.toml",
    "frontend/package.json",
)

_SKIP_SUFFIXES = (".pyc", ".woff2", ".png", ".jpg", ".svg", ".ico")

#: The built client staged inside the Python package: bundled third-party code (the video library
#: mentions screen capture for its own reasons), rewritten by every build. `frontend/src` is read.
_SKIP_DIRS = ("web",)


def _sources() -> list[Path]:
    root = _root()
    found: list[Path] = []
    for entry in _SEARCHED:
        target = root / entry
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            found.extend(
                path
                for path in target.rglob("*")
                if path.is_file()
                and path.suffix not in _SKIP_SUFFIXES
                and "__pycache__" not in path.parts
                and not any(skipped in path.parts for skipped in _SKIP_DIRS)
                and path != Path(__file__)
            )
    assert found, "the search found no files at all, which means it is searching the wrong place"
    return found


def _hits(pattern: re.Pattern[str]) -> list[str]:
    root = _root()
    offenders: list[str] = []
    for path in _sources():
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover (binary or unreadable)
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    return offenders


# A browser tab cannot reliably tell that the screen is being captured: the proposal is no shipped
# standard, and approximations fail both ways, a false negative being worse than an honest absence.
# The vault, with lock triggers and the Show-Hidden toggle, does the job under the user's control.
_SCREEN_CAPTURE = re.compile(
    r"isScreenCaptured|is-screen-captured|screenCaptureDetect|screen_capture_detect"
    r"|getDisplayMedia|screen-?share\s*detect|detect.{0,12}screen.?(capture|shar)",
    re.IGNORECASE,
)

# Sift indexes files where they are and never moves them, so it cannot encrypt them without copying
# them elsewhere; whole-disk encryption is the machine's business. The vault is concealment and says
# so: encryption here would promise secrecy the code cannot honour.
_AT_REST_ENCRYPTION = re.compile(
    r"\bLUKS\b|cryptsetup|dm-crypt|encrypt.{0,20}at.?rest|at.?rest.{0,20}encrypt"
    r"|encrypt.{0,12}(the )?(media|vault|library|asset)|vault.{0,12}encrypt",
    re.IGNORECASE,
)


def test_nothing_tries_to_detect_screen_capture() -> None:
    """Dropped on purpose: if this fails, delete the detection rather than widen this."""
    offenders = _hits(_SCREEN_CAPTURE)
    assert not offenders, (
        "something here tries to notice that the screen is being captured or shared:\n  "
        + "\n  ".join(offenders)
        + "\nA browser cannot answer that reliably, and a privacy signal that is sometimes wrong "
        "is trusted exactly as much as one that is always right. The vault's lock triggers are "
        "the answer to this."
    )


def test_nothing_encrypts_media_at_rest() -> None:
    """Dropped on purpose too."""
    offenders = _hits(_AT_REST_ENCRYPTION)
    assert not offenders, (
        "something here reaches for encryption of the stored media:\n  "
        + "\n  ".join(offenders)
        + "\nFiles are read where they are and never moved, so their bytes cannot be encrypted "
        "without copying them elsewhere. The vault conceals and says so; it must not start "
        "claiming to encrypt."
    )


def test_the_search_would_notice_a_violation() -> None:
    """The patterns match planted text and the search reads real files, so the gates above cannot
    pass by reading nothing."""
    assert _SCREEN_CAPTURE.search("await navigator.mediaDevices.getDisplayMedia()")
    assert _SCREEN_CAPTURE.search("const shared = window.isScreenCaptured;")
    assert _AT_REST_ENCRYPTION.search("# set up LUKS on the media volume")
    assert _AT_REST_ENCRYPTION.search("def encrypt_the_vault(path):")
    assert len(_sources()) > 100
