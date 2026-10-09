# SPDX-License-Identifier: AGPL-3.0-or-later
"""The auth business logic: first run, login, sessions, password and PIN.

Who a request is comes from the database every time: a session is a row and the token only
its key. The master key is unwrapped at login into memory, never written, never from a PIN.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeVar

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now, telling
from sift.kernel.client import Client
from sift.kernel.db import Connection, Database, Row, point_read
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger, hashed, security_event
from sift.kernel.vocabulary import Subject
from sift.kernel.wiring import Part
from sift.slices.auth.crypto import (
    Hasher,
    WrappedKey,
    derive_csrf_token,
    generate_master_key,
    hash_token,
    new_token,
    rewrap_master_key,
    unwrap_master_key,
    wrap_master_key,
)
from sift.slices.auth.errors import (
    InvalidCredentials,
    LockedOut,
    MasterKeyCorrupted,
    NoSuchUser,
    NotAGuestUser,
    SetupAlreadyDone,
    SignInBusy,
    TooManyRenames,
    UsernameTaken,
)
from sift.slices.auth.keys import MasterKeyStore
from sift.slices.auth.passwords import (
    generated_password,
    generated_username,
    validate_password,
    validate_pin,
)
from sift.slices.auth.throttle import InFlight, Tarpit, Throttle
from sift.slices.auth.tuning import (
    GENERATED_NAME_ATTEMPTS,
    LOGIN_TARPIT_BASE_SECONDS,
    LOGIN_TARPIT_FORGET_SECONDS,
    LOGIN_TARPIT_GRACE,
    LOGIN_TARPIT_MAX_KEYS,
    LOGIN_TARPIT_MAX_SECONDS,
    MAX_CONCURRENT_HASHES,
    MAX_PIN_ATTEMPTS,
    MAX_RENAMES_PER_WINDOW,
    MAX_UNLOCK_FAILURES,
    PIN_LOCKOUT_SECONDS,
    RENAME_WINDOW_SECONDS,
    SESSION_TOUCH_INTERVAL_SECONDS,
)
from sift.slices.auth.unlocks import VaultUnlockStore

log = get_logger(__name__)

_T = TypeVar("_T")


class LockOutcome(StrEnum):
    """What locking did: locked, signed out, or there was no session."""

    LOCKED = "locked"
    SIGNED_OUT = "signed_out"
    NO_SESSION = "no_session"


# --- Results ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class User:
    """One user as the management screen sees it, with no hash or key to leak into a response."""

    id: str
    username: str
    role: str
    disabled: bool
    created_at: int


@dataclass(frozen=True, slots=True)
class Authenticated:
    """What a setup or login yields: the session token for the cookie and the CSRF token."""

    user_id: str
    username: str
    role: str
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class GeneratedUser:
    """An invented guest and its password, which exists in the clear only here, once."""

    user: User
    password: str


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    """Who a live session belongs to, and whether the app lock is on it."""

    user_id: str
    locked: bool


# --- Statements ------------------------------------------------------------------------------

_USER_COUNT = "SELECT COUNT(*) AS n FROM users"

_INSERT_USER = """
INSERT INTO users
  (id, username, password_hash, pin_hash, role, mk_wrapped, mk_nonce, mk_kdf_salt,
   created_at, disabled)
VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, 0)
"""

_USER_BY_USERNAME = """
SELECT id, username, password_hash, pin_hash, role, mk_wrapped, mk_nonce, mk_kdf_salt, disabled
  FROM users WHERE username = ?
"""

_USER_BY_ID = """
SELECT id, username, password_hash, pin_hash, role, mk_wrapped, mk_nonce, mk_kdf_salt, disabled
  FROM users WHERE id = ?
"""

_REHASH_AND_REWRAP = """
UPDATE users SET password_hash = ?, mk_wrapped = ?, mk_nonce = ?, mk_kdf_salt = ? WHERE id = ?
"""

_REHASH_LOGIN = "UPDATE users SET password_hash = ? WHERE id = ?"

_UPDATE_PIN = "UPDATE users SET pin_hash = ? WHERE id = ?"

_INSERT_SESSION = """
INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at)
VALUES (?, ?, ?, ?, ?, ?)
"""

# A point read on the event loop: one seek by a unique hash.
_FIND_SESSION = point_read(
    "auth.session_by_token",
    """
