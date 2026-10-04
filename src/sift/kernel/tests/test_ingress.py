# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ingress gate.

Every accepted format and every evasion has a real file in `fixtures/ingress/`, made by ffmpeg or
built to be exactly wrong: a new evasion ships with the fixture that proves it is caught.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sift.kernel import ingress
from sift.kernel.config import Settings
from sift.kernel.ingress import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MEDIA,
    NOTE_SUFFIX,
    TRANSIENT_REASONS,
    IngressRejected,
    IngressResult,
    Kind,
    Origin,
    Reason,
    detect,
    unguarded_ingress,
    verify_decodable,
    verify_ingress,
)
from sift.kernel.log import REDACTED, configure_logging, hashed
from sift.testing.tools import stand_in_tool

FIXTURES = Path(__file__).parent / "fixtures" / "ingress"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@pytest.fixture
def incoming(tmp_path: Path) -> Path:
    """A directory Sift wrote to, so a rejected file there is Sift's to move."""
    directory = tmp_path / "incoming"
    directory.mkdir()
    return directory


def copy_fixture(name: str, into: Path, *, as_name: str | None = None) -> Path:
    """Always a copy: the gate *moves* the files it rejects, which would eat the corpus."""
    destination = into / (as_name or name)
    destination.write_bytes((FIXTURES / name).read_bytes())
    return destination


@pytest.fixture(scope="module", autouse=True)
def corpus_survives() -> Iterator[None]:
    """Fail loudly if a test damaged the corpus."""
    before = {path.name: path.read_bytes() for path in FIXTURES.iterdir()}
    yield
    after = {path.name: path.read_bytes() for path in FIXTURES.iterdir()}
    assert after == before, "a test used a fixture in place; copy it instead"


# --- The allowlist


def test_the_allowlist_has_one_entry_per_type() -> None:
    names = [media.name for media in ALLOWED_MEDIA]
    assert len(names) == len(set(names))


def test_no_extension_is_claimed_by_two_families() -> None:
    """No extension is claimed by two families, or the answer would depend on iteration order. Two
    types in one family is fine: a `.webp` is a still or a GIF."""
    seen: dict[str, str] = {}
    for media in ALLOWED_MEDIA:
        for extension in media.extensions:
            assert seen.setdefault(extension, media.family) == media.family, (
                f"{extension} claimed by {seen.get(extension)} and {media.family}"
            )


def test_every_extension_has_a_leading_dot() -> None:
    # `path.suffix` returns ".mp4", so an entry written "mp4" would never match.
    assert all(extension.startswith(".") for extension in ALLOWED_EXTENSIONS)


def test_the_types_sift_promises_to_handle_are_all_there() -> None:
    by_kind = {kind: {m.name for m in ALLOWED_MEDIA if m.kind is kind} for kind in Kind}
    assert by_kind[Kind.VIDEO] == {"mp4", "mov", "mkv", "webm"}
    assert by_kind[Kind.IMAGE] == {"jpeg", "png", "webp", "heic", "avif"}
    # An animated WebP, AVIF or HEIC is a GIF: Sift's word for "it moves, it loops, it has a
    # length".
    assert by_kind[Kind.GIF] == {"gif", "webp-animated", "avif-sequence", "heic-sequence"}


# --- Accepted: real files, made by ffmpeg


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("accepted.mp4", "mp4"),
        ("accepted.mov", "mov"),
        ("accepted.mkv", "mkv"),
        ("accepted.webm", "webm"),
        ("accepted.jpg", "jpeg"),
        ("accepted.png", "png"),
        ("accepted.webp", "webp"),
        ("accepted_animated.webp", "webp-animated"),
        ("accepted.heic", "heic"),
        ("accepted.gif", "gif"),
    ],
)
def test_a_genuine_file_is_accepted(
    name: str, expected: str, incoming: Path, settings: Settings
) -> None:
    result = verify_ingress(copy_fixture(name, incoming), origin=Origin.SCAN, settings=settings)
    assert result.media.name == expected
    assert result.size > 0


def _ftyp(major: bytes, *compatible: bytes) -> bytes:
    """An ISO base-media header carrying exactly these brands: the brand arithmetic is under test,
    and ffmpeg cannot write an animated AVIF or HEIC."""
    body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    return (len(body) + 4).to_bytes(4, "big") + b"ftyp" + body + b"\x00" * 64


def test_an_avif_holding_a_SEQUENCE_is_a_GIF_and_not_a_photograph() -> None:
    """An animated AVIF is a sequence: it carries both sets of brands, so the sequence set is asked
    first, or it would be filed as a HEIC photograph with no length."""
    media = detect(_ftyp(b"avis", b"avif", b"msf1", b"iso8", b"mif1", b"miaf", b"MA1B"), b"")
    assert media is not None
    assert (media.name, media.kind) == ("avif-sequence", Kind.GIF)
    assert media.mime == "image/avif"


def test_a_HEIC_holding_a_sequence_is_the_same_answer_with_the_other_mime() -> None:
    """An animated HEIC is the same answer with its own MIME type, which decides whether a browser
    can draw it."""
    media = detect(_ftyp(b"msf1", b"mif1", b"heic", b"miaf"), b"")
    assert media is not None
    assert (media.name, media.kind) == ("heic-sequence", Kind.GIF)
    assert media.mime == "image/heic"


