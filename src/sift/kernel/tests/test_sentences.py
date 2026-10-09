# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every line a History can say, read back and held to the bar: short, plain, the actor first."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable

import pytest

from sift.kernel.access import sentences as say
from sift.kernel.access.history import LINK_KINDS
from sift.kernel.ledger import VERBS
from sift.kernel.tests import sentences_said
from sift.kernel.tests.sentences_said import (
    A_BOX,
    BACKUP,
    COMPUTER,
    DATABASE_FILE,
    ENTITY_PAGE,
    FILE,
    FILE_PAGE,
    FROM_A_BROWSER,
    FROM_LAPTOP,
    PERSON,
    PERSON_PAGE,
    SAID,
    SHELF,
    SITE,
    SOMEBODY,
    TAG,
    USERNAME,
    _feed,
    _page,
)
from sift.kernel.vocabulary import Subject

#: Words that say the machinery rather than what happened.
MACHINE_WORDS = ("record ", "entry", "table", " row", "via ", "database", "attribute")


#: THE ONE PHRASE HOLDING A MACHINE WORD THAT IS WHAT HAPPENED: the file somebody chose on
#: `Settings > Backup and restore > Import a database file`, named in the control's own words when
#: a library says it was created from one; "database" anywhere else on a line is still refused.
SCREEN_PHRASES = ("the database file ", "a database file")


#: How many words a line may hold before its names: sixteen, flat, room for a line's how and why
#: (from its file name, because it had fewer than 10 photos).
MOST_WORDS = 16


#: The values handed in, so a line that OPENS with one is not held to a capital.
VALUES = (SOMEBODY, "poolside", "holiday.mp4", "harlowquin", "Studio", A_BOX, "d")


@pytest.mark.parametrize(("what", "by", "line"), SAID, ids=[one for one, _, _ in SAID])
def test_every_line_is_short_plain_and_unpunctuated(
    what: str, by: str | None, line: say.Line
) -> None:
    """The register of a caption: no full stop, no dash bolting on a second fact, no machinery."""
    words = say.text_of(line)
    assert words, what
    assert not words.endswith("."), what
    assert " -- " not in words, what
    # NO DASH JOINING TWO IDEAS: the second fact is said in words ("... and nothing matched").
    assert "\u2014" not in words, f"{what}: {words}"
    if not any(words.startswith(one) for one in VALUES):
        assert words[0] == words[0].upper(), what
    plain = f" {words.lower()} "
    for phrase in SCREEN_PHRASES:
        plain = plain.replace(phrase, " ")
    for word in MACHINE_WORDS:
        assert word not in plain, what
    assert len(words.split()) <= MOST_WORDS, f"{what}: {words}"


@pytest.mark.parametrize(("what", "by", "line"), SAID, ids=[one for one, _, _ in SAID])
def test_every_line_starts_with_its_actor(what: str, by: str | None, line: say.Line) -> None:
    """ACTOR FIRST, ACTIVE VOICE. A line whose record names who did it says so first; a line whose
    record names nobody is passive, and never guesses "You" on an install with a second user."""
    words = say.text_of(line)
    if by is None:
        assert not words.startswith(("You ", "Sift ", f"{A_BOX} ")), f"{what}: {words}"
        return
    assert words.startswith(f"{by} "), f"{what}: {words}"


def test_pieces_place_each_thing_where_it_sits_and_never_search() -> None:
    """A Collection called `d`: only the piece that IS the Collection links, never the "d" inside
    "added": the search that did that is gone because nothing is searched for."""
    line = _feed("linked", subjects=[("asset", FILE)], object_kind="collection", object_piece=SHELF)
    assert say.text_of(line) == "You added holiday.mp4 to d"
    things = [(one.kind, one.text) for one in line if one.kind is not None]
    assert things == [("asset", "holiday.mp4"), ("collection", "d")]


