# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which stash-box answers a settled disagreement kept, in the words the History threads read.

One press on a disagreement writes two things: the answer kept (`stash_box_kept`, which the threads
draw as "Your title, X, was kept over PMVStash's Y") and a receipt with the Undo. The receipt
declares which answers it kept under the kernel's key (`vocabulary.RECEIPT_KEPT`), so the threads
say the press once (`history.one_line_per_kept`).

Its own module because two writers need the one rule and neither may import the other: the route
that writes a receipt, and the schema step that gives older receipts the same key
(`schema._declare_kept_answers`).
"""

from __future__ import annotations

from typing import Any

from sift.kernel.vocabulary import KEPT_BY_TAKING_ANOTHER, KEPT_BY_THE_PRESS


def kept_answers(recorded: dict[str, Any]) -> list[dict[str, str]]:
    """The answers one settle receipt kept, as `vocabulary.RECEIPT_KEPT` entries.

    Read off what every generation of receipt carries: `box_id` is the box whose answer "Keep
    yours" refused, and `set_aside` lists the boxes a "Take theirs" discarded (an older take has no
    `set_aside`, kept nothing, and answers an empty list). A payload that does not name its record
    and field names nothing.

    `platform` is read as `site`: the kept rows were rewritten to the new word (v14) and a receipt
    still saying the old one has to meet them under it.
    """
    said = str(recorded.get("subject") or "")
    subject = "site" if said == "platform" else said
    local_id, key = str(recorded.get("local_id") or ""), str(recorded.get("key") or "")
    if not subject or not local_id or not key:
        return []

    def entry(box_id: str, how: str) -> dict[str, str]:
        return {"subject": subject, "local_id": local_id, "box_id": box_id, "key": key, "how": how}

    box_id = recorded.get("box_id")
    if isinstance(box_id, str) and box_id:
        return [entry(box_id, KEPT_BY_THE_PRESS)]
    listed = recorded.get("set_aside")
    return [
        entry(one["box_id"], KEPT_BY_TAKING_ANOTHER)
        for one in (listed if isinstance(listed, list) else [])
        if isinstance(one, dict) and isinstance(one.get("box_id"), str) and one["box_id"]
    ]
