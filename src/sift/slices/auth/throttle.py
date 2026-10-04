# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rate-limiting for the two places a secret is guessed at: login and the PIN.

Online guessing is answered by making it slow, but the two cases answer it differently, and the
difference matters.

The PIN uses a **lockout** (`Throttle`): after a handful of wrong tries the screen is locked for a
few minutes. That is right for a PIN: it unlocks an already-authenticated session, there is a
person at the keyboard, and stopping cold after a few misses is exactly what a screen lock should
do.

Login uses a **tarpit** (`Tarpit`), not a lockout, and on purpose. A lockout on the login is a
denial an attacker can trigger at will just by guessing the username: the sign-in is the one
admin's, so locking it shuts the sole operator out of their own instance from the login page, with
only the console reset to recover. The tarpit instead makes each further wrong attempt slower, up
to a cap: guessing is throttled to a crawl while a correct password always still gets in. The
user is never refused; only a wrong answer is made to wait.

Both are keyed by the *submitted* value (the username for login) and count failures against
that string whether or not any such user exists. Keying by real users only would leak which
usernames are real: an attacker would see the behaviour change on the names that exist and not on
the ones that do not. Every string is treated the same, so neither says anything.

State is in process memory. On a single-node app that is the whole of it; a restart clears the
counters, which at worst gives an attacker back the handful of guesses being held, and is not
worth a database write on the failure path.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Attempts:
    count: int = 0
    locked_until: float = 0.0


class Throttle:
    """Consecutive-failure lockout, by key.

    A key is whatever the caller is protecting: a submitted username for login, a user id for the
    PIN. `max_failures` failures in a row lock the key for `lockout_seconds`; a success clears it.
    """

    def __init__(
        self,
        *,
        max_failures: int,
        lockout_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        self._clock = clock
        self._by_key: dict[str, _Attempts] = {}

    def locked(self, key: str) -> bool:
        """Whether this key is currently locked out. Checked before a secret is even looked at."""
        record = self._by_key.get(key)
        if record is None:
            return False
        return bool(record.locked_until) and self._clock() < record.locked_until

    def record_failure(self, key: str) -> None:
        """Count a failed attempt, and lock the key if it has now failed too many times running."""
        record = self._by_key.setdefault(key, _Attempts())
        # A lockout that has already elapsed is a penalty served once, not a hair-trigger that
        # re-locks on the very next mistake for good. Once the window has passed, the count starts
        # over, so the window is the whole of the punishment and a lapsed lockout is a clean slate.
        if record.locked_until and self._clock() >= record.locked_until:
            record.count = 0
            record.locked_until = 0.0
        record.count += 1
        if record.count >= self._max_failures:
            record.locked_until = self._clock() + self._lockout_seconds

    def record_success(self, key: str) -> None:
        """Clear the count. A correct answer ends the run of failures that was being watched."""
        self._by_key.pop(key, None)


@dataclass
class _Run:
    count: int = 0
    last_at: float = 0.0


class Tarpit:
    """Escalating delay on a run of failures. Used for login, where a lockout must not be possible.

    A tarpit never refuses a correct answer: it only makes a wrong one slow. A short grace of free
    attempts absorbs ordinary mistyping; past it the delay doubles with each failure, up to a
    ceiling. A success clears the run, and a quiet period long enough forgets it, so an old fumble
    does not slow a later login. Keyed like the lockout, by the submitted string.
    """

    def __init__(
        self,
        *,
        grace: int,
        base_delay_seconds: float,
        max_delay_seconds: float,
        forget_after_seconds: float,
        max_keys: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._grace = grace
        self._base = base_delay_seconds
        self._max = max_delay_seconds
        self._forget_after = forget_after_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._by_key: dict[str, _Run] = {}

    def _forgotten(self, run: _Run) -> bool:
        return bool(run.last_at) and self._clock() - run.last_at >= self._forget_after

    def _reap(self) -> None:
        """Keep the map bounded so a flood of distinct names cannot grow it without end. Forgotten
        runs go first (they impose no delay, so dropping them costs nothing), and if that is not
        enough, the least recently seen go too, until the cap holds. Evicting a key is safe: a name
        the tarpit has forgotten simply starts fresh with no delay next time, which a sprayer of
        never-before-seen names gets anyway."""
        if len(self._by_key) <= self._max_keys:
            return
        for key in [k for k, run in self._by_key.items() if self._forgotten(run)]:
            del self._by_key[key]
        while len(self._by_key) > self._max_keys:
            oldest = min(self._by_key, key=lambda k: self._by_key[k].last_at)
            del self._by_key[oldest]

    def delay(self, key: str) -> float:
        """How long the next attempt on this key must wait. Zero within the grace, or once a quiet
        period has forgotten the run. A read: it never itself locks anything in."""
        run = self._by_key.get(key)
        if run is None or self._forgotten(run):
            return 0.0
        return self._delay_for(run.count)

    def reserve(self, key: str) -> float:
        """Count this attempt as it starts, and return how long it must wait before being judged.

        This is `record_failure` and `delay` fused into one atomic step, and the fusing is the
        point. `delay()` is a read of a count that only `record_failure()` raises, and those are two
        steps: attempts that arrive together all read the same pre-raise count, all wait the same
        short time, and only then raise it, so the escalation never applies within a burst and the
        real ceiling on a flood is elsewhere. Counting first, and deriving the wait from the raised
        count, makes the Nth simultaneous guess on one name take the Nth delay. A correct password
        still always gets in: `record_success` clears the run once the guess is judged right.

        Every attempt is counted, not only the wrong ones: the count is provisional until the
        outcome is known, and a success wipes it. A `reserve` is therefore never paired with a
        `record_failure`; the reserve already did the counting.
        """
        run = self._by_key.setdefault(key, _Run())
        if self._forgotten(run):
            run.count = 0
        run.count += 1
        run.last_at = self._clock()
        self._reap()
        return self._delay_for(run.count)

    def _delay_for(self, count: int) -> float:
        over = count - self._grace
        if over <= 0:
            return 0.0
        return min(self._base * 2.0 ** (over - 1), self._max)

    def record_failure(self, key: str) -> None:
        """Count a wrong attempt. A run the quiet period has forgotten starts over from zero."""
        run = self._by_key.setdefault(key, _Run())
        if self._forgotten(run):
            run.count = 0
        run.count += 1
        run.last_at = self._clock()
        self._reap()

    def record_success(self, key: str) -> None:
        """A correct answer ends the run, so the next attempt on this key waits for nothing."""
        self._by_key.pop(key, None)


class InFlight:
    """The keys that have an attempt being judged right now.

    A tarpit slows each attempt, but attempts sent together serve their delays side by side, so
    the rate a flood reaches is set by the hashing rather than by the delay. One attempt at a time
    per key makes the delay the ceiling. A second attempt is turned away rather than queued, so
    nothing piles up behind a flood and the key is free again the moment the flood stops. Only
    keys in flight are held, so the set is as small as the number of requests being served.
    """

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def claim(self, key: str) -> bool:
        """Take the key for one attempt, or say it is already taken."""
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    def release(self, key: str) -> None:
        """Give the key back once the attempt has been judged, however that went."""
        self._keys.discard(key)
