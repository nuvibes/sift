# SPDX-License-Identifier: AGPL-3.0-or-later
"""The compression targets, as numbers somebody can change.

**The names are a convenience. The numbers are the contract.** A target exists because somewhere
will not accept a file above a certain size, and where that line sits is not Sift's to know: it is
decided elsewhere, by somebody else, and it moves. A limit written into the source as though it
were permanent becomes wrong quietly: the preset keeps its name, keeps working, and starts
producing files that are refused, with nothing anywhere saying why.

So four numbers ship as starting points and every one of them is editable. The presets are named
for how big they are, not for anywhere they might be sent, because a name that points at a service
is a name that has to be maintained against that service.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.settings_registry import ReadBy, register_setting, retire_setting

BYTES_PER_MEGABYTE = 1024 * 1024


class Preset(StrEnum):
    """The named targets. `CUSTOM` carries its number in the request instead of in a setting."""

    SMALL = "small"
    STANDARD = "standard"
    LARGE = "large"
    VERY_LARGE = "very_large"
    CUSTOM = "custom"


SMALL_KEY = "compress.target_small_mb"
STANDARD_KEY = "compress.target_standard_mb"
LARGE_KEY = "compress.target_large_mb"
VERY_LARGE_KEY = "compress.target_very_large_mb"

#: Which setting holds each preset's number. `CUSTOM` is absent on purpose: it has no stored
#: number, and a caller asking for its key is a caller that has not handled the custom case.
KEY_FOR_PRESET: Mapping[Preset, str] = {
    Preset.SMALL: SMALL_KEY,
    Preset.STANDARD: STANDARD_KEY,
    Preset.LARGE: LARGE_KEY,
    Preset.VERY_LARGE: VERY_LARGE_KEY,
}

#: What each preset starts at, in megabytes. Starting points, not truths. See the module note.
#: Each is one of Discord's upload limits, which is what `STARTS_AT` says on screen.
DEFAULTS: Mapping[Preset, int] = {
    Preset.SMALL: 10,
    Preset.STANDARD: 50,
    Preset.LARGE: 100,
    # 1 GB, Discord's limit with Nitro since it was raised from 500 MB. A megabyte here is
    # `BYTES_PER_MEGABYTE`, so a gigabyte is 1024 of them.
    Preset.VERY_LARGE: 1024,
}

#: The range a target may be set to, in megabytes. One megabyte is already smaller than anything
#: worth producing and the arithmetic says so on its own; the ceiling is there so a typo cannot
#: store a number no disk could hold.
MINIMUM_TARGET_MB = 1
MAXIMUM_TARGET_MB = 100_000

#: Whose limit each preset starts at, said under its row. The number is written from `DEFAULTS`
#: so the sentence cannot name a size the preset does not start at. It names where the number came
#: from, not what the row is called, so the presets keep their plain size names.
STARTS_AT: Mapping[Preset, str] = {
    Preset.SMALL: "Discord's upload limit without Nitro.",
    Preset.STANDARD: "Discord's limit with Nitro Basic, or at boost level 2.",
    Preset.LARGE: "Discord's limit at boost level 3.",
    Preset.VERY_LARGE: "Discord's limit with Nitro.",
}


#: Megabytes in a gigabyte, for a size said the way its limit is published: "1 GB", not "1024 MB".
MEGABYTES_PER_GIGABYTE = 1024


def said_size(megabytes: int) -> str:
    """A size in the unit its limit is published in: whole gigabytes where it is some, else MB."""
    if megabytes % MEGABYTES_PER_GIGABYTE == 0:
        return f"{megabytes // MEGABYTES_PER_GIGABYTE} GB"
    return f"{megabytes} MB"


def size_help(preset: Preset) -> str:
    """One size's help line: the number it starts at and whose limit that is."""
    return f"Starts at {said_size(DEFAULTS[preset])}, {STARTS_AT[preset]}"


#: Which format "make a GIF" writes.
#:
#: A setting rather than a fixed answer because the three differ by 35x in size and by exactly
#: nothing else that matters here: all three animate, and Sift's own ingress files an animated one
#: of any of them as a GIF. Somebody keeping GIFs on their own disk wants AVIF;
#: somebody who saves them to send elsewhere wants the one that opens anywhere.
GIF_FORMAT_KEY = "edit.gif_format"

#: The key this setting was stored under before, answered through `GIF_FORMAT_KEY` so an older
#: caller, a saved link and a History row naming it still reach the one stored value. The settings
#: schema moves an existing row across; see `settings_hub.schema`.
RETIRED_GIF_FORMAT_KEY = "edit.animation_format"

#: AVIF, which is the smallest of the three by a long way.
#:
#: The three differ by 35x in size and by nothing else this slice decides: all three animate, and
#: the ingress files an animated one of any of them under the same kind, so a copy made here comes
#: back into the library as a GIF whichever one it is. So the question the default answers
#: is what a GIF is FOR, and nearly all of them are made to keep: built from a file
#: already in the library, landing back beside it. The menu says the same in one word each:
#: largest, balanced, best.
#:
#: GIF is not the default although "make a GIF" names the format and a `.gif` opens in anything:
#: the surprise is answered where it happens (the panel names the file it is about to write,
#: extension and all, before anything is written), and opening anywhere is a property of a file
#: that LEAVES this machine, which is the smaller half of what GIFs are made for and is one
#: setting away for the person it is not.
DEFAULT_GIF_FORMAT = "avif"

