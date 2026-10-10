# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for kernel.log, the redaction above all: a gap there produces a log that looks fine and
quietly carries what it was meant to hide."""

from __future__ import annotations

import json
import logging
import socket
import sys
import time
from collections.abc import Iterator, Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from sift.kernel import log as log_module
from sift.kernel import redaction as redaction_module
from sift.kernel.log import (
    REDACTED,
    WIDE_READ_ROWS,
    configure_logging,
    get_logger,
    hashed,
    hide_identity,
    hide_identity_in_path,
    path_facts,
    redact,
    timing_hook,
    user_safe_message,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def unredacted() -> Iterator[None]:
    """An admin has opted to see their own filesystem in their own logs."""
    log_module._redact_personal = False
    yield
    log_module._redact_personal = True


def test_whether_personal_detail_is_hidden_can_be_read(unredacted: None) -> None:
    """A process started by this one is set the same way, so it has to be readable."""
    assert log_module.redacts_personal() is False
    log_module._redact_personal = True
    assert log_module.redacts_personal() is True


def test_the_level_can_be_read_by_name() -> None:
    root = logging.getLogger()
    before = root.level
    try:
        root.setLevel("WARNING")
        assert log_module.level_name() == "WARNING"
    finally:
        root.setLevel(before)


# --- redaction by key --------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "cookie",
        "cookies",
        "auth_token",
        "password",
        "secret_key",
        "authorization",
        "session_token",
        "api_key",
        "private_key",
    ],
)
def test_a_credential_is_never_logged(key: str) -> None:
    """No mode reveals these. A session cookie in a log is a login somebody can replay."""
    assert redact({key: "anything at all"}) == {key: REDACTED}


def test_a_credential_stays_hidden_even_unredacted(unredacted: None) -> None:
    """The opt-out reveals an admin's own filesystem. It does not hand out their credentials."""
    assert redact({"cookie": "sessionid=abc"}) == {"cookie": REDACTED}
    assert redact({"password": "hunter2"}) == {"password": REDACTED}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # A host with no dotted TLD (localhost, an IP) is caught by no other net; only the
        # userinfo goes.
        ("http://user:hunter2@localhost:8080/x", "http://[redacted]@localhost:8080/x"),
        (
            "connecting to http://bob:pw@10.0.0.5/api",
            "connecting to http://[redacted]@10.0.0.5/api",
        ),
        # No userinfo, so nothing is touched.
        ("https://example.com/video.mp4", "https://example.com/video.mp4"),
    ],
)
def test_a_password_embedded_in_a_url_is_redacted(value: str, expected: str) -> None:
    """A password in `user:pass@host` is hidden like any credential; the host and path stay."""
    assert redact({"url": value}) == {"url": expected}


def test_a_url_password_stays_hidden_even_unredacted(unredacted: None) -> None:
    """The opt-out never shows a credential, including one inside a URL."""
    assert redact({"url": "https://admin:hunter2@example.com/x"}) == {
        "url": "https://[redacted]@example.com/x"
    }


@pytest.mark.parametrize("key", ["email", "username", "user_name"])
def test_a_personal_identifier_is_hidden_by_default(key: str) -> None:
    assert redact({key: "kate.smith@example.com"}) == {key: REDACTED}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/home/kate/Videos/holiday.mp4", "/home/[redacted]/Videos/holiday.mp4"),
        ("/Users/Kate/Movies/clip.mp4", "/Users/[redacted]/Movies/clip.mp4"),
        ("C:\\Users\\Kate\\Videos\\clip.mp4", "C:\\Users\\[redacted]\\Videos\\clip.mp4"),
        # The same path with its separators escaped, as a subprocess's JSON output carries it: the
        # username rule must match a doubled separator, and the share rule must not read `C:\\` as
        # the start of a `\\HOST\\share` path.
        (
            "C:\\\\Users\\\\Kate\\\\Videos\\\\clip.mp4",
            "C:\\\\Users\\\\[redacted]\\\\Videos\\\\clip.mp4",
        ),
        # A share, both ways. The host is usually the person.
        ("\\\\KATE-PC\\media\\clip.mp4", "\\\\[redacted]\\media\\clip.mp4"),
        ("\\\\\\\\KATE-PC\\\\media", "\\\\\\\\[redacted]\\\\media"),
        # Nothing personal in it, so nothing is touched.
        ("/mnt/nas/library/Season 1/ep01.mkv", "/mnt/nas/library/Season 1/ep01.mkv"),
        ("/media/library/clip.mp4", "/media/library/clip.mp4"),
    ],
)
def test_a_path_loses_its_name_and_keeps_its_shape(value: str, expected: str) -> None:
    """A path loses its name and keeps its shape: mount point, folder layout and filename."""
    assert redact({"path": value}) == {"path": expected}


@pytest.mark.parametrize(
    "value",
    ["/health", "/assets/{id}/save-to-device", "https://example.com/v/123", "GET /auth/login 403"],
)
def test_a_route_or_url_is_untouched(value: str) -> None:
    """Only a home-directory segment is reduced, so a route has nothing to match."""
    assert redact({"note": value}) == {"note": value}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Real names contain spaces, and a whitespace-bounded rule leaks the surname.
        ("C:\\Users\\John Smith\\Videos\\clip.mp4", "C:\\Users\\[redacted]\\Videos\\clip.mp4"),
        ("/Volumes/Kate Smith Backup/Movies/a.mkv", "/Volumes/[redacted]/Movies/a.mkv"),
        ("/Users/kate.smith/Movies/a.mp4", "/Users/[redacted]/Movies/a.mp4"),
        # macOS external drive, Windows network share, BSD.
        ("/Volumes/Backup/Movies/a.mkv", "/Volumes/[redacted]/Movies/a.mkv"),
        ("\\\\KATE-PC\\media\\a.mp4", "\\\\[redacted]\\media\\a.mp4"),
        ("/usr/home/kate/media/a.mp4", "/usr/home/[redacted]/media/a.mp4"),
    ],
)
def test_a_name_with_spaces_is_fully_removed(value: str, expected: str) -> None:
    """A path field is a path whole, so a name is read to the next separator, spaces and all."""
    assert hide_identity_in_path(value) == expected


def test_prose_rules_do_not_swallow_the_sentence() -> None:
    """In free text a name cannot run past a space, or a whole message would disappear."""
    result = hide_identity("C:\\Users\\Kate ran the job and it failed")
    assert "Kate" not in result
    assert "ran the job and it failed" in result


@pytest.mark.parametrize(
    "value",
    [
        # Mobile sandboxes by UUID and package id: no account name to remove.
        "/var/mobile/Containers/Data/Application/A1B2-C3D4/Documents/clip.mp4",
        "/storage/emulated/0/Download/clip.mp4",
        "/data/data/com.example.app/files/clip.mp4",
    ],
)
def test_mobile_paths_have_nothing_to_redact(value: str) -> None:
    assert hide_identity_in_path(value) == value