def test_a_face_match_is_one_sentence_on_every_screen() -> None:
    """The file, the person's page and the feed (its Decisions too) say ONE sentence, and only the
    vantage word changes."""
    on_the_file = say.text_of(say.recognized_face(PERSON, "here", [0.75]))
    on_her_page = say.text_of(say.recognized_face("them", say.said("in ", FILE), [0.75]))
    in_the_feed = say.text_of(
        _feed(
            "linked",
            by="Sift",
            task="faces",
            subjects=[("person", PERSON), ("asset", FILE)],
            object_kind="person",
            object_piece=PERSON,
            sures=[0.75],
        )
    )
    assert on_the_file == f"Sift recognized {SOMEBODY} here, 75% sure"
    assert on_her_page == "Sift recognized them in holiday.mp4, 75% sure"
    assert in_the_feed == f"Sift recognized {SOMEBODY} in holiday.mp4, 75% sure"
    assert say.matched_sentence(SOMEBODY, [0.75]) == on_the_file


def test_a_folded_list_carries_its_rest_in_one_piece() -> None:
    """Past five, a list says "and N more", and that run carries the rest to open in place."""
    line = say.listed([(say.thing("tag", str(n), f"t{n}"),) for n in range(9)])
    assert say.text_of(line) == "t0, t1, t2, t3, t4 and 4 more"
    fold = [one for one in line if one.rest]
    assert len(fold) == 1
    assert say.text_of(fold[0].rest) == ", t5, t6, t7 and t8"
    # One over the fold is said whole: "and 1 more" is longer than the name.
    assert say.text_of(say.listed([(say.Piece(f"t{n}"),) for n in range(6)])) == (
        "t0, t1, t2, t3, t4 and t5"
    )


def test_a_folded_song_links_where_it_came_from_and_opens_to_the_files_it_named() -> None:
    """One settle's spread as one line: the file the name came from is a link in the sentence, and
    the files it named are what "Show each" opens to, counted in all."""
    source = say.thing("asset", "a2", "longer.mp4")
    folded = say.feed_folded(
        "song_named",
        by="Sift",
        task="fingerprint",
        acts=11,
        object_kind="asset",
        objects=[source],
        subjects=[("asset", FILE), ("asset", say.thing("asset", "a3", "dune.mp4"))],
        subject_counts={"asset": 11},
        payload={"song": "Blue", "source": "shared", "from": "a2"},
    )
    linked = [(one.kind, one.id) for one in folded.pieces if one.kind is not None]
    assert linked == [("asset", "a2")]
    [group] = folded.groups
    assert group.words == "The newest 2 of 11 files"
    assert [one.id for one in group.things] == ["a1", "a3"]


def test_a_line_counting_many_files_says_whose_names_in_the_plural() -> None:
    """A Site's or a tag's counted line says the task's plural phrase past one file: "Sift filed
    4,000 files under it from file names", never "from the file's name" (one file's words)."""
    many = say.thing("files", "p1", "4,000 files")
    one = say.thing("files", "p1", "1 file")
    assert say.text_of(say.filed_under("Sift", "filename", many, 4181)) == (
        "Sift filed 4,000 files under it from file names"
    )
    assert say.text_of(say.filed_under("Sift", "filename", one, 1)) == (
        "Sift filed 1 file under it from the file's name"
    )
    assert say.text_of(say.put_on("Sift", "filename", many, 4181)) == (
        "Sift added it to 4,000 files from file names"
    )
    # A task with no plural phrase reads its one phrase, which is true of each file.
    assert say.from_pass("watermark", 9) == " from a watermark"
    assert say.from_pass(None, 9) == ""


def test_one_plural_rule_says_a_count_and_its_noun() -> None:
    """The kernel's plural, which Insights and the suggestions' receipts both read: grouped digits,
    one and many, and a slice's own wording of the number where it has one."""
    assert say.plural(1, "file", "files") == "1 file"
    assert say.plural(1240, "file", "files") == "1,240 files"
    assert say.plural(3, "person", "people") == "3 people"
    assert say.plural(200_000, "video", "videos", number=lambda n: "about 200,000") == (
        "about 200,000 videos"
    )


def test_a_counted_act_does_not_say_where_twice_when_its_one_subject_is_its_object() -> None:
    """A stash-box sweep records its ask with the box as the subject AND the object, and a count
    (`stash_boxes.service`). Where a count was is a subject the line does not print as its object:
    never "Sift asked StashDB about 40 files in StashDB"."""
    box = say.thing("box", "b1", A_BOX)
    line = _feed(
        "asked", by="Sift", count=40, subjects=[("box", box)], object_kind="box", object_piece=box
    )
    assert say.text_of(line) == "Sift asked StashDB about 40 files"