GIF_FORMATS: tuple[str, ...] = ("gif", "webp", "avif")


@dataclass(frozen=True, slots=True)
class GifFormatName:
    """What one GIF format is called where a person reads it.

    Together in one place because the same format is read in two shapes (in the menu, and inside
    a sentence), and two copies drift: a refusal that built its sentence by upper-casing the
    stored key and putting "A" in front of it would deliver a ceiling as "A AVIF can be at most
    60 seconds" while the menu beside it said WebP.
    """

    #: The name as it is written, which is not the key: `webp` is WebP wherever anybody reads it.
    spoken: str
    #: The article the word takes. Declared rather than read off the first letter, because the
    #: letter does not say how the word is said: AVIF takes "an", and a format added here could
    #: begin with a consonant and take one as well (an MP4).
    article: str
    #: The one word that chooses between the three, for the menu.
    #:
    #: SHORT ENOUGH TO BE READ IN THE MENU: the trigger is the width of the settings control column,
    #: and a phrase like "GIF - opens anywhere, and by far the largest" is 272px of text in a 188px
    #: box, so the chosen answer shows as an ellipsis. `e2e/settings-alignment.spec.ts` checks it.
    note: str


#: How each format is spoken about. The sizes behind the notes are measured. See
#: `operations.GIF_FORMATS` for what each one is actually written with.
GIF_FORMAT_NAMES: Mapping[str, GifFormatName] = {
    "gif": GifFormatName(spoken="GIF", article="a", note="largest"),
    "webp": GifFormatName(spoken="WebP", article="a", note="balanced"),
    "avif": GifFormatName(spoken="AVIF", article="an", note="best"),
}

#: What the menu offers, built from the table above so the spelling exists once.
GIF_FORMAT_LABELS: tuple[str, ...] = tuple(
    f"{GIF_FORMAT_NAMES[key].spoken} ({GIF_FORMAT_NAMES[key].note})" for key in GIF_FORMATS
)


def resolve_gif_format(raw: object) -> str:
    """The stored format, or the default when the stored one is unusable.

    A bad row falls back rather than failing, the same as a target above: an editor that cannot be
    opened because one row is the wrong type is worse than one that writes the format it shipped
    with.
    """
    return raw if isinstance(raw, str) and raw in GIF_FORMATS else DEFAULT_GIF_FORMAT


def register() -> None:
    """Declare the four targets and the GIF format. Called once, at import time."""
    register_setting(
        key=GIF_FORMAT_KEY,
        scope="app",
        default=DEFAULT_GIF_FORMAT,
        section="Editing",
        # "GIF" is what the app calls a moving picture on screen, whatever file format it is
        # written in, so the label names the kind and the choices name the format.
        label="Save GIFs as",
        help=(
            "The file format Sift uses when you turn part of a video into a GIF. A .gif file "
            "opens anywhere; WebP and AVIF are much smaller."
        ),
        choices=list(GIF_FORMATS),
        choice_labels=list(GIF_FORMAT_LABELS),
        read_by=ReadBy.SERVER,
    )
    retire_setting(
        RETIRED_GIF_FORMAT_KEY,
        into=(GIF_FORMAT_KEY,),
        read=lambda values: values[0],
        write=lambda value, _current: (value,),
        why="the stored word for a moving picture is GIF, as it's on screen",
    )
    for preset, label in (
        (Preset.SMALL, "Small"),
        (Preset.STANDARD, "Standard"),
        (Preset.LARGE, "Large"),
        (Preset.VERY_LARGE, "Very large"),
    ):
        register_setting(
            key=KEY_FOR_PRESET[preset],
            scope="app",
            default=DEFAULTS[preset],
            # One section with two headings, not two sections. Nobody hunting for which format an
            # GIF is saved in starts under a heading about making files smaller, so the
            # format has a heading of its own, but not a whole rail slot: one setting behind one
            # door and four behind another, for two questions a person asks in the same breath.
            section="Editing",
            label=label,
            help=size_help(preset),
            minimum=MINIMUM_TARGET_MB,
            maximum=MAXIMUM_TARGET_MB,
            unit="MB",
            read_by=ReadBy.SERVER,
        )


def resolve_target_mb(raw: object, preset: Preset) -> int:
    """The stored number for a preset, or its starting value when the stored one is unusable.

    A bad row falls back rather than failing. The alternative is a compression screen that cannot
    be opened because one number in the database is the wrong type, which is a worse outcome than
    working to the number the preset shipped with.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULTS[preset]
    if raw < MINIMUM_TARGET_MB or raw > MAXIMUM_TARGET_MB:
        return DEFAULTS[preset]
    return raw
