# SPDX-License-Identifier: AGPL-3.0-or-later
"""A count in words: one of a thing is singular, none and many are plural."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from sift.kernel.access import sentences_pieces as say


@pytest.mark.parametrize(
    ("words", "one", "more"),
    [
        (say.files, "file", "files"),
        (say.people, "person", "people"),
        (say.tags, "tag", "tags"),
        (say.usernames, "username", "usernames"),
    ],
)
def test_one_is_singular_and_none_and_many_are_plural(
    words: Callable[[int], str], one: str, more: str
) -> None:
    assert [words(0), words(1), words(2), words(7726)] == [
        f"0 {more}",
        f"1 {one}",
        f"2 {more}",
        f"7,726 {more}",
    ]
