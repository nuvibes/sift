# SPDX-License-Identifier: AGPL-3.0-or-later
"""The link to the desktop app: present only with both halves, and a refusal is no answer."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from sift.kernel.config import Settings
from sift.slices.desktop.shell import NoShell, ShellLink

pytestmark = pytest.mark.unit


def test_the_link_needs_both_the_address_and_the_secret(tmp_path: Path) -> None:
    both = Settings(data_dir=tmp_path, shell_url="http://127.0.0.1:5", shell_token=SecretStr("s"))
    assert ShellLink.from_settings(both).present
    assert not ShellLink.from_settings(Settings(data_dir=tmp_path)).present
    assert not ShellLink(url="http://127.0.0.1:5", token=None).present
    assert not ShellLink(url=None, token="s").present


async def test_no_link_is_no_shell_and_nothing_is_asked() -> None:
    with pytest.raises(NoShell):
        await ShellLink(url=None, token=None).facts()


def test_the_secret_never_prints(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path, shell_url="http://127.0.0.1:5", shell_token=SecretStr("launch")
    )
    assert "launch" not in repr(settings)
