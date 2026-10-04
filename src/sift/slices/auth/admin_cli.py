# SPDX-License-Identifier: AGPL-3.0-or-later
"""The host-console tool for resetting a forgotten password.

There is no email and no security question, on purpose: a user that cannot be correlated with
an address is one less thing an install leaks. The cost is that a forgotten password is recovered
from the machine itself, by someone with shell access to it, which an attacker on the internet
does not have. That requirement is the recovery story's security, not a gap in it.

The one thing this cannot do is keep the saved site logins. They are locked with a key wrapped by
the old password, and without the old password that key cannot be opened. So the reset mints a new
one and discards the old locked logins. Everything else (tags, ratings, people, collections, the
media itself) is untouched. The tool says so, in plain words, before it does anything.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sift.kernel.config import get_settings
from sift.kernel.db import Database, check_sqlite_capabilities
from sift.slices.auth.crypto import (
    Hasher,
    generate_master_key,
    resolve_argon2_params,
    wrap_master_key,
)
from sift.slices.auth.passwords import PasswordPolicyError, validate_password

_FIND_ADMINS = "SELECT id, username FROM users WHERE role = 'admin' ORDER BY id"
_USER_BY_USERNAME = "SELECT id, username FROM users WHERE username = ?"
# `disabled = 0` as well: this is the recovery path, and recovery restores *access*, not only the
# password. A user who was locked out and has forgotten its password would otherwise reset the
# password and still be unable to sign in, with nothing else to turn to from the console.
_UPDATE = """
UPDATE users
   SET password_hash = ?, mk_wrapped = ?, mk_nonce = ?, mk_kdf_salt = ?, disabled = 0
 WHERE id = ?
"""
_DELETE_SESSIONS = "DELETE FROM sessions WHERE user_id = ?"
_SECRETS_TABLE = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'secrets'"


class ResetError(Exception):
    """Something the operator can fix: no such user, or an ambiguous one."""


async def reset_password(database: Database, username: str, new_password: str) -> str:
    """Give a user a new password and a fresh master key. Returns the user id.

    The new master key means the old saved logins can no longer be decrypted: they are discarded
    here rather than left as undecryptable rows. Every session the user had is revoked, so a
    stolen cookie from before the reset is dead. Nothing else is touched.

    Uses fixed hashing parameters rather than probing the hardware: this runs as a short-lived
    command, not the server, and the floor is strong on any machine.
    """
    validate_password(new_password)

    row = await database.fetch_one(_USER_BY_USERNAME, (username,))
    if row is None:
        raise ResetError(f"there is no user called {username!r}")
    user_id = str(row["id"])

    hasher = Hasher(resolve_argon2_params(None))
    password_hash = hasher.hash(new_password)
    wrapped = wrap_master_key(generate_master_key(), new_password)

    async with database.write() as connection:
        await connection.execute(
            _UPDATE,
            (password_hash, wrapped.ciphertext, wrapped.nonce, wrapped.salt, user_id),
        )
        await connection.execute(_DELETE_SESSIONS, (user_id,))
        # The saved logins were locked with the old key. Discard them rather than leave rows nothing
        # can open. The table only exists once the download feature has been used; guard on it so
        # this works on an install that never has.
        has_secrets = await connection.execute_fetchall(_SECRETS_TABLE)
        if has_secrets:
            await connection.execute("DELETE FROM secrets")

    return user_id


async def _resolve_username(database: Database, requested: str | None) -> str:
    if requested is not None:
        return requested
    admins = await database.fetch_all(_FIND_ADMINS)
    if not admins:
        raise ResetError("there are no users yet — open Sift in a browser to create one")
    if len(admins) > 1:
        names = ", ".join(str(row["username"]) for row in admins)
        raise ResetError(f"there is more than one admin; pass --username (one of: {names})")
    return str(admins[0]["username"])


def _confirm(who: str) -> bool:
    """Warn, in plain words, and read the confirmation. Blocking terminal I/O, kept off the event
    loop by the caller."""
    sys.stdout.write(
        f"\nThis resets the password for {who!r}.\n\n"
        "Your saved site logins will be lost: they are locked with your old password, which\n"
        "this tool does not have. Everything else (your tags, ratings, people, collections\n"
        "and media) is kept.\n\n"
    )
    return input("Type 'reset' to continue: ").strip() == "reset"


def _prompt_new_password() -> str:
    first = getpass.getpass("New password: ")
    again = getpass.getpass("New password again: ")
    if first != again:
        raise ResetError("the two passwords did not match")
    return first


async def _run(username: str | None) -> None:
    settings = get_settings()
    # Quiet: this is a console tool, and a line about the database is not what
    # somebody resetting a password came here to read.
    check_sqlite_capabilities(announce=False)
    database = Database.for_data_dir(settings.data_dir)
    await database.connect()
    try:
        who = await _resolve_username(database, username)
        # The prompts block on the terminal, so they run in a thread rather than stalling the loop.
        if not await asyncio.to_thread(_confirm, who):
            sys.stdout.write("Canceled. Nothing changed.\n")
            return
        new_password = await asyncio.to_thread(_prompt_new_password)
        await reset_password(database, who, new_password)
        sys.stdout.write(
            f"\nDone. {who!r} can now sign in with the new password. Any existing sessions were\n"
            "signed out.\n"
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="sift-admin", description="Sift administration.")
    sub = parser.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset-password", help="reset a forgotten password")
    reset.add_argument("--username", help="the user to reset (needed only if there are several)")
    args = parser.parse_args()

    # No dispatch on the subcommand: there is one, and it is required, so argparse has already
    # refused anything else before this line runs. A branch here would be one nothing can reach.
    try:
        asyncio.run(_run(args.username))
    except (ResetError, PasswordPolicyError) as exc:
        # What reaches the shell is the sentence, not a traceback. The person running this has lost
        # their password; a stack trace tells them nothing they can act on.
        raise SystemExit(f"\n{exc}\n") from None


if __name__ == "__main__":
    main()
