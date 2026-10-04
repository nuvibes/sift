# SPDX-License-Identifier: AGPL-3.0-or-later
"""The FEED table, one act per ledger verb, and the templates that fill it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sift.kernel.access.sentences_file import copy_verb
from sift.kernel.access.sentences_pieces import SIFT, YOU, Line, _fill, said
from sift.kernel.access.sentences_who import ANSWER_OF, FROM_PASS, from_answer_of
from sift.kernel.text import non_empty_str
from sift.kernel.vocabulary import VIA_STASH

# --- one act, one line: every ledger verb, at every vantage and in the feed ----------------------


@dataclass(frozen=True, slots=True)
class Act:
    """One verb, said: the line with an object, the line without, and what a count counts. A page
    fills the slot that is ITSELF with its vantage word."""

    line: str
    alone: str | None = None
    #: What a COUNT counts where this act stands for a task rather than for one thing.
    counted: str = "files"
    #: WHICH KIND OF SUBJECT THE FEED NAMES, where naming all of them would be untrue: a delete
    #: names the file that went and everything it was on. A PREFERENCE, not a filter: an event
    #: naming nothing of this kind says what it does name rather than "something".
    about_kind: str | None = None


#: THE TWO REFUSALS ON A FOLDER, said with the word "folder": a bare folder name reads as a
#: person or a tag, and the mark reaches every file under the folder, which the word says. The
#: words around the folder's name, per act; `FEED` says the same acts about anything else.
FOLDER_REFUSALS: Mapping[str, tuple[str, str]] = {
    "kept_local": (" turned off stash-box lookups for the folder ", ""),
    "allowed": (" turned on stash-box lookups for the folder ", ""),
    "kept_from_swaps": (" kept the folder ", " out of swaps"),
    "allowed_in_swaps": (" let the folder ", " into swaps again"),
}

#: EVERY ACT THE LEDGER CAN HOLD, one template each. `test_sentences` holds its keys to
#: `kernel.ledger.VERBS` both ways: a verb nothing says draws "changed" on the screen that exists to
#: explain the library, and a template nothing writes is a filter that always answers empty.
FEED: Mapping[str, Act] = {
    "added": Act("{by} added {subjects} to {object}", "{by} added {subjects} to the library"),
    "removed": Act("{by} removed {object} from {subjects}", "{by} removed {subjects}"),
    "renamed": Act("{by} renamed {subjects} from {was}", "{by} renamed {subjects}"),
    "named": Act("{by} named {object} on {subjects}", "{by} named {subjects}"),
    "filed": Act("{by} filed {subjects} under {object}", "{by} filed {subjects}"),
    "moved": Act("{by} moved {subjects} to {object}", "{by} moved {subjects}"),
    # Faces attached to a person carry a count of FACES; calling them files would read perfectly
    # and say the wrong thing.
    "linked": Act("{by} linked {subjects} to {object}", "{by} linked {subjects}", counted="faces"),
    "unlinked": Act(
        "{by} removed {subjects} from {object}", "{by} removed {subjects}", counted="faces"
    ),
    "hidden": Act("{by} hid {subjects}"),
    "revealed": Act("{by} unhid {subjects}"),
    # Only a guest can be shared with (`sharing.service._require_guest`), so the line says so: a
    # bare name after "with" reads as another user of this install.
    "shared": Act("{by} shared {subjects} with guest {object}", "{by} shared {subjects}"),
    "unshared": Act(
        "{by} stopped sharing {subjects} with guest {object}", "{by} stopped sharing {subjects}"
    ),
    "kept_local": Act("{by} turned off stash-box lookups for {subjects}"),
    "allowed": Act("{by} turned on stash-box lookups for {subjects}"),
    "kept_from_swaps": Act("{by} kept {subjects} out of swaps"),
    "allowed_in_swaps": Act("{by} let {subjects} into swaps again"),
    "merged": Act("{by} merged {subjects} into {object}", "{by} merged {subjects}"),
    "edited": Act("{by} edited {subjects}"),
    "enriched": Act(
        "{by} filled in {subjects} from {object}", "{by} filled in {subjects} from a stash-box"
    ),
    "asked": Act("{by} asked {object} about {subjects}", "{by} asked a stash-box about {subjects}"),
    "scanned": Act("{by} generated fingerprints for {subjects}"),
    "face_run": Act("{by} looked for faces in {subjects}"),
    # A pass somebody pressed over a file: which pass is in the payload (`pressed_act`).
    "pressed": Act("{by} had Sift run a task on {subjects}"),
    "produced": Act("{by} created {subjects} from {object}", "{by} created {subjects}"),
    "deleted": Act("{by} deleted {subjects}", about_kind="asset"),
    # The faces feature's "Delete face data": its subject is the feature's switch, and its count is
    # the files that lost a name it had added. See `feed_line`.
    "forgot": Act("{by} deleted everything in {subjects}"),
    # A DOWNLOAD NAMES ONE OF ITS TWO SUBJECTS: both events name the Site as well, so its own thread
    # can find them. The one that landed names the file; the one that gave up has no file and names
    # the queue row, whose name is the address somebody pasted.
    "downloaded": Act(
        "{by} downloaded {subjects} from {object}", "{by} downloaded {subjects}", about_kind="asset"
    ),
    "download_failed": Act(
        "{by} could not download {subjects} from {object}",
        "{by} could not download {subjects}",
        about_kind="download",
    ),
    "paused": Act("{by} paused {subjects}"),
    "resumed": Act("{by} resumed {subjects}"),
    "cookies_saved": Act("{by} saved cookies for {subjects}"),
    "cookies_replaced": Act("{by} replaced the cookies for {subjects}"),
    "cookies_forgotten": Act("{by} deleted the cookies for {subjects}"),
    "canceled": Act("{by} canceled {subjects}"),
    "saved": Act("{by} saved {subjects} to a device"),
    # A THEATER WALL SENT between one person's own devices: said from the payload by `_template`,
    # which names the kind of device it went to and the one it came from.
    "wall_sent": Act("{by} sent {subjects} to Theater on another device"),
    "ran": Act("{by} ran {subjects}"),
    # A song Sift named: from the page a file was downloaded from, from AcoustID, or shared from
    # another file with the same song. Said by `_song_line`, by the payload's `source`: the song is
    # the payload's, and the page (or the file it was shared from) is the object's.
    "song_named": Act(
        "{by} named the song {song} on {subjects} from {object}'s page",
        "{by} named the song {song} on {subjects} from its download page",
        about_kind="asset",
    ),
    # A judgement taken on a queue says the words its receipt was written with. See `feed_line`.
    "decided": Act("{by} decided about {subjects}"),
    # A SWAP WITH ANOTHER SIFT: said from the payload by `swap_started` and `swap_ended`, before
    # either table is read: the session they are about has no name a line may say.
    "swap_started": Act("{by} started a swap"),
    "swap_ended": Act("The swap ended"),
    # A BACKUP RESTORED as this library: the subject is the backup file, named by its day.
    "restored": Act("{by} restored this library from {subjects}"),
    # A LIBRARY MADE FROM A SIFT DATABASE FILE: the subject is the file, named by its name.
    # "Created", the History word for something Sift makes.
    "adopted": Act("{by} created this library from {subjects}"),
    # AN ACT ON THE COMPUTER RUNNING SIFT, asked from a window: the subject is the computer, named
    # by the name it gives itself. Said by `machine_line`, which adds the version or the library
    # the act names and the device it was asked from; these are the lines where nothing else is.
    "sharing_turned_on": Act("{by} turned on network sharing on {subjects}"),
    "sharing_turned_off": Act("{by} turned off network sharing on {subjects}"),
    "start_with_windows_on": Act("{by} set Sift to start with Windows on {subjects}"),
    "start_with_windows_off": Act("{by} stopped Sift starting with Windows on {subjects}"),
    "firewall_opened": Act("{by} opened the firewall port for Sift on {subjects}"),
    "storage_moved": Act("{by} moved Sift data to another folder on {subjects}"),
    "update_started": Act("{by} started installing a newer Sift on {subjects}"),
    "library_opened": Act("{by} opened another library on {subjects}"),
    "restarted": Act("{by} restarted Sift on {subjects}"),
}

#: The acts on the computer running Sift, which `machine_line` says. See `FEED`.
MACHINE_ACTS: frozenset[str] = frozenset(
    {
        "sharing_turned_on",
        "sharing_turned_off",
        "start_with_windows_on",
        "start_with_windows_off",
        "firewall_opened",
        "storage_moved",
        "update_started",
        "library_opened",
        "restarted",
    }
)

#: `linked` and `unlinked`, said per kind of thing the link was made TO: a person is NAMED in a
#: file, a tag is ADDED to one, a file is FILED under a Site and ADDED to a Collection.
_LINKED: Mapping[str, tuple[str, str]] = {
    "person": ("{by} named {object} in {subjects}", "{by} removed {object} from {subjects}"),
    "tag": (
        "{by} added the tag {object} to {subjects}",
        "{by} removed the tag {object} from {subjects}",
    ),
    "site": ("{by} filed {subjects} under {object}", "{by} removed {subjects} from {object}"),
    "collection": ("{by} added {subjects} to {object}", "{by} removed {subjects} from {object}"),
    "photo_set": ("{by} added {subjects} to {object}", "{by} removed {subjects} from {object}"),
    # A file put on a song by hand is said in the family every song's naming is said in
    # (`_song_line`): the song is NAMED on the file, and taken off it.
    "song": (
        "{by} named the song {object} on {subjects}",
        "{by} removed the song {object} from {subjects}",
    ),
}

#: `linked` and `unlinked` read on the TAG'S OWN PAGE, where the tag is "it": the plain template
#: would say "You removed the tag it from 730f.mp4".
_LINKED_TAG_PAGE = ("{by} added {object} to {subjects}", "{by} removed {object} from {subjects}")

#: `linked` and `unlinked` read on the SONG'S OWN PAGE, in the words its own additions are said in
#: (`song_named_here`): "You named this song on x.mp4", never "the song it".
_LINKED_SONG_PAGE = ("{by} named this song on {subjects}", "{by} removed this song from {subjects}")

#: A username joined to a person, which "named in" would misread as a file of that name.
_LINKED_USERNAME = (
    "{by} added the username {subjects} to {object}",
    "{by} removed the username {subjects} from {object}",
)

#: The line for a verb this build has no template for: true, and says less.
_SOMETHING = "{by} changed {subjects}"

#: WHY A TASK DID WHAT IT DID, said at the back of a line Sift wrote, by the task's own word.
#: `FROM_PASS` for the tasks that read a name, and the two whose act says its own reason.
TASK_TAIL: Mapping[str, str] = {word: f" {phrase}" for word, phrase in FROM_PASS.items()}

#: The acts whose line already says which task did them: saying the task again at the back would
#: be the same fact twice ("Sift downloaded this file from Sunsetter from a download").
_TASK_SAID_BY_THE_ACT = frozenset(
    {
        "downloaded",
        "download_failed",
        "song_named",
        "scanned",
        "face_run",
        "ran",
        "decided",
        "enriched",
    }
)


def _with_task(
    line: Line,
    by: str | None,
    verb: str,
    task: str | None,
    payload: Mapping[str, object] | None = None,
) -> Line:
    """A line Sift wrote, with the task that wrote it said at the back where the act does not; a
    stash-box's answer named by its box where the payload carries it (`ANSWER_OF`)."""
    if by != SIFT or task is None or verb in _TASK_SAID_BY_THE_ACT:
        return line
    box = from_answer_of((payload or {}).get(ANSWER_OF)) if task == VIA_STASH else None
    return said(line, box or TASK_TAIL.get(task, ""))