def test_an_avif_holding_ONE_picture_is_a_photograph() -> None:
    """A still AVIF is a photograph: AV1 brand, no sequence brand."""
    media = detect(_ftyp(b"avif", b"mif1", b"miaf"), b"")
    assert media is not None
    assert (media.name, media.kind) == ("avif", Kind.IMAGE)


def test_an_unknown_brand_is_still_an_mp4() -> None:
    """An unknown brand (`iso5`, on fragmented and HLS-derived files) is still an MP4: ISO
    base-media is identified structurally, and ffprobe decides whether it decodes."""
    head = b"\x00\x00\x00\x1cftypiso5\x00\x00\x02\x00iso5iso6hlsf" + b"\x00" * 64
    media = detect(head, b"")
    assert media is not None
    assert media.name == "mp4"


# --- Rejected: the corpus of evasions


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        # An executable wearing a video extension: MZ, not ftyp.
        ("disguised_exe.mp4", Reason.SIGNATURE_NOT_ALLOWED),
        ("disguised_html.mp4", Reason.SIGNATURE_NOT_ALLOWED),
        ("disguised_zip.png", Reason.SIGNATURE_NOT_ALLOWED),
        # A real GIF with a payload welded on after its terminator.
        ("polyglot.gif", Reason.DOES_NOT_END_WHERE_IT_SHOULD),
        # The same against PNG, whose IEND chunk says where the image ends.
        ("polyglot.png", Reason.DOES_NOT_END_WHERE_IT_SHOULD),
        ("empty.mp4", Reason.EMPTY),
        ("truncated.mp4", Reason.SIGNATURE_NOT_ALLOWED),
    ],
)
def test_a_disguised_file_is_refused(
    name: str, reason: Reason, incoming: Path, settings: Settings
) -> None:
    with pytest.raises(IngressRejected) as caught:
        verify_ingress(copy_fixture(name, incoming), origin=Origin.DOWNLOAD, settings=settings)
    assert caught.value.reason is reason


def test_a_real_image_wearing_the_wrong_extension_is_taken_as_what_it_IS(
    incoming: Path, settings: Settings
) -> None:
    """A genuine PNG named `.mp4` is imported as a PNG, not quarantined.

    `classify` has already proved it a media container Sift accepts, so the name check could only
    refuse real media wearing the wrong extension. The type is read off the result and served with
    `nosniff`, never guessed from the name.
    """
    verified = verify_ingress(
        copy_fixture("accepted_png_wearing_mp4.mp4", incoming),
        origin=Origin.DOWNLOAD,
        settings=settings,
    )

    assert verified.media.name == "png", "believed the bytes rather than the name"
    assert verified.media.mime == "image/png"


def test_a_disguised_executable_is_still_refused_whatever_it_is_named(
    incoming: Path, settings: Settings
) -> None:
    """A `.mp4` that leads with MZ is still refused, a step earlier, whatever its name."""
    with pytest.raises(IngressRejected) as caught:
        verify_ingress(
            copy_fixture("disguised_exe.mp4", incoming), origin=Origin.DOWNLOAD, settings=settings
        )

    assert caught.value.reason is Reason.SIGNATURE_NOT_ALLOWED


