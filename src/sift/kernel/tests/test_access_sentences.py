# SPDX-License-Identifier: AGPL-3.0-or-later
"""The words History says, one shape at a time.

Every function here turns a recorded act into a sentence, and most of them choose between a few
shapes by what the record holds. The shapes a library rarely produces are the ones a reader meets
least and trusts most, so each is asked for directly and its words are checked whole.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from sift.kernel import settings_registry
from sift.kernel.access import sentences as say
from sift.kernel.access.sentences import Piece, Said, said, text_of, thing
from sift.kernel.vocabulary import Subject

pytestmark = pytest.mark.unit

PERSON = thing("person", "01HX0000000000000000000301", "Jane Roe")
SITE = thing("site", "01HX0000000000000000000302", "Another Studio")
FILE = thing("asset", "01HX0000000000000000000303", "clip.mp4")
FOLDER = thing("folder", "01HX0000000000000000000304", "Shoots")


def test_an_address_puts_its_value_in_one_query_parameter() -> None:
    assert say.files_in_folder("a b/c") == "/browse?in=a%20b%2Fc"
    assert say.download_row("01HX/2") == "/downloads?row=01HX%2F2"
    assert say.open_on("StashDB") == "Open on StashDB"


def test_a_naming_from_a_folder_names_the_folder() -> None:
    line = say.named_sentence("You", "folder", (PERSON,), folder=FOLDER)
    assert text_of(line) == "You named Jane Roe in this file from the folder Shoots"


def test_a_look_for_faces_that_found_nobody_says_which_gate_refused_them() -> None:
    assert text_of(say.looked_for_faces(0, (), small=2)) == (
        "Sift found faces here too small to recognize"
    )
    # One face refused is "a face": the count is per moment (see `looked_for_faces`).
    assert text_of(say.looked_for_faces(0, (), closer=1)) == (
        "Sift found a face here too unclear to recognize"
    )


def test_a_carried_attribution_names_the_copy_it_came_from() -> None:
    assert say.carried_sentence(from_name="a.mp4", person="Jane Roe") == (
        "Sift named Jane Roe here, carried from a.mp4"
    )
    assert say.carried_sentence(from_name="a.mp4", username="jroe", site="Another Studio") == (
        "Sift filed this file under jroe on Another Studio, carried from a.mp4"
    )


def test_a_distributors_watermark_says_what_it_filed() -> None:
    assert text_of(say.watermark_found("notice", "ACME", None, None, filed=False)) == (
        "Sift found a distributor's watermark, ACME, and filed nothing"
    )
    assert text_of(say.watermark_found("notice", "ACME", None, None, filed=True)) == (
        "Sift found a distributor's watermark, ACME"
    )


def test_a_sites_watermark_with_no_username_names_the_site() -> None:
    line = say.watermark_found("site", "studio.example", SITE, None, filed=True)
    assert text_of(line) == "Sift found a watermark of Another Studio"


def test_a_file_made_ready_with_nothing_named_is_prepared_to_play() -> None:
    assert text_of(say.made_ready([])) == "Sift prepared this file to play"


def test_a_person_named_from_file_names_says_so() -> None:
    line = say.named_on("You", "filename", "3 files")
    assert text_of(line) == "You named them on 3 files from their name in the file names"


def test_a_box_asked_again_by_nobody_says_it_had_nothing_new() -> None:
    actor, line = say.box_line("StashDB", pressed=None, filled=[], again=True)
    assert actor == ""
    assert text_of(line) == "StashDB was checked again and had nothing new"


def test_a_username_that_arrived_before_it_was_recorded_says_so() -> None:
    handle = thing("username", "01HX0000000000000000000305", "jroe")
    line = say.usernames_added(None, [handle], untold=True)
    assert text_of(line) == "The username jroe was added to it before Sift recorded how"


def test_a_thing_that_is_gone_is_named_as_since_deleted() -> None:
    assert say.since_deleted("Cassia Lynn") == "Cassia Lynn (since deleted)"


def test_a_finished_line_lists_the_things_it_names() -> None:
    assert Said(said("You named ", PERSON, " here")).names == (PERSON,)


def test_an_edit_names_counts_or_folds_its_fields() -> None:
    assert say.edited_fields([]) == ""
    assert say.edited_fields(["not_a_field"]) == "1 of its details"
    assert say.edited_fields(["title", "details", "release_date", "notes"]) == "4 details"
    assert say.edited_fields(["title"]) == "the title"


def test_a_merge_lists_everything_it_brought_over_in_the_survivors_words() -> None:
    assert say.brought_over(
        {"usernames": 2, "aliases": 1, "links": 3, "faces": 1, "children": 2}, "site"
    ) == ("2 usernames", "1 other name", "3 addresses", "1 face", "2 Sites published under it")
    assert say.brought_over({"aliases": 2, "links": 1, "children": 1}, "person") == (
        "2 other names",
        "1 link",
        "1 Site published under it",
    )


def test_a_box_that_filled_nothing_in_was_linked_by_whoever_pressed_it() -> None:
    box = Piece("StashDB")
    assert text_of(say._filled_in("You", PERSON, "person", box, {})) == (
        "You linked Jane Roe to StashDB"
    )
    assert text_of(say._filled_in("StashDB", PERSON, "person", box, {})) == (
        "StashDB recognized Jane Roe"
    )


def test_a_cover_filled_in_is_said_last_as_the_cover_picture() -> None:
    assert say._filled_words("person", ["cover"]) == ["cover picture"]


def test_a_link_with_nothing_to_link_to_falls_back_to_the_bare_act() -> None:
    template = say._template("linked", "tag", "asset", False)
    assert template == say.FEED["linked"].alone


def test_a_payload_that_is_not_an_object_reads_as_empty() -> None:
    assert say.payload_of("{not json") == {}
    assert say.payload_of("[1, 2]") == {}


def test_a_single_field_payload_names_that_field() -> None:
    assert say.fields_of({"field": "title"}) == ["title"]


def test_a_face_chosen_as_a_cover_on_the_files_own_page_says_it_was_a_face() -> None:
    edited = say._edited_line(
        say.SIFT,
        page=None,
        object_is_page=True,
        subjects=said(PERSON),
        object_line=said("this file"),
        object_kind="asset",
        fields=[],
        fields_kind=None,
        payload={},
        task="faces",
    )
    assert edited.what == "Sift chose Jane Roe's face in this file as the cover"


@pytest.mark.parametrize(
    ("before", "after", "line"),
    [
        (
            "Beach day",
            "Beach day two",
            "You changed the title of this file from Beach day to Beach day two",
        ),
        (None, "Beach day", "You set the title of this file to Beach day"),
        ("Beach day", "", "You cleared the title of this file"),
    ],
)
def test_one_field_a_save_moved_says_what_it_was_and_what_it_became(
    before: str | None, after: str, line: str
) -> None:
    edited = say._edited_line(
        "You",
        page="this file",
        object_is_page=False,
        subjects=said("this file"),
        object_line=(),
        object_kind=None,
        fields=["title"],
        fields_kind="asset",
        payload={"fields": [{"field": "title", "before": before, "after": after}]},
    )
    assert edited.what == line


def test_a_list_or_two_fields_moved_names_the_fields_alone() -> None:
    def edit(changed: list[dict[str, object]]) -> str:
        return say._edited_line(
            "You",
            page="this file",
            object_is_page=False,
            subjects=said("this file"),
            object_line=(),
            object_kind=None,
            fields=[str(one["field"]) for one in changed],
            fields_kind="asset",
            payload={"fields": changed},
        ).what

    assert edit([{"field": "links", "before": [], "after": ["a"]}]) == "You edited the links"
    assert edit(
        [{"field": "title", "before": "a", "after": "b"}, {"field": "music", "before": None}]
    ) == ("You edited the title and the music")


def test_a_cover_changed_with_no_picture_names_whose_cover_in_the_feed() -> None:
    edited = say._edited_line(
        "You",
        page=None,
        object_is_page=False,
        subjects=said(PERSON),
        object_line=(),
        object_kind=None,
        fields=["cover"],
        fields_kind="person",
        payload={"cover": "removed"},
    )
    assert edited.what.startswith("You ")
    assert edited.what.endswith(" of Jane Roe")
    assert "the cover" in edited.what


def test_an_edit_in_the_feed_counts_fields_it_has_no_words_for() -> None:
    def edit(fields: list[str]) -> str:
        return say._edited_line(
            "You",
            page=None,
            object_is_page=False,
            subjects=said(FILE),
            object_line=(),
            object_kind=None,
            fields=fields,
            fields_kind=None,
            payload={},
        ).what

    assert edit(["not_a_field"]) == "You edited 1 detail of clip.mp4"
    assert edit(["one_field", "another_field"]) == "You edited 2 details of clip.mp4"
    assert edit([]) == "You edited clip.mp4"


@pytest.fixture
def declared(clean_settings_registry: None) -> Iterator[str]:
    """One menu setting and one removed key, declared for the test and no longer after it."""
    settings_registry.register_setting(
        key="test.sentences.menu",
        scope="app",
        default="low",
        section="Library",
        label="Menu",
        help="A menu to say values from.",
        choices=("low", "high"),
        choice_labels=("Low", "High"),
    )
    settings_registry.remove_setting("test.sentences.gone", label="Gone", why="For a test.")
    yield "test.sentences.menu"


def test_a_setting_value_is_said_only_where_it_is_plain(declared: str) -> None:
    assert say._value_said(5) is None
    assert say._value_said('"on"', "test.sentences.gone") is None
    assert say._value_said("{not json", declared) is None
    assert say._value_said('"high"', declared) == "High"
    # A number where the menu has no such choice is said as the number it is.
    assert say._value_said("7", declared) == "7"


def test_a_setting_change_says_as_much_as_the_values_allow() -> None:
    setting = Piece("Starting volume")
    assert text_of(say.setting_changed("You", setting, {"after": "70"})) == (
        "You changed Starting volume to 70"
    )
    assert text_of(say.setting_changed("You", setting, {})) == "You changed Starting volume"


def test_how_long_a_task_ran_is_said_in_the_short_units() -> None:
    assert say._took(42) == "42 s"
    assert say._took(125) == "2 min"
    assert say._took(3900) == "1 h 5 min"
    assert say._took(3600) == "1 h"


def test_a_song_named_from_a_download_page_says_so() -> None:
    assert text_of(say._song_line("Sift", "Blue", None, None, False)) == (
        "Sift named the song Blue from its download page"
    )
    assert text_of(say._song_line("Sift", "Blue", FILE, None, False)) == (
        "Sift named the song Blue on clip.mp4 from its download page"
    )


def test_a_song_that_came_with_a_swapped_file_says_it_came_from_a_swap() -> None:
    """Never "from its download page": the file was sent by another library, with its song."""
    assert text_of(say._song_line("Sift", "Blue", None, None, False, "swap")) == (
        "Sift named the song Blue on this file from a swap"
    )
    assert text_of(say._song_line("Sift", "Blue", FILE, None, False, "swap")) == (
        "Sift named the song Blue on clip.mp4 from a swap"
    )
    said_line = say.event_said(
        "song_named",
        by="Sift",
        here=say.VANTAGE_FILE,
        task="swap",
        payload={"song": "Blue", "source": "swap"},
    )
    assert said_line.what == "Sift named the song Blue on this file from a swap"


def test_a_song_a_swap_brought_names_the_device_it_came_from() -> None:
    """The act names the device, as the file's own `added` act does, so the line says which swap."""
    said_line = say.event_said(
        "song_named",
        by="Sift",
        here=say.VANTAGE_FILE,
        task="swap",
        payload={"song": "Blue", "source": "swap", "device": "ABCD-EFGH"},
    )
    assert (
        said_line.what == "Sift named the song Blue on this file from a swap with device ABCD-EFGH"
    )


