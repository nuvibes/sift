# SPDX-License-Identifier: AGPL-3.0-or-later
"""The confirmations: one answer on the review screen, turned into every write it implies."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, replace

from sift.kernel.access import (
    Viewer,
    add_alias_on,
    attribute_assets_recording_on,
    by_sift,
    create_person_on,
    file_assets_under_site_on,
    give_person_a_cover_on,
    link_username_to_asset_on,
    link_username_to_person,
    people_named,
    people_named_on,
    seed_site_username_on,
)
from sift.kernel.access.sentences import plural
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.db import Connection
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.text import clean_stored_text
from sift.kernel.vocabulary import VIA_FOLDER, VIA_MIRROR, Subject
from sift.slices.suggestions.ladder import Evidence
from sift.slices.suggestions.namesakes import Namesake, file_namesakes_on, namesakes_of
from sift.slices.suggestions.naming import (
    fold,
    username_and_number_in_filename,
)
from sift.slices.suggestions.service_base import (
    AUTOMATIC,
    Applied,
    Ignored,
    NotFound,
    SuggestionError,
    Written,
)
from sift.slices.suggestions.service_folders import FolderRuleMixin
from sift.slices.suggestions.store import Claim

log = get_logger(__name__)


def _confirmation_receipt(applied: Applied) -> tuple[str, str, str]:
    """A confirmation as `(title, detail, payload)` for the record."""
    files = plural(applied.files, "file", "files")
    title = f"{applied.proposed} \u2014 {files}"
    parts = [f"{files} filed under {applied.proposed}"]
    if applied.faces:
        parts.append(plural(applied.faces, "face", "faces") + " named")
    if applied.created:
        parts.append("a new person added")
    if applied.alias:
        parts.append("the spelling remembered")
    if applied.username_linked:
        parts.append("the username linked")
    if applied.written.namesakes:
        more = len(applied.written.namesakes)
        parts.append(plural(more, "other folder", "other folders") + " with that name answered")
    payload = {
        "kind": "confirmed",
        "claim_id": applied.claim_id,
        "written": asdict(applied.written),
    }
    return title, ", ".join(parts) + ".", json.dumps(payload)


def _ignore_receipt(ignored: Ignored) -> tuple[str, str, str]:
    """Setting a folder aside, as `(title, detail, payload)`."""
    payload = {
        "kind": "ignored",
        "claim_id": ignored.claim_id,
        "name_key": ignored.name_key,
    }
    return (
        f"{ignored.proposed} \u2014 set aside",
        f"No folder called {ignored.proposed} will be offered again.",
        json.dumps(payload),
    )


class ConfirmMixin(FolderRuleMixin):
    """Answers a question: as a person, a Site, a username, or not at all."""

    # --- the one action ------------------------------------------------------------------------

    async def say_who_a_folder_is(
        self, viewer: Viewer, folder_id: str, name: str, *, kind: str = "person"
    ) -> Applied:
        """A person naming a folder the reader got wrong, or never asked about at all.

        **This is the only way a MISS is ever recorded, and a miss leaves no other trace.**
        """
        folder = await self._access.get_folder(viewer, folder_id)
        if folder is None:
            raise NotFound("no such folder")
        proposed = clean_stored_text(name).strip()
        key = fold(proposed)
        if not key:
            raise NotFound("that isn't a name")
        # A SITE is a different answer, not a variation on a person, and the difference is the
        # whole reason this branch exists: a folder of one site's downloads holds many people, one
        # per file, so treating the site's name as a person's would make a person called "RedGIFs"
        # and file everybody's clips under them. `_confirm_site` already knows what to do with
        # such a claim (it reads the people out of the filenames), so this writes the row and
        # hands it to the same confirmation every other claim goes through.
        if kind == "site":
            written = await self._store.add_claim(
                folder_id=folder_id,
                kind="site",
                name_key=key,
                proposed=proposed,
                person_id=None,
                group_id=None,
                site=proposed,
                is_username=False,
                evidence=Evidence.BY_HAND.value,
            )
        else:
            # Whoever it already names, by their own name or by an alias: the same question the
            # ladder asks, so naming somebody the library already holds links them rather than
            # making a second. Two people answering to one word is left for the confirmation to
            # refuse, exactly as it is for a claim the reader made.
            found = await people_named(self._store.database, proposed)
            written = await self._store.add_claim(
                folder_id=folder_id,
                kind="person",
                name_key=key,
                proposed=proposed,
                person_id=found[0] if len(found) == 1 else None,
                group_id=None,
                site="",
                is_username=False,
                evidence=Evidence.BY_HAND.value,
            )
        if not written:
            # This folder has already made this claim, in some state. A rejection is the interesting
            # one: somebody said no and is now saying yes, which is a correction of a correction and
            # the row has to be reopened rather than added beside itself.
            existing = next(
                (one for one in await self._store.claims_for(folder_id) if one.name_key == key),
                None,
            )
            if existing is None:  # pragma: no cover (add_claim only refuses an existing row)
                raise NotFound("that folder couldn't be named")
            async with self._store.write() as connection:
                await self._store.forget_rejection_on(connection, key)
                await self._store.reopen_on(connection, existing.id)
                # The question is back on Organize > Suggestions on every admin's other tabs.
                announce(EVERY_ADMIN, About.LIBRARY)
            return await self.confirm(viewer, existing.id)
        fresh = next(
            (one for one in await self._store.claims_for(folder_id) if one.name_key == key), None
        )
        if fresh is None:  # pragma: no cover (it was just written)
            raise NotFound("that folder couldn't be named")
        return await self.confirm(viewer, fresh.id)

    async def confirm(self, viewer: Viewer, claim_id: str, *, skip: Sequence[str] = ()) -> Applied:
        """Say yes. Names the face group, attributes the folder, and teaches the next spelling.

        **The face group is named before the transaction opens, and that ordering is deliberate.**
        """
        claim = await self._store.claim(claim_id)
        if claim is None or claim.state != "pending":
            raise NotFound("that suggestion isn't waiting for an answer")
        if await self._access.get_folder(viewer, claim.folder_id) is None:
            raise NotFound("that suggestion isn't waiting for an answer")

        # A Site folder is a different answer, not a variation on this one. It proposes a
        # SITE, and its people come one per file out of the filenames, so confirming it must
        # not make a person out of the Site's name, which is what treating it as an ordinary
        # folder would do.
        if claim.kind == "site":
            return await self._confirm_site(viewer, claim, skip=skip)

        # And a folder that named one USERNAME on one site is a third answer again. It writes no
        # person at all: a username is what a site calls somebody and not evidence of who they
        # are, which is the finding the whole filename reader rests on.
        if claim.kind == "username":
            return await self._confirm_username(viewer, claim)

        unticked = set(skip)
        assets = [
            asset_id
            for asset_id in await self._store.assets_under(claim.folder_id)
            if asset_id not in unticked
        ]

        person_id, created = await self._person_for(claim)
        named: list[str] = []
        if claim.group_id:
            async with self._store.write() as connection:
                named = await self._faces.name_group_recording(
                    connection, claim.group_id, person_id
                )
                announce(await self._told(connection, people=[person_id]), About.LIBRARY)

        namesakes = await namesakes_of(self, claim, person_id)
        async with self._store.write() as connection:
            applied = await self._confirm_person_on(
                connection,
                viewer,
                claim,
                assets,
                person_id=person_id,
                created=created,
                named=named,
                namesakes=namesakes,
            )

        # And the faces the answer named TEACH: a reference is filed, so the person is recognized by
        # something new; the decision is remembered, so a rescan of those files keeps the name; and
        # a re-match is asked for. After the transaction, for the reason the naming above runs
        # before it: this decodes pictures, and holding the single writer across that stops every
        # other write.
        if named:
            await self._faces.teach(named, person_id)

        log.info(
            "suggestions.confirmed",
            files=applied.files,
            faces=len(named),
            created=created,
            namesakes=len(applied.written.namesakes),
        )
        return await self._filed(applied)

    async def _confirm_person_on(
        self,
        connection: Connection,
        viewer: Viewer,
        claim: Claim,
        assets: list[str],
        *,
        person_id: str,
        created: bool,
        named: list[str],
        namesakes: Sequence[Namesake] = (),
    ) -> Applied:
        """Every write of a Yes to an ordinary folder, on its one connection, and its record."""
        attributed = await attribute_assets_recording_on(
            connection, asset_ids=assets, person_id=person_id
        )
        also = await file_namesakes_on(self, connection, namesakes, person_id)
        # A person this answer INVENTED is drawn as a monogram until somebody opens their page,
        # which on a library filed a folder at a time is nearly all of them: initials on a
        # wall of people every one of whom has just had files attributed to them. So the first
        # file that landed becomes their picture.
        if created and attributed:
            await give_person_a_cover_on(connection, person_id=person_id, asset_id=attributed[0])
        alias = await add_alias_on(connection, person_id=person_id, alias=claim.proposed)
        linked, username_id = await self._seed_username_on(connection, claim, person_id)
        await self._store.remember_folder_person_on(
            connection, folder_id=claim.folder_id, person_id=person_id
        )
        await self._store.settle_on(connection, claim.id, "confirmed")

        applied = Applied(
            person_id=person_id,
            created=created,
            files=len(attributed) + len(also.attributed),
            faces=len(named),
            alias=alias,
            username_linked=linked,
            people=1,
            site=linked,
            claim_id=claim.id,
            proposed=claim.proposed,
            written=Written(
                attributed=tuple(
                    (asset_id, person_id) for asset_id in (*attributed, *also.attributed)
                ),
                faces=tuple(named),
                created_people=(person_id,) if created else (),
                alias=(person_id, claim.proposed) if alias else None,
                username_linked=(username_id, person_id) if linked else None,
                remembered=(
                    (claim.folder_id, person_id),
                    *((folder_id, person_id) for folder_id in also.remembered),
                ),
                namesakes=also.answered,
            ),
        )
        recorded = await self._record(
            connection,
            viewer,
            _confirmation_receipt(applied),
            self._subjects_of(applied, claim.folder_id),
            object=LedgerObject(kind="person", id=person_id, name=claim.proposed or None),
        )
        applied = replace(applied, decision_id=recorded)
        # People, the grid, the Username and Organize > Suggestions on every other tab.
        announce(
            await self._told(
                connection, people=[person_id], usernames=[username_id] if username_id else []
            ),
            About.LIBRARY,
        )
        return applied

    async def _seed_username_on(
        self, connection: Connection, claim: Claim, person_id: str
    ) -> tuple[bool, str]:
        """The username a folder under a Site named, joined to the person. Returns whether it was
        joined and its id, or `(False, "")` where the folder named no username."""
        linked = False
        username_id = ""
        if claim.site and claim.is_username:
            _, username_id = await seed_site_username_on(
                connection,
                site=claim.site,
                name=claim.proposed,
                # The number the site knows this username by, out of the folder's own filenames.
                number=await self._username_number_in(connection, claim.folder_id),
                # `folder`, the same word `_person_for` writes on the person this very act
                # invents. Somebody pressed yes, but what they agreed to was a FOLDER NAME's
                # reading (the username here is the folder's own spelling and nothing on this
                # screen typed it), so the username and the person come out of one act saying
                # one thing, rather than the default 'sift' with no pass beside it.
                made=by_sift(VIA_FOLDER),
            )
            linked = await link_username_to_person(
                connection, username_id=username_id, person_id=person_id
            )
        return linked, username_id

    async def _confirm_site(self, viewer: Viewer, claim: Claim, *, skip: Sequence[str]) -> Applied:
        """Yes to a folder whose every filename opens with the same word."""
        files = await self._store.files_in(claim.folder_id)
        async with self._store.write() as connection:
            filed: list[tuple[str, str]] = []
            await file_assets_under_site_on(
                connection,
                asset_ids=[asset_id for asset_id, _ in files],
                site=claim.proposed,
                # The FOLDER's name became the site. Somebody pressed yes, which is why this is a
                # confirmation rather than a silent pass, but they did not type the name, Sift
                # read it, and that is what the row has to say.
                made=by_sift(VIA_FOLDER),
                # And the filings carry the folder's word, for the reason `_confirm_username`
                # gives: what was agreed to was a folder name's reading. It is also what lets the
                # undo take back exactly these filings and never one somebody made by hand.
                source=AUTOMATIC,
                landed=filed,
            )
            attributed, named, invented = await self._name_by_filename(
                connection, files, unticked={fold(one) for one in skip}
            )
            await self._store.settle_on(connection, claim.id, "confirmed")

            applied = Applied(
                files=len(attributed),
                people=len(named),
                site=True,
                claim_id=claim.id,
                proposed=claim.proposed,
                # The site itself is not in the record, and that is deliberate. Filing files under a
                # site creates the site and the "nobody" username behind it, and a later scan or a
                # download can attach to both within minutes, so an undo that removed them would
                # take rows it never wrote. The people are put back and so are the filings this
                # wrote; the site stays, and can be removed on its own page, where what else is
                # filed under it is on screen.
                written=Written(
                    attributed=tuple(attributed),
                    created_people=tuple(invented),
                    filed=tuple(filed),
                ),
            )
            recorded = await self._record(
                connection,
                viewer,
                _confirmation_receipt(applied),
                self._subjects_of(applied, claim.folder_id),
            )
            applied = replace(applied, decision_id=recorded)
            announce(
                await self._told(
                    connection,
                    people=[person_id for _, person_id in attributed],
                    sites=[claim.proposed],
                ),
                About.LIBRARY,
            )

        log.info("suggestions.confirmed.site", files=len(attributed), people=len(named))
        return await self._filed(applied)

    async def _confirm_username(self, viewer: Viewer, claim: Claim) -> Applied:
        """Yes to a folder named for one username on one site.

        **The site in the brackets, even where it only re-hosts.**
        """
        files = await self._store.files_in(claim.folder_id)
        async with self._store.write() as connection:
            _, username_id = await seed_site_username_on(
                connection,
                site=claim.site or "",
                name=claim.proposed,
                # And the site's own number where the filenames inside carry one, so the username
                # survives being renamed from the day it is made. `None` is the ordinary answer
                # here: a mirror's filenames rarely carry it.
                number=await self._username_number_in(connection, claim.folder_id),
                # `mirror` rather than `folder`, and the difference is the one this whole method
                # rests on: the site in the brackets is where these files were RE-HOSTED, read out
                # of a folder shaped `username (site)`. A row saying only `folder` would lose the
                # one thing that makes the filing arguable later.
                made=by_sift(VIA_MIRROR),
            )
            filed: list[str] = []
            for asset_id, _filename in files:
                if await link_username_to_asset_on(
                    connection, asset_id=asset_id, username_id=username_id, source=AUTOMATIC
                ):
                    filed.append(asset_id)
            await self._store.settle_on(connection, claim.id, "confirmed")

            applied = Applied(
                files=len(filed),
                site=True,
                claim_id=claim.id,
                proposed=claim.proposed,
                # The username is not in the record and the site is not either, for the reason
                # `_confirm_site` gives about the site it creates: making one attaches nothing
                # that an undo could safely remove, and a download or a stash-box can have written
                # to it within minutes.
                written=Written(filed=tuple((one, username_id) for one in filed)),
            )
            recorded = await self._record(
                connection,
                viewer,
                _confirmation_receipt(applied),
                [Subject(kind="folder", id=claim.folder_id)]
                + [Subject(kind="asset", id=one) for one in filed],
            )
            applied = replace(applied, decision_id=recorded)
            announce(await self._told(connection, usernames=[username_id]), About.LIBRARY)

        log.info("suggestions.confirmed.username", files=len(filed), site=claim.site)
        return await self._filed(applied)

    async def _person_for(self, claim: Claim) -> tuple[str, bool]:
        """Who this claim is about, made if they do not exist yet."""
        async with self._store.write() as connection:
            found = await people_named_on(connection, claim.proposed)
            if len(found) == 1:
                return found[0], False
            if len(found) > 1:
                raise SuggestionError("more than one person answers to that name \u2014 say which")
            person_id = await create_person_on(connection, claim.proposed, made=by_sift(VIA_FOLDER))
            if person_id is None:  # pragma: no cover (a claim is never filed under an empty name)
                raise SuggestionError("that name is empty once it's cleaned up")
            # A new person is on People on every admin's other tabs.
            announce(EVERY_ADMIN, About.LIBRARY)
            return person_id, True

    async def _username_number_in(self, connection: Connection, folder_id: str) -> str | None:
        """The site's own number for the username this folder holds, read out of its filenames.

        **One number, or none.**
        """
        seen: set[str] = set()
        for _, filename in await self._store.files_in_on(connection, folder_id):
            pair = username_and_number_in_filename(filename)
            if pair is not None:
                seen.add(pair[1])
            if len(seen) > 1:
                return None
        return next(iter(seen), None)

    async def reject(self, viewer: Viewer, claim_id: str) -> Ignored:
        """Not a person. Remembered permanently and by name, so it never comes back."""
        claim = await self._store.claim(claim_id)
        if claim is None or claim.state != "pending":
            raise NotFound("that suggestion isn't waiting for an answer")
        if await self._access.get_folder(viewer, claim.folder_id) is None:
            raise NotFound("that suggestion isn't waiting for an answer")
        async with self._store.write() as connection:
            await self._store.reject_name_on(connection, claim.name_key)
            settled = await self._store.settle_on(connection, claim.id, "rejected")
            ignored = Ignored(
                settled=settled,
                claim_id=claim.id,
                name_key=claim.name_key,
                proposed=claim.proposed,
            )
            # The folder alone. A no writes nothing onto any file, so there is nothing else this
            # decision touched, and the folder's files are NOT read for it: that would be a page
            # read added to a request that currently costs two small writes, to record a decision
            # that did not happen to any of them.
            recorded = await self._record(
                connection,
                viewer,
                _ignore_receipt(ignored),
                [Subject(kind="folder", id=claim.folder_id)],
            )
            ignored = replace(ignored, decision_id=recorded)
            # The question leaves Organize > Suggestions on every admin's other tabs.
            announce(EVERY_ADMIN, About.LIBRARY)
        return ignored

    async def unignore(self, *, claim_id: str, name_key: str) -> bool:
        """Put a set-aside folder back among the questions, and forget the no."""
        async with self._store.write() as connection:
            await self._store.forget_rejection_on(connection, name_key)
            # The question comes back on Organize > Suggestions on every admin's other tabs.
            announce(EVERY_ADMIN, About.LIBRARY)
            return await self._store.reopen_on(connection, claim_id)
