# SPDX-License-Identifier: AGPL-3.0-or-later
"""The faces decisions' lines on the record, worded when shown (`kernel.workbench.Recorded`).

Older titles are read back strictly for the facts only they hold (`_MATCHED_ONE`, `_GROUP`); the
person, the files and the counts come from the payload.
"""

from __future__ import annotations

import re

from sift.kernel.workbench import DOER, Named, Piece, Recorded, Worded

#: "Sift named <name> here, 75% sure": one match on one file.
_MATCHED_ONE = re.compile(r"Sift named .+ here, (?P<sure>\d{1,3})% sure")
#: "Sift named 14 more faces as <name>, between 56% and 63% sure": a re-match's run.
_MATCHED_RANGE = re.compile(
    r"Sift named [\d,]+ more faces? as .+, between (?P<low>\d{1,3})% and (?P<high>\d{1,3})% sure"
)
#: "Sift matched 344 more faces to <name>": a re-match's run, before it said how sure.
_MATCHED_MORE = re.compile(r"Sift matched [\d,]+ more faces? to .+")
#: "You agreed with 207 matches for <name>": a person confirming a run of Sift's matches.
_AGREED = re.compile(r"You agreed with [\d,]+ match(?:es)? for .+")
#: A No about one face in an older version's words ("1 face are not ..."); only that shape.
_REFUSED_ONE = re.compile(r"You said 1 face are not .+")
#: Every No about faces, worded from the payload so the person is the reader's to say.
_REFUSED = re.compile(r"You said [\d,]+ faces? (?:is|are) not .+")
#: An agreement about one face whose older detail said "those appearance "; only that phrase.
_AGREED_ONE = re.compile(r"You agreed with 1 (?:face|match) for .+")
_THOSE_APPEARANCE = "leaves those appearance "
#: "A group of 90 faces set aside", "discarded", "put back" or "restored", and "A group set aside".
_GROUP = re.compile(
    r"A group(?: of (?P<faces>[\d,]+) faces?)? (?P<act>set aside|discarded|put back|restored)"
)


def ignored_said(recorded: Recorded) -> Worded | None:
    """A group of faces discarded, or brought back. None where the title is not one it wrote."""
    held = recorded.held()
    pile_id = held.get("pile_id")
    found = _GROUP.fullmatch(recorded.title.strip())
    if not isinstance(pile_id, str) or not pile_id or found is None:
        return None
    faces = found["faces"]
    group = (
        "a group of faces"
        if faces is None
        else f"a group of {faces} {'face' if faces == '1' else 'faces'}"
    )
    verb = " restored " if found["act"] in ("put back", "restored") else " discarded "
    return Worded(said=(DOER, verb, Named(kind="pile", id=pile_id, recorded=group)))


def identified_said(recorded: Recorded) -> Worded | None:
    """A match Sift made, or a run of them somebody confirmed, as the act it was."""
    held = recorded.held()
    person_id = held.get("person_id")
    tracks = held.get("track_ids")
    if not isinstance(person_id, str) or not person_id or not isinstance(tracks, list):
        return None
    person = Named(kind="person", id=person_id, recorded=recorded.object_name)
    title = recorded.title.strip()
    faces = "1 face" if len(tracks) == 1 else f"{len(tracks):,} faces"
    more = "1 more face" if len(tracks) == 1 else f"{len(tracks):,} more faces"
    one = _MATCHED_ONE.fullmatch(title)
    if one is not None:
        files = [each for each in recorded.subjects if each.kind == "asset"]
        where: tuple[Piece, ...] = (
            (Named(kind="asset", id=files[0].id, recorded=files[0].name),)
            if len(files) == 1
            else (f"{len(files):,} files",)
        )
        return Worded(said=(DOER, " recognized ", person, " in ", *where, f", {one['sure']}% sure"))
    ranged = _MATCHED_RANGE.fullmatch(title)
    if ranged is not None:
        return Worded(
            said=(
                DOER,
                " recognized ",
                person,
                f" in {more}, {ranged['low']}% to {ranged['high']}% sure",
            )
        )
    if _MATCHED_MORE.fullmatch(title) is not None:
        return Worded(said=(DOER, " recognized ", person, f" in {more}"))
    if (
        _AGREED_ONE.fullmatch(title) is not None
        and len(tracks) == 1
        and _THOSE_APPEARANCE in recorded.detail
    ):
        # Read back rather than migrated: only the two words disagreed.
        return Worded(
            said=(DOER, " confirmed 1 face as ", person),
            more=(recorded.detail.replace(_THOSE_APPEARANCE, "leaves that appearance "),),
        )
    if _AGREED.fullmatch(title) is not None:
        return Worded(said=(DOER, f" confirmed {faces} as ", person))
    if _REFUSED_ONE.fullmatch(title) is not None and len(tracks) == 1:
        # The line under it too, each face's state from the payload.
        states = held.get("attribution")
        state = states.get(tracks[0]) if isinstance(states, dict) else None
        was = "waiting under Needs your input" if state == "suggested" else "Recognized by Sift"
        return Worded(
            said=(DOER, " said 1 face is not ", person),
            more=(
                f"It was {was}. Taking this back puts the name on it again, as it was, and lets "
                "Sift offer it there in future. No file is touched and nothing is deleted.",
            ),
        )
    if _REFUSED.fullmatch(title) is not None:
        # The count is the payload's faces; the writer's line under it is right.
        said = "1 face is" if len(tracks) == 1 else f"{len(tracks):,} faces are"
        return Worded(
            said=(DOER, f" said {said} not ", person),
            more=(recorded.detail,) if recorded.detail else (),
        )
    return None
