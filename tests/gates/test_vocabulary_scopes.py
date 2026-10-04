# SPDX-License-Identifier: AGPL-3.0-or-later
"""A verb scoped to some screens, and a name that keeps its capital: the Python half.

The client reads the same file the same way (`frontend/scripts/lib/vocabulary.js`), and its
tests are `frontend/src/lib/design/vocabulary-scopes.test.ts`.
"""

from __future__ import annotations

import pytest

from tests.gates import server_copy, vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SWAP = "lib/components/swap/SwapProgress.svelte"
RECONCILE = "lib/components/organize/ReconcilePanel.svelte"
ELSEWHERE = "lib/components/organize/DisagreementsPanel.svelte"


@pytest.mark.parametrize(
    ("label", "where", "allowed"),
    [
        ("End the swap", SWAP, True),
        ("They match", SWAP, True),
        ("Take theirs", RECONCILE, True),
        ("End the task", ELSEWHERE, False),
        ("Start again", ELSEWHERE, False),
        ("Take this off", ELSEWHERE, False),
        ("Take theirs", "", False),
        ("Remove this file", ELSEWHERE, True),
        ("Remove this file", "", True),
    ],
)
def test_a_scoped_verb_opens_a_label_on_its_own_screens_only(
    label: str, where: str, allowed: bool
) -> None:
    assert vocabulary.starts_with_a_verb(label, where) is allowed


def test_every_verb_scope_names_a_scope_that_exists() -> None:
    table = vocabulary.load()["verbs"]
    scoped = [one for one in (*table["allowed"], *table["also_allowed"]) if "scope" in one]
    assert {one["verb"] for one in scoped} >= {"Start", "Join", "End", "Take", "They match"}
    missing = [one["verb"] for one in scoped if one["scope"] not in vocabulary.load()["scopes"]]
    assert not missing, f"a verb names a scope vocabulary.json does not have: {missing}"


def test_a_name_without_its_capital_is_counted_in_the_server_copy() -> None:
    """Counted per file as a ratchet, over every string the server writes, the sentence tables
    included, and never for the name itself, a key or a template's gap."""
    assert "lower_case_names" in vocabulary.CHECKS
    found = server_copy.copy_in('raise SiteRefused("That site has no cookies.")\n', "src/sift/x.py")
    assert [vocabulary.offences("lower_case_names", one.text) for one in found] == [
        [("site", "Site, or Sites")]
    ]
    for text in (
        "That Site",
        "Sites within",
        "site",
        "{site}",
        "/sites/x",
        "website",
        "Photo Sets",
    ):
        assert vocabulary.offences("lower_case_names", text) == [], text
    assert vocabulary.offences("lower_case_names", "a photo set's name")