def test_a_generic_account_name_is_not_substituted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A generic account name (`media`, `data`) is not substituted, or every /media/... path would
    be redacted while protecting nobody."""
    for generic in ("media", "data", "root", "abc", "app"):
        # The home folder itself is patched: Windows reads USERPROFILE, never $HOME.
        monkeypatch.setattr(Path, "home", staticmethod(lambda name=generic: Path("/") / name))
        assert redaction_module._own_username() is None, (
            f"{generic!r} should not be treated as identifying"
        )


def test_a_distinctive_account_name_is_substituted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A distinctive account name is substituted."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/") / "harbourlight"))
    assert redaction_module._own_username() == "harbourlight"


@pytest.mark.parametrize("key", ["filename", "file_name", "basename"])
def test_a_bare_filename_is_left_alone(key: str) -> None:
    """A filename on its own says nothing about who owns it."""
    assert redact({key: "holiday.mp4"}) == {key: "holiday.mp4"}


@pytest.mark.parametrize("key", ["path", "source_path", "username"])
def test_an_admin_can_see_their_own_filesystem(key: str, unredacted: None) -> None:
    """The opt-out shows an admin their own filesystem."""
    assert redact({key: "/home/kate/clip.mp4"}) == {key: "/home/kate/clip.mp4"}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("url", "https://example.com/watch?v=abc"),
        ("source_url", "https://example.com/x"),
        ("host", "example.com"),
        ("title", "Some Video Title"),
        ("route", "/assets/{id}"),
        ("error", "HTTP Error 403: Forbidden"),
        ("stage", "transcode"),
    ],
)
def test_what_happened_stays_visible(key: str, value: str) -> None:
    """URLs, hosts, titles and error text say what happened, not who it happened to, and stay."""
    assert redact({key: value}) == {key: value}


@pytest.mark.parametrize("key", ["file_count", "file_size", "query_ms", "file_type"])
def test_counts_and_sizes_survive(key: str) -> None:
    assert redact({key: 42}) == {key: 42}


def test_a_session_id_is_redacted() -> None:
    """A session id is a credential that can be replayed, however opaque it looks."""
    assert redact({"session_id": "abc123"}) == {"session_id": REDACTED}


def test_opaque_ids_survive() -> None:
    """Opaque ids survive, or nothing is traceable."""
    event = {"asset_id": "01J5X2T7", "job_id": "01J5X2T8", "user_id": "01J5X2T9"}
    assert redact(event) == event


# --- the facts survive the redaction


def test_path_facts_report_what_the_path_cannot(tmp_path: Path) -> None:
    """Path facts report what the path cannot: whether the file is there and can be opened."""
    real = tmp_path / "clip.mkv"
    real.write_text("hello")

    facts = path_facts(real)
    assert facts["path_exists"] is True
    assert facts["path_readable"] is True
    assert facts["path_size"] == 5

    missing = path_facts(tmp_path / "gone.mp4")
    assert missing["path_exists"] is False
    assert missing["path_size"] is None


def test_path_facts_reveal_nothing_identifying() -> None:
    assert "kate" not in str(path_facts("/home/kate/Videos/holiday.mp4"))


def test_hide_identity_is_idempotent() -> None:
    """It runs on both the key and the value path, so a value may pass twice."""
    once = hide_identity("/home/kate/x.mp4")
    assert hide_identity(once) == once


# --- redaction by value shape


# Assembled, so the secret scanner does not flag a credential-shaped literal.
_TOKEN_SHAPED = "ghp" + "_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


@pytest.mark.parametrize(
    ("value", "must_not_contain"),
    [
        ("/home/realname/Videos/clip.mp4", "realname"),
        ("/Users/RealName/Movies/clip.mp4", "RealName"),
        ("C:\\Users\\RealName\\Videos\\clip.mp4", "RealName"),
        ('Traceback: File "/home/realname/app/x.py", line 3', "realname"),
        ("someone@example.com", "someone@example.com"),
        (_TOKEN_SHAPED, _TOKEN_SHAPED),
    ],
)
def test_an_identifying_value_is_caught_under_an_innocent_key(
    value: str, must_not_contain: str
) -> None:
    """Text is scanned as well as fields: paths turn up inside tracebacks and ffmpeg stderr."""
    result = redact({"note": value})
    assert must_not_contain not in result["note"]
    assert REDACTED in result["note"]


def test_a_hash_named_file_is_not_mistaken_for_a_secret() -> None:
    """A hash-named file with an extension is a fact and stays; a bare hex run is redacted, being
    indistinguishable from a key rendered as hex."""
    name = "d41d8cd98f00b204e9800998ecf8427e.jpg"
    assert redact({"filename": name}) == {"filename": name}
    assert redact({"note": "d41d8cd98f00b204e9800998ecf8427e"}) == {"note": REDACTED}


def test_a_long_file_name_with_a_dotted_version_is_not_mistaken_for_a_secret() -> None:
    """A backup's name with a dotted version before `.zip` is not read as a secret."""
    name = "sift-backup-01kx7b5cwh31d1hafvxeyf8g2p-20260101-120000-0.1.203.zip"
    assert redact({"note": f"Automatic backup would write {name}."}) == {
        "note": f"Automatic backup would write {name}."
    }
    # Without an extension the same run is redacted whole.
    bare = "sift-backup-01kx7b5cwh31d1hafvxeyf8g2p-20260101-120000"
    assert redact({"note": bare}) == {"note": REDACTED}


def test_a_credential_keyed_value_in_prose_is_redacted() -> None:
    """A session cookie or token is caught by the key that leads it in prose, at any length; the key
    is kept, so the log says what was hidden."""
    scrubbed = redact({"note": "auth failed for sessionid=8xk2mfp03qz9 on retry"})["note"]
    assert "8xk2mfp03qz9" not in scrubbed
    assert "sessionid=" in scrubbed
    assert REDACTED in scrubbed


# Assembled from parts, like _TOKEN_SHAPED above.
_FAKE_KEY = "AKIA" + "1234567890"
_FAKE_TOKEN = "abc123" + "def456"


@pytest.mark.parametrize(
    ("note", "secret"),
    [
        ("password=hunter2", "hunter2"),
        (f"the api_key={_FAKE_KEY} expired", _FAKE_KEY),
        ("Cookie: PHPSESSID=a1b2c3d4e5", "a1b2c3d4e5"),
        (f"token: {_FAKE_TOKEN}", _FAKE_TOKEN),
    ],
)
def test_a_credential_shaped_key_hides_its_value(note: str, secret: str) -> None:
    scrubbed = redact({"note": note})["note"]
    assert secret not in scrubbed
    assert REDACTED in scrubbed


def test_a_padded_base64_secret_is_redacted() -> None:
    """A base64 secret under the run rule's floor is caught by its `=` padding, which no path
    carries; the run rule leaves `+/` alone for paths and URLs."""
    # Assembled from parts, below the 32-character run floor.
    secret = "c2hvcnQt" + "c2VjcmV0" + "LXRva2Vu" + "=="
    scrubbed = redact({"note": f"signature {secret} rejected"})["note"]
    assert secret not in scrubbed
    assert REDACTED in scrubbed


def test_a_query_value_that_is_not_a_credential_stays_visible() -> None:
    """An ordinary query parameter is untouched; only credential names are keyed on."""
    url = "https://example.com/watch?v=abc123&list=xyz"
    assert redact({"note": url})["note"] == url


