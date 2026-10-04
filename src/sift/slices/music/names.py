# SPDX-License-Identifier: AGPL-3.0-or-later
"""A song's name, spread to every file that shares the song, and taken back when somebody says no.

A PMV named from its Site's page, or by AcoustID, carries the song; the clips cut from it and the
other editors' mixes of it carry the same sound and, without this, nothing that says so. When the
pairing of one file settles (`service.py` hands its pairs to `on_pairs_settled`), its group
(the files it pairs with and the files those pair with, one hop) is read, and a name crosses to
whichever side has none.

## It is Sift's act, and every one can be taken back

Each name written this way is one receipt: the file that received it is the subject, the file it
came from is the object, and History says "Sift named the song X, from the same music as <file>",
linked: the words of the file page's Same music strip, which say why the file has the name. Undo
takes the name off only while the file still carries it (a song somebody chose by hand since is
theirs) and writes a refusal, so the next time the group settles the same name does not come
straight back. The refusal is per song: a different song is a different claim.

## Which name, when a group disagrees

A file with no name takes the name most of its named partners carry; a tie gives it nothing,
because a wrong name spread through a group is worse than no name and a person can type one. A file
that has a name gives it to every partner that has none, and the partner joins that file's SONG
(`kernel/content/songs.py`). Never over a song: every write goes through the kernel's
`seed_music_on`, which puts a song only on a file that carries none.

## Why the spread reads the WHOLE group, unscoped

Whether a file may be seen is a question about a person looking at it, and nobody is looking: this
is Sift writing a fact about the files' sound, the way probing writes their length. The answer a
person is given about the group is scoped (`MusicStore.same_music_of`); the write is not
(`MusicStore.music_group`).
"""

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

#: The name the shared names' receipts are written under, and the reverser registered for them.
#:
#: NOT `music`: that name is the catch-up card's (`queue.QUEUE`), a queue that says no decision of
#: its kind can ever be taken back (`MusicQueue.reversible = False`), and a workbench name has one
#: reverser. Receipts under it would draw an Undo the card then refuses.
QUEUE = "music_names"

#: The address of one file's own page, as the client spells it (`history.ts`: `/asset/{id}`).
_FILE_PAGE = "/asset/{}"

#: The files that share a song with one file, as Sift's own acts see them (`MusicStore.music_group`).
GroupOf = Callable[[str], Awaitable[Sequence[str]]]

#: Telling the search index that these files' words changed: a song's name is searchable text.
Touched = Callable[[Sequence[str]], Awaitable[None]]

#: What happens after the names have spread: the lookup's start (`lookup.LookupStarter.consider`),
#: which queues the lookup task's own job only where that task's When starts it on its own.
AfterSpread = Callable[[str], Awaitable[object]]


async def _nothing_touched(asset_ids: Sequence[str]) -> None:
    return None


class SongNames:
    """Spreading a song's name through a group of files that share it, and taking one back."""

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
        """The hook the fingerprint job calls once a file's pairs are written.

        The spread first, then whether AcoustID should be asked about this file, in that order,
        because a name that arrived through the group is one the lookup does not need to send
        anything for. `pairs` is not read: the group is, and it spans the pairs of the pairs.
        """
        await self.spread(asset_id)
        if self._after_spread is not None:
            await self._after_spread(asset_id)

    async def spread(self, asset_id: str) -> list[str]:
        """Give the song to whichever side of this file's group has none. The files named, in order.

        See the module docstring for the two directions and the tie. A file that received a name
        here is named by one receipt each, with the file the name came from as the object.
        """
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
            # The closest file carrying it: the group is closest first.
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
        """One file takes a song from another: the name, where it came from, and its receipt, in
        one transaction, with the refusal read inside it. Whether the file was named."""
        cleaned = cleaned_song(song)
        if not cleaned:
            return False
        # What the file it came from is called, as History calls it (`history.files_called`: the
        # title somebody typed, the name it arrived under, or its name on the disk). Read before the
        # write: it is the words of the line, and a rename between the two is a line naming the file
        # by the name it had a moment ago, which is what every line does.
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
                    # The way to the file the name came from, placed on its name in the receipt's
                    # own words (`history._receipt_link`): the line on this file's page is the
                    # title, and the name in it is what somebody presses to see where it came from.
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
            # The file's record changed (its Music field, and a line on its History), so whoever
            # might be drawing it is told, as a record edit tells them (`who_may_see_a_file`).
            announce(await who_may_see_a_file(connection), About.LIBRARY)
        return True


class SharedNameReceipts:
    """How a shared song's name is taken back. A reverser with no card: the act is on a file's
    History, not in a pile anybody works through. Registered with `Workbench.register_reverser`."""

    name = QUEUE
    #: Every name shared this way can be taken back.
    reversible = True

    def __init__(self, names: NameStore, *, touched: Touched = _nothing_touched) -> None:
        self._names = names
        self._touched = touched

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing. The line names both files, linked, and each is one press away; a picture here
        would have to be scoped to the viewer, and the file's own page already is."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take the shared name back. True when the receipt said what to take back.

        Two writes in one transaction, and each is its own rule:

        - the file comes off the song ONLY while it still carries it as Sift put it there
          (`unseed_music_on`, through the song's one door): a song somebody chose since is theirs;
        - a refusal is written, so the next settle of the group does not name it again.

        True even where the name had already been changed, because the refusal is still what the
        Undo asked for. False only for a receipt that does not say which file and which song.
        """
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
            # Told whether or not the name was still there: the receipt on the file's History is
            # taken back either way, and that pane re-reads on the library's bell.
            announce(await who_may_see_a_file(connection), About.LIBRARY)
        if cleared:
            await self._touched([asset_id])
        log.info("music.name_refused", asset_id=asset_id, cleared=cleared)
        return True

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded from what it recorded. See `kernel.workbench.Recorded`.

        The receipt wrote down the song, the file it went on and the file it came from, and that
        is the whole line: Sift is the actor, since nobody decided it, and both files are named as
        things (the reader gives them their names and links). A receipt that recorded no file or
        no song keeps its stored title, which is the honest answer for a row this cannot word.
        """
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


# A song somebody took off a file by hand is a refusal this feature keeps (the spread and the
# lookup both honour it), and one somebody put on by hand takes a refusal of that song back. Told
# by the kernel's song door, on the act's own connection (`songs.on_hand`): the kernel may not
# import this feature, so it is registered here, at import.
songs.on_hand(QUEUE, taken_off=NameStore.refuse_on, put_on=NameStore.unrefuse_on)


def _taken_back(payload: str) -> tuple[str, str] | None:
    """The file and the song a shared name's receipt wrote down, or None for any other shape."""
    held = payload_held(payload)
    asset_id, song = held.get("asset"), held.get("song")
    if not isinstance(asset_id, str) or not asset_id or not isinstance(song, str) or not song:
        return None
    return asset_id, song
