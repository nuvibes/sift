# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers auth is paced by, in one place, with the reason beside each.

These are invariants, not settings. A password floor that varied by install would be a floor a
weak install could lower; a lockout window one operator could widen to nothing is a lockout that
protects nobody. The one number that genuinely depends on the machine (how hard to make the
password hash) is derived from the hardware at boot, and even that is clamped from below here.
"""

from __future__ import annotations

# --- Password policy -------------------------------------------------------------------------

PASSWORD_MIN_LENGTH = 10
"""Characters, at least. Paired with the class requirement and the breach list below."""

PASSWORD_MAX_LENGTH = 1024

GENERATED_PASSWORD_LENGTH = 20
"""How long a password Sift invents for a one-click guest.

Twenty characters from a 57-character alphabet is about 116 bits, which is far past anything a
household threat model needs and is chosen for a different reason: it is short enough to read off
one screen and type into another without the person giving up and asking for something simpler.
"""

GENERATED_NAME_ATTEMPTS = 20
"""How many times a generated username is retried before giving up.

The name is a word and four digits so a person can say it out loud, which means collisions are
possible rather than impossible. Twenty attempts against a household's worth of users will not
run out; if it somehow does, the caller is told to add one by hand rather than being handed a
longer, unreadable name it never asked for.
"""
"""An upper bound only so a megabyte of text cannot be handed to the hasher as a denial of
service. Well above anything a person types."""

# --- PIN policy ------------------------------------------------------------------------------

PIN_DIGITS = 6
"""A PIN is exactly this many digits wherever one is set.

It unlocks a screen that is already signed in. It is short on purpose, which is exactly why it may
never stand in for the password: a million combinations is a small space to anybody holding the
file. One length rather than a range, so every screen that asks for a PIN draws the same six cells
and a PIN is complete the moment the last one is filled.

A shorter PIN set under an older rule still verifies: only its hash is kept, so its length is
unknown until it is typed, and refusing it would lock its owner out of Hidden. The client asks for
a six-digit one when a shorter one opens Hidden.
"""

# --- Argon2id: the floor, and the room above it ----------------------------------------------
#
# The hasher is tuned to the machine at boot (a stronger box can afford a costlier hash), but
# never below this. A fast hash on a weak box is still a fast hash for whoever steals the file and
# runs it on a fast one.
#
# The floor sits comfortably above the usual minimum recommendation (19 MiB, one pass): 64 MiB and
# three passes. Memory is raised toward the ceiling on a machine that has the RAM to spare, because
# memory-hardness is what a GPU cannot cheaply parallelize away.

ARGON2_MIN_TIME_COST = 3
"""Passes over memory."""

ARGON2_MIN_MEMORY_KIB = 64 * 1024
"""64 MiB. The working set each hash must fill."""

ARGON2_MAX_MEMORY_KIB = 256 * 1024
"""256 MiB. A ceiling so a big-RAM box does not make each login take a noticeable second, and so
a burst of logins cannot exhaust memory."""

ARGON2_PARALLELISM = 1
"""Lanes. One, deliberately: more lanes help an attacker with many cores at least as much as the
defender, and Sift verifies one password at a time."""

ARGON2_HASH_LENGTH = 32
"""Bytes of output. Also the size of the key derived to wrap the master key."""

ARGON2_SALT_LENGTH = 16

# Memory is raised in proportion to total RAM, capped at the ceiling. A hash should not be allowed
# to claim so much that concurrent logins, or the rest of the process, are starved, so only a
# small fraction of physical memory is offered to it.
ARGON2_RAM_FRACTION_DENOMINATOR = 64
"""One sixty-fourth of total RAM is offered to the hash, then clamped to [floor, ceiling]. On a
4 GB box that is 64 MiB (the floor); on 16 GB it is 256 MiB (the ceiling)."""

MAX_CONCURRENT_HASHES = 4
"""How many Argon2 hashes may run at once. Each hash is deliberately slow and memory-hungry and
runs on a worker thread, off the event loop, so without a cap, a flood of login attempts would
start one per request and exhaust either the cores or the memory (a hash claims up to the ceiling
above). This bounds both: extra attempts wait their turn, which throttles online guessing for free
while the rest of the server stays responsive. Four is comfortable for one household and its peak
memory (4x the ceiling) is well within any machine that earns the ceiling in the first place."""

# --- Login tarpit ----------------------------------------------------------------------------
#
# Login is NOT locked out. The sign-in is the one admin's, and a lockout on it is a denial an
# attacker triggers at will just by guessing the username: the sole operator, shut out of their
# own instance from the login page. Instead a run of failures makes each further attempt on that
# name slower, up to a cap: guessing is throttled to a crawl while a correct password always still
# gets in. Counted per submitted username, existing or not, so the delay is not an existence oracle.

LOGIN_TARPIT_GRACE = 3
"""Free attempts before any delay, so ordinary mistyping is not punished."""

LOGIN_TARPIT_BASE_SECONDS = 1.0
"""The first delayed attempt waits this long; each further failure doubles it."""

LOGIN_TARPIT_MAX_SECONDS = 30.0
"""The ceiling. Reached after a handful of failures, it holds sustained guessing on one name to
about two attempts a minute (useless against the password policy), without ever refusing the real
one. The delay is counted and served per attempt as it arrives (`Tarpit.reserve`), so a burst that
arrives together escalates instead of sliding through at one short wait; how many verifies then run
at once is bounded separately by MAX_CONCURRENT_HASHES."""

LOGIN_TARPIT_FORGET_SECONDS = 900
"""Fifteen minutes of quiet and the run of failures is forgotten, so an earlier fumble does not go
on slowing a later, honest login."""

LOGIN_TARPIT_MAX_KEYS = 4096
"""A hard cap on how many distinct submitted names the tarpit holds at once. The map is keyed by the
name tried, so a flood of made-up names would otherwise grow it without bound: a slow memory
exhaustion. Far above the handful a real household produces; once reached, forgotten runs are dropped
first (they impose no delay anyway) and then the least recently seen, so memory is bounded whatever
an attacker sends."""

# --- PIN throttle ----------------------------------------------------------------------------
#
# Tighter than the login throttle: the PIN is short, so a few wrong tries is already suspicious,
# and unlike a login there is no offline-recovery story to fall back on if it locks.

#: How many times a user may rename ITSELF within one window, and how long that window is.
#:
#: Three a day. Renaming is not dangerous (a name is a label and the id never moves), but the
#: refusal on a name that is already taken is a question anybody may ask, and asked freely it turns
#: "who has a sign-in here" into something a guest can enumerate one guess at a time. A rate is
#: what closes that, and three is above any honest need: somebody settling on a name gets it right
#: in one or two, and nobody legitimately needs a fourth before tomorrow.
#:
#: An admin renaming somebody else is not counted. They can read the user list on the screen
#: they are standing on, so there is nothing here being kept from them.
MAX_RENAMES_PER_WINDOW = 3
RENAME_WINDOW_SECONDS = 24 * 60 * 60

MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 300

MAX_UNLOCK_FAILURES = 3
"""Wrong PINs against a locked session before the session is destroyed and the password is the only
way back.

