# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading an MP4's index to find out how far its audio sits from its video.

The files here are built byte by byte rather than encoded, because the thing under test is the
parsing and because ffmpeg cannot produce a badly interleaved file to test against: it
re-interleaves cleanly whatever it is fed, at every setting.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from sift.kernel import mp4


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def full(kind: bytes, payload: bytes, version: int = 0) -> bytes:
    return box(kind, bytes([version]) + b"\x00\x00\x00" + payload)


def stco(offsets: list[int]) -> bytes:
    return full(
        b"stco", struct.pack(">I", len(offsets)) + b"".join(struct.pack(">I", o) for o in offsets)
    )


def co64(offsets: list[int]) -> bytes:
    return full(
        b"co64", struct.pack(">I", len(offsets)) + b"".join(struct.pack(">Q", o) for o in offsets)
    )


def stsc(runs: list[tuple[int, int]]) -> bytes:
    body = struct.pack(">I", len(runs))
    for first, per in runs:
        body += struct.pack(">III", first, per, 1)
    return full(b"stsc", body)


def stsz(sizes: list[int] | int, count: int = 0) -> bytes:
    if isinstance(sizes, int):
        return full(b"stsz", struct.pack(">II", sizes, count))
    return full(
        b"stsz", struct.pack(">II", 0, len(sizes)) + b"".join(struct.pack(">I", s) for s in sizes)
    )


def stts(runs: list[tuple[int, int]]) -> bytes:
    body = struct.pack(">I", len(runs))
    for count, delta in runs:
        body += struct.pack(">II", count, delta)
    return full(b"stts", body)


def mdhd(timescale: int, version: int = 0) -> bytes:
    if version == 1:
        return full(b"mdhd", struct.pack(">QQI", 0, 0, timescale) + b"\x00" * 8, version=1)
    return full(b"mdhd", struct.pack(">III", 0, 0, timescale) + b"\x00" * 8)


def hdlr(kind: bytes) -> bytes:
    return full(b"hdlr", b"\x00\x00\x00\x00" + kind + b"\x00" * 12)


def trak(kind: bytes, tables: bytes, timescale: int = 1000, version: int = 0) -> bytes:
    stbl = box(b"stbl", tables)
    minf = box(b"minf", stbl)
    mdia = box(b"mdia", mdhd(timescale, version) + hdlr(kind) + minf)
    return box(b"trak", mdia)


def one_per_chunk(offsets: list[int], size: int, delta: int) -> bytes:
    """A track whose every chunk holds exactly one sample."""
    return (
        stco(offsets) + stsc([(1, 1)]) + stsz([size] * len(offsets)) + stts([(len(offsets), delta)])
    )


def written(tmp_path: Path, moov: bytes, *, name: str = "clip.mp4", lead: bytes = b"") -> Path:
    """A file that parses as boxes: optional leading media, then the index."""
    path = tmp_path / name
    path.write_bytes(box(b"ftyp", b"isom" + b"\x00" * 8) + lead + box(b"moov", moov))
    return path


def interleaved(tmp_path: Path, *, apart: int) -> Path:
    """Four moments of video, with the audio for each `apart` bytes away."""
    video = one_per_chunk([1000, 2000, 3000, 4000], 100, 1000)
    audio = one_per_chunk([1000 + apart, 2000 + apart, 3000 + apart, 4000 + apart], 10, 1000)
    return written(tmp_path, trak(b"vide", video) + trak(b"soun", audio))


