# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failed job's stored error, said in plain words by its kind; the tool's text never reaches them."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel import fetch
from sift.kernel.ingress import Reason
from sift.kernel.jobs.failure_words import (
    KINDS,
    OTHERWISE,
    VERDICT_WORDS,
    in_one_line,
    in_plain_words,
    kind_of,
    why_left_out,
)
from sift.kernel.jobs.recovery import _INTERRUPTED
from sift.kernel.jobs.worker_pool import _NO_HANDLER
from sift.kernel.subprocess import NEVER_STARTED
from sift.kernel.tests.test_fetch import FAILURES, FakeResponse, FakeSession, Unreached
from sift.slices.library_roots import jobs
from sift.slices.library_roots.walking import RootUnreachable

#: Stored errors in the shapes the queue keeps them, each with the kind it is.
STORED = [
    (
        "FFmpegError: ffmpeg.exe failed: [webp @ 0000028cd1b34ac0] image data not found",
        "unreadable",
    ),
    (
        "this file could not be decoded (ffmpeg.exe failed: [webp @ 01ab] image data not found",
        "unreadable",
    ),
    (
        "FFmpegError: ffmpeg.exe failed: [h264 @ 01ab] Invalid NAL unit size (1088342112 > 21767).",
        "unreadable",
    ),
    (
        "FileNotFoundError: [WinError 2] The system cannot find the file specified: 'X:\\a.mp4'",
        "missing",
    ),
    ("NoReadableCopy: none of the 1 known copies of this file could be opened.", "missing"),
    ("PermissionError: [WinError 5] Access is denied: 'X:\\library\\a.mp4'", "refused-write"),
    ("OSError: [Errno 28] No space left on device", "disk-full"),
    (f"FFmpegError: ffmpeg.exe failed: {NEVER_STARTED}", "never-started"),
    (_INTERRUPTED, "restarted"),
    (_NO_HANDLER, "retired"),
    (jobs.STOPPED_ANSWERING, "stopped-answering"),
]


@pytest.mark.parametrize(("error", "kind"), STORED)
def test_each_stored_error_is_said_by_its_kind(error: str, kind: str) -> None:
    found = kind_of(error)
    assert found is not None
    assert found.name == kind
    assert in_plain_words(error) == found.words


#: Each way a download fails, by the kind its words are.
_DOWNLOADS = {
    fetch.REFUSED: "connection-refused",
    fetch.UNREACHED: "unreached",
    fetch.NOT_FOUND: "name-not-found",
    fetch.NO_ANSWER: "no-answer",
    fetch.UNTRUSTED: "untrusted",
    fetch.PROXY: "proxy",
    fetch.DROPPED: "dropped",
}


@pytest.mark.parametrize(("error", "why"), FAILURES)
async def test_a_download_that_failed_is_said_by_its_kind(
    error: BaseException, why: str, tmp_path: Path
) -> None:
    with pytest.raises(fetch.FetchFailed) as failed:
        await fetch.fetch_resumable(
            "https://models.example.test/m",
            tmp_path / "m.part",
            what="detector model",
            session_factory=lambda: Unreached(error),
        )
    found = kind_of(f"WeightError: {failed.value} Or copy the file to this device yourself.")
    assert found is not None
    assert found.name == _DOWNLOADS[why]


@pytest.mark.parametrize(
    "words",
    [
        "ClientOSError: [Errno 13] Permission denied",
        "ClientConnectorError: [Errno 2] No such file or directory",
        "ClientOSError: [Errno 28] No space left on device",
    ],
)
def test_the_clients_words_after_a_download_sentence_never_change_its_kind(words: str) -> None:
    sentence = "The detector model couldn't be downloaded: the connection to files.test dropped."
    assert (kind_of(f"WeightError: {sentence} {words}") or KINDS[0]).name == "dropped"


def test_a_certificate_out_of_date_says_the_clock_and_no_other_refusal_does() -> None:
    def said(because: str) -> str:
        why = fetch.UNTRUSTED.format(host="files.test", because=because)
        return in_plain_words(f"The detector model couldn't be downloaded: {why}")

    assert "clock" in said(fetch.OUT_OF_DATE)
    for because in (fetch.UNKNOWN_ISSUER, fetch.WRONG_NAME, fetch.SAYS_NO_MORE):
        assert "clock" not in said(because)
        assert "security software" not in said(because)


