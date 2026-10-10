# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the watermark reader decides and refuses to decide; the models are not run here."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.slices.watermarks import frames, read, signatures

pytestmark = pytest.mark.anyio


# --- the matcher ----------------------------------------------------------------------------


def test_an_exact_address_and_the_handle_beside_it_are_read() -> None:
    found = signatures.mark_in("visit onlyfans.com/riverbend today")
    assert found is not None
    assert (found.site, found.host, found.username) == ("OnlyFans", "onlyfans.com", "riverbend")
    assert found.exact
    assert found.text == "onlyfans.com/riverbend"


def test_the_second_site_is_read_as_itself() -> None:
    found = signatures.mark_in("fansly.com/riverbend")
    assert found is not None
    assert (found.site, found.username, found.exact) == ("Fansly", "riverbend", True)


def test_a_letter_misread_is_still_found_and_still_decides_nothing() -> None:
    """A letter misread is still found and decides nothing: found is not certain."""
    found = signatures.mark_in("oniyfans.com/riverbend")
    assert found is not None
    assert found.username == "riverbend"
    assert not found.exact


def test_a_cut_off_address_is_read_as_the_site_it_is_a_tail_of() -> None:
    """A cut-off `fans.com/` is the tail of `onlyfans.com`, matched exactly rather than by edits,
    which would put it nearer `fansly.com`."""
    found = signatures.mark_in("fans.com/riverbend")
    assert found is not None
    assert found.site == "OnlyFans"
    assert found.truncated
    assert not found.exact


