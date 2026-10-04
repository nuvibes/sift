# SPDX-License-Identifier: AGPL-3.0-or-later
"""The filename rule: filing a file under the Site and username its own name says it came from."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import replace

from sift.kernel.access import (
    Viewer,
    by_sift,
    count_files_filed_from,
    count_usernames_filed_from,
    decisions_for_files,
    filenames_for_nameless_usernames,
    filenames_of_unfiled_files,
    files_filed_under,
    files_in_post,
    filings_without_a_post,
    link_username_to_asset_in_post_on,
    link_username_to_asset_on,
    position_filed_from,
    seed_site_username_on,
    set_filing_post_on,
    set_username_numbers,
    username_for_number,
    usernames_filed_from,
)
from sift.kernel.db import Connection
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FILENAME, VIA_METADATA, Subject
from sift.slices.suggestions.metadata import FIELDS
from sift.slices.suggestions.naming import (
    Posted,
    Username,
    fold,
    posted_in_filename,
    posts_among,
    username_and_number_in_filename,
)
from sift.slices.suggestions.service_base import (
    FILED_FROM_A_NAME,
    FILENAMES_QUEUE,
    FROM_FILENAME,
    FROM_METADATA,
    PAGE,
    SHOWN_PER_USERNAME,
    FiledFile,
    Filing,
    FilingGroup,
    MadeSet,
)
from sift.slices.suggestions.service_pictures import NOT_READ, MetadataRuleMixin

log = get_logger(__name__)

#: How many filenames one pass will read looking for username numbers. A floor under how long a pass
#: can spend on a library where nothing matches, not a correctness bound: the set of usernames
#: without a number only shrinks, so whatever is not reached now is reached next time.
_NUMBER_HUNT = 5000


def _filed_sentence(filing: Filing) -> str:
    """The one line a file's own History shows for having been filed by this pass."""
    said = f"{filing.where.name} on {filing.where.site}"
    number = filing.number
    if number is None or number.via is None:
        # THE KERNEL'S OWN PHRASE for this pass, so the naming, the filing and the arrival line say
        # one pass one way.
        return f"Filed under {said} from the file's name"
    # "ID", the one word the screen uses for the Site's number, never "username number", "no."
    # or "the Site's own number".
    carries = f"Filed under {said}: the file's name carries ID {filing.where.number}"
    if number.agreed is None:
        return f"{carries}, which you typed onto the username"
    return (
        f"{carries}, which the {FIELDS[0]} and {FIELDS[1]} fields of {number.agreed} of its "
        f"pictures name as {filing.where.name}"
    )


def _posted_by_username(
    candidates: Sequence[tuple[str, str]],
) -> dict[Username, dict[str, list[str]]]:
    """Every file whose name carries a signature, by the username it names and the post it was in.

    **A username carrying TWO different numbers is dropped entirely**
    """
    readings: list[tuple[str, Posted]] = []
    for asset_id, filename in candidates:
        posted = posted_in_filename(filename)
        if posted is not None:
            readings.append((asset_id, posted))
    numbers: dict[tuple[str, str], set[str]] = {}
    for _, posted in readings:
        # A shape that names no username is not part of this argument and must not be folded into
        # it: the rule is about one WORD carrying two numbers, and every nameless reading on a site
        # shares the same empty word.
        if not posted.username:
            continue
        numbers.setdefault((posted.site, posted.username), set()).add(posted.number)
    disputed = {where for where, seen in numbers.items() if len(seen) > 1}
    return posts_among(
        (asset_id, posted)
        for asset_id, posted in readings
        if (posted.site, posted.username) not in disputed
    )


