# SPDX-License-Identifier: AGPL-3.0-or-later
"""What `Wire` promises, held to: a reply publishes every field as sent, and a request still says a
defaulted field is optional to supply, which is why one base serves both directions."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ConfigDict

from sift.kernel.access import sentences as say
from sift.kernel.access.catalog import EntityMaker
from sift.kernel.access.history import Actor, Detail, Event, Link, Undo
from sift.kernel.wire import HistoryLink, Wire, history_event, link_of, made_by_wire

pytestmark = pytest.mark.unit


class Reply(Wire):
    """A field of each kind a model has: plain, defaulted, and nullable-with-a-default."""

    id: str
    count: int = 0
    name: str | None = None


class Strict(Wire):
    """One that adds a setting of its own, which must not cost it the one it inherited."""

    model_config = ConfigDict(extra="forbid")

    id: str
    count: int = 0


def test_a_field_with_a_default_is_still_described_as_sent() -> None:
    """The whole point. The server serialises every field, so every field is in the reply."""
    schema = Reply.model_json_schema(mode="serialization")

    assert set(schema["required"]) == {"id", "count", "name"}


def test_a_nullable_field_is_described_as_sent_and_still_allowed_to_be_null() -> None:
    """Sent is not the same as filled in, and the difference is what a reader checks for."""
    schema = Reply.model_json_schema(mode="serialization")

    assert "name" in schema["required"]
    assert {"type": "null"} in schema["properties"]["name"]["anyOf"]


def test_what_a_request_needs_is_left_exactly_as_it_was() -> None:
    """Why one base serves both directions.

    The setting reaches the serialisation schema and nothing else, so a field with a default is
    still optional to SUPPLY. If this ever stopped holding, request models would need a base of
    their own, and somebody would eventually put a model under the wrong one.
    """
    schema = Reply.model_json_schema(mode="validation")

    assert schema["required"] == ["id"]


def test_a_setting_of_its_own_merges_rather_than_replacing() -> None:
    """Pydantic merges config down the chain, and five models in the tree rely on it."""
    assert Strict.model_config["extra"] == "forbid"
    assert Strict.model_config["json_schema_serialization_defaults_required"] is True
    assert set(Strict.model_json_schema(mode="serialization")["required"]) == {"id", "count"}


# --- the three translations that live here -----------------------------------------------
#
# Field for field, in one place, so that three slices answering with the same shape cannot answer
# with three different ones. Tested here for exactly that reason: the bug these can have is a field
# that stops being carried, and nothing about a dropped field fails to import or fails a schema:
# the browser simply draws a blank where a name belongs.


def test_a_maker_crosses_the_wire_whole() -> None:
    """Every field, and the actor as its own word rather than as the enum."""
    made = made_by_wire(
        EntityMaker(
            actor=Actor.STASH_BOX,
            via="stash",
            box_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            box_name="A Box",
            box_slug="a-box",
        )
    )

    assert made.model_dump() == {
        "kind": "stash_box",
        "via": "stash",
        "act": None,
        "box_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "box_name": "A Box",
        "box_slug": "a-box",
    }
    # The act a produced file was made by crosses too: the screen draws that verb's glyph from it.
    produced = made_by_wire(EntityMaker(actor=Actor.SIFT, via="produced", act="compress"))
    assert produced.model_dump() == {
        "kind": "sift",
        "via": "produced",
        "act": "compress",
        "box_id": None,
        "box_name": None,
        "box_slug": None,
    }


def test_an_event_carries_its_links_its_groups_and_its_receipt() -> None:
    """A folded line is the hard case: the sentence's own links, the groups it stands for, and the
    receipt that can take it back all have to survive one translation."""
    event = Event(
        at=1_700_000_000,
        actor=Actor.SIFT,
        actor_name=None,
        kind="named",
        pieces=say.said("Sift named ", say.thing("person", "p1", "Ada Lumen"), " here"),
        undo=Undo(kind="decision", id="01ARZ3NDEKTSV4RRFFQ69G5FAW"),
        reversed=True,
        detail=(
            Detail(
                kind="person",
                words="2 people",
                links=(
                    Link(kind="person", id="p1", name="Ada Lumen"),
                    Link(
                        kind="person", id="p2", name="Bex Corrow", href="/organize/known-people/p2"
                    ),
                ),
            ),
        ),
        via="faces",
        how=None,
        receipt="01ARZ3NDEKTSV4RRFFQ69G5FAX",
    )

    crossed = history_event(event)

    assert crossed.actor == "sift"
    assert crossed.reversed is True
    assert crossed.undo is not None
    assert (crossed.undo.kind, crossed.undo.id) == ("decision", "01ARZ3NDEKTSV4RRFFQ69G5FAW")
    # The line crosses as its pieces, each thing where it sits; the words are read off them.
    assert crossed.what == "Sift named Ada Lumen here"
    assert [(one.text, one.kind, one.id) for one in crossed.pieces] == [
        ("Sift named ", None, None),
        ("Ada Lumen", "person", "p1"),
        (" here", None, None),
    ]
    assert crossed.means == "A person named in a file"
    assert [group.words for group in crossed.detail] == ["2 people"]
    assert [one.href for one in crossed.detail[0].entries] == [None, "/organize/known-people/p2"]
    assert crossed.receipt == "01ARZ3NDEKTSV4RRFFQ69G5FAX"
    assert crossed.via == "faces"


def test_an_event_with_nothing_to_take_back_carries_no_undo_point() -> None:
    """The null case is the one a client branches on, so it is worth stating: no undo, no links, no
    groups, not an empty object standing in for any of them."""
    crossed = history_event(
        Event(
            at=None,
            actor=Actor.SOMEBODY,
            actor_name=None,
            kind="added",
            pieces=say.said("Added"),
        )
    )

    assert crossed.undo is None
    assert [one.text for one in crossed.pieces] == ["Added"]
    assert crossed.detail == []
    assert crossed.at is None


def test_a_link_crosses_the_wire_field_for_field() -> None:
    """`link_of` is the ONE translation of a kernel link, and it drops nothing.

    A History line, the board's first question and a Shoots card all send the names their
    sentences say through it. A route that listed the fields itself would send `kind`, `id` and
    `name` and quietly lose any field added to the shape later (`href`, `gone`), so this holds
    the two shapes to the same fields and every value to its copy.
    """
    link = Link(kind="file", id="asset-9", name="clip.mp4", href="/browse?in=x", gone=True)

    sent = link_of(link)

    assert set(HistoryLink.model_fields) == {one.name for one in dataclasses.fields(Link)}
    assert sent.model_dump() == dataclasses.asdict(link)
