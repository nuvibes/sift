# SPDX-License-Identifier: AGPL-3.0-or-later
"""Theming: look preferences kept per user so they follow a person; only the browser reads them."""

from __future__ import annotations

import re

from sift.kernel.settings_registry import ReadBy, SettingError, register_setting

#: Fixed choices: a value no stylesheet matches would leave the page on the default silently.
BASE_KEY = "appearance.theme_base"
ACCENT_KEY = "appearance.theme_accent"

#: The colour the seventh accent is derived from, `#rrggbb`. Its own key, so trying a named
#: accent does not forget it and `ACCENT_KEY` keeps its `choices`.
ACCENT_HEX_KEY = "appearance.theme_accent_hex"

#: Kept colours in their order; wearing one copies it into `ACCENT_HEX_KEY`.
ACCENT_SWATCHES_KEY = "appearance.theme_accent_swatches"

#: Ten under the six still reads as a row of choices, not a palette to search.
MAX_SWATCHES = 10

#: Two keys, each naming a face of its own role, so one display face can pair with two text
#: faces without appearing twice in its menu.
FACE_DISPLAY_KEY = "appearance.theme_face_display"
FACE_BODY_KEY = "appearance.theme_face_body"

#: Read only by the browser, which writes every time through `lib/shell/when.ts`.
CLOCK_KEY = "appearance.clock"

CLOCKS: tuple[str, ...] = ("12", "24")

#: Darkest first: true black, the old near-black, then one neutral grey at two lightnesses.
BASES: tuple[str, ...] = ("obsidian", "midnight", "graphite", "chrome")

#: In picker order: one lightness, hues spaced evenly from the brand blue.
NAMED_ACCENTS: tuple[str, ...] = ("blue", "magenta", "red", "gold", "green", "cyan")

#: Derived by the browser from `ACCENT_HEX_KEY` into the same five roles.
CUSTOM_ACCENT = "custom"

ACCENTS: tuple[str, ...] = (*NAMED_ACCENTS, CUSTOM_ACCENT)

#: Each face is checked for tabular figures by the client's gate before it is offered here.
DISPLAY_FACES: tuple[str, ...] = (
    "archivo",
    "space-grotesk",
    "geist-mono",
    "manrope",
    "jetbrains-mono",
)
DISPLAY_FACE_LABELS: tuple[str, ...] = (
    "Archivo",
    "Space Grotesk",
    "Geist Mono",
    "Manrope",
    "JetBrains Mono",
)

#: DM Sans has no tabular figures; the stylesheet draws its numerals in the Main font.
BODY_FACES: tuple[str, ...] = (
    "instrument-sans",
    "inter",
    "geist",
    "public-sans",
    "sora",
    "dm-sans",
)
BODY_FACE_LABELS: tuple[str, ...] = (
    "Instrument Sans",
    "Inter",
    "Geist",
    "Public Sans",
    "Sora",
    "DM Sans",
)


def _colour(value: object) -> str:
    """A colour checked for shape only, lower-cased; legibility is the browser's derivation."""
    if not isinstance(value, str):
        raise SettingError("expected a color, written as a hash and six digits")
    text = value.strip()
    if _HEX.fullmatch(text) is None:
        raise SettingError("expected a color as a hash and six digits, using 0 to 9 and a to f")
    return text.lower()


def _swatches(value: object) -> list[str]:
    """The kept colours, each through `_colour`; a duplicate is refused rather than folded."""
    if not isinstance(value, list):
        raise SettingError("expected a list of colors")
    if len(value) > MAX_SWATCHES:
        raise SettingError(f"at most {MAX_SWATCHES} colors may be saved")
    kept = [_colour(one) for one in value]
    if len(set(kept)) != len(kept):
        raise SettingError("each color may be saved once")
    return kept


#: Six digits only: the browser's picker writes six, and one spelling reads back.
_HEX = re.compile(r"#[0-9a-fA-F]{6}")

register_setting(
    key=BASE_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default="midnight",
    choices=BASES,
    choice_labels=("Obsidian", "Midnight", "Graphite", "Chrome"),
    section="Appearance",
    label="Background",
    disclosure=(
        "Obsidian is true black. Midnight is the near-black Sift has always used. Graphite and "
        "Chrome are the same neutral gray, one darker and one a little lighter."
    ),
    help="The color everything else sits on.",
    # Not asked at first run: nothing lasting is lost by never being asked.
)

register_setting(
    key=ACCENT_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default="blue",
    choices=ACCENTS,
    choice_labels=("Blue", "Magenta", "Red", "Gold", "Green", "Cyan", "Custom"),
    section="Appearance",
    label="Accent color",
    disclosure=(
        "The six named colors have the same brightness, so none is harder to see. Custom starts "
        "from a color you choose and is adjusted to stay just as legible."
    ),
    help="The color that marks what is selected, focused or playing.",
)

register_setting(
    key=ACCENT_HEX_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    # The default accent's blue as `app.css` gives it, the one place allowed to name a colour.
    default="#2563eb",
    validator=_colour,
    section="Appearance",
    label="Custom accent color",
    help=(
        "The color a custom accent starts from, written as # and six digits. Used only while "
        "Accent color is Custom."
    ),
)

register_setting(
    key=ACCENT_SWATCHES_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    # The settings schema moves an older custom colour in as the first kept one.
    default=[],
    validator=_swatches,
    section="Appearance",
    label="Saved accent colors",
    help=(
        "Up to ten colors of your own, kept under the six named accents. Press one to wear it as "
        "your custom accent."
    ),
)

register_setting(
    key=FACE_DISPLAY_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default="archivo",
    choices=DISPLAY_FACES,
    choice_labels=DISPLAY_FACE_LABELS,
    section="Appearance",
    label="Main font",
    help=(
        "The typeface headings and numbers are set in. The sizes never change, only the "
        "shapes of the letters."
    ),
)

register_setting(
    key=FACE_BODY_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default="instrument-sans",
    choices=BODY_FACES,
    choice_labels=BODY_FACE_LABELS,
    section="Appearance",
    label="Secondary font",
    help=(
        "The typeface everything you read is set in: rows, help, the words on a button. "
        "Each of the six pairings sets both fonts, and you can change either one by itself."
    ),
)

register_setting(
    key=CLOCK_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default="12",
    choices=CLOCKS,
    choice_labels=("12-hour", "24-hour"),
    section="Appearance",
    # "Clock" under the pane's "Time" heading, so the row does not repeat the heading's word.
    label="Clock",
    help="How Sift writes a time of day, such as when a file was added or a task last ran.",
)

__all__ = [
    "ACCENTS",
    "ACCENT_HEX_KEY",
    "ACCENT_KEY",
    "ACCENT_SWATCHES_KEY",
    "BASES",
    "BASE_KEY",
    "BODY_FACES",
    "BODY_FACE_LABELS",
    "CLOCKS",
    "CLOCK_KEY",
    "CUSTOM_ACCENT",
    "DISPLAY_FACES",
    "DISPLAY_FACE_LABELS",
    "FACE_BODY_KEY",
    "FACE_DISPLAY_KEY",
    "MAX_SWATCHES",
    "NAMED_ACCENTS",
]
