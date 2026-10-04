# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every setting the app declares is fit to put on a screen.

The registry refuses a blank label or help at registration, so this cannot fail today. It exists for
the day someone adds a validator override or a new registration path that sidesteps that check: a
settings screen is generated from these strings, and a `snake_case` key with no words around it is
unusable by the person a self-hosted app is for. It walks what the whole app actually registers, so
it grows to cover every future setting with no edit here.
"""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import SECTIONS, Scope, registered_settings

pytestmark = [pytest.mark.regression]


@pytest.fixture(autouse=True)
def _load_every_slice() -> None:
    """Import the app so every slice has registered its settings before the registry is read."""
    import sift.main  # noqa: F401


def test_at_least_one_setting_is_registered() -> None:
    """A registry that walks to nothing would let every assertion below pass vacuously."""
    assert registered_settings()


def test_every_registered_setting_is_fit_for_a_screen() -> None:
    for setting in registered_settings().values():
        assert setting.label.strip(), f"{setting.key} has no label"
        assert setting.help.strip(), f"{setting.key} has no help text"
        assert setting.section in SECTIONS, f"{setting.key} names an unknown section"
        assert isinstance(setting.scope, Scope), f"{setting.key} has an invalid scope"


def test_no_setting_uses_a_raw_key_as_its_label() -> None:
    """A label that is just the key is the failure this is really guarding against: it passes the
    non-empty check and is still unreadable."""
    for setting in registered_settings().values():
        assert setting.label != setting.key, f"{setting.key} uses its key as its label"
