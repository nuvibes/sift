# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a safe name for a file or folder is: one rule that keeps a path inside its folder."""

from __future__ import annotations

from pathlib import PureWindowsPath

__all__ = [
    "MAX_FILENAME_LENGTH",
    "InvalidFilename",
    "check_filename",
    "check_folder_name",
]

#: Under the 255-byte limit, with room for characters that take several bytes each.
MAX_FILENAME_LENGTH = 200


class InvalidFilename(ValueError):
    """The typed text is not a name; the message is for the person who typed it."""


#: Names Windows refuses whatever the extension; refused everywhere, as libraries move.
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in "123456789"}
    | {f"LPT{digit}" for digit in "123456789"}
)


def _check_segment(name: str, *, noun: str) -> str:
    """One part of a path and never a path: separators and `..` would leave the folder."""
    cleaned = name.strip()
    if not cleaned:
        raise InvalidFilename(f"Give the {noun} a name.")
    if len(cleaned) > MAX_FILENAME_LENGTH:
        raise InvalidFilename(
            f"That name is too long. Keep it under {MAX_FILENAME_LENGTH} characters."
        )
    if "/" in cleaned or "\\" in cleaned:
        raise InvalidFilename(
            f"A {noun} name cannot contain a slash. To put the {noun} somewhere else, move it."
        )
    if "\x00" in cleaned or "\n" in cleaned:
        raise InvalidFilename(f"A {noun} name cannot contain line breaks.")
    if cleaned in (".", ".."):
        raise InvalidFilename(f"That is not a {noun} name.")
    if PureWindowsPath(cleaned).drive:
        # `C:name` replaces the root on Windows; refused so a name accepted can be stored.
        raise InvalidFilename(f"That name is not a {noun} name Sift can store.")
    if cleaned.endswith((".", " ")):
        # Windows drops both silently, so the disk and the stored row would disagree.
        raise InvalidFilename(f"A {noun} name cannot end with a dot or a space.")
    if cleaned.split(".")[0].upper() in _RESERVED:
        raise InvalidFilename(f"That name is reserved, so it cannot be used for a {noun}.")
    return cleaned


def check_filename(name: str) -> str:
    """A file's name, and nothing else. Never a path."""
    return _check_segment(name, noun="file")


def check_folder_name(name: str) -> str:
    """A folder's name, and nothing else. Never a path."""
    return _check_segment(name, noun="folder")
