# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder is read again when the reader changes, not only when its files do.

`naming.PARSER_VERSION` sits in each folder's stored signature, so raising it makes every
signature stale once; tests hold both directions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sift.kernel.content.tree import FolderNode
from sift.slices.suggestions import service_pass
from sift.slices.suggestions.naming import PARSER_VERSION
from sift.slices.suggestions.service import _signature

pytestmark = pytest.mark.unit


def _folder() -> FolderNode:
    return FolderNode(
        id="folder-1",
        root_id="root-1",
        rel_path="Pictures/Sittings",
        name="Sittings",
        chain=("Pictures", "Sittings"),
        files=42,
        newest=1_700_000_000,
    )


def test_a_folders_signature_carries_the_readers_generation() -> None:
    """First in the string, so a signature written by any build before this one (which opened
    with a file count) can never be read as one written under a version."""
    assert _signature(_folder(), None).startswith(f"v{PARSER_VERSION}:")


def test_a_signature_from_an_older_reader_is_stale_under_a_newer_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signature from an older reader is stale under a newer one. Patched on the module that
    builds the signature, since the name is bound at import."""
    folder = _folder()
    before = _signature(folder, None)

    monkeypatch.setattr(service_pass, "PARSER_VERSION", PARSER_VERSION + 1)
    after = _signature(folder, None)

    assert before != after, "an improved reader has to make an unchanged folder stale"
    assert after.startswith(f"v{PARSER_VERSION + 1}:")


def test_the_signature_still_moves_with_the_folder_itself() -> None:
    """The signature still moves with the folder itself."""
    folder = _folder()
    base = _signature(folder, None)

    assert _signature(replace(folder, files=43), None) != base
    assert _signature(replace(folder, newest=1_700_000_001), None) != base
    assert _signature(folder, _Stamp("faces-2")) != base
    # And the same face stamp twice is the same signature, or nothing would ever be skipped.
    assert _signature(folder, _Stamp("faces-2")) == _signature(folder, _Stamp("faces-2"))


class _Stamp:
    """How far a face pass has got with one folder, as the pass reads it."""

    def __init__(self, text: str) -> None:
        self._text = text

    def as_text(self) -> str:
        return self._text