SELECT id, user_id, last_seen_at, expires_at, locked_at, unlock_failures
  FROM sessions WHERE token_hash = ?
""",
)

_TOUCH_SESSION = "UPDATE sessions SET last_seen_at = ? WHERE id = ?"

_NOTE_DEVICE = "UPDATE sessions SET device_id = ?, client_kind = ? WHERE token_hash = ?"

_LOCK_SESSION = "UPDATE sessions SET locked_at = ?, unlock_failures = 0 WHERE id = ?"
_UNLOCK_SESSION = "UPDATE sessions SET locked_at = NULL, unlock_failures = 0 WHERE id = ?"
_COUNT_UNLOCK_FAILURE = "UPDATE sessions SET unlock_failures = unlock_failures + 1 WHERE id = ?"

_DELETE_SESSION_BY_ID = "DELETE FROM sessions WHERE id = ?"

_DELETE_SESSIONS_FOR_USER = "DELETE FROM sessions WHERE user_id = ?"

# Passing a value no session holds (the empty string) deletes them all.
_DELETE_OTHER_SESSIONS = "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?"

_COUNT_LIVE_SESSIONS = "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ? AND expires_at > ?"

# --- user management ---
#
# Oldest first, by ULID id: a wall clock can step backwards.
_LIST_USERS = """
SELECT id, username, role, disabled, created_at FROM users ORDER BY id
"""

# Named columns, never SELECT *, so no hash reaches `_user_from_row`.
_MANAGED_USER_BY_ID = (
    "SELECT id, username, role, disabled, created_at, renames, renames_since "
    "FROM users WHERE id = ?"
)

_SET_DISABLED = "UPDATE users SET disabled = ? WHERE id = ?"

_RENAME_USER = "UPDATE users SET username = ? WHERE id = ?"
_SPEND_RENAME = "UPDATE users SET renames = renames + 1 WHERE id = ?"
_RESET_RENAME_WINDOW = "UPDATE users SET renames = 1, renames_since = ? WHERE id = ?"

_DELETE_USER = "DELETE FROM users WHERE id = ?"


def _user_from_row(row: Row) -> User:
    return User(
        id=str(row["id"]),
        username=str(row["username"]),
        role=str(row["role"]),
        disabled=bool(row["disabled"]),
        created_at=int(row["created_at"]),
    )


class AuthService:
    """The one place auth logic lives, built at boot so a test can hand it its own parts."""

    def __init__(
        self,
        database: Database,
        *,
        hasher: Hasher,
        master_keys: MasterKeyStore,
        vault_unlocks: VaultUnlockStore | None = None,
        queue: JobQueue | None,
        session_ttl_seconds: int,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        login_tarpit: Tarpit | None = None,
        pin_throttle: Throttle | None = None,
        may_reopen_with_pin: Callable[[str], Awaitable[bool]] | None = None,
        on_key_available: Callable[[bytes], Awaitable[None]] | None = None,
    ) -> None:
        self._db = database
        self._hasher = hasher
        self._master_keys = master_keys
        self._vault_unlocks = vault_unlocks or VaultUnlockStore()
        # Asked of the composition root; absent means no, so locking ends the session.
        self._may_reopen_with_pin = may_reopen_with_pin
        # Told when a master key exists, for work that needs one.
        self._on_key_available = on_key_available
        self._queue = queue
        self._session_ttl = session_ttl_seconds
        self._clock = clock
        self._sleep = sleep
        self._login_tarpit = login_tarpit or Tarpit(
            grace=LOGIN_TARPIT_GRACE,
            base_delay_seconds=LOGIN_TARPIT_BASE_SECONDS,
            max_delay_seconds=LOGIN_TARPIT_MAX_SECONDS,
            forget_after_seconds=LOGIN_TARPIT_FORGET_SECONDS,
            max_keys=LOGIN_TARPIT_MAX_KEYS,
        )
        # Keyed by address too, so a guesser cannot make an admin wait; the longer wait is served.
        self._client_tarpit = Tarpit(
            grace=LOGIN_TARPIT_GRACE,
            base_delay_seconds=LOGIN_TARPIT_BASE_SECONDS,
            max_delay_seconds=LOGIN_TARPIT_MAX_SECONDS,
            forget_after_seconds=LOGIN_TARPIT_FORGET_SECONDS,
            max_keys=LOGIN_TARPIT_MAX_KEYS,
        )
        # Per name and address, so a guesser holds up only their own attempts.
        self._signing_in = InFlight()
        self._pin_throttle = pin_throttle or Throttle(
            max_failures=MAX_PIN_ATTEMPTS, lockout_seconds=PIN_LOCKOUT_SECONDS
        )
        # Argon2 on worker threads, capped so a login flood can neither stall nor exhaust memory.
        self._hash_slots = asyncio.Semaphore(MAX_CONCURRENT_HASHES)
        # So "no such user" cannot be told from "wrong password" by a stopwatch.
        self._dummy_hash = hasher.dummy_hash()

    def _now(self) -> int:
        return int(self._clock())

    async def _hash(self, fn: Callable[..., _T], *args: object) -> _T:
        """Run one Argon2 operation off the event loop, under the concurrency cap."""
        async with self._hash_slots:
            return await asyncio.to_thread(fn, *args)

    @property
    def master_keys(self) -> MasterKeyStore:
        return self._master_keys

    @property
    def vault_unlocks(self) -> VaultUnlockStore:
        return self._vault_unlocks

    # --- First run -------------------------------------------------------------------------

    async def admin_exists(self) -> bool:
        """Whether setup has been done. True the instant the one user exists."""
        return await self._user_count() > 0

    async def _user_count(self) -> int:
        row = await self._db.fetch_one(_USER_COUNT)
        return 0 if row is None else int(row["n"])

    async def create_first_admin(
        self, username: str, password: str, *, ttl_seconds: int | None = None
    ) -> Authenticated:
        """Create the single admin, once; SetupAlreadyDone if any user exists.

        A cheap check before any hashing, then the check and insert again inside one write, so two
        requests cannot both create an admin.
        """
        if await self._user_count() > 0:
            raise SetupAlreadyDone("a user already exists; setup cannot run again")
        validate_password(password)
        master_key = generate_master_key()
        wrapped = await self._hash(wrap_master_key, master_key, password)
        password_hash = await self._hash(self._hasher.hash, password)
        user_id = new_id()
        now = self._now()

        async with self._db.write() as connection:
            existing = list(await connection.execute_fetchall(_USER_COUNT))
            if int(existing[0]["n"]) > 0:
                raise SetupAlreadyDone("a user already exists; setup cannot run again")
            await connection.execute(
                _INSERT_USER,
                (
                    user_id,
                    username,
                    password_hash,
                    "admin",
                    wrapped.ciphertext,
                    wrapped.nonce,
                    wrapped.salt,
                    now,
                ),
            )

        log.info("auth.admin_created", user_id=user_id)
        self._master_keys.store(user_id, master_key)
        await self._release_blocked_jobs()
        session_token, csrf_token = await self._create_session(user_id, ttl_seconds)
        return Authenticated(
            user_id=user_id,
            username=username,
            role="admin",
            session_token=session_token,
            csrf_token=csrf_token,
        )

    # --- Login / logout --------------------------------------------------------------------

    async def login(
        self,
        username: str,
        password: str,
        *,
        ttl_seconds: int | None = None,
        client: str | None = None,
    ) -> Authenticated:
        """Verify a password and start a fresh session, unwrapping the master key.

        Failures tarpit the next attempt (a delay, never a lockout), served before the password is
        read. A new session is always minted, against session fixation.
        """
        key = username.strip().casefold()
        held = f"{key}\n{client or ''}"
        if not self._signing_in.claim(held):
            security_event("login.busy", account=hashed(key))
            raise SignInBusy(max(1, math.ceil(self._login_tarpit.delay(key))))
        try:
            return await self._judge_login(key, username, password, ttl_seconds, client)
        finally:
            self._signing_in.release(held)

    async def _judge_login(
        self, key: str, username: str, password: str, ttl_seconds: int | None, client: str | None
    ) -> Authenticated:
        # Counted up front, so simultaneous guesses each wait longer.
        delay = self._login_tarpit.reserve(key)
        if client:
            delay = max(delay, self._client_tarpit.reserve(client))
        if delay > 0:
            security_event("login.tarpit", account=hashed(key), delay_seconds=round(delay, 3))
            await self._sleep(delay)

        row = await self._db.fetch_one(_USER_BY_USERNAME, (username,))
        stored_hash = row["password_hash"] if row is not None else self._dummy_hash
        verified = await self._hash(self._hasher.verify, stored_hash, password)

        if row is None or not verified or row["disabled"]:
            security_event(
                "login.failed",
                account=hashed(key),
                user_id=row["id"] if row is not None else None,
            )
            raise InvalidCredentials("Incorrect username or password.")

        self._login_tarpit.record_success(key)
        if client:
            # Or every sign-in from one router would lengthen the next one's wait.
            self._client_tarpit.record_success(client)
        if self._hasher.needs_rehash(stored_hash):
            # A cheaper hash verifies faster than the dummy, an existence oracle: upgrade it now.
            upgraded = await self._hash(self._hasher.hash, password)
            await self._db.execute(_REHASH_LOGIN, (upgraded, row["id"]))
        await self._unlock_master_key(row, password)
        session_token, csrf_token = await self._create_session(row["id"], ttl_seconds)
        log.info("auth.login", user_id=row["id"], role=row["role"])
        return Authenticated(
            user_id=row["id"],
            username=row["username"],
            role=row["role"],
            session_token=session_token,
            csrf_token=csrf_token,
        )

    async def logout(self, token: str) -> None:
        """Revoke this token's session; the master key goes once no live session needs it."""
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        if row is None:
            return
        user_id = row["user_id"]
        await self._db.execute(_DELETE_SESSION_BY_ID, (row["id"],))
        # Only this session's vault; entries left by dead sessions are inert and capped.
        self._vault_unlocks.lock(hash_token(token))
        if await self._live_session_count(user_id) == 0:
            self._master_keys.forget(user_id)
        log.info("auth.logout", user_id=user_id)

    async def username_of(self, user_id: str) -> str | None:
        """The user's username, read fresh rather than trusted from the client."""
        row = await self._db.fetch_one("SELECT username FROM users WHERE id = ?", (user_id,))
        return None if row is None else str(row["username"])

    async def resolve_session_state(self, token: str) -> ResolvedSession | None:
        """The user behind a session token and whether the app is locked on it, in one read."""
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        if row is None:
            return None
        now = self._now()
        # Expiry outranks the lock, so a month-locked machine falls back to the password.
        if row["expires_at"] <= now:
            await self._db.execute(_DELETE_SESSION_BY_ID, (row["id"],))
            return None
        if now - row["last_seen_at"] >= SESSION_TOUCH_INTERVAL_SECONDS:
            await self._db.execute(_TOUCH_SESSION, (now, row["id"]))
        return ResolvedSession(user_id=str(row["user_id"]), locked=row["locked_at"] is not None)

    async def note_device(self, token: str, client: Client) -> None:
        """Write on this session which device it runs on and its kind of window."""
        await self._db.execute(_NOTE_DEVICE, (client.device, client.kind, hash_token(token)))

    # --- the app lock ------------------------------------------------------------------------

    async def lock_app(self, token: str) -> LockOutcome:
        """Shut this session, and say which way: locked, or signed out.

        Decided here, where the setting and the PIN are. The mark is on the row, so every tab and
        replayed cookie is refused; Hidden shuts too, and the failure count resets.
        """
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        if row is None:
            return LockOutcome.NO_SESSION

        user_id = str(row["user_id"])
        await self._db.execute(_LOCK_SESSION, (self._now(), row["id"]))
        self._vault_unlocks.lock(hash_token(token))
        log.info("auth.app_locked", user_id=user_id)
        return LockOutcome.LOCKED

    async def is_locked(self, token: str) -> bool:
        """Whether Sift is locked on this session, so the interface knows before it draws."""
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        return row is not None and row["locked_at"] is not None

    async def unlock_app_with_password(self, token: str, password: str) -> bool:
        """Open a locked session with the password; False when wrong.

        Always available: the PIN is a shortcut. Tarpitted, not counted against the PIN's
        cap. Hidden stays shut.
        """
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        if row is None:
            return False
        user_id = str(row["user_id"])

        user = await self._db.fetch_one(_USER_BY_ID, (user_id,))
        if user is None:  # pragma: no cover (the session resolved, so the user is there)
            return False

        key = f"unlock:{user_id}"
        await self._sleep(self._login_tarpit.reserve(key))
        if not await self._hash(self._hasher.verify, user["password_hash"], password):
            log.info("auth.app_unlock_password_failed", user_id=user_id)
            return False
        self._login_tarpit.record_success(key)

        await self._db.execute(_UNLOCK_SESSION, (row["id"],))
        self._vault_unlocks.lock(hash_token(token))
        log.info("auth.app_unlocked_with_password", user_id=user_id)
        return True

    async def may_reopen_with_pin(self, user_id: str) -> bool:
        """Whether the lock screen offers the PIN: the setting on and a PIN set."""
        if self._may_reopen_with_pin is None:
            return False
        if not await self.has_pin(user_id):
            return False
        return await self._may_reopen_with_pin(user_id)

    async def unlock_app(self, token: str, pin: str) -> bool:
        """Open a locked session with the PIN; False when wrong.

        It never creates a session, so the PIN is no second way in. A run of wrong PINs ends the
        session. Hidden is not opened, and the master key is not touched.
        """
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        if row is None:
            return False
        user_id = str(row["user_id"])
        # With the setting off, the PIN must not open Sift at all.
        if not await self.may_reopen_with_pin(user_id):
            log.info("auth.app_unlock_pin_not_offered", user_id=user_id)
            return False
        if not await self.verify_pin(user_id, pin):
            await self._db.execute(_COUNT_UNLOCK_FAILURE, (row["id"],))
            if int(row["unlock_failures"]) + 1 >= MAX_UNLOCK_FAILURES:
                await self._db.execute(_DELETE_SESSION_BY_ID, (row["id"],))
                self._vault_unlocks.lock(hash_token(token))
                if await self._live_session_count(user_id) == 0:
                    self._master_keys.forget(user_id)
                log.info("auth.app_unlock_exhausted", user_id=user_id)
            else:
                log.info("auth.app_unlock_failed", user_id=user_id)
            return False
        await self._db.execute(_UNLOCK_SESSION, (row["id"],))
        # Hidden stays shut on the way back in, however the session came to be locked.
        self._vault_unlocks.lock(hash_token(token))
        log.info("auth.app_unlocked", user_id=user_id)
        return True

    # --- Password change -------------------------------------------------------------------

    async def change_password(
        self,
        user_id: str,
        old_password: str,
        new_password: str,
        *,
        current_token: str | None = None,
    ) -> None:
        """Change a user's own password, the old one required.

        The master key is re-wrapped, so saved logins survive; every other session is revoked.
        """
        row = await self._db.fetch_one(_USER_BY_ID, (user_id,))
        if row is None or not await self._hash(
            self._hasher.verify, row["password_hash"], old_password
        ):
            raise InvalidCredentials("old password is incorrect")

        validate_password(new_password)
        new_hash = await self._hash(self._hasher.hash, new_password)

        if row["mk_wrapped"] is None:
            await self._db.execute(_REHASH_LOGIN, (new_hash, user_id))
        else:
            wrapped = WrappedKey(row["mk_wrapped"], row["mk_nonce"], row["mk_kdf_salt"])
            rewrapped = await self._hash(rewrap_master_key, wrapped, old_password, new_password)
            if rewrapped is None:
                raise MasterKeyCorrupted("the stored key could not be re-wrapped")
            await self._db.execute(
                _REHASH_AND_REWRAP,
                (new_hash, rewrapped.ciphertext, rewrapped.nonce, rewrapped.salt, user_id),
            )

        keep = hash_token(current_token) if current_token is not None else ""
        await self._db.execute(_DELETE_OTHER_SESSIONS, (user_id, keep))
        # With no session kept, nothing can reach the key, so it is dropped.
        if await self._live_session_count(user_id) == 0:
            self._master_keys.forget(user_id)
        log.info("auth.password_changed", user_id=user_id)

    # --- PIN -------------------------------------------------------------------------------

    async def set_pin(self, user_id: str, pin: str, current_password: str) -> None:
        """Set or change the PIN; the password is asked so an unlocked screen cannot plant one."""
        row = await self._db.fetch_one(_USER_BY_ID, (user_id,))
        if row is None or not await self._hash(
            self._hasher.verify, row["password_hash"], current_password
        ):
            raise InvalidCredentials("password is incorrect")
        validate_pin(pin)
        pin_hash = await self._hash(self._hasher.hash, pin)
        await self._db.execute(_UPDATE_PIN, (pin_hash, user_id))
        log.info("auth.pin_set", user_id=user_id)

    async def has_pin(self, user_id: str) -> bool:
        """Whether this user has a PIN, without which a vault could not be opened again."""
        row = await self._db.fetch_one(_USER_BY_ID, (user_id,))
        return row is not None and row["pin_hash"] is not None

    async def verify_pin(self, user_id: str, pin: str) -> bool:
        """Check the PIN that unlocks a locked session; rate-limited, and never a key."""
        if self._pin_throttle.locked(user_id):
            raise LockedOut("too many attempts")
        row = await self._db.fetch_one(_USER_BY_ID, (user_id,))
        stored = row["pin_hash"] if row is not None and row["pin_hash"] is not None else None
        ok = await self._hash(self._hasher.verify, stored, pin) if stored is not None else False
        if ok:
            self._pin_throttle.record_success(user_id)
        else:
            self._pin_throttle.record_failure(user_id)
            security_event("pin.failed", user_id=user_id)
        return ok

    # --- Admin operations on a user --------------------------------------------------------

    async def revoke_all_sessions(self, user_id: str) -> None:
        """Drop every session a user has, and forget their master key. Used by the reset tool."""
        await self._db.execute(_DELETE_SESSIONS_FOR_USER, (user_id,))
        self._master_keys.forget(user_id)

    async def list_users(self) -> list[User]:
        """Every user, the admin included, so the list agrees with the sharing screen."""
        rows = await self._db.fetch_all(_LIST_USERS)
        return [_user_from_row(row) for row in rows]

    # --- users ---------------------------------------------------------------------------------
    #
    # Below here every write changes the user list; above it, nothing a screen lists.

    async def _say(
        self,
        sql: str,
        params: tuple[object, ...],
        *,
        event: tuple[Viewer, str, str, str, dict[str, object] | None] | None = None,
    ) -> None:
        """Write to the list of users and tell every admin's screen that draws it.

        `event` only for an act on a user (made, turned off, removed); never for a credential.
        """
        if event is None:
            await self._db.execute(sql, params)
            announce_now(EVERY_ADMIN, About.SETTINGS)
            return
        by, verb, user_id, username, payload = event
        async with self._db.write() as connection:
            await connection.execute(sql, params)
            await self._user_event(connection, by, verb, user_id, username, payload)
        announce_now(EVERY_ADMIN, About.SETTINGS)

    async def _user_event(
        self,
        connection: Connection,
        by: Viewer,
        verb: str,
        user_id: str,
        username: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        """One line saying what was done to a user, its name a snapshot, in the same write."""
        await record_event(
            connection,
            actor=Actor.user(by.id),
            verb=verb,
            subject=Subject(kind="login", id=user_id, name=username),
            payload=None if payload is None else json.dumps(payload),
        )

    async def create_guest(self, by: Viewer, username: str, password: str) -> User:
        """Add a guest with a password an admin chooses and hands over; a guest holds no key."""
        cleaned = username.strip()
        if not cleaned:
            raise UsernameTaken("A username can't be blank.")
        validate_password(password)
        password_hash = await self._hash(self._hasher.hash, password)
        user_id = new_id()
        now = self._now()

        # One write, so two admins adding one name cannot both find it free.
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            taken = list(await connection.execute_fetchall(_USER_BY_USERNAME, (cleaned,)))
            if taken:
                raise UsernameTaken(f"There's already a user called {cleaned!r}.")
            await connection.execute(
                _INSERT_USER,
                (user_id, cleaned, password_hash, "guest", None, None, None, now),
            )
            await self._user_event(connection, by, "added", user_id, cleaned)

        log.info("auth.guest_created", user_id=user_id)
        return User(id=user_id, username=cleaned, role="guest", disabled=False, created_at=now)

    async def generate_guest(self, by: Viewer) -> GeneratedUser:
        """Invent a guest, a free name and a strong password, both handed back once.

        Both from the OS random source; the password is never stored in the clear or logged.
        """
        password = generated_password()
        password_hash = await self._hash(self._hasher.hash, password)
        now = self._now()

        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            for _ in range(GENERATED_NAME_ATTEMPTS):
                username = generated_username()
                taken = list(await connection.execute_fetchall(_USER_BY_USERNAME, (username,)))
                if taken:
                    continue
                user_id = new_id()
                await connection.execute(
                    _INSERT_USER,
                    (user_id, username, password_hash, "guest", None, None, None, now),
                )
                await self._user_event(connection, by, "added", user_id, username)
                break
            else:
                raise UsernameTaken("could not invent a free username; add one by hand")

        log.info("auth.guest_created", user_id=user_id)
        return GeneratedUser(
            user=User(id=user_id, username=username, role="guest", disabled=False, created_at=now),
            password=password,
        )

    def _charge_rename(self, connection: Connection, row: Row) -> None:
        """Refuse a rename past the day's allowance, charged before the name is looked at."""
        started = row["renames_since"]
        used = int(row["renames"] or 0)
        within_window = started is not None and self._now() - int(started) < RENAME_WINDOW_SECONDS
        if within_window and used >= MAX_RENAMES_PER_WINDOW:
            raise TooManyRenames(
                "That's as many name changes as one day allows. Try again tomorrow."
            )

    async def _record_rename(self, connection: Connection, row: Row, user_id: str) -> None:
        """Spend one of the allowance, starting a fresh window if the last one has run out."""
        started = row["renames_since"]
        now = self._now()
        if started is None or now - int(started) >= RENAME_WINDOW_SECONDS:
            await connection.execute(_RESET_RENAME_WINDOW, (now, user_id))
        else:
            await connection.execute(_SPEND_RENAME, (user_id,))

    async def rename_user(
        self, user_id: str, username: str, *, counted_against_admin: bool = True, by: Viewer
    ) -> User:
        """Change what a user is called and nothing else.

        The master key is salted, not derived from the name, so nothing is re-wrapped; no session
        ends. The name check and the write share one transaction.
        """
        cleaned = username.strip()
        if not cleaned:
            raise UsernameTaken("A username can't be blank.")

        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            existing = list(await connection.execute_fetchall(_MANAGED_USER_BY_ID, (user_id,)))
            if not existing:
                raise NoSuchUser("There's no such user.")

            # The cap is on renaming yourself, not on an admin renaming somebody.
            if not counted_against_admin:
                self._charge_rename(connection, existing[0])
                await self._record_rename(connection, existing[0], user_id)

            taken = list(await connection.execute_fetchall(_USER_BY_USERNAME, (cleaned,)))
            refused = bool(taken) and str(taken[0]["id"]) != user_id
            if not refused:
                await connection.execute(_RENAME_USER, (cleaned, user_id))
                # The old name, which the overwrite leaves nowhere else.
                await self._user_event(
                    connection,
                    by,
                    "renamed",
                    user_id,
                    cleaned,
                    {"before": str(existing[0]["username"])},
                )
        if refused:
            raise UsernameTaken(
                f"There's already a user called {cleaned!r}."
                if counted_against_admin
                else "That name can't be used."
            )

        security_event("account.renamed", user_id=user_id)
        return replace(_user_from_row(existing[0]), username=cleaned)

    async def set_user_disabled(self, by: Viewer, user_id: str, disabled: bool) -> User:
        """Turn a guest off or on; off ends their sessions, so on signs nobody back in."""
        user = await self._manageable(user_id)
        await self._say(
            _SET_DISABLED,
            (1 if disabled else 0, user_id),
            event=(by, "edited", user_id, user.username, {"disabled": disabled}),
        )
        if disabled:
            await self.revoke_all_sessions(user_id)
        security_event("account.disabled" if disabled else "account.enabled", user_id=user_id)
        return replace(user, disabled=disabled)

    async def delete_user(self, by: Viewer, user_id: str) -> None:
        """Remove a guest; their sessions and grants go with the row by foreign key."""
        user = await self._manageable(user_id)
        await self._say(
            _DELETE_USER, (user_id,), event=(by, "deleted", user_id, user.username, None)
        )
        self._master_keys.forget(user_id)
        security_event("account.deleted", user_id=user_id)

    async def reset_user_password(self, user_id: str, new_password: str) -> None:
        """Set a guest's password without the old one (a guest holds no key); their sessions end."""
        await self._manageable(user_id)
        validate_password(new_password)
        new_hash = await self._hash(self._hasher.hash, new_password)
        await self._say(_REHASH_LOGIN, (new_hash, user_id))
        await self.revoke_all_sessions(user_id)
        security_event("account.password_reset", user_id=user_id)

    async def _manageable(self, user_id: str) -> User:
        """The user this operation is about: 404 for no such id, 403 for an admin."""
        row = await self._db.fetch_one(_MANAGED_USER_BY_ID, (user_id,))
        if row is None:
            raise NoSuchUser("There's no such user.")
        user = _user_from_row(row)
        if user.role != "guest":
            raise NotAGuestUser("only guest users are managed here")
        return user

    async def unlock_secrets(self, user_id: str, password: str) -> bool:
        """Put a user's master key back in memory after a restart, keeping the session."""
        row = await self._db.fetch_one(_USER_BY_ID, (user_id,))
        if row is None or row["mk_wrapped"] is None:
            return False
        if not await self._hash(self._hasher.verify, row["password_hash"], password):
            return False
        await self._unlock_master_key(row, password)
        return self._master_keys.has(user_id)

    def secrets_locked(self, user_id: str) -> bool:
        """Whether this user's saved logins and tunnels are unreadable right now."""
        return not self._master_keys.has(user_id)

    # --- Internals -------------------------------------------------------------------------

    async def _unlock_master_key(self, row: Row, password: str) -> None:
        """Unwrap the user's master key into memory and release waiting jobs."""
        if row["mk_wrapped"] is None:
            return
        wrapped = WrappedKey(row["mk_wrapped"], row["mk_nonce"], row["mk_kdf_salt"])
        master_key = await self._hash(unwrap_master_key, wrapped, password)
        if master_key is None:
            log.warning("auth.master_key_unwrap_failed", user_id=row["id"])
            return
        self._master_keys.store(row["id"], master_key)
        await self._release_blocked_jobs()
        # Every admin's window is told, so an unlock bar in another window goes.
        announce_now(EVERY_ADMIN, About.JOBS)
        await self._announce_key(master_key)

    async def _announce_key(self, master_key: bytes) -> None:
        """Tell what waited for a key that there is one; a failure never costs the login."""
        if self._on_key_available is None:
            return
        try:
            await self._on_key_available(master_key)
        except Exception:
            log.warning("auth.key_listener_failed")

    async def _release_blocked_jobs(self) -> None:
        """Return every job that was waiting for a login to the queue; idempotent."""
        if self._queue is not None:
            await self._queue.unblock()

    async def _create_session(
        self, user_id: str, ttl_seconds: int | None = None
    ) -> tuple[str, str]:
        """Mint a session row; return (session_token, csrf_token), the token stored hashed.

        The caller gives the lifetime, the same number it gives the cookie; None is the default.
        """
        token = new_token()
        now = self._now()
        expires = now + (self._session_ttl if ttl_seconds is None else ttl_seconds)
        await self._db.execute(
            _INSERT_SESSION,
            (new_id(), user_id, hash_token(token), now, now, expires),
        )
        return token, derive_csrf_token(token)

    async def _live_session_count(self, user_id: str) -> int:
        row = await self._db.fetch_one(_COUNT_LIVE_SESSIONS, (user_id, self._now()))
        return 0 if row is None else int(row["n"])


#: Users, sessions and the master-key envelope.
SERVICE: Part[AuthService] = Part("auth")
