# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the two admin-only maintenance screens are sent."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class FileView(Wire):
    """One file of a group, with the facts that tell it from the others, sent with the group."""

    id: str
    media_type: str
    #: The tile is a locked placeholder; no rule may keep or delete it.
    concealed: bool = False
    original_filename: str | None = None
    #: Often the only thing telling two files apart; null when no copy is visible.
    where: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    container: str | None = None
    added_at: int | None = None
    #: The still's cache token (`kernel.serving.art_version`); None leaves the address bare.
    art: str | None = None


class GroupView(Wire):
    """One question: these files, and the one a rule would keep."""

    #: Smallest id first, so a group keeps its shape between reads.
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
    """One value a dial may be set to, and its on-screen label, both from the settings registry."""

    key: str
    label: str


class GroupList(Wire):
    """A page of groups, with the counts that tell an empty page's causes apart."""

    groups: list[GroupView]
    total: int
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
    """One group by its files, since a group has no id, and which of them to keep."""

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
    """What the press did, with refusals counted rather than assumed."""

    settled: int = Field(description="Groups that were acted on.")
    removed: int = Field(description="Files deleted from the disk.")
    refused: int = Field(description="Files the disk would not let go of. Still in the queue.")
    #: Not one of the server's groups any more, or holding a hidden file.
    unknown: int = 0


class CopyView(Wire):
    """One place a redundant asset sits."""

    location_id: str
    root_id: str
    rel_path: str
    filename: str
    size_bytes: int | None
    #: The full path: `rel_path` alone is usually the same on both copies.
    path: str | None = None


class RedundancyView(Wire):
    """An asset with more than one copy on disk, and what dropping the extras would free."""

    asset_id: str
    media_type: str
    copies: list[CopyView]
    #: On the asset, since every copy is the same bytes.
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    #: The still's cache token (`kernel.serving.art_version`); None leaves the address bare.
    art: str | None = None
    reclaimable_bytes: int = Field(
        description=(
            "What removing every copy but the largest would free. A copy whose size was never "
            "recorded counts as nothing rather than being estimated."
        )
    )


class ReclaimView(Wire):
    """One page of assets stored more than once; the totals are whole-library and not vault-held."""

    assets: list[RedundancyView]
    total: int
    total_reclaimable_bytes: int
    concealed: int = 0
    #: Where this page begins; only the answer knows where a `from` row is.
    offset: int = 0


class ReleaseRequest(Wire):
    """Let go of one copy of an asset, keeping the asset and its other copies."""

    location_id: str


class ReleaseOne(Wire):
    """One copy of one asset, in a page of them."""

    asset_id: str
    location_id: str


class ReleaseMany(Wire):
    """Let go of these copies in one press, as `ReleaseRequest` does for one."""

    releases: list[ReleaseOne]


class Released(Wire):
    """What a page release did; a copy that could not go is counted, not fatal."""

    released: int
    refused: int


class CarryResult(Wire):
    """What a carry did; `files` counts files, the grain receipts are written at."""

    carried: int
    files: int


class CarryTotals(Wire):
    """How much there is to carry across the whole library."""

    groups: int
    files: int