def test_png_bytes_named_jpg_are_a_png_and_the_log_says_the_name_disagreed(
    incoming: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """A PNG under a `.jpg` name is taken by its bytes, and the log says why its type differs."""
    configure_logging("INFO", redact_personal=True)
    named = copy_fixture("accepted.png", incoming, as_name="poster.jpg")

    verified = verify_ingress(named, origin=Origin.SCAN, settings=settings)

    assert verified.media.name == "png"
    assert verified.media.mime == "image/png"
    assert verified.path.name == "poster.jpg"
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    said = [one for one in records if one["event"] == "ingress.name_disagrees"]
    assert said, "the log never said the name and the bytes disagreed"
    assert said[-1]["file_type"] == "png"
    assert said[-1]["named"] == ".jpg"


def test_a_name_in_the_same_family_is_not_said_to_disagree(
    incoming: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """A matching name logs nothing: a line per ordinary file would bury the rest."""
    configure_logging("INFO", redact_personal=True)
    verify_ingress(copy_fixture("accepted.png", incoming), origin=Origin.SCAN, settings=settings)

    assert "ingress.name_disagrees" not in capsys.readouterr().out


@pytest.mark.parametrize("named", ["clip.mp4", "clip.mov", "photo.jpg"])
def test_noise_bytes_under_a_media_name_are_still_refused(
    named: str, incoming: Path, settings: Settings
) -> None:
    """Bytes with no signature at all are refused whatever the name."""
    noise = incoming / named
    noise.write_bytes(bytes((index * 151 + 7) % 256 for index in range(8192)))

    with pytest.raises(IngressRejected) as caught:
        verify_ingress(noise, origin=Origin.SCAN, settings=settings)

    assert caught.value.reason is Reason.SIGNATURE_NOT_ALLOWED


def test_a_web_page_named_mov_is_refused_and_called_a_web_page(
    incoming: Path, settings: Settings
) -> None:
    """A site's error page saved under a video's name is refused and labelled for what it is."""
    page = copy_fixture("disguised_html.mp4", incoming, as_name="clip.mov")

    with pytest.raises(IngressRejected) as caught:
        verify_ingress(page, origin=Origin.SCAN, settings=settings)

    assert caught.value.reason is Reason.SIGNATURE_NOT_ALLOWED
    assert caught.value.detected == "text/html"


def test_the_executable_is_identified_as_one(incoming: Path, settings: Settings) -> None:
    """The operator is told what it really was: a Windows executable."""
    with pytest.raises(IngressRejected) as caught:
        verify_ingress(
            copy_fixture("disguised_exe.mp4", incoming), origin=Origin.SCAN, settings=settings
        )
    assert caught.value.detected == "application/x-msdownload"


def test_a_motion_photo_is_still_a_photo(incoming: Path, settings: Settings) -> None:
    """A motion photo is still a photo: phones append a whole video after the JPEG end marker, so
    JPEG is exempt from the trailing-data rule."""
    photo = incoming / "motion.jpg"
    photo.write_bytes(
        (FIXTURES / "accepted.jpg").read_bytes() + (FIXTURES / "accepted.mp4").read_bytes()
    )
    result = verify_ingress(photo, origin=Origin.SCAN, settings=settings)
    assert result.media.name == "jpeg"


def test_a_file_that_cannot_be_read_is_refused(tmp_path: Path, settings: Settings) -> None:
    with pytest.raises(IngressRejected) as caught:
        verify_ingress(tmp_path / "gone.mp4", origin=Origin.SCAN, settings=settings)
    assert caught.value.reason is Reason.UNREADABLE


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "a named pipe cannot be put in a folder on Windows: its named pipes live under \\\\.\\pipe\\ and are not reachable as a path inside a media folder, so the hazard this guards against does not exist there. See kernel/paths.O_NONBLOCK."
    ),
)
def test_a_named_pipe_is_refused_without_blocking(tmp_path: Path, settings: Settings) -> None:
    """A FIFO is refused *without hanging*: opened the blocking way it waits for a writer that never
    comes, so the check runs on a thread that must be finished a moment later."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFO on this platform")
    fifo = tmp_path / "trap.mp4"
    os.mkfifo(fifo)  # type: ignore[attr-defined, unused-ignore]

    caught: list[Exception] = []

    def run() -> None:
        try:
            verify_ingress(fifo, origin=Origin.SCAN, settings=settings)
        except Exception as exc:
            caught.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive(), "verify_ingress blocked on a FIFO instead of refusing it"
    assert len(caught) == 1
    assert isinstance(caught[0], IngressRejected)
    assert caught[0].reason is Reason.UNREADABLE


def test_an_audio_container_is_not_a_video(incoming: Path, settings: Settings) -> None:
    """An `.m4a` is audio: MP4 and M4A share a container and differ only by brand."""
    audio = incoming / "song.mp4"
    audio.write_bytes(b"\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00M4A mp42" + b"\x00" * 64)
    with pytest.raises(IngressRejected) as caught:
        verify_ingress(audio, origin=Origin.DOWNLOAD, settings=settings)
    assert caught.value.reason is Reason.NO_VIDEO_STREAM


def test_the_refusal_reason_comes_from_the_code_that_refused_it(
    incoming: Path, settings: Settings
) -> None:
    """A file with PNG's first four bytes and not its next four is refused as a wrong signature:
    the reason is decided here, never inferred from a third-party matcher's answer."""
    forged = incoming / "forged.png"
    forged.write_bytes(b"\x89PNG\xff\xff\xff\xff" + b"\x00" * 64)

    with pytest.raises(IngressRejected) as caught:
        verify_ingress(forged, origin=Origin.DOWNLOAD, settings=settings)

    assert caught.value.reason is Reason.SIGNATURE_NOT_ALLOWED
    # The library still labels it.
    assert caught.value.detected == "image/png"


def test_a_file_cut_short_is_not_called_an_appended_payload(
    incoming: Path, settings: Settings
) -> None:
    """A PNG with no end marker is refused for what was observed: it does not end where it
    should."""
    cut_short = incoming / "cut_short.png"
    cut_short.write_bytes((FIXTURES / "accepted.png").read_bytes()[:-8])

    with pytest.raises(IngressRejected) as caught:
        verify_ingress(cut_short, origin=Origin.DOWNLOAD, settings=settings)

    assert caught.value.reason is Reason.DOES_NOT_END_WHERE_IT_SHOULD


def test_a_file_pretending_to_be_matroska_is_refused(incoming: Path, settings: Settings) -> None:
    """The DocType is walked to, so a file merely containing the word is not Matroska."""
    fake = incoming / "fake.mkv"
    fake.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 32 + b"matroska" + b"\x00" * 32)
    with pytest.raises(IngressRejected):
        verify_ingress(fake, origin=Origin.DOWNLOAD, settings=settings)


def test_a_malformed_ebml_length_is_refused(incoming: Path, settings: Settings) -> None:
    """An EBML length whose leading bit does not say its width is refused rather than guessed at."""
    malformed = incoming / "malformed.mkv"
    malformed.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x42\x82\x00" + b"matroska" + b"\x00" * 16)
    with pytest.raises(IngressRejected) as caught:
        verify_ingress(malformed, origin=Origin.DOWNLOAD, settings=settings)
    assert caught.value.reason is Reason.SIGNATURE_NOT_ALLOWED


# --- The extension is a claim, never evidence


def test_a_real_video_with_no_extension_is_accepted(incoming: Path, settings: Settings) -> None:
    """A downloader's temp file has no extension; the bytes decide."""
    result = verify_ingress(
        copy_fixture("accepted.mp4", incoming, as_name="tmp0001"),
        origin=Origin.DOWNLOAD,
        settings=settings,
    )
    assert result.media.name == "mp4"


