# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person Sift makes from facial fingerprints says so, in History and on the Created by line."""

from __future__ import annotations

from sift.kernel.access.history_line import VIAS
from sift.kernel.access.sentences_who import from_pass
from sift.kernel.vocabulary import MADE_VIAS, VIA_FACES, VIA_FACIAL_FINGERPRINTS


def test_the_word_is_its_own_and_is_said_in_words() -> None:
    assert VIA_FACIAL_FINGERPRINTS in MADE_VIAS
    assert VIA_FACIAL_FINGERPRINTS != VIA_FACES
    assert from_pass(VIA_FACIAL_FINGERPRINTS) == " from facial fingerprints"
    # A History line by it draws its mark, as a line by any pass that files does.
    assert VIA_FACIAL_FINGERPRINTS in VIAS
