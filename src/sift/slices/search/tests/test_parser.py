# SPDX-License-Identifier: AGPL-3.0-or-later
"""The query language pinned: a golden table of what each token means, and the equivalence that
keeps the modal and the box one engine. Parsing is a pure function of the text and the clock."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from sift.kernel.access import AllOf, AnyOf, FolderDepth, Not, Where
from sift.kernel.access.constraints import Filing
from sift.slices.search.filters import (
    ALIASES,
    EVERYTHING,
    FILTERS,
    IMPOSSIBLE,
    MAX_DEPTH,
    MAX_NUMBER,
    MAX_TERMS,
    MAX_VALUE,
    Caret,
    Field,
    Group,
    Negated,
    Node,
    Op,
    Presence,
    Query,
    Term,
    Word,
    clauses,
    group,
    left_out_products,
    over_budget,
    parse,
    parse_modal,
    parse_tokens,
    phrase_prefix,
    problems_in,
    scalar,
    token_prefix,
    word_prefix,
    write,
)

#: A fixed clock, so `added:7d` has an answer that can be written down: midnight UTC, 8 July 2026.
NOW = 1_783_468_800
_DAY = 86_400

# (what was typed, the free text it leaves, the filters it produces)
GRAMMAR: list[tuple[str, str | None, tuple[Node, ...]]] = [
    # --- every token named in the specification ------------------------------------------
    ("people:jane", None, (Term(Field.PEOPLE, "jane"),)),
    ("sites:tiktok", None, (Term(Field.SITES, "tiktok"),)),
    ("tags:beach", None, (Term(Field.TAGS, "beach"),)),
    ("collections:favorites", None, (Term(Field.COLLECTIONS, "favorites"),)),
    ("type:video", None, (Term(Field.MEDIA, "video"),)),
    ("rating:4+", None, (Term(Field.RATING, "4+"),)),
    ("rating:none", None, (Presence(Field.RATING, False),)),
    ("rating:any", None, (Presence(Field.RATING, True),)),
    ("fav:yes", None, (Term(Field.FAV, "yes"),)),
    ("sharing:restricted", None, (Term(Field.SHARING, "restricted"),)),
    ("orientation:portrait", None, (Term(Field.ORIENTATION, "portrait"),)),
    ("enriched:stash", None, (Term(Field.ENRICHED, "stash"),)),
    ("enriched_by:faces", None, (Term(Field.ENRICHED, "faces"),)),
    ("in:clips", None, (Term(Field.IN, "clips"),)),
    ("added:7d", None, (Term(Field.ADDED, "7d"),)),
    ("duration:5m+", None, (Term(Field.DURATION, "5m+"),)),
    # --- presence, on every dimension that has an answer to give -----------------------------
    ("tags:none", None, (Presence(Field.TAGS, False),)),
    ("tags:any", None, (Presence(Field.TAGS, True),)),
    ("-tags", None, (Presence(Field.TAGS, False),)),
    ("-people", None, (Presence(Field.PEOPLE, False),)),
    ("-collections", None, (Presence(Field.COLLECTIONS, False),)),
    ("-sites", None, (Presence(Field.SITES, False),)),
    (
        "-tags -people -collections",
        None,
        (
            Presence(Field.TAGS, False),
            Presence(Field.PEOPLE, False),
            Presence(Field.COLLECTIONS, False),
        ),
    ),
    # --- negating a value rather than a whole dimension --------------------------------------
    ("-tags:beach", None, (Negated(Term(Field.TAGS, "beach")),)),
    ("-type:video", None, (Negated(Term(Field.MEDIA, "video")),)),
    # A list behind a minus excludes every one of them.
    (
        "-tags:a,b",
        None,
        (Negated(Term(Field.TAGS, "a")), Negated(Term(Field.TAGS, "b"))),
    ),
    # --- either one ---------------------------------------------------------------------------
    (
        "tags:beach OR tags:sunset",
        None,
        (group(Op.ANY, (Term(Field.TAGS, "beach"), Term(Field.TAGS, "sunset"))),),
    ),
    (
        "tags:beach or tags:sunset",
        None,
        (group(Op.ANY, (Term(Field.TAGS, "beach"), Term(Field.TAGS, "sunset"))),),
    ),
    (
        "tags:a OR tags:b OR tags:c",
        None,
        (
            group(
                Op.ANY,
                (Term(Field.TAGS, "a"), Term(Field.TAGS, "b"), Term(Field.TAGS, "c")),
            ),
        ),
    ),
    # Precedence: either tag, AND four stars.
    (
        "tags:a OR tags:b rating:4+",
        None,
        (
            group(Op.ANY, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b"))),
            Term(Field.RATING, "4+"),
        ),
    ),
    (
        "tags:beach OR people:jane",
        None,
        (group(Op.ANY, (Term(Field.TAGS, "beach"), Term(Field.PEOPLE, "jane"))),),
    ),
    (
        "tags:beach OR -people",
        None,
        (group(Op.ANY, (Term(Field.TAGS, "beach"), Presence(Field.PEOPLE, False))),),
    ),
    # --- the connective is only a connective between two filters -----------------------------
    ("beach OR sunset", "beach OR sunset", ()),
    ("beach or sunset", "beach or sunset", ()),
    ("beach OR tags:sunset", "beach OR", (Term(Field.TAGS, "sunset"),)),
    ("tags:beach OR sunset", "OR sunset", (Term(Field.TAGS, "beach"),)),
    # Neither OR connects, so both filters simply apply.
    (
        "tags:a OR OR tags:b",
        "OR OR",
        (Term(Field.TAGS, "a"), Term(Field.TAGS, "b")),
    ),
    ("OR tags:beach", "OR", (Term(Field.TAGS, "beach"),)),
    ("tags:beach OR", "OR", (Term(Field.TAGS, "beach"),)),
    # --- a minus in front of something that is not a filter is not a negation -----------------
    ("-clip.mp4", "-clip.mp4", ()),
    ("-colour:blue", "-colour:blue", ()),
    # Every asset is in some place, so there is no "in nothing" to ask for.
    ("-in", "-in", ()),
    ("-type", "-type", ()),
    # --- quoting: a value with a space in it -----------------------------------------------
    ('tags:"beach party"', None, (Term(Field.TAGS, "beach party"),)),
    ('people:"Jane Doe"', None, (Term(Field.PEOPLE, "Jane Doe"),)),
    # An unclosed quote runs to the end: somebody is halfway through typing.
    ('tags:"beach par', None, (Term(Field.TAGS, "beach par"),)),
    # --- several tokens, which AND ----------------------------------------------------------
    (
        "people:jane tags:beach rating:4+",
        None,
        (Term(Field.PEOPLE, "jane"), Term(Field.TAGS, "beach"), Term(Field.RATING, "4+")),
    ),
    # --- free text mixed in with tokens -----------------------------------------------------
    ("holiday tags:beach", "holiday", (Term(Field.TAGS, "beach"),)),
    ("tags:beach holiday sunset", "holiday sunset", (Term(Field.TAGS, "beach"),)),
    ("just some words", "just some words", ()),
    ("", None, ()),
    ("   ", None, ()),
    # --- malformed: a token with nothing after the colon ------------------------------------
    ("tags:", None, (Term(Field.TAGS, ""),)),
    ("rating:", None, (Term(Field.RATING, ""),)),
    ("-tags:", None, (Negated(Term(Field.TAGS, "")),)),
    # --- a comma separates values, in the box exactly as in the modal ------------------------
    ("tags:a,b", None, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b"))),
    ('tags:"a,b"', None, (Term(Field.TAGS, "a,b"),)),
    # --- unknown: not a token at all, so it is free text ------------------------------------
    ("colour:blue", "colour:blue", ()),
    ("http://example.invalid/x", "http://example.invalid/x", ()),
    ("colour:blue tags:beach", "colour:blue", (Term(Field.TAGS, "beach"),)),
    # --- case --------------------------------------------------------------------------------
    ("TAGS:Beach", None, (Term(Field.TAGS, "Beach"),)),
    ("Rating:4+", None, (Term(Field.RATING, "4+"),)),
    ("-TAGS", None, (Presence(Field.TAGS, False),)),
]


@pytest.mark.parametrize(
    ("typed", "text", "filters"), GRAMMAR, ids=[row[0] or "empty" for row in GRAMMAR]
)
def test_the_grammar_means_what_it_says(
    typed: str, text: str | None, filters: tuple[Node, ...]
) -> None:
    """One row of the language, parsed. A gap in this table is a gap in the specification."""
    assert parse_tokens(typed) == Query(text=text, where=group(Op.ALL, filters))


def test_a_value_keeps_the_case_it_was_typed_in() -> None:
    """The field is case-insensitive; the value is not folded, because whether `Beach` and `beach`
    are one tag is the catalog's answer."""
    assert parse_tokens("tags:Beach").where == Term(Field.TAGS, "Beach")


