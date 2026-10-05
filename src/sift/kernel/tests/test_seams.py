# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the swap-point interfaces: no implementation grows inside the seams package, and the
database interface is checked against the real database."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType

import pytest

from sift.kernel import seams
from sift.kernel.db import Database
from sift.kernel.seams import DatabaseSeam

EXPECTED_SEAMS = {
    "RemovedMembersSeam",
    "MetadataSourceSeam",
    "DatabaseSeam",
    "DownloaderSeam",
    "DuplicatePairSeam",
    "FaceEvidenceSeam",
    # Filing what a dropped link fetched under the thing it was dropped ON. A seam published in the
    # package's `__all__` belongs on this list too: the list is the point of the file.
    "FilingSeam",
    "ForgetGoneSeam",
    "InferenceSeam",
    "LibraryWriteSeam",
    "PackSourceSeam",
    "PlaybackCacheSeam",
    "RecognitionSeam",
    "ReindexSeam",
    "SettingsSeam",
    "StillSeam",
    "StorageSeam",
    "SemanticSeam",
    "PhotoSetSeam",
    "DisagreementSeam",
    "SavedFilterSeam",
    "BoxPicturesSeam",
    "CreatorPicturesSeam",
    "UrlImporter",
    # The one query language, as the grid and a collection's Files tab both read it, and the shape
    # it answers with. Declared once here and published under `wiring.FILTER_ENGINE`, rather than
    # by the grid and again, under the same part name, by collections.
    "FilterEngine",
    "Narrowed",
}


def _seam_modules() -> list[ModuleType]:
    """Every module in the seams package, including the package itself."""
    modules = [seams]
    for info in pkgutil.iter_modules(seams.__path__, seams.__name__ + "."):
        modules.append(importlib.import_module(info.name))
    return modules


def _classes_defined_in(module: ModuleType) -> list[tuple[str, type]]:
    return [
        (name, obj)
        for name, obj in vars(module).items()
        if isinstance(obj, type) and obj.__module__ == module.__name__
    ]


def _is_protocol(cls: type) -> bool:
    return bool(getattr(cls, "_is_protocol", False))


@pytest.mark.regression
def test_no_seam_has_an_implementation_in_the_package() -> None:
    """Every class defined under seams/ is a Protocol: an interface with no body. A concrete
    class here would be a placeholder backend growing where a boundary is supposed to be, so it
    is the thing this test exists to reject."""
    offenders = [
        f"{module.__name__}.{name}"
        for module in _seam_modules()
        for name, cls in _classes_defined_in(module)
        if not _is_protocol(cls)
    ]
    assert not offenders, f"seams/ must contain only Protocols, found implementations: {offenders}"


@pytest.mark.unit
def test_every_seam_is_present_and_no_others() -> None:
    """The seams are exactly the expected ones: one vanishing takes its boundary, one appearing is
    an
    interface nobody reviewed."""
    defined = {
        name
        for module in _seam_modules()
        for name, cls in _classes_defined_in(module)
        if _is_protocol(cls)
    }
    assert defined == EXPECTED_SEAMS


@pytest.mark.unit
def test_every_seam_is_published() -> None:
    """`__all__` says what the package offers, and it drifted: the one seam with a real
    implementation behind it was left out of it while every unbacked one was listed. Nothing broke,
    because everything imports these by name, which is exactly why nothing reported it either."""
    assert set(seams.__all__) == EXPECTED_SEAMS


@pytest.mark.unit
def test_the_real_database_satisfies_the_database_seam(tmp_path: Path) -> None:
    """The interface is a true description of the database that exists, not a wish. Checked both
    ways: the annotation is a static structural check, the isinstance a runtime one."""
    db = Database(tmp_path / "x.sqlite3")

    seam: DatabaseSeam = db
    assert isinstance(seam, DatabaseSeam)


@pytest.mark.unit
def test_the_database_seam_does_not_expose_a_raw_connection() -> None:
    """The single-writer transaction hands out a live driver connection, which no other database
    could return in the same shape. Keeping it out of the interface is what keeps this a
    description of a database rather than of SQLite."""
    assert hasattr(DatabaseSeam, "execute")
    assert hasattr(DatabaseSeam, "fetch_all")
    assert not hasattr(DatabaseSeam, "write")
    assert not hasattr(DatabaseSeam, "read")