def test_an_entity_edit_names_each_field_by_its_own_word_whatever_order() -> None:
    """Each key is asked for its OWN word. Paired by position against the record's order, a save
    written in another order would print one field's label for another."""
    in_order = _page(
        "edited", ENTITY_PAGE, about_kind="site", payload={"aliases": ["x"], "parent": "y"}
    )
    other_order = _page(
        "edited", ENTITY_PAGE, about_kind="site", payload={"parent": "y", "aliases": ["x"]}
    )
    assert (
        say.text_of(other_order)
        == say.text_of(in_order)
        == "You edited the other names and the parent Site"
    )


@pytest.mark.parametrize("verb", ["linked", "unlinked"])
def test_a_count_of_faces_is_said_in_faces_and_never_in_files(verb: str) -> None:
    """Faces attached to a person, or taken off one, carry a count of FACES: "files" would read
    perfectly and say the wrong thing about what was counted."""
    line = _feed(verb, by="Sift", count=344, object_kind="person", object_piece=PERSON)
    assert "344 faces" in say.text_of(line)


def test_the_feed_s_delete_names_the_file_it_ended_and_not_what_it_was_on() -> None:
    """A delete names the file AND everything it was on, so each of their pages reads it. The feed
    names the file: listing the rest would say the person and the tag were deleted too."""
    line = _feed("deleted", subjects=[("asset", FILE), ("person", PERSON), ("tag", TAG)])
    assert say.text_of(line) == "You deleted the file holiday.mp4"


def test_the_feed_has_a_line_for_every_act_the_kernel_can_record() -> None:
    """Both ways: a verb nothing says draws "changed"; a template nothing writes is a dead filter."""
    assert set(say.FEED) == set(VERBS)


def test_every_kind_a_line_can_link_is_one_a_history_knows_how_to_draw() -> None:
    assert set(say.LINKED_KINDS.values()) <= set(LINK_KINDS)


def test_every_kind_has_words_for_its_mark_and_none_says_settled() -> None:
    from sift.kernel.access.history import KINDS

    assert set(KINDS) <= set(say.MEANS)
    assert not [one for one in say.MEANS.values() if "workbench" in one.lower()]
    assert say.means("renamed", actor="you") == "You did this"
    assert say.means("copied_into", "trim") == "A copy with its ends trimmed"


#: Builders that are PHRASES, addresses or plumbing rather than a line, each covered where the lines
#: they go into are.
_NOT_A_LINE = frozenset(
    {
        # The act a press had Sift do and how many presses a line stands for: the halves of
        # `pressed_here`, which is in the table.
        "pressed_act",
        "times_said",
        "passes_of",
        # A phrase inside a face line ("2 faces that may be Ada"), and the words a take-back's
        # line is built from: each is said by the line that holds it.
        "may_be_faces",
        "took_back_said",
        "said",
        "text_of",
        "thing",
        "things_in",
        "capitalized",
        "listed",
        "many",
        "plural",
        "files",
        "people",
        "tags",
        "usernames",
        "faces",
        "counted_faces",
        "and_then",
        "files_of_username",
        "username_opens",
        "files_in_folder",
        "folder_named",
        "download_row",
        "faces_of_person",
        "by_word",
        "from_pass",
        # The pass with its act, and the parts a box's line and a merge's fold are built from:
        # each is a piece of a line the table reads whole (`box_filled_named`, `feed_folded`).
        "from_maker",
        "filled_field",
        "filled_fields",
        "filled_in_fold",
        "since_removed",
        "brought_named",
        "sift_from",
        "today_words",
        "moved_into",
        "folder_of",
        "how_sure",
        "copy_verb",
        "matched_sentence",
        "carried_sentence",
        "filled_folded",
        "name_of",
        "mention",
        "edited_folded",
        "edited_fields",
        "cover_changed",
        "brought_over",
        "counted_line",
        "payload_of",
        "fields_of",
        "deleted_where",
        "subject_line_of",
        "means",
        "download_failed",
        # A stored failure in today's words (the Downloads row, `test_failure_said.py`) and a fold's
        # ", 78 times": a phrase, said with the feed's fold (`test_history_feed_fold.py`).
        "failure_today",
        "times",
        # Reached through `event_said` and `feed_line`, which are in the table many times over.
        "setting_changed",
        "merged_line",
        "as_then",
        "deleted_on",
        "deleted_here",
        "download_line",
        "failed_because",
        # The words OF a line rather than a line: what "Sift processed this file" opens to, counted,
        # and the words of a way out of Sift beside a line ("Open on StashDB").
        "processed_steps",
        "open_on",
        # A name with "(since deleted)" after it: the one way every line says a thing that is gone,
        # held where those lines are drawn (`test_worded.py`, `test_ledger_route.py`).
        "since_deleted",
        # The two halves of a username's number changed, around the username: held below and by
        # `test_username_id.py`, which writes the event it reads.
        "number_changed",
    }
)