def test_a_cut_off_address_wins_whichever_order_the_addresses_are_listed_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tail and the two-edit reading are equally far, so it is the rank that prefers the tail,
    never the accident of which address is looked for first."""
    monkeypatch.setattr(signatures, "SIGNATURES", tuple(reversed(signatures.SIGNATURES)))
    found = signatures.mark_in("fans.com/riverbend")
    assert found is not None
    assert (found.site, found.truncated) == ("OnlyFans", True)


def test_a_handle_stops_where_the_line_had_a_gap() -> None:
    """Gaps are closed to match the address and remembered to cut the username."""
    found = signatures.mark_in("visit only fans .com/riverbend today")
    assert found is not None
    assert found.exact and found.username == "riverbend"


def test_a_bare_tail_with_no_handle_beside_it_is_not_a_reading_of_anything() -> None:
    """A fragment in a caption is evidence of nothing, so the tail rule needs the username."""
    found = signatures.mark_in("more fans.com stuff")
    assert found is None or found.site != "OnlyFans" or not found.truncated


def test_something_that_is_not_an_address_is_not_read() -> None:
    assert signatures.mark_in("recorded live at the beach house") is None


def test_the_best_reading_across_a_frame_wins_and_prefers_one_with_a_handle() -> None:
    best = signatures.best_mark(
        ["subscribe now", "oniyfans.com", "onlyfans.com/riverbend", "00:12:44"]
    )
    assert best is not None
    assert best.exact and best.username == "riverbend"


# --- the username rule ----------------------------------------------------------------------


def test_a_handle_that_already_names_an_account_on_that_site_is_attributed() -> None:
    assert signatures.nearest_username("riverbend", ["riverbend", "othername"]) == "riverbend"


def test_one_letter_out_is_attributed_when_the_handle_is_long_enough() -> None:
    assert signatures.nearest_username("riverbend", ["riverbcnd"]) == "riverbcnd"


def test_a_short_handle_is_attributed_only_on_an_exact_match() -> None:
    """Readings of six characters or fewer are usually a crop cutting the mark in half rather than
    short names. One edit on a five-letter word reaches a great many real words."""
    assert signatures.nearest_username("riverb", ["riverc"]) is None
    assert signatures.nearest_username("riverb", ["riverb"]) == "riverb"


def test_two_accounts_one_edit_away_attribute_neither() -> None:
    """A coin toss is not an attribution, and the recorded reading is the better answer."""
    assert signatures.nearest_username("riverbend", ["riverbcnd", "riverbend1"]) is None


def test_a_handle_nothing_on_the_site_answers_to_is_not_attributed() -> None:
    assert signatures.nearest_username("riverbend", ["seagrasscove"]) is None


# --- finding the text -----------------------------------------------------------------------


def test_a_solid_line_becomes_one_strip_and_a_scattering_of_specks_does_not() -> None:
    """The confidence bar is averaged over the whole rectangle, which is what tells them apart."""
    answer = np.zeros((64, 200), np.float32)
    answer[20:30, 40:160] = 0.9
    found = read.strips(answer)
    assert len(found) == 1
    left, top, right, bottom = found[0]
    assert left < 40 and top < 20 and right > 160 and bottom > 30

    # Thin columns of confident ink down the whole height of a line: tall enough to be a line and
    # close enough together to join into one rectangle, so only the average turns it away.
    specks = np.zeros((64, 200), np.float32)
    specks[20:30, 40:160:7] = 0.9
    assert read.strips(specks) == []


def test_two_lines_with_a_gap_between_them_are_two_strips() -> None:
    answer = np.zeros((80, 200), np.float32)
    answer[10:20, 40:160] = 0.9
    answer[50:60, 40:160] = 0.9
    assert len(read.strips(answer)) == 2


def test_words_of_one_line_are_joined_and_a_second_mark_across_the_frame_is_not() -> None:
    answer = np.zeros((64, 400), np.float32)
    answer[20:30, 10:60] = 0.9
    answer[20:30, 66:120] = 0.9
    answer[20:30, 300:360] = 0.9
    found = read.strips(answer)
    assert len(found) == 2


def test_two_rows_of_ink_are_not_a_line_of_text() -> None:
    answer = np.zeros((64, 200), np.float32)
    answer[20:22, 40:160] = 0.9
    assert read.strips(answer) == []


def test_the_reader_is_given_a_grown_strip_because_the_finder_answers_with_a_shrunk_one() -> None:
    answer = np.zeros((64, 200), np.float32)
    answer[20:30, 40:160] = 0.9
    left, top, right, bottom = read.strips(answer)[0]
    assert (bottom - top) > 10
    assert (right - left) > 120


# --- reading it back ------------------------------------------------------------------------


def test_a_run_of_one_symbol_is_one_letter_and_the_separator_keeps_a_double_apart() -> None:
    """The one thing about the decoder that is not obvious: dropping the separator before the
    repeats are collapsed turns every genuine double letter into a single one."""
    blank, a, b = 0, 1 + read.ALPHABET.index("a"), 1 + read.ALPHABET.index("b")
    scores = np.zeros((1, 6, len(read.SYMBOLS)), np.float32)
    for at, symbol in enumerate([a, a, a, blank, a, b]):
        scores[0, at, symbol] = 1.0
    assert read.decode(scores)[0][0] == "aab"


def test_the_alphabet_is_the_publishers_order_and_covers_the_models_outputs() -> None:
    assert len(read.ALPHABET) == 95
    assert read.SYMBOLS[0] == ""
    assert len(read.SYMBOLS) == 97
    assert read.ALPHABET.startswith("0123456789")


# --- the crops --------------------------------------------------------------------------------


def test_the_two_crops_are_the_bottom_band_and_the_bottom_right_corner() -> None:
    graph = frames.filtergraph(1920, 1080)
    assert "split=2" in graph and "vstack=inputs=2" in graph
    # The band runs the whole width; the corner starts three fifths across.
    assert "crop=1920:130:0:950" in graph
    assert ":1152:864" in graph


def test_a_crop_is_never_reduced_below_its_own_size_and_never_enlarged_past_use() -> None:
    """Twice the finder's own longest side is where enlarging stops buying anything, and a frame
    already wider than that keeps every real pixel of its letters for the reader."""
    # Already wide enough: the band is passed at its own size, not enlarged past use.
    assert "scale=1920:130" in frames.filtergraph(1920, 1080)
    # Narrow: doubled, which is the one case enlarging buys the finder anything.
    assert "scale=960:128" in frames.filtergraph(480, 540)
    # Wider than twice the finder's longest side: passed at its own size rather than reduced.
    assert "scale=3840:260" in frames.filtergraph(3840, 2160)


def test_the_stacked_picture_is_cut_back_into_the_crops_it_was_made_of() -> None:
    across, down = frames.stacked_size(1920, 1080)
    raw = bytes(across * down * 3)
    pieces = frames.unstack(raw, 1920, 1080)
    assert [piece.name for piece in pieces] == ["band", "corner"]
    assert sum(piece.pixels.shape[0] for piece in pieces) == down
    assert pieces[0].pixels.shape[1] == across


def test_a_short_read_is_nothing_rather_than_half_a_picture() -> None:
    """What a decoder killed part way through writing leaves. Read as a crop with no text in it,
    it would mark the file as looked at and carrying no mark."""
    assert frames.unstack(b"\x00" * 30, 1920, 1080) == []


def test_a_still_is_read_from_the_beginning_and_a_video_a_quarter_of_the_way_in() -> None:
    assert frames.moment_of("image", None).seek == ()
    assert frames.moment_of("gif", 4000).seek == ()
    assert frames.moment_of("video", 40000).seek[0] == "-ss"


async def test_a_files_frame_is_read_through_its_storages_lane(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file's frame is read through its storage lane, so the share's reader cap holds."""
    held: list[Path] = []

    @asynccontextmanager
    async def watch(path: Path) -> AsyncIterator[None]:
        held.append(path)
        yield

    async def nothing(argv: list[str], **rest: object) -> bytes:
        return b""

    monkeypatch.setattr(lanes, "reading", watch)
    monkeypatch.setattr(frames, "subprocess_capture", nothing)
    target = tmp_path / "a.mp4"

    await frames.read(
        target,
        media_type="video",
        width=1920,
        height=1080,
        duration_ms=40000,
        settings=settings,
    )

    assert held == [target]