class TestMeasuring:
    def test_a_tightly_interleaved_file_measures_small(self, tmp_path: Path) -> None:
        assert mp4.worst_gap(interleaved(tmp_path, apart=200)) == 200

    def test_a_badly_interleaved_file_measures_large(self, tmp_path: Path) -> None:
        assert mp4.worst_gap(interleaved(tmp_path, apart=50_000_000)) == 50_000_000

    def test_the_worst_moment_is_reported_not_the_typical_one(self, tmp_path: Path) -> None:
        """Three moments together and one far away answers with the far one."""
        video = one_per_chunk([1000, 2000, 3000], 100, 1000)
        audio = one_per_chunk([1100, 2100, 9_000_000], 10, 1000)
        path = written(tmp_path, trak(b"vide", video) + trak(b"soun", audio))
        assert mp4.worst_gap(path) == 9_000_000 - 3000

    def test_a_sample_is_placed_after_the_ones_before_it_in_its_chunk(self, tmp_path: Path) -> None:
        """Two samples share a chunk, so the second sits one sample-size in."""
        video = stco([1000]) + stsc([(1, 2)]) + stsz([100, 100]) + stts([(2, 1000)])
        audio = one_per_chunk([1000, 5000], 10, 1000)
        path = written(tmp_path, trak(b"vide", video) + trak(b"soun", audio))
        # The second video sample is at 1100 and its audio is at 5000.
        assert mp4.worst_gap(path) == 5000 - 1100

    def test_which_track_is_which_comes_from_the_handler_not_the_order(
        self, tmp_path: Path
    ) -> None:
        """A file storing its audio first is measured the same as one storing it second.

        Asserting the number rather than just that the two agree: if the handler were ignored and
        both tracks read as one kind, both files would answer None and agree perfectly.
        """
        video = one_per_chunk([1000, 2000], 100, 1000)
        audio = one_per_chunk([700_000, 800_000], 10, 1000)
        audio_first = written(tmp_path, trak(b"soun", audio) + trak(b"vide", video), name="a.mp4")
        video_first = written(tmp_path, trak(b"vide", video) + trak(b"soun", audio), name="v.mp4")
        assert mp4.worst_gap(audio_first) == 800_000 - 2000
        assert mp4.worst_gap(video_first) == 800_000 - 2000

    def test_a_64_bit_offset_table_is_read(self, tmp_path: Path) -> None:
        far = 5_000_000_000
        video = co64([far]) + stsc([(1, 1)]) + stsz([100]) + stts([(1, 1000)])
        audio = co64([far + 4000]) + stsc([(1, 1)]) + stsz([10]) + stts([(1, 1000)])
        path = written(tmp_path, trak(b"vide", video) + trak(b"soun", audio))
        assert mp4.worst_gap(path) == 4000

    def test_a_uniform_sample_size_needs_no_table(self, tmp_path: Path) -> None:
        """The audio sits *before* the chunk, so the answer only comes out right if the second
        video sample was advanced by the uniform size rather than left on the chunk offset."""
        video = stco([1000]) + stsc([(1, 2)]) + stsz(100, count=2) + stts([(2, 1000)])
        audio = one_per_chunk([500], 10, 1000)
        path = written(tmp_path, trak(b"vide", video) + trak(b"soun", audio))
        assert mp4.worst_gap(path) == 1100 - 500

    def test_a_version_one_media_header_still_yields_its_timescale(self, tmp_path: Path) -> None:
        video = one_per_chunk([1000], 100, 1000)
        audio = one_per_chunk([3000], 10, 1000)
        path = written(
            tmp_path,
            trak(b"vide", video, version=1) + trak(b"soun", audio, version=1),
        )
        assert mp4.worst_gap(path) == 2000

    def test_the_index_is_found_after_the_media(self, tmp_path: Path) -> None:
        """The ordinary layout: media first, index last."""
        path = interleaved(tmp_path, apart=300)
        assert mp4.worst_gap(path) == 300

    def test_a_track_that_is_neither_audio_nor_video_is_ignored(self, tmp_path: Path) -> None:
        video = one_per_chunk([1000], 100, 1000)
        audio = one_per_chunk([2000], 10, 1000)
        data = one_per_chunk([9_000_000], 10, 1000)
        path = written(
            tmp_path,
            trak(b"vide", video) + trak(b"soun", audio) + trak(b"tmcd", data),
        )
        assert mp4.worst_gap(path) == 1000


