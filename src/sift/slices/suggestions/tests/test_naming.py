# SPDX-License-Identifier: AGPL-3.0-or-later
"""The path reader, against the shapes real collections take; each layout is an acceptance case."""

from __future__ import annotations

import pytest

from sift.slices.suggestions.naming import (
    STOP_WORDS,
    Reading,
    Segment,
    classify,
    fold,
    fold_known,
    is_date_like,
    is_stop_folder,
    known_names_in,
    known_people_in,
    people_in,
    person_in_filename,
    read_chain,
    reads_like_a_name,
    repeated_prefix,
    strip_noise,
    username_and_number_in_filename,
    username_in_filename,
)


class TestTheLayouts:
    """One per shape, using its worked example."""

    def test_a_downloader_tree_reads_site_then_handle(self) -> None:
        reading = read_chain(["gallery-dl", "instagram", "harlowquin"])
        assert reading == Reading(name="harlowquin", site="instagram", is_username=True, depth=2)

    def test_a_downloader_tree_with_a_post_folder_under_the_handle(self) -> None:
        reading = read_chain(["coomer", "onlyfans", "reya solberg", "post-2023"])
        assert reading.name == "reya solberg"
        assert reading.site == "onlyfans"
        assert reading.is_username is True

    def test_b_person_first_with_the_site_below(self) -> None:
        reading = read_chain(["Models", "Nadia Vance", "Instagram"])
        assert reading.name == "Nadia Vance"
        assert reading.site == "Instagram"
        # Below the person, so her folder is a display name and not a username on that site.
        assert reading.is_username is False

    def test_b_person_first_with_no_site_at_all(self) -> None:
        assert read_chain(["Models", "Nadia Vance"]) == Reading(name="Nadia Vance", depth=1)

    def test_c_the_site_prefix_is_the_site_and_the_middle_field_is_the_person(self) -> None:
        files = [
            "QMTV - Jane Doe - Show 34.mp4",
            "QMTV - Mary Roe - Show 35.mp4",
            "QMTV - Jane Doe - Show 36.mp4",
        ]
        assert repeated_prefix(files) == "QMTV"
        assert person_in_filename(files[0], after_prefix=True) == "Jane Doe"
        assert person_in_filename(files[1], after_prefix=True) == "Mary Roe"

    def test_d_a_topic_folder_names_nobody(self) -> None:
        assert read_chain(["Sandbar runways"]).name == "Sandbar runways"
        # It reads like a name and the faces are what refuse it, but the plainly generic ones are
        # refused here, before anything is proposed.
        assert read_chain(["Beach"]).name == ""
        assert read_chain(["Best of"]).name == ""
        assert read_chain(["Compilations"]).name == ""

    def test_e_a_flat_dump_contributes_nothing(self) -> None:
        for dump in ("Downloads", "New folder (2)", "Telegram Desktop", "Camera Roll"):
            assert read_chain([dump]) == Reading(), dump

    def test_f_a_date_tree_is_skipped_entirely(self) -> None:
        assert read_chain(["2023", "2023-07", "14"]) == Reading()
        assert read_chain(["Nadia Vance", "2023", "2023-07", "14"]).name == "Nadia Vance"

    def test_g_a_pack_folder_strips_back_to_the_person(self) -> None:
        assert strip_noise("Nadia Vance OnlyFans Mega Pack 2023") == "Nadia Vance"
        assert strip_noise("Nadia Vance (part 2)") == "Nadia Vance"
        assert strip_noise("Nadia Vance - Copy") == "Nadia Vance"

    def test_h_a_type_folder_is_noise_above_or_below_the_person(self) -> None:
        assert read_chain(["Videos", "Nadia Vance"]).name == "Nadia Vance"
        assert read_chain(["Nadia Vance", "Videos"]).name == "Nadia Vance"
        assert read_chain(["Nadia Vance", "4K"]).name == "Nadia Vance"

    def test_i_a_scene_release_keeps_only_what_precedes_the_year(self) -> None:
        assert strip_noise("Studio.Name.2023.1080p.WEB-DL.x264-GRP") == "Studio Name"

    def test_j_a_yt_dlp_flat_name_claims_its_uploader(self) -> None:
        assert person_in_filename("Uploader - Title [a1B2c3D4e5F].mp4", after_prefix=False) == (
            "Uploader"
        )

    def test_the_deep_chain_claims_the_nearest_meaningful_folder(self) -> None:
        reading = read_chain(["Media", "Models", "Instagram", "harlowquin", "posts", "2023"])
        assert reading.name == "harlowquin"
        assert reading.site == "Instagram"
        assert reading.is_username is True


