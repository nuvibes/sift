# SPDX-License-Identifier: AGPL-3.0-or-later
"""The host-console password reset.

The reset is the recovery path for a forgotten password, and it makes one deliberate trade: the
saved site logins are lost, because they were locked with the old password. Everything else must
survive. These prove both halves: the new password works and the old logins are gone, and the
rest of the library is untouched.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from sift.kernel.db import Database
from sift.kernel.secrets import open_secret, seal_secret
from sift.slices.auth import AuthService, MasterKeyStore, admin_cli
from sift.slices.auth.admin_cli import (
    ResetError,
    _confirm,
    _prompt_new_password,
    _resolve_username,
    reset_password,
)
from sift.slices.auth.crypto import (
    WrappedKey,
    unwrap_master_key,
)
from sift.slices.auth.passwords import PasswordPolicyError
from sift.slices.auth.tests.conftest import PASSWORD, PASSWORD_TWO
from sift.testing.auth import user_of_session

pytestmark = pytest.mark.integration


async def _wrapped(db: Database) -> WrappedKey:
    row = await db.fetch_one("SELECT mk_wrapped, mk_nonce, mk_kdf_salt FROM users LIMIT 1")
    assert row is not None
    return WrappedKey(row["mk_wrapped"], row["mk_nonce"], row["mk_kdf_salt"])


async def test_reset_sets_a_new_password_and_a_new_key(
    service: AuthService, auth_db: Database, master_keys: MasterKeyStore
) -> None:
    result = await service.create_first_admin("kate", PASSWORD)
    old_master_key = master_keys.get(result.user_id)
    assert old_master_key is not None
    old_wrapped = await _wrapped(auth_db)

    await reset_password(auth_db, "kate", PASSWORD_TWO)

    # The new password now works, the old one does not.
    assert (await service.login("kate", PASSWORD_TWO)).role == "admin"
    from sift.slices.auth.service import InvalidCredentials

    with pytest.raises(InvalidCredentials):
        await service.login("kate", PASSWORD)

    # The master key was replaced: the new one is not the old one, and the old password (even if it
    # were still accepted) could not open it.
    new_wrapped = await _wrapped(auth_db)
    assert new_wrapped.ciphertext != old_wrapped.ciphertext
    reopened = unwrap_master_key(new_wrapped, PASSWORD_TWO)
    assert reopened is not None and reopened != old_master_key


async def test_reset_re_enables_a_disabled_account(service: AuthService, auth_db: Database) -> None:
    """Recovery restores access, not only the password. A disabled user who has also forgotten
    its password would otherwise reset the password and still be shut out, with nothing left to try
    from the console, which the single admin must never be."""
    from sift.slices.auth.service import InvalidCredentials

    await service.create_first_admin("kate", PASSWORD)
    await auth_db.execute("UPDATE users SET disabled = 1 WHERE username = 'kate'")
    # Even the correct password is refused while the user is disabled.
    with pytest.raises(InvalidCredentials):
        await service.login("kate", PASSWORD)

    await reset_password(auth_db, "kate", PASSWORD_TWO)

    # The reset cleared the flag, so the user can sign in again.
    assert (await service.login("kate", PASSWORD_TWO)).role == "admin"


async def test_reset_discards_old_secrets_but_keeps_everything_else(
    service: AuthService, auth_db: Database, master_keys: MasterKeyStore
) -> None:
    result = await service.create_first_admin("kate", PASSWORD)
    old_master_key = master_keys.get(result.user_id)
    assert old_master_key is not None

    # A saved login under the old key, stored the way the downloader stores one, and an unrelated
    # row (a tag) that must survive the reset.
    sealed, nonce = seal_secret(old_master_key, b"a-saved-login")
    # The table the downloader makes the first time a login is saved. Written out here because the
    # auth schema does not create it, which is the same reason the reset has to guard on it.
    await auth_db.execute(
        "CREATE TABLE IF NOT EXISTS secrets (id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL,"
        " nonce BLOB NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    await auth_db.execute(
        "INSERT INTO secrets (id, ciphertext, nonce, created_at, updated_at)"
        " VALUES ('S', ?, ?, 1700000000, 1700000000)",
        (sealed, nonce),
    )
    await auth_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES ('T', 'holiday', 1700000000)"
    )

    await reset_password(auth_db, "kate", PASSWORD_TWO)

    # The row is GONE, not merely unreadable. Rotating the key alone would leave it sitting there
    # forever as ciphertext nothing in the world can open, and asserting only that the new key
    # cannot read it passes whether or not anything was deleted, which is no assertion at all.
    assert await auth_db.fetch_all("SELECT id FROM secrets") == []

    # And it really was locked with the old key, so what was discarded was a usable login rather
    # than a row that never held anything.
    new_key = unwrap_master_key(await _wrapped(auth_db), PASSWORD_TWO)
    assert new_key is not None
    assert open_secret(old_master_key, sealed, nonce) == b"a-saved-login"
    assert open_secret(new_key, sealed, nonce) is None

    # Everything unrelated is still there.
    tag = await auth_db.fetch_one("SELECT name FROM tags WHERE id = 'T'")
    assert tag is not None and tag["name"] == "holiday"


async def test_reset_revokes_existing_sessions(service: AuthService, auth_db: Database) -> None:
    result = await service.create_first_admin("kate", PASSWORD)
    assert await user_of_session(service, result.session_token) is not None
    await reset_password(auth_db, "kate", PASSWORD_TWO)
    # A cookie from before the reset is dead.
    assert await user_of_session(service, result.session_token) is None


async def test_reset_refuses_an_unknown_account(service: AuthService, auth_db: Database) -> None:
    await service.create_first_admin("kate", PASSWORD)
    with pytest.raises(ResetError, match="no user called 'someone-else'"):
        await reset_password(auth_db, "someone-else", PASSWORD_TWO)


async def test_reset_enforces_the_password_policy(service: AuthService, auth_db: Database) -> None:
    await service.create_first_admin("kate", PASSWORD)
    with pytest.raises(PasswordPolicyError):
        await reset_password(auth_db, "kate", "weak")


async def test_reset_works_on_an_install_that_has_never_downloaded_anything(
    service: AuthService, auth_db: Database
) -> None:
    """The saved-logins table only exists once the download feature has been used.

    Without the guard this is not a graceful degradation: it is an exception in the middle of the
    write, on the one path somebody reaches for when they are already locked out.
    """
    await service.create_first_admin("kate", PASSWORD)
    await auth_db.execute("DROP TABLE IF EXISTS secrets")

    await reset_password(auth_db, "kate", PASSWORD_TWO)

    assert (await service.login("kate", PASSWORD_TWO)).role == "admin"


# --- choosing which user --------------------------------------------------------------------


async def test_a_named_account_is_taken_as_given(service: AuthService, auth_db: Database) -> None:
    """No lookup at all when the name was passed: the argument is the answer, and an install with
    several admins must not have one chosen for it."""
    await service.create_first_admin("kate", PASSWORD)
    assert await _resolve_username(auth_db, "someone") == "someone"


async def test_the_only_admin_is_found_without_being_named(
    service: AuthService, auth_db: Database
) -> None:
    """The ordinary case. One admin, one console, no flags to remember at the worst moment."""
    await service.create_first_admin("kate", PASSWORD)
    assert await _resolve_username(auth_db, None) is not None
    assert await _resolve_username(auth_db, None) == "kate"


async def test_an_install_with_no_accounts_is_told_to_open_a_browser(auth_db: Database) -> None:
    """A reset before setup has nothing to reset. Saying so beats a confusing "no such user"."""
    with pytest.raises(ResetError, match="no users yet"):
        await _resolve_username(auth_db, None)


async def test_several_admins_must_be_told_apart(service: AuthService, auth_db: Database) -> None:
    """Guessing here resets the wrong person's password and destroys their saved logins. The names
    are listed, because somebody at a console cannot look them up any other way."""
    await service.create_first_admin("kate", PASSWORD)
    await auth_db.execute(
        """
        INSERT INTO users (id, username, role, password_hash, mk_wrapped, mk_nonce, mk_kdf_salt,
                           created_at)
        SELECT '01HX0000000000000000000ADM', 'sam', 'admin', password_hash, mk_wrapped, mk_nonce,
               mk_kdf_salt, created_at
          FROM users LIMIT 1
        """
    )

    with pytest.raises(ResetError, match="more than one admin") as raised:
        await _resolve_username(auth_db, None)

    assert "kate" in str(raised.value) and "sam" in str(raised.value)


# --- the terminal ---------------------------------------------------------------------------


def test_the_warning_says_what_is_lost_before_anything_happens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one thing this tool destroys has to be said in words, before the confirmation, not
    afterwards in a summary."""
    monkeypatch.setattr("builtins.input", lambda *_: "reset")

    assert _confirm("kate") is True

    said = capsys.readouterr().out
    assert "kate" in said
    assert "saved site logins will be lost" in said


