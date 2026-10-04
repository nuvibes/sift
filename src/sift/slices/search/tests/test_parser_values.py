# SPDX-License-Identifier: AGPL-3.0-or-later
"""The values each filter reads: presets, facet bands, plain numbers, states and the people's words."""

from __future__ import annotations

import pytest

from sift.kernel.access import AllOf, AnyOf, Not, Role, Viewer, Where
from sift.kernel.access.constraints import same_music_where
from sift.slices.search.filter_compiler import FilterCompiler
from sift.slices.search.filters import (
    ENTITY_FIELDS,
    MAX_NUMBER,
    OFFERED_VALUES,
    SUGGESTED_FIELDS,
    Field,
    FilterHelp,
    Negated,
    Op,
    Term,
    clauses,
    group,
    parse,
    parse_modal,
    parse_tokens,
    problems_in,
    scalar,
)
from sift.slices.search.tests.test_parser import NOW

PRESETS = (
    "-viewed",
    "fav:yes",
    "-rating",
    "resolution:4k+",
    "added:7d",
    "duration:20m+",
    "-tags",
)


@pytest.mark.parametrize("preset", PRESETS)
def test_every_preset_is_a_filter_and_is_spelled_the_way_it_comes_back(preset: str) -> None:
    """Every preset is a filter, not free text, and is spelled the way the parser writes it back,
    so the control can read as pressed and be taken off."""
    parsed = parse({"q": preset})
    written = [clause.query for clause in clauses(parsed)]

    assert parsed.text is None, "a preset that is free text is not a filter"
    assert written == [preset]


#: The values the facet panel emits for the two dimensions with no natural single value, and the
#: bounds each must come back as. Written out rather than imported, so the two places must agree.
FACET_BANDS = (
    ("resolution", "8k", 4320, MAX_NUMBER),
    ("resolution", "4k", 2160, 4319),
    ("resolution", "1440p", 1440, 2159),
    ("resolution", "1080p", 1080, 1439),
    ("resolution", "720p", 720, 1079),
    ("resolution", "480p", 480, 719),
    # The bottom band opens downward.
    ("resolution", "360p", 0, 479),
    # Half-open: a file of exactly 60,000 ms belongs to the row above (see `Range`).
    ("duration", "0s..<1m", 0, 59_999),
    ("duration", "1m..<3m", 60_000, 179_999),
    ("duration", "3m..<5m", 180_000, 299_999),
    ("duration", "5m..<15m", 300_000, 899_999),
    ("duration", "15m..<30m", 900_000, 1_799_999),
    ("duration", "30m..<60m", 1_800_000, 3_599_999),
    ("duration", "60m+", 3_600_000, None),
)


#: The Duration column's former bands: ranges in the one grammar, so a saved filter keeps its
#: meaning. None is mapped onto a new band, which would be a different question.
RETIRED_BANDS = (
    ("duration", "0s..<30s", 0, 29_999),
    ("duration", "30s..<60s", 30_000, 59_999),
    ("duration", "60s..<3m", 60_000, 179_999),
    ("duration", "5m..<10m", 300_000, 599_999),
    ("duration", "10m..<20m", 600_000, 1_199_999),
    ("duration", "0s..<60s", 0, 59_999),
    ("duration", "60s..<5m", 60_000, 299_999),
    ("duration", "5m..<20m", 300_000, 1_199_999),
)


@pytest.mark.parametrize(("field", "value", "low", "high"), FACET_BANDS)
def test_a_facet_band_selects_exactly_the_files_it_counted(
    field: str, value: str, low: int, high: int | None
) -> None:
    """What a band row counts and what clicking it selects are the same set."""
    column = "height" if field == "resolution" else "duration"
    ends: list[Where] = [Where(f"{column}_min", (low,))]
    if high is not None:
        ends.append(Where(f"{column}_max", (high,)))

    found = scalar(Term(Field(field), value), now=NOW)

    assert found == (ends[0] if len(ends) == 1 else AllOf(tuple(ends)))


@pytest.mark.parametrize(("field", "value", "low", "high"), RETIRED_BANDS)
def test_a_band_the_column_no_longer_offers_still_means_what_it_meant(
    field: str, value: str, low: int, high: int
) -> None:
    """A band the column no longer offers still selects the milliseconds it always did, since a
    saved filter or link may hold it."""
    found = scalar(Term(Field(field), value), now=NOW)

    assert found == AllOf((Where("duration_min", (low,)), Where("duration_max", (high,))))