class TestTheStopList:
    """A folder for each junk category proposes nothing."""

    @pytest.mark.parametrize(
        "folder",
        [
            "2023",
            "2023-07-14",
            "14-07-2023",
            "July 2023",
            "1080p",
            "4K",
            "WEB-DL",
            "x264",
            "mkv",
            "1234567890123456789",
            "[a1B2c3D4e5F]",
            "part 2",
            "Copy",
            "Mega Pack",
            "siterip",
            "Downloads",
            "New folder",
            "temp",
            "unsorted",
            "misc",
            "media",
            "pics",
            "videos",
            "gifs",
            "images",
            "photos",
            "stuff",
            "sorted",
        ],
    )
    def test_it_names_nobody(self, folder: str) -> None:
        assert is_stop_folder(folder), folder
        assert read_chain([folder]).name == ""

    def test_no_person_called_videos(self) -> None:
        assert read_chain(["Videos"]).name == ""
        assert read_chain(["Pics", "GIFs", "4K"]).name == ""

    def test_a_junk_word_inside_a_longer_name_is_stripped_not_fatal(self) -> None:
        assert is_stop_folder("Jane Doe Videos") is False
        assert strip_noise("Jane Doe Videos") == "Jane Doe"


class TestNameShape:
    def test_a_handle_is_a_name(self) -> None:
        assert reads_like_a_name("harlowquin")
        assert reads_like_a_name("nadia_vance")

    def test_a_sentence_is_not(self) -> None:
        assert not reads_like_a_name("send these to dave before friday")

    def test_numbers_alone_are_not(self) -> None:
        assert not reads_like_a_name("2023")
        assert not reads_like_a_name("00123456")

    def test_something_too_long_is_not(self) -> None:
        assert not reads_like_a_name("a" * 61)

    def test_two_people_in_one_folder_are_two_claims(self) -> None:
        # Only where the library recognises one of them (see the test below for why).
        pair = fold_known([("p1", "Nadia"), ("p2", "Sarah")])
        assert people_in("Nadia and Sarah", known=pair) == ["Nadia", "Sarah"]
        assert people_in("Nadia & Sarah", known=pair) == ["Nadia", "Sarah"]
        # A joiner is not always a space. `Nadia_and_Sarah` must not yield nothing at all, which a
        # split looking only for a space would, before `reads_like_a_name` refused the whole thing.
        assert people_in("Nadia_and_Sarah", known=pair) == ["Nadia", "Sarah"]
        assert people_in("Nadia Vance") == ["Nadia Vance"]
        assert people_in("Videos") == []

    def test_a_joiner_nobody_is_named_around_splits_nothing(self) -> None:
        """`Salt and Pepper` is not two people: a split needs the library to recognise a half."""
        assert people_in("Salt and Pepper") == []
        assert people_in("Salt and Pepper", known=fold_known([("p1", "Nadia")])) == []
        assert people_in("Random Clips and GIFs") == []

    def test_two_people_written_with_no_joiner_at_all(self) -> None:
        """Two people written with no joiner are split only when the library knows both halves."""
        known = fold_known(
            [("p1", "Nadia Vance"), ("p2", "Priya Sandoval"), ("p3", "Talia Brandt")]
        )
        # Longest name first, which is the order every question here answers in.
        assert people_in("Nadia Vance Priya Sandoval", known=known) == [
            "Priya Sandoval",
            "Nadia Vance",
        ]
        # One known person and a co-star the library has never heard of is still HER folder.
        assert people_in("Talia Brandt Dorian", known=known) == ["Talia Brandt"]

    def test_a_recognised_half_beside_a_stranger_splits_the_folder(self) -> None:
        """A recognised half beside a stranger splits on the joiner, offering the stranger."""
        known = fold_known([("p1", "Nadia Vance")])

        assert people_in("Nadia Vance and Dorian Halstead", known=known) == [
            "Nadia Vance",
            "Dorian Halstead",
        ]

    def test_two_names_for_one_person_are_one_claim(self) -> None:
        """Two names for one person are one claim."""
        known = fold_known([("p1", "Linnea Ross"), ("p1", "Linnea Kastellan")])

        assert known_people_in("Linnea Ross aka Linnea Kastellan", known) == [
            ("p1", "Linnea Kastellan")
        ]

    def test_a_known_name_that_is_a_small_part_of_a_folder_does_not_claim_it(self) -> None:
        """A known name that is a small part of a folder does not claim it."""
        assert people_in(
            "Northlight Studio Trailers", known=fold_known([("p1", "Northlight")])
        ) == ["Northlight Studio Trailers"]