def test_anything_other_than_the_word_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typed word rather than a y/N, because pressing return by habit must not destroy the saved
    logins of somebody who opened this to read what it did."""
    for answer in ("", "y", "yes", "RESET", "reset it"):
        monkeypatch.setattr("builtins.input", lambda *_, a=answer: a)
        assert _confirm("kate") is False


def test_the_word_is_read_past_surrounding_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *_: "  reset\n")
    assert _confirm("kate") is True


def test_the_new_password_is_asked_for_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([PASSWORD_TWO, PASSWORD_TWO])
    monkeypatch.setattr("getpass.getpass", lambda *_: next(answers))
    assert _prompt_new_password() == PASSWORD_TWO


def test_two_different_passwords_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is echoed, so a typo is invisible, and a mistyped password on this path locks the
    user out for good rather than merely being wrong."""
    answers = iter([PASSWORD_TWO, PASSWORD_TWO + "x"])
    monkeypatch.setattr("getpass.getpass", lambda *_: next(answers))
    with pytest.raises(ResetError, match="did not match"):
        _prompt_new_password()


# --- the command ----------------------------------------------------------------------------


async def _nothing() -> None:
    return None


@pytest.fixture
def at_the_console(
    monkeypatch: pytest.MonkeyPatch, auth_db: Database, tmp_path: Path
) -> Callable[..., None]:
    """Point the command at the test database and answer its prompts for it.

    `_run` opens the database itself, from the settings, because it is a program somebody starts at
    a shell rather than something the server hands a handle to. Redirected here so it works on the
    schema these tests have already built, and its connect and close are made no-ops, because the
    handle belongs to the fixture and closing it mid-test would take the rest of the test with it.
    """

    def _drive(*, answer: str, password: str = PASSWORD_TWO) -> None:
        monkeypatch.setattr(admin_cli, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
        monkeypatch.setattr(admin_cli, "check_sqlite_capabilities", lambda **_: None)
        monkeypatch.setattr(
            "sift.slices.auth.admin_cli.Database.for_data_dir",
            classmethod(lambda cls, _: auth_db),
        )
        monkeypatch.setattr(auth_db, "connect", _nothing)
        monkeypatch.setattr(auth_db, "close", _nothing)
        monkeypatch.setattr("builtins.input", lambda *_: answer)
        monkeypatch.setattr("getpass.getpass", lambda *_: password)

    return _drive


async def test_the_console_run_resets_the_only_admin(
    at_the_console: Callable[..., None],
    service: AuthService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end the way somebody runs it: no arguments, one admin, one typed word."""
    await service.create_first_admin("kate", PASSWORD)
    at_the_console(answer="reset")

    await admin_cli._run(None)

    assert (await service.login("kate", PASSWORD_TWO)).role == "admin"
    said = capsys.readouterr().out
    assert "kate" in said and "signed out" in said


async def test_saying_no_changes_nothing(
    at_the_console: Callable[..., None],
    service: AuthService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The confirmation is not decoration. Declining leaves the user exactly as it was: the
    old password still working is the assertion, because a half-done reset is a lock-out."""
    await service.create_first_admin("kate", PASSWORD)
    at_the_console(answer="no")

    await admin_cli._run(None)

    assert (await service.login("kate", PASSWORD)).role == "admin"
    assert "Canceled" in capsys.readouterr().out


async def test_the_account_can_be_named_on_the_command_line(
    at_the_console: Callable[..., None], service: AuthService
) -> None:
    await service.create_first_admin("kate", PASSWORD)
    at_the_console(answer="reset")

    await admin_cli._run("kate")

    assert (await service.login("kate", PASSWORD_TWO)).role == "admin"


async def test_the_database_is_closed_even_when_the_reset_fails(
    at_the_console: Callable[..., None],
    service: AuthService,
    auth_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command that leaves the write connection open holds the lock, and the next thing to touch
    this database (Sift itself, starting up) waits on a process that has already exited."""
    await service.create_first_admin("kate", PASSWORD)
    at_the_console(answer="reset")
    closed: list[bool] = []
    monkeypatch.setattr(auth_db, "close", lambda: _record(closed))

    with pytest.raises(ResetError):
        await admin_cli._run("nobody")

    assert closed == [True]


async def _record(seen: list[bool]) -> None:
    seen.append(True)


# --- what the shell sees ---------------------------------------------------------------------


def test_a_problem_ends_the_command_with_a_message_rather_than_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audience is somebody at a shell who has lost their password. A stack trace tells them
    nothing they can act on; the sentence the error carries does."""

    async def refuse(_: str | None) -> None:
        raise ResetError("there is no user called 'nobody'")

    monkeypatch.setattr(admin_cli, "_run", refuse)
    monkeypatch.setattr(sys, "argv", ["sift-admin", "reset-password", "--username", "nobody"])

    with pytest.raises(SystemExit) as raised:
        admin_cli.main()

    assert "nobody" in str(raised.value)


def test_a_weak_password_comes_back_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    """The policy failure is the other thing that reaches the shell, and it must not arrive as a
    traceback out of the hasher either."""

    async def refuse(_: str | None) -> None:
        raise PasswordPolicyError("that password is too short")

    monkeypatch.setattr(admin_cli, "_run", refuse)
    monkeypatch.setattr(sys, "argv", ["sift-admin", "reset-password"])

    with pytest.raises(SystemExit) as raised:
        admin_cli.main()

    assert "too short" in str(raised.value)


def test_the_username_reaches_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag is the only way to pick between several admins, so a parser that drops it silently
    resets the wrong person."""
    asked: list[str | None] = []

    async def record(username: str | None) -> None:
        asked.append(username)

    monkeypatch.setattr(admin_cli, "_run", record)

    monkeypatch.setattr(sys, "argv", ["sift-admin", "reset-password", "--username", "sam"])
    admin_cli.main()
    monkeypatch.setattr(sys, "argv", ["sift-admin", "reset-password"])
    admin_cli.main()

    assert asked == ["sam", None]


def test_the_command_needs_to_be_told_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    """No subcommand is a usage error, not a silent no-op that reads as a reset that did nothing."""
    monkeypatch.setattr(sys, "argv", ["sift-admin"])
    with pytest.raises(SystemExit):
        admin_cli.main()
