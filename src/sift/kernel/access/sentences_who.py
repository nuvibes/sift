# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who did it: the words for the person, the pass or the stash-box at the front of a line."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.access.sentences_pieces import (
    A_STASH_BOX,
    ANOTHER_USER,
    HERE,
    SIFT,
    VANTAGE_FILE,
    YOU,
    Line,
    Part,
    _fill,
    and_then,
    capitalized,
    many,
    said,
)

# --- who did it ----------------------------------------------------------------------------------


def by_word(actor: str, name: str | None = None) -> str | None:
    """Who did it, as the first word of the line, or None where the record names nobody.

    `name` is the stash-box's or, for an admin, the other user's. None is `somebody`, and the line
    is passive rather than guessing "You".
    """
    if actor == "you":
        return YOU
    if actor == "sift":
        return SIFT
    if actor == "stash_box":
        return name or A_STASH_BOX
    if actor == "another_user":
        return name or ANOTHER_USER
    return None


#: WHERE A TASK READ WHAT IT DECIDED, said at the back of the line, by the stored word; a word this
#: build has no phrase for says nothing.
FROM_PASS: Mapping[str, str] = {
    "folder": "from a folder name",
    "filename": "from the file's name",
    "metadata": "from file metadata",
    "watermark": "from a watermark",
    "username": "from a username",
    "mirror": "from a mirror folder",
    "download": "from a download",
    "faces": "from a face",
    "stash": "from a stash-box answer",
    "produced": "from a file it created",
    "archive": "from an archive",
    "shoot": "from a shoot",
    # A file that arrived from another install, and the People, Sites and Usernames it arrived
    # under (`vocabulary.VIA_SWAP`). "by", not "from": the phrase says how it came, and where it
    # came from is the other install's device id, which the line names itself.
    "swap": "by swap",
    # The People, Sites and Tags a Stash library imported (`vocabulary.VIA_STASH_LIBRARY`).
    "stash_library": "from a Stash library",
    # One Stash attached to nothing (`vocabulary.VIA_STASH_UNATTACHED`).
    "stash_unattached": "from a Stash library, attached to nothing there",
    # A row carried onto a file from a copy of it (`catalog.COPIED`).
    "copy": "from a copy of this file",
    # A song AcoustID named, made where no song carried its recording
    # (`vocabulary.VIA_MUSIC_LOOKUP`).
    "music_lookup": "from AcoustID",
    # A person made from a facial fingerprints file or a folder of somebody's pictures
    # (`vocabulary.VIA_FACIAL_FINGERPRINTS`).
    "facial_fingerprints": "from facial fingerprints",
}

#: THE PAYLOAD KEY NAMING THE STASH-BOXES WHOSE ANSWERS A `stash` ACT CAME FROM, by name: one name
#: or a list. Where a line can read it, the back of the line names them rather than "a stash-box".
ANSWER_OF = "answer_of"


def from_answer_of(boxes: object) -> str | None:
    """The back of a line stash-box answers led Sift to write, naming the boxes, or None."""
    held = [boxes] if isinstance(boxes, str) else boxes if isinstance(boxes, list) else []
    names = [f"{one}'s" for one in held if isinstance(one, str) and one]
    if not names:
        return None
    if len(names) == 1:
        return f" from {names[0]} answer"
    return f" from {', '.join(names[:-1])} and {names[-1]} answers"


#: THE SAME TASKS' PHRASES WHEN ONE LINE COUNTS MANY FILES; a task with none reads its one phrase.
FROM_PASS_MANY: Mapping[str, str] = {
    "filename": "from file names",
    "folder": "from folder names",
}

#: The two words a stash-box's act is stored under: the rows' `stash_box`, the filter's `stash`. The
#: box is the actor of those lines, so no task is said at the back of them.
_BOX_WORDS = frozenset({"stash_box", "stash"})


def from_pass(via: object, count: int = 1) -> str:
    """The task's phrase with its leading space, or nothing; past one file, the plural phrase."""
    if via is None:
        return ""
    phrase = (FROM_PASS_MANY.get(str(via)) if count != 1 else None) or FROM_PASS.get(str(via))
    return f" {phrase}" if phrase else ""


#: WHICH ACT made the file a `produced` row came from, said as the row's own page says it.
FROM_ACT: Mapping[str, str] = {
    "compress": "from a file it compressed",
    "edit": "from a file it edited",
}


def from_maker(via: object, act: object) -> str:
    """`from_pass`, naming the act where the `produced` pass made the row and kept the act."""
    if via == "produced" and isinstance(act, str) and act in FROM_ACT:
        return f" {FROM_ACT[act]}"
    return from_pass(via)


