# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers auth is paced by: invariants, not settings, so no install can weaken them."""

from __future__ import annotations

PASSWORD_MIN_LENGTH = 10
#: So a megabyte of text cannot be handed to the hasher.
PASSWORD_MAX_LENGTH = 1024
GENERATED_PASSWORD_LENGTH = 20
GENERATED_NAME_ATTEMPTS = 20

#: One PIN length everywhere; short, so it never stands in for the password.
PIN_DIGITS = 6

# Argon2: floors above the usual 19 MiB and one pass; boot offers a sixty-fourth of RAM, clamped.
ARGON2_MIN_TIME_COST = 3
ARGON2_MIN_MEMORY_KIB = 64 * 1024
ARGON2_MAX_MEMORY_KIB = 256 * 1024
ARGON2_PARALLELISM = 1
#: Bytes, and the size of the key that wraps the master key.
ARGON2_HASH_LENGTH = 32
ARGON2_SALT_LENGTH = 16
ARGON2_RAM_FRACTION_DENOMINATOR = 64
#: Hashes running together, so a login flood waits instead of exhausting cores or memory.
MAX_CONCURRENT_HASHES = 4

# Login is slowed, never locked out: a lockout would let anyone shut out the admin.
LOGIN_TARPIT_GRACE = 3
LOGIN_TARPIT_BASE_SECONDS = 1.0
LOGIN_TARPIT_MAX_SECONDS = 30.0
LOGIN_TARPIT_FORGET_SECONDS = 900
LOGIN_TARPIT_MAX_KEYS = 4096

#: A rename's "name taken" answer enumerates users, so it is rate-limited; admins are not.
MAX_RENAMES_PER_WINDOW = 3
RENAME_WINDOW_SECONDS = 24 * 60 * 60

MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 300

#: Wrong PINs before a locked session ends: a total, where the PIN throttle is a rate.
MAX_UNLOCK_FAILURES = 3

VAULT_CONCEALMENT_KEY = "vault.concealment"
SESSION_DAYS_KEY = "sessions.stay_signed_in_days"
DEFAULT_SESSION_DAYS = 7
_SECONDS_PER_DAY = 24 * 60 * 60


def session_seconds_from(value: object) -> int:
    """The stored days as seconds; anything unreadable is the default, never zero."""
    if not isinstance(value, str | int | float):
        return DEFAULT_SESSION_DAYS * _SECONDS_PER_DAY
    try:
        days = int(value)
    except ValueError:
        return DEFAULT_SESSION_DAYS * _SECONDS_PER_DAY
    return max(1, days) * _SECONDS_PER_DAY


#: Sent on sign-out only: the browser's cached pictures go, in a secure context.
CLEAR_CACHE_ON_SIGN_OUT = '"cache"'

SESSION_TOKEN_BYTES = 32

#: How stale last_seen_at gets before a rewrite, so reads do not each write.
SESSION_TOUCH_INTERVAL_SECONDS = 60