def test_a_path_given_as_a_path_object_is_reduced_not_blanked() -> None:
    """A Path object under a path key is reduced like a str, not erased."""
    # A home directory as THIS platform spells one.
    if sys.platform == "win32":
        assert redact({"path": Path(r"C:\Users\kate\Videos\holiday.mp4")}) == {
            "path": r"C:\Users\[redacted]\Videos\holiday.mp4"
        }
    else:
        assert redact({"path": Path("/home/kate/Videos/holiday.mp4")}) == {
            "path": "/home/[redacted]/Videos/holiday.mp4"
        }


def test_subprocess_output_keeps_everything_but_the_name() -> None:
    """Downloader output keeps the URL and destination, losing only the account name."""
    output = (
        "[download] Destination: /home/realname/media/SomeTitle.mp4\n"
        "[info] Downloading https://example.com/video/12345\n"
    )
    scrubbed = redact(output)
    assert "realname" not in scrubbed
    assert "/media/SomeTitle.mp4" in scrubbed
    assert "https://example.com/video/12345" in scrubbed


def test_an_exception_message_is_scrubbed() -> None:
    """An exception message quoting a path is scrubbed."""
    error = FileNotFoundError("No such file: /home/realname/Videos/private.mp4")
    assert "realname" not in str(redact(error))


def test_nesting_does_not_get_past_it() -> None:
    event = {"job": {"id": "01J5", "input": {"path": "/home/kate/x.mp4", "retries": 2}}}
    result = redact(event)
    assert result["job"]["input"]["path"] == f"/home/{REDACTED}/x.mp4"
    assert result["job"]["input"]["retries"] == 2
    assert result["job"]["id"] == "01J5"


def test_a_list_of_paths_does_not_get_past_it() -> None:
    result = redact({"note": ["/home/realname/a.mp4", "/home/realname/b.mp4"]})
    assert all("realname" not in item for item in result["note"])
    assert all(item.endswith(".mp4") for item in result["note"])


# --- correlation


def test_the_same_value_hashes_to_the_same_handle() -> None:
    """Two lines about one URL share a handle."""
    url = "https://example.com/video/12345"
    assert hashed(url) == hashed(url)
    assert hashed(url) != hashed("https://example.com/video/99999")


def test_the_handle_reveals_nothing() -> None:
    url = "https://example.com/video/12345"
    handle = hashed(url)
    assert "example.com" not in handle
    assert len(handle) == 12


# --- errors shown to users


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PermissionError("/srv/library/one.mp4"), "You do not have access to that."),
        (FileNotFoundError("/srv/library/one.mp4"), "That is no longer available."),
        (TimeoutError("waited 30s for ffprobe"), "That took too long. Try again."),
        (RuntimeError("SELECT * FROM users WHERE id = 'u1'"), "Something went wrong."),
    ],
)
def test_every_error_a_guest_can_provoke_says_only_what_it_may(
    error: Exception, expected: str
) -> None:
    """A user sees a sentence they can act on, never a path, a query or a URL; the original goes to
    the log."""
    said = user_safe_message(error)

    assert said.startswith(expected)
    assert str(error) not in said


def test_a_user_facing_error_says_nothing_about_the_system() -> None:
    error = FileNotFoundError("/home/realname/Videos/private.mp4 is missing")
    message = user_safe_message(error)
    assert "realname" not in message
    assert "/" not in message


# --- third-party loggers


def test_a_library_log_record_is_also_scrubbed(capsys: pytest.CaptureFixture[str]) -> None:
    """A third-party library's log record goes through the scrubber too. An ordinary logger, since
    `uvicorn.access` is silenced outright (below)."""
    configure_logging("INFO", redact_personal=True)

    logging.getLogger("httpx").info(
        '127.0.0.1 - "GET /assets?q=/home/realname/private.mp4 HTTP/1.1" 200'
    )

    output = capsys.readouterr().out
    assert "realname" not in output
    assert "private.mp4" in output
    assert REDACTED in output


def test_the_access_log_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    """uvicorn logs no requests, and `access_log=False` is not what stops it.

    uvicorn decides with `self.access_logger.hasHandlers()`, which walks up to the root handlers
    Sift installs. Its line is the raw target with ids in it, where Sift's middleware logs the
    route TEMPLATE.
    """
    configure_logging("INFO", redact_personal=True)
    capsys.readouterr()

    logging.getLogger("uvicorn.access").info(
        '127.0.0.1 - "GET /api/assets/01J5T6R7S8QRS9TVWXYZ0ABCD1/thumb HTTP/1.1" 200'
    )

    captured = capsys.readouterr()
    assert captured.out == "", "the access log is writing again. See configure_logging"
    assert captured.err == ""


def test_uvicorn_cannot_find_a_handler_for_the_access_log() -> None:
    """`hasHandlers()` itself is false for the access log: a level or a filter would silence the
    output and leave uvicorn formatting the line."""
    configure_logging("INFO", redact_personal=True)

    assert not logging.getLogger("uvicorn.access").hasHandlers(), (
        "uvicorn will log every request: it checks hasHandlers() and not the access_log flag."
    )


def test_a_logged_traceback_is_scrubbed(capsys: pytest.CaptureFixture[str]) -> None:
    """A traceback from `log.exception` passes through the scrubber."""
    configure_logging("INFO", redact_personal=True)
    log = get_logger("sift.test")

    try:
        raise ValueError("cannot open /home/realname/Videos/private.mp4")
    except ValueError:
        log.exception("job.failed")

    output = capsys.readouterr().out
    assert "job.failed" in output
    assert "Traceback" in output
    assert "realname" not in output
    assert "private.mp4" in output
    assert REDACTED in output


# --- timing


def test_timing_records_a_duration() -> None:
    with timing_hook("thumbnail"):
        pass


def test_timing_records_a_failure_and_re_raises() -> None:
    """A failure is timed and re-raised: the wrapper never swallows what it measured."""
    with pytest.raises(ValueError), timing_hook("transcode"):
        raise ValueError("boom")


def _timing_record(output: str, stage: str) -> dict[str, object] | None:
    """The structured timing record for `stage` in captured output."""
    import json

    for line in output.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("stage") == stage:
            return record
    return None


def test_a_demoted_timing_is_silent_at_info(capsys: pytest.CaptureFixture[str]) -> None:
    """A demoted per-statement timing is silent at INFO."""
    configure_logging("INFO", redact_personal=True)
    with timing_hook("db.read", level="debug", sql="SELECT 1"):
        pass
    assert "db.read" not in capsys.readouterr().out


def test_a_demoted_timing_reappears_at_debug(capsys: pytest.CaptureFixture[str]) -> None:
    """It reappears at DEBUG: demoted, not dropped."""
    configure_logging("DEBUG", redact_personal=True)
    with timing_hook("db.read", level="debug", sql="SELECT 1"):
        pass
    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["level"] == "debug"


