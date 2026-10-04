# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rule about what a filename may be, which renaming a file and naming the editor's copy both
call. Refusals are asserted by the sentence they carry, which is what the person reads."""

from __future__ import annotations

import pytest

from sift.kernel.filenames import (
    MAX_FILENAME_LENGTH,
    InvalidFilename,
    check_filename,
    check_folder_name,
)


def test_an_ordinary_name_is_returned_trimmed() -> None:
    assert check_filename("  holiday.mp4  ") == "holiday.mp4"


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(InvalidFilename, match="Give the file a name"):
        check_filename("   ")


def test_a_name_at_the_cap_is_kept_and_one_over_it_is_refused() -> None:
    """The boundary: a name at the cap is kept, one over is refused."""
    assert check_filename("a" * MAX_FILENAME_LENGTH) == "a" * MAX_FILENAME_LENGTH

    with pytest.raises(InvalidFilename, match="too long"):
        check_filename("a" * (MAX_FILENAME_LENGTH + 1))


@pytest.mark.parametrize("name", ["holiday/clip.mp4", "holiday\\clip.mp4", "../clip.mp4"])
def test_anything_carrying_a_separator_is_refused(name: str) -> None:
    """Both separators are refused on every machine: the string came from a text box."""
    with pytest.raises(InvalidFilename, match="cannot contain a slash"):
        check_filename(name)


@pytest.mark.parametrize("name", ["clip\x00.mp4", "clip\n.mp4"])
def test_a_null_or_a_line_break_is_refused(name: str) -> None:
    """Neither survives a request's round trip, so they are asserted directly."""
    with pytest.raises(InvalidFilename, match="line breaks"):
        check_filename(name)


@pytest.mark.parametrize("name", [".", ".."])
def test_the_two_names_that_are_folders_are_refused(name: str) -> None:
    with pytest.raises(InvalidFilename, match="not a file name"):
        check_filename(name)


def test_a_drive_qualified_name_is_refused() -> None:
    """`C:clip.mp4` is refused: joined on Windows it replaces the root."""
    with pytest.raises(InvalidFilename, match="not a file name Sift can store"):
        check_filename("C:clip.mp4")


# --- the same rule, said about a folder


def test_a_folder_name_is_checked_exactly_as_a_file_name_is() -> None:
    """A folder name is checked exactly as a file name is, by one rule."""
    assert check_folder_name("  Holidays  ") == "Holidays"
    for refused in ("", "   ", "..", ".", "sub/folder", "sub\\folder", "C:folder", "a\x00b"):
        with pytest.raises(InvalidFilename):
            check_folder_name(refused)


def test_a_folder_refusal_says_folder() -> None:
    """A folder refusal says "folder", the only thing that differs."""
    with pytest.raises(InvalidFilename, match="folder"):
        check_folder_name("")
    with pytest.raises(InvalidFilename, match="file"):
        check_filename("")


@pytest.mark.parametrize("name", ["CON", "con", "PRN.mp4", "COM1", "LPT9", "nul.txt"])
def test_a_name_windows_reserves_is_refused_on_every_site(name: str) -> None:
    """A name Windows reserves is refused everywhere: libraries move between machines."""
    with pytest.raises(InvalidFilename, match="reserved"):
        check_filename(name)
    with pytest.raises(InvalidFilename, match="reserved"):
        check_folder_name(name)


@pytest.mark.parametrize("name", ["trailing.", "two..", "dot . "])
def test_a_name_ending_in_a_dot_is_refused(name: str) -> None:
    """A trailing dot is refused (Windows drops it silently); a trailing space is stripped first,
    so the last case proves the check still fires after it."""
    with pytest.raises(InvalidFilename, match="dot or a space"):
        check_filename(name)


def test_an_ordinary_name_with_a_dot_in_it_is_still_fine() -> None:
    """A dot inside a name is fine: the rule is about the END."""
    assert check_filename("holiday.clip.mp4") == "holiday.clip.mp4"
    assert check_folder_name("2024.summer") == "2024.summer"