class TestFolding:
    def test_three_spellings_of_one_person_fold_together(self) -> None:
        assert fold("nadia_vance") == fold("Nadia.Vance") == fold("Nadia Vance")

    def test_accents_and_emoji_fold_away_for_matching(self) -> None:
        assert fold("Ren\u00e9e \U0001f496") == "renee"
        assert fold("  trailing  ") == "trailing"

    def test_a_name_is_kept_as_written_not_prettified(self) -> None:
        # The reader hands back the folded spelling; nothing here invents a capital letter.
        assert strip_noise("nerith") == "nerith"

    @pytest.mark.parametrize(
        "name",
        [
            "\u0410\u043d\u043d\u0430 \u0418\u0432\u0430\u043d\u043e\u0432\u0430",  # Cyrillic
            "\u5c71\u7530\u82b1\u5b50",  # Han
            "\uae40\ubbfc\uc9c0",  # Hangul
            "\u0395\u03bb\u03ad\u03bd\u03b7",  # Greek
            "\u05d0\u05e0\u05d4",  # Hebrew
            "\u0645\u062d\u0645\u062f",  # Arabic
            "\u0e2a\u0e21\u0e0a\u0e32\u0e22",  # Thai
            "\u0928\u092e\u0938\u094d\u0924\u0947",  # Devanagari
        ],
    )
    def test_a_name_in_any_script_is_still_a_name(self, name: str) -> None:
        """A name in any script is a name, never folded to nothing and read as junk."""
        assert fold(name)
        assert not is_stop_folder(name)
        assert classify(name) is Segment.WORD
        assert reads_like_a_name(name)
        assert read_chain(["lib", name]).name

    def test_a_mark_is_dropped_from_a_latin_letter_and_from_nobody_else(self) -> None:
        """A mark goes from a Latin letter only: elsewhere it is part of the syllable."""
        assert fold("Zo\xeb M\xfcller") == "zoe muller"
        assert fold("Jos\xe9") == fold("Jose")
        # The Devanagari vowel sign survives, so these two words stay two words.
        assert fold("\u0928\u092e\u0938\u094d\u0924\u0947") != fold(
            "\u0928\u092e\u0938\u094d\u0924"
        )

    def test_a_latin_letter_that_decomposes_into_nothing_is_spelled_out(self) -> None:
        """These are not accented forms of anything, so decomposing yields nothing and dropping
        them would silently shorten the name: `AEvar` would come out as `var`."""
        assert fold("\xc6var") == "aevar"
        assert fold("\u0141ukasz") == "lukasz"
        assert fold("\xd8yvind") == "oyvind"


class TestDates:
    @pytest.mark.parametrize(
        "value", ["2023", "2023-07", "2023-07-14", "14", "2023_07_14", "July 2023", "14.07.2023"]
    )
    def test_every_common_form(self, value: str) -> None:
        assert is_date_like(value), value

    def test_a_year_inside_a_name_is_not_the_whole_name(self) -> None:
        assert not is_date_like("Nadia Vance 2023")