def test_a_removal_on_a_stash_box_answer_names_the_box_where_the_payload_carries_it() -> None:
    """The back of the line names the box; with none carried it says the kind, as before."""
    tag = Subject(kind="tag", id="t", name="poolside")

    def line(payload: dict[str, object]) -> str:
        return say.event_said(
            "unlinked",
            by="Sift",
            here=say.VANTAGE_FILE,
            task="stash",
            subjects=[Subject(kind="asset", id="a", name="clip.mp4")],
            object_kind="tag",
            object_id=tag.id,
            object_name=tag.name,
            payload=payload,
        ).what

    assert line({say.ANSWER_OF: "FansDB"}).endswith(" from FansDB's answer")
    assert line({say.ANSWER_OF: ["FansDB", "StashDB"]}).endswith(
        " from FansDB's and StashDB's answers"
    )
    assert line({}).endswith(" from a stash-box answer")


def test_an_act_under_an_older_name_says_what_the_thing_was_called() -> None:
    assert text_of(say.as_then(said("You tagged it"), "Old name", here=say.VANTAGE_ENTITY)) == (
        "You tagged it, when it was called Old name"
    )


def test_a_delete_on_a_persons_page_with_no_file_named_says_a_file_of_theirs() -> None:
    assert text_of(say.deleted_on("You", "person", None, {})) == "You deleted a file of theirs"