def test_a_username_number_changed_says_the_old_value_and_a_first_one_says_only_the_new() -> None:
    assert say.number_changed(
        {"number_site": "Instagram", "number_before": "4242", "number_after": "12345678"}
    ) == ("changed the Instagram ID", " from 4242 to 12345678")
    assert say.number_changed({"number_site": "Instagram", "number_after": "4242"}) == (
        "set the Instagram ID",
        " to 4242",
    )
    assert say.number_changed({"fields": ["name"]}) is None


def test_every_builder_is_in_the_table() -> None:
    """A line-builder added to `sentences.py` and not listed above fails here."""
    source = inspect.getsource(sentences_said)
    table = source[source.index("SAID: tuple") :]
    called = set(re.findall(r"say\.(\w+)\(", table)) | {"event_said", "feed_line"}
    builders = {
        name
        for name, value in vars(say).items()
        if isinstance(value, Callable)  # type: ignore[arg-type]
        and inspect.isfunction(value)
        and value.__module__.startswith(say.__name__)
        and not name.startswith("_")
    }
    assert builders - _NOT_A_LINE - called == set()


@pytest.mark.parametrize(
    ("line", "said"),
    [
        (
            _page(
                "song_named",
                FILE_PAGE,
                by="Sift",
                object_kind="site",
                object_id="s",
                object_name="PMVHaven",
                payload={"song": "Blue"},
            ),
            "Sift named the song Blue from PMVHaven's page",
        ),
        (
            _page(
                "song_named",
                ENTITY_PAGE,
                by="Sift",
                object_kind="site",
                object_id="s",
                object_name="PMVHaven",
                from_object=True,
                subjects=[Subject(kind="asset", id="a", name="x.mp4")],
                payload={"song": "Blue"},
            ),
            "Sift named the song Blue on x.mp4 from this Site's page",
        ),
        (
            _page(
                "song_named", FILE_PAGE, by="Sift", payload={"song": "Blue", "source": "acoustid"}
            ),
            "Sift named the song Blue from AcoustID",
        ),
        (
            _page(
                "song_named",
                FILE_PAGE,
                by="Sift",
                object_kind="asset",
                object_id="b",
                object_name="y.mp4",
                payload={"song": "Blue", "source": "shared", "from": "b"},
            ),
            "Sift named the song Blue, from the same music as y.mp4",
        ),
        (
            _page(
                "song_named",
                FILE_PAGE,
                by="Sift",
                object_kind="asset",
                object_id="b",
                object_name="y.mp4",
                from_object=True,
                subjects=[Subject(kind="asset", id="a", name="x.mp4")],
                payload={"song": "Blue", "source": "shared", "from": "b"},
            ),
            "Sift named the song Blue on x.mp4, from the same music as this file",
        ),
        (
            _feed(
                "song_named",
                by="Sift",
                subjects=[("asset", FILE)],
                payload={"song": "Blue", "source": "acoustid"},
            ),
            "Sift named the song Blue on holiday.mp4 from AcoustID",
        ),
        (
            _feed(
                "song_named",
                by="Sift",
                subjects=[("asset", FILE)],
                object_kind="asset",
                object_piece=say.thing("asset", "a2", "longer.mp4"),
                payload={"song": "Blue", "source": "shared", "from": "a2"},
            ),
            "Sift named the song Blue on holiday.mp4, from the same music as longer.mp4",
        ),
        (
            say.feed_folded(
                "song_named",
                by="Sift",
                task="fingerprint",
                acts=11,
                object_kind="asset",
                objects=[say.thing("asset", "a2", "longer.mp4")],
                subjects=[("asset", FILE)],
                subject_counts={"asset": 11},
                payload={"song": "Blue", "source": "shared", "from": "a2"},
            ).pieces,
            "Sift named the song Blue on 11 files, from the same music as longer.mp4",
        ),
        (
            say.feed_folded(
                "song_named",
                by="Sift",
                task="music_lookup",
                acts=2,
                subjects=[("asset", FILE)],
                subject_counts={"asset": 2},
                payload={"song": "Blue", "source": "acoustid"},
            ).pieces,
            "Sift named the song Blue on 2 files from AcoustID",
        ),
        (
            say.feed_folded(
                "song_named",
                by="Sift",
                task="download",
                acts=3,
                object_kind="site",
                objects=[SITE],
                subjects=[("asset", FILE)],
                subject_counts={"asset": 3},
                payload={"song": "Blue"},
            ).pieces,
            "Sift named the song Blue on 3 files from Studio's page",
        ),
        (say.undone("song_name"), "That song name was undone"),
        (
            _feed("decided", title="Chose to fingerprint the music later"),
            "Chose to fingerprint the music later",
        ),
        (
            _page("deleted", FILE_PAGE, payload={"from": "disk"}),
            "You deleted this file from the disk",
        ),
        (
            _page("deleted", FILE_PAGE, payload={"from": "sift"}),
            "You deleted this file from Sift only",
        ),
        (
            _feed(
                "added",
                by="Sift",
                task="watermark",
                subjects=[("username", USERNAME)],
                object_kind="site",
                object_piece=SITE,
            ),
            "Sift added the username harlowquin to Studio from a watermark",
        ),
        (say.shared_with("You", "fff"), "You shared this file with guest fff"),
        (
            say.shared_with(None, "fff", share=False, here="it"),
            "It was kept private from guest fff",
        ),
        (say.asked_and_found_nothing(A_BOX), "Sift asked StashDB and nothing matched"),
        (
            say.person_added("Sift", PERSON, "folder"),
            f"Sift added {SOMEBODY} to the library from a folder name",
        ),
        (
            say.kept_mine(None, A_BOX, "breast type"),
            "Your breast type was kept over StashDB's answer",
        ),
        (_page("kept_local", PERSON_PAGE), "You turned off stash-box lookups for them"),
        (_page("revealed", FILE_PAGE), "You unhid this file"),
    ],
)
def test_the_verify_rows_read_exactly(line: say.Line, said: str) -> None:
    """These lines, word for word."""
    assert say.text_of(line) == said