class TestSites:
    def test_a_known_site_is_never_a_person(self) -> None:
        assert classify("Instagram") is Segment.SITE
        assert classify("OnlyFans") is Segment.SITE

    def test_a_site_this_library_knows_about_is_recognised_too(self) -> None:
        assert classify("Fanhouse") is Segment.WORD
        assert classify("Fanhouse", sites=["Fanhouse"]) is Segment.SITE

    def test_junk_wins_over_a_site_of_the_same_name(self) -> None:
        assert classify("Downloads", sites=["Downloads"]) is Segment.JUNK


class TestFilenames:
    def test_a_prefix_only_counts_when_every_file_carries_it(self) -> None:
        assert repeated_prefix(["QMTV - A - 1.mp4", "QMTV - B - 2.mp4"]) == "QMTV"
        assert repeated_prefix(["QMTV - A - 1.mp4", "Other - B - 2.mp4"]) == ""

    def test_one_file_is_never_a_repeated_prefix(self) -> None:
        assert repeated_prefix(["QMTV - A - 1.mp4"]) == ""

    def test_a_bare_title_claims_nobody(self) -> None:
        assert person_in_filename("Show 34.mp4", after_prefix=False) == ""

    def test_a_double_barrelled_name_is_not_cut_in_half(self) -> None:
        assert person_in_filename("Anne-Marie Halstead - Title - 1.mp4", after_prefix=False) == (
            "Anne Marie Halstead"
        )


class TestWhichFolderMadeTheClaim:
    """The depth: one person filed in several folders points at her folder, so is asked once."""

    def test_it_points_at_the_folder_the_name_came_from(self) -> None:
        assert read_chain(["Media", "Nadia Vance", "Videos"]).depth == 1
        assert read_chain(["Media", "Nadia Vance", "Pics"]).depth == 1

    def test_a_deep_chain_points_at_the_nearest_meaningful_folder(self) -> None:
        chain = ["Media", "Models", "Instagram", "harlowquin", "posts", "2023"]
        assert read_chain(chain).depth == 3

    def test_a_chain_that_names_nobody_points_at_nothing(self) -> None:
        assert read_chain(["Downloads"]).depth == -1
        assert read_chain(["Instagram"]).depth == -1
        assert read_chain(["Instagram", "send these to dave before friday"]).depth == -1

    def test_a_scene_title_does_not_beat_the_person_it_sits_under(self) -> None:
        """A scene title does not beat the person above it: the library recognises her shallower
        folder first."""
        chain = ["Collection", "Northlight", "Tamsin Vellory", "Danger Zone"]
        assert read_chain(chain).name == "Danger Zone"
        assert (
            read_chain(chain, known=fold_known([("p1", "Tamsin Vellory")])).name == "Tamsin Vellory"
        )

    def test_a_category_subdivided_by_person_still_names_the_person(self) -> None:
        """The other half of the same shape, and it must not move. Nobody in this library is called
        Family, so nothing above `Mum` is recognised and the walk stays where it was."""
        assert read_chain(["lib", "Family", "Mum"], known=fold_known([("p1", "Mum")])).name == "Mum"
        assert read_chain(["lib", "Family", "Mum"]).name == "Mum"

    def test_only_a_WHOLE_folder_name_can_take_the_claim_upwards(self) -> None:
        """Only a whole folder name can take the claim upwards to an ancestor."""
        chain = ["Collection", "Northlight Studio Collection", "Compilations", "Wet"]
        assert read_chain(chain, known=fold_known([("p1", "Northlight")])).name == "Wet"

    def test_a_library_that_knows_nobody_reads_exactly_as_it_did(self) -> None:
        """The rule only ever moves a claim onto somebody already confirmed, so an empty library is
        untouched by it, which is what makes it safe to run on every folder of every pass."""
        for chain in (
            ["Media", "Nadia Vance", "Videos"],
            ["Media", "Models", "Instagram", "harlowquin", "posts", "2023"],
            ["Collection", "Northlight", "Tamsin Vellory", "Danger Zone"],
        ):
            assert read_chain(chain, known=[]) == read_chain(chain)


