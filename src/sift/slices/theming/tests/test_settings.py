# SPDX-License-Identifier: AGPL-3.0-or-later
"""The look-and-feel preferences are declared the way the browser expects to find them.

Nothing on the server acts on these: the stylesheet does all of it, and for the seventh accent the
browser's own arithmetic does. So what there is to check is that they are declared correctly
enough for the client to be able to. Three things matter, and each of them fails silently if it is
wrong:

  - the SCOPE. A per-user setting follows a person to another device and leaves everybody else's
    alone. Declared as `app` by mistake, one person's taste would become the whole install's, and
    a guest would be refused when they tried to change it.
  - the CHOICES. A value no stylesheet rule matches leaves the page on the default with no error
    anywhere. The fixed set is what makes that impossible, and it has to be the same set the
    stylesheet actually contains, which the client's own gate checks from the other side.
  - the DEFAULTS. They have to be what `:root` already is, or a fresh install would paint itself
    once and then change.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.settings_registry import ReadBy, Scope, SettingError, get_registered
from sift.slices.theming import (
    ACCENT_HEX_KEY,
    ACCENT_KEY,
    ACCENT_SWATCHES_KEY,
    ACCENTS,
    BASE_KEY,
    BASES,
    BODY_FACE_LABELS,
    BODY_FACES,
    CLOCK_KEY,
    CLOCKS,
    DISPLAY_FACE_LABELS,
    DISPLAY_FACES,
    FACE_BODY_KEY,
    FACE_DISPLAY_KEY,
    MAX_SWATCHES,
)

pytestmark = [pytest.mark.regression]

EXPECTED = (
    (BASE_KEY, BASES, "midnight"),
    (ACCENT_KEY, ACCENTS, "blue"),
    (FACE_DISPLAY_KEY, DISPLAY_FACES, "archivo"),
    (FACE_BODY_KEY, BODY_FACES, "instrument-sans"),
    (CLOCK_KEY, CLOCKS, "12"),
)

#: The stylesheet, which is the one file in this repository allowed to name a colour.
APP_CSS = Path(__file__).resolve().parents[5] / "frontend" / "src" / "app.css"


@pytest.mark.parametrize(("key", "choices", "default"), EXPECTED)
def test_each_choice_is_per_account_and_drawn_from_a_fixed_set(
    key: str, choices: tuple[str, ...], default: str
) -> None:
    setting = get_registered(key)
    assert setting is not None, f"{key} is not registered: the browser would be refused on save"
    assert setting.scope is Scope.USER, f"{key} must be per-account, not instance-wide"
    assert setting.read_by is ReadBy.CLIENT, f"{key} is acted on by the browser, not the server"
    assert setting.choices == choices
    assert setting.default == default
    assert default in choices


@pytest.mark.parametrize(("key", "choices", "_default"), EXPECTED)
def test_a_value_outside_the_set_is_refused(
    key: str, choices: tuple[str, ...], _default: str
) -> None:
    """The whole point of the fixed set. A name the stylesheet has no rule for would not error in
    the browser: it would simply leave the page on the default forever."""
    setting = get_registered(key)
    assert setting is not None
    with pytest.raises(SettingError):
        setting.validate("no-such-theme")


def test_the_clock_is_twelve_hour_until_somebody_chooses_twenty_four() -> None:
    setting = get_registered(CLOCK_KEY)
    assert setting is not None
    assert setting.section == "Appearance"
    assert dict(zip(CLOCKS, setting.choice_labels or (), strict=True)) == {
        "12": "12-hour",
        "24": "24-hour",
    }


def test_the_four_sets_have_no_name_in_common() -> None:
    """They are separate attributes on the page, and a name in two of them would be a value that
    looks valid in the wrong one. Cheap to check, impossible to see by reading. A display face and
    a text face sharing a name is the same fault: a family's name meaning one face on one key and
    another face on the other is exactly what the faces were given their own names to end."""
    sets = (BASES, ACCENTS, DISPLAY_FACES, BODY_FACES)
    for index, one in enumerate(sets):
        for other in sets[index + 1 :]:
            assert set(one).isdisjoint(other), (one, other)


def test_each_face_is_named_on_screen() -> None:
    """The menus name a face rather than showing the stored word, one label per face."""
    for key, labels in ((FACE_DISPLAY_KEY, DISPLAY_FACE_LABELS), (FACE_BODY_KEY, BODY_FACE_LABELS)):
        setting = get_registered(key)
        assert setting is not None
        assert setting.choice_labels == labels
        assert setting.choices is not None
        assert len(labels) == len(setting.choices)


@pytest.mark.skipif(not APP_CSS.exists(), reason="the client is not checked out here")
def test_every_face_and_base_offered_is_a_rule_in_the_stylesheet() -> None:
    """A choice the server accepts and no stylesheet rule matches would save, and then leave the
    page on the default with nothing anywhere saying why. Read from the stylesheet both ways, so a
    rule the server does not offer is noticed too."""
    css = re.sub(r"/\*.*?\*/", " ", APP_CSS.read_text(encoding="utf-8"), flags=re.S)
    for attribute, names in (
        ("data-face-display", DISPLAY_FACES),
        ("data-face-body", BODY_FACES),
        ("data-base", BASES),
    ):
        ruled = set(re.findall(rf"\[{attribute}='([^']+)'\]", css))
        assert ruled == set(names), attribute


def test_the_colour_is_free_text_checked_for_shape() -> None:
    """The one value with no fixed set behind it. Shape is all a server can honestly check: whether
    a colour is LEGIBLE depends on the background it lands on, and the browser answers that by
    deriving five values that each clear the design system's floors. A second, weaker rule here
    would disagree with the real one about which colours a person may pick."""
    setting = get_registered(ACCENT_HEX_KEY)
    assert setting is not None
    assert setting.scope is Scope.USER
    assert setting.read_by is ReadBy.CLIENT
    assert setting.choices is None, "a colour is not a menu"

    for refused in ("burnt umber", "2563eb", "#2563e", "#2563ebb", "#2563eg", "", 16):
        with pytest.raises(SettingError):
            setting.validate(refused)

    # One spelling stored, so two rows differing only in the case of a letter cannot be two choices.
    assert setting.validate("#A831B7") == "#a831b7"


@pytest.mark.skipif(not APP_CSS.exists(), reason="the client is not checked out here")
def test_the_starting_colour_is_the_accent_the_stylesheet_already_draws() -> None:
    """The one place this module names a colour, and it is checked rather than trusted.

    A registered default has to be a literal. There is nowhere else for it to come from, so this
    is a second copy of a value `app.css` owns. It is here so the copy cannot drift: somebody who
    presses "Your own" before choosing anything should see the app exactly as it already was, and if
    the shipped blue is ever retuned this is what says the starting colour went with it.
    """
    setting = get_registered(ACCENT_HEX_KEY)
    assert setting is not None

    css = re.sub(r"/\*.*?\*/", " ", APP_CSS.read_text(encoding="utf-8"), flags=re.S)
    block = re.search(r":root,\s*\[data-accent='blue'\]\s*\{([^}]*)\}", css)
    assert block is not None, "the default accent is not in app.css under that selector"
    shipped = re.search(r"--p-accent\s*:\s*([^;]+);", block.group(1))
    assert shipped is not None

    assert setting.default == shipped.group(1).strip().lower()


def test_the_names_are_the_shape_the_boot_script_will_accept() -> None:
    """`static/theme-boot.js` puts these on the page before the first paint and checks their SHAPE
    rather than keeping a second copy of the lists: a lowercase word, at most 24 characters. A
    name that failed that test here would be dropped there, silently, and only on a cold load."""
    shape = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
    for names in (BASES, ACCENTS, DISPLAY_FACES, BODY_FACES):
        for name in names:
            assert shape.match(name), f"{name} would be refused by the boot script"


def _colours(count: int) -> list[str]:
    """`count` distinct colours of the stored shape, built rather than typed."""
    return [f"#{index:06x}" for index in range(count)]


def test_the_kept_colours_are_a_per_account_list_starting_empty() -> None:
    setting = get_registered(ACCENT_SWATCHES_KEY)
    assert setting is not None
    assert setting.scope is Scope.USER
    assert setting.read_by is ReadBy.CLIENT
    assert setting.section == "Appearance"
    assert setting.default == []
    assert setting.choices is None, "a list of colours is not a menu"


def test_each_kept_colour_is_checked_for_shape_and_stored_in_one_spelling() -> None:
    """The same shape check the one colour takes, colour by colour, so wearing a kept colour stores
    exactly what was kept."""
    setting = get_registered(ACCENT_SWATCHES_KEY)
    assert setting is not None
    assert setting.validate(["#A831B7", " #00ff7f "]) == ["#a831b7", "#00ff7f"]
    for refused in ("#a831b7", ["burnt umber"], ["#a831b"], [16], None, {"one": "#a831b7"}):
        with pytest.raises(SettingError):
            setting.validate(refused)


def test_at_most_ten_are_kept() -> None:
    setting = get_registered(ACCENT_SWATCHES_KEY)
    assert setting is not None
    assert MAX_SWATCHES == 10
    assert setting.validate(_colours(MAX_SWATCHES)) == _colours(MAX_SWATCHES)
    with pytest.raises(SettingError, match="at most 10"):
        setting.validate(_colours(MAX_SWATCHES + 1))


def test_a_colour_is_kept_once_whatever_its_spelling() -> None:
    """Two spellings of one colour would be two identical dots in the row."""
    setting = get_registered(ACCENT_SWATCHES_KEY)
    assert setting is not None
    with pytest.raises(SettingError, match="once"):
        setting.validate(["#a831b7", "#A831B7"])
