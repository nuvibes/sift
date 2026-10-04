# SPDX-License-Identifier: AGPL-3.0-or-later
"""The field registry: what it refuses, and the order it keeps.

The refusals are the point. A registry that accepts a field with no label produces a record with a
blank word on it, which the person running Sift can only understand by reading the source, so the
declaration fails instead, at import, where whoever wrote it is looking.
"""

from __future__ import annotations

import pytest

from sift.kernel.records import (
    LIST_KINDS,
    FieldError,
    FoundRecord,
    Kind,
    Shown,
    Subject,
    every_field,
    field,
    fields_filled,
    fields_of,
    register_field,
    said_plainly,
    value_said,
)


def test_every_field_writes_site_as_the_name_it_is() -> None:
    """A Site is Sift's noun, capitalised wherever a person reads it, in help as in a label. The
    sentence-case gate only asks that a capital in a label be a name, so this asks the other way
    round, and a record's help is read on every form it draws."""
    import re

    lower = re.compile(r"\bsites?\b")
    helps = [one.help for one in every_field() if one.help]
    assert any("Site" in help for help in helps), "no help names a Site, so this proves nothing"
    wrong = [
        (one.subject.value, one.key, words)
        for one in every_field()
        for words in (one.label, one.help or "")
        if lower.search(words)
    ]
    assert not wrong, wrong


def test_the_shipped_fields_are_all_described() -> None:
    """The known positive, and it comes first: something really is declared.

    Every assertion below about what is REFUSED would pass just as well against an empty registry.
    """
    assert every_field(), "nothing is declared, so nothing below is being tested"
    for one in every_field():
        assert one.label.strip(), one.key
        assert one.kind in set(Kind), one.key
        assert one.shown in set(Shown), one.key


def test_a_field_with_no_label_is_refused() -> None:
    with pytest.raises(FieldError, match="needs a label"):
        register_field(key="blank", subject=Subject.TAG, label="   ", kind=Kind.TEXT)


def test_a_field_with_empty_help_is_refused_rather_than_carried() -> None:
    """Empty help is not the same as no help. One draws a hint with nothing in it."""
    with pytest.raises(FieldError, match="empty help"):
        register_field(key="hollow", subject=Subject.TAG, label="Hollow", kind=Kind.TEXT, help=" ")


def test_a_list_somebody_types_into_must_say_what_one_entry_is() -> None:
    """The adder's empty box says the field's word, never a guess from the kind: a guess would
    have a box for tattoos ask for "another name"."""
    with pytest.raises(FieldError, match="say its entry"):
        register_field(
            key="marks", subject=Subject.TAG, label="Marks", kind=Kind.NAMES, editable=True
        )
    with pytest.raises(FieldError, match="say its entry"):
        register_field(
            key="marks",
            subject=Subject.TAG,
            label="Marks",
            kind=Kind.NAMES,
            editable=True,
            entry=" ",
        )


def test_an_entry_word_on_what_is_not_a_list_is_refused() -> None:
    with pytest.raises(FieldError, match="leave entry out"):
        register_field(
            key="worded", subject=Subject.TAG, label="Worded", kind=Kind.TEXT, entry="a word"
        )


def test_an_order_kept_on_what_is_not_a_list_is_refused() -> None:
    with pytest.raises(FieldError, match="no order to keep"):
        register_field(
            key="ordered_word", subject=Subject.TAG, label="Ordered", kind=Kind.TEXT, ordered=True
        )


def test_a_songs_artists_keep_their_order_and_no_other_list_does() -> None:
    """The order a recording credits its artists in is part of what is stored; the other lists
    (other names, tattoos, links) are sets, and offering to reorder them would be a control that
    changes nothing."""
    ordered = {f"{one.subject.value}.{one.key}" for one in every_field() if one.ordered}
    assert ordered == {"song.artists"}


def test_every_shipped_list_somebody_types_into_says_its_entry() -> None:
    for one in every_field():
        if one.kind in LIST_KINDS and one.editable:
            assert one.entry and one.entry.strip(), f"{one.subject.value}.{one.key}"
        elif one.kind not in LIST_KINDS:
            assert one.entry is None, f"{one.subject.value}.{one.key}"


