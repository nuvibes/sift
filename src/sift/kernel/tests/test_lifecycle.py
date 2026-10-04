# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking the backend to stop so that its supervisor starts it again: it REFUSES when nothing
supervises it, and the code it leaves says the stop was asked for."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel import lifecycle


@pytest.fixture(autouse=True)
def _clean() -> object:
    """Module state, and the tests share a process."""
    lifecycle.forget()
    yield
    lifecycle.forget()


def test_a_backend_nobody_is_watching_refuses_rather_than_stopping() -> None:
    """The one that keeps a library on the air. Run by hand, or in a container with no restart
    policy, stopping would leave it stopped, with a screen still saying "restarting"."""
    assert lifecycle.can_restart() is False
    assert lifecycle.ask_to_restart() is False
    assert lifecycle.was_asked_to_restart() is False


def test_a_supervised_backend_is_asked_to_stop_the_clean_way() -> None:
    stopped: list[bool] = []
    lifecycle.stops_with(lambda: stopped.append(True))

    assert lifecycle.can_restart() is True
    assert lifecycle.ask_to_restart() is True
    assert stopped == [True]


def test_the_stop_is_remembered_so_the_exit_code_can_say_so() -> None:
    """`main` reads this after the server returns. Without it the process exits 0, the supervisor
    reads that as an ordinary shutdown, and the backend never comes back."""
    lifecycle.stops_with(lambda: None)
    lifecycle.ask_to_restart()

    assert lifecycle.was_asked_to_restart() is True


def test_a_refused_ask_is_not_remembered_as_one() -> None:
    """Otherwise a backend that refused would still exit as though it had been asked."""
    lifecycle.ask_to_restart()

    assert lifecycle.was_asked_to_restart() is False


def test_the_two_halves_agree_on_the_number() -> None:
    """The exit code agrees with the desktop shell's, or a deliberate restart counts as a crash."""
    shell = Path(__file__).resolve().parents[4] / "desktop" / "src" / "backend.ts"
    if not shell.is_file():
        pytest.skip("no desktop shell beside this tree")

    said = shell.read_text(encoding="utf-8")

    assert f"ASKED_TO_RESTART = {lifecycle.RESTART_EXIT_CODE}" in said
