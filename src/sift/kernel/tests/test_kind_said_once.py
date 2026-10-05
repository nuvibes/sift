# SPDX-License-Identifier: AGPL-3.0-or-later
"""A thing with no name is said by its kind once, on a page and in the feed: never "the tag a tag"."""

from __future__ import annotations

import re

import pytest

from sift.kernel.access import sentences as say
from sift.kernel.access.sentences_feed import FEED
from sift.kernel.access.sentences_ledger import A_THING, mention
from sift.kernel.vocabulary import Subject

pytestmark = pytest.mark.unit

_TWICE = re.compile(r"\bthe (\w[\w -]*?) an? \1\b")
_VERBS = sorted({*FEED, "linked", "unlinked", "added", "removed", "deleted"})
_ID = "01HX0000000000000000000901"


def _nameless(kind: str) -> Subject:
    return Subject(kind=kind, id=_ID, name=None)  # type: ignore[arg-type]


def _lines(verb: str, kind: str) -> list[str]:
    """The act with a nameless thing of this kind in every slot it can stand in; a song named is
    given its song, which its payload always carries."""
    payload = {"song": "seagrass"}
    file = Subject(kind="asset", id="01HX0000000000000000000902", name="a.mp4")
    person = Subject(kind="person", id="01HX0000000000000000000903", name="Delphine Ostrow")
    return [
        say.event_said(
            verb,
            by="You",
            here=say.VANTAGE_FILE,
            object_kind=kind,
            object_id=_ID,
            subjects=[file],
            payload=payload,
        ).what,
        say.event_said(
            verb,
            by="You",
            here=say.VANTAGE_PERSON,
            object_kind="person",
            object_id=person.id,
            subjects=[_nameless(kind)],
            from_object=True,
            payload=payload,
        ).what,
        say.feed_line(
            verb,
            by="You",
            subjects=[("asset", mention(file))],
            object_kind=kind,
            object_piece=mention(_nameless(kind)),
            payload=payload,
        ).what,
        say.feed_line(
            verb,
            by="You",
            subjects=[(kind, mention(_nameless(kind)))],
            object_kind="person",
            object_piece=mention(person),
            payload=payload,
        ).what,
    ]


@pytest.mark.parametrize("kind", sorted(A_THING))
def test_no_act_says_a_nameless_thing_s_kind_twice(kind: str) -> None:
    twice = {line for verb in _VERBS for line in _lines(verb, kind) if _TWICE.search(line)}
    assert not twice


def test_a_named_thing_keeps_its_kind_before_it() -> None:
    tag = Subject(kind="tag", id=_ID, name="poolside")
    file = Subject(kind="asset", id="01HX0000000000000000000902", name="a.mp4")
    assert (
        say.feed_line(
            "unlinked",
            by="You",
            subjects=[("asset", mention(file))],
            object_kind="tag",
            object_piece=mention(tag),
        ).what
        == "You removed the tag poolside from a.mp4"
    )
    assert (
        say.feed_line(
            "unlinked",
            by="You",
            subjects=[("asset", mention(file))],
            object_kind="tag",
            object_piece=mention(_nameless("tag")),
        ).what
        == "You removed a tag from a.mp4"
    )