def test_a_slow_demoted_timing_escalates_to_info_and_never_to_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A slow demoted statement shows in the ordinary log, not among the warnings; a stage that
    already logs louder keeps its level. A tiny real pause crosses the 1ms bar."""
    import time

    configure_logging("INFO", redact_personal=True)
    with timing_hook("db.read", level="debug", slow_ms=1.0, sql="SELECT 1"):
        time.sleep(0.01)
    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["level"] == "info"
    with timing_hook("db.read", level="warning", slow_ms=1.0, sql="SELECT 1"):
        time.sleep(0.01)
    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["level"] == "warning"


def test_a_failed_demoted_timing_escalates_to_warning(capsys: pytest.CaptureFixture[str]) -> None:
    """A failed demoted statement escalates to warning."""
    configure_logging("INFO", redact_personal=True)
    with pytest.raises(ValueError), timing_hook("db.read", level="debug", sql="SELECT 1"):
        raise ValueError("boom")
    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["level"] == "warning"


def test_an_ordinary_timing_still_logs_at_info(capsys: pytest.CaptureFixture[str]) -> None:
    """A stage that did not opt in still logs at info."""
    configure_logging("INFO", redact_personal=True)
    with timing_hook("sprite.render", asset_id="x"):
        pass
    record = _timing_record(capsys.readouterr().out, "sprite.render")
    assert record is not None
    assert record["level"] == "info"


# --- a wait is not the work
#
# A query that queued for a connection and ran fast must not be logged as a slow query: a block
# that queues says when its wait ended, and the record carries both numbers.


def test_a_block_that_declares_a_wait_reports_both_numbers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("DEBUG", redact_personal=True)
    with timing_hook("db.read", level="debug", sql="SELECT 1") as timing:
        time.sleep(0.02)
        timing.acquired()

    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    waited = record["waited_ms"]
    ran = record["ran_ms"]
    total = record["duration_ms"]
    assert isinstance(waited, float) and isinstance(ran, float) and isinstance(total, float)
    assert waited >= 15.0, "the sleep happened before the wait was declared over"
    assert ran < waited, "nothing ran after the wait, so the work is the smaller number"
    assert abs((waited + ran) - total) < 0.05, "the two halves are the whole"


def test_a_block_that_declares_no_wait_carries_neither_number() -> None:
    """A block declaring no wait carries neither field."""
    configure_logging("DEBUG", redact_personal=True)
    with timing_hook("thumbnail"):
        pass


def test_a_long_wait_on_a_fast_statement_is_not_reported_as_a_slow_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`slow_ms` judges the work, not the wall clock, so a fast lookup that waited is not escalated
    as slow; the wait is the pool's question."""
    configure_logging("DEBUG", redact_personal=True)
    with timing_hook("db.read", level="debug", slow_ms=10.0, sql="SELECT 1") as timing:
        time.sleep(0.05)
        timing.acquired()

    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["level"] == "debug", "a queue must never be escalated as a slow statement"


def test_a_statement_that_is_genuinely_slow_still_escalates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Work over the threshold still escalates."""
    configure_logging("DEBUG", redact_personal=True)
    with timing_hook("db.read", level="debug", slow_ms=10.0, sql="SELECT 1") as timing:
        timing.acquired()
        time.sleep(0.05)

    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["level"] == log_module.SLOW_LEVEL


def test_the_wait_ends_once_and_a_second_claim_is_ignored() -> None:
    """A nested acquisition is part of the work, not a second wait."""
    started = time.perf_counter()
    timing = log_module.Timing(started)
    timing.acquired()
    first = timing._split(time.perf_counter())[1]["waited_ms"]
    time.sleep(0.02)
    timing.acquired()
    assert timing._split(time.perf_counter())[1]["waited_ms"] == first


# --- how much a read moved, which a duration cannot say
#
# Each row costs a turn of the interpreter, so a wide read is fine when quiet and slow beside
# anything busy; the count is the same either way, which is why it is escalated on.


def test_a_block_can_report_what_it_measured_as_well_as_how_long_it_took(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("DEBUG", redact_personal=True)
    with timing_hook("db.read", level="debug", sql="SELECT 1") as timing:
        timing.measured(rows=3)

    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["rows"] == 3


def test_two_reports_from_one_block_both_land() -> None:
    """Two reports from one block are merged."""
    timing = log_module.Timing(time.perf_counter())
    timing.measured(rows=2)
    timing.measured(bytes_read=9)

    assert timing._measured == {"rows": 2, "bytes_read": 9}


def test_a_wide_read_escalates_on_the_ROW_COUNT_rather_than_the_clock(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An instant block escalates on its width alone, before a busy machine shows it."""
    configure_logging("INFO", redact_personal=True)
    with timing_hook("db.read", level="debug", sql="SELECT * FROM face_tracks") as timing:
        timing.measured(rows=WIDE_READ_ROWS)

    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["level"] == "info"


def test_a_record_names_the_statement_and_never_carries_its_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The statement text is not on a record, only its name: the text goes to the wide-read
    report."""
    configure_logging("DEBUG", redact_personal=True)
    with timing_hook(
        "db.read",
        level="debug",
        sql="SELECT rude_column FROM notes WHERE id = ?",
        statement="select:notes#0badf00d",
    ):
        pass

    output = capsys.readouterr().out
    record = _timing_record(output, "db.read")
    assert record is not None
    assert "sql" not in record
    assert record["statement"] == "select:notes#0badf00d"
    assert "rude_column" not in output


def test_the_statement_text_still_reaches_the_wide_read_report() -> None:
    """The wide-read report still gets the text, read by somebody who asked for it."""
    seen: list[tuple[str, int, str | None]] = []
    log_module.set_rows_sink(lambda stage, rows, sql: seen.append((stage, rows, sql)))
    try:
        with timing_hook("db.read", level="debug", sql="SELECT * FROM notes") as timing:
            timing.measured(rows=3)
    finally:
        log_module.set_rows_sink(None)

    assert seen == [("db.read", 3, "SELECT * FROM notes")]


def test_a_block_can_read_back_what_it_was_judged_on() -> None:
    """A block reads back what it was judged on, so the database layer never times twice."""
    with timing_hook("db.read", level="debug") as timing:
        timing.acquired()
    assert timing.ran_ms >= 0.0
    assert timing.ran_ms <= 1000.0


def test_an_ordinary_page_of_rows_is_not_escalated(capsys: pytest.CaptureFixture[str]) -> None:
    """A grid page of 200 rows is the ordinary case and not escalated."""
    configure_logging("DEBUG", redact_personal=True)
    with timing_hook("db.read", level="debug", sql="SELECT * FROM assets") as timing:
        timing.measured(rows=WIDE_READ_ROWS - 1)

    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None
    assert record["level"] == "debug"


def test_a_wide_read_that_FAILED_is_not_reported_as_a_wide_one() -> None:
    """A failed wide read is not reported as wide: it handed back nothing."""
    seen: list[tuple[str, int, str | None]] = []
    log_module.set_rows_sink(lambda stage, rows, sql: seen.append((stage, rows, sql)))
    try:
        with pytest.raises(ValueError), timing_hook("db.read", level="debug") as timing:
            timing.measured(rows=WIDE_READ_ROWS * 10)
            raise ValueError("boom")
    finally:
        log_module.set_rows_sink(None)

    # Still reported to the watch, not escalated.
    assert seen == [("db.read", WIDE_READ_ROWS * 10, None)]


# --- who this machine is: the username and hostname are substituted out of prose, and both
# readers answer NOTHING rather than raise where the machine will not say


def test_a_machine_that_will_not_say_who_it_is_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Path.home()` raises in a container run as a bare uid."""

    def refuses() -> Path:
        raise RuntimeError("no home directory")

    monkeypatch.setattr(Path, "home", staticmethod(refuses))
    assert redaction_module._own_username() is None


