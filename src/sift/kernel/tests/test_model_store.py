# SPDX-License-Identifier: AGPL-3.0-or-later
"""The device's model store is where every reader looks, and never a library's own data folder.

The one-time move of a library's own copies into the store is retired (see `kernel.ml.store`);
what stays is the rule it served: the face, Smart Search and watermark models and the graphics-card
runtime are read from `Settings.models_dir`, once per device, whichever library asks.
"""

from __future__ import annotations

from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.ml import accel, store
from sift.kernel.ml.weights import WeightStore


def test_every_reader_reads_the_store(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    for feature in ("faces", "semantic", "watermarks"):
        where = WeightStore(settings, feature).directory()
        assert where == settings.models_dir / feature
        assert settings.data_dir / feature / store.LIBRARY_MODELS not in (where, *where.parents)
    runtime = accel.directory(settings)
    assert runtime.parent.parent == settings.models_dir
    assert runtime.parent.name == accel.FOLDER