Separate from the throttle above and stricter, because the two bound different things. The throttle
bounds a RATE (five tries, then wait five minutes, then five more), which is right for a secret
somebody has to be signed in to try at all. A locked session is a machine sitting in front of
whoever picked it up, with all the time in the world, so a rate limit alone leaves a four-digit
secret open to being worked through. This bounds the TOTAL instead: three wrong answers and the
session is gone.

Three rather than one, because a mistyped digit on a screen with no feedback is ordinary; and
rather than ten, because ten guesses at four digits is a real fraction of the space. Destroying the
session costs the person their password, which they have."""

# --- The vault -------------------------------------------------------------------------------

VAULT_CONCEALMENT_KEY = "vault.concealment"

#: How long a sign-in lasts before it has to be done again.
#:
#: A setting rather than an environment variable, which on an application somebody installs is
#: not a choice at all. Days rather than seconds because that is the unit the answer is thought in,
#: and the one place the conversion happens is `session_seconds_from` below.
SESSION_DAYS_KEY = "sessions.stay_signed_in_days"

#: What a sign-in lasts when nothing has been stored: seven days.
DEFAULT_SESSION_DAYS = 7

_SECONDS_PER_DAY = 24 * 60 * 60


def session_seconds_from(value: object) -> int:
    """The stored days as seconds, with the default for anything that is not a number.

    Separate from the validator on purpose, and this one matters more than most: it is what decides
    how long a session lives, so a value it could not read must fall back to the shipped answer
    rather than to zero. A zero here is every user signed out on the next request.
    """
    if not isinstance(value, str | int | float):
        return DEFAULT_SESSION_DAYS * _SECONDS_PER_DAY
    try:
        days = int(value)
    except ValueError:
        return DEFAULT_SESSION_DAYS * _SECONDS_PER_DAY
    return max(1, days) * _SECONDS_PER_DAY


"""The preference that decides how the vault hides what is in it, by the name it is stored under.

The name lives here because this is where it is *read*: resolving a session is what turns the
stored choice into the viewer every scoped query is answered against, and that resolution belongs
to auth. The vault feature registers the preference under this same constant, so there is one
spelling of it and a rename cannot leave a reader looking for a key nobody writes.
"""

# --- Sessions --------------------------------------------------------------------------------
#
# The cookie name and the CSRF header name are in `kernel.http`: they are what
# the browser and the server agree on rather than numbers this feature is paced by,
# and things that are neither sign-in nor the browser need them.

CLEAR_CACHE_ON_SIGN_OUT = '"cache"'
"""Sent on sign-out, and only on sign-out: throw away everything stored for this origin.

Sift serves the pictures it generates at an address that names their contents, so a browser may
keep them for a week without asking. That is what makes a grid cost nothing on a second visit, and
it is also a copy on the disk that no permission check stands in front of: signing out revokes
the session, and it cannot reach into a store on somebody else's machine.

This is the instruction that does reach it. `"cache"` and nothing else: `"cookies"` would sign the
user out of every other tab mid-request, and `"storage"` would throw away the interface's own
remembered state (the grid size, the sort, the sidebar), which nobody signing out is asking for.

**Locking the vault deliberately does not send this**, and does not need to: a concealed picture is
never given a keepable address in the first place, so there is nothing stored to clear and locking
takes effect on the very next request.

Ignored outside a secure context, so over plain HTTP this does nothing at all. That is the
browser's rule rather than a choice here, and it is worth knowing rather than discovering: the
protection arrives when Sift is put behind TLS, and until then signing out leaves the pictures
readable on that machine until they expire."""

SESSION_TOKEN_BYTES = 32
"""Entropy in the opaque session token and the CSRF token. 256 bits, from the OS CSPRNG."""

SESSION_TOUCH_INTERVAL_SECONDS = 60
"""last_seen_at is only rewritten when it is at least this stale. Advancing it on every request
would put a database write in front of every read and serialize the whole app behind one lock, to
record a timestamp nobody reads to the second."""