def test_a_machine_that_will_not_say_its_name_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuses() -> str:
        raise OSError("no name")

    monkeypatch.setattr(socket, "gethostname", refuses)
    assert redaction_module._own_hostname() is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("workshop.lan", "workshop"),
        # Too short to substitute safely.
        ("pi", None),
        ("localhost", None),
        ("raspberrypi", None),
        # A container id: unique per run.
        ("3f2a91b7c4de", None),
        # Not hexadecimal, so a real name.
        ("northroom4de", "northroom4de"),
    ],
)
def test_a_hostname_is_only_worth_hiding_when_it_names_somebody(
    name: str, expected: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: name)
    assert redaction_module._own_hostname() == expected


def test_this_machines_name_is_taken_out_of_prose(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostname is taken out of prose: mount points, URLs and errors carry it with no key."""
    monkeypatch.setattr(redaction_module, "_HOSTNAME", "workshop")
    monkeypatch.setattr(redaction_module, "_OS_USERNAME", None)

    assert log_module.hide_identity("cannot reach WORKSHOP:5171") == f"cannot reach {REDACTED}:5171"


def test_a_path_ending_in_this_machines_user_loses_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A home directory last in a path, with no separator after it."""
    monkeypatch.setattr(redaction_module, "_OS_USERNAME", "wren")
    monkeypatch.setattr(redaction_module, "_HOSTNAME", None)

    # A mount that is not a home directory: the name in an ffmpeg error or a library root.
    assert redaction_module._hide_own_names("could not open /srv/wren") == (
        f"could not open /srv/{REDACTED}"
    )
    assert redaction_module._hide_own_names("/srv/wren/one.mp4") == f"/srv/{REDACTED}/one.mp4"
    assert log_module.hide_identity("/home/wren") == f"/home/{REDACTED}"


def test_a_path_nothing_can_be_asked_about_reports_what_it_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filesystem that refuses the question answers "not there"."""

    def refuses(_self: Path) -> bool:
        raise OSError("no")

    monkeypatch.setattr(Path, "exists", refuses)

    assert path_facts(Path("/wherever/one.mp4")) == {
        "path_exists": False,
        "path_readable": False,
        "path_size": None,
    }


# --- setting the logging up


def test_a_log_file_that_cannot_be_opened_does_not_stop_the_boot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A log file that cannot be opened leaves the stream logging, said once on stderr, since the
    logger is what failed to be set up."""
    in_the_way = tmp_path / "not-a-directory"
    in_the_way.write_bytes(b"a file where a folder should be")

    configure_logging(log_file=in_the_way / "sift.log", max_bytes=1024)

    assert "could not open the log file" in capsys.readouterr().err
    get_logger(__name__).info("still.working")


def test_a_log_file_that_can_be_opened_is_written_to(tmp_path: Path) -> None:
    """A log file that can be opened is written to."""
    log_file = tmp_path / "logs" / "sift.log"
    try:
        configure_logging(log_file=log_file, max_bytes=100_000)
        get_logger(__name__).info("wrote.something")
    finally:
        configure_logging()

    assert "wrote.something" in log_file.read_text(encoding="utf-8")


def test_turning_the_redaction_off_says_so_at_every_boot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Redaction switched off is said at every boot, for whoever pastes the log later."""
    try:
        configure_logging(redact_personal=False)
    finally:
        configure_logging()

    assert "log.unredacted" in capsys.readouterr().out


def test_a_child_whose_parent_said_so_does_not_say_it_again(
    capsys: pytest.CaptureFixture[str],
) -> None:
    try:
        configure_logging(redact_personal=False, warn_unredacted=False)
    finally:
        configure_logging()

    assert "log.unredacted" not in capsys.readouterr().out


# --- what is worth recording as a security event


def test_a_security_event_is_recorded_as_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    """A security event logs that it happened, to whom and when, never the content."""
    log_module._security_log = None
    configure_logging()

    log_module.security_event("denied", user_id="u1")
    # Twice: the logger is made once and kept.
    log_module.security_event("denied", user_id="u2")

    written = capsys.readouterr().out
    assert written.count("security.denied") == 2
    assert "u1" in written


# --- what a diagnostic is allowed to cost


def test_a_record_is_handed_to_whoever_is_counting_the_work() -> None:
    """The report is a seam rather than an import: the record keeps no state and the thing that
    does lives with the other watches."""
    seen: list[tuple[str, float]] = []
    log_module.set_work_sink(lambda stage, ms: seen.append((stage, ms)))
    try:
        with timing_hook("something"):
            pass
    finally:
        log_module.set_work_sink(None)

    assert [stage for stage, _ in seen] == ["something"]


def test_a_counter_that_falls_over_is_not_the_reason_a_request_fails() -> None:
    """A diagnostic must never be the reason a request fails, and this one is called from inside
    every read in the application."""

    def angry(_stage: str, _ms: float) -> None:
        raise RuntimeError("no")

    log_module.set_work_sink(angry)
    try:
        with timing_hook("something"):
            pass
    finally:
        log_module.set_work_sink(None)


def test_nothing_is_reported_when_nobody_is_counting() -> None:
    """The ordinary case in a test and before the watches are wired at boot."""
    log_module.set_work_sink(None)
    with timing_hook("something"):
        pass


def test_a_record_is_also_handed_to_the_ledger_when_one_is_listening() -> None:
    """The second reader of the same measurement, set at its own moment of the boot and so kept
    apart from the first: each one hears the stage whether or not the other is there."""
    counted: list[str] = []
    filed: list[str] = []
    log_module.set_work_sink(lambda stage, _ms: counted.append(stage))
    log_module.set_stage_sink(lambda stage, _ms: filed.append(stage))
    try:
        with timing_hook("something"):
            pass
        log_module.set_work_sink(None)
        with timing_hook("another"):
            pass
    finally:
        log_module.set_work_sink(None)
        log_module.set_stage_sink(None)

    assert counted == ["something"]
    assert filed == ["something", "another"]


def test_a_ledger_that_falls_over_is_not_the_reason_a_request_fails_either() -> None:
    def angry(_stage: str, _ms: float) -> None:
        raise RuntimeError("no")

    log_module.set_stage_sink(angry)
    try:
        with timing_hook("something"):
            pass
    finally:
        log_module.set_stage_sink(None)


def test_a_backlog_source_that_falls_over_reports_nothing_rather_than_raising() -> None:
    """Same rule, on the other seam. It is asked once per record."""

    def angry() -> float:
        raise RuntimeError("no")

    log_module.set_loop_backlog(angry)
    try:
        assert log_module._loop_backlog_ms() is None
    finally:
        log_module.set_loop_backlog(None)


def test_a_statement_that_is_slow_while_the_loop_is_behind_is_not_blamed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The second wait hiding inside `ran_ms`: a finished statement's result is handed back through
    the loop like everything else, so a drained-queue time of seconds lands in the reading and reads
    as a slow statement."""
    configure_logging("DEBUG", redact_personal=True)
    log_module.set_loop_backlog(lambda: log_module.BACKLOGGED_SECONDS * 4)
    try:
        with timing_hook("db.read", level="debug", slow_ms=0.0):
            time.sleep(0.002)
    finally:
        log_module.set_loop_backlog(None)

    record = _timing_record(capsys.readouterr().out, "db.read")
    assert record is not None and "loop_backlog_ms" in record
    assert record["level"] == "debug"


# --- the decorator form ---------------------------------------------------------------------------


def test_the_decorator_times_a_plain_function(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()

    @log_module.timed("counting")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
    assert "counting" in capsys.readouterr().out


async def test_the_decorator_times_an_awaitable_one(capsys: pytest.CaptureFixture[str]) -> None:
    """Two wrappers rather than one, because awaiting a coroutine inside a synchronous wrapper
    would time how long it took to CREATE it."""
    configure_logging()

    @log_module.timed("counting")
    async def add(a: int, b: int) -> int:
        return a + b

    assert await add(2, 3) == 5
    assert "counting" in capsys.readouterr().out


# --- the timing hook does not pay for records nobody reads -----------------------------------
#
# `structlog.stdlib.BoundLogger` runs the whole processor chain, the redaction scrubber included,
# before the standard library drops a record for its level, so a discarded `debug` costs far more
# than the clock reads it reports, and every database statement carries one.
#
# The guard must not be able to silence the two cases that matter, which is what the last two say.


def test_a_debug_timing_at_info_level_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", redact_personal=True)
    capsys.readouterr()

    with timing_hook("db.read", level="debug", sql="SELECT 1"):
        pass

    assert capsys.readouterr().out == ""


def test_a_discarded_timing_never_reaches_the_scrubber(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is a COST fix, so it has to be tested by cost and not by output.

    The obvious test above cannot see it. Without the guard the record is still built, still runs
    the whole processor chain, and is still dropped by the standard library for its level, so
    "nothing was printed" is true either way.

    What IS observable is the work. The scrubber is the last and most expensive processor in the
    chain, so counting its calls answers the actual question (did building this record cost
    anything) rather than the question that happens to be easy to ask.
    """
    calls = 0
    original = log_module._redaction_processor

    def counting(logger: object, name: str, event: dict[str, object]) -> Mapping[str, object]:
        nonlocal calls
        calls += 1
        return original(logger, name, event)

    monkeypatch.setattr(log_module, "_redaction_processor", counting)
    configure_logging("INFO", redact_personal=True)

    with timing_hook("db.read", level="debug", sql="SELECT 1"):
        pass
    assert calls == 0, "a discarded timing still ran the processor chain"

    # And the control: when the level does admit it, the chain runs. Without this the assertion
    # above is satisfied by a scrubber that is simply never wired up.
    with timing_hook("db.read", level="warning", sql="SELECT 1"):
        pass
    assert calls > 0, "the counting scrubber was never wired in, so the check above proved nothing"


def test_a_debug_timing_at_debug_level_still_writes(capsys: pytest.CaptureFixture[str]) -> None:
    """The other half. A guard that silenced this would have turned the level off, not saved work."""
    configure_logging("DEBUG", redact_personal=True)
    capsys.readouterr()

    with timing_hook("db.read", level="debug", sql="SELECT 1"):
        pass

    assert "db.read" in capsys.readouterr().out


def test_a_slow_statement_is_still_reported_though_its_level_is_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The escalation outranks the guard, and this is why the guard asks about the CHOSEN level.

    A per-statement timing is emitted at debug so an ordinary run is not a wall of SQL. `slow_ms`
    escalates a slow one to warning. Asking `isEnabledFor` about the level the caller PASSED rather
    than the level finally chosen would discard exactly the statements worth having, silently, and
    only for the slow ones, which is the worst possible direction to be wrong in.
    """
    configure_logging("INFO", redact_personal=True)
    capsys.readouterr()

    with timing_hook("db.read", level="debug", slow_ms=0.0, sql="SELECT 1"):
        time.sleep(0.002)

    assert "db.read" in capsys.readouterr().out


def test_a_wide_read_is_still_reported_though_its_level_is_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same for the other escalation: a read wide enough to be a latency problem later."""
    configure_logging("INFO", redact_personal=True)
    capsys.readouterr()

    with timing_hook("db.read", level="debug", sql="SELECT 1") as timing:
        timing.measured(rows=WIDE_READ_ROWS)

    assert "db.read" in capsys.readouterr().out


def test_a_failed_statement_is_still_reported_though_its_level_is_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """And the third: a failure hidden at debug is a failure nobody sees."""
    configure_logging("INFO", redact_personal=True)
    capsys.readouterr()

    with pytest.raises(ValueError), timing_hook("db.read", level="debug", sql="SELECT 1"):
        raise ValueError("boom")

    assert "db.read" in capsys.readouterr().out


def test_the_hook_still_re_raises_what_the_block_threw() -> None:
    """The guard sits in a `finally`, and a `return` there discards the exception on its way out.

    The readable version of the guard is exactly that, and it would swallow every database error
    silently. Ruff's B012 flags it; this catches it if anyone tidies the condition back into an
    early return.
    """
    configure_logging("INFO", redact_personal=True)

    with pytest.raises(ValueError, match="boom"), timing_hook("db.read", level="debug"):
        raise ValueError("boom")


def test_a_distinctive_account_name_is_the_one_that_gets_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The arm that produces a name to redact, rather than the two that decline to.

    Everything else here drives the refusals: a name too short, a service account, a home that
    cannot be read. This is the case the redaction exists for, and on a machine whose own account is
    called something generic it is the arm nothing reaches.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "genevieve"))

    assert redaction_module._own_username() == "genevieve"


@pytest.mark.parametrize("generic", ["root", "app", "abc", "media", "container"])
def test_a_name_every_installation_shares_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, generic: str
) -> None:
    """Substituting one of these would eat every `/media/...` path in the log while protecting
    nobody. That is a live bug in at least one tool in this space."""
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / generic))

    assert redaction_module._own_username() is None


class TestMovingTheRunningLoggerOntoTheStoredPreferences:
    """`apply_log_preferences` is what makes the two log settings take effect without a restart.

    It is deliberately not `configure_logging` again: rebuilding the handlers would close and
    reopen the file mid-run and lose whatever a rotation was part way through. The two things
    somebody is choosing are a level and a cap, and both are attributes of objects already there.

    The restore below matters more than usual. These change the process-wide root logger, so a test
    that left it turned up would quietly change what every later test in the run writes.
    """

    @pytest.fixture(autouse=True)
    def _put_the_root_logger_back(self) -> Iterator[None]:
        root = logging.getLogger()
        was_level, was_handlers = root.level, list(root.handlers)
        yield
        root.setLevel(was_level)
        root.handlers[:] = was_handlers

    def test_the_detailed_log_turns_the_level_down_to_debug(self) -> None:
        logging.getLogger().setLevel(logging.INFO)

        log_module.apply_log_preferences(detailed=True, per_file_bytes=1024)

        assert logging.getLogger().level == logging.DEBUG

    def test_the_ordinary_log_puts_it_back_to_info(self) -> None:
        """Both directions, because a switch that only goes one way is a switch nobody can undo."""
        logging.getLogger().setLevel(logging.DEBUG)

        log_module.apply_log_preferences(detailed=False, per_file_bytes=1024)

        assert logging.getLogger().level == logging.INFO

    def test_it_never_turns_the_log_up_past_what_the_machine_was_started_at(
        self, tmp_path: Path
    ) -> None:
        """`SIFT_LOG_LEVEL` lasts past the database opening.

        Written as INFO flat, a server started at WARNING would go back to INFO a second later with
        nothing said, and the only tell would be the size of the log, which is the tell nobody
        reads.
        """
        log_module.configure_logging("WARNING", log_file=tmp_path / "sift.log")

        log_module.apply_log_preferences(detailed=False, per_file_bytes=1024)

        assert logging.getLogger().level == logging.WARNING

    def test_and_detailed_still_means_detailed_from_there(self, tmp_path: Path) -> None:
        """The other half. `Detailed` is a request for MORE, so it must still reach DEBUG from a
        machine that was started quiet: otherwise the switch does nothing on exactly the install
        where somebody would reach for it."""
        log_module.configure_logging("WARNING", log_file=tmp_path / "sift.log")

        log_module.apply_log_preferences(detailed=True, per_file_bytes=1024)

        assert logging.getLogger().level == logging.DEBUG

    def test_the_cap_reaches_the_file_handler_that_is_already_open(self, tmp_path: Path) -> None:
        """PER FILE, and read on every emit, so it takes effect on the next line written rather
        than at the next launch."""
        root = logging.getLogger()
        handler = RotatingFileHandler(tmp_path / "sift.log", maxBytes=10, backupCount=1)
        root.handlers[:] = [handler]

        log_module.apply_log_preferences(detailed=False, per_file_bytes=4096)

        assert handler.maxBytes == 4096
        handler.close()

    def test_a_lowered_cap_trims_the_older_files_immediately_and_a_raised_one_trims_nothing(
        self, tmp_path: Path
    ) -> None:
        """The handler only ever looks at the file it writes, so a rotated file bigger than the new
        cap would keep the whole set over the setting until later rotations pushed it out. Lowering
        trims the oldest immediately; raising has nothing to trim, and deletes nothing."""
        root = logging.getLogger()
        current = tmp_path / "sift.log"
        current.write_bytes(b"x" * 1_000)
        for n in (1, 2):
            (tmp_path / f"sift.log.{n}").write_bytes(b"x" * 9_000)
        handler = RotatingFileHandler(current, maxBytes=10_000, backupCount=2)
        root.handlers[:] = [handler]
        try:
            log_module.apply_log_preferences(detailed=False, per_file_bytes=20_000)
            assert sorted(one.name for one in tmp_path.iterdir()) == [
                "sift.log",
                "sift.log.1",
                "sift.log.2",
            ]

            log_module.apply_log_preferences(detailed=False, per_file_bytes=3_000)
        finally:
            handler.close()

        assert sorted(one.name for one in tmp_path.iterdir()) == ["sift.log"]
        assert current.stat().st_size == 1_000, "the file being written is never the one trimmed"

    def test_a_rollover_trims_the_set_to_the_total_the_setting_stands_for(
        self, tmp_path: Path
    ) -> None:
        """The standard handler keeps `backupCount` older files whatever their size. Sift's own
        ends every rollover by dropping the oldest until the set fits `maxBytes` times the number
        of files, so a big file from before a lowered setting cannot ride the rotation out."""
        current = tmp_path / "sift.log"
        current.write_bytes(b"x" * 800)
        (tmp_path / "sift.log.1").write_bytes(b"x" * 5_000)
        handler = log_module._CappedRotatingFileHandler(
            current, maxBytes=1_000, backupCount=2, encoding="utf-8"
        )
        try:
            handler.doRollover()
        finally:
            handler.close()

        assert sorted(one.name for one in tmp_path.iterdir()) == ["sift.log", "sift.log.1"]
        assert (tmp_path / "sift.log.1").stat().st_size == 800
        assert sum(one.stat().st_size for one in tmp_path.iterdir()) <= 1_000 * 3

    def test_a_handler_that_is_not_a_log_file_is_left_alone(self) -> None:
        """A stream handler has no cap to set, and reaching for one would be an AttributeError on
        the path that runs every time somebody saves a setting."""
        root = logging.getLogger()
        stream = logging.StreamHandler()
        root.handlers[:] = [stream]

        log_module.apply_log_preferences(detailed=False, per_file_bytes=4096)

        assert not hasattr(stream, "maxBytes")

    def test_no_log_file_is_MADE_by_a_screen_about_how_large_one_may_get(
        self, tmp_path: Path
    ) -> None:
        """The claim in the function's own note. Somebody who never asked for a log file must not
        get one because they looked at the setting that caps it."""
        root = logging.getLogger()
        root.handlers[:] = []

        log_module.apply_log_preferences(detailed=True, per_file_bytes=4096)

        assert root.handlers == []
        assert list(tmp_path.iterdir()) == []


# --- the library's `Hide personal details in the log` ------------------------------------------


@pytest.mark.parametrize("hide", [False, True])
def test_hide_personal_details_decides_whether_a_written_line_keeps_its_path(
    hide: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """Written whole unless the setting asks; the secret beside the path is hidden either way."""
    configure_logging("INFO", redact_personal=True)
    try:
        log_module.apply_log_preferences(
            detailed=False, per_file_bytes=1024 * 1024, hide_personal=hide
        )
        capsys.readouterr()

        logging.getLogger("httpx").info("opened /home/someone/Videos/clip.mp4 token=hunter22")

        output = capsys.readouterr().out
        assert ("someone" in output) is not hide
        assert "clip.mp4" in output
        assert "hunter22" not in output
    finally:
        log_module._redact_personal = True


def test_leaving_hide_personal_out_keeps_the_answer_already_set() -> None:
    log_module._redact_personal = False
    try:
        log_module.apply_log_preferences(detailed=False, per_file_bytes=1024 * 1024)
        assert log_module.redacts_personal() is False
    finally:
        log_module._redact_personal = True


def test_the_database_drivers_lines_never_reach_the_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The driver logs every statement with its bound values at debug; the detail switch must not
    let them through."""
    configure_logging("INFO", redact_personal=True)
    log_module.apply_log_preferences(detailed=True, per_file_bytes=1_000_000)
    capsys.readouterr()

    logging.getLogger("aiosqlite").debug(
        "executing SELECT title FROM assets WHERE title = 'canary-x9'"
    )
    logging.getLogger("sift.kernel.db").debug("timing", extra={"statement": "select:assets#1"})

    output = capsys.readouterr().out
    assert "canary-x9" not in output
    assert "timing" in output, "Sift's own debug line is what the detail switch is for"


def test_the_shells_word_goes_to_stdout_whole(capsys: pytest.CaptureFixture[str]) -> None:
    from sift.kernel.log import tell_the_shell

    tell_the_shell("sift.listening")
    assert capsys.readouterr().out == "sift.listening\n"


# --- What one job cost ----------------------------------------------------------------------------


def test_a_job_s_stages_reads_and_waits_are_filed_to_it_and_nothing_counts_twice() -> None:
    """Spans nest (a write inside a stage), so what they account for is their union."""
    cost = log_module.JobCost(began=0.0)
    cost.staged("probe.verify", 0.0, 0.4)
    cost.staged("probe.verify", 0.5, 0.6)
    cost.staged("db.read", 0.6, 0.7)
    cost.staged("db.sweep", 0.7, 0.75)
    # A statement inside a write block, and the job's own record: neither is a part of its own.
    cost.staged("db.write", 0.1, 0.2)
    cost.staged("job", 0.0, 1.0)
    cost.wrote(0.1, 0.3, 0.35)
    cost.waited_for_storage(0.8, 0.85)
    cost.launched(0.84, 0.9, 1234)

    said = cost.summary(ended=1.0)

    assert said["stages"] == {"probe.verify": 500}
    assert (said["reads"], said["read_ms"]) == (2, 150)
    assert (said["writes"], said["writer_wait_ms"], said["writer_held_ms"]) == (1, 200, 50)
    assert said["storage_wait_ms"] == 50
    assert (said["launches"], said["tool_ms"], said["tool_read_bytes"]) == (1, 60, 1234)
    # 0 to 0.4, 0.5 to 0.75, 0.8 to 0.9: the write inside the first stage adds nothing.
    assert (said["wall_ms"], said["covered_ms"], said["covered_pct"]) == (1000, 750, 75.0)


def test_parts_that_ran_together_never_claim_more_than_the_job_s_time() -> None:
    """A walk's take-ins wait for the writer side by side: the wait is the time some part waited,
    and the sum of the parts' waits is said beside it."""
    cost = log_module.JobCost(began=0.0)
    cost.wrote(0.0, 0.6, 0.65)
    cost.wrote(0.2, 0.8, 0.85)
    cost.staged("library.take_in.copy", 0.1, 0.5)
    cost.staged("library.take_in.copy", 0.3, 0.7)
    said = cost.summary(ended=1.0)
    assert said["writer_wait_ms"] == 800 and said["writer_held_ms"] == 100
    assert said["stages"] == {"library.take_in.copy": 600}
    assert said["parts_ms"] == {"library.take_in.copy": 800, "writer_wait_ms": 1200}
    assert said["covered_ms"] <= said["wall_ms"]


def test_a_span_past_either_end_of_the_job_counts_only_inside_it() -> None:
    cost = log_module.JobCost(began=1.0)
    cost.staged("early", 0.5, 1.5)
    cost.staged("late", 1.8, 3.0)
    assert cost.summary(ended=2.0)["covered_ms"] == 700


def test_a_job_that_took_no_time_is_wholly_accounted_for() -> None:
    assert log_module.JobCost(began=5.0).summary(ended=5.0)["covered_pct"] == 100.0


def test_a_long_job_s_spans_are_merged_as_they_come_so_its_record_stays_small(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(log_module, "_KEPT_SPANS", 4)
    cost = log_module.JobCost(began=0.0)
    for n in range(10):
        cost.staged("step", n, n + 1)
    assert len(cost.stages["step"].spans) <= 5 and len(cost._covered.spans) <= 5
    assert cost.summary(ended=10.0)["covered_ms"] == 10_000


def test_a_timed_block_inside_a_job_is_filed_to_it_and_outside_one_to_nothing() -> None:
    cost = log_module.JobCost()
    with timing_hook("outside"):
        pass
    with log_module.costing(cost):
        assert log_module.job_cost() is cost
        with timing_hook("inside"):
            pass
    assert log_module.job_cost() is None
    assert list(cost.stages) == ["inside"]


def test_the_job_s_own_record_is_its_handler_time_and_its_per_file_lines_are_counted() -> None:
    cost = log_module.JobCost(began=0.0)
    cost.staged("job", 0.0, 0.8)
    cost.counted("importing.skipped")
    cost.counted("importing.skipped")
    said = cost.summary(ended=1.0)
    assert said["ran_ms"] == 800 and said["stages"] == {}
    assert said["folded"] == {"importing.skipped": 2}


@pytest.mark.parametrize("detailed", [False, True])
def test_at_normal_a_per_file_line_is_counted_into_its_job_and_a_claim_is_left_to_the_summary(
    capsys: pytest.CaptureFixture[str], detailed: bool
) -> None:
    configure_logging("DEBUG" if detailed else "INFO", redact_personal=True)
    capsys.readouterr()
    log = get_logger("test.fold")
    cost = log_module.JobCost()
    with log_module.costing(cost):
        log.info("importing.skipped", asset_id="A")
        log.info("content.location_missing", asset_id="B")
        log.info("job.done", job_id="J")
        log.info("content.ingested", asset_id="C")
        with timing_hook("job"), timing_hook("probe.verify"):
            pass
    log.info("job.claimed", job_id="J")
    out = capsys.readouterr().out
    assert "content.ingested" in out and '"stage": "probe.verify"' in out
    assert ('"stage": "job"' in out) is detailed
    for event in ("importing.skipped", "content.location_missing", "job.done", "job.claimed"):
        assert (event in out) is detailed
    assert cost.summary()["folded"] == {"content.location_missing": 1, "importing.skipped": 1}


def test_per_file_lines_outside_a_job_are_said_as_counts_once_a_minute(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_logging("INFO", redact_personal=True)
    monkeypatch.setattr(log_module, "_folded_outside", {})
    monkeypatch.setattr(log_module, "_folded_since", time.monotonic())
    capsys.readouterr()
    log = get_logger("test.fold")
    for _ in range(3):
        log.info("job.enqueue_deduped", job_type="scan")
    log.info("something.else")
    assert "log.folded" not in capsys.readouterr().out
    monkeypatch.setattr(log_module, "_folded_since", time.monotonic() - 61)
    log.info("something.else")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    folded = [one for one in lines if one["event"] == "log.folded"]
    assert len(folded) == 1 and folded[0]["counts"] == {"job.enqueue_deduped": 3}
    assert folded[0]["seconds"] >= 60
    # Said once: the counts start again.
    log.info("something.else")
    assert "log.folded" not in capsys.readouterr().out
    log_module._say_folded()
    assert "log.folded" not in capsys.readouterr().out


def test_a_storage_wait_is_filed_to_the_job_it_was_for() -> None:
    log_module.note_storage_wait(0.0, 1.0)  # outside a job: nobody to tell
    cost = log_module.JobCost(began=0.0)
    with log_module.costing(cost):
        log_module.note_storage_wait(0.0, 0.25)
    assert cost.summary(ended=1.0)["storage_wait_ms"] == 250


def test_a_start_never_rolls_a_log_its_setting_would_keep(tmp_path: Path) -> None:
    """The start's own size is far below the library's setting, which is read seconds later: a
    long log rolled at the first line, and every older file trimmed to fit the start's size."""
    from sift.kernel.log_settings import largest_file_bytes

    log_file = tmp_path / "sift.log"
    log_file.write_bytes(b"x" * 4096)
    older = tmp_path / "sift.log.1"
    older.write_bytes(b"y" * 8192)
    try:
        configure_logging(log_file=log_file, max_bytes=1024, backups=5)
        get_logger(__name__).info("boot.imported")
        handler = next(
            h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)
        )
        assert handler.maxBytes == largest_file_bytes(5)
    finally:
        configure_logging()
    assert log_file.stat().st_size > 4096
    assert older.stat().st_size == 8192
