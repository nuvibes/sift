# SPDX-License-Identifier: AGPL-3.0-or-later
"""The auth business logic: first-run, login, sessions, password and PIN.

Everything here is written so that the answer to "who is this and what may they do" is read from
the database on every request, never carried in the token. A session is a row; the token in the
cookie is only a key to it. That is what makes logout revoke, an expiry expire, and a disabled
user lose their live session on the next request instead of at some later login.

The master key rides along at login: the submitted password unwraps it into the in-memory store,
where a job that needs a saved site login can reach it, and it is dropped at logout. It is never
written down and never derived from a PIN.
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


# --- Errors ----------------------------------------------------------------------------------
#
# Each maps to one HTTP status in the router. They carry no detail a caller could use to tell
# users apart: the login errors are deliberately interchangeable.


class AuthError(Exception):
    """Base for the failures this slice raises."""


class SetupAlreadyDone(AuthError):
    """An admin already exists. First-run setup cannot run a second time: that idempotency is
    the anti-takeover guard, not a convenience."""


class InvalidCredentials(AuthError):
    """A username, password, or old password did not match. One error for every such case, so the
    response cannot be read to learn which part was wrong or whether a user exists."""


class LockedOut(AuthError):
    """Too many failures in a row. The target is locked for a few minutes."""


class SignInBusy(AuthError):
    """A sign-in for this username from this address is already being checked. Try again later."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "A sign-in for that username is already being checked. Try again in a moment."
        )
        self.retry_after_seconds = retry_after_seconds


class UsernameTaken(AuthError):
    """The name is already a user's. Said plainly, because this one is not a login: an admin
    typing a name into the create-a-guest form is entitled to know the name is spoken for, and
    they could learn it from the user list on the same screen anyway."""


class LockOutcome(StrEnum):
    """What locking actually did, so the caller does not have to work it out again.

    Three answers rather than a boolean. "Locked" and "signed out" are both success and they land
    somebody in different places, and a client that guessed between them would be re-deriving the
    decision this moved off it.
    """

    LOCKED = "locked"
    SIGNED_OUT = "signed_out"
    NO_SESSION = "no_session"


class TooManyRenames(AuthError):
    """This user has spent their name changes for the window. Applies to renaming yourself only."""


class NoSuchUser(AuthError):
    """No user has that id. Either it never existed or it has since been deleted."""


class NotAGuestUser(AuthError):
    """The user exists, and it is not one this surface manages.

    User management here covers guests and only guests. An admin's own password is changed from
    their profile and recovered from the console tool, and there is no second admin to manage,
    so refusing every admin here is one rule that stands in for three: you cannot delete yourself,
    you cannot lock yourself out, and the last admin cannot be removed.
    """


class MasterKeyCorrupted(AuthError):
    """The password verified against its hash but did not open the wrapped master key. The two are
    stored together, so this means the wrapped key is damaged: refuse rather than silently mint a
    new one, which would discard the saved logins the change was meant to preserve."""


# --- Results ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class User:
    """One user, as the management screen sees it.

    No hash, no wrapped key, no PIN: not because the screen has no use for them but because a
    shape that carries them is a shape that ends up in a response body one refactor later. What is
    here is what an admin needs to decide whether to disable, reset or remove somebody.
    """

    id: str
    username: str
    role: str
    disabled: bool
    created_at: int


@dataclass(frozen=True, slots=True)
class Authenticated:
    """What a successful setup or login yields. The router turns the token into a cookie and hands
    the CSRF token back in the body for the client to echo on later requests."""

    user_id: str
    username: str
    role: str
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class GeneratedUser:
    """An invented guest, and the password that goes with it, once.

    The password is here because this is the only moment it exists in the clear. It is shown, and
    then it is gone: nothing stores it, no later read hands it back, and it is not logged. A caller
    that loses it resets the password rather than looking it up.
    """

    user: User
    password: str


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    """Who a live session belongs to, and whether Sift is locked on it.

    Both come out of one read because both are needed on every request, and because the second is
    meaningless without the first. `locked` is the app lock (the session is inert until the PIN
    arrives) and it is a different question from whether Hidden is open, which is a fact about
    this browser and lives in memory.
    """

    user_id: str
    locked: bool


# --- Statements ------------------------------------------------------------------------------
#
# users and sessions are this slice's own tables. Written out in full and bound by parameter;
# nothing here is assembled from anything.

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

