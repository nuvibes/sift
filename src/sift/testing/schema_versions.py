# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers for writing a library as an older Sift left it: one component's version moved back.

Two different pasts, and a test has to choose which it means. One version behind its pin is a
library this build brings forward (it reads as older). Below a component's baseline is one no step
in this build can bring forward (it reads as unreadable, with the reason in a sentence). Which
component gives which is the registry's answer, so the component is chosen here and never named by
a test.
"""

from __future__ import annotations

from sift.kernel.db import registered_components, too_old_to_bring_forward


def wind_one_back(versions: dict[str, int]) -> str:
    """Put one component one version behind its pin, and say which one.

    Only a component whose baseline sits below its pin can be brought forward from one behind;
    every other component is refused there, which is the baseline refusal's own test. So the
    component is chosen, not named: the first that a boot would still carry forward.
    """
    for name in sorted(versions):
        trial = {**versions, name: versions[name] - 1}
        if too_old_to_bring_forward(trial) is None:
            versions[name] = trial[name]
            return name
    raise AssertionError("no component has a baseline below its pin")


def wind_below_a_baseline(versions: dict[str, int]) -> str:
    """Put one component one version below where this build starts it, and say which one."""
    components = registered_components()
    for name in sorted(versions):
        baseline = components[name].baseline if name in components else None
        if baseline is not None and baseline > 1:
            versions[name] = baseline - 1
            return name
    raise AssertionError("no component has a baseline above its first version")
