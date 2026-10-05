# SPDX-License-Identifier: AGPL-3.0-or-later
"""One event, said: the line a page draws for a ledger event and the line the feed draws."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.access.sentences_downloads import KIND_BEFORE, download_line
from sift.kernel.access.sentences_edited import (
    _edited_line,
    _ran_line,
    fields_of,
    passes_of,
    setting_changed,
)
from sift.kernel.access.sentences_feed import (
    FEED,
    FOLDER_REFUSALS,
    MACHINE_ACTS,
    Act,
    _template,
    _with_task,
    machine_line,
)
from sift.kernel.access.sentences_file import recognized_face
from sift.kernel.access.sentences_folded import _grouped, subject_line_of
from sift.kernel.access.sentences_gone import deleted_here, deleted_on, deleted_where
from sift.kernel.access.sentences_ledger import (
    A_GONE,
    A_THING,
    FilledField,
    Said,
    _filled_in,
    filled_in_fold,
    mention,
)
from sift.kernel.access.sentences_pieces import (
    HERE,
    SIFT,
    VANTAGE_FILE,
    WHOSE,
    Line,
    Part,
    Piece,
    _fill,
    counted_line,
    files,
    listed,
    said,
)
from sift.kernel.access.sentences_songs import (
    _song_line,
    boxes_recorded,
    departures_kept,
    merged_line,
    songs_moved,
)
from sift.kernel.access.sentences_sources import nothing_to_fingerprint
from sift.kernel.access.sentences_swaps import (
    _arrived_by_swap,
    _took_back_from,
    _took_back_line,
    swap_ended,
    swap_started,
)
from sift.kernel.access.sentences_who import pressed_act, today_words
from sift.kernel.text import non_empty_str
from sift.kernel.vocabulary import FINGERPRINTS_EMPTY, Subject


def event_said(
    verb: str,
    *,
    by: str,
    here: str,
    task: str | None = None,
    object_kind: str | None = None,
    object_id: str | None = None,
    object_name: str | None = None,
    count: int | None = None,
    subjects: Sequence[Subject] = (),
    about_kind: str | None = None,
    from_object: bool = False,
    title: str = "",
    payload: Mapping[str, object] | None = None,
) -> Said:
    """ONE ledger event as a line on the page of a thing it names. The vantage is the only input
    that is about the screen; everything else is the event's own row.

    `here` is which page (see `HERE`); `from_object` is whether that page is the event's OBJECT
    rather than one of its subjects, in which case the object slot is the page's word and the
    subjects are named. `subjects` are the event's other things, read for a delete, for a line
    read from the object's side, and for nothing else. `about_kind` is WHOSE page this is, exactly:
    a delete reads differently on a person's page and on a tag's.
    """
    payload = payload or {}
    word = HERE.get(here, HERE[VANTAGE_FILE])
    obj = (
        mention(Subject(kind=object_kind, id=object_id, name=object_name))  # type: ignore[arg-type]
        if object_kind is not None and object_id is not None
        else None
    )
    others = [mention(one) for one in subjects]
    said_0 = _said_by_its_own(
        verb=verb,
        by=by,
        count=count,
        from_object=from_object,
        title=title,
        payload=payload,
        word=word,
        obj=obj,
        others=others,
    )
    if said_0 is not None:
        return said_0
    said_1 = _said_built(
        verb=verb,
        by=by,
        here=here,
        task=task,
        object_kind=object_kind,
        subjects=subjects,
        about_kind=about_kind,
        from_object=from_object,
        payload=payload,
        word=word,
        obj=obj,
        others=others,
    )
    if said_1 is not None:
        return said_1
    return _said_from_template(
        verb=verb,
        by=by,
        task=task,
        object_kind=object_kind,
        count=count,
        subjects=subjects,
        about_kind=about_kind,
        from_object=from_object,
        payload=payload,
        word=word,
        obj=obj,
        others=others,
    )


def _said_by_its_own(
    *,
    verb: str,
    by: str,
    count: int | None,
    from_object: bool,
    title: str,
    payload: Mapping[str, object],
    word: str,
    obj: Piece | None,
    others: list[Piece],
) -> Said | None:
    """The acts whose line is said by a builder of its own."""
    if verb == "decided":
        return Said(said(today_words(title) or "A decision was taken"))
    if verb == "swap_started":
        return Said(swap_started(by, payload))
    if verb == "swap_ended":
        return Said(swap_ended(payload))
    if verb == "added" and (moved := songs_moved(by, payload, count)) is not None:
        return Said(moved)
    if verb == "added" and (departed := departures_kept(by, payload)) is not None:
        return Said(departed)
    if verb == "added" and (boxed := boxes_recorded(by, payload)) is not None:
        return Said(boxed)
    if verb == "scanned" and payload.get(FINGERPRINTS_EMPTY) is True and not from_object:
        return Said(nothing_to_fingerprint(by, word))
    if verb == "pressed":
        return Said(said(by, " had Sift ", pressed_act(passes_of(payload), word)))
    if verb == "song_named":
        song = non_empty_str(payload.get("song")) or "a song"
        source = payload.get("source")
        if from_object:
            return Said(
                _song_line(
                    by,
                    song,
                    listed([(one,) for one in others]) or "a file",
                    None,
                    True,
                    source,
                    word,
                    payload.get("device"),
                )
            )
        return Said(_song_line(by, song, None, obj, False, source, device=payload.get("device")))
    return None


def _said_built(
    *,
    verb: str,
    by: str,
    here: str,
    task: str | None,
    object_kind: str | None,
    subjects: Sequence[Subject],
    about_kind: str | None,
    from_object: bool,
    payload: Mapping[str, object],
    word: str,
    obj: Piece | None,
    others: list[Piece],
) -> Said | None:
    """:A delete, a download, a merge, a take-back, an edit or a fill."""
    if verb == "deleted":
        if about_kind is None or about_kind == "asset":
            on = [one.name for one in subjects if one.kind != "asset" and one.name]
            more = payload.get("more")
            return Said(
                _with_task(
                    deleted_here(by, on, more if isinstance(more, int) else 0, payload),
                    by,
                    verb,
                    task,
                )
            )
        gone = next((one for one in subjects if one.kind == "asset"), None)
        file = None if gone is None else mention(gone, gone=True)
        return Said(_with_task(deleted_on(by, about_kind, file, payload), by, verb, task))
    if verb in ("downloaded", "download_failed"):
        return Said(download_line(by, verb, here, obj, payload=payload))
    if verb == "merged":
        if from_object:
            return merged_line(
                by, others=others, here=word, into=None, brought=payload or None, kind=object_kind
            )
        return merged_line(by, others=(), here=word, into=obj, brought=None, kind=None)
    if verb == "removed" and object_kind == "box" and not from_object:
        taken = _took_back_line(by, payload, said(word))
        if taken is not None:
            return Said(taken)
    if verb == "enriched" and object_kind is None and not from_object:
        # The record writers' enrichment names no box (they are handed none), and a stash-box is
        # the only thing that fills a record in, so that much is said and no more.
        whose = WHOSE.get(here, "its")
        return Said(said(by, f" filled in {whose} details from a stash-box"))
    if verb == "edited":
        return _edited_line(
            by,
            page=None if from_object else word,
            object_is_page=from_object,
            subjects=listed([(one,) for one in others]) if from_object else said(word),
            object_line=said(word) if from_object else said(obj),
            object_kind=object_kind,
            fields=fields_of(payload),
            fields_kind=subjects[0].kind if subjects else about_kind,
            payload=payload,
            task=task,
        )
    return None


def _said_from_template(
    *,
    verb: str,
    by: str,
    task: str | None,
    object_kind: str | None,
    count: int | None,
    subjects: Sequence[Subject],
    about_kind: str | None,
    from_object: bool,
    payload: Mapping[str, object],
    word: str,
    obj: Piece | None,
    others: list[Piece],
) -> Said:
    """Every other act: its FEED template, filled from this vantage."""
    act = FEED.get(verb)
    counted = act.counted if act is not None else "files"
    if from_object:
        subject_line = (
            counted_line(count, counted, others[0] if others else None)
            if count is not None
            else listed([(one,) for one in others])
        ) or said("something")
        slots = {"subjects": subject_line, "object": said(word)}
        has_object = True
    elif count is not None:
        slots = {"subjects": counted_line(count, counted), "object": said(obj)}
        has_object = obj is not None
    else:
        slots = {"subjects": said(word), "object": said(obj)}
        has_object = obj is not None
    first = subjects[0].kind if subjects else about_kind
    template = _template(
        verb, object_kind, first, has_object, payload, object_is_page=from_object, by=by
    )
    if from_object and count is None:
        template = _kind_once(template, "subjects", first, others)
    elif not from_object and obj is not None:
        template = _kind_once(template, "object", object_kind, [obj])
    was = non_empty_str(payload.get("before"))
    if verb == "renamed":
        template = FEED["renamed"].line if was else FEED["renamed"].alone or template
    line = _fill(template, {**slots, "by": said(by), "was": said(was or "")})
    return Said(
        _arrived_by_swap(_with_task(line, by, verb, task, payload), by, verb, task, payload)
    )


#: WHAT AN ACT THAT NAMED NOTHING WAS ABOUT, where the act itself says. A save names its file; the
#: only save that names none is one the workbench's v13 backfill wrote from the save log after the
#: file had gone (`_BACKFILLED_SAVED_FILE` is written only while the log still names it), so it is a
#: file that is gone: "You saved something to your device" said less than the record knows.
RECORDED_NOTHING: Mapping[str, str] = {"saved": A_GONE["asset"]}


def feed_line(
    verb: str,
    *,
    by: str,
    task: str | None = None,
    subjects: Sequence[tuple[str, Piece]] = (),
    object_kind: str | None = None,
    object_piece: Piece | None = None,
    count: int | None = None,
    title: str = "",
    payload: Mapping[str, object] | None = None,
    sures: Sequence[float | None] | None = None,
    named: Sequence[FilledField] | None = None,
) -> Said:
    """ONE ledger event as a line in the feed, which names everything: the same builder the pages
    use, with every slot a name. `subjects` are (the ledger's kind, the thing as a piece), already
    resolved by the reader: its name now where the row kept none, its address, whether it is gone.

    Which subjects it names: a PREFERENCE where naming all would be untrue (`Act.about_kind`);
    the printed object is never listed again; a decision says its receipt's words with what it was
    about under "Show each"; a face match is said as the file's page says it (`recognized_face`).
    """
    payload = payload or {}
    act = FEED.get(verb)
    preferred = [one for one in subjects if act is not None and one[0] == act.about_kind]
    chosen = preferred or list(subjects)
    printed = None if object_piece is None else (object_kind, object_piece.id)
    # The subjects the line does not print as its object. `kept` falls back to every subject so an
    # act whose one subject IS its object still names it; `others` does not, or a count would say
    # its place twice ("about 40 files in StashDB" on StashDB's own ask).
    others = [one for one in chosen if (one[0], one[1].id) != printed]
    kept = others or chosen
    pieces = [one for _kind, one in kept]
    where_counted = others[0][1] if others else None
    by_line = said(by)

    said_early = _feed_own_words(
        verb=verb,
        by=by,
        task=task,
        payload=payload,
        object_kind=object_kind,
        object_piece=object_piece,
        count=count,
        title=title,
        sures=sures,
        subjects=subjects,
        kept=kept,
        pieces=pieces,
    )
    if said_early is not None:
        return said_early
    said_built = _feed_built_lines(
        verb=verb,
        by=by,
        task=task,
        payload=payload,
        object_kind=object_kind,
        object_piece=object_piece,
        count=count,
        named=named,
        kept=kept,
        pieces=pieces,
    )
    if said_built is not None:
        return said_built
    return _feed_template_line(
        verb=verb,
        by=by,
        task=task,
        payload=payload,
        object_kind=object_kind,
        object_piece=object_piece,
        count=count,
        kept=kept,
        pieces=pieces,
        where_counted=where_counted,
        act=act,
        by_line=by_line,
    )


def _feed_own_words(
    *,
    verb: str,
    by: str,
    task: str | None,
    payload: Mapping[str, object],
    object_kind: str | None,
    object_piece: Piece | None,
    count: int | None,
    title: str,
    sures: Sequence[float | None] | None,
    subjects: Sequence[tuple[str, Piece]],
    kept: list[tuple[str, Piece]],
    pieces: list[Piece],
) -> Said | None:
    """The acts whose line is said by a builder of its own, before anything is counted."""
    if verb in FOLDER_REFUSALS and len(kept) == 1 and kept[0][0] == "folder":
        before, after = FOLDER_REFUSALS[verb]
        before = _kind_once(f"{before}{{x}}", "x", "folder", pieces).removesuffix("{x}")
        return Said(said(by, before, pieces[0], after) if after else said(by, before, pieces[0]))
    if verb == "decided":
        return Said(said(today_words(title) or "A decision was taken"), groups=_grouped(subjects))
    if verb == "swap_started":
        return Said(swap_started(by, payload))
    if verb == "swap_ended":
        return Said(swap_ended(payload))
    if (
        verb == "linked"
        and object_kind == "person"
        and object_piece is not None
        and by == SIFT
        and task == "faces"
    ):
        files_here = [one for kind, one in kept if kind == "asset"]
        return Said(
            recognized_face(
                object_piece, said("in ", listed([(one,) for one in files_here])), sures or ()
            )
        )
    if verb == "added" and (moved := songs_moved(by, payload, count)) is not None:
        return Said(moved)
    if verb == "added" and (departed := departures_kept(by, payload)) is not None:
        return Said(departed)
    if verb == "added" and (boxed := boxes_recorded(by, payload)) is not None:
        return Said(boxed)
    # One file's empty read only: a run folded into a count says the run, by the count.
    if verb == "scanned" and payload.get(FINGERPRINTS_EMPTY) is True and count is None and pieces:
        return Said(nothing_to_fingerprint(by, listed([(one,) for one in pieces])))
    if verb == "pressed":
        file = listed([(one,) for one in pieces]) or said(A_THING["asset"])
        return Said(said(by, " had Sift ", pressed_act(passes_of(payload), file)))
    if verb == "song_named":
        song = non_empty_str(payload.get("song")) or "a song"
        return Said(
            _song_line(
                by,
                song,
                listed([(one,) for one in pieces]) or None,
                object_piece,
                False,
                payload.get("source"),
                device=payload.get("device"),
            )
        )
    if verb == "ran":
        return Said(_ran_line(by, subject_line_of(pieces) or said("a task"), payload))
    if verb in MACHINE_ACTS:
        return Said(
            machine_line(verb, by, subject_line_of(pieces) or said(A_THING["computer"]), payload)
        )
    return None


def _by_its_kind(kind: str | None, one: Piece | None) -> bool:
    """A thing with no name, said by its kind ("a tag"): never "the tag a tag"."""
    return kind is not None and one is not None and one.text == A_THING.get(kind)


def _kind_before(kind: str, one: Piece) -> str:
    if _by_its_kind(kind, one) or not (one.kind is not None or one.text):
        return ""
    return KIND_BEFORE.get(kind, "")


def _kind_once(template: str, slot: str, kind: str | None, things: Sequence[Piece]) -> str:
    if len(things) != 1 or kind is None or not _by_its_kind(kind, things[0]):
        return template
    return template.replace(f"the {A_THING[kind].split(' ', 1)[-1]} {{{slot}}}", f"{{{slot}}}")


def _feed_built_lines(
    *,
    verb: str,
    by: str,
    task: str | None,
    payload: Mapping[str, object],
    object_kind: str | None,
    object_piece: Piece | None,
    count: int | None,
    named: Sequence[FilledField] | None,
    kept: list[tuple[str, Piece]],
    pieces: list[Piece],
) -> Said | None:
    """The acts a stash-box, a take-back, a delete, a download or a merge says."""
    if verb == "enriched" and object_piece is not None and count is None and pieces:
        return Said(
            _filled_in(by, pieces[0], kept[0][0], object_piece, payload, named),
            folded=filled_in_fold(named),
        )
    if verb == "removed" and object_kind == "box" and pieces:
        taken = _took_back_line(by, payload, _took_back_from(payload, said(pieces[0])))
        if taken is not None:
            return Said(taken)
    if verb == "forgot":
        feature = subject_line_of(pieces) or said("a feature")
        took = f" and the names it added to {files(count)}" if count else ""
        return Said(said(by, " deleted everything in ", feature, took))
    if verb == "deleted":
        gone_ones = [said(_kind_before(kind, one), one) for kind, one in kept]
        about = counted_line(count, "files") if count is not None else listed(gone_ones)
        return Said(
            _with_task(
                said(by, " deleted ", about or "something", deleted_where(payload)), by, verb, task
            )
        )
    if verb in ("downloaded", "download_failed"):
        return Said(
            download_line(
                by, verb, None, object_piece, pieces[0] if pieces else None, payload=payload
            )
        )
    if verb == "merged":
        return merged_line(by, others=pieces, here=None, into=object_piece, brought=None, kind=None)
    return None


def _feed_template_line(
    *,
    verb: str,
    by: str,
    task: str | None,
    payload: Mapping[str, object],
    object_kind: str | None,
    object_piece: Piece | None,
    count: int | None,
    kept: list[tuple[str, Piece]],
    pieces: list[Piece],
    where_counted: Piece | None,
    act: Act | None,
    by_line: Line,
) -> Said:
    """Every other act: its FEED template, filled with what the event named."""
    subject_line = (
        counted_line(count, act.counted if act else "files", where_counted)
        if count is not None
        else listed([(one,) for one in pieces])
    ) or said(RECORDED_NOTHING.get(verb, "something"))
    backfilled = verb == "added" and kept and kept[0][0] == "username" and payload.get("backfilled")
    if backfilled and by == SIFT and task is None:
        # A username from before arrivals were recorded, backfilled with no task that could be
        # read: the line says so rather than crediting Sift with an act nobody recorded.
        where: Part = said(" to ", object_piece) if object_piece is not None else None
        return Said(
            said(
                "The username ",
                subject_line_of(pieces),
                " was added",
                where,
                " before Sift recorded how",
            )
        )
    if verb == "edited" and kept and kept[0][0] == "setting" and "after" in payload:
        return Said(setting_changed(by, pieces[0], payload))
    if verb == "edited":
        return _edited_line(
            by,
            page=None,
            object_is_page=False,
            subjects=subject_line,
            object_line=said(object_piece),
            object_kind=object_kind,
            fields=fields_of(payload),
            fields_kind=kept[0][0] if kept else None,
            payload=payload,
            task=task,
        )
    template = _template(
        verb, object_kind, kept[0][0] if kept else None, object_piece is not None, payload, by=by
    )
    if count is None:
        template = _kind_once(template, "subjects", kept[0][0] if kept else None, pieces)
    if object_piece is not None:
        template = _kind_once(template, "object", object_kind, [object_piece])
    was = non_empty_str(payload.get("before"))
    if verb == "renamed":
        template = FEED["renamed"].line if was else FEED["renamed"].alone or template
    line = _fill(
        template,
        {
            "by": by_line,
            "subjects": subject_line,
            "object": said(object_piece),
            "was": said(was or ""),
        },
    )
    return Said(
        _arrived_by_swap(_with_task(line, by, verb, task, payload), by, verb, task, payload)
    )
