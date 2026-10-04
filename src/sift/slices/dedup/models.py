# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the two maintenance screens are sent.

Both are admin-only surfaces, so these carry ids plainly rather than concealing anything: there
is nobody to conceal them from by the time a response is being built. The gate is at the route.
"""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class FileView(Wire):
    """One file of a group, and everything needed to tell it from the others beside it.

    The facts arrive WITH the group rather than being fetched per file, and that is not a
    micro-optimisation: asked per file, a page of a hundred pairs is two hundred requests, and every
    one of them a permission walk on a self-hosted server that is often a small box.
    """

    id: str
    media_type: str
    #: True when this user may not be shown the file itself. The tile is a locked placeholder,
    #: nothing about it is printed, and no rule may keep or delete it. See the route.
    concealed: bool = False
    original_filename: str | None = None
    #: Where the file is, said the one way every screen says a place (`kernel.where`). Regularly the
    #: ONLY thing that tells two files in a group apart, because the pictures are the same picture.
    #: Null when no copy is anywhere Sift can currently see.
    where: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    container: str | None = None
    added_at: int | None = None
    #: The token on the end of the still's address, so the browser may keep the picture instead of
    #: asking again on every visit. Minted by the access layer with every other file's (see
    #: `kernel.serving.art_version`); None for a concealed file and for one with no recorded picture,
    #: whose address is then left bare and re-checked on every use: slower, never wrong.
    art: str | None = None


class GroupView(Wire):
    """One question: these files, and the one a rule would keep."""

    #: The files, smallest id first. Stable, so a group keeps its shape between two reads.
    files: list[FileView]
    method: str = Field(
        description=(
            "Which fingerprint chained these together: one frame for a photograph, thirty for a "
            "GIF, one number for a whole video. A group is never mixed: the three measure on "
            "three different scales."
        )
    )
    distance: int = Field(
        description="The closest any pair inside this group measured, on that method's scale."
    )
    keeper: str | None = Field(
        default=None,
        description=(
            "The file the rule would keep, or null when it could not decide. Null is what "
            "'needs you' means, and it is what the card on the board counts."
        ),
    )
    too_big: bool = Field(
        default=False,
        description=(
            "This is a CHAIN rather than a group: each pair inside it is under the threshold and "
            "its two ends may look nothing alike. Nothing in it is ever pre-marked and it cannot "
            "be settled in one press; the answer is a tighter closeness setting."
        ),
    )


class ChoiceView(Wire):
    """One value a dial may be set to, and what it is called on screen.

    One record for both dials rather than one each: a stored value is a word like `higher_res` or
    `medium` and the reader is shown "Higher resolution" or "Medium - re-encoded", and both mappings
    are declared in the settings registry. A screen holding either of them as its own list is a list
    that drifts the first time a choice is added and nobody edits both.
    """

    key: str
    label: str


class GroupList(Wire):
    """A page of groups, and everything needed to read it honestly.

    The numbers beside the list are not decoration. A near-duplicate screen showing nothing has
    several completely different meanings (there are none, the dial is hiding them, half the
    library has never been fingerprinted) and they read identically without these.
    """

    groups: list[GroupView]
    #: How many groups these settings make, across the whole library. What the page is a part of.
    total: int
    #: Where this page starts, counting from zero. Echoed back so a pager cannot drift from it.
    offset: int
    needs_you: int = Field(
        description=(
            "Groups the rule could not settle, across the whole library. The honest count for a "
            "screen whose promise is that it empties."
        )
    )
    matching: int = Field(
        description="Pairs waiting that these settings show, across the whole library."
    )
    pending_total: int = Field(
        description="Pairs waiting in all, whatever the settings. Never smaller than `matching`."
    )
    concealed: int = Field(
        default=0,
        description=(
            "Groups left out of THIS page because a file in them is in a vault this session has "
            "not opened. Counted so a short page says a number rather than quietly being short."
        ),
    )
    awaiting_fingerprint: int = Field(
        default=0,
        description=(
            "Videos nothing has fingerprinted yet, so they cannot be in the queue at all. The "
            "number that tells an empty queue apart from an unfinished one."
        ),
    )
    cannot_fingerprint: int = Field(
        default=0,
        description=(
            "Files nothing can ever compare, because the decoder refused their frames. Work that "
            "will not happen, as against `awaiting_fingerprint`, which is work in flight."
        ),
    )
    level: str = Field(description="The named closeness these results were read at.")
    max_duration_gap_ms: int | None = Field(
        default=None,
        description="The length rule these results were read at. Null means lengths are ignored.",
    )
    rule: str = Field(description="The keeper rule in force, as it is stored.")
    rules: list[ChoiceView] = Field(
        default=[],
        description=(
            "Every keeper rule that may be chosen, with what each is called. Sent with the queue so "
            "the screen does not hold a second copy of the list that drifts when one is added."
        ),
    )
    levels: list[ChoiceView] = Field(
        default=[],
        description=(
            "Every closeness the dial may be set to, in order from strictest. The dial lives on "
            "this screen rather than on a settings pane, because it decides what the list under it "
            "holds and the two are read together."
        ),
    )
    max_duration_gap_limit: int = Field(
        default=3600,
        description=(
            "The largest length rule the dial accepts, in seconds, as the setting declares it. "
            "Zero means lengths are not compared at all."
        ),
    )
    max_duration_gap_word: str | None = Field(
        default=None,
        description=(
            "What the dial says in place of zero, as the setting declares it, so the queue's dial "
            "and the settings pane name the same state in the same words."
        ),
    )


class GroupChoice(Wire):
    """One group, and which of its files to keep.

    The whole group is named rather than a group id, because a group has no id: it is computed from
    the pair table at the dials in force, so the only durable name for one is the set of files in
    it. The server clusters again and refuses anything that is not one of its own groups, which
    is what stops a request naming an arbitrary set of files and having them deleted together.
    """

    ids: list[str]
    keep: str | None = Field(
        default=None,
        description=(
            "Which file to keep. Required when confirming; ignored when saying these are "
            "different, where nothing is deleted."
        ),
    )


class GroupSettleRequest(Wire):
    """A page of groups, settled in one press."""

    groups: list[GroupChoice]


class GroupSettleResult(Wire):
    """What the press actually did, which is not always what it was asked to do.

    A folder Sift was never given write access to refuses one file and says nothing about the rest,
    so the counts are reported rather than assumed. `refused` is how a screen can say "twenty-two
    done, two could not be deleted" instead of claiming a success it did not have.
    """

    settled: int = Field(description="Groups that were acted on.")
    removed: int = Field(description="Files deleted from the disk.")
    refused: int = Field(description="Files the disk would not let go of. Still in the queue.")
    #: Groups the server would not act on: not one of its own groups any more, or holding a file
    #: this session may not be shown. A dial moved under the screen is the ordinary cause.
    unknown: int = 0


class CopyView(Wire):
    """One place a redundant asset sits."""

    location_id: str
    root_id: str
    rel_path: str
    filename: str
    size_bytes: int | None
    #: Where the copy is, said the one way every screen says a place (`kernel.where`): the full
    #: path, with a folder Hidden hides as "...". `rel_path` alone is the half that is usually the
    #: SAME on both copies of a file, so alone it cannot answer the one question this screen asks.
    path: str | None = None


class RedundancyView(Wire):
    """An asset with more than one copy on disk, and what dropping the extras would free."""

    asset_id: str
    media_type: str
    copies: list[CopyView]
    #: What the tile prints under the picture, the same three facts a near-duplicate tile does.
    #:
    #: On the ASSET rather than on each copy, because every copy is the same bytes (that is what
    #: makes them exact duplicates), so a shape and a length per copy would be the same number
    #: printed twice with a suggestion that it might not be. What differs between copies is where
    #: each one is and what the disk says it takes, and both of those are on `CopyView`.
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    #: The token on the end of the still's address, so the browser may keep the picture instead of
    #: asking again on every visit. Minted by the access layer with every other file's (see
    #: `kernel.serving.art_version`); None for a concealed file and for one with no recorded picture,
    #: whose address is then left bare and re-checked on every use: slower, never wrong.
    art: str | None = None
    reclaimable_bytes: int = Field(
        description=(
            "What removing every copy but the largest would free. A copy whose size was never "
            "recorded counts as nothing rather than being estimated."
        )
    )


class ReclaimView(Wire):
    """One page of the assets stored more than once, and what the whole of it comes to.

    Three numbers over two populations, kept apart on purpose. `assets` is this page, held to the
    vault. `total` and `total_reclaimable_bytes` are the whole library and are not: they describe
    how many and how much, never which. `concealed` is how many of THIS page were dropped for the
    vault, so a page that comes back short says why rather than looking like the end of the list.
    """

    assets: list[RedundancyView]
    total: int
    total_reclaimable_bytes: int
    concealed: int = 0
    #: Where this page begins. What the request asked for, except when it asked by a row (`from`):
    #: only the answer knows where that row is, and the page's own pager counts from here.
    offset: int = 0


class ReleaseRequest(Wire):
    """Let go of one copy of an asset, keeping the asset and its other copies."""

    location_id: str


class ReleaseOne(Wire):
    """One copy of one asset, in a page of them."""

    asset_id: str
    location_id: str


class ReleaseMany(Wire):
    """Let go of these copies, one press for a page: the same act `ReleaseRequest` is, over
    the copies the screen marked as not the keeper of each file."""

    releases: list[ReleaseOne]


class Released(Wire):
    """What a page release did. A copy that could not go (its file already gone, the last
    copy of something) is counted rather than failing the rest."""

    released: int
    refused: int


class CarryResult(Wire):
    """What a carry did.

    `files` counts FILES and not rows, because a file that gained two attributions is one file that
    changed and one thing to take back: the same grain the receipts are written at.
    """

    #: Groups that were acted on.
    carried: int
    #: Files that gained an attribution.
    files: int


class CarryTotals(Wire):
    """How much there is to carry across the whole library, counted before anything is offered."""

    #: Groups of copies where one file knows something the others do not.
    groups: int
    #: Files that would gain an attribution.
    files: int