class FilenameRuleMixin(MetadataRuleMixin):
    """Files what a file's own name says, and draws the filings page."""

    async def _learn_username_numbers(self) -> int:
        """Write down the number each site knows a username by, out of the filenames.

        **Driven from the USERNAMES, not from the folders this pass walked.**

        **Only fills a blank, and never creates anything.**
        """
        candidates = await filenames_for_nameless_usernames(self._store.database, _NUMBER_HUNT)
        if not candidates:
            return 0
        # Keyed by the username's own ID, never by its name. The same name is one person on
        # Instagram and a different one on TikTok, and a write matched on the name would put the
        # first site's number onto both, permanently, because the write only ever fills a blank.
        found: dict[str, str] = {}
        disagreed: set[str] = set()
        for username_id, name, filename in candidates:
            pair = username_and_number_in_filename(filename)
            if pair is None:
                continue
            # The filename has to be about the username it was matched to. `LIKE` is a prefix test
            # and a prefix is not an identity: `mail` would match every one of `harlowquin`'s files.
            if fold(pair[0]) != fold(name):
                continue
            standing = found.setdefault(username_id, pair[1])
            # Two different numbers under one username is not something a filename can settle, and
            # writing either of them is worse than writing neither: the column is filled once and
            # never corrected, so a wrong number is permanent and silent.
            if standing != pair[1]:
                disagreed.add(username_id)
        for username_id in disagreed:
            log.info("suggestions.username_number_disputed", username_id=username_id)
            found.pop(username_id, None)
        if not found:
            return 0
        return await set_username_numbers(self._store.database, found)

    async def file_from_filenames(self) -> int:
        """File every unfiled file whose own name carries a site's signature. Returns how many.

        **The SHAPE says the site, never the username.**

        **It writes no person**

        **It applies itself, and says so on the file.**
        """
        candidates = await filenames_of_unfiled_files(self._store.database)
        if not candidates:
            return 0
        # Anything somebody has taken back is not offered again.
        refused = await self._store.refused_filenames()
        if refused:
            candidates = [one for one in candidates if one[0] not in refused]
            if not candidates:
                return 0
        found = await asyncio.to_thread(_posted_by_username, candidates)
        filed = 0
        waiting: list[tuple[str, str, int]] = []
        # What the last pass could not name, and how many files each of those numbers had then.
        already = {
            (site, number): files for site, number, files in await self._store.waiting_numbers()
        }
        reads = await self.reads_pictures()
        for read, posts in found.items():
            here = [one for files in posts.values() for one in files]
            filing = await self._filing_for(
                read, here, unchanged=already.get((read.site, read.number)) == len(here)
            )
            if filing is None:
                # A number no username carries and the pictures could not name: the only reading
                # `_filing_for` answers None for, since a reading that names its username is always
                # filed.
                waiting.append((read.site, read.number, len(here) if reads else NOT_READ))
                continue
            written, fresh = await self._file_under(filing, posts)
            filed += written
            # The sets come AFTER the transaction that filed them: a set is made through a write of
            # its own, and the write guard is not reentrant, so one made inside it would deadlock.
            for post_id, asset_ids in fresh:
                await self._set_from_post(filing.where, post_id, asset_ids)
        await self._store.remember_waiting_numbers(waiting)
        return filed

    async def _filing_for(
        self, where: Username, asset_ids: Sequence[str], *, unchanged: bool = False
    ) -> Filing | None:
        """How these files may be filed, or None where this library cannot name the username.

        **A shape that carries only the site's number for a username may file and may not invent.**

        **It does not converge on its own, and that is the honest shape rather than an oversight.**
        """
        if where.name:
            return Filing(where=where, source=FROM_FILENAME)
        known = await username_for_number(
            self._store.database, site=where.site, number=where.number
        )
        if known is None and not unchanged:
            known = await self._learn_number(where, asset_ids)
        if known is None:
            log.info(
                "suggestions.username_number_unknown",
                site=where.site,
                number=where.number,
            )
            return None
        # The word the FILING carries says what the filing rests on, which is not the same as what
        # the username's number rests on.
        source = FROM_METADATA if known.via == VIA_METADATA else FROM_FILENAME
        return Filing(where=replace(where, name=known.name), source=source, number=known)

    async def _file_under(
        self, filing: Filing, posts: dict[str, list[str]]
    ) -> tuple[int, list[tuple[str, list[str]]]]:
        """One username's files, filed in one transaction with the username they are filed under.

        **The post goes on the filing, and a post this library has already seen part of does not.**
        """
        where = filing.where
        async with self._store.write() as connection:
            _, username_id = await seed_site_username_on(
                connection,
                site=where.site,
                name=where.name,
                number=where.number,
                # The site is invented here as often as the username is, and nobody was asked about
                # either: this pass applies itself. `filename` is the same word the filings it
                # writes carry, so the site's own page and `enriched:filename` agree about it.
                made=by_sift(VIA_FILENAME),
            )
            written: list[str] = []
            fresh: list[tuple[str, list[str]]] = []
            for post_id, asset_ids in posts.items():
                # An empty post id is not a post: it is every file of this username whose own name
                # carries a stamp that is not a moment, gathered under one key by `one_post` and
                # named as such there.
                known = (
                    await files_in_post(
                        self._store.database, username_id=username_id, post_id=post_id
                    )
                    if post_id
                    else []
                )
                landed: list[str] = []
                for asset_id in asset_ids:
                    if known or not post_id:
                        wrote = await link_username_to_asset_on(
                            connection,
                            asset_id=asset_id,
                            username_id=username_id,
                            source=filing.source,
                        )
                    else:
                        wrote = await link_username_to_asset_in_post_on(
                            connection,
                            asset_id=asset_id,
                            username_id=username_id,
                            post_id=post_id,
                            source=filing.source,
                        )
                    if wrote:
                        written.append(asset_id)
                        landed.append(asset_id)
                # A post nothing knew before, and every one of its files newly filed: the only
                # case a set may be derived from. The order is the order the site numbered them.
                if post_id and not known and landed:
                    fresh.append((post_id, landed))
            # Nothing was written, so there is no decision to record: a receipt naming no files
            # would offer an undo over rows this pass never made.
            if not written:  # pragma: no cover (only a concurrent filing reaches this)
                return 0, []
            await self._record_filings(connection, filing, username_id, written)
        log.info(
            "suggestions.filed",
            site=where.site,
            posts=len(posts),
            files=len(written),
            created=username_id,
        )
        return len(written), fresh

    async def _record_filings(
        self, connection: Connection, filing: Filing, username_id: str, asset_ids: Sequence[str]
    ) -> None:
        """Write down what this filing did, so it can be read back and taken back.

        **ONE RECEIPT PER FILE, not one per username per pass.**
        """
        # A service built without one records nothing, exactly as `_record` above says: a pass that
        # runs for nobody makes no decisions. Here it means the filing still lands and is still
        # marked on the file. It is only the receipt that is absent.
        if self._recorder is None:
            return
        title = _filed_sentence(filing)
        record: dict[str, object] = {"kind": "filed", "username_id": username_id}
        # WHY, written into the receipt rather than left to be worked out again by whatever draws
        # the line.
        if filing.number is not None and filing.number.via is not None:
            record["number"] = filing.where.number
            record["name"] = filing.where.name
            record["number_via"] = filing.number.via
            if filing.number.agreed is not None:
                record["fields"] = list(FIELDS)
                record["agreed"] = filing.number.agreed
        for asset_id in asset_ids:
            await self._recorder.record_on(
                connection,
                queue=FILENAMES_QUEUE,
                user_id=None,
                # Which task: the filing's own source word, `filename` or `metadata`, the same
                # word the filing row carries, so the receipt and the row say one thing.
                via=filing.source,
                # The title is what the file's own History shows on this row, so it is written as a
                # sentence about ONE file and carries no full stop, like every sentence in that
                # pane.
                title=title,
                detail=f"{title}.",
                payload=json.dumps({**record, "assets": [asset_id]}),
                subjects=[Subject(kind="asset", id=asset_id)],
                # WHAT THIS DECISION WAS, in the ledger's own words: this pass filed the file under
                # a username.
                verb="filed",
                object=LedgerObject(
                    kind="username", id=username_id, name=filing.where.name or None
                ),
            )

    async def _set_from_post(
        self, where: Username, post_id: str, asset_ids: Sequence[str]
    ) -> str | None:
        """One post's pictures, as a Photo Set, with a record that can take it back.

        **The name is the username**

        **Not the date, although the post carries one.**
        """
        if self._sets is None:
            return None
        made = await self._sets.derive(list(asset_ids), where.name)
        if made is None:
            return None
        await self._record_set(where, made, asset_ids)
        log.info("suggestions.post_set", photo_set=made.id, pictures=len(asset_ids))
        return made.id

    async def _record_set(self, where: Username, made: MadeSet, asset_ids: Sequence[str]) -> None:
        """The set's OWN decision, beside the per-file filings and separate from them."""
        if self._recorder is None:
            return
        pictures = "picture" if len(asset_ids) == 1 else "pictures"
        said = f"{where.name} on {where.site}"
        async with self._store.write() as connection:
            await self._recorder.record_on(
                connection,
                queue=FILENAMES_QUEUE,
                user_id=None,
                via=VIA_FILENAME,
                # No full stop, like every sentence in this pane. "Photo Set" is capitalised as the
                # thing it is.
                title=f"Created a Photo Set of {len(asset_ids)} {pictures} posted together",
                detail=(
                    f"{len(asset_ids)} {pictures} posted together by {said}, read from the files' "
                    "own names."
                ),
                payload=json.dumps(
                    {
                        "kind": "post_set",
                        "photo_set_id": made.id,
                        "assets": list(asset_ids),
                    }
                ),
                # The files AND the grouping they were made into.
                subjects=[
                    Subject(kind="photo_set", id=made.id, name=made.name),
                    *(Subject(kind="asset", id=one) for one in asset_ids),
                ],
                # What this act WAS, rather than the vague word a receipt carries when its area
                # has not been through the ledger yet. Something was added; the payload says
                # which pass worked it out.
                verb="added",
            )

    async def take_back_set(self, photo_set_id: str, *, by: Viewer) -> bool:
        """Unmake one Photo Set this pass derived. True if there was anything to unmake.

        **The set goes and the pictures stay.**
        """
        if self._sets is None:
            return False
        await self._sets.forget(photo_set_id, by)
        return True

    async def group_filings_into_posts(self) -> int:
        """Work out the post for files this pass filed before it could read one. Sets made.

        **The catch-up, and it is a re-reading rather than a back-fill.**

        **A post whose files are filed under two different usernames is left alone.**
        """
        rows = await filings_without_a_post(self._store.database, FILED_FROM_A_NAME)
        if not rows:
            return 0
        under = {asset_id: username_id for asset_id, username_id, _ in rows}
        found = await asyncio.to_thread(
            _posted_by_username, [(asset_id, name) for asset_id, _, name in rows]
        )
        made = 0
        for read, posts in found.items():
            # Named the same way the filing pass names one, and for the same two reasons: the set
            # this makes is called after the username, and a reading off the nameless shape has none
            # of its own.
            named = await self._filing_for(read, [], unchanged=True)
            if named is None:  # pragma: no cover (every row read here is already filed)
                continue
            where = named.where
            for post_id, asset_ids in posts.items():
                # No stamp, no post: `one_post` gathers those under the empty key and the filing
                # pass writes none, so there is nothing here to catch up either.
                if not post_id:
                    continue
                owners = {under[one] for one in asset_ids}
                if len(owners) != 1:
                    continue
                username_id = owners.pop()
                async with self._store.write() as connection:
                    for asset_id in asset_ids:
                        await set_filing_post_on(
                            connection,
                            asset_id=asset_id,
                            username_id=username_id,
                            post_id=post_id,
                        )
                if await self._set_from_post(where, post_id, asset_ids) is not None:
                    made += 1
        log.info("suggestions.posts_caught_up", filings=len(rows), sets=made)
        return made

    async def filed_from_filenames(self, viewer: Viewer) -> int:
        """How many files a filename's own shape has filed, of those this viewer may be shown. The
        count on the card, scoped like every count, so the vault shut takes its files out of it."""
        return await count_files_filed_from(self._store.database, viewer, FILED_FROM_A_NAME)

    async def filing_position(self, viewer: Viewer, username_id: str) -> int | None:
        """Where one username sits on the list `filings_by_username` pages, or None when it is not
        on this viewer's list: every filing under it taken back since. What a page asked for by
        its first row starts at."""
        return await position_filed_from(
            self._store.database, viewer, FILED_FROM_A_NAME, username_id
        )

    async def filings_by_username(
        self, viewer: Viewer, *, limit: int = PAGE, offset: int = 0
    ) -> tuple[list[FilingGroup], int]:
        """One page of what the filename pass filed, grouped by the username it filed under.

        **Grouped by username because that is what the pass DECIDED.**
        """
        database = self._store.database
        total = await count_usernames_filed_from(database, viewer, FILED_FROM_A_NAME)
        found = await usernames_filed_from(
            database, viewer, FILED_FROM_A_NAME, limit=limit, offset=offset
        )
        # One read per username for its files, then ONE for every decision on the page. The second
        # is deliberately not per file: a page of twenty groups is twenty reads and one, rather
        # than twenty and five hundred.
        files = {
            username_id: await files_filed_under(
                database,
                username_id=username_id,
                sources=FILED_FROM_A_NAME,
                limit=SHOWN_PER_USERNAME,
                stamp=viewer.cache_stamp,
            )
            for username_id, _name, _site, _person, _count in found
        }
        allowed = await self._access.visible_of(
            viewer, [asset_id for rows in files.values() for asset_id, _name, _art in rows]
        )
        decisions = await decisions_for_files(database, sorted(allowed), queue=FILENAMES_QUEUE)
        groups = [
            FilingGroup(
                username_id=username_id,
                name=name,
                site=site,
                person_id=person_id,
                files=count,
                shown=[
                    FiledFile(
                        asset_id=asset_id,
                        filename=name,
                        decision_id=decisions.get(asset_id),
                        art=art,
                    )
                    for asset_id, name, art in files[username_id]
                    if asset_id in allowed
                ],
            )
            for username_id, name, site, person_id, count in found
        ]
        return groups, total