def test_a_half_open_range_missing_an_end_is_a_problem_rather_than_a_guess() -> None:
    """`a..<b` missing an end matches nothing rather than guessing one."""
    assert scalar(Term(Field.DURATION, "..<60s"), now=NOW) == AnyOf()
    assert scalar(Term(Field.DURATION, "0s..<"), now=NOW) == AnyOf()

    reported = problems_in(parse_tokens("duration:..<60s"), now=NOW)
    assert [(one.field, one.reason) for one in reported] == [
        ("duration", "a range needs both ends")
    ]


def test_excluding_a_pipe_list_is_one_query_typed_or_clicked() -> None:
    """`-media:video|gif` typed distributes the minus as `?media=-video|gif` does."""
    from starlette.datastructures import QueryParams

    neither = group(
        Op.ALL, (Negated(Term(Field.MEDIA, "gif")), Negated(Term(Field.MEDIA, "video")))
    )

    assert parse_tokens("-media:video|gif").where == neither
    assert parse_tokens("-media:video|gif").where == parse(QueryParams("media=-video|gif")).where
    # Excluded, the separators agree: NOT(a OR b) is NOT a AND NOT b.
    assert parse_tokens("-media:video|gif").where == parse_tokens("-media:video,gif").where


def test_a_parameter_holding_nothing_but_separators_narrows_nothing() -> None:
    """`?people=-|` from an emptied control narrows nothing rather than matching nothing."""
    from starlette.datastructures import QueryParams

    assert parse(QueryParams("people=-|")).where == parse(QueryParams("")).where


def test_a_picture_size_can_be_a_plain_number() -> None:
    """`resolution:1200` is a number of pixels, not a band."""
    assert scalar(Term(Field.RESOLUTION, "1200+"), now=NOW) == Where("height_min", (1200,))


def test_a_file_size_can_be_a_plain_number_of_bytes_and_has_a_ceiling() -> None:
    """A bare size is bytes; one too large for the column matches nothing, never clamped."""
    assert scalar(Term(Field.SIZE, "2048+"), now=NOW) == Where("size_min", (2048,))
    assert scalar(Term(Field.SIZE, "99999gb"), now=NOW) == AnyOf()


def test_whether_a_file_has_been_opened_is_asked_as_a_flag() -> None:
    """`viewed:yes` and `viewed:no` are flags over "touched at all", so a part-way file with no
    view is not called unopened. `yes` stays the wide word Recently viewed is built on."""
    assert scalar(Term(Field.VIEWED, "yes"), now=NOW) == Where("opened")
    assert scalar(Term(Field.VIEWED, "no"), now=NOW) == Not(Where("opened"))
    assert scalar(Term(Field.VIEWED, "done"), now=NOW) == AllOf(
        (Where("viewed"), Not(Where("resuming")))
    )


def test_the_same_word_asks_for_a_STATE_when_it_names_one() -> None:
    """A state name (`started`, `finished`) is tried before the yes/no flag; anything else falls
    through to it."""
    assert scalar(Term(Field.VIEWED, "finished"), now=NOW) == Where("finished")
    assert scalar(Term(Field.VIEWED, "started"), now=NOW) == Where("started")
    assert scalar(Term(Field.VIEWED, " Finished "), now=NOW) == Where("finished")
    # A half-typed word matches nothing, never an error.
    assert scalar(Term(Field.VIEWED, "fin"), now=NOW) == AnyOf()


def test_part_way_through_is_a_state_of_its_own_and_reads_as_its_label() -> None:
    """`viewed:continue` reads one per-user parameter, so the facet row, the filter and a tile's
    bar share a rule; its label is a spelling of it."""
    assert scalar(Term(Field.VIEWED, "continue"), now=NOW) == Where("resuming")
    assert scalar(Term(Field.VIEWED, "Continue Watching"), now=NOW) == Where("resuming")
    assert scalar(Term(Field.VIEWED, "continue_watching"), now=NOW) == Where("resuming")