def test_declaring_one_key_twice_on_a_subject_is_refused() -> None:
    """Two owners for one field is the drift a single registry exists to prevent."""
    with pytest.raises(FieldError, match="registered twice"):
        register_field(key="name", subject=Subject.PERSON, label="Name again", kind=Kind.TEXT)


def test_the_same_key_on_two_subjects_is_fine() -> None:
    """A person and a site both have a name and both have details, labelled separately."""
    assert field(Subject.PERSON, "name") is not None
    assert field(Subject.SITE, "name") is not None
    assert field(Subject.PERSON, "details") is not None
    assert field(Subject.SITE, "details") is not None


def test_a_field_nobody_declared_is_none_rather_than_a_raise() -> None:
    """A screen asking about a field this version does not have is an older client, not a fault."""
    assert field(Subject.TAG, "not-a-field") is None


def test_fields_come_back_in_the_order_they_were_declared() -> None:
    """The order IS the reading order of the record, so it belongs with the declarations.

    Asserted on the file, where the two halves are deliberately separated: what the file IS comes
    before what it measures, and neither is alphabetical.
    """
    keys = [one.key for one in fields_of(Subject.ASSET)]

    assert keys.index("title") < keys.index("size_bytes")
    assert keys.index("filename") < keys.index("container")
    assert keys != sorted(keys), "alphabetical would mean nobody chose an order"


def test_what_a_file_measures_cannot_be_typed_into() -> None:
    """A size on disk is not an opinion, and a box around one invites an edit nothing can honour."""
    measured = {"size_bytes", "container", "vcodec", "acodec", "fps", "bit_depth", "dimensions"}
    for one in fields_of(Subject.ASSET):
        if one.key in measured:
            assert not one.editable, one.key
    assert field(Subject.ASSET, "title") is not None
    title = field(Subject.ASSET, "title")
    assert title is not None and title.editable, "the one a person names the file with"


def test_no_screen_is_told_a_field_is_called_notes() -> None:
    """One word for this everywhere. The column keeps its name; no label says "Notes"."""
    labels = {one.label.lower() for one in every_field()}

    assert "notes" not in labels
    assert field(Subject.PERSON, "details") is not None


def test_every_field_belongs_to_a_subject_the_registry_knows() -> None:
    """A subject nothing declares is a group no screen would ever ask for."""
    assert {one.subject for one in every_field()} <= set(Subject)


def test_field_keys_are_said_in_the_records_order_and_mid_sentence() -> None:
    """The words of a history line: the registry's labels, lowercased only where the label has no
    capitals of its own, in the order the record draws them rather than the order they were
    written, with a count where the writer gave one above one."""
    said = said_plainly(Subject.PERSON, ["links", "aliases"], counts={"links": 3, "aliases": 1})
    assert said == ("aliases", "3 links")
    assert said_plainly(Subject.PERSON, ["pmv_creator"]) == ("Is PMV creator",)


def test_a_key_nothing_declares_keeps_its_own_spelling_opened_out() -> None:
    """An older or newer build's field: dropped, the line would say two things and mean three."""
    assert said_plainly(Subject.PERSON, ["aliases", "hair_colour_old"]) == (
        "aliases",
        "hair colour old",
    )


def test_a_stored_list_and_a_stored_object_are_both_read() -> None:
    """Rows written before the counts came are a list of keys; since, a key-to-count object."""
    assert fields_filled(Subject.PERSON, '["links", "aliases"]') == ("aliases", "links")
    assert fields_filled(Subject.PERSON, '{"links": 9, "aliases": 0, "odd": "x"}') == ("9 links",)


def test_what_the_caller_accounts_for_is_left_out_of_both_shapes() -> None:
    assert fields_filled(Subject.PERSON, '["links", "aliases"]', omitting=["links"]) == ("aliases",)
    assert fields_filled(Subject.PERSON, '{"links": 2, "aliases": 1}', omitting=["links"]) == (
        "aliases",
    )