def test_a_days_downloads_on_a_persons_page_are_counted_as_theirs() -> None:
    line, heading = say.downloads_folded("You", 3, say.VANTAGE_PERSON)
    assert text_of(line) == "You downloaded 3 files of theirs"
    assert heading == "3 files"


def test_a_status_with_no_standard_phrase_is_said_as_its_number() -> None:
    assert say.failed_because({"code": "http-599"}, site_named=True) == " because it answered 599"


def test_a_download_that_failed_from_nowhere_named_says_why() -> None:
    line = say.download_line("You", "download_failed", None, None, payload={"code": "unsupported"})
    assert text_of(line) == (
        "You could not download this file because Sift cannot download from the site yet"
    )


def test_a_swap_is_said_as_its_own_line_in_a_thread_and_in_the_feed() -> None:
    assert say.event_said("swap_started", by="You", here=say.VANTAGE_FILE).what == (
        "You started a swap"
    )
    assert say.event_said("swap_ended", by="Sift", here=say.VANTAGE_FILE).what == ("The swap ended")
    assert say.feed_folded("swap_started", by="You", acts=1).what == "You started a swap"
    assert say.feed_folded("swap_ended", by="Sift", acts=1).what == "The swap ended"


def test_a_counted_act_with_no_object_counts_what_it_did() -> None:
    said_line = say.event_said("added", by="You", here=say.VANTAGE_FILE, count=3)
    assert said_line.what == "You added 3 files to the library"


