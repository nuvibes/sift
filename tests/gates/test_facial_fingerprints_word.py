# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift learns from a face is a FACIAL FINGERPRINT, said in full wherever a person reads it.

"Fingerprint" already names a file's identity and a song's, so a face's is said in full, and the
code's words ("face description", "face pack") are never the screen's. The wrong phrasings are
`wrong_words` in `tests/gates/data/vocabulary.json`, proved on planted sentences; on the face and
sharing screens every "fingerprint" must be the facial one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.gates.test_one_word_per_thing import _phrases, offences

pytestmark = [pytest.mark.regression, pytest.mark.unit]

CLIENT = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: The screens about faces and about sharing them, where a fingerprint can only be a face's.
FACE_SHARING_SCREENS = (
    "lib/settings-ui/Faces.svelte",
    "lib/settings-ui/Faces.search.ts",
    "lib/components/swap/StartSwap.svelte",
    "lib/components/swap/OfferScreen.svelte",
    "lib/swap/HeldFaces.svelte",
)

#: A fingerprint not said as a facial one.
_BARE = re.compile(r"(?<!facial )\bfingerprints?\b", re.IGNORECASE)


def _bare_on(source: str, where: str) -> list[str]:
    """Every readable sentence on a face-sharing screen with a fingerprint not said in full."""
    return [
        phrase.strip()
        for phrase in _phrases(source, has_markup=not where.endswith(".ts"))
        if _BARE.search(phrase)
    ]


def test_the_wrong_names_for_a_facial_fingerprint_are_refused_on_any_screen() -> None:
    for source, where in (
        ("<p>Sift sends their face fingerprints once the codes match.</p>", "lib/swap/X.svelte"),
        ('toasts.show("Added 3 face descriptions to Ada Example.")', "lib/people/x.ts"),
        ('label="Import a face pack"', "lib/settings-ui/Faces.svelte"),
    ):
        assert offences(source, where), f"{where}: {source}"
    # And the right one passes.
    assert not offences("<p>Sift sends their facial fingerprints.</p>", "lib/swap/X.svelte")


def test_a_bare_fingerprint_for_a_face_is_refused_on_the_face_screens() -> None:
    planted = "<p>Share the fingerprints of the people Sift knows.</p>"
    assert _bare_on(planted, "lib/settings-ui/Faces.svelte")
    assert not _bare_on("<p>Share their facial fingerprints.</p>", "lib/settings-ui/Faces.svelte")


def test_every_fingerprint_on_the_face_screens_is_a_facial_one() -> None:
    complaints: list[str] = []
    for where in FACE_SHARING_SCREENS:
        source = (CLIENT / where).read_text(encoding="utf-8")
        complaints.extend(f"{where}: {phrase[:110]}" for phrase in _bare_on(source, where))
    assert not complaints, (
        "\nA face's fingerprint is a facial fingerprint, said in full:\n  "
        + "\n  ".join(complaints)
    )