def test_a_row_that_says_nothing_is_none_and_not_an_empty_list() -> None:
    """None is "the row does not say"; the empty tuple is "a plan ran and filled nothing". A value
    this cannot read makes no claim, so it is None too."""
    assert fields_filled(Subject.PERSON, None) is None
    assert fields_filled(Subject.PERSON, "") is None
    assert fields_filled(Subject.PERSON, "not json") is None
    assert fields_filled(Subject.PERSON, '"a string"') is None
    assert fields_filled(Subject.PERSON, "[]") == ()


def test_the_old_word_for_a_username_still_names_one_and_nothing_else_does() -> None:
    """`account` was this subject's word before `username`, and an address or a stored row that
    still spells it must reach the same subject, for one release, then the fallback goes.

    The known positive first, by value and by identity, because a fallback that answered some OTHER
    member would still be a member. Then the refusals: a word nobody ever used, the old word in the
    wrong case, and the plural, each of which must be refused as an unknown value rather than
    quietly read as a username.
    """
    assert Subject("username") is Subject.USERNAME
    assert Subject("account") is Subject.USERNAME
    assert Subject("account").value == "username"
    for nonsense in ("nonsense", "Account", "accounts", "handle", ""):
        with pytest.raises(ValueError):
            Subject(nonsense)


# --- a stored value as a line says it -------------------------------------------------------------


def test_a_boxs_constant_is_said_as_the_word_it_stands_for_in_a_word_field() -> None:
    """Whether stored as the value or as the JSON text the stash-box tables keep."""
    assert value_said(Subject.PERSON, "breast_type", '"NATURAL"') == "Natural"
    # A constant that is no word is said as what it stands for, never "Na".
    assert value_said(Subject.PERSON, "breast_type", '"NA"') == "Not applicable"
    assert value_said("person", "hair_color", "STRAWBERRY_BLONDE") == "Strawberry blonde"


def test_a_constant_is_read_by_the_fields_kind_never_by_the_look_of_the_value() -> None:
    """A name in capitals is a name, and a country's code is the country."""
    assert value_said(Subject.PERSON, "name", "WRENFIELD") == "WRENFIELD"
    assert value_said(Subject.PERSON, "country", "US") == "United States"
    assert value_said(Subject.PERSON, "country", '"GB"') == "United Kingdom"


def test_a_length_says_its_unit() -> None:
    assert value_said(Subject.PERSON, "height_cm", 170) == "170 cm"
    assert value_said(Subject.PERSON, "height_cm", "165") == "165 cm"


def test_what_is_not_a_plain_value_is_not_said() -> None:
    """Nothing, an empty word, a list, an object and a yes-or-no have no sentence of their own."""
    for stored in (None, "", '""', "null", [1], '["a"]', {"a": 1}, '{"a": 1}', True, "true"):
        assert value_said(Subject.PERSON, "hair_color", stored) is None, stored


def test_text_that_is_not_json_is_said_as_it_is() -> None:
    assert value_said(Subject.PERSON, "name", "Jane Doe") == "Jane Doe"


def test_a_value_under_no_declared_field_is_said_plainly() -> None:
    """An unknown subject or key has no kind, so nothing is read into the value."""
    assert value_said("nonsense", "hair_color", "BLONDE") == "BLONDE"
    assert value_said(Subject.PERSON, "no_such_field", 7) == "7"


# --- a box's own id for a name it gave ------------------------------------------------------------


def test_a_boxs_id_for_a_name_is_found_without_case_or_padding() -> None:
    found = FoundRecord(
        source_id="box",
        remote_id="s-1",
        subject=Subject.ASSET,
        name="A scene",
        refs={"person": {"Jane Doe": "p-1", "Somebody Else": ""}, "site": {"Studio": "t-1"}},
    )

    assert found.id_for("person", "  jane doe ") == "p-1"
    assert found.id_for("site", "STUDIO") == "t-1"


def test_a_name_the_box_gave_no_id_for_has_none() -> None:
    """An empty id is no id, a name it did not give is none, and a kind it named nobody under is
    none: a record kept before ids were written down has no refs at all."""
    found = FoundRecord(
        source_id="box",
        remote_id="s-1",
        subject=Subject.ASSET,
        name="A scene",
        refs={"person": {"Somebody Else": ""}},
    )

    assert found.id_for("person", "Somebody Else") is None
    assert found.id_for("person", "Jane Doe") is None
    assert found.id_for("site", "Studio") is None
