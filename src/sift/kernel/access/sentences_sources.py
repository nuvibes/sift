# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines for the sources Sift wrote down about a file and never read as a ledger event."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from sift.kernel.access.sentences_pieces import SIFT, Line, Part, and_then, many, said
from sift.kernel.access.sentences_who import _active, had_sift

# --- the sources that were written down and never read -------------------------------------------


def watermark_found(
    kind: object,
    text: object,
    site: Part,
    username: object,
    *,
    filed: bool,
    by: str | None = None,
) -> Line:
    """A watermark Sift found on the picture: what it was, what it said, and what came of it.

    A Site's mark that filed something names the Site; one that filed nothing says its letters,
    since a near reading names a guessed Site. `filed` comes from the rows the filing lines are
    drawn from, so the two cannot disagree. `by` is who pressed the look.
    """
    found = _watermark_found(kind, text, site, username, filed=filed)
    if by is None:
        return found
    return had_sift(by, "look for a watermark", said("it ", _after_sift(found)))


def _after_sift(line: Line) -> Line:
    """A Sift line without its first word, to be said after another subject ("found a watermark").
    Every shape of `_watermark_found` opens with Sift's name in plain words, so the first piece is
    always text to take it from."""
    head, *rest = line
    return (replace(head, text=head.text.removeprefix(f"{SIFT} ")), *rest)


def _watermark_found(
    kind: object, text: object, site: Part, username: object, *, filed: bool
) -> Line:
    """The five shapes of `watermark_found`, as Sift's own line."""
    if kind == "channel":
        return said(f"Sift found a Telegram channel's watermark, {text}, which names no Site")
    if kind == "username":
        if filed:
            return said(f"Sift found the username {text} in a watermark")
        return said(f"Sift found the username {text} in a watermark and filed nothing")
    if kind == "notice":
        if not filed:
            return said(f"Sift found a distributor's watermark, {text}, and filed nothing")
        if site:
            return said("Sift found a distributor's watermark, which marks ", site, " material")
        return said(f"Sift found a distributor's watermark, {text}")
    if not filed:
        return said(f"Sift found a watermark, {text}, and filed nothing")
    if not site:
        return said(f"Sift found the watermark {text}")
    if username:
        return said(f"Sift found the watermark {username} on ", site)
    return said("Sift found a watermark of ", site)


def watermark_none(*, by: str | None = None) -> Line:
    """A look for a watermark that found nothing. `by` is who pressed it, where somebody did."""
    if by is not None:
        return had_sift(by, "look for a watermark", "it found none")
    return said("Sift looked for a watermark and found none")


def watermark_refused(by: str | None = None) -> Line:
    """A filing from a watermark was undone, and the refusal keeps the next sweep from redoing it.

    Not "You said the watermark was wrong": the table records no user at all. The one writer of a
    refusal is the Undo of that filing
    (`watermarks.service.take_back`), so what the row knows is the act and its reach.
    """
    return _active(
        by,
        "permanently undid the filing from its watermark",
        "the filing from its watermark was permanently undone",
    )


def downloaded(
    site: Part, username: object, arrived_as: object = None, *, by: str | None = SIFT
) -> Line:
    """Where the file was downloaded from: the most useful line a downloaded file can carry. With
    the name it arrived under where this download is what brought it in, which is its arrival.

    `by` is who asked for it (`by_word`): "You" for the viewer's own paste, a user's name for an
    admin, Sift for a download nobody pressed for (or one from before that was recorded)."""
    who = by or SIFT
    tail = f" as {arrived_as}" if arrived_as else ""
    if site and username:
        return said(f"{who} downloaded this file from {username} on ", site, tail)
    if site:
        return said(f"{who} downloaded this file from ", site, tail)
    return said(f"{who} downloaded this file from the web{tail}")


#: What each kind of derivative IS, in words. `derivatives.kind` is a CHECK constraint; a kind with
#: no phrase is left out of the sentence rather than printed as the stored token.
MADE_READY: Mapping[str, str] = {
    "thumb": "a thumbnail",
    "preview": "a hover preview",
    "sprite": "a scrubbing strip",
    "rendition": "a copy that plays in a browser",
    "remux": "a repacked copy",
}


def made_ready(kinds: Sequence[str], *, by: str | None = None) -> Line:
    """The pictures and copies Sift generates so a file can be looked at, as ONE line. `by` is who
    pressed them, where somebody did."""
    named = [MADE_READY[one] for one in kinds if one in MADE_READY]
    if by is not None:
        if named:
            return had_sift(by, f"generate {and_then(named)} for this file")
        return had_sift(by, "prepare this file to play")
    if named:
        return said(f"Sift generated {and_then(named)} for this file")
    return said("Sift prepared this file to play")


def faces_taken_off(count: int) -> Line:
    """Appearances deleted from the file. No user is recorded, so passive."""
    return said(
        "A face was removed from this file"
        if count == 1
        else f"{many(count)} faces were removed from this file"
    )


def faces_set_aside(count: int) -> Line:
    """Appearances discarded rather than answered (the stored verb is `set_aside`)."""
    return said(
        "A face here was discarded" if count == 1 else f"{many(count)} faces here were discarded"
    )


def processed() -> Line:
    """One sitting of a file's routine lines folded into one: its pictures, its Smart Search index, a
    look for a watermark that found none, a stash-box that had never heard of it
    (`history._one_processed_line`)."""
    return said("Sift processed this file")


def processed_steps(count: int) -> str:
    """What "Sift processed this file" opens to, counted: "3 steps"."""
    return "1 step" if count == 1 else f"{many(count)} steps"


def read_its_meaning(*, by: str | None = None) -> Line:
    """The model that answers a search by meaning read this file. `by` is who pressed it."""
    if by is not None:
        return had_sift(by, "index this file for Smart Search")
    return said("Sift indexed this file for Smart Search")


