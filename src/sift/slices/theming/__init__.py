# SPDX-License-Identifier: AGPL-3.0-or-later
"""Theming: what Sift looks like, as five preferences the browser acts on.

It has no router, no schema and no service, and that is the whole shape of it. Almost everything a
theme actually does happens in the stylesheet: `frontend/src/app.css` holds four bases, six named
accents, the faces headings may be set in and the faces everything else may be read in, and the
browser picks one of each by stamping four attributes on the page. The server's entire part is to remember which, per user, so the
choice follows somebody to another device instead of living only in the browser that made it.

The fifth preference is the odd one and it is the seventh accent: a colour somebody chose, which
cannot be a block in a stylesheet written months earlier. The browser works the five values a
`[data-accent]` block would set out of it, holding each one to the same contrast floors the six
named accents were solved to. The server stores the colour and checks its SHAPE, which is the whole
of what a server can honestly check about a colour: every six-digit colour is a colour, and it is
the derivation rather than the value that makes one legible. Beside it are the colours somebody
kept, up to ten, each checked the same way; one is worn by copying it into the colour in force.

It is its own module rather than three more registrations inside another feature for the reason
every other slice owns its own settings: `settings_hub` holds the one instance-wide setting that
must exist before any feature is built, and a look-and-feel preference is not that. Importing this
package registers them, the same as every other slice.

Nothing here reads a value. `read_by=CLIENT` says so, and the gate that refuses a setting nothing
acts on checks the client for these rather than the server.
"""

from __future__ import annotations

import re

from sift.kernel.settings_registry import ReadBy, SettingError, register_setting

#: The keys, named here and read by the browser. All but the colour are strings with a fixed set of
#: choices rather than free text: a value nothing in the stylesheet matches would leave the page on
#: the default with no error anywhere, which is the failure a fixed set exists to make impossible.
BASE_KEY = "appearance.theme_base"
ACCENT_KEY = "appearance.theme_accent"

#: The colour the seventh accent is derived from, `#rrggbb`.
#:
#: ITS OWN KEY, AND THAT IS ONE KEY PER FACT rather than two keys for one. Which accent is in force
#: and what the custom colour is are two separate answers: one key holding either a name or a colour
#: would forget the colour the moment somebody tried one of the six and came back, and it could not
#: carry `choices` either, so the settings screen would lose the menu it draws from and this
#: module would lose the check that refuses a name no stylesheet matches.
ACCENT_HEX_KEY = "appearance.theme_accent_hex"

#: The colours somebody kept beside the six named accents, in the order they arranged them: a list
#: of `#rrggbb`, at most `MAX_SWATCHES`, each one once.
#:
#: ITS OWN KEY rather than a longer value under `ACCENT_HEX_KEY`, for the reason that key is its own:
#: the colour in force and the colours kept are two answers. Wearing a kept colour copies it into the
#: colour in force, so it takes the one path a custom accent takes and is derived the same way; a
#: kept colour is never painted from here.
ACCENT_SWATCHES_KEY = "appearance.theme_accent_swatches"

#: How many colours one person may keep. A row of ten under the six is the most that still reads as
#: a row of choices rather than as a palette to search.
MAX_SWATCHES = 10

#: The face the headings and the numbers are set in, and the face everything else is read in.
#:
#: TWO KEYS because they are two choices, and each holds a face OF ITS OWN ROLE by the face's own
#: name. A FAMILY's name on both, meaning its display face on the first key and its text face on
#: the second, could not offer one display face over two text faces without that face appearing
#: twice in its menu. The settings component's schema carries a stored family name to the face it
#: meant in each role, at version 14.
FACE_DISPLAY_KEY = "appearance.theme_face_display"
FACE_BODY_KEY = "appearance.theme_face_body"

#: How a time of day is written everywhere Sift shows one: on a twelve-hour clock with AM and PM, or
#: on a twenty-four-hour one. Per person, like the rest of how the app looks, and read only by the
#: browser, which writes every time through one formatter (`lib/shell/when.ts`).
CLOCK_KEY = "appearance.clock"