def _on_its_page(verb: str, kind: str, name: str, file: str = "holiday.mp4") -> say.Line:
    """An act read on the page of the thing it was done WITH: a Collection, a tag, a Site."""
    return _page(
        verb,
        ENTITY_PAGE,
        object_kind=kind,
        object_id="o",
        object_name=name,
        from_object=True,
        subjects=[Subject(kind="asset", id="a", name=file)],
    )


#: THE HISTORY LINES, word for word: who, what, where, how, why and how much, where the
#: record carries it. A line whose fact is not recorded is not here.
REWRITTEN: tuple[tuple[str, say.Line, str], ...] = (
    (
        "a face match on their page",
        say.recognized_face("them", say.said("in ", say.thing("asset", "a", "image.png")), [0.76]),
        "Sift recognized them in image.png, 76% sure",
    ),
    (
        "a face chosen as their cover",
        _page(
            "edited",
            PERSON_PAGE,
            by="Sift",
            task="faces",
            object_kind="asset",
            object_id="a",
            object_name="image.png",
        ),
        "Sift chose their face in image.png as the cover",
    ),
    (
        "a file chosen as their cover",
        _page(
            "edited", PERSON_PAGE, object_kind="asset", object_id="a", object_name="a1b2c3d4.jpg"
        ),
        "You chose a1b2c3d4.jpg as the cover",
    ),
    (
        "this file chosen as somebody's cover",
        _page(
            "edited",
            FILE_PAGE,
            object_kind="asset",
            object_id="a",
            object_name="x",
            from_object=True,
            subjects=[Subject(kind="person", id="p", name=SOMEBODY)],
        ),
        f"You chose this file as {SOMEBODY}'s cover",
    ),
    # A Photo Set named after the person in it must not read as HER cover on the feed.
    (
        "a picture chosen as a Photo Set's cover, in the feed",
        _feed(
            "edited",
            by="Sift",
            subjects=[("photo_set", say.thing("photo_set", "s", "Cassia Lynn"))],
            object_kind="asset",
            object_piece=say.thing("asset", "a", "beach.jpg"),
        ),
        "Sift chose beach.jpg as the cover of the Photo Set Cassia Lynn",
    ),
    (
        "this file chosen as a Photo Set's cover",
        _page(
            "edited",
            FILE_PAGE,
            object_kind="asset",
            object_id="a",
            object_name="x",
            from_object=True,
            subjects=[Subject(kind="photo_set", id="s", name="Cassia Lynn")],
        ),
        "You chose this file as the cover of the Photo Set Cassia Lynn",
    ),
    (
        "a file added to a Collection",
        _on_its_page("linked", "collection", "d", "1600000000000.jpg"),
        "You added 1600000000000.jpg to it",
    ),
    (
        "a file removed from a Collection",
        _on_its_page("unlinked", "collection", "d", "clip_12-34.jpg"),
        "You removed clip_12-34.jpg from it",
    ),
    (
        "a tag removed, on the tag's page",
        _on_its_page("unlinked", "tag", "poolside", "0f1e2d3c.mp4"),
        "You removed it from 0f1e2d3c.mp4",
    ),
    (
        "downloaded",
        _page(
            "downloaded",
            FILE_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="Studio",
        ),
        "Sift downloaded this file from Studio",
    ),
    (
        "a watermark filing in the feed",
        _feed(
            "linked",
            by="Sift",
            task="watermark",
            subjects=[("asset", say.thing("asset", "a", "5e6f7a8b.jpg"))],
            object_kind="site",
            object_piece=SITE,
        ),
        "Sift filed 5e6f7a8b.jpg under Studio from a watermark",
    ),
    (
        "a Photo Set under the floor",
        _feed(
            "deleted",
            by="Sift",
            task="photo_set_floor",
            subjects=[("photo_set", say.thing("photo_set", "ps", "poolside"))],
            payload={"under_floor": 10},
        ),
        "Sift deleted the Photo Set poolside because it had fewer than 10 photos",
    ),
    (
        "a download refused, and why",
        _page(
            "download_failed",
            FILE_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="Studio",
            payload={"code": "http-410"},
        ),
        "Sift could not download this file from Studio because it answered 410 Gone",
    ),
    (
        "a download Sift cannot make yet",
        _page(
            "download_failed",
            ENTITY_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="Studio",
            from_object=True,
            payload={"code": "unsupported"},
        ),
        "Sift could not download a file from it because Sift cannot download from it yet",
    ),
    (
        "lookups turned off",
        _feed("kept_local", subjects=[("person", PERSON)]),
        f"You turned off stash-box lookups for {SOMEBODY}",
    ),
    (
        "a file of theirs deleted",
        _page(
            "deleted",
            ENTITY_PAGE,
            about_kind="person",
            subjects=[Subject(kind="asset", id="a", name="9a8b7c6d.jpg")],
            payload={"from": "disk"},
        ),
        "You deleted their file 9a8b7c6d.jpg from the disk",
    ),
    (
        "a file with this tag deleted",
        _page(
            "deleted",
            ENTITY_PAGE,
            about_kind="tag",
            subjects=[Subject(kind="asset", id="a", name="9a8b7c6d.jpg")],
            payload={"from": "sift"},
        ),
        "You deleted 9a8b7c6d.jpg, which had this tag, from Sift only",
    ),
    (
        "this file deleted",
        _page(
            "deleted",
            FILE_PAGE,
            subjects=[Subject(kind="person", id="p", name=SOMEBODY)],
            payload={"from": "disk"},
        ),
        f"You deleted this file, which was on {SOMEBODY}, from the disk",
    ),
    (
        "shared with a guest",
        _feed(
            "shared",
            subjects=[("asset", FILE)],
            object_kind="login",
            object_piece=say.Piece("fff"),
        ),
        "You shared holiday.mp4 with guest fff",
    ),
    (
        "kept private from a guest",
        say.shared_with(None, "fff", share=False, here="it"),
        "It was kept private from guest fff",
    ),
    (
        "a watermark filing undone",
        say.watermark_refused(),
        "The filing from its watermark was permanently undone",
    ),
    (
        "face data deleted",
        _feed("forgot", subjects=[("setting", say.Piece("Faces"))], count=1203),
        "You deleted everything in Faces and the names it added to 1,203 files",
    ),
    ("a save to your device", _page("saved", FILE_PAGE), "You saved this file to your device"),
    (
        "fingerprints",
        _page("scanned", FILE_PAGE, by="Sift"),
        "Sift generated fingerprints for this file",
    ),
    (
        "fingerprints that came back empty",
        _page("scanned", FILE_PAGE, by="Sift", payload={"empty": True}),
        "Sift found nothing to fingerprint in this file",
    ),
    (
        "fingerprints that came back empty, in the feed",
        _feed("scanned", by="Sift", subjects=[("asset", FILE)], payload={"empty": True}),
        "Sift found nothing to fingerprint in holiday.mp4",
    ),
    ("faces removed", say.faces_taken_off(3), "3 faces were removed from this file"),
    (
        "a task ran, with its counts",
        _feed(
            "ran",
            by="Sift",
            subjects=[("run", say.Piece("Scan"))],
            payload={"seconds": 42, "files": 1203, "jobs_failed": 3, "stopped": False},
        ),
        "Sift ran Scan in 42 s: 1,203 files, 3 failed",
    ),
    (
        "a scan ran, with how many files were new",
        _feed(
            "ran",
            by="Sift",
            subjects=[("run", say.Piece("Scan"))],
            payload={"seconds": 42, "files": 1203, "new": 12, "jobs_failed": 0},
        ),
        "Sift ran Scan in 42 s: 1,203 files, 12 new",
    ),
    (
        "a scan ran and found nothing new",
        _feed(
            "ran",
            by="Sift",
            subjects=[("run", say.Piece("Scan"))],
            payload={"seconds": 42, "files": 1203, "new": 0},
        ),
        "Sift ran Scan in 42 s: 1,203 files, none new",
    ),
    (
        "a task you ran that ended early",
        _feed(
            "ran",
            subjects=[("run", say.Piece("Identify"))],
            payload={"seconds": 3900, "files": 300, "jobs_failed": 0, "stopped": True},
        ),
        "You ran Identify for 1 h 5 min and it ended early: 300 files",
    ),
    (
        "a dry run, with what it said",
        _feed(
            "ran",
            subjects=[("run", say.Piece("Dry run"))],
            payload={
                "seconds": 3,
                "outcome": "done",
                "said": '{"dry": 1, "said": "Find shoots would suggest 4 shoots, of 2 people. '
                'Nothing was changed.", "parts": [], "names": [], "more": 0, "cannot": []}',
            },
        ),
        "You did a dry run in 3 s: Find shoots would suggest 4 shoots, of 2 people. "
        "Nothing was changed.",
    ),
    (
        "a dry run says its headline, never its list of files",
        _feed(
            "ran",
            subjects=[("run", say.Piece("Dry run"))],
            payload={
                "seconds": 3,
                "outcome": "done",
                "said": '{"dry": 1, "said": "Find shoots would suggest 4 shoots, of 2 people. '
                'First files: beach.jpg, dunes.jpg. Nothing was changed.", "head": "Find shoots '
                'would suggest 4 shoots, of 2 people.", "parts": [], "names": ["beach.jpg"], '
                '"more": 0, "cannot": []}',
            },
        ),
        "You did a dry run in 3 s: Find shoots would suggest 4 shoots, of 2 people.",
    ),
    (
        "a run whose note is only a sentence",
        _feed(
            "ran",
            subjects=[("run", say.Piece("Dry run"))],
            payload={"seconds": 3, "said": "{not a report"},
        ),
        "You ran Dry run in 3 s",
    ),
    (
        "a box's answer, the details it filled",
        _feed(
            "enriched",
            by=A_BOX,
            subjects=[("person", PERSON)],
            object_kind="box",
            object_piece=say.Piece(A_BOX),
            payload={"birth_date": 1, "height_cm": 1},
        ),
        f"StashDB filled in {SOMEBODY}'s birthdate and height",
    ),
    (
        "a box's answer you applied, counted",
        _feed(
            "enriched",
            subjects=[("person", PERSON)],
            object_kind="box",
            object_piece=say.Piece(A_BOX),
            payload={one: 1 for one in ("birth_date", "height_cm", "country", "gender")},
        ),
        f"You filled in 4 of {SOMEBODY}'s details from StashDB",
    ),
    (
        "a box's answer whose details were stored as a list",
        _feed(
            "enriched",
            by=A_BOX,
            subjects=[("person", PERSON)],
            object_kind="box",
            object_piece=say.Piece(A_BOX),
            payload=say.payload_of('["birth_date", "height_cm"]'),
        ),
        f"StashDB filled in {SOMEBODY}'s birthdate and height",
    ),
)


