# SPDX-License-Identifier: AGPL-3.0-or-later
"""The naming words, as the kernel keeps them for every feature that names files.

What a download makes of them is tested in the download slice; this is the part both features
lean on: the number a taken name gets, and a template carried in a form no check reads as a path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.naming import Facts, fill, free_name, numbered, without_unsafe


def test_a_taken_name_gets_the_next_number_in_one_shape(tmp_path: Path) -> None:
    landed = tmp_path / "clip.mp4"
    landed.write_bytes(b"x")
    (tmp_path / "holiday.mp4").write_bytes(b"y")

    assert numbered("holiday", 1) == "holiday-1"
    assert free_name(landed, "holiday") == tmp_path / "holiday-1.mp4"


@pytest.mark.parametrize(
    "template",
    ["~{name}", "{creator}/{name}", "C:\\{site}\\{n}", "{title} :: {posted}", "..{name}"],
)
def test_a_template_without_its_unsafe_characters_fills_to_the_same_name(template: str) -> None:
    facts = Facts(site="Site", username="someone", original="clip", n=3, title="A title")

    carried = without_unsafe(template)

    assert fill(carried, facts) == fill(template, facts)
    assert not carried.startswith("~")
    assert "/" not in carried and "\\" not in carried