#: The two clocks, twelve-hour first because it is the default.
CLOCKS: tuple[str, ...] = ("12", "24")

#: The bases, darkest first. `obsidian` is true black; `midnight` is the near-black the app has
#: always been; `graphite` and `chrome` are the same neutral grey at two lightnesses.
BASES: tuple[str, ...] = ("obsidian", "midnight", "graphite", "chrome")

#: The named accents, in the order the picker draws them. One family: the same lightness, hues
#: spaced evenly around the circle, starting from the blue the brand mark is drawn in.
NAMED_ACCENTS: tuple[str, ...] = ("blue", "magenta", "red", "gold", "green", "cyan")

#: The seventh: whatever colour is stored under `ACCENT_HEX_KEY`, worked out into the same five
#: roles by the browser.
CUSTOM_ACCENT = "custom"

ACCENTS: tuple[str, ...] = (*NAMED_ACCENTS, CUSTOM_ACCENT)

#: The faces headings and numbers may be set in, and what each is called, so the settings screen
#: names a choice rather than showing the stored word. Every face in either list is checked for
#: tabular figures by the client's own gate before it may be offered here. Written out beside the
#: keys because this module is where a person's word for a thing lives; the stylesheet holds the
#: font stacks.
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

#: The faces everything else is read in, the same way. DM Sans has no figures that hold still, and
#: the stylesheet draws its numerals in the Main face in force, which is how it passes.
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
    """A colour, checked for SHAPE and nothing else.

    Shape is all a server can honestly check here. Every six-digit colour is a colour, and whether
    one is LEGIBLE is not a fact about the value: it depends on the background it lands on, and the
    browser answers it by deriving a fill, a hover, a text shade, a tint and a ring that each clear
    the design system's floors against the base in force. A server-side legibility rule would be a
    second, weaker copy of that arithmetic, disagreeing with the real one about which colours are
    allowed.

    Lower-cased on the way in so the stored value has one spelling. Two rows differing only in the
    case of a letter are the same colour and would look like two different choices in a log.
    """
    if not isinstance(value, str):
        raise SettingError("expected a color, written as a hash and six digits")
    text = value.strip()
    if _HEX.fullmatch(text) is None:
        raise SettingError("expected a color as a hash and six digits, using 0 to 9 and a to f")
    return text.lower()


def _swatches(value: object) -> list[str]:
    """The kept colours, each checked for shape the way the one colour is, at most ten, each once.

    Each one goes through `_colour`, so a kept colour has the same one spelling as the colour in
    force and wearing it moves nothing but where the value lives. Duplicates are compared in that
    spelling: `#A831B7` and `#a831b7` are one colour and would be two identical dots in the row.
    Refused rather than quietly folded together, because a client that sends a duplicate has lost
    track of the list and should hear so.
    """
    if not isinstance(value, list):
        raise SettingError("expected a list of colors")
    if len(value) > MAX_SWATCHES:
        raise SettingError(f"at most {MAX_SWATCHES} colors may be saved")
    kept = [_colour(one) for one in value]
    if len(set(kept)) != len(kept):
        raise SettingError("each color may be saved once")
    return kept


#: A hash and six hexadecimal digits. Not three: the browser's own picker always writes six, and one
#: spelling stored is one spelling to read back.
_HEX = re.compile(r"#[0-9a-fA-F]{6}")

register_setting(
    key=BASE_KEY,
    # Drawn by the browser and acted on by the browser. The server keeps it so the choice follows
    # the user to another device, and reads it never.
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
    # Not asked at first run: nothing lasting is lost by never being asked, and Appearance is the
    # pane people open first anyway.
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
    # The blue everything else starts from, so choosing "Your own" begins where the app already is
    # rather than on a colour nobody picked. It is the same value `app.css` gives the default accent
    # (the one place in this repository allowed to name a colour) and the client's own gate
    # would refuse a second copy of it on that side.
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
    # Nothing kept until somebody keeps something. The settings schema carries a custom colour
    # chosen before this key existed into the list as its first colour, so nobody loses one.
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