def test_a_delete_in_the_feed_without_a_floor_says_where_the_files_went() -> None:
    pressed = say.feed_folded(
        "deleted",
        by="You",
        acts=2,
        subjects=[("asset", FILE)],
        subject_counts={"asset": 2},
        payload={"from": "disk"},
    )
    assert pressed.what == "You deleted 2 files from the disk"


def test_what_a_decision_was_about_groups_only_things() -> None:
    groups = say._grouped([("asset", Piece("plain words")), ("asset", FILE)])
    assert [(one.kind, one.words, one.things) for one in groups] == [("asset", "1 file", (FILE,))]


def test_a_failed_download_is_worded_by_its_code_unless_the_site_said_more() -> None:
    assert say.download_failed("http-404", None, tier=3) is None
    assert say.download_failed("http-404", "Another Studio") == (
        "Another Studio answered 404 Not Found: the post is not there"
    )
    assert say.download_failed("http-404", None) == (
        "The site answered 404 Not Found: the post is not there"
    )
    assert say.download_failed("a-code-nobody-wrote", None) is None


def test_a_stored_failure_in_retired_words_is_said_in_todays() -> None:
    stored = (
        "Another Studio needs you to be logged in. Add a Platform Connection for it, then try the"
        " download again."
    )
    assert say.failure_today(stored) == (
        "Another Studio wants cookies before it will show this. Add cookies for it, then try the"
        " download again."
    )
    assert say.failure_today("Something today's writer says.") is None
    assert say.failure_today(None) is None