# --- names a machine assembled -----------------------------------------------------------------
#
# Filenames as the popular downloaders and browser extensions write them.


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # The Instagram extensions. Two tools, two field orders, and they differ twice over: the
        # second field is a unix time in one and a post id in the other, and only one ends in a date.
        ("harlowquin_3141592653_2718281828459045235_56432312663.jpg", "harlowquin"),
        ("inesmarchcroft_1618033988_6931471805599453094_109592248.jpg", "inesmarchcroft"),
        ("odalie_frisk_4669201609102990671_409995848_2030-05-07.jpg", "odalie frisk"),
        # A username that contains the separator, which is why this peels from the END.
        ("orlafennimore__1732050807_5772156649015328606_7132547850.jpg", "orlafennimore"),
        ("lunette.vale_1259921049_2302585092994045684_45323924681.mp4", "lunette vale"),
        # A middleman service stamps its brand on the front; the person is what it fetched.
        ("ssstik.io_talibrandt_1712345678901.mov", "talibrandt"),
        # TikTok, three tools' worth of shapes.
        ("pellquorley-1414213562373095048.mp4", "pellquorley"),
        ("wendalinfrost_3162277660168379332_hd.mp4", "wendalinfrost"),
        ("harlowquin - [1202056903159594285].mp4", "harlowquin"),
        ("Brisavantle_-_2645751311064590590.mp4", "Brisavantle"),
        ("thequietmeridian_-_1324717957244746025-from-23s.mp4", "thequietmeridian"),
        # A creator site: username, the date it was posted, the post, and a counter.
        ("carrowvellum-20240803-1839286755214161132-01.mp4", "carrowvellum"),
    ],
)
def test_a_name_a_tool_assembled_is_read(filename: str, expected: str) -> None:
    assert username_in_filename(filename) == expected
    # And it reaches the caller, which is what actually files anybody.
    assert person_in_filename(filename, after_prefix=False) == expected


@pytest.mark.parametrize(
    "filename",
    [
        # Three English words joined read like a username; only "a machine made this" tells them
        # apart.
        "alarmedrustymastodon.mp4",
        "chartreusemediumblueborderterriersdl.mp4",
        # Instagram's own CDN name. All ids, no username: it is a post, never a person.
        "548494356_660806847480229_6988455643347679231_n.jpg",
        # Telegram, a phone, a screen recorder, a chat client.
        "photo_2023-07-11_17-22-58.jpg",
        "IMG_20230907_153652_120e3456789012e345d.jpg",
        "2025-05-14 21.22.22.mp4",
        "1A2B3C4D-5E6F-4A7B-8C9D-0E1F2A3B4C5D.mp4",
        "6B7C8D9E-0F1A-4B2C-9D3E-4F5A6B7C8D9E_123456-xQ7RtLN3.jpg",
        "vlcsnap-2024-02-02-22h17m26s542a103f4c5d6e7b8a9.png",
        "VideoCapture_20220427-120119.jpg",
        # Debris short enough to be meaningless.
        "ia_606646202.jpg",
        # A title somebody typed. A quality word coming off is not evidence a tool built this.
        "Some Girl HD.mp4",
        "Nadia-Vance-Summer-Beach-Holiday-Sunset-Photos-27.jpg",
    ],
)
def test_a_name_nobody_assembled_claims_nobody(filename: str) -> None:
    assert username_in_filename(filename) == ""


def test_the_hyphen_reading_still_wins_where_it_applies() -> None:
    """A file laid out with ` - ` was laid out by somebody, and that says more than a peel does."""
    assert person_in_filename("Uploader - Title - x.mp4", after_prefix=False) == "Uploader"


# --- the chain walks past a name it cannot read -------------------------------------------------