def test_the_same_container_family_is_not_a_contradiction(
    incoming: Path, settings: Settings
) -> None:
    """MP4 and M4V are one family: a phone's `.mp4` detected as M4V is ordinary."""
    result = verify_ingress(
        copy_fixture("accepted.mp4", incoming, as_name="clip.m4v"),
        origin=Origin.SCAN,
        settings=settings,
    )
    assert result.media.family == "isobmff-video"


def test_the_extension_check_ignores_case(incoming: Path, settings: Settings) -> None:
    result = verify_ingress(
        copy_fixture("accepted.jpg", incoming, as_name="HOLIDAY.JPG"),
        origin=Origin.SCAN,
        settings=settings,
    )
    assert result.media.name == "jpeg"


# --- Quarantine: Sift moves its own files, and only its own


@pytest.mark.parametrize(
    "origin", [Origin.DOWNLOAD, Origin.UPLOAD, Origin.DROP, Origin.PASTE, Origin.SWAP]
)
def test_a_file_sift_wrote_is_moved_aside(
    origin: Origin, incoming: Path, settings: Settings
) -> None:
    bad = copy_fixture("disguised_exe.mp4", incoming)

    with pytest.raises(IngressRejected) as caught:
        verify_ingress(bad, origin=origin, settings=settings)

    assert not bad.exists()
    quarantined = caught.value.quarantined_to
    assert quarantined is not None
    assert quarantined.parent == settings.quarantine_dir
    assert quarantined.read_bytes().startswith(b"MZ")


@pytest.mark.parametrize("origin", [Origin.SCAN, Origin.WATCH])
def test_a_file_in_the_users_library_is_never_moved(
    origin: Origin, incoming: Path, settings: Settings
) -> None:
    """A refused file in a user's own library is not moved: that would be Sift losing their file."""
    bad = copy_fixture("disguised_exe.mp4", incoming)

    with pytest.raises(IngressRejected) as caught:
        verify_ingress(bad, origin=origin, settings=settings)

    assert bad.exists(), "a file Sift did not create must be left where the user put it"
    assert caught.value.quarantined_to is None


def test_a_note_that_cannot_be_written_still_leaves_the_file_refused(
    incoming: Path, settings: Settings
) -> None:
    """A note that cannot be written beside a moved file does not change the refusal; only the
    sentence saying why is lost."""
    bad = copy_fixture("disguised_exe.mp4", incoming)
    settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
    # A DIRECTORY where the moved file's note has to go.
    blocked = settings.quarantine_dir / f"{hashed(str(bad))}-{bad.name}{NOTE_SUFFIX}"
    blocked.mkdir()

    with pytest.raises(IngressRejected) as caught:
        verify_ingress(bad, origin=Origin.DOWNLOAD, settings=settings)

    quarantined = caught.value.quarantined_to
    assert quarantined is not None, "the file was still moved out of the way"
    assert quarantined.read_bytes().startswith(b"MZ")
    assert blocked.is_dir(), "and nothing overwrote what was in the way"


#: Hostile names Windows refuses to create (a backslash, a control character, trailing dots, a
#: path past the length limit) are skipped there per case: the filesystem itself refuses them.
NEEDS_A_FILESYSTEM_THAT_ACCEPTS_IT = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows will not create a file with this name, so there is nothing to point the gate at",
)


@pytest.mark.parametrize(
    "hostile_name",
    [
        # A path separator on Windows, legal in a POSIX name.
        pytest.param(
            r"..\..\windows\system32\evil",
            id="windows_separators",
            marks=NEEDS_A_FILESYSTEM_THAT_ACCEPTS_IT,
        ),
        # A leading dot hides the file from the operator.
        pytest.param("...hidden", id="leading_dots"),
        # Control characters, in a name a human will handle in a terminal.
        pytest.param(
            "clip\n\r\x1b[31mred",
            id="control_characters",
            marks=NEEDS_A_FILESYSTEM_THAT_ACCEPTS_IT,
        ),
        # The filesystem caps a name at 255 bytes.
        pytest.param("A" * 200 + ".mp4", id="very_long", marks=NEEDS_A_FILESYSTEM_THAT_ACCEPTS_IT),
        pytest.param("...", id="nothing_left", marks=NEEDS_A_FILESYSTEM_THAT_ACCEPTS_IT),
    ],
)
def test_a_hostile_filename_cannot_shape_the_quarantine_path(
    hostile_name: str, incoming: Path, settings: Settings
) -> None:
    """A hostile filename is rebuilt, not reused, in the directory an operator will browse."""
    hostile = incoming / hostile_name
    hostile.write_bytes((FIXTURES / "disguised_exe.mp4").read_bytes())

    with pytest.raises(IngressRejected) as caught:
        verify_ingress(hostile, origin=Origin.UPLOAD, settings=settings)

    quarantined = caught.value.quarantined_to
    assert quarantined is not None
    assert quarantined.resolve().parent == settings.quarantine_dir.resolve()

    name = quarantined.name
    assert not name.startswith("."), "a quarantined file must not be hidden from the operator"
    assert not set(name) & set("\\/\n\r\x1b\x00")
    assert len(name) <= 96


