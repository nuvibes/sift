# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the review screen is sent, and what it sends back.

Everything here has already been resolved against whoever is reading it. The counts are counts of
what that user may open, the unticked files are files they may already see, and a folder they
may not see produced no row at all. Nothing in this file does any of that; the service does, and
these shapes exist so that a route cannot accidentally hand back something wider.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.cover_frame import CoverFrame
from sift.kernel.wire import Wire


class ProposalView(Wire):
    """One folder, and what Sift thinks it says."""

    id: str
    kind: str = Field(
        description=(
            "person for an ordinary folder, site for one whose every filename opens with "
            "the same word, username for one naming one person's name on a site it names."
        )
    )
    proposed: str = Field(description="The name being proposed, spelled as the folder spells it.")
    evidence: str = Field(
        description=(
            "Why this is being asked: face_group when a group of faces agrees, name_only when "
            "nothing here carries a face, filenames when the names of the files said it."
        )
    )
    folder: str
    #: The folder itself, so a screen showing this question can also correct it. The claim id
    #: answers "which question"; this answers "which folder", and saying who a folder really is is
    #: a statement about the folder rather than about the question.
    folder_id: str
    path: str
    files: int = Field(description="How many files under that folder this user may see.")
    group_id: str | None = None
    face_id: str | None = Field(
        default=None, description="One face of the group, to draw beside the question."
    )
    face_art: str | None = Field(
        default=None,
        description=(
            "The token to put on the end of that face's crop address, so the browser may keep "
            "the picture instead of re-asking about it on every visit. Null exactly when there "
            "is no face."
        ),
    )
    near_miss: str | None = Field(
        default=None,
        description=(
            "Somebody already here whose name is a letter away. A merge to consider, never one "
            "Sift performs."
        ),
    )
    site: str | None = None
    #: Whether `proposed` is a username on `site` (a folder sitting directly under a site folder)
    #: rather than a display name. `is_` because a bool called `username` would read as the name.
    is_username: bool = False
    dissenting: list[str] = Field(
        default=[],
        description="Files here whose faces do not agree, offered to be left out.",
    )
    per_file: list[str] = Field(
        default=[],
        description="For a Site folder, the names read out of the filenames.",
    )
    cover: str = Field(
        default="",
        description="One file under the folder, to draw beside the question.",
    )
    art: dict[str, str] = Field(
        default={},
        description=(
            "The token to put on the end of each still this row draws, keyed by file id: the "
            "cover and every dissenting file. A file that is absent has nothing yet known about "
            "its pictures to name them by, and its address is left bare and checked on every use: "
            "a slower card, never a broken one."
        ),
    )


class ProposalList(Wire):
    proposals: list[ProposalView]
    total: int
    #: Where this page starts, as the server resolved it. See `GroupPage` in the faces slice: a
    #: request that named a row rather than an offset does not know the answer until it arrives.
    offset: int = 0


class NameAFolder(Wire):
    """Who a folder is, said by a person rather than worked out.

    The name is taken as written. It is cleaned the way any stored name is and nothing else is done
    to it: no stripping of words the reader thinks are noise, no splitting on a joiner. Somebody
    typing a name into this box has already decided, and second-guessing them here would be the
    reader making the same mistake twice.
    """

    name: str = Field(min_length=1, max_length=120)
    #: Whether this folder is a PERSON or a SITE.
    #:
    #: Two different answers rather than one with a flag on it. A folder of one site's downloads
    #: holds many people, one per file, so naming it as a person would make a person out of the
    #: site and file everybody's clips under them. Defaulting to `person` keeps a caller that names
    #: no kind meaning a person.
    kind: Literal["person", "site"] = "person"


class ConfirmRequest(Wire):
    """Yes, with what to leave out.

    For an ordinary folder `skip` is the odd files out, unticked on the screen. For a Site
    folder it is the NAMES read out of the filenames, because that is what the row offers: the
    folder is a Site and its people come one per file, so there is nothing useful to untick a
    file by.

    Either way it leaves those out of the attribution and out of nothing else: they are not marked,
    not remembered and not asked about again.
    """

    skip: list[str] = Field(default_factory=list)


class ConfirmedView(Wire):
    """What the answer actually did. Every number is a write that landed."""

    person_id: str = Field(
        description=(
            "Who the folder turned out to be. Empty for a Site folder, whose people come "
            "one per file."
        )
    )
    created: bool = Field(description="Whether this created the person rather than finding them.")
    files: int
    faces: int
    alias: bool
    username_linked: bool
    people: int = Field(description="How many different people were named.")
    site: bool = Field(description="Whether the folder was filed under a Site.")
    decision_id: str = Field(
        default="",
        description="The record this decision wrote, so it can be taken back from the toast.",
    )