def test_a_scene_title_does_not_erase_the_creator_above_it() -> None:
    """`Creator/Scene Title/` claims the creator one folder up."""
    chain = [
        "Main Library",
        "Northlight Studio Collection",
        "Orla Fennimore",
        "My Rise in the Ranks",
    ]
    assert read_chain(chain).name == "Orla Fennimore"
    assert read_chain(chain).depth == 2


def test_the_walk_stops_rather_than_climbing_to_the_shelf() -> None:
    """The walk takes one step, never two, or a collection is filed under its shelf."""
    chain = ["Nadia Vance", "My Rise in the Ranks", "Part Two of the Thing"]
    assert read_chain(chain).name == ""
    # And one step still works, which is what makes the bound a bound rather than a refusal.
    assert read_chain(["Nadia Vance", "My Rise in the Ranks"]).name == "Nadia Vance"


@pytest.mark.parametrize(
    "folder",
    [
        "Main Library",
        "Independent Creators",
        "bookmarks-2026-06-07",
        "4k Old Project Vids That Need Sorted",
    ],
)
def test_a_shelf_label_is_not_a_person(folder: str) -> None:
    """What somebody calls a shelf, not what is on it. Each of these claimed a person."""
    assert read_chain([folder]).name == ""


def test_a_sentence_is_refused_rather_than_shortened() -> None:
    """A sentence is refused, not stripped, so the walk can step over it."""
    assert not reads_like_a_name("My Rise in the Ranks")
    assert "the" not in STOP_WORDS
    assert reads_like_a_name("Nadia Vance")


# --- the middleman services are sites, never people ---------------------------------------------


@pytest.mark.parametrize(
    "folder", ["ssstik.io", "SnapInsta.to", "StorySaver.net", "BraveDown.Com", "instasave.website"]
)
def test_a_downloader_service_is_a_site(folder: str) -> None:
    assert classify(folder) is Segment.SITE


def test_a_site_word_does_not_capture_a_person_who_starts_with_one() -> None:
    """The first label is read only where the name looks like a domain: `Mega Ashcombe` is somebody."""
    assert classify("Mega Ashcombe") is Segment.WORD


# --- the library recognising itself -------------------------------------------------------------

_KNOWN = fold_known(
    [
        ("talia", "Talia Brandt"),
        ("ines-reid", "Ines Marchcroft"),
        ("ines", "Ines"),
        ("linnea", "Linnea Ross"),
        ("linnea-short", "Linnea"),
        ("al", "Al"),
    ]
)


def test_a_known_person_is_found_inside_a_sentence() -> None:
    """A known person is found inside a sentence, by asking the library."""
    found = known_names_in("1080 Talia Brandt in Slow Motion   QHB Fashion Week  2023.mp4", _KNOWN)
    assert found == ["talia"]


def test_the_longer_name_wins_and_says_it_once() -> None:
    """`Linnea` and `Linnea Ross` are one person under two names, and both is the same claim twice."""
    assert known_names_in("Linnea Ross compilation.mp4", _KNOWN) == ["linnea"]
    assert known_names_in("ines marchcroft scene 4.mp4", _KNOWN) == ["ines-reid"]


def test_a_short_name_is_never_looked_for_inside_a_longer_text() -> None:
    """Looked for inside longer text, a single short name finds files belonging to somebody else."""
    assert known_names_in("Bo Renwick.mp4", _KNOWN) == []


def test_nothing_is_recognised_in_a_name_that_holds_nobody() -> None:
    """The negative control. A folder can hold dozens of these, and none of them is anybody."""
    assert known_names_in("alarmedrustymastodon.mp4", _KNOWN) == []
    assert known_names_in("", _KNOWN) == []


