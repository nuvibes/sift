# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spread a song's name through the files that share it, each one undoable, never over a song."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Sequence

from sift.kernel.access import Viewer
from sift.kernel.access.history import files_called
from sift.kernel.changes import About, announce, who_may_see_a_file
from sift.kernel.content import songs
from sift.kernel.content.identity import (
    MUSIC_SHARED,
    cleaned_song,
    seed_music_on,
    unseed_music_on,
)
from sift.kernel.ledger import Object, Reversal
from sift.kernel.log import get_logger
from sift.kernel.workbench import Named, Piece, Preview, Recorded, Worded, payload_held
from sift.slices.music.store import NameStore

log = get_logger(__name__)

#: Not `music`: that queue's decisions cannot be undone, and a workbench name has one reverser.
QUEUE = "music_names"

#: As the client spells it (`history.ts`).
_FILE_PAGE = "/asset/{}"

#: Unscoped: Sift writing a fact about the sound, not a person looking (`MusicStore.music_group`).
GroupOf = Callable[[str], Awaitable[Sequence[str]]]

#: A song's name is searchable text.
Touched = Callable[[Sequence[str]], Awaitable[None]]

#: The lookup's start, which queues a job only where its task's When starts on its own.
AfterSpread = Callable[[str], Awaitable[object]]


async def _nothing_touched(asset_ids: Sequence[str]) -> None:
    return None


class SongNames:
    def __init__(
        self,
        names: NameStore,
        *,
        group_of: GroupOf,
        touched: Touched = _nothing_touched,
        after_spread: AfterSpread | None = None,
    ) -> None:
        self._names = names
        self._group_of = group_of
        self._touched = touched
        self._after_spread = after_spread

    async def on_pairs_settled(self, asset_id: str, pairs: Sequence[object]) -> None:
        """The fingerprint job's hook: spread first, since a name from the group needs no lookup."""
        await self.spread(asset_id)
        if self._after_spread is not None:
            await self._after_spread(asset_id)

    async def spread(self, asset_id: str) -> list[str]:
        """Give the song to whichever side of the group has none; returns the files named."""
        group = [one for one in await self._group_of(asset_id) if one != asset_id]
        if not group:
            return []
        songs = await self._names.songs_of([asset_id, *group])
        named: list[str] = []
        own = songs.get(asset_id)
        source = asset_id
        if own is None:
            carried = Counter(songs[one] for one in group if one in songs)
            ranked = carried.most_common(2)
            if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
                return []
            song = ranked[0][0]
            # The group is closest first.
            source = next(one for one in group if songs.get(one) == song)
            if not await self._give(asset_id, song, source):
                return []
            own = song
            named.append(asset_id)
        for one in group:
            if one not in songs and one != source and await self._give(one, own, source):
                named.append(one)
        if named:
            log.info("music.names_shared", asset_id=asset_id, files=len(named))
            await self._touched(named)
        return named

    async def _give(self, asset_id: str, song: str, source_id: str) -> bool:
        """One file takes a song from another, with its receipt and refusal check, atomically."""
        cleaned = cleaned_song(song)
        if not cleaned:
            return False
        # Read before the write: these are the line's words.
        called = await files_called(self._names.database, [source_id])
        source_name = called.get(source_id) or "another file"
        async with self._names.database.write() as connection:
            if await NameStore.refused_on(connection, asset_id, cleaned):
                return False
            written = await seed_music_on(
                connection,
                asset_id,
                cleaned,
                source=MUSIC_SHARED,
                from_asset=Object(kind="asset", id=source_id, name=source_name),
                facts={
                    "asset": asset_id,
                    "link": {
                        "kind": "asset",
                        "id": source_id,
                        "words": source_name,
                        "href": _FILE_PAGE.format(source_id),
                    },
                },
                receipt=Reversal(
                    queue=QUEUE,
                    title=f"Sift named the song {cleaned}, from the same music as {source_name}",
                    detail=(
                        f"{source_name} carries the same song. Undo takes the name off this file "
                        "and keeps it off."
                    ),
                ),
            )
            if not written:
                return False
            announce(await who_may_see_a_file(connection), About.LIBRARY)
        return True


class SharedNameReceipts:
    """How a shared song's name is taken back, from a file's History; a reverser with no card."""

    name = QUEUE
    reversible = True

    def __init__(self, names: NameStore, *, touched: Touched = _nothing_touched) -> None:
        self._names = names
        self._touched = touched

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the line links both files, each scoped on its own page."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take Sift's name off if still there, and refuse it; False for an unreadable receipt."""
        found = _taken_back(payload)
        if found is None:
            return False
        asset_id, song = found
        named = payload_held(payload).get("song_id")
        async with self._names.database.write() as connection:
            cleared = await unseed_music_on(
                connection, asset_id, song, song_id=named if isinstance(named, str) else None
            )
            await NameStore.refuse_on(connection, asset_id, song)
            # Told either way: the receipt itself is taken back.
            announce(await who_may_see_a_file(connection), About.LIBRARY)
        if cleared:
            await self._touched([asset_id])
        log.info("music.name_refused", asset_id=asset_id, cleared=cleared)
        return True

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded from what it recorded (see `kernel.workbench.Recorded`)."""
        found = _taken_back(recorded.payload)
        if found is None:
            return None
        asset_id, song = found
        pieces: list[Piece] = [
            "Sift named the song ",
            song,
            " on ",
            Named(kind="asset", id=asset_id),
        ]
        shared_from = recorded.held().get("from")
        if isinstance(shared_from, str) and shared_from:
            pieces.extend([", from the same music as ", Named(kind="asset", id=shared_from)])
        return Worded(said=tuple(pieces))


# Hand-made song changes refuse or unrefuse a name; registered here, as the kernel cannot import it.
songs.on_hand(QUEUE, taken_off=NameStore.refuse_on, put_on=NameStore.unrefuse_on)


def _taken_back(payload: str) -> tuple[str, str] | None:
    held = payload_held(payload)
    asset_id, song = held.get("asset"), held.get("song")
    if not isinstance(asset_id, str) or not asset_id or not isinstance(song, str) or not song:
        return None
    return asset_id, song
