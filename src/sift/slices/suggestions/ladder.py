# SPDX-License-Identifier: AGPL-3.0-or-later
"""What to do about one folder, given what its name says and what its faces say.

A pure ladder, cheapest and strongest first, the first rung that fires winning. The top two write
without asking, resting on a name a person already gave (a face named on the file, or a person's own
name); the bottom offers on a name alone, and only once a pass has looked.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.attribution import FolderFaces
from sift.slices.suggestions.naming import Reading

#: How much of a folder one face group must account for, over the files with any face, before it
#: corroborates the folder's name; at three fifths no second group can also dominate.
DOMINANT_SHARE = 0.6

#: A half is dominance too, when nothing else in the folder comes close.
DOMINANT_SHARE_WITH_A_LEAD = 0.5

#: How many times the runner-up the leader must hold for that half to be enough.
DOMINANT_LEAD = 10

#: And it takes at least this many files: two matching photographs corroborate nothing.
DOMINANT_FLOOR = 3


class Action(StrEnum):
    """What the pass should do about this folder."""

    ATTRIBUTE = "attribute"
    """Write it. Nobody is asked, because nobody has to be (see the module docstring)."""

    OFFER = "offer"
    """Put the question on the review screen and write nothing until somebody answers."""

    WAIT = "wait"
    """Not enough is known yet. A pass has not finished looking at this folder."""

    NOTHING = "nothing"
    """There is no claim here, or there is one nothing could act on."""


class Evidence(StrEnum):
    """Why a claim exists, kept so a person can ask why they are being asked."""

    NAMED_GROUP = "named_group"
    KNOWN_NAME = "known_name"
    FACE_GROUP = "face_group"
    FILENAMES = "filenames"
    USERNAME_FOLDER = "username_folder"
    """The folder's own name says a username and the site it is on: `harlowquin (RedGifs)`.

    Its own reason because what it proposes is not a person and not a Site either: it is one
    USERNAME on a site, and answering yes files the folder under that username and writes nobody.
    The brackets are the whole evidence: a downloader that writes this shape writes it for every
    username it fetches, and without them the word in front would read as somebody's name."""
    KNOWN_IN_FILENAMES = "known_in_filenames"
    """Somebody this library already holds is named inside the files themselves.

    Its own reason because its standing is different from every other rung: the name was not worked
    out from a shape, it was recognised because a human had already decided that person exists. It
    still only ever OFFERS: a name appearing in a title is not proof the file is about them."""

    BY_HAND = "by_hand"
    """Somebody said so.

    Not worked out at all, and it is the most valuable row in the table for exactly that reason:
    every other reason here records a case the reader got RIGHT or a case somebody rejected. This is
    the only one that records a case the reader MISSED, and a miss leaves no trace of itself."""

    NAME_ONLY = "name_only"


@dataclass(frozen=True, slots=True)
class Verdict:
    """The one thing to do about one folder."""

    action: Action = Action.NOTHING
    evidence: Evidence | None = None

    #: Who, when the rung already knows; None on a rung proposing somebody who may not exist.
    person_id: str | None = None

    #: The name to propose, as the folder spells it; empty when the rung names nobody new.
    name: str = ""

    #: The face group the claim rests on, when it rests on one.
    group_id: str | None = None


def dominant(counts: Mapping[str, int], *, with_faces: int) -> str | None:
    """The one group or person that accounts for a clear majority here, or None, as for a tie."""
    if with_faces <= 0 or not counts:
        return None
    leader, seen = max(counts.items(), key=lambda pair: (pair[1], pair[0]))
    if seen < DOMINANT_FLOOR:
        return None
    # The runner-up is the next largest count; a tie for the top equals the leader.
    runner_up = sorted(counts.values(), reverse=True)[1] if len(counts) > 1 else 0
    alone = seen >= with_faces * DOMINANT_SHARE
    led = seen >= with_faces * DOMINANT_SHARE_WITH_A_LEAD and seen >= DOMINANT_LEAD * runner_up
    if not (alone or led):
        return None
    # A second group matching the leader is not a majority, whatever the numbers say.
    if sum(1 for count in counts.values() if count == seen) > 1:
        return None
    return leader


def decide(
    reading: Reading,
    faces: FolderFaces,
    *,
    files: int,
    named_by: Sequence[str],
    looking: bool,
    rejected: bool,
    owned_inside: Collection[str] = (),
    by_name_only: bool = False,
) -> Verdict:
    """What to do about this folder.

    Two people named by the folder's name link nobody. `looking` off offers a name-shaped folder at
    once. `owned_inside` and `by_name_only` bind only rung 1.
    """
    # 1. A face group somebody has already named accounts for this folder. It writes a whole
    # subtree, so never for a folder holding somebody else's own folder, nor one whose own name says
    # whose it is.
    already_named = None if by_name_only else dominant(faces.named, with_faces=faces.with_faces)
    if already_named is not None and not set(owned_inside) - {already_named}:
        return Verdict(
            action=Action.ATTRIBUTE, evidence=Evidence.NAMED_GROUP, person_id=already_named
        )

    if not reading.name or rejected:
        return Verdict()

    # 2. The name is already somebody's, or an alias they answer to.
    if len(named_by) == 1:
        return Verdict(
            action=Action.ATTRIBUTE,
            evidence=Evidence.KNOWN_NAME,
            person_id=named_by[0],
            name=reading.name,
        )
    if len(named_by) > 1:
        return Verdict()

    # 3. A name, and one unnamed group that accounts for the folder.
    group = dominant(faces.piles, with_faces=faces.with_faces)
    if group is not None:
        return Verdict(
            action=Action.OFFER,
            evidence=Evidence.FACE_GROUP,
            name=reading.name,
            group_id=group,
        )

    # 5. A name and no face evidence at all, but only once something has actually looked.
    if not looking:
        return Verdict(action=Action.OFFER, evidence=Evidence.NAME_ONLY, name=reading.name)
    if faces.looked_at < files:
        return Verdict(action=Action.WAIT)
    if faces.with_faces == 0:
        return Verdict(action=Action.OFFER, evidence=Evidence.NAME_ONLY, name=reading.name)

    # Looked at, faces found, none agreeing: a folder of many people, never one person.
    return Verdict()
