# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a named thing in the feed goes, held to a screen that draws it.

The feed's addresses are written on the server (`router._ADDRESS`), and the screens they land on are
the client's, so an address can go on answering while the page behind it goes away. A group of
faces is the case in point: `/organize/to-check/<id>`, the list the Faces tabs replaced, lands on
"There is nothing of that name here".
"""

from __future__ import annotations

import re
from pathlib import Path

from sift.slices.workbench.router import _href

#: The client's registry of which queue draws which screen, and what one item of it opens.
_PANELS = (
    Path(__file__).resolve().parents[5] / "frontend" / "src" / "lib" / "organize" / "panels.ts"
)

#: Where the client names its queues' addresses, which the registry above imports.
_ADDRESSES = _PANELS.parent / "addresses.ts"

#: And the client's own address for a group of faces, which a History line on a file already uses.
_HISTORY_TS = (
    Path(__file__).resolve().parents[5]
    / "frontend"
    / "src"
    / "lib"
    / "components"
    / "common"
    / "history.ts"
)


def test_a_group_of_faces_opens_the_screen_that_draws_one_group() -> None:
    href = _href("pile", "01HX0000000000000000000801")

    assert href == "/organize/faces-to-name/01HX0000000000000000000801"


def test_that_screen_is_one_the_client_draws_a_group_on() -> None:
    """The queue in the address must be one whose ITEM the client opens as a group of faces, and it
    must be the address the client's own History lines use: one kind, one place."""
    href = _href("pile", "x")
    assert href is not None
    queue = href.split("/")[2]
    panels = _PANELS.read_text(encoding="utf-8")
    addresses = _ADDRESSES.read_text(encoding="utf-8")
    named = re.search(r"const (\w+) = '" + re.escape(queue) + "';", addresses)
    assert named is not None, queue
    assert re.search(r"\[" + named.group(1) + r"\]: PileDetail", panels), queue
    assert f"/organize/{queue}/" in _HISTORY_TS.read_text(encoding="utf-8")


class _NoQueues:
    """A board that knows no queue, which is all `_receipt` asks of one here."""

    def reverser(self, queue: str) -> None:
        return None


def test_a_receipt_s_saved_title_is_said_in_today_s_words() -> None:
    """A title saved before a phrase was retired still carries it ("set aside") and the feed
    must not say it that way."""
    from typing import Any, cast

    from sift.kernel.access.history_events import LedgerEvent
    from sift.slices.workbench.router import _receipt

    event = LedgerEvent(
        id="01HX0000000000000000000802",
        at=1_700_000_000,
        verb="decided",
        actor_kind="sift",
        actor_id="faces",
        user_id=None,
        object=None,
        count=None,
        queue="faces",
        payload="{}",
        title="A group of 90 faces set aside",
        detail="",
        reversed_at=None,
    )

    said = _receipt(event, cast(Any, _NoQueues()))

    assert said is not None
    assert said.title == "A group of 90 faces discarded"