def test_an_alias_taken_off_a_person_names_the_alias() -> None:
    """An alias removed, on the person's own page and in the feed, says which name went."""
    on_page = say.event_said(
        "removed",
        by="You",
        here=say.VANTAGE_PERSON,
        about_kind="person",
        payload={"alias": "jroe"},
    )
    assert on_page.what == "You removed the alias jroe from them"
    in_feed = say.feed_line(
        "removed", by="You", subjects=(("person", PERSON),), payload={"alias": "jroe"}
    )
    assert in_feed.what == "You removed the alias jroe from Jane Roe"
    # A removal that names no alias keeps the line it always had.
    assert (
        say.event_said("removed", by="You", here=say.VANTAGE_PERSON, about_kind="person").what
        == "You removed them"
    )


# --- what a stash-box filled in, named, value by value -----------------------------------------


def _values(*words: str) -> tuple[tuple[Piece, ...], ...]:
    return tuple(said(one) for one in words)


def test_a_filled_field_names_what_it_holds_and_folds_a_long_list_at_five() -> None:
    aliases = say.FilledField(
        "aliases", count=7, values=_values("A1", "B2", "C3", "D4", "E5", "F6", "G7")
    )
    line = say.filled_field(aliases)
    assert text_of(line) == "7 aliases (A1, B2, C3, D4, E5 and 2 more)"
    # The fold opens in place to the rest, never drops them.
    fold = next(one for one in line if one.rest)
    assert text_of(fold.rest) == ", F6 and G7"
    assert text_of(say.filled_field(say.FilledField("gender", values=_values("Female")))) == (
        "gender (Female)"
    )


def test_a_value_changed_since_the_box_filled_it_says_so() -> None:
    changed = say.FilledField("gender", values=_values("Female"), changed=True)
    assert text_of(say.filled_field(changed)) == "gender (Female, since changed)"


def test_rows_a_box_added_that_are_gone_are_counted_not_dropped() -> None:
    field = say.FilledField("aliases", count=3, values=_values("A1"))
    assert text_of(say.filled_field(field)) == "3 aliases (A1 and 2 since removed)"


def test_the_ledger_line_names_every_field_with_its_values_and_links_a_username() -> None:
    who = thing("username", "01HX0000000000000000000305", "wren_k", href="/people/x")
    fields = (
        say.FilledField("aliases", count=2, values=_values("Wren K", "W. Kastellan")),
        say.FilledField("usernames", count=1, values=(said(who, " on ", SITE),)),
        say.FilledField("gender", values=_values("Female")),
        say.FilledField("breast type", values=_values("Natural")),
    )
    line = say.box_filled_named("FansDB", fields)
    assert text_of(line) == (
        "FansDB filled in 2 aliases (Wren K and W. Kastellan), usernames (wren_k on Another"
        " Studio), gender (Female) and breast type (Natural)"
    )
    assert who in line and SITE in line


def test_a_bare_link_and_a_run_that_filled_nothing_are_two_answers() -> None:
    assert text_of(say.box_filled_named("StashDB", None)) == (
        "StashDB was linked without filling anything in"
    )
    assert text_of(say.box_filled_named("StashDB", ())) == (
        "StashDB was linked and had nothing new to fill in"
    )


def test_a_link_older_than_the_record_says_when_and_never_no_record() -> None:
    before = say.linked_before_recorded("StashDB", ())
    assert text_of(before) == "StashDB was linked before Sift recorded what a stash-box fills in"
    agreeing = say.linked_before_recorded(
        "StashDB", (say.FilledField("height", values=_values("170 cm")),)
    )
    assert text_of(agreeing) == (
        "StashDB was linked before Sift recorded what a stash-box fills in, and agrees on"
        " height (170 cm)"
    )
    assert "no record" not in text_of(agreeing).casefold()


def test_the_history_line_and_the_feed_name_the_same_values() -> None:
    fields = (say.FilledField("gender", values=_values("Female")),)
    _who, history = say.box_line("FansDB", None, ["gender"], "their", named=fields)
    assert text_of(history) == "FansDB filled in their gender (Female)"
    feed = say._filled_in("FansDB", PERSON, "person", Piece("FansDB"), {"gender": 1}, fields)
    assert text_of(feed) == "FansDB filled in Jane Roe's gender (Female)"


