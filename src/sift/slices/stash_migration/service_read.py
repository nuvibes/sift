# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a Stash database into a plan a run can take."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.jobs import JobQueue, JobState
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine
from sift.kernel.vocabulary import VIA_STASH_UNATTACHED
from sift.kernel.whole_file import write_json_whole
from sift.slices.stash_migration import reader
from sift.slices.stash_migration.deciding import (
    STASH_CONFIG,
    STASH_DATABASE,
    _database_in,
    _summary_of,
    ran_after,
    said_waiting,
)
from sift.slices.stash_migration.reader import NotAStashDatabase
from sift.slices.stash_migration.service_base import (
    COPY_NAME,
    PLAN_NAME,
    STASH_IMPORT,
    StashBase,
    StashRefused,
)

log = get_logger(__name__)

#: How many rows of each kind wear the mark of a Stash row attached to nothing. Counts, for the
#: line on the pane; the walls list them (`created=stash_unattached`).
_UNATTACHED = {
    "people": "SELECT COUNT(*) AS n FROM people WHERE created_by_via = ?",
    "sites": "SELECT COUNT(*) AS n FROM sites WHERE created_by_via = ?",
    "tags": "SELECT COUNT(*) AS n FROM tags WHERE created_by_via = ?",
}


class ReadMixin(StashBase):
    """Reading a Stash database: the copy, the summary, and what a run is asked to do."""

    async def _confined(self, chosen: str, what: str = "Stash's blobs folder") -> Path:
        """The path named, proven to be inside a folder Sift has been given. The picker's rule.
        `what` names what was asked for in the sentence a refusal says."""
        places = [Path(grant.abs_path) for grant in await self._library.grants()]

        def inside() -> Path | None:
            for place in places:
                try:
                    return confine(place, Path(chosen.strip()))
                except PathEscape:
                    continue
            return None

        found = await asyncio.to_thread(inside)
        if found is None:
            raise StashRefused(
                "Sift can only read a file in a folder it has been given. Add the folder that "
                f"holds {what} in `Settings > Folders` first."
            )
        return found

    async def _chosen(self, chosen: str) -> Path:
        """The file named, proven to be inside a folder Sift has been given. The picker's rule.

        A FOLDER named is Stash's folder (what a browser can pick): the database is the one its
        `config.yml` names, or `stash-go.sqlite`, looked for inside that folder by name."""
        found = await self._confined(chosen, "Stash's database")
        if await asyncio.to_thread(found.is_dir):
            inside = await asyncio.to_thread(_database_in, found)
            if inside is None:
                raise StashRefused(
                    "There's no Stash database in that folder. Choose the folder that holds "
                    f"{STASH_DATABASE}, or the one its {STASH_CONFIG} is in."
                )
            return inside
        if not await asyncio.to_thread(found.is_file):
            raise StashRefused("There's no file there. Check the name and try again.")
        return found

    async def read(self, chosen: str) -> dict[str, Any]:
        """Copy Stash's database in, read it, and say what it holds and what matches here."""
        source = await self._chosen(chosen)
        copy = self.folder / COPY_NAME
        try:
            await asyncio.to_thread(reader.copy_in, source, copy)
            summary = await asyncio.to_thread(_summary_of, copy)
        except NotAStashDatabase as refused:
            await asyncio.to_thread(copy.unlink, True)
            raise StashRefused(str(refused)) from refused
        mapping = await self._proposed(summary.folders)
        # `source` names the Stash database this read came from, which is what tells one Stash's
        # gallery 3 from another's (`schema`, `stash_galleries`).
        # Stash keeps its blobs folder beside its database unless told otherwise, so that is the
        # folder offered where it keeps pictures as files.
        plan = {
            "read_at": int(self._clock()),
            "mapping": mapping,
            "source": str(source),
            "blobs_proposed": str(source.parent / "blobs"),
        }
        await asyncio.to_thread(write_json_whole, self.folder / PLAN_NAME, plan)
        log.info("stash.read", version=summary.version, scenes=summary.scenes)
        return {
            "summary": asdict(summary),
            "mapping": mapping,
            "source": plan["source"],
            "waiting": await self._waiting.count(),
            "blobs": plan["blobs_proposed"] if summary.pictures_in_a_folder else None,
            "unattached": await self.unattached(),
        }

    async def _proposed(self, folders: Sequence[reader.TopFolder]) -> dict[str, str]:
        """Which folder here each of Stash's top folders is: the library folder called what the
        Stash folder is called, by the name it was given here or by its folder's own name. A
        folder with no match here is left out; its files are then found by their fingerprints, or
        wait for them."""
        roots = await self._library.roots()
        proposed: dict[str, str] = {}
        for one in folders:
            name = reader.stash_path(one.path).name.casefold()
            for root in roots:
                if name in (root.name.casefold(), Path(root.abs_path).name.casefold()):
                    proposed[one.path] = root.abs_path
                    break
        return proposed

    async def _matched_now(
        self, folders: Sequence[reader.TopFolder], kept: Mapping[str, str]
    ) -> dict[str, str]:
        """Which folder here each of Stash's top folders is now: the read's own answer where its
        folder is still one of this library's, and the name rule (`_proposed`) for the rest.

        Nothing lands without its file, so what counts is what this library holds when the
        answer is used, not what it held at the read."""
        roots = {root.abs_path for root in await self._library.roots()}
        now = {stash: here for stash, here in kept.items() if here in roots}
        for stash, here in (await self._proposed(folders)).items():
            now.setdefault(stash, here)
        return now

    async def last_read(self, queue: JobQueue | None = None) -> dict[str, Any] | None:
        """What the last read found, while its copy is still here; None when nothing was read.

        With the queue, it also says the run over that read, from the queue's record of the task.
        `waiting` is how many scenes and pictures wait for their files now, which falls as they
        arrive. `mapping` is the folder match as it stands now (`_matched_now`).
        """
        copy = self.folder / COPY_NAME
        if not await asyncio.to_thread(copy.is_file):
            return None
        try:
            summary = await asyncio.to_thread(_summary_of, copy)
            plan = json.loads(
                await asyncio.to_thread((self.folder / PLAN_NAME).read_text, encoding="utf-8")
            )
        except (NotAStashDatabase, OSError, ValueError):
            return None
        mapping = await self._matched_now(summary.folders, dict(plan.get("mapping") or {}))
        ran = None
        if queue is not None:
            done = await queue.last_finished_runs([STASH_IMPORT], ended_in=(JobState.DONE,))
            ran = ran_after(done.get(STASH_IMPORT), int(plan.get("read_at") or 0))
        blobs = plan.get("blobs") or plan.get("blobs_proposed")
        return {
            "summary": asdict(summary),
            "mapping": mapping,
            "source": plan.get("source"),
            "ran": ran,
            "waiting": await self._waiting.count(),
            "blobs": blobs if summary.pictures_in_a_folder else None,
            "unattached": await self.unattached(),
        }

    async def waiting_page(self, offset: int, limit: int) -> tuple[int, list[dict[str, Any]]]:
        """How many scenes and pictures wait for their files, and one page of them as listed."""
        total, rows = await self._waiting.page(offset, limit)
        return total, [said_waiting(one) for one in rows]

    async def ask_to_run(
        self, actor: Viewer, queue: JobQueue, *, pictures: bool = False, blobs: str | None = None
    ) -> str:
        """Queue the run over the last read, refused where there is nothing read or one is going.

        `pictures` is the choice to bring the pictures of People, Sites and Tags, and `blobs` the
        folder Stash keeps them in when it keeps them as files (`choose`)."""
        if await self.last_read() is None:
            raise StashRefused("Read a Stash database first.")
        for state in (JobState.QUEUED, JobState.RUNNING):
            waiting = await queue.list(job_type=STASH_IMPORT, state=state, limit=1)
            if waiting.total:
                raise StashRefused("A Stash library is already being imported.")
        await self.choose(pictures=pictures, blobs=blobs)
        return await queue.enqueue(STASH_IMPORT, {}, requested_by=actor.id)

    async def choose(self, *, pictures: bool, blobs: str | None) -> None:
        """Keep the choice about pictures with the read, where the run and the pass that lands
        what waits both find it. A blobs folder is held to the folders Sift has been given, the
        rule the database file itself is held to, and refused where it is not a folder."""
        folder: str | None = None
        if pictures and blobs and blobs.strip():
            found = await self._confined(blobs)
            if not await asyncio.to_thread(found.is_dir):
                raise StashRefused("There's no folder there. Check the blobs folder and try again.")
            folder = str(found)
        plan_path = self.folder / PLAN_NAME
        plan = json.loads(await asyncio.to_thread(plan_path.read_text, encoding="utf-8"))
        plan["pictures"] = bool(pictures)
        plan["blobs"] = folder
        await asyncio.to_thread(write_json_whole, plan_path, plan)

    async def unattached(self) -> dict[str, int]:
        """How many People, Sites and Tags a Stash library made here that Stash attached to nothing
        are still here: the rows the walls list under `created=stash_unattached`. Falls as they
        are deleted, so the line that links to them says what is left to look at."""
        counts: dict[str, int] = {}
        for kind, statement in _UNATTACHED.items():
            row = await self._db.fetch_one(statement, (VIA_STASH_UNATTACHED,))
            counts[kind] = int(row["n"]) if row is not None else 0
        return counts