class TestWhatCannotBeMeasured:
    """None means unknown. It must never be confused with zero, which means perfect."""

    def test_a_file_with_no_index(self, tmp_path: Path) -> None:
        path = tmp_path / "no-index.mp4"
        path.write_bytes(box(b"ftyp", b"isom" + b"\x00" * 8) + box(b"mdat", b"\x00" * 64))
        assert mp4.worst_gap(path) is None

    def test_an_index_larger_than_the_reader_will_hold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mp4, "MAX_INDEX_BYTES", 8)
        assert mp4.worst_gap(interleaved(tmp_path, apart=200)) is None

    def test_a_silent_video_is_answered_not_left_unknown(self, tmp_path: Path) -> None:
        """No audio track means no distance to get wrong, so it is zero rather than None: silent
        videos are ordinary files, and they must not queue up as unknown."""
        path = written(tmp_path, trak(b"vide", one_per_chunk([1000], 100, 1000)))
        assert mp4.worst_gap(path) == 0

    def test_an_audio_track_present_but_empty_stays_unknown(self, tmp_path: Path) -> None:
        """Different from a silent video: this one claims audio and yields none, so it was not read
        rather than having nothing to read."""
        empty = stco([]) + stsc([(1, 1)]) + stsz([]) + stts([])
        path = written(
            tmp_path, trak(b"vide", one_per_chunk([1000], 100, 1000)) + trak(b"soun", empty)
        )
        assert mp4.worst_gap(path) is None

    def test_a_file_with_no_video(self, tmp_path: Path) -> None:
        path = written(tmp_path, trak(b"soun", one_per_chunk([1000], 10, 1000)))
        assert mp4.worst_gap(path) is None

    def test_a_track_whose_tables_yield_no_samples(self, tmp_path: Path) -> None:
        """Zero samples is not a gap of zero: that is the answer meaning perfectly interleaved."""
        empty = stco([]) + stsc([(1, 1)]) + stsz([]) + stts([])
        path = written(
            tmp_path, trak(b"vide", empty) + trak(b"soun", one_per_chunk([9000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_a_box_that_runs_past_the_end(self, tmp_path: Path) -> None:
        path = tmp_path / "short.mp4"
        path.write_bytes(struct.pack(">I", 9999) + b"moov" + b"\x00" * 16)
        assert mp4.worst_gap(path) is None

    def test_a_chunk_table_claiming_more_than_it_holds(self, tmp_path: Path) -> None:
        lying = full(b"stco", struct.pack(">I", 400) + struct.pack(">I", 1000))
        video = lying + stsc([(1, 1)]) + stsz([100]) + stts([(1, 1000)])
        path = written(
            tmp_path, trak(b"vide", video) + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_a_track_with_a_zero_timescale(self, tmp_path: Path) -> None:
        video = one_per_chunk([1000], 100, 1000)
        audio = one_per_chunk([2000], 10, 1000)
        path = written(tmp_path, trak(b"vide", video, timescale=0) + trak(b"soun", audio))
        assert mp4.worst_gap(path) is None

    def test_a_sample_to_chunk_table_not_starting_at_the_first_chunk(self, tmp_path: Path) -> None:
        video = stco([1000]) + stsc([(2, 1)]) + stsz([100]) + stts([(1, 1000)])
        path = written(
            tmp_path, trak(b"vide", video) + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_a_track_missing_a_table_it_needs(self, tmp_path: Path) -> None:
        video = stco([1000]) + stsc([(1, 1)]) + stts([(1, 1000)])  # no stsz
        path = written(
            tmp_path, trak(b"vide", video) + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_a_file_that_is_not_there(self, tmp_path: Path) -> None:
        assert mp4.worst_gap(tmp_path / "absent.mp4") is None


class TestBoxWalking:
    def test_a_64_bit_box_header_is_understood(self) -> None:
        blob = struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 24) + b"\x00" * 8
        found = list(mp4.boxes(blob, 0, len(blob)))
        assert [(b.kind, b.size, b.header) for b in found] == [(b"mdat", 24, 16)]

    def test_a_zero_size_box_runs_to_the_end(self) -> None:
        blob = struct.pack(">I", 0) + b"mdat" + b"\x00" * 12
        found = list(mp4.boxes(blob, 0, len(blob)))
        assert [(b.kind, b.size) for b in found] == [(b"mdat", 20)]

    def test_a_box_smaller_than_its_own_header_is_rejected(self) -> None:
        blob = struct.pack(">I", 4) + b"mdat" + b"\x00" * 8
        with pytest.raises(mp4.Malformed):
            list(mp4.boxes(blob, 0, len(blob)))

    def test_find_returns_nothing_when_the_box_is_absent(self) -> None:
        blob = box(b"ftyp", b"isom")
        assert mp4.find(blob, 0, len(blob), b"moov") is None


class TestEveryRefusal:
    """One case per guard. A parse that gives up quietly is only safe if it gives up for the reason
    it claims, and these are the lines a malformed file reaches."""

    def _unmeasurable(self, tmp_path: Path, video: bytes, name: str = "clip.mp4") -> None:
        path = written(
            tmp_path,
            trak(b"vide", video) + trak(b"soun", one_per_chunk([2000], 10, 1000)),
            name=name,
        )
        assert mp4.worst_gap(path) is None

    def test_a_header_that_does_not_fit_in_what_is_left(self) -> None:
        with pytest.raises(mp4.Malformed):
            list(mp4.boxes(b"\x00\x00", 0, 2))

    def test_a_64_bit_header_that_does_not_fit(self) -> None:
        with pytest.raises(mp4.Malformed):
            list(mp4.boxes(struct.pack(">I", 1) + b"mdat" + b"\x00\x00", 0, 10))

    def test_a_truncated_top_level_header(self, tmp_path: Path) -> None:
        path = tmp_path / "stub.mp4"
        path.write_bytes(b"\x00\x00\x00")
        assert mp4.worst_gap(path) is None

    def test_an_index_shorter_than_it_claims(self, tmp_path: Path) -> None:
        """The header says the index is long; the file stops before it ends."""
        path = tmp_path / "cut.mp4"
        path.write_bytes(box(b"ftyp", b"isom") + struct.pack(">I", 4096) + b"moov" + b"\x00" * 32)
        assert mp4.worst_gap(path) is None

    def test_a_table_with_no_room_for_a_count(self, tmp_path: Path) -> None:
        self._unmeasurable(
            tmp_path,
            box(b"stco", b"\x00\x00\x00\x00") + stsc([(1, 1)]) + stsz([1]) + stts([(1, 1)]),
        )

    def test_a_track_with_no_chunk_offsets(self, tmp_path: Path) -> None:
        self._unmeasurable(tmp_path, stsc([(1, 1)]) + stsz([100]) + stts([(1, 1000)]))

    def test_a_track_with_no_sample_to_chunk_table(self, tmp_path: Path) -> None:
        self._unmeasurable(tmp_path, stco([1000]) + stsz([100]) + stts([(1, 1000)]))

    def test_a_track_with_no_time_to_sample_table(self, tmp_path: Path) -> None:
        self._unmeasurable(tmp_path, stco([1000]) + stsc([(1, 1)]) + stsz([100]))

    def test_a_sample_size_table_too_short_to_read(self, tmp_path: Path) -> None:
        self._unmeasurable(
            tmp_path, stco([1000]) + stsc([(1, 1)]) + box(b"stsz", b"\x00" * 4) + stts([(1, 1000)])
        )

    def test_a_sample_size_table_claiming_more_than_it_holds(self, tmp_path: Path) -> None:
        lying = full(b"stsz", struct.pack(">II", 0, 500) + struct.pack(">I", 10))
        self._unmeasurable(tmp_path, stco([1000]) + stsc([(1, 1)]) + lying + stts([(1, 1000)]))

    def test_a_chunk_wanting_more_samples_than_the_tables_hold(self, tmp_path: Path) -> None:
        """`stsc` promises three samples in the chunk and only one size exists, so the walk stops
        early rather than inventing positions, and one sample is still measured."""
        video = stco([1000]) + stsc([(1, 3)]) + stsz([100]) + stts([(1, 1000)])
        path = written(
            tmp_path, trak(b"vide", video) + trak(b"soun", one_per_chunk([9000], 10, 1000))
        )
        assert mp4.worst_gap(path) == 8000

    def test_a_track_with_no_media_header(self, tmp_path: Path) -> None:
        headless = box(
            b"mdia", hdlr(b"vide") + box(b"minf", box(b"stbl", one_per_chunk([1000], 100, 1000)))
        )
        path = written(
            tmp_path, box(b"trak", headless) + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_a_media_header_too_short_for_a_timescale(self, tmp_path: Path) -> None:
        stub = box(
            b"mdia",
            full(b"mdhd", b"\x00\x00\x00\x00")
            + hdlr(b"vide")
            + box(b"minf", box(b"stbl", one_per_chunk([1000], 100, 1000))),
        )
        path = written(
            tmp_path, box(b"trak", stub) + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_a_track_with_no_handler(self, tmp_path: Path) -> None:
        nameless = box(
            b"mdia", mdhd(1000) + box(b"minf", box(b"stbl", one_per_chunk([1000], 100, 1000)))
        )
        path = written(
            tmp_path, box(b"trak", nameless) + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_a_handler_too_short_to_name_a_type(self, tmp_path: Path) -> None:
        stub = box(
            b"mdia",
            mdhd(1000)
            + full(b"hdlr", b"\x00\x00\x00\x00")
            + box(b"minf", box(b"stbl", one_per_chunk([1000], 100, 1000))),
        )
        path = written(
            tmp_path, box(b"trak", stub) + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_a_box_inside_the_index_that_is_not_a_track(self, tmp_path: Path) -> None:
        """`mvhd` sits beside the tracks and is walked past, not into."""
        moov = (
            box(b"mvhd", b"\x00" * 16)
            + trak(b"vide", one_per_chunk([1000], 100, 1000))
            + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(written(tmp_path, moov)) == 1000

    def test_a_track_with_no_media_box_at_all(self, tmp_path: Path) -> None:
        moov = (
            box(b"trak", box(b"tkhd", b"\x00" * 16))
            + trak(b"vide", one_per_chunk([1000], 100, 1000))
            + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(written(tmp_path, moov)) == 1000

    def test_a_track_with_no_sample_table(self, tmp_path: Path) -> None:
        gutted = box(b"mdia", mdhd(1000) + hdlr(b"vide") + box(b"minf", b""))
        path = written(
            tmp_path, box(b"trak", gutted) + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(path) is None

    def test_one_empty_video_track_beside_a_measurable_one(self, tmp_path: Path) -> None:
        """The empty one is skipped; the file is still measured by the other."""
        empty = stco([]) + stsc([(1, 1)]) + stsz([]) + stts([])
        moov = (
            trak(b"vide", empty)
            + trak(b"vide", one_per_chunk([1000], 100, 1000))
            + trak(b"soun", one_per_chunk([2000], 10, 1000))
        )
        assert mp4.worst_gap(written(tmp_path, moov)) == 1000

    def test_one_empty_audio_track_beside_a_measurable_one(self, tmp_path: Path) -> None:
        """The empty one is skipped on the audio side too, not just the video side.

        The video sits well away from the start of the file on purpose: a skipped track that was
        instead treated as sitting at offset zero would answer 5000 here, not 1000.
        """
        empty = stco([]) + stsc([(1, 1)]) + stsz([]) + stts([])
        moov = (
            trak(b"soun", empty)
            + trak(b"vide", one_per_chunk([5000], 100, 1000))
            + trak(b"soun", one_per_chunk([6000], 10, 1000))
        )
        assert mp4.worst_gap(written(tmp_path, moov)) == 1000

    def test_an_index_that_stops_before_the_file_says_it_should(self, tmp_path: Path) -> None:
        """Told the file is bigger than it is, the index header fits inside that claim but the
        bytes behind it stop early, which is what a file being written or truncated under a scan
        looks like."""
        path = tmp_path / "shrinking.mp4"
        path.write_bytes(box(b"ftyp", b"isom") + struct.pack(">I", 4096) + b"moov" + b"\x00" * 32)
        with pytest.raises(mp4.Malformed):
            mp4.index_box(path, 8192)

    def test_a_chunk_wanting_more_uniform_samples_than_there_are(self, tmp_path: Path) -> None:
        """`stsz` says two samples of one size; `stsc` asks the chunk for four. The walk stops when
        the sizes run out rather than repeating the last one forever."""
        video = stco([1000]) + stsc([(1, 4)]) + stsz(100, count=2) + stts([(4, 1000)])
        path = written(
            tmp_path, trak(b"vide", video) + trak(b"soun", one_per_chunk([9000], 10, 1000))
        )
        assert mp4.worst_gap(path) == 8000


class TestTheGuardsRefuseRatherThanDrift:
    """Two guards below produce the same `None` from `worst_gap` whether they are there or not,
    because a later step fails anyway. Tested through `worst_gap` they prove nothing: removing
    both would leave the suite green. These reach the parse directly, where the
    difference between refusing and quietly measuring nothing is visible."""

    def walk(self, moov: bytes) -> list[tuple[int, float]]:
        found = mp4.tracks(
            box(b"moov", moov), mp4.Box(kind=b"moov", start=0, size=len(moov) + 8, header=8)
        )
        return [sample for track in found for sample in track.samples()]

    def test_a_sample_to_chunk_table_starting_past_the_first_chunk_is_refused(self) -> None:
        """Without the guard this yields no samples and looks like an empty track."""
        video = stco([1000]) + stsc([(2, 1)]) + stsz([100]) + stts([(1, 1000)])
        with pytest.raises(mp4.Malformed):
            self.walk(trak(b"vide", video))

    def test_a_chunk_table_claiming_more_than_it_holds_is_refused(self) -> None:
        """Without the guard `struct` raises instead, which is not this module's error to give."""
        lying = full(b"stco", struct.pack(">I", 400) + struct.pack(">I", 1000))
        video = lying + stsc([(1, 1)]) + stsz([100]) + stts([(1, 1000)])
        with pytest.raises(mp4.Malformed):
            self.walk(trak(b"vide", video))

    def test_a_sample_size_table_claiming_more_than_it_holds_is_refused(self) -> None:
        lying = full(b"stsz", struct.pack(">II", 0, 500) + struct.pack(">I", 10))
        video = stco([1000]) + stsc([(1, 1)]) + lying + stts([(1, 1000)])
        with pytest.raises(mp4.Malformed):
            self.walk(trak(b"vide", video))
