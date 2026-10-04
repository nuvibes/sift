# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failed comparison of long bytes prints a length and a digest, never the bytes."""

from __future__ import annotations

import os

import pytest
from blake3 import blake3

from sift.testing.bytes_compared import SHOWN_WHOLE, pytest_assertrepr_compare


def _digest(data: bytes) -> str:
    return blake3(data).hexdigest()[:16]


@pytest.mark.unit
def test_a_received_file_missing_from_a_dict_is_told_by_its_digest() -> None:
    sent = os.urandom(5 * SHOWN_WHOLE)

    lines = pytest_assertrepr_compare("==", {}, {"a": sent})

    assert lines is not None
    shown = "\n".join(lines)
    assert f"<{len(sent)} bytes, blake3 {_digest(sent)}>" in shown
    assert "\\x" not in shown and len(shown) < 200


@pytest.mark.unit
def test_two_files_that_differ_are_told_apart_and_only_the_differing_key_is_named() -> None:
    same, mine, theirs = (os.urandom(SHOWN_WHOLE + 1) for _ in range(3))

    lines = pytest_assertrepr_compare("==", {"a": same, "b": mine}, {"a": same, "b": theirs})

    assert lines is not None
    assert lines[1:] == [
        f"'b': <{len(mine)} bytes, blake3 {_digest(mine)}> != "
        f"<{len(theirs)} bytes, blake3 {_digest(theirs)}>"
    ]


@pytest.mark.unit
def test_long_bytes_inside_a_list_or_on_one_side_alone_are_told_too() -> None:
    data = os.urandom(SHOWN_WHOLE * 2)

    lines = pytest_assertrepr_compare("==", [b"short", data], data[:-1])

    assert lines is not None
    assert "\\x" not in "\n".join(lines).replace("b'short'", "")


@pytest.mark.unit
def test_a_comparison_without_long_bytes_is_left_to_pytest() -> None:
    assert pytest_assertrepr_compare("==", {"a": b"x" * SHOWN_WHOLE}, {}) is None
    assert pytest_assertrepr_compare("!=", os.urandom(500), os.urandom(500)) is None


@pytest.mark.unit
def test_the_whole_session_explains_long_bytes_this_way(request: pytest.FixtureRequest) -> None:
    """Registered for every test, and asked before pytest's own explanation."""
    sent = os.urandom(1000)

    answers = request.config.hook.pytest_assertrepr_compare(
        config=request.config, op="==", left={}, right={"a": sent}
    )

    first = next(answer for answer in answers if answer)
    assert first[0] == f"{{}} == {{'a': <1000 bytes, blake3 {_digest(sent)}>}}"
