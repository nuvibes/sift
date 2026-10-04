# SPDX-License-Identifier: AGPL-3.0-or-later
"""The control a setting gets and the name a job gets follow from what each declares, not from a
list kept beside it.

Every registered setting falls into exactly one of the shapes the row primitive draws, named as the
client names them (the client's own test owns drawing each right, an integer never as a toggle),
and the client has a branch for each. A job's name is compulsory at registration; it must read as a
name, not repeat its type.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path
from typing import Any

import pytest

import sift.slices
from sift.kernel.settings_registry import Setting, registered_settings

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = _ROOT / "src" / "sift"
_CONTROLS = _ROOT / "frontend" / "src" / "lib" / "settings-ui" / "control.ts"


def _load_every_slice() -> None:
    """Import every slice, which fills the registry: importing nothing would find it empty."""
    for module in pkgutil.iter_modules(sift.slices.__path__):
        importlib.import_module(f"sift.slices.{module.name}")


#: The shapes the row primitive draws, in the order decided, in the client's words.
MENU = "menu"
TOGGLE = "toggle"
SLIDER = "slider"
NUMBER = "number"
TEXT = "text"

SHAPES = (MENU, TOGGLE, SLIDER, NUMBER, TEXT)


def shape_of(setting: Setting) -> str | None:
    """Which control a setting's metadata asks for, or None.

    Choices make a menu whatever the values; otherwise the default names the type. A percentage is
    the one number drawn as a slider, a proportion tuned by feel.
    """
    if setting.choices is not None:
        return MENU
    default: Any = setting.default
    if isinstance(default, bool):
        return TOGGLE
    if isinstance(default, int):
        return SLIDER if setting.unit == "%" else NUMBER
    if isinstance(default, str):
        return TEXT
    return None


#: Settings that are none of the row's shapes, each drawn by a control of its own, with why; a key
#: here that a pane draws as a row is the fault this file exists for.
DRAWN_BY_THEIR_OWN_CONTROL: dict[str, str] = {
    "appearance.theme_accent_swatches": (
        "a list of colours, drawn as dots under the accents by the accent picker on Appearance"
    ),
}


def test_every_registered_setting_has_a_shape_to_draw() -> None:
    _load_every_slice()
    settings = registered_settings()
    assert settings, "no settings were registered, so this gate would pass by finding nothing"

    undrawable = sorted(
        key
        for key, one in settings.items()
        if shape_of(one) is None and key not in DRAWN_BY_THEIR_OWN_CONTROL
    )
    assert not undrawable, (
        "these settings declare nothing the settings screen can pick a control from, so a row for "
        "one would fall through to whatever the client draws last: " + ", ".join(undrawable)
    )


def test_nothing_is_excused_by_accident() -> None:
    """An excused key is registered and really has no row shape."""
    _load_every_slice()
    settings = registered_settings()
    for key in DRAWN_BY_THEIR_OWN_CONTROL:
        assert key in settings, f"`{key}` is excused here and is not a registered setting"
        assert shape_of(settings[key]) is None, f"`{key}` has a row shape and needs no excuse"


def test_a_number_never_asks_for_a_toggle() -> None:
    """No setting holding a number claims to be a boolean; a number with fixed values is a menu."""
    _load_every_slice()
    for key, setting in registered_settings().items():
        if isinstance(setting.default, bool):
            continue
        if isinstance(setting.default, int):
            assert shape_of(setting) != TOGGLE, f"{key} holds a number and asks for a switch"


def test_every_option_has_something_to_call_it() -> None:
    """Every option has a name: `nvidia` reads "Graphics card"."""
    _load_every_slice()
    for key, setting in registered_settings().items():
        if setting.choices is None:
            continue
        assert setting.choice_labels is not None, f"{key} has choices and no names for them"
        assert len(setting.choice_labels) == len(setting.choices), (
            f"{key} has {len(setting.choices)} choices and "
            f"{len(setting.choice_labels)} names for them"
        )


@pytest.mark.skipif(not _CONTROLS.exists(), reason="the client is not checked out here")
def test_the_client_has_a_branch_for_every_shape() -> None:
    """The client names every shape this file can produce, or one falls to its last `else`."""
    source = _CONTROLS.read_text(encoding="utf-8")
    missing = [shape for shape in SHAPES if f"'{shape}'" not in source]
    assert not missing, (
        "the settings screen has no branch for: "
        + ", ".join(missing)
        + ": a setting of that shape would be drawn by whatever comes last instead"
    )


# --- jobs


def _registrations() -> list[tuple[Path, int, ast.Call]]:
    """Every `register_handler` call outside a test, with where it is."""
    found: list[tuple[Path, int, ast.Call]] = []
    for path in _SOURCE.rglob("*.py"):
        if "tests" in path.parts or "testing" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "register_handler":
                found.append((path, node.lineno, node))
    return found


def _named(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            return value if isinstance(value, str) else None
    return None


def test_every_job_says_what_it_is_called() -> None:
    calls = _registrations()
    assert calls, "no job registrations were found, so this gate would pass by finding nothing"

    for path, line, call in calls:
        where = f"{path.relative_to(_ROOT)}:{line}"
        # A name given as a variable (the media jobs loop) cannot be read here and is skipped.
        name = _named(call)
        if name is None:
            continue
        assert name.strip(), f"{where} registers a job with a blank name"
        assert name[0].isupper(), f"{where} names a job {name!r}, which does not start a sentence"
        assert "_" not in name, (
            f"{where} names a job {name!r}, which still reads as an internal name"
        )