async def test_a_frame_a_tasks_one_decode_prepared_is_not_read_again(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ask a task's one decode answers is the same the read makes, so the crops come from it
    and nothing is launched; a refusal of the bytes reaches the service as a decoder's refusal."""
    from sift.kernel import media
    from sift.kernel.subprocess import ToolFailed

    async def launched(argv: list[str], **rest: object) -> bytes:
        raise AssertionError("a prepared frame was read again")

    monkeypatch.setattr(frames, "subprocess_capture", launched)
    target = Path("still.jpg")
    (asked,) = await frames.frame_requests(
        media.FileFacts(
            asset_id="a",
            path=target,
            media_type="image",
            duration_ms=0,
            width=640,
            height=480,
            fps=0.0,
            size_bytes=1,
        )
    )
    assert isinstance(asked, media.RawFrames)
    ready = media.PreparedFrames()
    ready.put_raw(target, asked, [bytes(asked.frame_bytes)])
    with media.prepared(ready):
        pieces = await frames.read(
            target, media_type="image", width=640, height=480, duration_ms=None, settings=settings
        )
    assert [piece.name for piece in pieces] == ["band", "corner"]

    async def refused(argv: list[str], **rest: object) -> bytes:
        raise ToolFailed("ffmpeg failed: Decode error rate 1", returncode=69, said="")

    monkeypatch.setattr(frames, "subprocess_capture", refused)
    with pytest.raises(media.FFmpegError, match="Decode error rate"):
        await frames.read(
            target, media_type="image", width=640, height=480, duration_ms=None, settings=settings
        )


async def test_a_file_without_a_size_or_a_picture_plans_nothing() -> None:
    from sift.kernel import media

    for kind, width in (("gif", 640), ("image", 0)):
        facts = media.FileFacts(
            asset_id="a",
            path=Path("x"),
            media_type=kind,
            duration_ms=0,
            width=width,
            height=480,
            fps=0.0,
            size_bytes=1,
        )
        assert await frames.frame_requests(facts) == []


# --- the three kinds that name no site in their letters ----------------------------------------


def test_a_distributors_band_carries_the_site_its_distributor_re_hosts() -> None:
    """A distributor's band carries the site its distributor re-hosts, and no username."""
    found = signatures.mark_in("DMCA PROTECTED CONTENT")
    assert found is not None
    assert (found.kind, found.site, found.username) == (signatures.NOTICE, "OnlyFans", None)
    assert found.exact and found.text == "DMCA PROTECTED CONTENT"


def test_the_site_a_band_means_is_read_out_of_the_table_and_not_hard_wired() -> None:
    """The site a band means is a column of `NOTICES`."""
    written, _, site = signatures.NOTICES[0]
    found = signatures.mark_in(written)
    assert found is not None and found.site == site


def test_a_band_writes_no_tag_at_all() -> None:
    """A tag would record a fact nothing could act on, and the fact is actionable. `TAG_OF` is what
    says which kinds tag, and a notice is not one of them."""
    assert signatures.NOTICE not in signatures.TAG_OF
    assert signatures.tags_in(["DMCA PROTECTED CONTENT"]) == []


def test_the_band_is_read_however_the_recogniser_spaced_it() -> None:
    """The two crops of one file can read the same band as `DMCAPROTECTEDCONTENT` and as
    `DMCA PROTECTEDCONTENT`. Tidying closes both up to one string, which is why the phrase is
    written down twice: once for matching, once for reading."""
    for line in ("DMCAPROTECTEDCONTENT", "DMCA PROTECTEDCONTENT", "dmca protected content"):
        found = signatures.mark_in(line)
        assert found is not None and found.kind == signatures.NOTICE


def test_a_band_a_letter_out_is_not_a_band() -> None:
    """No distance rule here, and the reason is the asymmetry the file argues: an address a letter
    out is still that address, and a twenty-character phrase a letter out is a phrase nobody
    wrote."""
    assert signatures.mark_in("DMCA PROTECTFD CONTENT") is None


def test_a_telegram_channel_is_read_as_a_channel_and_names_no_site() -> None:
    found = signatures.mark_in("t.me/quietharbour")
    assert found is not None
    assert (found.kind, found.site, found.username) == (
        signatures.CHANNEL,
        None,
        "quietharbour",
    )


def test_a_channel_at_the_end_of_a_caption_is_still_read() -> None:
    """The gap the tidying closed is what says the mark starts there. `visit t.me/x` becomes
    `visitt.me/x`, where the character in front of the address belongs to the word before it."""
    found = signatures.mark_in("join us visit t.me/quietharbour")
    assert found is not None and found.username == "quietharbour"
    found = signatures.mark_in("https://t.me/quietharbour")
    assert found is not None and found.username == "quietharbour"


def test_three_letters_inside_another_word_are_not_a_channel() -> None:
    """`t.me` is short enough to land inside ordinary words once the gaps are closed up, which is
    what the context test in front of it exists for."""
    assert signatures.mark_in("chat.me/quietharbour") is None


def test_a_channel_with_no_name_after_it_is_not_a_reading_of_anything() -> None:
    assert signatures.mark_in("find us on t.me today") is None


def test_a_bare_handle_is_read_as_an_account_and_says_nothing_about_a_site() -> None:
    found = signatures.mark_in("@quietharbour")
    assert found is not None
    assert (found.kind, found.site, found.username) == (
        signatures.USERNAME,
        None,
        "quietharbour",
    )
    assert found.text == "@quietharbour"


def test_a_handle_written_after_an_address_stays_that_addresss_handle() -> None:
    """The `@` is dropped by the tidying, which is the only reason `onlyfans.com/@name` reads
    correctly today. This kind gets its own look at the untidied line so that keeps working."""
    found = signatures.mark_in("onlyfans.com/@quietharbour")
    assert found is not None
    assert found.kind == signatures.SITE and found.username == "quietharbour"


def test_an_address_anywhere_on_the_frame_outranks_a_bare_handle() -> None:
    """Being alone is the whole of what makes a bare username evidence, and the ranking is what
    enforces it: a frame with an address and a username elsewhere is that address's file."""
    best = signatures.best_mark(["@somebodyelse", "onlyfans.com/quietharbour"])
    assert best is not None
    assert best.kind == signatures.SITE and best.username == "quietharbour"


def test_an_address_outranks_a_band_even_when_the_address_was_read_badly() -> None:
    """An address read off the copy outranks a band, even read badly."""
    best = signatures.best_mark(["DMCA PROTECTED CONTENT", "oniyfans.com/quietharbour"])
    assert best is not None
    assert best.kind == signatures.SITE and not best.exact
    assert signatures.tags_in(["DMCA PROTECTED CONTENT", "oniyfans.com/quietharbour"]) == []


def test_the_tags_a_frame_asks_for_are_read_separately_from_the_one_reading_kept() -> None:
    """A tag is a fact about the copy standing beside the filing rather than a rival claim, so a
    channel on a frame whose best reading is a band still puts its own tag on. Only the channel:
    the band is the reading kept against the file and files it, and asking for it here as well
    would be asking a second time for something already decided."""
    assert signatures.tags_in(["t.me/quietharbour", "DMCAPROTECTEDCONTENT"]) == ["Telegram mirror"]
    assert signatures.tags_in(["onlyfans.com/quietharbour"]) == []


def test_a_name_after_an_address_too_short_to_be_a_username_is_no_username() -> None:
    """Two letters after the slash are not a username: the address is still read, and it files
    under the site alone rather than attributing a name the mark cannot have carried."""
    found = signatures.mark_in("onlyfans.com/ab")
    assert found is not None
    assert (found.site, found.username) == ("OnlyFans", None)


def test_a_bare_username_that_is_mostly_dots_names_nobody() -> None:
    """The dots at either end are punctuation the recogniser picked up, not part of the name; what
    is left is too short to name anybody, so nothing is read."""
    assert signatures.mark_in("@..ab") is None
    assert signatures.mark_in("@..abc") is not None


# --- preparing a crop for the finder --------------------------------------------------------


def test_a_crop_is_handed_to_the_finder_no_larger_than_it_is() -> None:
    """Enlarging gives the model nothing it did not have and costs by area, so a small crop keeps
    its size (rounded to the finder's step) and only a large one is brought down to the long side."""
    assert read.detect_size(100, 40) == (96, 32)
    assert read.detect_size(10, 10) == (read.DETECT_STEP, read.DETECT_STEP)
    wide, tall = read.detect_size(3840, 260)
    assert wide == read.DETECT_LONG_SIDE
    assert tall % read.DETECT_STEP == 0 and tall < 260


def test_each_colour_is_spread_on_its_own_and_a_flat_one_is_left_alone() -> None:
    """A white mark over a warm frame is a few percent of one channel and most of another, so each
    channel is stretched over its own range. A channel with no range to speak of is left exactly as
    it was: stretching nothing only amplifies the noise in it."""
    crop = np.zeros((40, 40, 3), np.uint8)
    crop[:, :20, 0] = 100
    crop[:, 20:, 0] = 140
    crop[:, :, 1] = 77
    crop[:, :, 2] = np.arange(40, dtype=np.uint8)[None, :] // 10 + 50
    out = read.stretch(crop)
    assert (int(out[:, :, 0].min()), int(out[:, :, 0].max())) == (0, 255)
    assert np.array_equal(out[:, :, 1], crop[:, :, 1])
    assert np.array_equal(out[:, :, 2], crop[:, :, 2])


def test_a_picture_already_the_size_asked_for_is_handed_back_untouched() -> None:
    picture = np.full((8, 12, 3), 9, np.uint8)
    assert read.resample(picture, 12, 8) is picture


def test_a_resize_blends_between_neighbours_and_keeps_the_pictures_type() -> None:
    """Bilinear: an edge between black and white comes out as a ramp, and the ends stay the colours
    they were."""
    picture = np.zeros((4, 4, 3), np.uint8)
    picture[:, 2:] = 255
    out = read.resample(picture, 8, 2)
    assert out.shape == (2, 8, 3) and out.dtype == np.uint8
    row = out[0, :, 0].tolist()
    assert row[0] == 0 and row[-1] == 255
    assert any(0 < value < 255 for value in row)
    assert row == sorted(row)


def test_the_finder_is_given_one_centred_picture_colour_first() -> None:
    crop = np.full((64, 200, 3), 255, np.uint8)
    blob = read.for_detector(crop)
    width, height = read.detect_size(200, 64)
    assert blob.shape == (1, 3, height, width)
    assert blob.dtype == np.float32
    # White, centred and spread by the publisher's own numbers, per channel.
    assert np.allclose(blob[0, :, 0, 0], (1.0 - read._MEAN) / read._STANDARD_DEVIATION)


def test_a_line_too_narrow_to_hold_a_letter_is_not_a_strip() -> None:
    answer = np.zeros((64, 200), np.float32)
    answer[20:30, 50:52] = 0.9
    assert read.strips(answer) == []


def test_ink_the_finder_was_unsure_of_across_the_whole_line_is_not_a_strip() -> None:
    """Above the ink threshold at every pixel, below the confidence bar on average: the finder saw
    something and did not think it was text."""
    answer = np.zeros((64, 200), np.float32)
    answer[20:30, 40:160] = read.INK + 0.05
    assert read.CONFIDENCE > read.INK + 0.05
    assert read.strips(answer) == []


# --- preparing strips for the reader --------------------------------------------------------


def test_strips_are_one_batch_at_the_readers_height_padded_to_the_longest() -> None:
    """A batch is one rectangle, so every strip is brought to the height the model reads and padded
    out, black and on the right, to the widest, rounded up to the reader's step."""
    short = np.full((10, 20, 3), 200, np.uint8)
    long = np.full((10, 100, 3), 200, np.uint8)
    batch = read.for_reader([short, long])
    assert batch.shape[:3] == (2, 3, read.LINE_HEIGHT)
    assert batch.shape[3] % read.LINE_STEP == 0
    assert batch.shape[3] >= 100 * read.LINE_HEIGHT // 10
    # The short one ends where its own scaled width does, and the rest of its row is padding.
    assert float(batch[0, 0, 0, -1]) == 0.0
    assert float(batch[1, 0, 0, 0]) != 0.0


def test_the_readers_strips_arrive_with_their_colours_reversed() -> None:
    """The publisher's reader was trained on the other channel order. Handed this order it still
    answers confidently, with some letters wrong, so the reversal is asserted rather than trusted."""
    strip = np.zeros((48, 48, 3), np.uint8)
    strip[:, :, 0] = 255
    batch = read.for_reader([strip])
    assert float(batch[0, 2, 0, 0]) == 1.0
    assert float(batch[0, 0, 0, 0]) == -1.0