def test_quarantining_twice_from_one_path_keeps_both(incoming: Path, settings: Settings) -> None:
    """Two files quarantined from one temporary name are both kept: the earlier is evidence."""
    first = copy_fixture("disguised_exe.mp4", incoming, as_name="tmp0001")
    with pytest.raises(IngressRejected) as caught_first:
        verify_ingress(first, origin=Origin.DOWNLOAD, settings=settings)

    second = copy_fixture("disguised_zip.png", incoming, as_name="tmp0001")
    with pytest.raises(IngressRejected) as caught_second:
        verify_ingress(second, origin=Origin.DOWNLOAD, settings=settings)

    kept_first = caught_first.value.quarantined_to
    kept_second = caught_second.value.quarantined_to
    assert kept_first is not None and kept_second is not None
    assert kept_first != kept_second
    assert kept_first.read_bytes().startswith(b"MZ")
    assert kept_second.read_bytes().startswith(b"PK")


def test_a_quarantine_that_cannot_be_written_still_refuses(incoming: Path, tmp_path: Path) -> None:
    """A broken quarantine directory never turns a rejection into an acceptance."""
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        SIFT_QUARANTINE_DIR=tmp_path / "data" / "quarantine" / "nested",
    )
    (tmp_path / "data").mkdir()
    # A file where the quarantine directory must go.
    (tmp_path / "data" / "quarantine").write_text("in the way")

    bad = copy_fixture("disguised_exe.mp4", incoming)
    with pytest.raises(IngressRejected) as caught:
        verify_ingress(bad, origin=Origin.DOWNLOAD, settings=settings)

    assert caught.value.reason is Reason.SIGNATURE_NOT_ALLOWED
    assert caught.value.quarantined_to is None


# --- The rejection log tells an admin what happened, and nothing else


