# SPDX-License-Identifier: AGPL-3.0-or-later
"""A press of many acts, said once: the feed's fold."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from sift.kernel.access.sentences_edited import _value_said, passes_of, setting_changed
from sift.kernel.access.sentences_feed import (
    _TASK_SAID_BY_THE_ACT,
    FEED,
    MACHINE_ACTS,
    TASK_TAIL,
    _template,
    machine_line,
)
from sift.kernel.access.sentences_gone import deleted_where
from sift.kernel.access.sentences_ledger import A_THING, Group, Said
from sift.kernel.access.sentences_pieces import (
    SIFT,
    Line,
    Piece,
    _fill,
    counted_line,
    listed,
    many,
    said,
    times,
    usernames,
)
from sift.kernel.access.sentences_songs import _song_line
from sift.kernel.access.sentences_swaps import _arrived_by_swap, swap_ended, swap_started
from sift.kernel.access.sentences_who import FROM_PASS_MANY, pressed_act, times_said, today_words
from sift.kernel.text import non_empty_str
from sift.kernel.vocabulary import UPDATE_TO

# --- a press of many acts, said once (the feed's fold) --------------------------------------------

#: A TASK'S PHRASE WHEN IT ACTED ON MANY, with its leading space: `FROM_PASS_MANY`, the one table.
#: A task this has no plural for reads its one phrase, which is still true of each file.
TASK_TAIL_MANY: Mapping[str, str] = {word: f" {phrase}" for word, phrase in FROM_PASS_MANY.items()}


def _counted(kind: str, count: int) -> str:
    """ "4,200 files", "23 usernames", "700 Photo Sets": a count in the words a group is headed by."""
    one_word, many_word = _GROUP_WORDS.get(kind, ("thing", "things"))
    return f"1 {one_word}" if count == 1 else f"{many(count)} {many_word}"


def _capped(kind: str, things: Sequence[Piece], total: int, *, newest: bool = True) -> Group:
    """One "Show each" group, headed by its count; where only some are listed, it says so: "The
    newest 100 of 4,200 files", or "100 of 3,600 usernames" where they are not in time order."""
    words = _counted(kind, total)
    if len(things) < total:
        shown = many(len(things))
        words = f"The newest {shown} of {words}" if newest else f"{shown} of {words}"
    return Group(kind=things[0].kind or kind if things else kind, words=words, things=tuple(things))


def feed_folded(
    verb: str,
    *,
    by: str,
    task: str | None = None,
    acts: int,
    object_kind: str | None = None,
    objects: Sequence[Piece] = (),
    objects_total: int | None = None,
    subjects: Sequence[tuple[str, Piece]] = (),
    subject_counts: Mapping[str, int] | None = None,
    title: str = "",
    first: Mapping[str, object] | None = None,
    payload: Mapping[str, object] | None = None,
    untold: int = 0,
) -> Said:
    """ONE PRESS OF MANY ACTS as one line of the feed, with what it stands for under "Show each".

    The press is the reader's (`history_feed.presses_recent`); this says it. The same templates as
    one act (`FEED`), with the subjects and (where the press was done with several things) the
    object COUNTED rather than named: "Sift filed 4,200 files under 23 usernames from file names",
    and the 23 usernames and the files are what it opens to. The things are in `objects` and
    `subjects` already resolved by the reader and may be only the newest of them;
    `objects_total` and `subject_counts` are how many there are in all.

    A setting says where it started (`first`) and where it was left (`payload`); backfilled
    usernames say how many arrivals nobody recorded; decisions say their words and how many times;
    a delete says where it took them; a song says itself and where it came from (`_song_line`).
    """
    payload = payload or {}
    first = first or {}
    counts = dict(subject_counts or {})
    for kind, _one in subjects:
        counts.setdefault(kind, 0)
    by_kind: dict[str, list[Piece]] = {}
    for kind, one in subjects:
        by_kind.setdefault(kind, []).append(one)
    many_objects = (objects_total if objects_total is not None else len(objects)) > 1

    def groups_of(kinds: Sequence[str]) -> tuple[Group, ...]:
        found = [
            _capped(kind, by_kind[kind], counts.get(kind, 0)) for kind in kinds if by_kind.get(kind)
        ]
        if many_objects and objects and object_kind is not None:
            found.insert(
                0, _capped(object_kind, objects, objects_total or len(objects), newest=False)
            )
        return tuple(found)

    said_0 = _folded_own_words(
        verb=verb,
        by=by,
        acts=acts,
        objects=objects,
        subjects=subjects,
        title=title,
        first=first,
        payload=payload,
        untold=untold,
        counts=counts,
        by_kind=by_kind,
        groups_of=groups_of,
    )
    if said_0 is not None:
        return said_0
    return _folded_from_template(
        verb=verb,
        by=by,
        task=task,
        acts=acts,
        object_kind=object_kind,
        objects=objects,
        objects_total=objects_total,
        payload=payload,
        counts=counts,
        by_kind=by_kind,
        many_objects=many_objects,
        groups_of=groups_of,
    )