def test_a_label_that_is_not_a_spelling_of_its_token_is_refused_at_import() -> None:
    """A label must be its token or an alias of the same field, or the dropdown teaches a word the
    parser refuses. Checked at construction."""
    assert FilterHelp(Field.TAGS, "Tags", "Anything you have tagged", "tags:beach").label == "Tags"
    assert FilterHelp(Field.IN, "Folder", "A folder in your library", "in:videos").label == "Folder"

    with pytest.raises(ValueError, match="is not a spelling of"):
        FilterHelp(Field.PEOPLE, "Performer", "Someone in it", "people:alex")


# --- the registry's facets, asked of a file ----------------------------------------------------


def test_a_persons_word_is_one_condition_however_it_is_spelled() -> None:
    """Each person token's spellings reach the same leaf."""
    same = {
        "gender": ("gender", []),
        "hair": ("hair", ["hair_colour", "hair_color"]),
        "eyes": ("eyes", ["eye_colour", "eye_color"]),
        "ethnicity": ("ethnicity", []),
        "nationality": ("nationality", ["country"]),
        "breasts": ("breasts", ["breast_type"]),
    }
    for token, (leaf, aliases) in same.items():
        wanted = (Term(Field(token), "blonde"),)
        assert tuple(parse_tokens(f"{token}:blonde").leaves()) == wanted, token
        assert scalar(Term(Field(token), "blonde"), now=NOW) == Where(leaf, ("blonde",)), token
        for old in aliases:
            assert tuple(parse_tokens(f"{old}:blonde").leaves()) == wanted, old
            assert tuple(parse_modal({old: "blonde"}).leaves()) == wanted, old


def test_a_band_is_matched_as_the_name_the_column_wrote() -> None:
    """`height:160-169` is the facet column's value written back; a bare number matches nothing."""
    assert scalar(Term(Field.HEIGHT, "160-169"), now=NOW) == Where("height", ("160-169",))
    assert scalar(Term(Field.HEIGHT, "200+"), now=NOW) == Where("height", ("200+",))
    assert scalar(Term(Field.HEIGHT, "<150"), now=NOW) == Where("height", ("<150",))
    assert scalar(Term(Field.HEIGHT, "165"), now=NOW) == AnyOf()


def test_an_age_is_one_number_and_a_kept_span_still_means_its_ages() -> None:
    """`age:27` is one age; a kept span such as `25-29` still means its ages."""
    assert scalar(Term(Field.AGE, "27"), now=NOW) == Where("age", (27, 27))
    assert scalar(Term(Field.AGE, "25-29"), now=NOW) == Where("age", (25, 29))
    assert scalar(Term(Field.AGE, "50+"), now=NOW) == Where("age", (50, 120))
    assert scalar(Term(Field.AGE, "<25"), now=NOW) == Where("age", (0, 24))
    assert scalar(Term(Field.AGE, "middle"), now=NOW) == AnyOf()


def test_created_reads_the_five_makers_and_the_words_people_reach_for() -> None:
    """`created:` takes the column's five words and their past tenses."""
    assert scalar(Term(Field.CREATED, "download"), now=NOW) == Where("created", ("download",))
    assert scalar(Term(Field.CREATED, "Compressed"), now=NOW) == Where("created", ("compress",))
    assert scalar(Term(Field.CREATED, "edited"), now=NOW) == Where("created", ("edit",))
    assert scalar(Term(Field.CREATED, "folder"), now=NOW) == Where("created", ("library",))
    assert scalar(Term(Field.CREATED, "swap"), now=NOW) == Where("created", ("swap",))
    assert scalar(Term(Field.CREATED, "nobody"), now=NOW) == AnyOf()


def test_a_year_is_four_digits_and_anything_else_is_a_problem_worth_saying() -> None:
    """A year is four digits; anything else is reported as a problem with its filter."""
    assert scalar(Term(Field.RELEASED, "2021"), now=NOW) == Where("released", ("2021",))
    assert scalar(Term(Field.RELEASED, "2021-06"), now=NOW) == AnyOf()

    reported = problems_in(parse_tokens("released:2021-06"), now=NOW)
    assert [(one.field, one.reason) for one in reported] == [("released", "that is not a year")]


def test_a_network_is_the_site_condition_and_not_a_second_copy_of_it() -> None:
    """`network:` is the `sites:` condition; the grouping belongs to the facet."""
    assert scalar(Term(Field.NETWORK, "a1b2c3d4"), now=NOW) == Where("sites", ("a1b2c3d4",))


