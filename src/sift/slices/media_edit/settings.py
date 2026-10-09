# SPDX-License-Identifier: AGPL-3.0-or-later
"""The compression targets, as numbers somebody can change.
Where a size limit sits is decided elsewhere and moves, so every preset's number is editable."""

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

#: `CUSTOM` has no stored number.
KEY_FOR_PRESET: Mapping[Preset, str] = {
    Preset.SMALL: SMALL_KEY,
    Preset.STANDARD: STANDARD_KEY,
    Preset.LARGE: LARGE_KEY,
    Preset.VERY_LARGE: VERY_LARGE_KEY,
}

#: What each preset starts at, in megabytes; each is a known upload limit.
DEFAULTS: Mapping[Preset, int] = {
    Preset.SMALL: 10,
    Preset.STANDARD: 50,
    Preset.LARGE: 100,
    Preset.VERY_LARGE: 1024,
}

#: The ceiling stops a typo storing a number no disk could hold.
MINIMUM_TARGET_MB = 1
MAXIMUM_TARGET_MB = 100_000

#: Whose limit each preset starts at, said under its row.
STARTS_AT: Mapping[Preset, str] = {
    Preset.SMALL: "Discord's upload limit without Nitro.",
    Preset.STANDARD: "Discord's limit with Nitro Basic, or at boost level 2.",
    Preset.LARGE: "Discord's limit at boost level 3.",
    Preset.VERY_LARGE: "Discord's limit with Nitro.",
}


MEGABYTES_PER_GIGABYTE = 1024


def said_size(megabytes: int) -> str:
    """A size in the unit its limit is published in: whole gigabytes where it is some, else MB."""
    if megabytes % MEGABYTES_PER_GIGABYTE == 0:
        return f"{megabytes // MEGABYTES_PER_GIGABYTE} GB"
    return f"{megabytes} MB"


def size_help(preset: Preset) -> str:
    """One size's help line: the number it starts at and whose limit that is."""
    return f"Starts at {said_size(DEFAULTS[preset])}, {STARTS_AT[preset]}"


#: Which format a GIF is written in; they differ 35x in size and nothing else that matters here.
GIF_FORMAT_KEY = "edit.gif_format"

#: The key's previous name, answered through `GIF_FORMAT_KEY` so older callers reach the same value.
RETIRED_GIF_FORMAT_KEY = "edit.animation_format"

#: AVIF, the smallest: most GIFs are kept in the library, and the panel names the file before
#: writing.
DEFAULT_GIF_FORMAT = "avif"

GIF_FORMATS: tuple[str, ...] = ("gif", "webp", "avif")


@dataclass(frozen=True, slots=True)
class GifFormatName:
    """What one GIF format is called where a person reads it, in one place so the menu and sentences
    agree."""

    spoken: str
    #: Declared, since the first letter does not say how the word is said.
    article: str
    #: One word, short enough for the settings menu's control column.
    note: str


#: See `operations.GIF_FORMATS` for what each is written with.
GIF_FORMAT_NAMES: Mapping[str, GifFormatName] = {
    "gif": GifFormatName(spoken="GIF", article="a", note="largest"),
    "webp": GifFormatName(spoken="WebP", article="a", note="balanced"),
    "avif": GifFormatName(spoken="AVIF", article="an", note="best"),
}

GIF_FORMAT_LABELS: tuple[str, ...] = tuple(
    f"{GIF_FORMAT_NAMES[key].spoken} ({GIF_FORMAT_NAMES[key].note})" for key in GIF_FORMATS
)


def resolve_gif_format(raw: object) -> str:
    """The stored format, or the default when the stored one is unusable."""
    return raw if isinstance(raw, str) and raw in GIF_FORMATS else DEFAULT_GIF_FORMAT


def register() -> None:
    """Declare the four targets and the GIF format. Called once, at import time."""
    register_setting(
        key=GIF_FORMAT_KEY,
        scope="app",
        default=DEFAULT_GIF_FORMAT,
        section="Editing",
        # GIF is the app's word for a moving picture; the choices name the file format.
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
            # One section with two headings: the format question is asked beside the size question.
            section="Editing",
            label=label,
            help=size_help(preset),
            minimum=MINIMUM_TARGET_MB,
            maximum=MAXIMUM_TARGET_MB,
            unit="MB",
            read_by=ReadBy.SERVER,
        )


def resolve_target_mb(raw: object, preset: Preset) -> int:
    """The stored number for a preset, or its starting value when the stored one is unusable."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULTS[preset]
    if raw < MINIMUM_TARGET_MB or raw > MAXIMUM_TARGET_MB:
        return DEFAULTS[preset]
    return raw
