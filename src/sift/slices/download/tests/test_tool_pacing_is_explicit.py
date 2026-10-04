# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every value Sift decides about a tool run is handed to the tool on every run, even where it
matches the tool's default today: an unset default changes when the dependency updates. A value not
handed over, or an option dropped from a command, fails here.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest

from sift.slices.download.sources.argv import (
    CONCERNS,
    Shape,
    build_gallerydl_argv,
    build_ytdlp_argv,
)
from sift.slices.download.sources.policy import READ_OFF_A_SETTING
from sift.slices.download.sources.tuning import (
    PACING,
    POLICY,
    Filters,
    Pacing,
    RunPolicy,
)

_BUILDERS = {"ytdlp": build_ytdlp_argv, "gallerydl": build_gallerydl_argv}


def _leaves(shape: type, prefix: str = "") -> set[str]:
    """Every decided value on the policy, as a dotted path into its nested pieces."""
    found: set[str] = set()
    for field in fields(shape):
        path = f"{prefix}{field.name}"
        inner: Any = field.type
        if inner in (Pacing, Filters) or (isinstance(inner, type) and is_dataclass(inner)):
            found |= _leaves(inner, f"{path}.")
        elif field.name in ("pacing", "filters"):
            found |= _leaves(Pacing if field.name == "pacing" else Filters, f"{path}.")
        else:
            found.add(path)
    return found


def test_every_value_sift_decides_is_handed_to_the_tools() -> None:
    """Every value Sift decides is handed to the tools. `quality` chooses between two orderings and
    is tested beside the allow-list; `policy.READ_OFF_A_SETTING` names values about other values."""
    named = {concern.value for concern in CONCERNS}
    assert _leaves(RunPolicy) - {"quality"} - READ_OFF_A_SETTING.keys() == named
    assert not READ_OFF_A_SETTING.keys() & named, "a value read off a setting is not told twice"


@pytest.mark.parametrize("tool", sorted(_BUILDERS))
def test_every_command_carries_every_concern_that_has_a_value(tool: str, tmp_path: Path) -> None:
    command = _BUILDERS[tool]("https://example.com/x", tmp_path)
    for concern in CONCERNS:
        flag = getattr(concern, tool)
        if flag is None:
            # Said out loud in the table rather than quietly missing: this tool has no such option.
            continue
        if concern.shape in (Shape.SWITCH, Shape.OPTIONAL, Shape.SIZE):
            # Off by default, and off means the option is absent rather than passed as a nothing.
            continue
        assert flag in command, f"{tool} runs without {concern.value} being set"


@pytest.mark.parametrize("tool", sorted(_BUILDERS))
def test_a_different_policy_reaches_the_command(tool: str, tmp_path: Path) -> None:
    """The values are an argument, not something read while a command is assembled, which is the
    whole of what it took to put them on a settings screen."""
    slower = RunPolicy(
        pacing=Pacing(
            seconds_between_requests=9.5,
            retries=1,
            timeout_seconds=99.0,
            wait_after_too_many_requests=5.0,
            bytes_per_second=512_000,
        ),
        filters=Filters(at_least_bytes=2048, at_most_bytes=4096),
        verbose=True,
    )
    command = _BUILDERS[tool]("https://example.com/x", tmp_path, policy=slower)
    for concern in CONCERNS:
        flag = getattr(concern, tool)
        if flag is None:
            continue
        assert flag in command
        if concern.shape is not Shape.SWITCH:
            value = command[command.index(flag) + 1]
            assert value not in ("", "None")


def test_the_pacing_is_a_real_pause_rather_than_none() -> None:
    """Zero is what both tools do on their own, and passing it explicitly would look like a decision
    while being the same unpaced traffic that made this necessary."""
    assert PACING.seconds_between_requests > 0
    assert PACING.wait_after_too_many_requests > 0
    # Sift retries the whole download as well, so a tool's own retries multiply with those. The
    # tools' defaults are ten and four; underneath three attempts that is up to thirty tries.
    assert PACING.retries <= 3


def test_nothing_is_filtered_or_capped_out_of_the_box() -> None:
    """A downloader that silently drops files, or crawls, before anybody asked it to is not one
    anybody would trust. Every bound starts off."""
    assert POLICY.pacing.bytes_per_second is None
    assert POLICY.filters.at_least_bytes is None
    assert POLICY.filters.at_most_bytes is None
    assert POLICY.verbose is False