@pytest.mark.parametrize(
    ("line", "said"),
    [(line, said) for _, line, said in REWRITTEN],
    ids=[what for what, _, _ in REWRITTEN],
)
def test_the_rewritten_lines_read_exactly(line: say.Line, said: str) -> None:
    """Each of these History lines, word for word."""
    assert say.text_of(line) == said


def test_a_recolour_an_older_build_recorded_still_reads() -> None:
    """A tag has no colour since catalog v69, and the lines an older build wrote for changing one
    still read: the field word comes from the key the line recorded, never the column that went."""
    from sift.kernel.access.sentences import edited_fields

    assert edited_fields(["color"], "tag") == "the color"


@pytest.mark.parametrize(
    ("verb", "subject", "said"),
    [
        (
            "restored",
            ("backup", BACKUP),
            "Sift restored this library from the backup from 14 November 2023",
        ),
        (
            "adopted",
            ("database_file", DATABASE_FILE),
            "Sift created this library from the database file old-library.sqlite3",
        ),
    ],
    ids=["a backup", "a database file"],
)
def test_a_library_says_where_it_came_from(
    verb: str, subject: tuple[str, say.Piece], said: str
) -> None:
    """An imported library's first line, word for word: a backup by its day, a Sift database file
    by the name it was chosen under, each as Sift's act."""
    assert say.text_of(_feed(verb, by=say.SIFT, subjects=[subject])) == said