def music_fingerprinted(*, empty: bool, by: str | None = None) -> Line:
    """Sift made the music fingerprint of this file, or had no sound to make one of: the empty
    answer is said as plainly as the full one. `by` is who pressed it."""
    if by is not None:
        if empty:
            return had_sift(
                by, "generate a music fingerprint for this file", "it could not read any sound"
            )
        return had_sift(by, "generate a music fingerprint for this file")
    if empty:
        return said("Sift could not read any sound in this file to fingerprint")
    return said("Sift generated a music fingerprint for this file")


def nothing_to_fingerprint(by: str, about: Part) -> Line:
    """The read for duplicate fingerprints gave nothing to compare (every fingerprint came back
    empty), said as plainly as a full one: "Sift found nothing to fingerprint in this file"."""
    return said(by, " found nothing to fingerprint in ", about)


def details_read_again(*, by: str | None = None) -> Line:
    """The file's size, length and kind read again from disk, after the sitting it arrived in. `by`
    is who pressed it, where somebody did."""
    if by is not None:
        return had_sift(by, "read this file's details again")
    return said("Sift read this file's details again")


def fingerprinted_by(by: str, *, empty: bool) -> Line:
    """The fingerprints duplicates and the stash-boxes match on, made because somebody pressed them:
    the pressed form of the ledger's `scanned` line ("Sift generated fingerprints for this file"),
    and of its empty answer ("Sift found nothing to fingerprint in this file")."""
    if empty:
        return had_sift(
            by, "generate fingerprints for this file", "it found nothing to fingerprint"
        )
    return had_sift(by, "generate fingerprints for this file")


#: WHAT ACOUSTID ANSWERED, by the status its kept answer carries (`music_lookups.status`). A named
#: song is said by the song's own line where the file was given it (`song_named`); this is the line
#: for the answers that named nothing on the file. AcoustID is somebody else's service, said the
#: way a stash-box is ("Sift asked StashDB and nothing matched"): Sift asked, the service answered.
ACOUSTID_SAID: Mapping[str, str] = {
    "nothing": "Sift asked AcoustID and it didn't know the song",
    # The two answers the lookup keeps owed: a press of the task asks about the file again.
    "failed": "Sift asked AcoustID and got no answer, so this file will be asked about again",
    "refused": "Sift asked AcoustID and it refused, so this file will be asked about again",
}

#: What AcoustID answered, said after a press of the ask: "wren had Sift ask AcoustID, and ...".
ACOUSTID_SAID_PRESSED: Mapping[str, str] = {
    "nothing": "it didn't know the song",
    "failed": "no answer came, so this file will be asked about again",
    "refused": "it refused, so this file will be asked about again",
}


def acoustid_answered(status: str, title: str | None = None, *, by: str | None = None) -> Line:
    """AcoustID asked about this file, and what it said; `named` only where the song's own line is
    not on the file. `by` is who pressed the ask."""
    if by is not None:
        if status == "named":
            song = (title or "").strip()
            return had_sift(
                by, "ask AcoustID", f"it named the song {song}" if song else "it named a song"
            )
        answer = ACOUSTID_SAID_PRESSED.get(status)
        if answer is None:
            return had_sift(by, "ask AcoustID about this file")
        return had_sift(by, "ask AcoustID", answer)
    if status == "named":
        song = (title or "").strip()
        return said(
            f"Sift asked AcoustID and it named the song {song}"
            if song
            else "Sift asked AcoustID and it named a song"
        )
    return said(ACOUSTID_SAID.get(status, "Sift asked AcoustID about this file"))


#: WHAT A PASS COULD NOT DO FOR ONE FILE, by the product its verdict is filed under
#: (`file_verdicts.product`, `identity.VerdictProduct`). Each says the act in the words of the line
#: the same pass draws when it does it, so "could not" is the only difference between the two.
COULD_NOT: Mapping[str, str] = {
    "probe": "read this file's details",
    "thumbnails": "generate a thumbnail for this file",
    "previews": "generate a hover preview for this file",
    "sprites": "generate a scrubbing strip for this file",
    "fingerprints": "generate fingerprints for this file",
    "faces": "look for faces in this file",
    "meaning": "index this file for Smart Search",
    "watermarks": "look for a watermark on this file",
    "identity": "read this file to tell it apart from other files",
}


def could_not(product: str, *, again: bool, by: str | None = None) -> Line:
    """A pass gave up on this file; `again` is a verdict the pass keeps trying past, said with
    "yet". The act only: why is the wall's to say. `by` is who pressed the pass."""
    act = COULD_NOT.get(product, "finish a step for this file")
    if by is not None:
        return had_sift(by, act, f"it could not{' yet' if again else ''}")
    return said(f"Sift could not {act}{' yet' if again else ''}")


def person_refused(person: Part) -> Line:
    """Somebody said this person is not in this file, for good. No user recorded, so passive."""
    return said(person, " was marked as not in this file")


def hidden_here() -> Line:
    """The viewer concealed this file: the column's line, for a hide the ledger never saw."""
    return said("You hid this file")


def kept_mine(
    by: str | None, box: str, field: str, mine: str | None = None, theirs: str | None = None
) -> Line:
    """A stash-box disagreed about a field and the answer was to keep what was here, each value as
    the record draws it; passive where `by` is None."""
    if mine and theirs:
        return _active(
            by,
            f"kept your {field}, {mine}, over {box}'s {theirs}",
            f"your {field}, {mine}, was kept over {box}'s {theirs}",
        )
    return _active(
        by,
        f"kept your {field} over {box}'s answer",
        f"your {field} was kept over {box}'s answer",
    )
