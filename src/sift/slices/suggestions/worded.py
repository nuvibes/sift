# SPDX-License-Identifier: AGPL-3.0-or-later
"""The file-name pass's decisions and a Yes on a folder, worded when they are shown.

See `kernel.workbench.Recorded` for the rule. Three kinds of receipt, each of whose stored titles
has words this application no longer uses: "Filed under X on Instagram from the file's own name"
(the pass's phrase no longer says "own"), "Made a Photo Set of 6 pictures posted together"
("made"), and "Learned this account's Instagram number 10000000001 ..." ("account", "number":
the word is ID). Each is worded now from what the receipt recorded: the payload's kind, username,
Photo Set and counts, and the ledger's own verb, object and pass.

**One fact was written only into the filing's title**: which Site the username was on, as the
file's name read it. It is read back strictly from the one format the writer used
(`_FILED_ON`); a title that does not match it is said without the Site rather than guessed at.
"""

from __future__ import annotations

import re
from typing import Any

from sift.kernel.text import non_empty_str
from sift.kernel.vocabulary import VIA_FILENAME, VIA_METADATA
from sift.kernel.workbench import DOER, Named, Piece, Recorded, Worded

#: "Filed under <username> on <Site> from the file's (own) name" and the ID-carrying shapes.
_FILED_ON = re.compile(
    r"Filed under (?P<name>\S+) on (?P<site>.+?)"
    r"(?: from the file's (?:own )?name|: the file's name carries ID .+)"
)

#: How the pass read the username, by the pass that filed it.
_FROM: dict[str, tuple[str, str]] = {
    VIA_FILENAME: ("its file name", "their file names"),
    VIA_METADATA: ("its photo details", "their photo details"),
}


def filenames_said(recorded: Recorded) -> Worded | None:
    """One of the pass's three decisions, or a person's No on a whole username, as the act it
    was. None where it recorded too little."""
    held = recorded.held()
    kind = held.get("kind")
    if kind == "filed":
        return _filed(recorded, held)
    if kind == "post_set":
        return _post_set(recorded, held)
    if kind == "numbered":
        return _numbered(held)
    if kind == "declined":
        return _declined(recorded, held)
    return None


def folders_said(recorded: Recorded) -> Worded | None:
    """A Yes on a folder that named one person, as the act it was: who filed how many files under
    whom, from which folder, with what else the press wrote said underneath. None for a Yes that
    named a Site or a username, whose payload does not say which, and for a No."""
    held = recorded.held()
    written = held.get("written")
    if held.get("kind") != "confirmed" or not isinstance(written, dict):
        return None
    if recorded.object_kind != "person" or not recorded.object_id:
        return None
    folder_id = _first_folder(written, recorded)
    if folder_id is None:
        return None
    files = _rows(written, "attributed")
    person = Named(kind="person", id=recorded.object_id, recorded=recorded.object_name)
    folder = Named(kind="folder", id=folder_id)
    count = "1 file" if len(files) == 1 else f"{len(files):,} files"
    said: tuple[Piece, ...] = (DOER, f" filed {count} under ", person, " from the folder ", folder)
    return Worded(said=said, more=_confirmed_more(written))


def _rows(written: dict[str, Any], key: str) -> list[Any]:
    rows = written.get(key)
    return rows if isinstance(rows, list) else []


def _first_folder(written: dict[str, Any], recorded: Recorded) -> str | None:
    """The folder the question was about: the first standing answer the Yes wrote, else the first
    folder the receipt names."""
    for pair in _rows(written, "remembered"):
        if isinstance(pair, list) and pair and isinstance(pair[0], str) and pair[0]:
            return pair[0]
    return next((one.id for one in recorded.subjects if one.kind == "folder" and one.id), None)


def _confirmed_more(written: dict[str, Any]) -> tuple[Piece, ...]:
    """What else the Yes wrote, as the one line under it, or nothing."""
    parts: list[str] = []
    faces = len(_rows(written, "faces"))
    if faces:
        parts.append("1 face named" if faces == 1 else f"{faces:,} faces named")
    if _rows(written, "created_people"):
        parts.append("a new person added")
    if written.get("alias"):
        parts.append("the spelling remembered")
    if written.get("username_linked"):
        parts.append("the username linked")
    namesakes = len(_rows(written, "namesakes"))
    if namesakes:
        others = "1 other folder" if namesakes == 1 else f"{namesakes:,} other folders"
        parts.append(f"{others} with that name answered")
    if not parts:
        return ()
    said = ", ".join(parts)
    return (said[:1].upper() + said[1:] + ".",)