# Every request that names a session runs this, so it is declared a point read and runs on the
# event loop where the machine allows it: a lookup by a unique hash is one seek, and handing one
# of those to a thread costs more than the seek. See `point_read`.
_FIND_SESSION = point_read(
    "auth.session_by_token",
    """
SELECT id, user_id, last_seen_at, expires_at, locked_at, unlock_failures
  FROM sessions WHERE token_hash = ?
""",
)

_TOUCH_SESSION = "UPDATE sessions SET last_seen_at = ? WHERE id = ?"

# The device a session runs on and its kind of window (`kernel/client.py`). See `note_device`.
_NOTE_DEVICE = "UPDATE sessions SET device_id = ?, client_kind = ? WHERE token_hash = ?"

# The app lock, on the row rather than on the screen. See `lock_app`.
_LOCK_SESSION = "UPDATE sessions SET locked_at = ?, unlock_failures = 0 WHERE id = ?"
_UNLOCK_SESSION = "UPDATE sessions SET locked_at = NULL, unlock_failures = 0 WHERE id = ?"
_COUNT_UNLOCK_FAILURE = "UPDATE sessions SET unlock_failures = unlock_failures + 1 WHERE id = ?"

_DELETE_SESSION_BY_ID = "DELETE FROM sessions WHERE id = ?"

_DELETE_SESSIONS_FOR_USER = "DELETE FROM sessions WHERE user_id = ?"

# Every session except the one whose token hashes to the value passed. Passing a value no session
# holds (the empty string) therefore deletes them all.
_DELETE_OTHER_SESSIONS = "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?"

_COUNT_LIVE_SESSIONS = "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ? AND expires_at > ?"

# --- user management ---
#
# Ordered oldest first, which puts the one admin at the top of the list on every install: the user
# that made the instance is the one the reader is looking for their bearings against.
#
# By ID rather than by `created_at`, and the two are not the same promise. `created_at` is a
# wall-clock second, and a wall clock steps backwards: a user made after another can carry a
# smaller number and sort above it. An ID is a ULID minted under a floor that never goes down (see
# `kernel.ids.new_id`, which exists for exactly this), so it is the one column here that really
# does say which user came first. The timestamp is still stored and still shown; it is simply
# not what the order rests on.
_LIST_USERS = """
SELECT id, username, role, disabled, created_at FROM users ORDER BY id
"""

# `renames` and `renames_since` ride along because renaming reads the allowance off the same row
# it has already fetched. Naming the columns rather than SELECT * is deliberate: this row is
# handed to `_user_from_row`, and a wildcard here would quietly widen what a caller can reach
# to include the password and PIN hashes.
_MANAGED_USER_BY_ID = (
    "SELECT id, username, role, disabled, created_at, renames, renames_since "
    "FROM users WHERE id = ?"
)

_SET_DISABLED = "UPDATE users SET disabled = ? WHERE id = ?"

