# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a viewer can reach, and what to say when they cannot.

Two questions with one answer between them, which is why they share a module: whether a person may
act on one named file, and how much of a selection they may act on. Both come down to the access
layer refusing something, and neither may answer with a 404 that says the file is not there.

## Why this is one shape and not seven

Seven routes write over a selection: tags, people, Sites, collections, photo sets, and the two that
name faces. Seven near-identical reply shapes would be seven chances for the screens to disagree
about what happened: a selection of three files with one in the vault coming back as a flat
failure from one route and as "2 removed, 1 refused" from another. One shape, in the kernel, because
a slice may not import another slice: the same reasoning `partial_write` gives for living here.

## Why partial, not refused

*A partial success that does not say which part succeeded is worse than a refusal* is an argument
against a SILENT partial success, not against a partial one. The answer it points at is the one
below (do the work that can be done and say exactly what was left out and why) rather than
throwing away four completed writes because a fifth could not be made.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, status

from sift.kernel.access import Actionable, Repository, Viewer
from sift.kernel.wire import Wire

VAULT_LOCKED = "It is in your vault. Unlock the vault to include it."
"""Why an item was left out, when the reason is the viewer's own locked vault.

One sentence, here, because seven routes and one screen have to say the same thing about the same
situation. Addressed to the person who put the file there: the vault conceals from onlookers, not
from its owner, so this tells them something they already know and can act on.

Singular. `VAULT_LOCKED_MANY` is the same sentence about more than one; see `BulkWriteDone.reason`
for why both are sent rather than the screen inflecting one.
"""

VAULT_LOCKED_MANY = "They are in your vault. Unlock the vault to include them."
"""The same refusal, about more than one item."""

OUT_OF_REACH = "Sift could not find the file."
"""Why an item was left out, for every reason that is not the vault.

Deliberately one sentence for two situations (a file that is not there, and a file belonging to
somebody else) because telling those apart is exactly what the access model refuses to do. A
message that said "you may not have this one" would confirm to a stranger that the id names
something real.

**It says what SIFT could not do, never what is or is not there, and that is the whole of the
wording.** *"There is no such file."* is a correct sentence in one of the two situations and a
false statement in the other: told to somebody being denied, it asserts that a file they can see
the padlock of does not exist. The whole point of one wording for both is that the wording gives
nothing away, and one that lies in half the cases gives away that it is lying. So there is one of
these, here, and every service imports it.

Singular; `OUT_OF_REACH_MANY` is its plural. See `BulkWriteDone.reason`.
"""

OUT_OF_REACH_MANY = "Sift could not find the files."
"""The same refusal, about more than one file."""

KEPT_LOCAL_LEFT_OUT = "It is kept local \u2014 nothing about it leaves this machine."
"""Why a file was left out of a question to a stash-box: somebody said "Do not enrich".

Here beside the other two reasons because it answers in the same place and the same shape (a
selection of five where one was held back says "Asking about 4 of 5." and then this) and the
screens pick between the singular and the plural the way they already do for the vault.

Not `OUT_OF_REACH`, which would tell somebody who marked a file kept local that Sift could not FIND
it: a lie about the one file whose whereabouts they are sure of, and no hint that the switch they
pressed is doing its job. It says where the thing stays rather
than which switch to press, because the switch may not be on the file at all: a file under a
Site, a person or a tag that is kept local is kept local too, and "Allow enrichment on it" would
send somebody to a row that changes nothing.
"""

KEPT_LOCAL_LEFT_OUT_MANY = "They are kept local \u2014 nothing about them leaves this machine."
"""The same reason, about more than one file."""


