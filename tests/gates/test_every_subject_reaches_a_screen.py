# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every kind of live update the server can send is one the client does something about.

The connection carries a word: the server's enumeration chooses it, the client's map decides what
to re-read. Three directions fail silently: a subject the client ignores, a client name the server
cannot send, and a subject that rings a bell nothing listens for. Both lists are read from source.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "src" / "sift" / "kernel" / "changes.py"
CLIENT = ROOT / "frontend" / "src" / "lib" / "shell" / "live.svelte.ts"

#: The client's map from a subject to what happens when it arrives, read to the closing brace: the
#: type in between holds an arrow, so "up to the first =" would find nothing.
_HANDLED = re.compile(r"const HANDLED\b.*?=\s*\{\n(.*?)\n\};", re.DOTALL)
#: One key per line, named or shorthand, the last one without a comma.
_KEY = re.compile(r"^\s*(\w+)\s*(?::|,|$)", re.MULTILINE)


def server_subjects() -> set[str]:
    """The server's enumeration values, from its own source."""
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "About":
            return {
                item.value.value
                for item in node.body
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
            }
    raise AssertionError("the server's list of subjects has moved; this check cannot find it")


def client_subjects() -> set[str]:
    """The keys of the client's map, from its own source."""
    found = _HANDLED.search(CLIENT.read_text(encoding="utf-8"))
    assert found is not None, "the client's map of subjects has moved; this check cannot find it"
    return set(_KEY.findall(found.group(1)))


def test_the_client_does_something_about_every_subject() -> None:
    unhandled = sorted(server_subjects() - client_subjects())

    assert not unhandled, (
        "\nThe server can send these and no screen does anything about them. The write lands, the\n"
        "message is delivered, and nothing on any screen moves, with every part of it reporting\n"
        "success.\n\n  " + "\n  ".join(unhandled) + "\n"
    )


def test_the_client_waits_for_nothing_the_server_cannot_send() -> None:
    invented = sorted(client_subjects() - server_subjects())

    assert not invented, (
        "\nThese are waited for and can never arrive, which looks exactly like a feature nobody\n"
        "finished.\n\n  " + "\n  ".join(invented) + "\n"
    )


def test_there_are_subjects_to_check() -> None:
    """A floor: a walk finding nothing on both sides would agree with itself."""
    assert len(server_subjects()) >= 5
    assert len(client_subjects()) >= 5


def test_the_last_entry_is_read_like_the_others() -> None:
    """The last entry, with no comma after it, is read like the others."""
    keys = set(
        _KEY.findall("""
	library: libraryChanges,
	arrivals,
	mine
""")
    )

    assert keys == {"library", "arrivals", "mine"}


#: Where a screen watches (`whenChanged`, stopping when taken down) or a tab-long store subscribes
#: (it cannot use an effect).
_LISTENS = ("whenChanged({name}", "{name}.subscribe(")

#: The signals a subject rings, by the name the client gives each one.
_RINGS = re.compile(r"^\s*(\w+):\s*\(\)\s*=>\s*(\w+)\.changed\(\)", re.MULTILINE)


def _client_files() -> list[Path]:
    root = ROOT / "frontend" / "src"
    found = [*root.rglob("*.svelte"), *root.rglob("*.ts")]
    return sorted(
        path
        for path in found
        if not path.name.endswith(".d.ts") and ".test." not in path.name and path != CLIENT
    )


def test_every_bell_a_subject_rings_has_somebody_listening_for_it() -> None:
    """Every bell a subject rings has a screen or store listening: a subject can be handled and
    still reach nothing while the client's list looks complete."""
    rung = dict(_RINGS.findall(CLIENT.read_text(encoding="utf-8")))
    sources = [path.read_text(encoding="utf-8") for path in _client_files()]

    deaf = sorted(
        subject
        for subject, bell in rung.items()
        if not any(form.format(name=bell) in source for form in _LISTENS for source in sources)
    )

    assert not deaf, (
        "\nThese are announced, delivered and acted on, and nothing anywhere is listening for what\n"
        "they ring. Every part of it reports success and no screen moves.\n\n  "
        + "\n  ".join(deaf)
        + "\n"
    )


def test_the_check_can_see_a_bell_being_listened_for() -> None:
    """The check tells a bell listened for from one that is not, both ways round."""
    listened = "whenChanged(libraryChanges, reload);"
    assert any(form.format(name="libraryChanges") in listened for form in _LISTENS)
    assert not any(form.format(name="libraryChanges") in "const x = 1;" for form in _LISTENS)


def test_every_subject_is_either_rung_or_handled_some_other_way() -> None:
    """The bells and the two named exceptions account for every subject: opinions arrive as rows
    applied in place, and a phone's command is handed to the screen it names."""
    rung = set(dict(_RINGS.findall(CLIENT.read_text(encoding="utf-8"))))
    others = client_subjects() - rung

    assert others == {"opinions", "remote"}, f"subjects handled some other way: {sorted(others)}"
