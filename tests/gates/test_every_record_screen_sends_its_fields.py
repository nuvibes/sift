# SPDX-License-Identifier: AGPL-3.0-or-later
"""The screen that saves a record mentions every field that record can edit.

`test_every_editable_field_can_be_written` holds the server; this holds the screen, where a
declared, accepted, drawn field can still be left out of the object the page sends. A file's
record is generated from the registry at both ends; the other screens' saves are hand-written
calls, so this reads each page as TEXT for every field's name. Crude on purpose: a forgotten
field is nowhere on the page at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.records import Subject, fields_of

pytestmark = [pytest.mark.gate, pytest.mark.unit]

_CLIENT = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: The screen that saves each subject's record.
_SCREENS: dict[Subject, str] = {
    Subject.ASSET: "lib/components/AssetView.svelte",
    Subject.PERSON: "routes/people/[id]/+page.svelte",
    Subject.SITE: "routes/sites/[id]/+page.svelte",
    Subject.TAG: "routes/tags/[id]/+page.svelte",
    Subject.PHOTO_SET: "routes/photo-sets/[id]/+page.svelte",
    Subject.SONG: "routes/songs/[id]/+page.svelte",
    Subject.USERNAME: "lib/components/entity/UsernameLines.svelte",
}

#: Screens that build their save FROM the registry, and the expression that proves it still does.
#: Their fields are exempt from the name check except those the page still spells by hand (a file's
#: `filename`, sent through the rename route).
_GENERATED: dict[Subject, tuple[str, ...]] = {
    Subject.ASSET: ("fields.drawn('asset')", "fields.of('asset')"),
}

#: Of a generated subject's fields, the ones the page still names itself.
_STILL_SPELLED: dict[Subject, frozenset[str]] = {
    Subject.ASSET: frozenset({"filename"}),
}

#: Fields a screen legitimately never names, each with why: an unexplained exception is where a dead
#: box hides.
_NOT_NAMED: dict[Subject, dict[str, str]] = {
    Subject.USERNAME: {
        # A username has no page: a person types the Site's number into the sheet on their Sites
        # tab; the other two come from a download or a stash-box.
        "number": "typed into the sheet's own `typed` box and sent as `{ number }`",
        "display_name": "no screen draws a box for it; a download or a stash-box fills it",
        "links": "no screen draws a box for it; a download or a stash-box fills it",
    },
    Subject.PERSON: {
        key: "written through `recordFrom`, which walks the registry"
        for key in (
            "birth_date",
            "country",
            "hair_color",
            "measurements",
            "disambiguation",
            "gender",
            "ethnicity",
            "eye_color",
            "height_cm",
            "breast_type",
            "career_start_year",
            "career_end_year",
            "tattoos",
            "piercings",
            "pmv_creator",
        )
    },
}


#: Where a field's value is read out of the draft: `draft.x`, `draft['x']`, `draft["x"]`.
def _named_in(source: str, key: str) -> bool:
    return bool(
        re.search(rf"\bdraft\.{re.escape(key)}\b", source)
        or re.search(rf"\bdraft\[[\"']{re.escape(key)}[\"']\]", source)
    )


def test_every_screen_is_there() -> None:
    """Every screen exists, or every check below is vacuously true."""
    for subject, where in _SCREENS.items():
        assert (_CLIENT / where).is_file(), f"{subject.value}'s record screen is not at {where}"


def test_every_subject_has_a_screen() -> None:
    """Every subject with a record has a screen here."""
    for subject in Subject:
        assert subject in _SCREENS, f"`{subject.value}` has a record and no screen named here"


def test_a_generated_screen_still_generates() -> None:
    """A generated screen still generates, since its fields are exempt from the name check."""
    for subject, expressions in _GENERATED.items():
        source = (_CLIENT / _SCREENS[subject]).read_text(encoding="utf-8")
        for expression in expressions:
            assert expression in source, (
                f"`{_SCREENS[subject]}` is supposed to build {subject.value}'s save from the"
                f" registry and no longer contains `{expression}`. Either it still does and this"
                " needs updating, or it has gone back to a hand-written list of fields."
            )


def test_every_editable_field_is_read_out_of_the_draft() -> None:
    for subject, where in _SCREENS.items():
        source = (_CLIENT / where).read_text(encoding="utf-8")
        excused = _NOT_NAMED.get(subject, {})
        generated = subject in _GENERATED
        spelled = _STILL_SPELLED.get(subject, frozenset())
        for one in fields_of(subject):
            if not one.editable or one.key in excused:
                continue
            if generated and one.key not in spelled:
                continue
            assert _named_in(source, one.key), (
                f"`{subject.value}.{one.key}` is editable and `{where}` never reads it out of the"
                " draft, so the box is drawn, takes a value, reports success and writes nothing."
                " Send it, or add it to _NOT_NAMED with what does."
            )


def test_nothing_is_excused_by_accident() -> None:
    """No exception names a field that does not exist."""
    for subject, excused in _NOT_NAMED.items():
        declared = {one.key for one in fields_of(subject)}
        for key in excused:
            assert key in declared, f"`{subject.value}.{key}` is excused and is not a field"