@pytest.mark.parametrize(
    ("verb", "payload", "said"),
    [
        (
            "sharing_turned_on",
            FROM_LAPTOP,
            "You turned on network sharing on DESK-ONE, from LAPTOP-TWO",
        ),
        (
            "sharing_turned_off",
            FROM_A_BROWSER,
            "You turned off network sharing on DESK-ONE, from another computer",
        ),
        (
            "start_with_windows_on",
            FROM_LAPTOP,
            "You set Sift to start with Windows on DESK-ONE, from LAPTOP-TWO",
        ),
        (
            "start_with_windows_off",
            FROM_LAPTOP,
            "You stopped Sift starting with Windows on DESK-ONE, from LAPTOP-TWO",
        ),
        (
            "firewall_opened",
            FROM_LAPTOP,
            "You opened the firewall port for Sift on DESK-ONE, from LAPTOP-TWO",
        ),
        (
            "storage_moved",
            FROM_LAPTOP,
            "You moved Sift data to another folder on DESK-ONE, from LAPTOP-TWO",
        ),
        (
            "update_started",
            {**FROM_LAPTOP, "version": "0.1.300"},
            "You started installing Sift 0.1.300 on DESK-ONE, from LAPTOP-TWO",
        ),
        (
            "update_started",
            FROM_LAPTOP,
            "You started installing a newer Sift on DESK-ONE, from LAPTOP-TWO",
        ),
        (
            "library_opened",
            {**FROM_LAPTOP, "library": "Holidays"},
            "You opened the library Holidays on DESK-ONE, from LAPTOP-TWO",
        ),
        (
            "library_opened",
            {**FROM_LAPTOP, "library": "{by}"},
            "You opened another library on DESK-ONE, from LAPTOP-TWO",
        ),
        ("restarted", {"here": True}, "You restarted Sift on DESK-ONE, from its own screen"),
        (
            "restarted",
            {"from": "{subjects}"},
            "You restarted Sift on DESK-ONE, from another computer",
        ),
    ],
)
def test_an_act_on_the_computer_running_sift_says_who_what_where_and_from_which_device(
    verb: str, payload: dict[str, object], said: str
) -> None:
    """Word for word, on the feed and folded: the device the window named, its own screen, or
    "another computer" for a browser; a version or library the act named, never a word read as a
    slot."""
    assert say.text_of(_feed(verb, subjects=[("computer", COMPUTER)], payload=payload)) == said
    folded = say.feed_folded(
        verb, by=say.YOU, acts=2, subjects=[("computer", COMPUTER)], payload=payload
    )
    assert say.text_of(folded.pieces) == said