# --- the number a site knows a username by ------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # Two tools, two field orders, one rule. The username number comes after the post id.
        (
            "harlowquin_3141592653_2718281828459045235_56432312663.jpg",
            ("harlowquin", "56432312663"),
        ),
        (
            "odalie_frisk_4669201609102990671_409995848_2030-05-07.jpg",
            ("odalie frisk", "409995848"),
        ),
        (
            "inesmarchcroft_1618033988_6931471805599453094_109592248.jpg",
            ("inesmarchcroft", "109592248"),
        ),
        (
            "orlafennimore__1732050807_5772156649015328606_7132547850.jpg",
            ("orlafennimore", "7132547850"),
        ),
    ],
)
def test_the_account_number_is_read_where_a_filename_carries_one(
    filename: str, expected: tuple[str, str]
) -> None:
    assert username_and_number_in_filename(filename) == expected


def test_width_alone_cannot_tell_an_account_from_a_timestamp() -> None:
    """Width cannot tell an account from a timestamp; position can."""
    # Ten digits, and it is the username's number.
    assert username_and_number_in_filename(
        "midnight_ivy_1700000000_3500000000000000000_6967788463.mp4"
    ) == (
        "midnight ivy",
        "6967788463",
    )


@pytest.mark.parametrize(
    "filename",
    [
        # No post id before it, so the long number on the end is when it was downloaded.
        "ssstik.io_talibrandt_1712345678901.mov",
        # A post id and nothing after it.
        "pellquorley-1414213562373095048.mp4",
        "harlowquin - [1202056903159594285].mp4",
        # Numbers and no username: a username this cannot identify.
        "548494356_660806847480229_6988455643347679231_n.jpg",
        "alarmedrustymastodon.mp4",
    ],
)
def test_a_number_without_the_shape_around_it_is_not_an_account(filename: str) -> None:
    assert username_and_number_in_filename(filename) is None


def test_a_handle_that_opens_with_a_sentence_word_is_still_a_handle() -> None:
    """`the_halla_nordquist__` is somebody, and one word out of a sentence's vocabulary is how
    plenty of people write their name. It takes TWO to make a phrase, which is what a sentence
    needs."""
    assert reads_like_a_name("the_halla_nordquist__")
    assert not reads_like_a_name("My Rise in the Ranks")


def test_a_name_made_of_nothing_but_machine_parts_is_no_name() -> None:
    """A name of nothing but a site and a post id is no username."""
    assert username_in_filename("ssstik.io_1712345678901.mov") == ""


def test_a_name_that_is_only_ever_machine_parts_stops_being_whittled() -> None:
    """Peeling trailing machine parts is bounded, so nothing short is invented."""
    assert username_in_filename("instagram_1234_5678_9012_3456_7890_1234_5678_9012_3456.jpg") == ""


# --- a folder named for what it holds ------------------------------------------------------------


@pytest.mark.parametrize(
    "folder",
    ["first", "second", "2nd", "silent", "songs", "Harbour folder set", "sunset collection"],
)
def test_a_folder_named_for_what_it_holds_is_nobody(folder: str) -> None:
    """A place in somebody's sorting, the sound of the files, a shelf's theme. None is a person,
    and each left on its own reads exactly like a username."""
    assert not reads_like_a_name(folder), folder
    assert read_chain(["Library", folder]).name == "", folder


@pytest.mark.parametrize(
    "folder", ["Cassia Lynn", "Delphine Ostrow", "Elina Sorrel", "Halla Nordquist", "Bryn Calloway"]
)
def test_a_full_name_is_still_somebody_spelled_the_way_the_folder_spells_it(folder: str) -> None:
    """The proposal is the folder's own spelling; folding is for matching, never for showing.
    A shelf word beside a full name still comes off, leaving the person."""
    assert read_chain(["Library", folder]).name == folder
    assert read_chain(["Library", f"{folder} collection"]).name == folder
    assert people_in(folder) == [folder]


def test_a_name_beyond_ascii_keeps_each_word_as_the_folder_spells_it() -> None:
    """The general path walks the string: a doubled separator makes no empty word, an accented
    letter stays accented in what is shown, and a mark on its own is a word of its own."""
    assert strip_noise("Zo\u00eb, , L\u00f6we") == "Zo\u00eb L\u00f6we"
    assert strip_noise("Zo\u00eb \u0301") == "Zo\u00eb \u0301"
