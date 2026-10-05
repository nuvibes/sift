# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared by the semantic tests: the runtime stays in this process, where the stubs are."""

from __future__ import annotations

import pytest

from sift.kernel.ml import child as ml_child
from sift.kernel.ml import runtime, session
from sift.slices.semantic import embed


@pytest.fixture(autouse=True)
def models_in_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """The embedder runs models in a child process; a stub `InferenceSession` set on this
    process's `onnxruntime` never reaches one. The child itself is proved by the kernel's own
    test, against a real model, once."""
    monkeypatch.setattr(embed, "ChildRunner", runtime.Runner)
    monkeypatch.setattr(runtime, "loader", session)
    monkeypatch.setattr(ml_child, "DEVICES", ml_child.DeviceQuestion(session.providers))