class BulkWriteDone(Wire):
    """How much a write over a selection changed, how much it left alone, and why.

    `changed` counts what the write ALTERED, which is not always the number of items asked about:
    putting two tags on three files changes up to six pairs, and putting a tag a file already
    carries on it changes none. `skipped` counts ITEMS (the ones in the request that were not
    acted on at all) because that is the number a person recognises as "one of the three I
    picked", and it is what the message on screen is built from.

    One reason rather than a list of them: a selection spanning one locked vault produces the
    SAME sentence for every item in it, and thirty copies of one sentence is a screen nobody
    reads. The count says how many; the sentence says why.
    """

    changed: int = 0
    skipped: int = 0
    #: The first skip's own words FOR ONE ITEM, or None where nothing was skipped.
    #:
    #: ## Why the plural is a second field and not the screen's job
    #:
    #: The count and the sentence are decided in two different places and neither can see the other.
    #: Only the client knows how many were skipped (it is the one that made the selection) and
    #: only the server knows the sentence, which for bulk delete is not a constant at all but
    #: whatever refused the file, such as a folder handed over read-only. A screen pairing its own
    #: "3 files could not be included" with a sentence written about one would print
    #: *"3 files could not be included. It is in your vault."*
    #:
    #: Two fields keep each half where its facts are. A KEY the client renders cannot carry the
    #: reason, which is free text (see the `vault_locked` flag below), and a sentence worded to
    #: carry no number reads wrong about one file, which is the common case.
    reason: str | None = None
    #: The same reason, worded for more than one. None exactly when `reason` is.
    #:
    #: Always set beside `reason`: `after` below does both, and a producer with only one wording
    #: sends it twice rather than leaving this empty, because a client falling back would print the
    #: singular against a plural count.
    reason_many: str | None = None
    #: Whether the skipping was the viewer's OWN vault, which is the one reason they can undo.
    #:
    #: A flag rather than the screen matching on `reason`, and that is the whole point of it. The
    #: sentence is free text (bulk delete's reasons come from whatever refused the file, like a
    #: folder handed over read-only), so a client comparing it against a copy of one particular
    #: sentence is two lists that must agree, and the day the wording is improved the Unlock button
    #: silently stops appearing. This says the fact; the sentence says it in words.
    vault_locked: bool = False

    @classmethod
    def after(cls, actionable: Actionable, changed: int) -> BulkWriteDone:
        """The reply, built from what the access layer allowed and what the write then changed.

        Here rather than in each route, because seven of them would otherwise each decide which
        reason wins when a selection is skipped for both, and seven answers to that is how two
        screens come to explain the same selection differently.

        The vault wins when both are present, and that is the one judgement in this class. It is
        the only reason of the two the person can act on: told "Sift could not find it" they have
        learned nothing to do, and told the vault is locked they have a PIN and a way through.
        """
        if actionable.concealed:
            reason: str | None = VAULT_LOCKED
            many: str | None = VAULT_LOCKED_MANY
        elif actionable.refused:
            reason, many = OUT_OF_REACH, OUT_OF_REACH_MANY
        else:
            reason = many = None
        return cls(
            changed=changed,
            skipped=actionable.skipped,
            reason=reason,
            reason_many=many,
            vault_locked=bool(actionable.concealed),
        )


class ConcealedByVault(Exception):
    """A refusal that is the asker's OWN locked vault, wherever it was raised.

    A marker, carrying no behaviour, so that the three areas whose services raise their own
    refusals (delete, organize and the media editor) can say *this* kind of no without any of
    them importing another. Each mixes it into its own `NotFound`, so everything already catching
    that keeps working and only the one line that chooses a status code has to know.

    ## Why those three needed it and the other seven did not

    `require_reachable` and `refuse_one` below serve a ROUTER: they build an `HTTPException` and
    the router raises it. Seven routers use them. The other three do the same resolving inside a
    SERVICE, which must not know about status codes at all, so they cannot reach either helper,
    and a flat `NotFound("There is no such file.")` would be all they could say.

    That sentence is a lie told to the one person who is owed the truth. Somebody who has just
    hidden a file, and can see its padlock on the screen, would be told the file does not exist
    when they try to move or delete it. The vault conceals from onlookers, never from its owner:
    `vault_locked` below sets out at length why saying so to that user gives nothing away, and
    this is how a service says the same thing without growing an opinion about HTTP.
    """