def _filed(recorded: Recorded, held: dict[str, Any]) -> Worded | None:
    username_id = held.get("username_id")
    if not isinstance(username_id, str) or not username_id:
        return None
    shape = _FILED_ON.fullmatch(recorded.title.strip())
    name = (
        recorded.object_name
        or non_empty_str(held.get("name"))
        or (shape["name"] if shape else None)
    )
    username = Named(kind="username", id=username_id, recorded=name)
    on: tuple[Piece, ...] = (f" on {shape['site']}",) if shape else ()
    # The pass that filed it: the ledger's own word since the pass said it, and the title's
    # phrase before that: every title this writer wrote said "from the file's name".
    how = _FROM.get(recorded.actor_id or "") or (_FROM[VIA_FILENAME] if shape else None)
    because: tuple[Piece, ...] = ()
    number = non_empty_str(held.get("number"))
    if number is not None:
        because = (f", which carries ID {number}",)
    if recorded.run > 1:
        said: tuple[Piece, ...] = (DOER, f" filed {recorded.run:,} files under ", username, *on)
        return Worded(said=(*said, f" from {how[1]}") if how else said)
    files = [one for one in recorded.subjects if one.kind == "asset"]
    assets = held.get("assets")
    asset_id = (
        files[0].id if files else (assets[0] if isinstance(assets, list) and assets else None)
    )
    if not isinstance(asset_id, str):
        return None
    what = Named(kind="asset", id=asset_id, recorded=files[0].name if files else None)
    said = (DOER, " filed ", what, " under ", username, *on)
    return Worded(said=(*said, f" from {how[0]}", *because) if how else said)


def _declined(recorded: Recorded, held: dict[str, Any]) -> Worded | None:
    """A person's No on a whole username: every file its names filed there taken back at once."""
    username_id = non_empty_str(held.get("username_id"))
    files = held.get("files")
    if username_id is None or not isinstance(files, list) or not files:
        return None
    name = recorded.object_name or non_empty_str(held.get("name"))
    username = Named(kind="username", id=username_id, recorded=name)
    site = non_empty_str(held.get("site"))
    on: tuple[Piece, ...] = (f" on {site}",) if site else ()
    count = "1 file" if len(files) == 1 else f"{len(files):,} files"
    return Worded(said=(DOER, f" removed {count} from ", username, *on))


def _post_set(recorded: Recorded, held: dict[str, Any]) -> Worded | None:
    photo_set_id = non_empty_str(held.get("photo_set_id"))
    assets = held.get("assets")
    if photo_set_id is None or not isinstance(assets, list) or not assets:
        return None
    called = next(
        (
            one.name
            for one in recorded.subjects
            if one.kind == "photo_set" and one.id == photo_set_id
        ),
        None,
    )
    photos = "1 photo" if len(assets) == 1 else f"{len(assets):,} photos"
    return Worded(
        said=(
            DOER,
            " created ",
            Named(kind="photo_set", id=photo_set_id, recorded=called, kind_said=True),
            f" from {photos} posted together",
        )
    )


def _numbered(held: dict[str, Any]) -> Worded | None:
    username_id = non_empty_str(held.get("username_id"))
    number = non_empty_str(held.get("number"))
    # The old key on receipts written before the Site was called a Site; `site` since.
    site = non_empty_str(held.get("site")) or non_empty_str(held.get("platform"))
    agreed = held.get("agreed")
    if username_id is None or number is None or site is None or not isinstance(agreed, int):
        return None
    photos = "1 photo" if agreed == 1 else f"{agreed:,} photos"
    return Worded(
        said=(
            DOER,
            f" matched {site} ID {number} to ",
            Named(kind="username", id=username_id, recorded=non_empty_str(held.get("name"))),
            f" from the details of {photos}",
        )
    )
