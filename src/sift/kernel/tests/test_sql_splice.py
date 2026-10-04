# SPDX-License-Identifier: AGPL-3.0-or-later
"""The template-and-fragments splice every SQL rule written once goes through."""

from __future__ import annotations

import pytest

from sift.kernel.sql_splice import splice


def test_a_statement_and_its_fragments_have_to_name_each_other() -> None:
    """Both directions, at the moment the module is imported and never at run time: a template
    handed a fragment it never names, and one left with a marker nothing filled. Either would
    otherwise run as SQL with a rule missing from it, and the known positive proves the check is
    reading the text at all."""
    assert splice("SELECT {{A}}", A="1") == "SELECT 1"
    with pytest.raises(RuntimeError, match=r"never names \{\{B\}\}"):
        splice("SELECT {{A}}", A="1", B="2")
    with pytest.raises(RuntimeError, match=r"still names \{\{B\}\}"):
        splice("SELECT {{A}} {{B}}", A="1")


def test_a_marker_written_inside_a_comment_is_refused_before_it_can_be_spliced() -> None:
    """Substitution is plain text and does not know what SQL is, so a marker named in a `--`
    comment, to SAY which fragment a column comes from, is replaced there too. The comment then
    swallows the fragment's first line and every line after it becomes live SQL in the middle of a
    SELECT list, so every request that reads the statement answers a syntax error.

    Refused whether or not the marker was going to be filled, because the damage is done by the
    substitution and not by the fragment: the check reads the template.
    """
    with pytest.raises(RuntimeError, match="written inside a comment"):
        splice("SELECT 1 -- the {{A}} rule lives here\nAND {{A}}", A="1")

    # A comment with no marker in it is left alone, and a marker before one on the same line is
    # not in the comment at all, which is the known positive that proves the check is reading
    # the position of the `--` rather than the presence of either.
    assert splice("SELECT {{A}} -- the rule", A="1") == "SELECT 1 -- the rule"