def _folded_setting(
    by: str,
    setting: Piece,
    acts: int,
    first: Mapping[str, object],
    payload: Mapping[str, object],
) -> Said:
    """One setting changed several times over a sitting, from its first value to its last."""
    key = payload.get("key")
    # Each end in the words a single line would say it in: the plain value where there is one,
    # else the words its writer kept (a Site's name template, a Site's folder), so a Site's
    # settings given and then removed are caught here as a toggle is.
    left = _value_said(payload.get("after"), key) or non_empty_str(payload.get("after_said"))
    started = _value_said(first.get("before"), key) or non_empty_str(first.get("before_said"))
    if left is not None and started == left:
        # Moved and moved back over the sitting: "from on to on" would read as a change that
        # changed nothing, and the true sentence is that it was tried and left where it was.
        return Said(said(by, " changed ", setting, f" {many(acts)} times and left it at {left}"))
    moved = {
        "key": key,
        "before": first.get("before"),
        "after": payload.get("after"),
        "before_said": first.get("before_said"),
        "after_said": payload.get("after_said"),
        UPDATE_TO: payload.get(UPDATE_TO),
    }
    return Said(setting_changed(by, setting, moved))


def _folded_own_words(
    *,
    verb: str,
    by: str,
    acts: int,
    objects: Sequence[Piece],
    subjects: Sequence[tuple[str, Piece]],
    title: str,
    first: Mapping[str, object],
    payload: Mapping[str, object],
    untold: int,
    counts: dict[str, int],
    by_kind: dict[str, list[Piece]],
    groups_of: Callable[[Sequence[str]], tuple[Group, ...]],
) -> Said | None:
    """The presses whose line is said by a builder of its own: a setting, the backfilled usernames, decisions, a machine act, a swap, a song."""
    if verb == "edited" and "setting" in by_kind and "after" in payload:
        return _folded_setting(by, by_kind["setting"][0], acts, first, payload)
    if verb == "added" and "username" in by_kind and payload.get("backfilled"):
        total = counts.get("username", 0)
        return Said(
            said(
                SIFT,
                f" recorded how {usernames(total)} were added",
                f", {many(untold)} of them from before this was recorded" if untold else None,
            ),
            groups=groups_of(["username"]),
        )
    if verb == "decided":
        words = today_words(title) or "A decision was taken"
        return Said(
            said(words, times(acts)),
            groups=groups_of(list(by_kind)),
        )
    if verb in MACHINE_ACTS:
        # NEVER FOLDED either (`history_events._FOLD_KEY`): each act on the computer running Sift
        # is one deliberate press. Said as the newest act says it, should a press ever hold two.
        machine = [one for kind, one in subjects if kind == "computer"][:1]
        return Said(
            machine_line(verb, by, subject_line_of(machine) or said(A_THING["computer"]), payload)
        )
    if verb in ("swap_started", "swap_ended"):
        # NEVER FOLDED (`history_events._FOLD_KEY`): each end of each swap is its own line. Said as
        # the newest act says it, should a press ever hold two.
        return Said(swap_started(by, payload) if verb == "swap_started" else swap_ended(payload))
    if verb == "song_named":
        # ONE SONG FROM ONE PLACE: the fold's key holds the song and what it was named from
        # (`history_events._FOLD_KEY`), so the newest act's song and the one object are the whole
        # press's. The files named are counted and open under "Show each".
        song = non_empty_str(payload.get("song")) or "a song"
        named = said(_counted("asset", counts.get("asset", 0) or acts))
        return Said(
            _song_line(
                by,
                song,
                named,
                objects[0] if objects else None,
                False,
                payload.get("source"),
                device=payload.get("device"),
            ),
            groups=groups_of(["asset"]),
        )

    return None


def _pressed(
    by: str,
    acts: int,
    payload: Mapping[str, object],
    counts: dict[str, int],
    by_kind: dict[str, list[Piece]],
    groups_of: Callable[[Sequence[str]], tuple[Group, ...]],
) -> Said:
    """Presses of the same passes, by the passes they ran: one file pressed again names the file
    and how many times, as its own page does; a run over many files counts them."""
    files = counts.get("asset", 0)
    named = by_kind.get("asset", [])
    if files == 1 and named:
        line = said(
            by, " had Sift ", pressed_act(passes_of(payload), said(named[0])), times_said(acts)
        )
        return Said(line)
    file_line = said(_counted("asset", files)) if files else said(A_THING["asset"])
    return Said(
        said(by, " had Sift ", pressed_act(passes_of(payload), file_line)),
        groups=groups_of(["asset"]),
    )