# A name and nothing else. The id stays, so every grant, rating and hidden row keyed on it is
# untouched, and the wrapped master key is not re-derived because it never depended on the name.
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
    """The one place auth logic lives. One per application, built at boot and held on app.state.

    Held rather than reached for through a module global so a test can hand it a different
    database, queue and clock.
    """

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
        # Defaulted rather than required: an unlocked vault is per-session state that only the vault
        # routes ever set, so a test that is not about the vault should not have to build one.
        self._vault_unlocks = vault_unlocks or VaultUnlockStore()
        # Asked, not decided here. Whether a PIN may reopen a locked session is a preference this
        # slice does not own, and the setting belongs to another one, so the question arrives as
        # a callable from the composition root, which is the one place that knows both.
        #
        # Absent means no, which is the stronger answer: with nobody to ask, locking ends the
        # session and the password is what comes back.
        self._may_reopen_with_pin = may_reopen_with_pin
        # Told, not asked. Some work can only be done once the master key exists (opening a sealed
        # thing that has to be running rather than fetched on demand) and until a password login
        # happens there is no key to do it with. This slice does not know what that work is; it
        # knows the moment, so the composition root hands it something to call.
        self._on_key_available = on_key_available
        self._queue = queue
        self._session_ttl = session_ttl_seconds
        self._clock = clock
        # How the login tarpit's delay is actually served. Injectable so a test can drive the
        # escalation without spending real seconds asleep.
        self._sleep = sleep
        self._login_tarpit = login_tarpit or Tarpit(
            grace=LOGIN_TARPIT_GRACE,
            base_delay_seconds=LOGIN_TARPIT_BASE_SECONDS,
            max_delay_seconds=LOGIN_TARPIT_MAX_SECONDS,
            forget_after_seconds=LOGIN_TARPIT_FORGET_SECONDS,
            max_keys=LOGIN_TARPIT_MAX_KEYS,
        )
        # The same tarpit again, keyed by where the attempts come from. Keyed by the name alone, a
        # neighbour on the LAN could make an admin's own sign-in wait thirty seconds per attempt
        # by guessing at that admin's name; keyed by the address alone, one guesser's run would be
        # split across the names they try. Both are counted, and the longer wait is served.
        self._client_tarpit = Tarpit(
            grace=LOGIN_TARPIT_GRACE,
            base_delay_seconds=LOGIN_TARPIT_BASE_SECONDS,
            max_delay_seconds=LOGIN_TARPIT_MAX_SECONDS,
            forget_after_seconds=LOGIN_TARPIT_FORGET_SECONDS,
            max_keys=LOGIN_TARPIT_MAX_KEYS,
        )
        # One sign-in at a time per username and address. Keyed by the address as well as the name
        # so that somebody guessing at a name holds up only their own attempts, never the person
        # whose name it is signing in from somewhere else.
        self._signing_in = InFlight()
        self._pin_throttle = pin_throttle or Throttle(
            max_failures=MAX_PIN_ATTEMPTS, lockout_seconds=PIN_LOCKOUT_SECONDS
        )
        # Argon2 is CPU-bound and memory-hungry by design. Every use runs on a worker thread so it
        # never stalls the event loop (otherwise one login flood would freeze the whole server,
        # media serving and health check included) and this semaphore caps how many run at once so
        # the flood cannot exhaust memory instead.
        self._hash_slots = asyncio.Semaphore(MAX_CONCURRENT_HASHES)
        # Computed once. The no-user login path verifies against this so it takes the same time
        # as a real verify, and "no such user" cannot be told from "wrong password" by a stopwatch.
        self._dummy_hash = hasher.dummy_hash()

    def _now(self) -> int:
        return int(self._clock())

    async def _hash(self, fn: Callable[..., _T], *args: object) -> _T:
        """Run one Argon2 operation (hash, verify, wrap, unwrap) off the event loop and under the
        concurrency cap. Everything expensive in this slice goes through here."""
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
        """Create the single admin, once. Raises SetupAlreadyDone if any user already exists.

        The check and the insert run inside one write transaction: two setup requests arriving
        together must not both find the table empty and both create an admin. The unique username
        would stop a literal duplicate, but not two different admins, which is the takeover this
        guards against.

        A cheap existence check runs first, before any hashing. On an instance that is already set
        up, every setup request would otherwise pay for two Argon2 operations (the key-wrap and the
        password hash) only to be turned away by the transactional guard below, which an
        unauthenticated caller could repeat to load the hash workers. The transactional recheck
        stays: it, not this, is what makes setup a one-time race-free event.
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
        """Verify a password and start a fresh session. Raises on failure.

        A run of failures on a name tarpits the next attempt on it: an escalating delay, not a
        lockout, so a correct password always still gets in and the one admin cannot be shut out of
        their own instance by someone guessing at the login. The delay is served before the
        password is even looked at, so it costs a wrong guess time whether or not the user
        exists, and says nothing about which it was.

        A new session is always minted, so a token an attacker may have planted before login does
        not survive it (session fixation). The master key is unwrapped into memory here and nowhere
        else.
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
        # Count this attempt as it starts and serve its delay before the password is even read. The
        # count is raised up front, not after the verify, so a burst of simultaneous guesses on one
        # name each take a longer wait rather than all reading the same pre-count and sliding through
        # together; a correct password still always gets in, because success clears the run below.
        delay = self._login_tarpit.reserve(key)
        if client:
            # The longer of the two waits, never their sum: each is already escalating.
            delay = max(delay, self._client_tarpit.reserve(client))
        if delay > 0:
            security_event("login.tarpit", account=hashed(key), delay_seconds=round(delay, 3))
            await self._sleep(delay)

        row = await self._db.fetch_one(_USER_BY_USERNAME, (username,))
        stored_hash = row["password_hash"] if row is not None else self._dummy_hash
        verified = await self._hash(self._hasher.verify, stored_hash, password)

        if row is None or not verified or row["disabled"]:
            # `reserve` already counted this attempt; a wrong guess simply leaves the raised count
            # in place, so the next one on this name waits longer.
            security_event(
                "login.failed",
                account=hashed(key),
                user_id=row["id"] if row is not None else None,
            )
            raise InvalidCredentials("Incorrect username or password.")

        self._login_tarpit.record_success(key)
        if client:
            # The address's run ends too. Left counting, every sign-in from one machine (the
            # right password included) would lengthen the next one's wait, up to the ceiling, for
            # everybody behind that address: a household on one router, or a test suite.
            self._client_tarpit.record_success(client)
        if self._hasher.needs_rehash(stored_hash):
            # A floor-parameter hash (a console reset, or a box since tuned higher) verifies faster
            # than the tuned no-user dummy, and that gap is an existence oracle. This is the moment
            # to upgrade it, so a real verify and the dummy converge to the same cost.
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
        """Revoke the session behind this token, server-side. A no-op if it is already gone.

        The master key is forgotten once no live session for the user still needs it, so a
        second browser that is still logged in keeps working.
        """
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        if row is None:
            return
        user_id = row["user_id"]
        await self._db.execute(_DELETE_SESSION_BY_ID, (row["id"],))
        # The vault closes with the session that opened it, and only that one: another browser
        # still signed in keeps whatever it unlocked for itself.
        #
        # This is the tidy exit, not the only one. A session that expires, or that a password change
        # or an out-of-process reset revokes, leaves its entry behind: the store is keyed by the
        # session and nothing walks it. Those leftovers are inert, because the token is dead and can
        # never be matched again, and the store caps itself so they cannot accumulate without bound.
        self._vault_unlocks.lock(hash_token(token))
        if await self._live_session_count(user_id) == 0:
            self._master_keys.forget(user_id)
        log.info("auth.logout", user_id=user_id)

    async def username_of(self, user_id: str) -> str | None:
        """The user's username, read fresh. Used to show who is signed in without trusting the
        client to say."""
        row = await self._db.fetch_one("SELECT username FROM users WHERE id = ?", (user_id,))
        return None if row is None else str(row["username"])

    async def resolve_session_state(self, token: str) -> ResolvedSession | None:
        """The user behind a session token and whether the app is locked on it.

        One read for both, because they are answered on every single request and the second is
        useless without the first.
        """
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        if row is None:
            return None
        now = self._now()
        # Expiry outranks the lock, deliberately. A machine left locked for a month must fall back
        # to the password rather than staying PIN-openable forever, so the ordinary lifetime keeps
        # running underneath the lock and this is the line that says so.
        if row["expires_at"] <= now:
            await self._db.execute(_DELETE_SESSION_BY_ID, (row["id"],))
            return None
        if now - row["last_seen_at"] >= SESSION_TOUCH_INTERVAL_SECONDS:
            await self._db.execute(_TOUCH_SESSION, (now, row["id"]))
        return ResolvedSession(user_id=str(row["user_id"]), locked=row["locked_at"] is not None)

    async def note_device(self, token: str, client: Client) -> None:
        """Write on this session which device it runs on and what kind of window it is.

        At sign-in, and once more for a session that began before the device was kept, the first
        time it asks who it is (the router decides which). A token that names no session writes
        nothing. The device is the browser's or the app's, never the person's, and outlives the
        session; see `kernel/client.py`.
        """
        await self._db.execute(_NOTE_DEVICE, (client.device, client.kind, hash_token(token)))

    # --- the app lock ------------------------------------------------------------------------

    async def lock_app(self, token: str) -> LockOutcome:
        """Shut this session, and say which way it was shut.

        Two shapes, and WHICH ONE RUNS IS DECIDED HERE rather than by the caller. The setting and
        the PIN are both the server's, and a client that reads them for itself gets it wrong the
        first time it asks before an answer has landed: pressing the shortcut soon after opening a
        tab would sign out somebody who had a PIN and had asked for it to work.

        The mark goes on the row, so the server refuses this session everywhere: another tab, a
        reload, the cookie replayed at the API by hand. A lock drawn over the screen would leave all
        three working, which is why this is not that.

        Hidden is shut at the same time. The two are separate acts and the PIN opens them
        separately, but a lock that left hidden things revealed underneath would put them back on
        screen the moment it was opened.

        Failures are reset here rather than left to accumulate across locks: the count exists to cap
        one sitting's guessing, and a session that was opened correctly has answered for the last
        one.
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
        """Whether Sift is locked on this session.

        Asked by the one route a locked session may still call, so the interface can know before it
        draws anything. Without it the shell learns only when some later request is refused, and
        the library is on screen until then.
        """
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        return row is not None and row["locked_at"] is not None

    async def unlock_app_with_password(self, token: str, password: str) -> bool:
        """Open a locked session with the user's password. False when it is wrong.

        Always available, whatever the PIN setting says, and that is the point of it. Locking shuts
        a session; it does not throw one away. The password is what has always reopened a session,
        and the PIN is a shortcut past it for somebody who asked for one, not a replacement, and
        not something whose absence should cost anybody their session and make them type a username
        again.

        Not counted against the PIN allowance. They are different secrets: a six-digit PIN needs a
        hard cap because it is guessable, and spending a passphrase attempt out of that same small
        budget would let somebody shut themselves out of their own session by mistyping. A wrong
        password here is slowed by the same tarpit that slows one at the front door.

        Hidden stays shut, for the reason the PIN route gives: the same act twice, one at a time.
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
        """Whether the lock screen OFFERS the PIN for this user.

        Both halves: the setting turned on, and a PIN that exists. It decides what is offered and
        nothing else: the password always reopens a locked session, so a false here is one fewer
        way in rather than no way in.
        """
        if self._may_reopen_with_pin is None:
            return False
        if not await self.has_pin(user_id):
            return False
        return await self._may_reopen_with_pin(user_id)

    async def unlock_app(self, token: str, pin: str) -> bool:
        """Open a locked session with the PIN. False when the PIN is wrong.

        It unlocks a session that already exists; it never creates one. That is what stops the PIN
        being a second, weaker way in: a browser with no session has nothing to unlock, so it is
        asked for the password. The property falls out of the design rather than needing a rule.

        A run of wrong PINs destroys the session, and the caller lands back at the password. A PIN is
        short by design and can be tried against a session sitting in front of somebody, so a rate
        limit alone would leave the guessing open indefinitely; this caps it at a handful.

        Hidden is deliberately NOT opened. The same secret, two acts: you come back to the app with
        your hidden things still out of sight, because returning to a screenful of them is what the
        lock was raised to prevent.

        The master key is not touched. It is held per user and unwrapped at sign-in, so a locked
        session keeps it in memory where a full sign-out would drop it. Reaching it needs the machine
        itself, which is already past every control here; signing out remains the stronger act and
        stays one keystroke away.
        """
        row = await self._db.fetch_one(_FIND_SESSION, (hash_token(token),))
        if row is None:
            return False
        user_id = str(row["user_id"])
        # The setting has to mean something here or it means nothing anywhere. "Let the PIN unlock
        # Sift itself", turned off, must actually stop the PIN from doing that. Otherwise it is a
        # switch that changes only which box the lock screen draws, while the shortcut it claims to
        # withdraw still works for anybody who knows the six digits.
        if not await self.may_reopen_with_pin(user_id):
            log.info("auth.app_unlock_pin_not_offered", user_id=user_id)
            return False
        # Throttled like every other PIN check, so unlocking is not a second door with a fresh
        # allowance behind it.
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
        # Hidden stays shut on the way back in, and this is the line that keeps it shut rather than
        # the one that shut it. Locking clears the same entry, but a session marked locked by any
        # other means would otherwise come back revealed, and coming back to a screenful of hidden
        # things is precisely what the lock was raised to prevent.
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
        """Change a user's own password. The old one is required.

        For a user who holds a master key, the key is re-wrapped under the new password and
        the data it protects is never touched, so the saved logins survive a password change. A
        guest holds no key, so it is only a re-hash.

        Every OTHER session the user has is revoked. Changing the password is what someone does
        when they think a session has been taken, so a cookie captured before the change must not
        keep working after it. The session making the change (`current_token`) is kept, so the
        person doing it is not signed out of their own browser.
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
        # The master key value did not change, so the copy the surviving session holds is still
        # correct. But if the change came from outside a session (no token kept), nothing is left to
        # use it, so drop it rather than leave it in memory with no way to reach it.
        if await self._live_session_count(user_id) == 0:
            self._master_keys.forget(user_id)
        log.info("auth.password_changed", user_id=user_id)

    # --- PIN -------------------------------------------------------------------------------

    async def set_pin(self, user_id: str, pin: str, current_password: str) -> None:
        """Set or change the PIN. The current password is required, so an unattended unlocked
        screen cannot be used to plant one."""
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
        """Whether this user has a PIN at all.

        The vault is opened with the PIN and with nothing else, so a user without one has no
        way back into it. That is why the screens ask this before they offer to conceal anything:
        putting something in a vault you cannot open is not privacy, it is loss.
        """
        row = await self._db.fetch_one(_USER_BY_ID, (user_id,))
        return row is not None and row["pin_hash"] is not None

    async def verify_pin(self, user_id: str, pin: str) -> bool:
        """Check the PIN that unlocks a locked live session. Rate-limited, and it never touches the
        master key: the PIN is a screen lock, not a key, and unlocking one is not unwrapping the
        other. A cold session has no key for a PIN to reach even if this wanted to."""
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
        """Every user on this instance, admin included.

        An admin is in the list rather than filtered out of it. They cannot be acted on from here
        (every write below refuses one), but leaving them out would make the list disagree with
        the sharing screen, which has to name them, and would leave an admin wondering whether the
        instance really does have only the users shown.
        """
        rows = await self._db.fetch_all(_LIST_USERS)
        return [_user_from_row(row) for row in rows]

    # --- users ---------------------------------------------------------------------------------
    #
    # Everything below this line changes the list of users, which one screen draws and only an
    # admin may see. Everything ABOVE it (signing in and out, locking, unlocking, changing a
    # password, ending sessions) writes rows that no screen lists: they decide whether a request
    # is answered at all, and a screen that drew them would be a screen showing somebody's session
    # tokens. So those are deliberately silent, and this is where that stops being true.

    async def _say(
        self,
        sql: str,
        params: tuple[object, ...],
        *,
        event: tuple[Viewer, str, str, str, dict[str, object] | None] | None = None,
    ) -> None:
        """Write to the list of users, and tell the screen that draws it.

        Every admin, because that screen is admin-only: who else may reach this library is not a
        guest's business. Announced after the write rather than on its commit, since these are
        single statements outside any transaction of their own.

        `event` is what somebody DID, and only three of this class's writes carry one: a user
        made, turned off or removed. The rest are CREDENTIALS (a password rehashed, a PIN set, a
        session stamped) and a record of those is a record of nothing anybody can act on with
        detail that is nobody's business. The user list itself already draws the state; what it
        cannot say is when it changed and who changed it.
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
        """One line saying what was done to a user, in the transaction that did it.

        The username is a snapshot and never a lookup, exactly as every other subject here is: the
        delete takes the row with it, so a name read afterwards is no name at all, and "who did I
        remove" is the one question a record of users is opened to answer.
        """
        await record_event(
            connection,
            actor=Actor.user(by.id),
            verb=verb,
            # `login` and not `username`: that word is a name on a Site. See `SubjectKind`.
            subject=Subject(kind="login", id=user_id, name=username),
            payload=None if payload is None else json.dumps(payload),
        )

    async def create_guest(self, by: Viewer, username: str, password: str) -> User:
        """Add a guest, with a password an admin chooses and hands over.

        No email, so no invitation and no reset link: an admin sets a first password and tells the
        person what it is, and the person changes it from their profile whenever they like. That is
        the whole bootstrap, and it is the one that adds no machinery: a token flow would need a
        second delivery channel this application deliberately does not have.

        A guest holds no master key. Saved site logins belong to an admin, wrapped under that
        admin's password, and a guest never reaches the downloader that uses them, so there is no
        key to wrap here and the columns for one stay empty.
        """
        cleaned = username.strip()
        if not cleaned:
            raise UsernameTaken("A username can't be blank.")
        validate_password(password)
        password_hash = await self._hash(self._hasher.hash, password)
        user_id = new_id()
        now = self._now()

        # Checked and inserted inside one write, for the same reason setup is: two admins adding
        # the same name at once must not both find it free. The unique collation would catch the
        # duplicate anyway, but as a database error rather than as the sentence below.
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
        """Invent a guest: a name nobody has, a strong password, both handed back once.

        The pair is returned here and nowhere else. Nothing stores the password in the clear, no
        later read hands it over again, and it is never logged, which is the point of showing it
        once and saying so on the screen.

        Both come from the OS random source through `secrets`, never from a seeded generator: a
        password anybody could reproduce from a known seed is not a password.

        The name is retried rather than made unique by construction, because "guest-4821" is a name
        a person can read out loud and a thirty-character one is not. A handful of attempts is
        plenty at four digits against a household's worth of users, and running out raises
        rather than falling back to something longer and unpronounceable: an unexplained name is
        worse than an error somebody can act on.
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
        """Refuse a rename once this user has had their allowance for the day.

        Charged before the name is looked at: otherwise "is this name taken" is answered for
        free, which is the question the allowance exists to stop anybody asking repeatedly.
        """
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
        """Change what a user is called. Nothing else about it moves.

        Verified safe rather than assumed: the master key is wrapped by the password against a
        stored random salt and is not derived from the username, so a rename re-wraps nothing and
        costs no saved site login. The user keeps their id, which is what every grant, rating and
        hidden row is keyed on.

        No session is ended, by anybody. An admin renaming somebody must not sign them out (the
        name is a label, not a credential) and renaming yourself must not sign you out of the
        browser you did it in.

        The same refusal creating a user gives, in the same words, decided by the same read inside
        the same write: two requests claiming one name at the same moment must not both find it
        free. A rename to the name the user already holds is allowed and does nothing, so
        correcting only the capitalisation works.
        """
        cleaned = username.strip()
        if not cleaned:
            raise UsernameTaken("A username can't be blank.")

        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            existing = list(await connection.execute_fetchall(_MANAGED_USER_BY_ID, (user_id,)))
            if not existing:
                raise NoSuchUser("there is no such user")

            # The cap is on renaming yourself, not on an admin renaming somebody. An admin can read
            # the user list on the same screen, so nothing here is being kept from them and a
            # limit would only be in the way when they are tidying up several at once.
            if not counted_against_admin:
                self._charge_rename(connection, existing[0])
                await self._record_rename(connection, existing[0], user_id)

            taken = list(await connection.execute_fetchall(_USER_BY_USERNAME, (cleaned,)))
            refused = bool(taken) and str(taken[0]["id"]) != user_id
            if not refused:
                await connection.execute(_RENAME_USER, (cleaned, user_id))
                # The old name in the payload: the row is overwritten in place, so it exists
                # nowhere else. Every act on a user goes through `_user_event`, so the user list
                # can say who was renamed as well as who was removed.
                await self._user_event(
                    connection,
                    by,
                    "renamed",
                    user_id,
                    cleaned,
                    {"before": str(existing[0]["username"])},
                )
        # Raised once the charge has landed: a refusal inside the write would take it back.
        if refused:
            raise UsernameTaken(
                f"There's already a user called {cleaned!r}."
                if counted_against_admin
                else "That name can't be used."
            )

        security_event("account.renamed", user_id=user_id)
        return replace(_user_from_row(existing[0]), username=cleaned)

    async def set_user_disabled(self, by: Viewer, user_id: str, disabled: bool) -> User:
        """Turn a guest user off, or back on.

        Disabling ends their live sessions rather than only refusing the next one. The refusal
        alone would already be enough (the role and the disabled flag are re-read on every single
        request, so a disabled user's next call fails whatever cookie it holds), but leaving
        the session rows behind means re-enabling silently signs them back in on a browser that was
        left open, which is not what "off" looked like on screen.
        """
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
        """Remove a guest user, and everything hanging off them.

        Their sessions and every share and restrict made to them go with the row, by foreign key,
        which is the one place in the access model where a cascade is available, because a grant's
        subject really is a `users` row. What the grant *points at* is the polymorphic side that
        cannot cascade, and that is handled where objects are deleted, not here.
        """
        user = await self._manageable(user_id)
        await self._say(
            _DELETE_USER, (user_id,), event=(by, "deleted", user_id, user.username, None)
        )
        self._master_keys.forget(user_id)
        security_event("account.deleted", user_id=user_id)

    async def reset_user_password(self, user_id: str, new_password: str) -> None:
        """Set a guest's password for them, without knowing the old one.

        Safe to do without the old password precisely because it is a guest: they hold no wrapped
        master key, so there is nothing that only the old password could have opened and nothing
        that is lost by not having it. The same operation on an admin destroys their saved site
        logins, which is why the console tool warns before doing it and why this refuses.

        Every session they hold ends. A reset is what an admin does when they think somebody else
        has the password, so the sessions that password already opened must not survive it.
        """
        await self._manageable(user_id)
        validate_password(new_password)
        new_hash = await self._hash(self._hasher.hash, new_password)
        await self._say(_REHASH_LOGIN, (new_hash, user_id))
        await self.revoke_all_sessions(user_id)
        security_event("account.password_reset", user_id=user_id)

    async def _manageable(self, user_id: str) -> User:
        """The user this operation is about, having proved it is one this surface may touch.

        Both refusals happen before anything is written, and they are different answers on purpose:
        an id that names nothing is a 404 and an admin is a 403. Telling them apart costs nothing
        here: the caller is already an admin, who can list every user on the instance.
        """
        row = await self._db.fetch_one(_MANAGED_USER_BY_ID, (user_id,))
        if row is None:
            raise NoSuchUser("there is no such user")
        user = _user_from_row(row)
        if user.role != "guest":
            raise NotAGuestUser("only guest users are managed here")
        return user

    async def unlock_secrets(self, user_id: str, password: str) -> bool:
        """Put a user's master key back in memory, without ending its session.

        The key exists only while the process does, so a restart leaves a perfectly valid session
        signed in and unable to read a saved login or a tunnel. Signing out and back in would fix
        that, and telling somebody who already had to "log in with your password" helps nobody.
        This is that, without the round trip.

        False for a wrong password and for a user who holds no key at all (a guest), because
        neither has anything to unlock and telling them apart would say which is which.
        """
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
        """Unwrap the user's master key into the in-memory store, and release waiting jobs.

        Skipped for a user with no wrapped key (a guest). If the key is present but will not
        unwrap under a password that just verified, the wrapped key is damaged: the login still
        succeeds (the user can browse), but the secrets stay unavailable, which is the same
        state a not-logged-in machine is in, and jobs that need them wait.
        """
        if row["mk_wrapped"] is None:
            return
        wrapped = WrappedKey(row["mk_wrapped"], row["mk_nonce"], row["mk_kdf_salt"])
        master_key = await self._hash(unwrap_master_key, wrapped, password)
        if master_key is None:
            log.warning("auth.master_key_unwrap_failed", user_id=row["id"])
            return
        self._master_keys.store(row["id"], master_key)
        await self._release_blocked_jobs()
        # Every admin's window is told, whether or not anything was parked: a window still drawing
        # the unlock bar re-reads its session on the queue's bell, and an unlock in one window
        # would otherwise leave the bar standing in another until a reload. Nothing is written, so
        # it is said now rather than on a commit.
        announce_now(EVERY_ADMIN, About.JOBS)
        await self._announce_key(master_key)

    async def _announce_key(self, master_key: bytes) -> None:
        """Let whatever was waiting for a key know there is one. Never at the cost of the login:
        a failure here is logged and the sign-in still succeeds, because being unable to start
        something is not a reason to keep somebody out of their own library."""
        if self._on_key_available is None:
            return
        try:
            await self._on_key_available(master_key)
        except Exception:
            log.warning("auth.key_listener_failed")

    async def _release_blocked_jobs(self) -> None:
        """Return every job that was waiting for a login to the queue. Idempotent, and a no-op when
        nothing is blocked."""
        if self._queue is not None:
            await self._queue.unblock()

    async def _create_session(
        self, user_id: str, ttl_seconds: int | None = None
    ) -> tuple[str, str]:
        """Mint a session row and return (session_token, csrf_token). The token is stored only as
        its hash; the CSRF token is derived from it and stored nowhere.

        HOW LONG IT LASTS IS THE CALLER'S TO SAY. It is an installation policy, stored as a setting
        and changeable from the Privacy screen, and the router is what can read one. `None` is the
        value this service was built with, which is the shipped default: it exists so that every
        other caller, including a test that has no settings hub, mints a session of the ordinary
        length without saying so.

        A row's expiry and the cookie's `max-age` are two copies of one answer, so both are given
        the same number by the same caller in the same breath, rather than each reading a field of
        its own and agreeing by chance.
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