#: What Sift says about itself BESIDE a line rather than as its actor, for the one reader that
#: wants a name for the actor on its own (`history.maker_of`), which is not drawn.
SIFT_FROM: Mapping[str, str] = {
    **{word: f"Sift, {phrase}" for word, phrase in FROM_PASS.items()},
    # The tasks that read no name and make no row, each named by what it does.
    "fingerprint": "Sift, generating fingerprints",
    "photo_set_floor": "Sift, deleting Photo Sets with too few photos",
    "update": "Sift, updating to a new version",
    "music_lookup": "Sift, looking up songs",
    "backup": "Sift, from Backup and restore",
    "benchmark": "Sift, from the benchmark of this device",
    "insights": "Sift, adding up Insights",
}


def sift_from(via: object) -> str:
    """ "Sift" on its own, or Sift and the task, where the row says which."""
    return SIFT if via is None else SIFT_FROM.get(str(via), SIFT)


#: THE WORDS A SAVED TITLE CAN STILL CARRY that this application no longer says, and today's: only
#: phrases that name one act exactly, so the swap cannot change what a title says happened.
STALE_IN_A_TITLE: Mapping[str, str] = {
    "from the file's own name": FROM_PASS["filename"],
    "Set aside": "Discarded",
    "set aside": "discarded",
    # The word for a Photo Set that Sift creates is Created (the verb table); older receipts say
    # Made.
    "Made a Photo Set": "Created a Photo Set",
    # A discarded group brought back: the word is restored (the History word table).
    "put back": "restored",
}


def today_words(title: str) -> str:
    """A saved title with its retired phrases said the way they are said now. See above."""
    for stale, now in STALE_IN_A_TITLE.items():
        title = title.replace(stale, now)
    return title


def _active(by: str | None, active: Part, passive: Part) -> Line:
    """The active line where the record names who, the passive one where it does not."""
    if by is None:
        return capitalized(said(passive))
    return said(by, " ", active)


def had_sift(by: str, asked: str, then: Part = None) -> Line:
    """A pass somebody pressed, said as theirs: "wren had Sift look for faces here, and it found none".
    `by` is who pressed, `asked` what they had Sift do, `then` what came of it."""
    if then is None or then == "":
        return said(f"{by} had Sift {asked}")
    return said(f"{by} had Sift {asked}, and ", then)


#: WHAT A PRESS OF EACH PASS HAD SIFT DO TO A FILE, by pass key: `{file}` the file, `{what}` what
#: several alike passes made, in the words the pass's own line says.
PRESSED_ACTS: Mapping[str, tuple[str, str]] = {
    **dict.fromkeys(("thumbnails", "thumbnail"), ("generate {what} for {file}", "a thumbnail")),
    **dict.fromkeys(("previews", "preview"), ("generate {what} for {file}", "a hover preview")),
    **dict.fromkeys(("sprites", "sprite"), ("generate {what} for {file}", "a scrubbing strip")),
    "loop_thumbnail": ("generate {what} for {file}", "a Loop's still"),
    "remux": ("generate {what} for {file}", "a repacked copy"),
    **dict.fromkeys(
        ("fingerprints", "fingerprint_file", "fingerprint_stash_box"),
        ("generate {what} for {file}", "fingerprints"),
    ),
    **dict.fromkeys(
        ("music", "audio_fingerprint"), ("generate {what} for {file}", "a music fingerprint")
    ),
    "music_lookup": ("ask AcoustID about {file}", ""),
    **dict.fromkeys(("faces", "face_scan"), ("look for faces in {file}", "")),
    **dict.fromkeys(("meaning", "semantic_describe"), ("index {file} for Smart Search", "")),
    **dict.fromkeys(("watermarks", "watermark_read"), ("look for a watermark on {file}", "")),
    **dict.fromkeys(("details", "probe"), ("read the details of {file} again", "")),
    "stash_box_scan": ("ask a stash-box about {file}", ""),
}


def pressed_act(passes: Sequence[str], file: Part) -> Line:
    """What one press had Sift do to `file` (`PRESSED_ACTS`); an unknown pass is said as the task."""
    whats: dict[str, list[str]] = {}
    for one in passes:
        if one in PRESSED_ACTS:
            template, what = PRESSED_ACTS[one]
            kept = whats.setdefault(template, [])
            if what and what not in kept:
                kept.append(what)
    if not whats:
        return said("run a task on ", file)
    acts = [
        _fill(template, {"what": said(and_then(made)), "file": said(file)})
        for template, made in whats.items()
    ]
    return said(*[part for at, act in enumerate(acts) for part in (" and " if at else "", act)])


def times_said(times: int) -> str:
    """How many presses one line stands for, said after the act: nothing for one, " twice"."""
    if times <= 1:
        return ""
    return " twice" if times == 2 else f" {many(times)} times"


def pressed_here(by: str, passes: Sequence[str], times: int = 1) -> Line:
    """A press no pass line says, "You had Sift look for faces in this file", folded "... 3 times"."""
    return said(by, " had Sift ", pressed_act(passes, HERE[VANTAGE_FILE]), times_said(times))
