# SPDX-License-Identifier: AGPL-3.0-or-later
"""A line fit to share, and the shapes a log carries that the narrower rules once let through."""

from __future__ import annotations

import json

import pytest

from sift.kernel.redaction import REDACTED, redacted_line

pytestmark = pytest.mark.unit


def test_a_record_is_redacted_field_by_field_in_its_own_order() -> None:
    record = {"path": "C:\\Users\\Hollis\\a.mp4", "session": "x", "event": "opened"}

    shared = json.loads(redacted_line(json.dumps(record)))

    assert list(shared) == ["path", "session", "event"]
    assert shared == {
        "path": f"C:\\Users\\{REDACTED}\\a.mp4",
        "session": REDACTED,
        "event": "opened",
    }


@pytest.mark.parametrize(
    "line", ["Traceback: /home/hollis/a.mp4", json.dumps(["/home/hollis/a.mp4"])]
)
def test_any_other_line_is_redacted_as_text(line: str) -> None:
    assert "hollis" not in redacted_line(line)
    assert "a.mp4" in redacted_line(line)


@pytest.mark.parametrize(
    ("line", "shared"),
    [
        ('File "C:\\Users\\Esme Wrenfield\\x.py"', f'File "C:\\Users\\{REDACTED}\\x.py"'),
        ("at C:\\\\Users\\\\Esme Wrenfield\\\\x.py", f"at C:\\\\Users\\\\{REDACTED}\\\\x.py"),
        ("at /Volumes/Esme Wrenfield Backup/x.mp4", f"at /Volumes/{REDACTED}/x.mp4"),
        # Ending the line or a quote, the name goes whole, and so do up to three words of prose.
        ("home C:\\Users\\Esme Wrenfield", f"home C:\\Users\\{REDACTED}"),
        ("cwd='/Volumes/Esme Wrenfield Backup'", f"cwd='/Volumes/{REDACTED}'"),
        ("/home/esme wrenfield", f"/home/{REDACTED}"),
        ("C:\\Users\\Esme ran it", f"C:\\Users\\{REDACTED}"),
        # A capital after the first word may be a surname: everything after goes.
        ("C:\\Users\\Esme Wrenfield is missing a file", f"C:\\Users\\{REDACTED}"),
        # Longer lowercase prose is not a name: the narrower rule takes the one word.
        ("C:\\Users\\esme is missing a file", f"C:\\Users\\{REDACTED} is missing a file"),
    ],
)
def test_a_name_with_spaces_goes_whole_where_a_separator_ends_it(line: str, shared: str) -> None:
    assert redacted_line(line) == shared


@pytest.mark.parametrize(
    ("line", "shared"),
    [
        ("headers={'Cookie': 'a=1; b=f00d'}", f"headers={{'Cookie': '{REDACTED}'}}"),
        ("Set-Cookie: a=1; b=f00d", f"Set-Cookie: {REDACTED}"),
        (".site\tTRUE\t/\tFALSE\t0\tsid\tf00d", f".site\tTRUE\t/\tFALSE\t0\tsid\t{REDACTED}"),
        (
            "'.site\\tTRUE\\t/\\tFALSE\\t0\\tsid\\tf00d'",
            f"'.site\\tTRUE\\t/\\tFALSE\\t0\\tsid\\t{REDACTED}'",
        ),
        ("Address = 10.0.33.7/32, fd00::7/128", f"Address = {REDACTED}"),
        # An address with no prefix is a host, which a diagnosis needs.
        ("address=203.0.113.4", "address=203.0.113.4"),
    ],
)
def test_a_cookie_and_a_tunnels_address_are_taken_out(line: str, shared: str) -> None:
    assert redacted_line(line) == shared


#: The web server's own lines, with the address of the device that opened Sift.
CLIENT_LINES = [
    ('198.51.100.23:52095 - "WebSocket /api/live/stream?since=ab%3A67" [accepted]', ":52095 - "),
    ('198.51.100.23:52095 - "WebSocket /api/live/stream" 403', ":52095 - "),
    ('INFO:     192.0.2.44:61000 - "GET /api/assets/12/thumb HTTP/1.1" 200', ":61000 - "),
    # A four-digit port reads as one more group of the address unless the port is tried first.
    ('fe80::1c2:5ff:fe3:7a1:9050 - "GET /api/health HTTP/1.1" 200', ":9050 - "),
    ('::ffff:192.0.2.44:50113 - "POST /api/login HTTP/1.1" 401', ":50113 - "),
    ('2001:db8::a17 - "GET / HTTP/1.1" 200', ' - "GET'),
]


@pytest.mark.parametrize(("line", "kept"), CLIENT_LINES)
def test_the_address_of_the_device_that_opened_sift_is_taken_out(line: str, kept: str) -> None:
    address = line.removeprefix("INFO:     ").split(" ")[0].removesuffix(kept.split(" ")[0])
    shared = redacted_line(json.dumps({"event": line, "level": "info"}))

    assert address not in shared
    assert f"{REDACTED}{kept}" in json.loads(shared)["event"]
    assert address not in redacted_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "Cannot connect to host 203.0.113.9:443 ssl:default [Connect call failed]",
        "Endpoint = 198.51.100.20:51820",
        "Connect call failed ('203.0.113.45', 41873)",
        "Uvicorn running on http://0.0.0.0:5171 (Press CTRL+C to quit)",
        "fetch 203.0.113.9:443 - refused",
    ],
)
def test_a_host_sift_talks_to_is_kept(line: str) -> None:
    assert redacted_line(line) == line