#: The two kinds of device a wall is sent to, as a line names them.
_WALL_GOES_TO: Mapping[str, str] = {"desk": "computer", "phone": "phone"}


def _wall_sent(payload: Mapping[str, object], by: str | None) -> str:
    """A wall sent to Theater on which kind of device, and from which, where recorded. The sending
    device's own words are quoted as they came."""
    whose = "your" if by == YOU else "their"
    device = _WALL_GOES_TO.get(non_empty_str(payload.get("to")) or "")
    line = (
        f"{{by}} sent {{subjects}} to Theater on {whose} {device}"
        if device
        else FEED["wall_sent"].line
    )
    came_from = non_empty_str(payload.get("from"))
    return f"{line}, from {came_from}" if came_from and "{" not in came_from else line


def _backup_saved(folder: object) -> str:
    """A backup saved from the Backup pane, and the folder it went to on the computer running Sift.
    Quoted as it came, and only where it cannot read as a slot."""
    where = non_empty_str(folder)
    if where and "{" not in where and "}" not in where:
        return f"{{by}} saved {{subjects}} in {where}"
    return "{by} saved {subjects} in the backup folder"


def _machine_template(verb: str, payload: Mapping[str, object]) -> str:
    """The line for one act on the computer running Sift, with the version or library it named."""
    version = non_empty_str(payload.get("version"))
    if verb == "update_started" and version and "{" not in version:
        return f"{{by}} started installing Sift {version} on {{subjects}}"
    library = non_empty_str(payload.get("library"))
    if verb == "library_opened" and library and "{" not in library:
        return f"{{by}} opened the library {library} on {{subjects}}"
    return FEED[verb].line