def test_the_rejection_log_says_what_was_refused_and_which_file(
    incoming: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rejection log names the file and drops the account name in its path, by the logger's
    own redaction policy."""
    configure_logging("INFO", redact_personal=True)
    bad = copy_fixture("disguised_exe.mp4", incoming, as_name="cute_puppy.mp4")

    with pytest.raises(IngressRejected):
        verify_ingress(bad, origin=Origin.DOWNLOAD, settings=settings)

    output = capsys.readouterr().out
    record = json.loads(output.strip().splitlines()[-1])

    assert record["event"] == "security.ingress_rejected"
    assert record["reason"] == "signature_not_allowed"
    assert record["detected"] == "application/x-msdownload"
    assert record["quarantined"] is True
    assert record["handle"]
    assert "cute_puppy.mp4" in record["path"]


def test_the_rejection_log_still_loses_the_account_name(
    incoming: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Whose machine this is never survives."""
    configure_logging("INFO", redact_personal=True)
    library = tmp_path / "home" / "kate" / "Videos"
    library.mkdir(parents=True)
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    bad = copy_fixture("disguised_exe.mp4", library, as_name="cute_puppy.mp4")
    with pytest.raises(IngressRejected):
        verify_ingress(bad, origin=Origin.SCAN, settings=settings)

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "cute_puppy.mp4" in record["path"]
    assert "Videos" in record["path"]
    assert "/kate/" not in record["path"]
    assert REDACTED in record["path"]


@pytest.mark.integration
async def test_the_decoder_says_why_it_could_not_read_the_file(
    incoming: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """ffprobe's own words ("moov atom not found") are kept: a fact about the file, not the
    person."""
    configure_logging("INFO", redact_personal=True)
    bad = copy_fixture("corrupt_body.mp4", incoming, as_name="cut_short.mp4")

    result = verify_ingress(bad, origin=Origin.DOWNLOAD, settings=settings)
    with pytest.raises(IngressRejected):
        await verify_decodable(result, settings=settings)

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["reason"] == "not_decodable"
    assert "moov atom not found" in record["detected"]
    assert "cut_short.mp4" in record["path"]


def test_the_same_file_gets_the_same_handle_every_time(
    incoming: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """One file can be followed across log lines without the log naming it."""
    configure_logging("INFO", redact_personal=True)
    handles = []
    for _ in range(2):
        bad = copy_fixture("disguised_exe.mp4", incoming, as_name="same.mp4")
        with pytest.raises(IngressRejected):
            verify_ingress(bad, origin=Origin.SCAN, settings=settings)
        handles.append(json.loads(capsys.readouterr().out.strip().splitlines()[-1])["handle"])

    assert handles[0] == handles[1]


# --- The decoder check


@pytest.mark.integration
async def test_a_real_video_decodes(incoming: Path, settings: Settings) -> None:
    result = verify_ingress(
        copy_fixture("accepted.mp4", incoming), origin=Origin.SCAN, settings=settings
    )
    await verify_decodable(result, settings=settings)


@pytest.mark.integration
async def test_a_valid_signature_on_a_corrupt_body_is_refused(
    incoming: Path, settings: Settings
) -> None:
    """A container the signature accepts but ffprobe cannot read is refused, with the exact reason:
    the exit-code and video-stream guards are separate, and either answer would prove neither."""
    result = verify_ingress(
        copy_fixture("corrupt_body.mp4", incoming), origin=Origin.DOWNLOAD, settings=settings
    )
    assert result.media.name == "mp4"

    with pytest.raises(IngressRejected) as caught:
        await verify_decodable(result, settings=settings)

    assert caught.value.reason is Reason.NOT_DECODABLE


@pytest.mark.integration
async def test_a_container_with_no_picture_in_it_is_refused(
    incoming: Path, settings: Settings
) -> None:
    """A container with no video stream is refused: every accepted type has a picture."""
    result = verify_ingress(
        copy_fixture("audio_only.mp4", incoming), origin=Origin.DOWNLOAD, settings=settings
    )
    assert result.media.name == "mp4"

    with pytest.raises(IngressRejected) as caught:
        await verify_decodable(result, settings=settings)

    assert caught.value.reason is Reason.NO_VIDEO_STREAM


@pytest.mark.integration
async def test_a_file_that_fails_the_decoder_is_quarantined(
    incoming: Path, settings: Settings
) -> None:
    result = verify_ingress(
        copy_fixture("corrupt_body.mp4", incoming), origin=Origin.DOWNLOAD, settings=settings
    )
    with pytest.raises(IngressRejected) as caught:
        await verify_decodable(result, settings=settings)

    assert caught.value.quarantined_to is not None
    assert not result.path.exists()


@pytest.mark.integration
async def test_a_filename_cannot_become_an_ffprobe_option(
    incoming: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file named `-i` still decodes: ffprobe has no `--`, so an absolute path is passed. The path
    is RELATIVE here, since pytest's temporary paths are absolute and would pass without the
    guard."""
    copy_fixture("accepted.mp4", incoming, as_name="-i")
    monkeypatch.chdir(incoming)

    result = verify_ingress(Path("-i"), origin=Origin.DOWNLOAD, settings=settings)
    assert result.media.name == "mp4"

    # Not raising is the assertion.
    await verify_decodable(result, settings=settings)


@pytest.mark.integration
async def test_a_hung_decoder_does_not_hang_ingestion(
    incoming: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decoder that spins is given bounded time and refused, or one file stalls the queue."""
    monkeypatch.setattr(ingress, "_PROBE_TIMEOUT_SECONDS", 0.2)

    # One process, no shell: a wrapper that dies while the sleep lives holds the pipe open.
    hangs = stand_in_tool(tmp_path / "tools", "ffprobe-that-hangs", "import time; time.sleep(5)")

    settings = Settings(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", ffprobe_path=hangs
    )
    result = verify_ingress(
        copy_fixture("accepted.mp4", incoming), origin=Origin.DOWNLOAD, settings=settings
    )

    with pytest.raises(IngressRejected) as caught:
        await verify_decodable(result, settings=settings)

    # A stall is transient: the next pass asks again.
    assert caught.value.reason is Reason.TIMED_OUT
    assert caught.value.reason in TRANSIENT_REASONS
    assert caught.value.detected == "timeout"


@pytest.mark.integration
async def test_a_frame_with_too_many_pixels_is_refused(incoming: Path, tmp_path: Path) -> None:
    """A frame over the pixel ceiling is refused before anything renders it (a decompression bomb);
    a fake ffprobe stands in for the bomb."""
    fake = stand_in_tool(tmp_path / "tools", "ffprobe-bomb", "print('video,45000,45000')")
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", ffprobe_path=fake)
    result = verify_ingress(
        copy_fixture("accepted.mp4", incoming), origin=Origin.DOWNLOAD, settings=settings
    )

    with pytest.raises(IngressRejected) as caught:
        await verify_decodable(result, settings=settings)

    assert caught.value.reason is Reason.PIXELS_EXCEEDED


@pytest.mark.integration
async def test_a_video_stream_without_dimensions_is_not_refused_here(
    incoming: Path, tmp_path: Path
) -> None:
    """A stream reporting no dimensions is not refused for it; ffmpeg's allocation cap backs it."""
    fake = stand_in_tool(tmp_path / "tools", "ffprobe-nodims", "print('video')")
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", ffprobe_path=fake)
    result = verify_ingress(
        copy_fixture("accepted.mp4", incoming), origin=Origin.DOWNLOAD, settings=settings
    )

    await verify_decodable(result, settings=settings)


@pytest.mark.integration
async def test_a_broken_ffprobe_refuses_rather_than_waves_through(
    incoming: Path, tmp_path: Path
) -> None:
    """A decoder that cannot run lets nothing in."""
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        ffprobe_path="/nonexistent/ffprobe",
    )
    result = verify_ingress(
        copy_fixture("accepted.mp4", incoming), origin=Origin.SCAN, settings=settings
    )
    with pytest.raises((IngressRejected, OSError)):
        await verify_decodable(result, settings=settings)


# --- Properties: a byte-signature parser's interesting inputs are the ones nobody wrote down


@given(st.binary(max_size=64))
def test_the_classifier_never_raises(head: bytes) -> None:
    """Arbitrary bytes may be rejected, never crash the classifier."""
    detect(head, head)


@given(st.binary(min_size=1, max_size=512))
def test_random_bytes_are_not_media(head: bytes) -> None:
    """Anything accepted was accepted because of a signature."""
    media = detect(head, head)
    if media is not None:
        signatures = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"\x1a\x45\xdf\xa3")
        assert head.startswith(signatures) or head[4:8] == b"ftyp"


@given(st.binary(min_size=1, max_size=32))
def test_a_truncated_real_file_never_becomes_something_else(prefix: bytes) -> None:
    """A truncated real file never becomes a different accepted type."""
    genuine = (FIXTURES / "accepted.png").read_bytes()
    for cut in range(1, min(len(genuine), 40)):
        media = detect(genuine[:cut], prefix)
        assert media is None or media.name == "png"


@given(st.binary(min_size=1, max_size=16).filter(lambda b: not b.endswith(b"\x3b")))
def test_appending_bytes_to_a_terminated_format_breaks_it(payload: bytes) -> None:
    """Bytes after a terminated format's end mean it is not that format."""
    gif = (FIXTURES / "accepted.gif").read_bytes()
    assert detect(gif, gif[-32:]) is not None

    welded = gif + payload
    assert detect(welded, welded[-32:]) is None


# --- The gate stays the only way in


def test_no_feature_bypasses_the_gate() -> None:
    """No feature bypasses the gate: the risk is a new ingress path that never knew of it."""
    slices = Path(__file__).parents[2] / "slices"
    assert list(unguarded_ingress(slices)) == []


def test_the_bypass_check_catches_a_feature_that_hashes_without_verifying(
    tmp_path: Path,
) -> None:
    """The bypass check catches a feature that skips the gate."""
    (tmp_path / "sneaky").mkdir()
    (tmp_path / "sneaky" / "importer.py").write_text(
        "from sift.kernel.content import ingest\ndef run(path):\n    return ingest(path)\n"
    )
    complaints = list(unguarded_ingress(tmp_path))
    assert any("without importing verify_ingress" in c for c in complaints)


def test_the_bypass_check_catches_a_forged_proof(tmp_path: Path) -> None:
    """A feature minting its own `IngressResult` has forged the proof."""
    (tmp_path / "sneaky").mkdir()
    (tmp_path / "sneaky" / "importer.py").write_text(
        "from sift.kernel.ingress import IngressResult\n"
        "def run(path):\n"
        "    return IngressResult(path=path, media=None, size=0, origin='scan')\n"
    )
    complaints = list(unguarded_ingress(tmp_path))
    assert any("only the ingress gate may mint one" in c for c in complaints)


def test_the_bypass_check_catches_a_second_copy_of_the_allowlist(tmp_path: Path) -> None:
    """A feature's own list of media types is a second allowlist."""
    (tmp_path / "sneaky").mkdir()
    (tmp_path / "sneaky" / "scanner.py").write_text(
        "MEDIA = {'.mp4', '.mkv', '.webm', '.jpg'}\n"
        "def walk(root):\n"
        "    return [p for p in root.iterdir() if p.suffix in MEDIA]\n"
    )
    complaints = list(unguarded_ingress(tmp_path))
    assert any("import ALLOWED_MEDIA instead" in c for c in complaints)


def test_the_bypass_check_catches_an_allowlist_hiding_in_a_dict(tmp_path: Path) -> None:
    """A dict is a second allowlist as surely as a set."""
    (tmp_path / "sneaky").mkdir()
    (tmp_path / "sneaky" / "codecs.py").write_text(
        "CODEC_FOR = {'.mp4': 'h264', '.mkv': 'h264', '.webm': 'vp9', '.jpg': None}\n"
        "def pick(path):\n"
        "    return CODEC_FOR[path.suffix]\n"
    )
    complaints = list(unguarded_ingress(tmp_path))
    assert any("import ALLOWED_MEDIA instead" in c for c in complaints)


def test_the_bypass_check_is_quiet_on_a_feature_that_does_it_right(tmp_path: Path) -> None:
    """A feature that does it right passes."""
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "importer.py").write_text(
        "from sift.kernel.content import ingest\n"
        "from sift.kernel.ingress import Origin, verify_ingress\n"
        # A list of strings a feature may have; only one like the media allowlist is a copy.
        "PARTIAL_SUFFIXES = ('.part', '.tmp')\n"
        "def run(path, settings):\n"
        "    checked = verify_ingress(path, origin=Origin.UPLOAD, settings=settings)\n"
        "    return ingest(checked)\n"
    )
    assert list(unguarded_ingress(tmp_path)) == []


def test_the_bypass_check_leaves_a_features_own_tests_alone(tmp_path: Path) -> None:
    """A feature's own tests may build rejected files and forged proofs."""
    (tmp_path / "feature" / "tests").mkdir(parents=True)
    (tmp_path / "feature" / "tests" / "test_it.py").write_text(
        "from sift.kernel.ingress import IngressResult\n"
        "MEDIA = {'.mp4', '.mkv', '.webm', '.jpg'}\n"
        "def test_x():\n"
        "    IngressResult(path=None, media=None, size=0, origin='scan')\n"
    )
    assert list(unguarded_ingress(tmp_path)) == []


def test_the_bypass_check_does_not_trip_over_an_unusual_call(tmp_path: Path) -> None:
    """A call that is neither a name nor an attribute does not crash the check."""
    (tmp_path / "odd").mkdir()
    (tmp_path / "odd" / "weird.py").write_text(
        "HANDLERS = [lambda p: p]\n"
        "def run(path):\n"
        "    return (lambda p: p)(path) + HANDLERS[0](path)\n"
    )
    assert list(unguarded_ingress(tmp_path)) == []


def test_a_swapped_file_is_let_in_as_a_swap(incoming: Path, settings: Settings) -> None:
    """A swap's file is verified like the others, its origin carried, and it is Sift's to move."""
    result = verify_ingress(
        copy_fixture("accepted.mp4", incoming), origin=Origin.SWAP, settings=settings
    )
    assert result.origin is Origin.SWAP
    assert result.media.family == "isobmff-video"
    assert Origin.SWAP.sift_wrote_it
    assert Origin("swap") is Origin.SWAP


def test_the_result_carries_what_was_decided(incoming: Path, settings: Settings) -> None:
    result = verify_ingress(
        copy_fixture("accepted.png", incoming), origin=Origin.UPLOAD, settings=settings
    )
    assert isinstance(result, IngressResult)
    assert result.origin is Origin.UPLOAD
    assert result.media.kind is Kind.IMAGE
    with pytest.raises(AttributeError):
        result.size = 0  # type: ignore[misc]


# --- animated WebP
#
# Served where GIFs were; ffmpeg cannot demux the animation chunks, so accepted as an image it
# would be a row that never finishes importing.


def _webp(chunk: bytes, flags: int = 0) -> bytes:
    """A WebP header: RIFF, a length, WEBP, then the first chunk and its payload."""
    payload = bytes([flags]) + b"\x00" * 9
    body = b"WEBP" + chunk + len(payload).to_bytes(4, "little") + payload
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def test_an_animated_webp_is_a_GIF_rather_than_an_image(incoming: Path, settings: Settings) -> None:
    """An animated WebP is a GIF, down the path GIFs take, not a still."""
    written = incoming / "animated.webp"
    written.write_bytes(_webp(b"VP8X", flags=0x12))

    result = verify_ingress(written, origin=Origin.SCAN, settings=settings)

    assert result.media.name == "webp-animated"
    assert result.media.kind is Kind.GIF


def test_a_still_webp_with_an_extended_header_is_still_accepted(
    incoming: Path, settings: Settings
) -> None:
    """The extended header is not the animation flag: it carries alpha and profiles on stills."""
    written = incoming / "still.webp"
    written.write_bytes(_webp(b"VP8X", flags=0x10))

    assert verify_ingress(written, origin=Origin.SCAN, settings=settings).media.name == "webp"


def test_an_ordinary_webp_is_not_mistaken_for_a_GIF(incoming: Path, settings: Settings) -> None:
    written = incoming / "plain.webp"
    written.write_bytes(_webp(b"VP8 "))

    assert verify_ingress(written, origin=Origin.SCAN, settings=settings).media.name == "webp"


async def test_an_animated_webp_is_checked_by_the_tool_that_can_read_one(
    incoming: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An animated WebP is checked by libwebp's own tools, not ffprobe, which cannot demux it."""
    from sift.kernel import webp

    written = incoming / "moving.webp"
    written.write_bytes(_webp(b"VP8X", flags=0x12))
    result = verify_ingress(written, origin=Origin.SCAN, settings=settings)
    asked: list[Path] = []

    async def inspect(path: Path, **_: object) -> webp.Animation:
        asked.append(path)
        return webp.Animation(width=320, height=240, frames=12, duration_ms=1200)

    monkeypatch.setattr(webp, "inspect", inspect)

    await verify_decodable(result, settings=settings)

    assert asked == [result.path], "the animated WebP was not handed to the tool that can read it"


async def test_an_animated_webp_that_will_not_open_is_refused_with_what_went_wrong(
    incoming: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A still wearing the animation flag, a truncated download or a missing tool: one refusal,
    keeping the tool's own words."""
    from sift.kernel import webp

    written = incoming / "broken.webp"
    written.write_bytes(_webp(b"VP8X", flags=0x12))
    result = verify_ingress(written, origin=Origin.SCAN, settings=settings)

    async def inspect(path: Path, **_: object) -> webp.Animation:
        raise webp.WebpError("webpinfo failed: no detail")

    monkeypatch.setattr(webp, "inspect", inspect)

    with pytest.raises(IngressRejected) as refused:
        await verify_decodable(result, settings=settings)

    assert refused.value.reason is Reason.ANIMATED_WEBP_UNREADABLE


async def test_an_animated_webp_too_big_to_decode_is_refused_like_any_other(
    incoming: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same pixel ceiling holds for an animated WebP's canvas."""
    from sift.kernel import webp

    written = incoming / "enormous.webp"
    written.write_bytes(_webp(b"VP8X", flags=0x12))
    result = verify_ingress(written, origin=Origin.SCAN, settings=settings)

    async def inspect(path: Path, **_: object) -> webp.Animation:
        return webp.Animation(width=40_000, height=40_000, frames=2, duration_ms=200)

    monkeypatch.setattr(webp, "inspect", inspect)

    with pytest.raises(IngressRejected) as refused:
        await verify_decodable(result, settings=settings)

    assert refused.value.reason is Reason.PIXELS_EXCEEDED


def test_a_webp_truncated_before_its_flags_is_not_read_past_its_end(
    incoming: Path, settings: Settings
) -> None:
    """A short read answers "not a GIF" rather than reading past what it was given."""
    written = incoming / "short.webp"
    written.write_bytes(b"RIFF\x08\x00\x00\x00WEBPVP8X")

    assert verify_ingress(written, origin=Origin.SCAN, settings=settings).media.name == "webp"


def test_a_descriptor_that_is_not_a_regular_file_is_refused_wherever_that_can_happen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard asks the descriptor, not the path, what it is, so a swap between check and open
    cannot slip a special file through: tested on every platform."""
    ordinary = tmp_path / "clip.mp4"
    ordinary.write_bytes(b"\x00" * 64)
    monkeypatch.setattr(os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFIFO))

    with pytest.raises(OSError, match="not a regular file"):
        ingress.read_ends(ordinary)