class FiledView(Wire):
    """One folder a pass filed under somebody without asking.

    On screen so that a sweep is visible after it happens rather than only findable by noticing a
    name on a file and wondering where it came from.
    """

    person_id: str
    person: str
    folder_id: str = Field(description="The folder, for the press that takes it back.")
    folder: str
    path: str
    files: int
    #: Which picture this person's cover is, and the token on the end of its address: the same
    #: four the People wall's card is handed. Without them the portrait here would be addressed
    #: bare, which the server answers the careful way, so every visit would re-ask about a picture
    #: it had.
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    #: WHICH MOMENT of that file, when the cover is a chosen frame of a video; withheld with it.
    #: Folded into the cover's address, which is what lets the browser keep the picture for a
    #: week (`kernel/covers.py names_its_cover`) instead of re-asking on every visit.
    cover_at_ms: int | None = None
    #: The window of that picture it is drawn as, or None for the whole of it, withheld with
    #: the file. The cover's address names it (`kernel/covers.py names_its_cover`), and the
    #: editor opens on it. See `kernel/cover_frame.py CoverFrame`.
    cover_frame: CoverFrame | None = None
    cover_track_id: str | None = None
    art: str | None = None


class FiledList(Wire):
    filed: list[FiledView]


class FiledFromNameView(Wire):
    """One file a pass filed from its own name, and the decision that can take it back."""

    asset_id: str
    filename: str = Field(description="The name the shape was read out of, as it is on disk.")
    decision_id: str | None = Field(
        default=None,
        description=(
            "The record to undo, or null where there is none to offer: a filing made before "
            "the pass wrote one decision per file, or one already taken back. The control is "
            "absent rather than dead in that case: an Undo the server would refuse is worse than "
            "none, because the only way to find out is to press it."
        ),
    )
    art: str | None = Field(
        default=None,
        description=(
            "The token to put on the end of this file's still, so the browser may keep it for a "
            "week rather than asking again on every visit. Null where nothing is yet known about "
            "the pictures to name them by, and the address is then left bare and checked each "
            "time: a slower card, never a broken one."
        ),
    )


class FilenameGroupView(Wire):
    """One username, and the files a pass filed under it from their own names."""

    username_id: str
    username: str
    site: str | None = Field(
        default=None,
        description=(
            "The Site this username is on, or null where the site row has been deleted out "
            "from under it. Not an error: a filing outlives the site it names."
        ),
    )
    person_id: str | None = Field(
        default=None,
        description=(
            "The person this username is joined to, or null where nobody has been said for it. "
            "A username has no page of its own: a press on it opens this person, or where there "
            "is none, the Files wall narrowed to the username (`?username=`)."
        ),
    )
    files: int = Field(description="How many files this pass filed under this username, in total.")
    shown: list[FiledFromNameView] = Field(
        default_factory=list,
        description=(
            "The first few of them, resolved against whoever is asking. Fewer than `files` "
            "whenever the username holds more than a card draws, or holds files this viewer may "
            "not be shown."
        ),
    )


class FilenameFilingList(Wire):
    """One page of the filename filings, grouped by the username each was filed under."""

    groups: list[FilenameGroupView]
    total: int = Field(
        description="How many USERNAMES carry filings, which is what the page pages."
    )
    offset: int


class UsernameTakenBack(Wire):
    """What taking one username's filename filings back did, and the record it wrote."""

    files: int = Field(description="How many files came off the username.")
    decision_id: str = Field(
        default="",
        description="The record this decision wrote, so it can be taken back from the toast.",
    )


class FolderTakenBack(Wire):
    """What taking one folder back from the person it was added to did, and the record it wrote."""

    files: int = Field(description="How many files the person came off.")
    decision_id: str = Field(
        default="",
        description="The record this decision wrote, so it can be put back from the toast.",
    )


class FolderSetAside(Wire):
    """What setting a folder aside did, and the record it can be taken back from.

    Named for the thing rather than the verb, for the reason its sibling elsewhere gives: two
    response models with one name are published under their module paths instead.
    """

    settled: bool = Field(description="Whether this was the press that settled it.")
    decision_id: str = Field(
        default="",
        description="The record this decision wrote, so it can be taken back from the toast.",
    )