def _asked_from(payload: Mapping[str, object]) -> str:
    """Which device an act on the computer running Sift was asked from, as the end of its line: the
    window's name for its computer, or "another computer" for a browser. Quoted as it came."""
    if payload.get("here") is True:
        return ", from its own screen"
    device = non_empty_str(payload.get("from"))
    if device and "{" not in device:
        return f", from {device}"
    return ", from another computer"


def machine_line(verb: str, by: str, subjects: Line, payload: Mapping[str, object]) -> Line:
    """One act on the computer running Sift: who, what, on which computer, from which device."""
    line = _fill(_machine_template(verb, payload), {"by": said(by), "subjects": subjects})
    return said(line, _asked_from(payload))


def _template(
    verb: str,
    object_kind: str | None,
    first_kind: str | None,
    has_object: bool,
    payload: Mapping[str, object] | None = None,
    *,
    object_is_page: bool = False,
    by: str | None = None,
) -> str:
    """The one template this act is said with, given what it was done with: a copy says the verb
    that made it, a username arriving says so, a tag's page is "it", a save says whose device."""
    if verb == "saved" and first_kind == "backup":
        return _backup_saved((payload or {}).get("folder"))
    if verb == "saved":
        return f"{{by}} saved {{subjects}} to {'your' if by == YOU else 'their'} device"
    if verb == "wall_sent":
        return _wall_sent((payload or {}), by)
    if verb == "produced":
        word = copy_verb((payload or {}).get("operation"))
        return (
            f"{{by}} {word} {{subjects}} from {{object}}"
            if has_object
            else f"{{by}} {word} {{subjects}}"
        )
    if verb == "added" and first_kind == "username" and has_object:
        return "{by} added the username {subjects} to {object}"
    # An alias taken off a person names the alias; without it the line reads "You removed them",
    # which says nothing about what went. Quoted as it came, and only where it cannot read as a slot.
    alias = non_empty_str((payload or {}).get("alias"))
    alias = alias if alias and "{" not in alias else None
    if verb == "removed" and first_kind == "person" and not has_object and alias:
        return f"{{by}} removed the alias {alias} from {{subjects}}"
    if verb in ("linked", "unlinked"):
        pair = (
            _LINKED_USERNAME
            if object_kind == "person" and first_kind == "username"
            else _LINKED.get(object_kind or "")
        )
        if pair is not None and object_is_page and object_kind == "tag":
            pair = _LINKED_TAG_PAGE
        if pair is not None and object_is_page and object_kind == "song":
            pair = _LINKED_SONG_PAGE
        if pair is not None and has_object:
            return pair[0] if verb == "linked" else pair[1]
    act = FEED.get(verb)
    if act is None:
        return _SOMETHING
    return act.line if has_object or act.alone is None else act.alone


def payload_of(stored: str | None) -> Mapping[str, object]:
    """An event's payload as a mapping, or nothing where it is not one. Never an error on a screen.

    A LIST OF NAMES is read as those names, each once: a stash-box run records the details it filled
    as a list (`stash_boxes._as_applied`).
    """
    import json

    if not stored:
        return {}
    try:
        held = json.loads(stored)
    except ValueError:
        return {}
    if isinstance(held, list):
        return {one: 1 for one in held if isinstance(one, str)}
    return held if isinstance(held, Mapping) else {}
