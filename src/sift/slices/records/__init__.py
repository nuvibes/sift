# SPDX-License-Identifier: AGPL-3.0-or-later
"""The record surface: one description of every field, served to every screen that draws one.

The fields themselves are declared in the kernel (`kernel/records.py`), where they can be reached
without importing a feature. This slice is the way a screen asks for them. It owns no table, no
service and no write path.
"""

from __future__ import annotations

from sift.kernel.settings_registry import ReadBy, register_setting
from sift.slices.records.models import FieldDescription, FieldRegistry
from sift.slices.records.router import router

#: Which system of measurement a record is READ in. `kernel/records.py` for what is stored.
UNITS_KEY = "appearance.units"

#: The two answers, in the order the menu offers them.
UNITS = ("metric", "imperial")

# How a measured field is READ, never how it is stored: a height is one fact in whole centimetres,
# which every filter and facet is built on, and only the reading differs per person. `height_cm`
# is the one field with an imperial form; free-text `measurements` is left as typed. The arithmetic
# is `lib/shell/measure.ts`.
register_setting(
    key=UNITS_KEY,
    # Kept on the server only so the choice follows the user to another device.
    read_by=ReadBy.CLIENT,
    scope="user",
    default="imperial",
    choices=UNITS,
    choice_labels=("Centimeters", "Feet and inches"),
    section="Appearance",
    # Not "Measurements", which is already a field on a person's record.
    label="Units",
    help="How heights are shown. Sift stores them in centimeters either way.",
)

__all__ = ["UNITS", "UNITS_KEY", "FieldDescription", "FieldRegistry", "router"]
