# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a safe name is, in the one place everything that accepts one reads it from.

Several features let somebody type a name that becomes part of a path: renaming a file, naming the
copy the editor is about to write, and making or renaming a folder. All of them need the same
answer, and the rule belongs to none of them: it is not about renaming and it is not about
editing, it is about what may be joined onto a folder path without leaving that folder.

**One rule, two nouns.** A file and a folder are the same question to a filesystem, so there is one
check and the only difference is the word in the sentence somebody reads. Written as two rules they
would drift, and the one that drifted would be the one nobody was looking at.

Kept out of `kernel/text.py` on purpose. That module decides what counts as text at all, for the
names of tags and People, and its rules are about storing something that can be found again. These
rules are about a path staying inside the folder a permission was resolved against. One module
holding both would put them a line apart and invite calling the wrong one.

The refusal raised here is the kernel's own, and each feature catches it and re-raises its own,
so the routes keep the status codes they already answer with, and the sentences come from one
place.
"""

from __future__ import annotations

from pathlib import PureWindowsPath

__all__ = [
    "MAX_FILENAME_LENGTH",
    "InvalidFilename",
    "check_filename",
    "check_folder_name",
]

#: The longest name a file may be given. Well under the 255 bytes most filesystems allow, because
#: the limit is on bytes and a name of accented or non-Latin characters is several bytes a
#: character: a cap counted in characters has to leave room for that or it fails on real names.
MAX_FILENAME_LENGTH = 200


class InvalidFilename(ValueError):
    """The text somebody typed is not a name, and the message says why.

    Written for the person who typed it. A feature catching this re-raises it as its own refusal
    so that its route answers with the status it already answers with, and the sentence travels
    unchanged.
    """


#: Names Windows will not give anything, whatever the extension. `CON.mp4` is as refused as `CON`.
#:
#: Refused rather than left to fail, because what a person gets otherwise is the operating system's
#: own error in the middle of an operation that has already half happened. They exist on every
#: site's list here on purpose: a library is copied between machines and restored onto them,
#: and a name that is fine on one and impossible on another is a file somebody loses in the move.
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in "123456789"}
    | {f"LPT{digit}" for digit in "123456789"}
)


def _check_segment(name: str, *, noun: str) -> str:
    """One part of a path, and nothing else. Never a path itself.

    This is the whole of what stands between a text box and the rest of the disk. A `/` or a `\\`
    in it makes it a path rather than a name, and `..` walks up out of the folder the permission was
    resolved against, so both are refused here rather than being resolved into something harmless
    later, where whether they are harmless depends on which code got them.

    Both separators, on every site. Which one is "the" separator is a property of the machine
    parsing the string, not of the string, and the string came from a text box.

    Nothing is said about an extension. For a rename that is right: somebody may call `clip.mp4`
    something ending `.mkv` and be wrong on their own terms. A caller for whom the extension is not
    the person's to choose keeps that rule where the extension is decided.
    """
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
        # `C:clip.mp4`. On Linux that is an ordinary filename with a colon in it, which is why the
        # slash check above lets it through, but joined on Windows it replaces the root rather
        # than extending it, so the check that guards a stored path refuses it. Refusing it here
        # too keeps the two agreeing: a name this accepts must be a name that can be stored, or the
        # file moves on disk and the row that describes it cannot be written.
        raise InvalidFilename(f"That name is not a {noun} name Sift can store.")
    if cleaned.endswith((".", " ")):
        # Windows drops both silently rather than refusing them, so the thing that gets made is not
        # the thing that was asked for, and the row Sift writes then names a path that does not
        # exist. Refused, so what is stored and what is on the disk cannot disagree.
        raise InvalidFilename(f"A {noun} name cannot end with a dot or a space.")
    if cleaned.split(".")[0].upper() in _RESERVED:
        raise InvalidFilename(f"That name is reserved, so it cannot be used for a {noun}.")
    return cleaned


def check_filename(name: str) -> str:
    """A file's name, and nothing else. Never a path."""
    return _check_segment(name, noun="file")


def check_folder_name(name: str) -> str:
    """A folder's name, and nothing else. Never a path.

    The same rule as a file's, because a filesystem asks the same question of both. Only the
    sentence differs, so that somebody who typed a slash into a folder name is told about folders.
    """
    return _check_segment(name, noun="folder")