async def conceals(access: Repository, viewer: Viewer, asset_id: str) -> bool:
    """Whether it is THIS viewer's OWN vault that is keeping the file from them.

    The one question that earns a different answer from the undifferentiated 404, so it is asked in
    one place. Both halves matter: a viewer who has already unlocked cannot be in this case at all,
    and the concealment is per user: a file in somebody ELSE's vault is not this, and telling
    them apart is what stops the 423 confirming that a stranger's id names something real.

    Read on the refusal path only. Nothing that succeeds pays for it.
    """
    return not viewer.show_hidden and await access.is_concealed(viewer.id, asset_id)


def vault_locked() -> HTTPException:
    """The refusal for a SINGLE item the viewer's own vault is concealing.

    423 rather than the 404 everything else answers, and this is the one place the access layer's
    usual rule is deliberately relaxed. Everywhere else a refusal and an absence wear the same face,
    because a 403 on a file a guest was never shown confirms that the file exists: the one bit the
    model is built to withhold.

    That reasoning does not reach this case. The vault is presentation-layer concealment and always
    has been: it defends against shoulder-surfing, screen-sharing and somebody standing at your
    screen, and it never claimed to keep a secret from the user who locked it. Telling that one
    user "this is in your vault" says nothing they did not do themselves, and answering 404
    instead tells them their file has been deleted, which is a lie, and the more alarming one.

    It is reached only by asking for a specific id, so nothing is enumerable through it: an onlooker
    in `FULLY_GONE` mode has no tile to press, and one in `PLACEHOLDER` mode is looking at a padlock
    that already says exactly this.
    """
    return HTTPException(status.HTTP_423_LOCKED, VAULT_LOCKED)


async def require_reachable(
    access: Repository, viewer: Viewer, asset_id: str, missing: Callable[[], HTTPException]
) -> str:
    """The asset id, if this viewer may act on it. 423 for their own vault, 404 for anything else.

    ONE of these, for the tags, people, collections and photo-set routers alike: four copies of a
    permission check is four chances for one of them to drift, and the thing they would disagree
    about is a permission.

    `open_asset` rather than `get_asset`, a distinction worth keeping written down: a concealed
    asset comes back from the second one as a locked placeholder, and a placeholder is not
    something to tag, collect or file under somebody. Acting on one would also say it exists,
    through a count somebody else could read.
    """
    asset = await access.open_asset(viewer, asset_id)
    if asset is not None:
        return asset.id
    raise await refuse_one(access, viewer, asset_id, missing)


async def refuse_one(
    access: Repository, viewer: Viewer, asset_id: str, missing: Callable[[], HTTPException]
) -> HTTPException:
    """The right refusal for one asset this viewer cannot have: 423, or the caller's own 404.

    Asked only once the answer is known to be no, and it costs a second read to decide WHICH no,
    which is why it lives here rather than inside every resolve. The read only happens on the
    refusal path, so nothing that succeeds pays for it.

    A viewer with the vault already open cannot be in this branch for vault reasons, so the
    question is not asked of them at all.

    THE 404 IS THE CALLER'S OWN AND IS NOT WRITTEN HERE, which is the whole reason for the last
    argument. Written here it would differ from the caller's own (the player's `_missing` says
    `"Not found."`), and on that route a file on an unplugged drive and a file that never existed
    would come back as two DISTINGUISHABLE 404s. That is the exact property the access model
    exists to hold, and
    `test_an_asset_on_a_drive_that_is_not_plugged_in_reads_as_a_miss` holds it.
    A shared helper may add a new answer; it may not quietly become a second spelling of an old one.
    """
    if await conceals(access, viewer, asset_id):
        return vault_locked()
    return missing()


__all__ = [
    "OUT_OF_REACH",
    "OUT_OF_REACH_MANY",
    "VAULT_LOCKED",
    "VAULT_LOCKED_MANY",
    "BulkWriteDone",
    "ConcealedByVault",
    "conceals",
    "refuse_one",
    "require_reachable",
    "vault_locked",
]