def test_a_choice_is_not_flattened_into_the_things_it_is_a_choice_between() -> None:
    """An OR turned into an AND still parses and runs, so the shape is asserted, not the members."""
    either = parse_tokens("tags:beach OR tags:sunset").where
    assert isinstance(either, type(group(Op.ANY, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b")))))
    assert either == group(Op.ANY, (Term(Field.TAGS, "beach"), Term(Field.TAGS, "sunset")))
    assert either != group(Op.ALL, (Term(Field.TAGS, "beach"), Term(Field.TAGS, "sunset")))

    assert parse_tokens("tags:beach tags:sunset").where == group(
        Op.ALL, (Term(Field.TAGS, "beach"), Term(Field.TAGS, "sunset"))
    )


def test_a_group_of_one_is_that_one_thing() -> None:
    """A group of one is its member, so the two front-ends never differ by a wrapper."""
    assert group(Op.ANY, (Term(Field.TAGS, "a"),)) == Term(Field.TAGS, "a")
    assert group(Op.ALL, (Term(Field.TAGS, "a"),)) == Term(Field.TAGS, "a")


def test_a_group_inside_a_group_of_the_same_kind_is_spliced_into_it() -> None:
    """A nested group of the same kind is spliced in: one spelling per query."""
    nested = group(
        Op.ALL,
        (group(Op.ALL, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b"))), Term(Field.TAGS, "c")),
    )
    assert nested == group(
        Op.ALL, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b"), Term(Field.TAGS, "c"))
    )


# --- the two front-ends are one engine --------------------------------------------------------

# (what was typed into the box, what the modal would send instead)
EQUIVALENT: list[tuple[str, dict[str, str]]] = [
    ("tags:beach", {"tags": "beach"}),
    ("tags:beach tags:sunset", {"tags": "beach,sunset"}),
    ("people:jane", {"people": "jane"}),
    ("rating:4+", {"rating": "4+"}),
    ("rating:none", {"rating": "none"}),
    ("rating:any", {"rating": "any"}),
    ("tags:none", {"tags": "none"}),
    ("tags:any", {"tags": "any"}),
    ("type:video", {"type": "video"}),
    ("fav:yes", {"fav": "yes"}),
    ("sharing:shared", {"sharing": "shared"}),
    ("orientation:square", {"orientation": "square"}),
    ("enriched:folder", {"enriched": "folder"}),
    ("in:clips", {"in": "clips"}),
    ("added:7d", {"added": "7d"}),
    ("duration:5m+", {"duration": "5m+"}),
    ('tags:"beach party"', {"tags": '"beach party"'}),
    # Mixed case: neither front-end may fold it, since that is the catalog's answer.
    ("tags:Beach", {"tags": "Beach"}),
    ('people:"Jane Doe"', {"people": '"Jane Doe"'}),
    (
        "tags:beach people:jane rating:4+",
        {"tags": "beach", "people": "jane", "rating": "4+"},
    ),
]


@pytest.mark.parametrize(("typed", "clicked"), EQUIVALENT, ids=[row[0] for row in EQUIVALENT])
def test_the_modal_and_the_tokens_are_the_same_query(typed: str, clicked: dict[str, str]) -> None:
    """Equivalent inputs parse to equal queries, compared as parsed queries rather than results."""
    assert parse({"q": typed}) == parse(clicked)


def test_a_compound_query_travels_in_the_box_rather_than_in_a_second_spelling() -> None:
    """A choice travels in `q`; the named parameters always mean AND, which saved links rely on."""
    assert parse({"q": "tags:a OR tags:b"}).where == group(
        Op.ANY, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b"))
    )
    assert parse({"tags": "a,b"}).where == group(
        Op.ALL, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b"))
    )


def test_a_typed_query_and_a_clicked_one_combine_rather_than_replace() -> None:
    """Somebody who typed a query and then opened the modal meant both things."""
    both = parse({"q": "tags:beach", "rating": "4+"})
    assert both.where == group(Op.ALL, (Term(Field.TAGS, "beach"), Term(Field.RATING, "4+")))


def test_a_compound_typed_query_survives_being_combined_with_the_modal() -> None:
    """The choice stays a choice: the modal's filters are ANDed onto it, not folded into it."""
    both = parse({"q": "tags:a OR tags:b", "rating": "4+"})
    assert both.where == group(
        Op.ALL,
        (
            group(Op.ANY, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b"))),
            Term(Field.RATING, "4+"),
        ),
    )


def test_the_modal_reads_its_parameters_in_a_fixed_order() -> None:
    """The same filters in any parameter order are the same query."""
    one = parse({"tags": "beach", "rating": "4+", "type": "video"})
    other = parse({"type": "video", "rating": "4+", "tags": "beach"})
    assert one == other


def test_an_emptied_modal_control_is_not_a_filter_that_matches_nothing() -> None:
    """`tags=` from a cleared control is no filter; a typed bare `tags:` stays a filter naming
    nothing, because it was typed on purpose."""
    assert parse({"tags": ""}).where == EVERYTHING
    assert parse({"q": "tags:"}).where == Term(Field.TAGS, "")


# --- the value parsers ------------------------------------------------------------------------
# Each term is resolved alone rather than folded, so two ratings either side of an OR stay apart.


def _rating(value: str) -> object:
    return scalar(Term(Field.RATING, value), now=NOW)


def _duration(value: str) -> object:
    return scalar(Term(Field.DURATION, value), now=NOW)


def _added(value: str) -> object:
    return scalar(Term(Field.ADDED, value), now=NOW)


def test_a_rating_range_reads_every_form_of_it() -> None:
    assert _rating("4") == AllOf((Where("rating_min", (4,)), Where("rating_max", (4,))))
    assert _rating("4+") == Where("rating_min", (4,))
    assert _rating("4-") == Where("rating_max", (4,))
    assert _rating("2..4") == AllOf((Where("rating_min", (2,)), Where("rating_max", (4,))))


def test_a_rating_outside_the_scale_is_clamped_rather_than_refused() -> None:
    """`rating:99+` clamps to ten, the stored scale's top, rather than answering nothing."""
    assert _rating("9+") == Where("rating_min", (9,))
    assert _rating("99+") == Where("rating_min", (10,))


def test_an_o_count_reads_the_range_grammar_the_stars_take_and_is_never_clamped() -> None:
    """A tally has no top, so `o_count:99+` means ninety-nine where `rating:99+` means ten."""

    def o_count(value: str) -> object:
        return scalar(Term(Field.O_COUNT, value), now=NOW)

    assert o_count("3") == AllOf((Where("o_count_min", (3,)), Where("o_count_max", (3,))))
    assert o_count("5+") == Where("o_count_min", (5,))
    assert o_count("2..4") == AllOf((Where("o_count_min", (2,)), Where("o_count_max", (4,))))
    assert o_count("99+") == Where("o_count_min", (99,))
    assert o_count("lots") == AnyOf()


def test_enrichment_is_how_lately_a_stash_box_was_asked_in_any_of_its_words() -> None:
    def enrichment(value: str) -> object:
        return scalar(Term(Field.ENRICHMENT, value), now=NOW)

    assert enrichment("never") == Where("enrichment", ("never",))
    assert enrichment("none") == Where("enrichment", ("never",))
    assert enrichment("Kept-Local") == Where("enrichment", ("local",))
    assert enrichment(" week ") == Where("enrichment", ("week",))
    assert enrichment("old") == Where("enrichment", ("older",))
    assert enrichment("someday") == AnyOf()


def test_a_zero_bound_survives() -> None:
    """Zero is a real bound, not "no bound"."""
    assert _rating("0..2") == AllOf((Where("rating_min", (0,)), Where("rating_max", (2,))))


def test_two_ratings_side_by_side_both_apply_and_therefore_narrow() -> None:
    """Two ratings side by side both apply, so the query narrows by its shape."""
    assert parse_tokens("rating:2+ rating:4+").where == group(
        Op.ALL, (Term(Field.RATING, "2+"), Term(Field.RATING, "4+"))
    )


def test_two_ratings_inside_a_choice_are_not_narrowed_together() -> None:
    """`rating:1 OR rating:5` keeps both ends apart instead of folding into an empty range."""
    assert parse_tokens("rating:1 OR rating:5").where == group(
        Op.ANY, (Term(Field.RATING, "1"), Term(Field.RATING, "5"))
    )


def test_asking_whether_there_is_a_rating_is_not_asking_what_it_is() -> None:
    """`rating:none` is its own question: unrated is NULL, so no number can mean it."""
    for word in ("none", "unrated", "no"):
        assert parse_tokens(f"rating:{word}").where == Presence(Field.RATING, False), word

    for word in ("any", "rated", "yes"):
        assert parse_tokens(f"rating:{word}").where == Presence(Field.RATING, True), word

    assert parse_tokens("rating:4+").where == Term(Field.RATING, "4+")


def test_the_other_dimensions_take_only_the_two_words() -> None:
    """Only rating takes the extra spellings; on other fields each extra word would hide a tag."""
    assert parse_tokens("tags:none").where == Presence(Field.TAGS, False)
    assert parse_tokens("tags:any").where == Presence(Field.TAGS, True)
    assert parse_tokens("tags:no").where == Term(Field.TAGS, "no")
    assert parse_tokens("tags:unrated").where == Term(Field.TAGS, "unrated")


def test_a_duration_reads_its_units() -> None:
    assert _duration("30s+") == Where("duration_min", (30_000,))
    assert _duration("5m-") == Where("duration_max", (300_000,))
    assert _duration("1m..5m") == AllOf(
        (Where("duration_min", (60_000,)), Where("duration_max", (300_000,)))
    )
    assert _duration("90") == AllOf(
        (Where("duration_min", (90_000,)), Where("duration_max", (90_000,)))
    )
    assert _duration("1h+") == Where("duration_min", (3_600_000,))


def test_a_date_covers_the_whole_day_it_names() -> None:
    """`added:` with a calendar day has to find what arrived at teatime, not only at midnight."""
    assert _added("2026-07-01") == AllOf(
        (
            Where("added_from", (1_782_864_000,)),
            Where("added_to", (1_782_864_000 + 86_399,)),
        )
    )


def test_a_date_is_the_day_on_the_machines_clock(machine_zone: Callable[[str], None]) -> None:
    """A typed day is the machine's local day, as a History line's day is."""
    machine_zone("EST5EDT")
    assert _added("2026-07-01") == AllOf(
        (
            Where("added_from", (1_782_864_000 + 4 * 3_600,)),
            Where("added_to", (1_782_864_000 + 4 * 3_600 + 86_399,)),
        )
    )


def test_a_span_is_measured_back_from_now() -> None:
    assert _added("7d") == AllOf(
        (Where("added_from", (NOW - 7 * _DAY,)), Where("added_to", (NOW,)))
    )
    assert _added("24h") == AllOf(
        (Where("added_from", (NOW - 24 * 3_600,)), Where("added_to", (NOW,)))
    )
    assert _added("2w") == AllOf(
        (Where("added_from", (NOW - 14 * _DAY,)), Where("added_to", (NOW,)))
    )


def test_a_date_range_runs_from_the_start_of_one_to_the_end_of_the_other() -> None:
    assert _added("2026-07-01..2026-07-02") == AllOf(
        (
            Where("added_from", (1_782_864_000,)),
            Where("added_to", (1_782_864_000 + _DAY + 86_399,)),
        )
    )


def test_a_kind_of_media_accepts_what_people_actually_say() -> None:
    assert scalar(Term(Field.MEDIA, "video"), now=NOW) == Where("media_type", ("video",))
    assert scalar(Term(Field.MEDIA, "photo"), now=NOW) == Where("media_type", ("image",))
    assert scalar(Term(Field.MEDIA, "clip"), now=NOW) == Where("media_type", ("video",))
    assert scalar(Term(Field.MEDIA, "GIF"), now=NOW) == Where("media_type", ("gif",))


def test_a_favorite_reads_either_way_of_saying_it() -> None:
    for word in ("yes", "1"):
        assert scalar(Term(Field.FAV, word), now=NOW) == Where("favorite"), word
    for word in ("no", "false"):
        assert scalar(Term(Field.FAV, word), now=NOW) == Not(Where("favorite")), word


def test_a_pmv_creator_reads_either_way_of_saying_it() -> None:
    """`pmv:` is a flag, read as `fav:` is: the predicate or its `Not`. `pmv_creator:` is the same
    field under the person record's name."""
    for word in ("yes", "1"):
        assert scalar(Term(Field.PMV, word), now=NOW) == Where("pmv_creator"), word
    for word in ("no", "false"):
        assert scalar(Term(Field.PMV, word), now=NOW) == Not(Where("pmv_creator")), word
    wanted = (Term(Field.PMV, "yes"),)
    assert tuple(parse_tokens("pmv:yes").leaves()) == wanted
    assert tuple(parse_tokens("pmv_creator:yes").leaves()) == wanted
    assert tuple(parse_modal({"pmv_creator": "yes"}).leaves()) == wanted


def test_an_orientation_is_one_of_three_words_and_their_synonyms() -> None:
    assert scalar(Term(Field.ORIENTATION, "portrait"), now=NOW) == Where(
        "orientation", ("portrait",)
    )
    assert scalar(Term(Field.ORIENTATION, "tall"), now=NOW) == Where("orientation", ("portrait",))
    assert scalar(Term(Field.ORIENTATION, "wide"), now=NOW) == Where("orientation", ("landscape",))
    assert scalar(Term(Field.ORIENTATION, "square"), now=NOW) == Where("orientation", ("square",))
    # A value nobody could act on matches nothing rather than failing.
    assert scalar(Term(Field.ORIENTATION, "sideways"), now=NOW) == AnyOf()


def test_enriched_names_which_of_the_seven_things_wrote_to_the_file() -> None:
    """`enriched:` names which of seven sources wrote to the file: stash, faces, folder, filename,
    watermark, metadata and acoustid. `any` and `none` cover all seven; filename, watermark and
    metadata read the same table but answer different questions."""
    stash, faces, folder, filename, watermark, metadata, acoustid = (
        Where("enriched_stash"),
        Where("enriched_faces"),
        Where("enriched_folder"),
        Where("enriched_filename"),
        Where("enriched_watermark"),
        Where("enriched_metadata"),
        Where("enriched_acoustid"),
    )
    assert scalar(Term(Field.ENRICHED, "stash"), now=NOW) == stash
    assert scalar(Term(Field.ENRICHED, "stash-box"), now=NOW) == stash
    assert scalar(Term(Field.ENRICHED, "faces"), now=NOW) == faces
    assert scalar(Term(Field.ENRICHED, "folder"), now=NOW) == folder
    assert scalar(Term(Field.ENRICHED, "filename"), now=NOW) == filename
    assert scalar(Term(Field.ENRICHED, "filenames"), now=NOW) == filename
    assert scalar(Term(Field.ENRICHED, "watermark"), now=NOW) == watermark
    assert scalar(Term(Field.ENRICHED, "watermarks"), now=NOW) == watermark
    assert scalar(Term(Field.ENRICHED, "metadata"), now=NOW) == metadata
    assert scalar(Term(Field.ENRICHED, "acoustid"), now=NOW) == acoustid
    assert scalar(Term(Field.ENRICHED, "any"), now=NOW) == AnyOf(
        (stash, faces, folder, filename, watermark, metadata, acoustid)
    )
    assert scalar(Term(Field.ENRICHED, "none"), now=NOW) == AllOf(
        (
            Not(stash),
            Not(faces),
            Not(folder),
            Not(filename),
            Not(watermark),
            Not(metadata),
            Not(acoustid),
        )
    )
    # A box's own word is bound as a value: the parser cannot know which boxes are configured.
    assert scalar(Term(Field.ENRICHED, "fansdb"), now=NOW) == Where("enriched_box", ("fansdb",))
    assert scalar(Term(Field.ENRICHED, "StashDB"), now=NOW) == Where("enriched_box", ("stashdb",))

    # An unknown word is a box nobody configured, so it matches no file either.
    assert scalar(Term(Field.ENRICHED, "magic"), now=NOW) == Where("enriched_box", ("magic",))
    assert scalar(Term(Field.ENRICHED, "a box!"), now=NOW) == AnyOf()
    assert scalar(Term(Field.ENRICHED, "x"), now=NOW) == AnyOf()


def test_left_out_names_the_files_one_product_gave_up_on() -> None:
    """`left_out:` takes the Build's product keys and the Importing pane's labels for them."""
    thumbnails = Where("left_out", ("thumbnails",))
    assert scalar(Term(Field.LEFT_OUT, "thumbnails"), now=NOW) == thumbnails
    assert scalar(Term(Field.LEFT_OUT, "Thumbnail"), now=NOW) == thumbnails
    assert scalar(Term(Field.LEFT_OUT, "hover_previews"), now=NOW) == Where(
        "left_out", ("previews",)
    )
    assert scalar(Term(Field.LEFT_OUT, "hover-previews"), now=NOW) == Where(
        "left_out", ("previews",)
    )
    assert scalar(Term(Field.LEFT_OUT, "scrubber_strips"), now=NOW) == Where(
        "left_out", ("sprites",)
    )
    assert scalar(Term(Field.LEFT_OUT, "watermarks"), now=NOW) == Where("left_out", ("watermarks",))
    every = tuple(
        Where("left_out", (product,))
        for product in (
            "thumbnails",
            "previews",
            "sprites",
            "fingerprints",
            "faces",
            "meaning",
            "watermarks",
        )
    )
    assert scalar(Term(Field.LEFT_OUT, "any"), now=NOW) == AnyOf(every)
    assert scalar(Term(Field.LEFT_OUT, "none"), now=NOW) == AllOf(tuple(Not(one) for one in every))
    assert scalar(Term(Field.LEFT_OUT, "sandwiches"), now=NOW) == AnyOf()
    assert [problem.field for problem in problems_in(parse_tokens("left_out:x"), now=NOW)] == [
        "left_out"
    ]


def test_a_wall_is_told_which_products_its_left_out_terms_ask_about() -> None:
    """A wall learns which products its `left_out:` terms ask about, in the Build's order."""
    assert left_out_products(parse({"left_out": "faces"})) == ("faces",)
    assert left_out_products(
        parse_tokens("left_out:faces OR left_out:thumbnails left_out:faces")
    ) == (
        "thumbnails",
        "faces",
    )
    assert left_out_products(parse_tokens("left_out:any")) == (
        "thumbnails",
        "previews",
        "sprites",
        "fingerprints",
        "faces",
        "meaning",
        "watermarks",
    )
    assert left_out_products(parse_tokens("left_out:sandwiches")) == ()
    assert left_out_products(parse_tokens("tags:beach")) == ()


def test_a_sharing_state_reads_as_a_question_about_this_file() -> None:
    """`sharing:` asks what was decided on this file, not what reaches it from a root."""
    shared = Where("shared_here")
    restricted = Where("restricted_here")
    assert scalar(Term(Field.SHARING, "shared"), now=NOW) == shared
    assert scalar(Term(Field.SHARING, "restricted"), now=NOW) == restricted
    assert scalar(Term(Field.SHARING, "none"), now=NOW) == AllOf((Not(shared), Not(restricted)))
    assert scalar(Term(Field.SHARING, "any"), now=NOW) == AnyOf((shared, restricted))


# --- input nobody could act on ----------------------------------------------------------------

UNREADABLE = [
    (Field.RATING, "abc"),
    (Field.RATING, "4x"),
    (Field.RATING, ""),
    (Field.RATING, ".."),
    (Field.RATING, "2.."),
    (Field.RATING, "..4"),
    (Field.DURATION, "soon"),
    (Field.DURATION, ""),
    (Field.ADDED, "yesterday"),
    (Field.ADDED, "2026-13-45"),
    (Field.ADDED, ""),
    (Field.MEDIA, "sculpture"),
    (Field.MEDIA, ""),
    (Field.FAV, "maybe"),
    (Field.FAV, ""),
    (Field.PMV, "maybe"),
    (Field.PMV, ""),
    (Field.SHARING, "sort of"),
    (Field.SHARING, ""),
]


@pytest.mark.parametrize(("field", "value"), UNREADABLE, ids=[f"{f}:{v}" for f, v in UNREADABLE])
def test_a_value_nobody_could_act_on_narrows_to_nothing(field: Field, value: str) -> None:
    """An unreadable value narrows to nothing, never to everything."""
    assert scalar(Term(field, value), now=NOW) == AnyOf()


def test_the_problems_in_a_query_are_the_unreadable_values_with_their_reasons() -> None:
    """Each unreadable term is reported with its field and value."""
    found = problems_in(parse({"q": "rating:4+x tags:beach size:enormous"}), now=NOW)
    assert [(one.field, one.value) for one in found] == [("rating", "4+x"), ("size", "enormous")]
    assert found[0].reason == "a rating is a number of stars"
    assert problems_in(parse({"q": "rating:4+ tags:beach"}), now=NOW) == []


def test_a_readable_value_is_not_the_impossible_condition() -> None:
    """The other side of the branch, so the test above cannot pass by always being true."""
    assert scalar(Term(Field.RATING, "4+"), now=NOW) != AnyOf()
    assert scalar(Term(Field.MEDIA, "video"), now=NOW) != AnyOf()
    assert scalar(Term(Field.FAV, "yes"), now=NOW) != AnyOf()


# --- the dropdown's view of where the caret is ------------------------------------------------


def test_the_dropdown_knows_which_token_is_being_typed_and_where_it_starts() -> None:
    """The offset the client replaces from comes from the one tokenizer, which respects quoting."""
    assert token_prefix("people:ja") == Caret(Field.PEOPLE, "ja", 0)
    assert token_prefix("tags:beach people:") == Caret(Field.PEOPLE, "", 11)
    assert token_prefix("holiday tags:be") == Caret(Field.TAGS, "be", 8)

    assert token_prefix("in:c:/photos") == Caret(Field.IN, "c:/photos", 0)

    assert token_prefix('people:"Jane: the') == Caret(Field.PEOPLE, "Jane: the", 0)


def test_a_negated_token_is_completed_without_swallowing_its_minus() -> None:
    """The offset is after the hyphen, so a picked suggestion keeps the negation."""
    assert token_prefix("-tags:be") == Caret(Field.TAGS, "be", 1)
    assert token_prefix("holiday -people:ja") == Caret(Field.PEOPLE, "ja", 9)


def test_the_caret_has_left_the_token_after_any_whitespace() -> None:
    """Any whitespace, as `isspace` defines it, ends the token."""
    for space in (" ", "\t", "\n", "\r", "\x0b", "\x0c"):
        assert token_prefix(f"people:ja{space}") is None, repr(space)


def test_the_dropdown_offers_recents_when_the_caret_is_not_in_a_token() -> None:
    """None is the signal to show recent searches instead of matches."""
    assert token_prefix("") is None
    assert token_prefix("holiday") is None
    assert token_prefix("tags:beach ") is None
    # An unknown word with a colon is a bare word, not a token.
    assert token_prefix("colour:bl") is None


def test_a_field_with_nothing_to_suggest_is_still_a_token() -> None:
    """A field with nothing to suggest, such as `rating:`, is still a token and draws a chip."""
    assert token_prefix("rating:4") == Caret(Field.RATING, "4", 0)
    assert token_prefix("rating:") == Caret(Field.RATING, "", 0)
    assert token_prefix("type:vid") == Caret(Field.MEDIA, "vid", 0)
    assert token_prefix("fav:") == Caret(Field.FAV, "", 0)
    assert token_prefix("duration:2m") == Caret(Field.DURATION, "2m", 0)
    assert token_prefix("beach added:7d") == Caret(Field.ADDED, "7d", 6)


def test_every_filter_is_offered_and_every_example_parses() -> None:
    """Every filter is offered, and every example printed beside it parses."""
    for entry in FILTERS:
        parsed = parse_tokens(entry.example)
        assert parsed.text is None, f"{entry.example} was read as free text, not as a filter"
        assert parsed.where is not EVERYTHING, f"{entry.example} built no constraint"


def test_every_label_and_retired_token_still_resolves() -> None:
    """Every label and retired token resolves, since a stored search keeps the text as typed and an
    unknown token would widen it to free text."""
    labelled = {
        "folder": "in",
        "file_type": "filetype",
        "favorites": "fav",
        "sharing_status": "sharing",
        "file_size": "size",
        "video_codec": "vcodec",
        "audio_codec": "acodec",
        "file_name": "filename",
    }
    for label, token in labelled.items():
        assert ALIASES[label].value == token, label

    assert ALIASES["type"].value == "media"

    # The two spellings must compile to the same constraint, not merely resolve.
    assert parse_tokens("fav:yes") == parse_tokens("favorites:yes")
    assert parse_tokens("in:videos") == parse_tokens("folder:videos")
    assert parse_tokens("size:500mb+") == parse_tokens("file_size:500mb+")
    assert parse_tokens("duration:2m+") == parse_tokens("duration:2m+")


def test_a_word_nobody_named_is_not_a_filter() -> None:
    """Only a filter's names, current and past, are spellings; no invented synonyms."""
    for word in ("length", "watched", "person", "folder_name", "codec"):
        assert word not in ALIASES, word
        assert parse_tokens(f"{word}:x").text is not None, f"{word}: was read as a filter"


def test_the_dropdown_completes_a_bare_word_across_the_catalog() -> None:
    """`word_prefix` finds the bare word being typed and where it starts."""
    assert word_prefix("holiday") == Word("holiday", 0)
    assert word_prefix("tags:city be") == Word("be", 10)
    assert word_prefix('"be') == Word("be", 0)


def test_the_dropdown_looks_a_name_up_by_the_whole_run_of_words() -> None:
    """`phrase_prefix` looks a name up by the whole run of words, so `reya so` finds the person."""
    assert phrase_prefix("reya so") == Word("reya so", 0)
    assert phrase_prefix("cassia Lynn") == Word("cassia Lynn", 0)
    assert phrase_prefix("anne marie ha") == Word("anne marie ha", 0)


def test_a_phrase_stops_at_anything_that_is_not_a_plain_word() -> None:
    """A phrase stops at a filter or a connective, so a pick never swallows a filter."""
    assert phrase_prefix("tags:beach reya so") == Word("reya so", 11)
    assert phrase_prefix("-people:ja cassia Lynn") == Word("cassia Lynn", 11)
    assert phrase_prefix("tags:a or cassia Lynn") == Word("cassia Lynn", 10)


def test_there_is_no_phrase_when_the_phrase_is_just_the_word_again() -> None:
    """None means "ask the word", sparing a second lookup when the phrase is one word."""
    assert phrase_prefix("holiday") is None
    assert phrase_prefix("tags:city be") is None
    assert phrase_prefix("") is None
    assert phrase_prefix("holiday ") is None
    assert phrase_prefix("bella people:ja") is None


def test_a_word_is_not_offered_when_the_caret_is_on_a_token_or_nothing() -> None:
    """No bare word is offered on a token, on nothing, or on a finished word."""
    assert word_prefix("people:ja") is None
    assert word_prefix("rating:4") is None
    assert word_prefix("-people:ja") is None
    assert word_prefix("") is None
    # Empty quotes leave no word once the quotes come off.
    assert word_prefix('""') is None
    for space in (" ", "\t", "\n", "\r", "\x0b", "\x0c"):
        assert word_prefix(f"holiday{space}") is None, repr(space)
    assert word_prefix("colour:bl") == Word("colour:bl", 0)


# --- the cost bound ---------------------------------------------------------------------------


def test_a_query_carrying_more_filters_than_the_cap_matches_nothing() -> None:
    """Over the term cap the query matches nothing: each term is a lookup, and a trimmed filter
    would let more through than was asked for."""
    within = parse({"tags": ",".join(f"tag{number}" for number in range(MAX_TERMS))})
    assert over_budget(within) is False

    over = parse({"tags": ",".join(f"tag{number}" for number in range(MAX_TERMS + 1))})
    assert over.where == IMPOSSIBLE

    # An overlong value is a prefix scan bought with one parameter.
    assert over_budget(parse({"tags": "x" * (MAX_VALUE + 1)})) is True
    assert over_budget(parse({"tags": "x" * MAX_VALUE})) is False


def test_a_tree_nested_deeper_than_the_cap_matches_nothing() -> None:
    """A tree deeper than the cap matches nothing, so nesting cannot overflow the stack."""
    deep: Node = Term(Field.TAGS, "beach")
    for _ in range(MAX_DEPTH):
        deep = Group(Op.ANY, (deep,))
    assert over_budget(Query(where=deep)) is True

    shallow: Node = Term(Field.TAGS, "beach")
    for _ in range(MAX_DEPTH - 2):
        shallow = Group(Op.ANY, (shallow,))
    assert over_budget(Query(where=shallow)) is False


def test_the_cap_is_applied_before_the_work_it_bounds() -> None:
    """The cap refuses before the work it bounds is done."""
    over = parse({"tags": ",".join(["x"] * 20_000)})
    assert over.where == IMPOSSIBLE
    assert over.leaves() == ()

    within = parse({"tags": ",".join(f"tag{n}" for n in range(MAX_TERMS))})
    assert len(within.leaves()) == MAX_TERMS
    assert over_budget(within) is False


# --- named parameters ------------------------------------------------------------------------


def test_a_named_parameter_can_exclude_and_the_minus_covers_all_of_it() -> None:
    """A minus on a named parameter excludes every value in it, distributed over the values exactly
    as `-people:a,b` typed is."""
    from starlette.datastructures import QueryParams

    assert parse(QueryParams("tags=-beach")).where == parse_tokens("-tags:beach").where

    assert parse(QueryParams("people=-a,b")).where == parse_tokens("-people:a,b").where
    assert parse(QueryParams("people=-a,b")).where == group(
        Op.ALL, (Negated(Term(Field.PEOPLE, "a")), Negated(Term(Field.PEOPLE, "b")))
    )

    # NOT(a OR b) is NOT a AND NOT b, so both spellings reach one node.
    assert parse(QueryParams("media=-video|gif")).where == group(
        Op.ALL, (Negated(Term(Field.MEDIA, "gif")), Negated(Term(Field.MEDIA, "video")))
    )


def test_a_pipe_means_either_of_them_in_both_front_ends() -> None:
    """A pipe means either, typed or clicked, and is what the OR connective spells."""
    from starlette.datastructures import QueryParams

    either = group(Op.ANY, (Term(Field.TAGS, "beach"), Term(Field.TAGS, "sunset")))

    assert parse(QueryParams("tags=beach|sunset")).where == group(Op.ALL, (either,))
    assert parse_tokens("tags:beach|sunset").where == group(Op.ALL, (either,))
    assert (
        parse_tokens("tags:beach|sunset").where == parse_tokens("tags:beach OR tags:sunset").where
    )

    assert parse(QueryParams("tags=beach,sunset")).where == parse_tokens("tags:beach,sunset").where
    assert (
        parse(QueryParams("tags=beach,sunset")).where
        != parse(QueryParams("tags=beach|sunset")).where
    )


def test_a_parameter_given_twice_applies_both_times() -> None:
    """A parameter given twice is read both times and combined with ANY, as a multi-select means;
    different fields still combine with ALL."""
    from starlette.datastructures import QueryParams

    both = parse(QueryParams("tags=beach&tags=sunset"))
    assert both.where == group(
        Op.ALL,
        (
            group(Op.ALL, (Term(Field.TAGS, "beach"),)),
            group(Op.ALL, (Term(Field.TAGS, "sunset"),)),
        ),
    )

    mixed = parse(QueryParams("tags=beach&rating=4%2B&tags=sunset"))
    assert mixed.where == group(
        Op.ALL,
        (
            group(Op.ALL, (Term(Field.TAGS, "beach"),)),
            group(Op.ALL, (Term(Field.TAGS, "sunset"),)),
            group(Op.ALL, (Term(Field.RATING, "4+"),)),
        ),
    )

    # A plain dict, one value per name, still works.
    assert parse({"tags": "beach"}).where == Term(Field.TAGS, "beach")


def test_a_number_too_big_to_store_matches_nothing_rather_than_raising() -> None:
    """A number too big for SQLite to bind matches nothing instead of raising."""
    huge = "9" * 180
    assert _duration(f"{huge}s") == AnyOf()
    assert _added(f"{huge}d") == AnyOf()
    assert _duration(huge) == AnyOf()

    assert _duration(f"{MAX_NUMBER}s") != AnyOf()
    assert _duration(f"{MAX_NUMBER + 1}s") == AnyOf()


def test_a_span_means_one_thing_alone_and_another_as_an_end_of_a_range() -> None:
    """`7d` alone is the last seven days; as a range end it is the instant seven days ago."""
    assert _added("7d") == AllOf(
        (Where("added_from", (NOW - 7 * _DAY,)), Where("added_to", (NOW,)))
    )
    assert _added("7d+") == Where("added_from", (NOW - 7 * _DAY,))

    # `7d-` is older than seven days ago.
    assert _added("7d-") == Where("added_to", (NOW - 7 * _DAY,))

    assert _added("7d..1d") == AllOf(
        (Where("added_from", (NOW - 7 * _DAY,)), Where("added_to", (NOW - _DAY,)))
    )

    # Crossed ends both hold, so nothing satisfies them.
    assert _added("1d..7d") == AllOf(
        (Where("added_from", (NOW - _DAY,)), Where("added_to", (NOW - 7 * _DAY,)))
    )

    assert _added("2026-07-01") == AllOf(
        (
            Where("added_from", (1_782_864_000,)),
            Where("added_to", (1_782_864_000 + 86_399,)),
        )
    )


def test_zero_stars_is_not_how_you_ask_for_unrated() -> None:
    """`rating:0` matches nothing since nothing stores zero; `rating:none` asks for unrated."""
    assert _rating("0") == AllOf((Where("rating_min", (0,)), Where("rating_max", (0,))))
    assert parse_tokens("rating:0").where == Term(Field.RATING, "0")
    assert parse_tokens("rating:none").where == Presence(Field.RATING, False)


def test_a_comma_means_the_same_thing_in_both_front_ends() -> None:
    """A comma splits values in both front-ends; a quoted comma is part of the value."""
    assert parse({"q": "tags:a,b"}) == parse({"tags": "a,b"})
    assert parse({"q": 'tags:"a,b"'}) == parse({"tags": '"a,b"'})


def test_a_token_naming_nothing_is_kept_as_a_filter_that_matches_nothing() -> None:
    """A bare `tags:` is a term matching nothing, answered without the database."""
    for token in ("tags", "people", "sites", "collections", "in"):
        assert parse_tokens(f"{token}:").where == Term(Field(token), ""), token


def test_a_space_in_a_token_still_ends_it_and_that_is_the_quoting_rule() -> None:
    """A space ends a typed token (quoting is for that), while the modal's value has no such
    boundary."""
    typed = parse({"q": "people:jane doe"})
    assert typed.where == Term(Field.PEOPLE, "jane")
    assert typed.text == "doe"

    assert parse({"q": 'people:"jane doe"'}) == parse({"people": '"jane doe"'})


# --- writing a query back out --------------------------------------------------------------------
# The server writes a query back out, so no client is a second writer of the language.

WRITTEN = [
    "tags:beach",
    "tags:a OR tags:b",
    "tags:a OR tags:b OR tags:c",
    "tags:a,b",
    "tags:a,b OR tags:c",
    "-tags",
    "-tags:beach",
    "-tags:a,b",
    "tags:any",
    "tags:a OR tags:b rating:4+",
    "people:jane tags:beach rating:4+",
    'tags:"beach party"',
    'tags:"a,b"',
    "tags:beach OR people:jane",
    "tags:beach OR -people",
    "-tags -people -collections",
    "in:clips added:7d duration:5m+ fav:yes type:video",
]


@pytest.mark.parametrize("typed", WRITTEN)
def test_a_query_written_out_reads_back_as_the_same_query(typed: str) -> None:
    """Parse, write and parse again give the same query, not merely an equivalent one."""
    once = parse_tokens(typed)
    again = parse_tokens(write(once.where))
    assert again.where == once.where


def test_a_choice_with_no_options_is_written_as_a_filter_naming_nothing() -> None:
    """The impossible condition is written as `tags:`, the one spelling that round-trips."""
    assert write(IMPOSSIBLE) == "tags:"
    assert parse_tokens("tags:").where == Term(Field.TAGS, "")


def test_a_negated_group_is_written_as_each_of_its_parts_negated() -> None:
    """A negated group is written as each part negated, so the writer is total."""
    either = group(Op.ANY, (Term(Field.TAGS, "a"), Term(Field.TAGS, "b")))
    assert write(Negated(either)) == "-tags:a -tags:b"

    both = group(Op.ALL, (Term(Field.TAGS, "a"), Term(Field.PEOPLE, "b")))
    assert write(Negated(both)) == "-people:b OR -tags:a"


def test_a_negation_of_a_negation_is_the_thing_itself() -> None:
    assert write(Negated(Negated(Term(Field.TAGS, "a")))) == "tags:a"


def test_a_negated_presence_is_the_presence_turned_over() -> None:
    """ "Not nothing" is "something"."""
    assert write(Negated(Presence(Field.TAGS, False))) == "tags:any"
    assert write(Negated(Presence(Field.TAGS, True))) == "-tags"


def test_a_group_of_mixed_fields_is_written_side_by_side() -> None:
    """Different fields are written side by side; a comma list is one field's."""
    assert (
        write(group(Op.ALL, (Term(Field.TAGS, "a"), Term(Field.PEOPLE, "b")))) == "people:b tags:a"
    )


def test_a_query_is_described_as_the_rows_it_is_made_of() -> None:
    """The rows the Filters screen draws, one per thing the query asks for."""
    rows = clauses(parse_tokens("tags:a OR tags:b -people rating:4+"))
    described = {row.query: row for row in rows}

    assert described["tags:a OR tags:b"].field is Field.TAGS
    assert described["tags:a OR tags:b"].values == ("a", "b")
    assert described["tags:a OR tags:b"].match is Op.ANY
    assert described["-people"].present is False
    assert described["-people"].negated is True
    assert described["rating:4+"].values == ("4+",)
    assert described["rating:4+"].negated is False


def test_a_query_of_one_filter_is_one_row() -> None:
    """A single-leaf tree is one row."""
    assert [row.query for row in clauses(parse_tokens("tags:beach"))] == ["tags:beach"]
    assert clauses(parse_tokens("")) == ()


def test_a_row_across_two_fields_names_neither_of_them() -> None:
    """A choice across fields belongs to no one field."""
    (row,) = clauses(parse_tokens("tags:beach OR people:jane"))
    assert row.field is None
    assert set(row.values) == {"beach", "jane"}


def test_each_exclusion_is_its_own_row() -> None:
    """A list behind a minus is one row per value, as it writes back out."""
    rows = clauses(parse_tokens("-tags:a,b"))
    assert [row.query for row in rows] == ["-tags:a", "-tags:b"]
    assert all(row.negated for row in rows)


def test_a_choice_of_exclusions_reads_as_one_exclusion_of_several() -> None:
    """ "Not a or not b" is one row excluding both, combined the other way."""
    (row,) = clauses(parse_tokens("-tags:a OR -tags:b"))
    assert row.negated is True
    assert row.match is Op.ALL
    assert row.values == ("a", "b")
    # The field is read through the negations, so the rule builder keeps the row.
    assert row.field is Field.TAGS


def test_a_range_with_neither_end_is_the_condition_nothing_satisfies() -> None:
    """A range with neither end matches nothing, not everything. No query produces one."""
    from sift.slices.search.filters import Range, _between

    assert _between("rating_min", "rating_max", Range(None, None)) == AnyOf()
    assert _between("rating_min", "rating_max", Range(1, None)) == Where("rating_min", (1,))


def test_faces_is_not_a_field_anybody_can_type() -> None:
    """`faces:` is ordinary words, not a filter."""
    assert "faces" not in {field.value for field in Field}
    typed = parse_tokens("faces:unnamed")
    assert typed.where == EVERYTHING
    assert typed.text == "faces:unnamed"


# --- the renamed token and the fields added with it ------------------------------------------


def test_the_old_spelling_of_the_media_token_still_means_what_it_meant() -> None:
    """`type:` still compiles to the same constraint as `media:`."""
    assert parse({"q": "type:video"}) == parse({"q": "media:video"})
    assert scalar(Term(Field.MEDIA, "video"), now=NOW) == Where("media_type", ("video",))


def test_the_old_spelling_survives_being_drawn_and_put_back() -> None:
    """A chip from an old query is written back in the new spelling with the same meaning."""
    old = parse({"q": "type:video rating:4+"})
    assert parse({"q": write(old.where)}) == old


def test_the_container_is_its_own_question() -> None:
    """The container (`filetype:`) is a separate question from the kind of media."""
    assert scalar(Term(Field.FILETYPE, "mp4"), now=NOW) == Where("container", ("mp4",))
    assert scalar(Term(Field.FILETYPE, ".MKV"), now=NOW) == Where("container", ("mkv",))


def test_a_named_picture_size_is_a_band_and_not_a_number() -> None:
    """A named picture size covers up to just under the next name; few files are exactly 1080."""
    banded = scalar(Term(Field.RESOLUTION, "1080p"), now=NOW)
    assert banded == AllOf((Where("height_min", (1080,)), Where("height_max", (1439,))))
    assert scalar(Term(Field.RESOLUTION, "1080p+"), now=NOW) == Where("height_min", (1080,))
    assert scalar(Term(Field.RESOLUTION, "4k"), now=NOW) == AllOf(
        (Where("height_min", (2160,)), Where("height_max", (4319,)))
    )


def test_a_file_size_written_in_a_unit_covers_that_unit() -> None:
    """A size in a unit covers that unit; no two files share a byte count."""
    assert scalar(Term(Field.SIZE, "500mb"), now=NOW) == AllOf(
        (
            Where("size_min", (500 * 1024**2,)),
            Where("size_max", (501 * 1024**2 - 1,)),
        )
    )
    assert scalar(Term(Field.SIZE, "1.5gb+"), now=NOW) == Where("size_min", (int(1.5 * 1024**3),))


def test_a_codec_is_folded_onto_the_spelling_the_probe_wrote() -> None:
    """A codec is folded onto the probe's spelling."""
    assert scalar(Term(Field.VCODEC, "H.265"), now=NOW) == Where("vcodec", ("hevc",))
    assert scalar(Term(Field.VCODEC, "x264"), now=NOW) == Where("vcodec", ("h264",))


def test_having_no_sound_is_asked_as_a_presence_and_not_as_a_codec_name() -> None:
    """A silent clip is asked as a presence; no codec name names an absence."""
    assert scalar(Term(Field.ACODEC, "none"), now=NOW) == Not(Where("has_audio"))
    assert scalar(Term(Field.ACODEC, "aac"), now=NOW) == Where("acodec", ("aac",))


def test_a_filename_is_matched_anywhere_inside_both_names() -> None:
    """A filename matches anywhere in either name, with typed wildcards kept literal."""
    found = scalar(Term(Field.FILENAME, "holi_day"), now=NOW)
    assert found == Where("filename", ("%holi\\_day%", "%holi\\_day%"))


def test_a_value_the_new_fields_cannot_read_narrows_to_nothing() -> None:
    """The new fields narrow an unreadable value to nothing."""
    for field, value in (
        (Field.RESOLUTION, "enormous"),
        (Field.SIZE, "a lot"),
        (Field.FILETYPE, ""),
        (Field.VCODEC, ""),
    ):
        assert scalar(Term(field, value), now=NOW) == AnyOf()


def test_a_saved_search_written_the_old_way_still_narrows() -> None:
    """A saved search with the old `type` parameter still narrows instead of widening."""
    assert parse_modal({"type": "video"}) == parse_modal({"media": "video"})
    assert parse_modal({"type": "video"}).where != EVERYTHING


# --- how far below a named folder `in:` reaches ------------------------------------------------


def test_a_request_that_names_no_depth_means_the_whole_subtree() -> None:
    """No depth means the whole subtree, which existing links rely on."""
    assert parse({"in": "abc"}).folder_depth is FolderDepth.SUBTREE
    assert parse_modal({}).folder_depth is FolderDepth.SUBTREE


def test_a_control_asks_for_the_folder_and_not_what_is_under_it() -> None:
    """A named parameter asks for the folder alone, and survives the join with `q`."""
    assert parse({"in": "abc", "depth": "direct"}).folder_depth is FolderDepth.DIRECT
    assert parse({"q": "clip", "in": "abc", "depth": "direct"}).folder_depth is FolderDepth.DIRECT


def test_there_is_no_word_for_the_depth_in_the_query_language() -> None:
    """A depth cannot be typed: `depth:direct` in the box is free text."""
    typed = parse({"q": "depth:direct", "in": "abc"})
    assert typed.folder_depth is FolderDepth.SUBTREE


def test_a_depth_nobody_could_read_matches_nothing_rather_than_widening() -> None:
    """An unreadable depth matches nothing; a default could answer with more."""
    assert parse({"in": "abc", "depth": "deeper"}).where == IMPOSSIBLE


def test_two_depths_in_one_address_take_the_narrower() -> None:
    """Two depths in one address take the narrower."""

    class Twice(dict[str, str]):
        def getlist(self, name: str) -> list[str]:
            return ["subtree", "direct"] if name == "depth" else []

    assert parse_modal(Twice()).folder_depth is FolderDepth.DIRECT


# --- one History line's files: `?filed=`, `?tagged=`, `?named=` -------------------------------


def test_a_filing_is_read_off_the_address_and_carried_onto_the_query() -> None:
    """A History line's filing is read off the address as the UTC day number and carried onto the
    query."""
    query = parse({"q": "clip", "filed": "01HXSITE~download~2023-11-14"})

    assert query.filings == (Filing("filed", "01HXSITE", "download", 19675),)
    assert query.filings[0].where == Where("filed_under", ("01HXSITE", "download", 19675))


def test_an_empty_source_and_an_empty_day_are_values_and_not_any() -> None:
    """An empty source or day binds a NULL compared with `IS`, never a wildcard."""
    (filing,) = parse_modal({"tagged": "01HXTAG~~"}).filings

    assert filing.where == Where("tagged_with", ("01HXTAG", None, None))
    assert filing.value == "01HXTAG~~"


def test_a_stash_box_filing_says_which_box_and_the_rest_of_the_value_is_the_name() -> None:
    """The box is the third part; the rest is the name, separators and all."""
    (named,) = parse_modal({"named": "01HXP~stash_box~2023-11-14~odd~name"}).filings
    (nameless,) = parse_modal({"named": "01HXP~stash_box~2023-11-14"}).filings

    assert named.where == Where("named_as_box", ("01HXP", 19675, "odd~name"))
    assert nameless.where == Where("named_as_box", ("01HXP", 19675, None))
    assert Filing.read("named", named.value) == named


@pytest.mark.parametrize(
    "value",
    [
        "no-separators",
        "01HX~download",
        "01HX~download~2023-02-30",
        "01HX~download~yesterday",
        "01HX~Download~2023-11-14",
        "~download~2023-11-14",
        "01HX~download~2023-11-14~a-box",
    ],
)
def test_a_filing_nobody_could_read_matches_nothing_rather_than_widening(value: str) -> None:
    """An unreadable filing matches nothing rather than opening the whole Site."""
    assert parse({"filed": value}).where == IMPOSSIBLE


def test_an_emptied_filing_is_a_cleared_control_and_asks_nothing() -> None:
    """A cleared `filed=` asks nothing."""
    assert parse_modal({"filed": ""}).filings == ()


def test_there_is_no_word_for_a_filing_in_the_query_language() -> None:
    """A filing cannot be typed: `filed:` in the box is free text."""
    assert parse({"q": "filed:01HX~download~2023-11-14"}).filings == ()


def test_filings_count_against_the_term_budget() -> None:
    """Each filing is a subquery, so it counts against the term cap."""

    class Many(dict[str, str]):
        def getlist(self, name: str) -> list[str]:
            if name != "filed":
                return []
            return [f"01HX{index}~download~2023-11-14" for index in range(MAX_TERMS + 1)]

    assert parse_modal(Many()).where == IMPOSSIBLE
    at_the_cap = Query(
        filings=tuple(Filing("filed", f"S{n}", None, None) for n in range(MAX_TERMS))
    )
    assert not over_budget(at_the_cap)
    assert over_budget(Query(where=Term(Field.TAGS, "x"), filings=at_the_cap.filings))
