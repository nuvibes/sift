# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines for a song moved, kept or merged."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.access.sentences_ledger import BROUGHT_OVER, Said, brought_over
from sift.kernel.access.sentences_pieces import (
    VANTAGE_PERSON,
    Line,
    Part,
    Piece,
    files,
    listed,
    many,
    said,
)
from sift.kernel.text import non_empty_str
from sift.kernel.vocabulary import BOXES_RECORDED, DEPARTURES_KEPT

#: The words after the song for a name AcoustID gave, and for one carried from a file with the same
#: music (`_song_line`); a payload with no source is read as a Site's page.
SONG_FROM_ACOUSTID = " from AcoustID"

SONG_FROM_SAME_MUSIC = ", from the same music as "

#: Where a song came with a file another library sent: the swap that brought the file in.
SONG_FROM_SWAP = " from a swap"


def departures_kept(by: str, payload: Mapping[str, object]) -> Line | None:
    """The one line Insights' version 6 step writes for a library (`insights.schema`): "Sift kept
    the 13 files deleted before this update, so their days keep their figures". None for any other
    `added`, which says itself.

    The files are what the step kept, so they are the number said; the rest is why, because kept
    deletions nobody asked for read as something going wrong.
    """
    kept = payload.get(DEPARTURES_KEPT)
    if not isinstance(kept, int) or isinstance(kept, bool) or kept < 1:
        return None
    return said(
        by,
        f" kept the {files(kept)} deleted before this update, so {'its' if kept == 1 else 'their'}"
        " days keep their figures",
    )


def boxes_recorded(by: str, payload: Mapping[str, object]) -> Line | None:
    """The one line the stash-box feature's version 21 step writes for a library
    (`stash_boxes.filed_by`): "Sift recorded which stash-box added the people, tags and Sites on
    12 files". None for any other `added`."""
    boxed = payload.get(BOXES_RECORDED)
    if not isinstance(boxed, int) or isinstance(boxed, bool) or boxed < 1:
        return None
    return said(by, f" recorded which stash-box added the people, tags and Sites on {files(boxed)}")


def songs_moved(by: str, payload: Mapping[str, object], count: int | None) -> Line | None:
    """The one line catalog step 81 writes for a library (`songs.move_named_songs`), both numbers
    said, and the one the music feature's step 5 writes (`songs.credit_kept_answers`). None for any
    other `added`."""
    credited = payload.get("credited")
    if isinstance(credited, int) and not isinstance(credited, bool) and credited >= 1:
        return said(
            by,
            f" added the artists AcoustID named to {many(credited)}"
            f" {'song' if credited == 1 else 'songs'}",
        )
    made = payload.get("songs")
    if not isinstance(made, int) or isinstance(made, bool) or made < 1:
        return None
    on = files(count) if count is not None else "files"
    return said(
        by, f" created {many(made)} {'song' if made == 1 else 'songs'} from the songs named on {on}"
    )


def _song_line(
    by: str,
    song: str,
    file: Part,
    site: Part,
    site_is_page: bool,
    source: object = None,
    here: str = "this file",
    device: object = None,
) -> Line:
    """A song Sift named: on the file, on the page of what it was named from, or in the feed.

    `source` is the payload's: None or `site` for a Site's page (`site` is that Site), `acoustid`
    for AcoustID's answer (nothing in the library to link), `shared` for a name that came from
    another file sharing the song (`site` is then THAT file, and `site_is_page` means this page is
    it, called `here`), `swap` for the song a file came with from another library ("Sift named the
    song Tidewater on this file from a swap with device ABCD-EFGH-..." where the act names the
    device). One sentence per source, actor first, no dash joining a second fact on.
    """
    if source == "swap":
        named = non_empty_str(device)
        whence = SONG_FROM_SWAP + (f" with device {named}" if named else "")
        if file is None:
            return said(by, f" named the song {song} on {here}{whence}")
        return said(by, f" named the song {song} on ", file, whence)
    if source == "acoustid":
        if file is None:
            return said(by, f" named the song {song}{SONG_FROM_ACOUSTID}")
        return said(by, f" named the song {song} on ", file, SONG_FROM_ACOUSTID)
    if source == "shared":
        origin: Part = here if site_is_page else (site if site is not None else "another file")
        if file is None:
            return said(by, f" named the song {song}{SONG_FROM_SAME_MUSIC}", origin)
        return said(by, f" named the song {song} on ", file, SONG_FROM_SAME_MUSIC, origin)
    if site_is_page:
        return said(by, f" named the song {song} on ", file, " from this Site's page")
    if file is None:
        if site is None:
            return said(by, f" named the song {song} from its download page")
        return said(by, f" named the song {song} from ", site, "'s page")
    if site is None:
        return said(by, f" named the song {song} on ", file, " from its download page")
    return said(by, f" named the song {song} on ", file, " from ", site, "'s page")


def merged_line(
    by: str,
    *,
    others: Sequence[Piece],
    here: str | None,
    into: Piece | None,
    brought: Mapping[str, object] | None,
    kind: str | None,
) -> Said:
    """A merge, read on the survivor's page (`into` None), the page of the one going, or the feed.

    On the survivor's page the line names who was merged in and folds what came over under "Show
    each"; a merge that brought nothing says so in words, and an older merge that recorded nothing
    says only that it happened.
    """
    who = listed([(one,) for one in others]) or said("something")
    if here is not None and into is None:
        line = said(by, " merged ", who, " into ", here)
        if not brought:
            return Said(line)
        members = brought_over(brought, kind)
        if not members:
            return Said(said(line, ", and nothing came over"))
        return Said(line, folded=(BROUGHT_OVER, members))
    target: Part = into if into is not None else "something"
    return Said(said(by, " merged ", here if here is not None else who, " into ", target))


def as_then(line: Line, name: str, here: str = VANTAGE_PERSON) -> Line:
    """A line about an act taken under ANOTHER name (somebody merged into this page), said so."""
    if here == VANTAGE_PERSON:
        return said(line, f", when they were called {name}")
    return said(line, f", when it was called {name}")
