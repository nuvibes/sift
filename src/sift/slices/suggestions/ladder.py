# SPDX-License-Identifier: AGPL-3.0-or-later
"""What to do about one folder, given what its name says and what its faces say.

Cheapest and strongest first, and the first rung that fires wins: a folder makes at most one
claim about who it is. Everything here is a pure decision over values the caller has already
gathered, so the ordering can be read in one place and tested without a database.

The two ends of the ladder are the interesting ones.

**The top two rungs write without asking.** A folder whose files already carry one face group
somebody has NAMED, and a folder whose name is already the name of somebody in the library, are
both resting on a judgement a human has already made. Nothing is created and nothing is invented;
the attribution is carried from where it was decided to where it obviously also applies. That is
what makes a large library collapse in one pass instead of asking a thousand questions.

**Named means named on the file**, by the one rule for that: a face somebody confirmed, or one
recognized above the line at which Sift puts the name on the file without asking. A face recognized
that way rests on the pictures somebody confirmed as that person (here or, for descriptions a swap
brought, on the other install), and the file already carries the name, so it counts. A face Sift is
only ASKING about is not a name: it puts nobody on its own file, and it may not give away a folder.

**The bottom rung offers on a name alone**, and only once a pass has actually looked. Body-only
content, back shots and art carry no face and are a large share of many libraries, so refusing to
offer them would gut the feature on exactly the content it is for. But *not looked yet* and *looked
and found nobody* are opposite answers, and telling them apart is the difference between a useful
screen and a new library producing thousands of unfounded guesses in its first hour.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.attribution import FolderFaces
from sift.slices.suggestions.naming import Reading

#: How much of a folder one face group has to account for, on its own, before it corroborates the
#: folder's name.
#:
#: Three fifths, measured over the files that contain any face at all rather than over the folder.
#: A folder of forty-seven photographs where six are landscapes is still a folder about one person,
#: and counting the landscapes against her would set the bar by how much scenery somebody shot.
#:
#: Three fifths rather than a bare majority because a bare majority is a coin toss on a small
#: folder: four of seven is 57% and is one misgrouped face away from being three of seven. At three
#: fifths a second group in the same folder cannot also be dominant, which is the property that
#: matters: the answer is either one person or nobody, never an argument between two.
DOMINANT_SHARE = 0.6

#: A half is dominance too, when nothing else in the folder comes close.
#:
#: Three fifths alone misses the folder that is plainly one person's and carries a scatter of faces
#: nobody has grouped: half the files with a face are hers, every other group holds a file or two,
#: and the share lands a point under the bar. What the three fifths guards against is an argument
#: between two groups, and a leader holding ten times what the next one holds is no argument. So a
#: half with that lead is enough; a half without it is still the coin toss above. The tie guard and
#: the floor below apply to either road.
DOMINANT_SHARE_WITH_A_LEAD = 0.5

#: How many times the runner-up the leader has to hold for `DOMINANT_SHARE_WITH_A_LEAD` to be
#: enough. A folder with no runner-up at all has the lead by definition.
DOMINANT_LEAD = 10

#: And it takes at least this many. A pile of two across forty-seven files is not dominance and
#: corroborates nothing; it is two photographs that happen to match. The share alone would call a
#: folder where exactly two files have a face and both are the same person "dominant", which is
#: true and worthless.
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
    """Why a claim exists, kept because "why am I being asked this" is a fair question."""

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

    #: Who, when the rung already knows. `None` on a rung that proposes somebody who may not exist.
    person_id: str | None = None

    #: The name to propose, as the folder spells it. Empty when the rung names nobody new.
    name: str = ""

    #: The face group the claim rests on, when it rests on one.
    group_id: str | None = None


def dominant(counts: Mapping[str, int], *, with_faces: int) -> str | None:
    """The one group or person that accounts for a clear majority here, or None.

    None for an empty folder, for a folder nothing agrees about, and for a tie: a tie between two
    groups is exactly the case where picking one is a wrong attribution that looks like a right
    one, and the threshold is set so a tie for the top cannot pass it anyway.
    """
    if with_faces <= 0 or not counts:
        return None
    leader, seen = max(counts.items(), key=lambda pair: (pair[1], pair[0]))
    if seen < DOMINANT_FLOOR:
        return None
    # The runner-up is the next largest count, whoever holds it. A tie for the top makes it equal
    # to the leader, which no lead can pass and the guard below refuses anyway.
    runner_up = sorted(counts.values(), reverse=True)[1] if len(counts) > 1 else 0
    alone = seen >= with_faces * DOMINANT_SHARE
    led = seen >= with_faces * DOMINANT_SHARE_WITH_A_LEAD and seen >= DOMINANT_LEAD * runner_up
    if not (alone or led):
        return None
    # A second group matching the leader exactly is not a majority, whatever the arithmetic above
    # says about the share. Written out rather than relied on, because the share and the floor are
    # numbers somebody may move later and this property is not negotiable.
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

    `named_by` is everybody the folder's name already names, by their own name or by an alias. Its
    LENGTH is what matters as much as its contents: two people answering to one word is a real
    thing the stash-box allows on purpose, and which of them was meant is not a question a folder
    name can answer. So two matches link nobody, and the folder is left alone rather than guessed
    at: offering it would only invite somebody to create a third person with the same name.

    `looking` is whether recognition is switched on at all. With it off there is no pass to wait
    for, so a name-shaped folder is offered immediately and marked as resting on its name alone.

    `owned_inside` is everybody a folder somewhere UNDER this one is already somebody's own folder
    for: answered as them, or named for them by its own name. `by_name_only` is set for a folder
    whose name is the whole of what it is, which the faces in it may not overrule (a person's own
    folder a swap made). Both bind only rung 1; see the rung.
    """
    # 1. A face group somebody has already named accounts for this folder.
    #
    # **It gives a whole subtree away without asking, so it is the rung that has to know what a
    # folder IS before it counts the faces in it.** Two shapes are never one person's, however the
    # faces fall. A folder holding somebody ELSE's own folder is a collection of people: the faces
    # of the one with the most files in it outvote everybody else's, and the silent write then
    # reaches the other person's files (a face nobody named is no veto, and a file with no face is
    # none either). And a folder whose own name says whose it is, where the faces are somebody
    # filmed with them.
    #
    # The share stays over the files WITH a face (see `DOMINANT_SHARE`): counted over every file,
    # it would refuse the body-only folder this rung exists for, and the share is not what makes a
    # collection of people read as one person: what the folder holds is.
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

    # Looked at, faces found, and none of them agrees with any other. That is a folder of many
    # different people (a topic folder), and it is never one person.
    return Verdict()
