# SPDX-License-Identifier: AGPL-3.0-or-later
"""The log archive: every log of both places, each line redacted, made with nothing that can crash."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from sift import logbundle
from sift.kernel import redaction

pytestmark = pytest.mark.unit

#: What each line plants, and the value that must not leave the machine with it.
PLANTED = {
    "a spaced name in a traceback": (
        '  File "C:\\Users\\Esme Wrenfield\\AppData\\Local\\Sift\\client.py", line 703, in run',
        "Wrenfield",
    ),
    "a spaced name in a record's error": (
        json.dumps({"event": "scan.failed", "error": "denied: 'C:\\Users\\Bryn Calloway\\a.mp4'"}),
        "Calloway",
    ),
    "a spaced name on an escaped path": (
        "OSError: 'C:\\\\Users\\\\Bryn Calloway\\\\Videos'",
        "Calloway",
    ),
    "a spaced name ending the line": ("home C:\\Users\\Esme Wrenfield", "Wrenfield"),
    "a spaced name before a quote": ("cwd='/Users/Esme Wrenfield'", "Wrenfield"),
    "a drive named for somebody": ("OSError: /Volumes/Esme Wrenfield Backup/clip.mp4", "Wrenfield"),
    "a tunnel's own address": (
        "The client said: Address = 10.0.33.7/32, fd00::7/128",
        "10.0.33.7",
    ),
    "a tunnel's key": ("PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=", "yAnz5TF"),
    "a cookie in a traceback": (
        "ClientResponseError: 403, headers={'Cookie': 'theme=dark; sid_guard=f00dfeed42; u=7'}",
        "f00dfeed42",
    ),
    "a cookie header's second pair": ("Cookie: theme=dark; sessionkey2=beadfacade", "beadfacade"),
    "a cookies file line in a traceback": (
        "LoadError: '.media.example\\tTRUE\\t/\\tTRUE\\t0\\tremember_me\\tc0ffee7731'",
        "c0ffee7731",
    ),
    "a cookies file line": (
        ".media.example\tTRUE\t/\tFALSE\t0\tremember_me\tc0ffee7731",
        "c0ffee7731",
    ),
    "a folder named for the account": (
        "could not open D:\\Wrenfield\\Videos\\clip.mp4",
        "Wrenfield",
    ),
}


def write(folder: Path, name: str, lines: list[str]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes("".join(f"{one}\n" for one in lines).encode("utf-8"))
    return path


def read(made: io.BytesIO | Path) -> dict[str, str]:
    with zipfile.ZipFile(made) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def made_of(places: list[tuple[str, Path]], facts: Path | None = None) -> dict[str, str]:
    made = io.BytesIO()
    logbundle.write_archive(made, places, facts)
    return read(made)


@pytest.mark.parametrize("case", list(PLANTED))
def test_a_planted_value_never_leaves_in_the_archive(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(redaction, "_OS_USERNAME", "Wrenfield")
    line, value = PLANTED[case]
    write(tmp_path / "app", "backend.log", ["before", line, "after"])

    held = made_of([("app", tmp_path / "app")])["app/backend.log"]

    assert value not in held
    assert held.startswith("before\n") and held.endswith("after\n")


def test_every_log_of_both_places_is_kept_and_nothing_else(tmp_path: Path) -> None:
    library, app = tmp_path / "library", tmp_path / "app"
    write(library, "sift.log", [json.dumps({"event": "boot.ready", "level": "info"})])
    write(library, "sift.log.2", ["older"])
    write(library, "sift.sqlite3", ["not a log"])
    write(app, "shell.log", ["shell"])
    write(app, "backend.1.log", ["a start"])
    write(app, "Cookies", ["sid=c0ffee7731"])
    (app / "Local Storage" / "LOG").parent.mkdir()

    held = made_of([("library", library), ("app", app)])

    assert sorted(held) == [
        "app/backend.1.log",
        "app/shell.log",
        "contents.txt",
        "library/sift.log",
        "library/sift.log.2",
    ]
    assert json.loads(held["library/sift.log"]) == {"event": "boot.ready", "level": "info"}
    assert "library/sift.log: " in held["contents.txt"]


def test_a_place_with_no_logs_says_so_with_its_names_taken_out(tmp_path: Path) -> None:
    held = made_of([("app", Path("C:\\Users\\Esme Wrenfield\\AppData\\Roaming\\gone"))])

    assert held["contents.txt"].startswith("app: no logs in ")
    assert "Wrenfield" not in held["contents.txt"]


def test_the_newest_of_a_place_is_kept_up_to_its_budget_from_a_whole_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logbundle, "PLACE_BUDGET", 30)
    oldest = write(tmp_path, "sift.log.2", ["too old"])
    older = write(tmp_path, "sift.log.1", ["first line", "second", "third"])
    newest = write(tmp_path, "sift.log", ["newest"])
    for age, path in enumerate([newest, older, oldest]):
        os.utime(path, (1_000_000 - age, 1_000_000 - age))

    held = made_of([("library", tmp_path)])

    assert held["library/sift.log"] == "newest\n"
    # 23 bytes left of 30: the cut lands inside "first line", which is dropped whole.
    assert held["library/sift.log.1"] == "second\nthird\n"
    assert "library/sift.log.2" not in held
    assert "library/sift.log.2: 8 bytes, newest 0 kept" in held["contents.txt"]


def test_a_line_past_the_cap_keeps_its_front_and_the_next_line_is_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logbundle, "LINE_CAP", 8)
    write(tmp_path, "backend.log", ["x" * 30, "next"])

    assert made_of([("app", tmp_path)])["app/backend.log"] == "xxxxxxxx\nnext\n"


def test_a_file_cut_short_while_it_is_read_ends_where_it_ends(tmp_path: Path) -> None:
    path = write(tmp_path, "backend.log", ["one", "two"])

    assert list(logbundle._lines(path, 100)) == ["one", "two"]


def test_a_file_gone_since_it_was_listed_is_said_and_the_rest_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = write(tmp_path, "shell.log", ["kept"])
    monkeypatch.setattr(logbundle, "log_files", lambda _folder: [tmp_path / "gone.log", kept])

    held = made_of([("app", tmp_path)])

    assert "app/gone.log: unreadable" in held["contents.txt"]
    assert held["app/shell.log"] == "kept\n"


def test_the_facts_handed_in_are_redacted_and_an_unreadable_one_is_said(tmp_path: Path) -> None:
    facts = write(tmp_path, "facts.txt", ["sift 0.2.1", "home C:\\Users\\Esme Wrenfield"])

    held = made_of([], facts)
    missing = made_of([], tmp_path / "none.txt")

    assert held["facts.txt"].startswith("sift 0.2.1\n")
    assert "Wrenfield" not in held["facts.txt"]
    assert missing["contents.txt"] == "facts.txt: unreadable\n"


def test_the_command_writes_the_archive_and_leaves_no_part(tmp_path: Path) -> None:
    write(tmp_path / "data", "sift.log", ["library"])
    write(tmp_path / "app", "shell.log", ["app"])
    out = tmp_path / "logs.zip"

    app, data = str(tmp_path / "app"), str(tmp_path / "data")

    code = logbundle.main(["--out", str(out), "--app-logs", app, "--library-logs", data])

    assert code == 0
    assert sorted(read(out)) == ["app/shell.log", "contents.txt", "library/sift.log"]
    assert list(tmp_path.glob("*.part")) == []


def test_the_command_says_why_in_one_line_when_it_cannot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "no such folder" / "logs.zip"

    with pytest.raises(SystemExit) as ended:
        logbundle.main(["--out", str(out), "--app-logs", str(tmp_path)])

    assert ended.value.code == 1
    said = capsys.readouterr().err
    assert said.startswith("Couldn't write the log archive: ") and said.count("\n") == 1


def test_a_command_missing_its_folder_is_refused_in_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as ended:
        logbundle.main(["--out", "logs.zip"])

    assert ended.value.code == 2
    assert capsys.readouterr().err.count("\n") == 1