def test_a_counted_feed_line_opens_to_every_field_with_its_values() -> None:
    fields = tuple(say.FilledField(f"field {one}", values=_values(str(one))) for one in range(4))
    fold = say.filled_in_fold(fields)
    assert fold == ("4 details", ("field 0 (0)", "field 1 (1)", "field 2 (2)", "field 3 (3)"))
    assert say.filled_in_fold(fields[:3]) is None


# --- an exchange, a take-back, and the lines a press or a one-time step says --------------------

DEVICE = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"
OTHER_BOX = Piece("StashDB")


@pytest.mark.parametrize(
    ("files", "back", "host_said", "guest_said"),
    [
        (0, 0, "nothing sent or received", "nothing sent or received"),
        (4, 0, "4 files sent and nothing received", "nothing sent and 4 files received"),
        (0, 4, "nothing sent and 4 files received", "4 files sent and nothing received"),
        (38, 4, "38 files sent and 4 received", "4 files sent and 38 received"),
    ],
)
def test_an_exchange_says_both_ways_from_whose_side_the_record_is(
    files: int, back: int, host_said: str, guest_said: str
) -> None:
    """`files` crossed from the host and `back` from the guest, so the host's sent is the guest's
    received: one payload read from both sides says the two numbers the other way round."""
    payload: dict[str, object] = {"device": DEVICE, "files": files, "back": back, "reason": "done"}
    host = text_of(say.swap_ended(payload))
    guest = text_of(say.swap_ended({**payload, "role": "guest"}))
    assert host == f"The exchange with device {DEVICE} ended: {host_said}"
    assert guest == f"The exchange with device {DEVICE} ended: {guest_said}"


def test_a_lost_exchange_says_how_far_it_got_both_ways() -> None:
    lost = say.swap_ended({"files": 3, "back": 2, "reason": "lost"})
    assert text_of(lost) == "The exchange lost the connection with 3 files sent and 2 received"


def test_an_exchange_started_is_said_as_an_exchange_and_a_one_way_swap_as_a_swap() -> None:
    assert text_of(say.swap_started("You", {"two_way": True})) == "You started an exchange"
    assert text_of(say.swap_started("You", {"two_way": "yes"})) == "You started a swap"


def test_a_take_back_names_its_boxes_and_on_one_file_only_the_boxes_that_spoke_about_it() -> None:
    payload = {
        "took_back": "picture",
        "boxes": ["StashDB", "FansDB"],
        "by_file": {"a1": ["FansDB"]},
    }
    assert say.took_back_said(payload) == (
        ["StashDB", "FansDB"],
        ", because each matched on the picture alone",
    )
    assert say.took_back_said(payload, "a1") == (
        ["FansDB"],
        ", because it matched on the picture alone",
    )
    assert say.took_back_said(payload, "a2") == (
        ["StashDB", "FansDB"],
        ", because each matched on the picture alone",
    )
    # Taken back by a person: no reason to give, and boxes that are not a list name none.
    assert say.took_back_said({"took_back": "hand", "boxes": "StashDB"}) == ([], None)
    assert say.took_back_said({"took_back": "something else"}) is None


def test_a_take_back_on_a_files_page_says_what_was_taken_back_not_a_box_removed() -> None:
    def on_page(payload: dict[str, object]) -> str:
        line = say.event_said(
            "removed",
            by="You",
            here=say.VANTAGE_FILE,
            object_kind="box",
            object_id="b1",
            object_name="StashDB",
            payload=payload,
        )
        return text_of(line.pieces)

    taken = on_page({"took_back": "hand", "boxes": ["StashDB"]})
    assert taken == "You took back what StashDB said about this file"
    assert on_page({}) == "You removed StashDB from this file"


def test_a_take_back_in_the_feed_names_the_file_or_counts_many() -> None:
    def in_feed(payload: dict[str, object]) -> str:
        line = say.feed_line(
            "removed",
            by="You",
            subjects=[("asset", FILE)],
            object_kind="box",
            object_piece=OTHER_BOX,
            payload=payload,
        )
        return text_of(line.pieces)

    assert in_feed({"took_back": "picture", "boxes": ["StashDB"]}) == (
        "You took back what StashDB said about clip.mp4, because it matched on the picture alone"
    )
    assert in_feed({"took_back": "hand", "boxes": [], "files": 3}) == (
        "You took back what a stash-box said about 3 files"
    )
    assert in_feed({}) == "You removed StashDB from clip.mp4"