async def test_a_refused_download_and_one_that_arrived_damaged_are_said_by_their_kinds(
    tmp_path: Path,
) -> None:
    with pytest.raises(fetch.FetchFailed) as failed:
        await fetch.fetch_resumable(
            "https://models.example.test/m",
            tmp_path / "m.part",
            session_factory=lambda: FakeSession(FakeResponse(503, b"")),
        )
    assert (kind_of(f"AccelError: {failed.value}") or KINDS[0]).name == "server-refused"
    for stored in (
        "WeightError: The reader model didn't arrive intact, so it was removed.",
        "AccelError: The onnxruntime package didn't arrive intact. Nothing was installed.",
    ):
        assert (kind_of(stored) or KINDS[0]).name == "arrived-damaged"


@pytest.mark.parametrize(
    "why", ["No such file or directory", "Access is denied", "The network path was not found"]
)
async def test_a_library_folder_that_did_not_answer_is_said_as_one_whatever_the_reason(
    why: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(jobs, "_root_answer", lambda _: OSError(2, why))
    with pytest.raises(RootUnreachable) as raised:
        await jobs._walk_for_scan(cast(Any, None), "root", tmp_path, tmp_path, None)
    found = kind_of(str(raised.value))
    assert found is not None
    assert found.name == "library-unreachable"


def test_an_error_no_kind_knows_says_the_tool_failed_and_where_its_words_are() -> None:
    assert kind_of("ValueError: the query needs exactly one marker") is None
    assert in_plain_words("ValueError: the query needs exactly one marker") == OTHERWISE


def test_no_plain_words_carry_a_tool_name_an_address_or_a_bracket() -> None:
    for words in [*(one.words for one in KINDS), OTHERWISE]:
        assert words.endswith(".")
        assert words[0].isupper()
        for token in ("ffmpeg", "Error", "[", "0x", "@"):
            assert token not in words


#: The source tree's features, where every pass that gives up on a file writes its code.
_SLICES = Path(__file__).resolve().parents[2] / "slices"

#: A code written as a literal where a pass gives up: `code="no_picture"`.
_WRITTEN_CODE = re.compile(r'code="([a-z_]+)"')


def test_every_code_a_pass_gives_up_with_has_words_of_its_own() -> None:
    """A code with no line here is said by its stored reason's KIND, which is the fallback and not
    the design: the gate's every reason, and every code a pass writes as a literal where it gives
    up, has its own sentence. Read out of the source so a new code is caught the day it is written.
    """
    written: set[str] = set()
    for path in _SLICES.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "tests" not in path.parts and ("record_verdict" in text or "_give_up" in text):
            written.update(_WRITTEN_CODE.findall(text))
    assert {"no_copy", "no_frame_decoded", "no_picture"} <= written, "the scan read nothing"
    missing = ({reason.value for reason in Reason} | {"no_frame"} | written) - set(VERDICT_WORDS)
    assert missing == set(), f"no words for {sorted(missing)}"


def test_a_left_out_file_is_said_by_its_code_and_never_by_the_tools_text() -> None:
    stored = (
        "this file could not be decoded (ffmpeg.exe failed: [h264 @ 01ab] Invalid NAL unit size)"
    )
    assert why_left_out("not_decodable", stored) == "It wouldn't open."
    # A code nobody has worded yet is said by the stored reason's kind, never by the reason.
    assert why_left_out("a_new_code", stored) == in_plain_words(stored)
    assert "ffmpeg" not in why_left_out("a_new_code", stored)
    for words in VERDICT_WORDS.values():
        assert words.endswith(".")
        assert words[0].isupper()
        for token in ("ffmpeg", "Error", "[", "0x", "@", "_"):
            assert token not in words


def test_a_rows_one_line_is_the_kinds_words_or_the_tools_last_line() -> None:
    assert in_one_line("FileNotFoundError: gone") == in_plain_words("FileNotFoundError: gone")
    assert in_one_line("ffmpeg said\n  frame 12 \n\nexit code 69\n") == "exit code 69"
    assert in_one_line("") == OTHERWISE
    long = in_one_line("x" * 1000)
    assert len(long) == 240 and long.endswith("...")