def _folded_from_template(
    *,
    verb: str,
    by: str,
    task: str | None,
    acts: int,
    object_kind: str | None,
    objects: Sequence[Piece],
    objects_total: int | None,
    payload: Mapping[str, object],
    counts: dict[str, int],
    by_kind: dict[str, list[Piece]],
    many_objects: bool,
    groups_of: Callable[[Sequence[str]], tuple[Group, ...]],
) -> Said:
    """Every other press: its FEED template, the subjects and objects counted."""
    if verb == "pressed":
        return _pressed(by, acts, payload, counts, by_kind, groups_of)
    act = FEED.get(verb)
    preferred = act.about_kind if act is not None and act.about_kind in by_kind else None
    kinds = [preferred] if preferred else [kind for kind in by_kind if kind != object_kind]
    kinds = kinds or list(by_kind)
    about = [said(_counted(kind, counts.get(kind, 0))) for kind in kinds]
    # A press whose acts recorded no subject this viewer may be told about still says how many:
    # in the words the act counts in ("3 files"), never "3 times".
    subject_line = (
        listed(about)
        if about
        else said(counted_line(acts, act.counted if act is not None else "files"))
    )
    if many_objects and object_kind is not None:
        object_line: Line = said(_counted(object_kind, objects_total or len(objects)))
    elif objects:
        object_line = said(objects[0])
    else:
        object_line = ()

    recognized = verb == "linked" and object_kind == "person" and by == SIFT and task == "faces"
    if verb == "deleted":
        floor = payload.get("under_floor")
        why = (
            f" with fewer than {many(floor)} photos"
            if isinstance(floor, int) and not isinstance(floor, bool)
            else deleted_where(payload, several=acts > 1)
        )
        line = said(by, " deleted ", subject_line, why)
        if isinstance(floor, int) and not isinstance(floor, bool):
            # The floor deletes the SET, never a photo: said on the line, because "deleted 700
            # Photo Sets" read alone is 700 losses to whoever reads it.
            line = said(line, " and left every photo in place")
    elif recognized:
        line = said(by, " recognized ", object_line, " in ", subject_line)
    elif verb == "enriched" and not many_objects and objects and objects[0].text == by:
        # A box filling in on its own is the line's actor and its object both: said once, as one
        # act says it (`_filled_in`), never "FansDB filled in 3 Sites from FansDB".
        line = said(by, " filled in ", subject_line)
    else:
        # `by` too: a save says whose device it went to, or a fold of your own saves would say
        # "their device".
        template = _template(
            verb, object_kind, kinds[0] if kinds else None, bool(object_line), payload, by=by
        )
        if many_objects:
            # "added the tag {object}" names ONE tag; a press over several counts them instead.
            template = template.replace("the tag {object}", "{object}").replace(
                "the username {subjects}", "{subjects}"
            )
        line = _fill(template, {"by": said(by), "subjects": subject_line, "object": object_line})
    # "recognized" already says it was a face: not "Sift recognized 4 people in 8 files from a
    # face", as one match's own line says no task.
    if by == SIFT and task is not None and verb not in _TASK_SAID_BY_THE_ACT and not recognized:
        line = said(line, TASK_TAIL_MANY.get(task) or TASK_TAIL.get(task, ""))
    # ONE SWAP'S ARRIVALS FOLD APART FROM ANOTHER'S (the fold key holds the session), so the newest
    # act's device is the whole press's: "Sift added 38 files to the library by swap from device".
    line = _arrived_by_swap(line, by, verb, task, payload)
    return Said(line, groups=groups_of(kinds))


#: What a group of things of one kind is called over its "Show each", by the ledger's kind.
_GROUP_WORDS: Mapping[str, tuple[str, str]] = {
    "asset": ("file", "files"),
    "person": ("person", "people"),
    "tag": ("tag", "tags"),
    "site": ("Site", "Sites"),
    "collection": ("Collection", "Collections"),
    "photo_set": ("Photo Set", "Photo Sets"),
    "song": ("song", "songs"),
    "folder": ("folder", "folders"),
    "username": ("username", "usernames"),
    "pile": ("group of faces", "groups of faces"),
    "download": ("download", "downloads"),
    "swap": ("swap", "swaps"),
    "saved_filter": ("saved filter", "saved filters"),
}


def subject_line_of(pieces: Sequence[Piece]) -> Line:
    """Several things said as a list, folded past `FEED_MOST`."""
    return listed([(one,) for one in pieces])


def _grouped(subjects: Sequence[tuple[str, Piece]]) -> tuple[Group, ...]:
    """What a decision was about, as one "Show each" group per kind of thing, in first-seen order."""
    by_kind: dict[str, list[Piece]] = {}
    for kind, one in subjects:
        if one.kind is None:
            continue
        by_kind.setdefault(kind, []).append(one)
    groups: list[Group] = []
    for kind, things in by_kind.items():
        one_word, many_word = _GROUP_WORDS.get(kind, ("thing", "things"))
        words = f"1 {one_word}" if len(things) == 1 else f"{many(len(things))} {many_word}"
        groups.append(Group(kind=things[0].kind or kind, words=words, things=tuple(things)))
    return tuple(groups)