@pytest.mark.parametrize(
    ("payload", "said_line"),
    [
        ({"songs": 12}, "Sift created 12 songs from the songs named on 40 files"),
        ({"credited": 3}, "Sift added the artists AcoustID named to 3 songs"),
        (
            {"departures_kept": 13},
            "Sift kept the 13 files deleted before this update, so their days keep their figures",
        ),
        (
            {"boxes_recorded": 12},
            "Sift recorded which stash-box added the people, tags and Sites on 12 files",
        ),
    ],
)
def test_a_one_time_step_over_the_library_says_its_counts_on_a_page_and_in_the_feed(
    payload: dict[str, object], said_line: str
) -> None:
    """These `added` acts are about the library, not a file: said as the template, they would read
    "Sift added 40 files" and hide what the step did."""
    page = say.event_said("added", by="Sift", here=say.VANTAGE_FILE, payload=payload, count=40)
    feed = say.feed_line("added", by="Sift", payload=payload, count=40)
    assert text_of(page.pieces) == said_line
    assert text_of(feed.pieces) == said_line


@pytest.mark.parametrize(
    ("verb", "line"),
    [
        ("kept_from_swaps", "You kept the folder Beach days out of swaps"),
        ("allowed_in_swaps", "You let the folder Beach days into swaps again"),
        ("kept_local", "You turned off stash-box lookups for the folder Beach days"),
        ("allowed", "You turned on stash-box lookups for the folder Beach days"),
    ],
)
def test_history_says_the_folder_for_each_refusal_on_it(verb: str, line: str) -> None:
    """A bare folder name reads as a person or a tag, so the word "folder" is said."""
    folder = thing("folder", "f-beach", "Beach days")
    said_line = say.feed_line(verb, by="You", subjects=[("folder", folder)])
    assert text_of(said_line.pieces) == line


@pytest.mark.parametrize(
    ("by", "payload", "said_line"),
    [
        (
            "You",
            {"to": "phone", "from": "DESK-ONE"},
            "You sent clip.mp4 to Theater on your phone, from DESK-ONE",
        ),
        ("wren", {"to": "desk"}, "wren sent clip.mp4 to Theater on their computer"),
        # A device name that could read as a slot is not quoted, and an unknown kind is not said.
        ("You", {"to": "fridge", "from": "{by}"}, "You sent clip.mp4 to Theater on another device"),
    ],
)
def test_a_wall_sent_says_which_kind_of_device_and_from_where(
    by: str, payload: dict[str, object], said_line: str
) -> None:
    line = say.feed_line("wall_sent", by=by, subjects=[("asset", FILE)], payload=payload)
    assert text_of(line.pieces) == said_line


@pytest.mark.parametrize(
    ("payload", "where"),
    [
        ({"folder": "D:\\Backups"}, "in D:\\Backups"),
        ({"folder": "{subjects}"}, "in the backup folder"),
        ({}, "in the backup folder"),
    ],
)
def test_a_backup_saved_says_the_folder_it_went_to(payload: dict[str, object], where: str) -> None:
    backup = thing("backup", "1700000000", "the backup from 14 November 2023")
    line = say.feed_line("saved", by="You", subjects=[("backup", backup)], payload=payload)
    assert text_of(line.pieces) == f"You saved the backup from 14 November 2023 {where}"


def test_a_file_put_on_a_song_reads_on_the_songs_own_page_as_this_song() -> None:
    def on_song(verb: str) -> str:
        line = say.event_said(
            verb,
            by="You",
            here=say.VANTAGE_ENTITY,
            from_object=True,
            object_kind="song",
            object_id="g1",
            object_name="Blue",
            subjects=[Subject(kind="asset", id="a1", name="clip.mp4")],
        )
        return text_of(line.pieces)

    assert on_song("linked") == "You named this song on clip.mp4"
    assert on_song("unlinked") == "You removed this song from clip.mp4"