def test_same_music_is_the_access_layers_group_condition_and_refuses_a_name() -> None:
    """`same_music:` hands the id to the access layer; a value that is not an id is a problem,
    never a 500."""
    an_id = "01HX0000000000000000000001"
    assert scalar(Term(Field.SAME_MUSIC, an_id), now=NOW) == same_music_where(an_id)
    assert scalar(Term(Field.SAME_MUSIC, "beach"), now=NOW) == AnyOf()
    found = problems_in(parse({"q": "same_music:beach"}), now=NOW)
    assert [(one.field, one.value) for one in found] == [("same_music", "beach")]
    assert "ID" in found[0].reason


def test_unnamed_face_is_one_persons_folder_files_with_a_face_still_unnamed() -> None:
    """`unnamed_face:` is counted through the same condition its link opens."""
    an_id = "01HX0000000000000000000001"
    assert scalar(Term(Field.UNNAMED_FACE, an_id), now=NOW) == Where("unnamed_face", (an_id,))
    assert scalar(Term(Field.UNNAMED_FACE, "beach"), now=NOW) == AnyOf()
    found = problems_in(parse({"q": "unnamed_face:beach"}), now=NOW)
    assert [(one.field, one.value) for one in found] == [("unnamed_face", "beach")]


async def test_unnamed_face_is_a_question_only_an_admin_is_answered() -> None:
    """`unnamed_face:` matches nothing for anyone but an admin."""
    compiler = FilterCompiler(None)  # type: ignore[arg-type]
    asked = {"unnamed_face": "01HX0000000000000000000001"}
    admin = await compiler.constrain(Viewer(id="a", role=Role.ADMIN), asked)
    guest = await compiler.constrain(Viewer(id="g", role=Role.GUEST), asked)
    assert "unnamed_face" in repr(admin.where)
    assert "unnamed_face" not in repr(guest.where)


def test_every_one_of_them_reads_its_own_value() -> None:
    """Each value parser reads its own value instead of falling through to the date branch. That a
    guest may ask is proved in `browse/tests/test_facets.py`."""
    for token in (
        Field.GENDER,
        Field.HAIR,
        Field.EYES,
        Field.ETHNICITY,
        Field.NATIONALITY,
        Field.BREASTS,
        Field.HEIGHT,
        Field.AGE,
        Field.RELEASED,
        Field.NETWORK,
    ):
        value = {Field.HEIGHT: "160-169", Field.AGE: "27"}.get(token)
        value = value or ("2021" if token is Field.RELEASED else "blonde")
        assert scalar(Term(token, value), now=NOW) != AnyOf(), token


# --- the values a filter offers after its prefix ---------------------------------------------

#: The fields whose value is typed rather than chosen from a list.
_TYPED = frozenset(
    {
        Field.FILETYPE,
        Field.RATING,
        Field.O_COUNT,
        Field.ADDED,
        Field.DURATION,
        Field.RESOLUTION,
        Field.SIZE,
        Field.VCODEC,
        Field.ACODEC,
        Field.FILENAME,
        Field.TITLE,
        Field.MUSIC,
        Field.GENDER,
        Field.HAIR,
        Field.EYES,
        Field.ETHNICITY,
        Field.NATIONALITY,
        Field.BREASTS,
        Field.HEIGHT,
        Field.AGE,
        Field.RELEASED,
        Field.NETWORK,
        Field.SAME_MUSIC,
        Field.LIKE,
        Field.UNNAMED_FACE,
    }
)


def test_every_filter_names_something_offers_a_list_or_is_typed() -> None:
    """Every field names something, offers a list or is typed: exactly one."""
    for one in Field:
        kinds = [one in ENTITY_FIELDS, one in OFFERED_VALUES, one in _TYPED]
        assert kinds.count(True) == 1, one
    assert frozenset(Field) - _TYPED == SUGGESTED_FIELDS


def test_media_offers_its_three_kinds() -> None:
    assert OFFERED_VALUES[Field.MEDIA] == ("video", "image", "gif")


def test_every_offered_value_is_one_the_parser_takes() -> None:
    """Every offered value is one the parser takes."""
    for one, values in OFFERED_VALUES.items():
        assert values, one
        for value in values:
            query = parse_tokens(f"{one.value}:{value}")
            assert not query.text, (one, value)
            assert [leaf.field for leaf in query.leaves()] == [one], (one, value)
            assert problems_in(query, now=NOW) == [], (one, value)