def test_a_pressed_pass_says_who_pressed_it_and_what_came_of_it() -> None:
    assert text_of(say.asked_and_found_nothing("StashDB", 3, by="wren")) == (
        "wren had Sift ask StashDB again, and nothing matched in 3 asks"
    )
    assert text_of(say.asked_and_waiting("StashDB", by="wren")) == (
        "wren had Sift ask StashDB, and its match is waiting to be checked"
    )
    assert text_of(say.looked_for_faces(0, (), small=2, by="wren")) == (
        "wren had Sift look for faces here, and it found faces too small to recognize"
    )
    assert text_of(say.looked_for_faces(2, said("Neve Alder"), by="wren")) == (
        "wren had Sift look for faces here, and it found Neve Alder"
    )
    assert text_of(say.made_ready([], by="wren")) == "wren had Sift prepare this file to play"
    assert text_of(say.music_fingerprinted(empty=True, by="wren")) == (
        "wren had Sift generate a music fingerprint for this file, and it could not read any sound"
    )
    assert text_of(say.fingerprinted_by("wren", empty=True)) == (
        "wren had Sift generate fingerprints for this file, and it found nothing to fingerprint"
    )


@pytest.mark.parametrize(
    ("status", "title", "said_line"),
    [
        ("named", " Blue ", "wren had Sift ask AcoustID, and it named the song Blue"),
        ("named", None, "wren had Sift ask AcoustID, and it named a song"),
        # A status this build has no words for says the press and claims no answer.
        ("unheard", None, "wren had Sift ask AcoustID about this file"),
    ],
)
def test_a_pressed_acoustid_ask_says_what_it_answered(
    status: str, title: str | None, said_line: str
) -> None:
    assert text_of(say.acoustid_answered(status, title, by="wren")) == said_line


def test_a_pressed_watermark_look_says_what_it_found_after_it() -> None:
    """Sift's own line with its first word moved: "it found", never "it Sift found"."""
    username = say.watermark_found("username", "harlowquin", None, None, filed=True, by="wren")
    assert text_of(username) == (
        "wren had Sift look for a watermark, and it found the username harlowquin in a watermark"
    )
    notice = say.watermark_found("notice", "x", SITE, None, filed=True, by="wren")
    assert text_of(notice) == (
        "wren had Sift look for a watermark, and it found a distributor's watermark, which marks"
        " Another Studio material"
    )
    shapes = [
        ("channel", None, None, True),
        ("username", None, None, False),
        ("notice", None, None, False),
        ("notice", None, None, True),
        ("site", None, None, False),
        ("site", None, None, True),
        ("site", SITE, "harlowquin", True),
        ("site", SITE, None, True),
    ]
    for kind, site, handle, filed in shapes:
        line = text_of(say.watermark_found(kind, "QH", site, handle, filed=filed, by="wren"))
        assert line.startswith("wren had Sift look for a watermark, and it found "), line


def test_a_run_whose_note_is_not_a_dry_report_is_said_as_a_run() -> None:
    """A note shaped like a report and not marked dry is an ordinary run: "did a dry run" would say
    nothing was changed about a run that changed things."""
    task = [("task", Piece("Scan"))]
    run = say.feed_line(
        "ran", by="Sift", subjects=task, payload={"said": '{"dry": 0, "said": "x"}', "files": 3}
    )
    assert text_of(run.pieces) == "Sift ran Scan: 3 files"
    dry = say.feed_line(
        "ran",
        by="Sift",
        subjects=task,
        payload={"said": '{"dry": 1, "said": "Would do x.", "head": "Head."}', "seconds": 3},
    )
    assert text_of(dry.pieces) == "Sift did a dry run in 3 s: Head."


def test_a_merge_names_what_it_moved_and_skips_a_name_it_cannot_read() -> None:
    from sift.kernel.access.sentences_ledger import brought_over

    named = {"usernames": [{"name": "riverbend", "where": "X"}, "junk", {"where": "Y"}]}
    assert brought_over({"usernames": 2, "named": named}, "person") == (
        "2 usernames (riverbend on X and 1 more)",
    )
