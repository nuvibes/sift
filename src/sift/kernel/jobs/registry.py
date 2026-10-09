# SPDX-License-Identifier: AGPL-3.0-or-later
"""The job types the running code has claimed, and what each declared about itself."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sift.kernel.jobs.families import Family
from sift.kernel.jobs.queue import JobHeld

if TYPE_CHECKING:
    from sift.kernel.jobs.worker_pool import Handler

# Process-global: which handlers exist is a property of the running code, not of a database.
_HANDLERS: dict[str, Handler] = {}


#: What each job type is called on screen, required where the handler is declared.
_NAMES: dict[str, str] = {}

#: Which long pass each job type belongs to, declared where the handler is (see `families`).
_FAMILIES: dict[str, Family] = {}

#: What each job type comes after, which orders one file's work. Not folded into the priority,
#: which would make the rank beat arrival across the whole library.
_FOLLOWS: dict[str, str] = {}

#: The most urgent a job type ever runs, as a priority number: a clamp, so a pass asked for two
#: ways keeps one urgency.
_URGENCY: dict[str, int] = {}

#: The job types claimed whether or not their family can run here: coordinators of several products.
_NOT_GATED: set[str] = set()

#: The job types whose payload names the products a task makes, so a screen says whose work it is.
_CARRIERS: set[str] = set()

#: The job types that may only have one running: a whole-library pass run twice repeats itself.
_ALONE: set[str] = set()

#: The job types that have the whole queue to themselves: work that measures the machine.
_EXCLUSIVE: set[str] = set()

#: The job types that go after every other piece of one file's work, declared or not.
_TRAILS: set[str] = set()

#: What one file of a kind of work is called under a bar: "files looked at for faces".
_COUNTS: dict[str, str] = {}

#: The machine's own upkeep (the update check, the backup), left off Activity's list.
_UNLISTED: set[str] = set()

#: The work that runs by itself as files arrive, left off Activity's list unless a run holds it.
_BY_ITSELF: set[str] = set()


def register_handler(
    job_type: str,
    handler: Handler,
    *,
    name: str,
    family: Family = Family.OTHER,
    alone: bool = False,
    follows: str | None = None,
    trails: bool = False,
    counts: str | None = None,
    urgency: int | None = None,
    needs_ready: bool = True,
    carries_products: bool = False,
    unlisted: bool = False,
    by_itself: bool = False,
    exclusive: bool = False,
) -> None:
    """Claim a job type and its on-screen name; each flag is the registry of the same name above."""
    _refuse_registration(
        job_type, name=name, follows=follows, unlisted=unlisted, by_itself=by_itself, trails=trails
    )
    _HANDLERS[job_type] = handler
    _NAMES[job_type] = name
    _FAMILIES[job_type] = family
    _declare_claiming(
        job_type,
        follows=follows,
        urgency=urgency,
        needs_ready=needs_ready,
        carries_products=carries_products,
        alone=alone,
    )
    _declare_listing(
        job_type,
        trails=trails,
        counts=counts,
        unlisted=unlisted,
        by_itself=by_itself,
        exclusive=exclusive,
    )


def _refuse_registration(
    job_type: str, *, name: str, follows: str | None, unlisted: bool, by_itself: bool, trails: bool
) -> None:
    if job_type in _HANDLERS:
        raise ValueError(f"a handler for job type {job_type!r} is already registered")
    if not name.strip():
        raise ValueError(f"job type {job_type!r} needs a name to show on screen")
    if follows == job_type:
        raise ValueError(f"job type {job_type!r} cannot follow itself")
    if unlisted and by_itself:
        raise ValueError(
            f"job type {job_type!r} is either upkeep that is never listed or work that runs by"
            " itself, not both"
        )
    if trails and follows is not None:
        raise ValueError(
            f"job type {job_type!r} trails everything, so it follows nothing in particular"
        )


def _declare_claiming(
    job_type: str,
    *,
    follows: str | None,
    urgency: int | None,
    needs_ready: bool,
    carries_products: bool,
    alone: bool,
) -> None:
    if follows is not None:
        _FOLLOWS[job_type] = follows
    else:
        _FOLLOWS.pop(job_type, None)
    if urgency is not None:
        _URGENCY[job_type] = urgency
    else:
        _URGENCY.pop(job_type, None)
    if needs_ready:
        _NOT_GATED.discard(job_type)
    else:
        _NOT_GATED.add(job_type)
    if carries_products:
        _CARRIERS.add(job_type)
    else:
        _CARRIERS.discard(job_type)
    if alone:
        _ALONE.add(job_type)
    else:
        _ALONE.discard(job_type)


def _declare_listing(
    job_type: str,
    *,
    trails: bool,
    counts: str | None,
    unlisted: bool,
    by_itself: bool,
    exclusive: bool,
) -> None:
    if trails:
        _TRAILS.add(job_type)
    else:
        _TRAILS.discard(job_type)
    if counts is not None:
        _COUNTS[job_type] = counts
    else:
        _COUNTS.pop(job_type, None)
    if unlisted:
        _UNLISTED.add(job_type)
    else:
        _UNLISTED.discard(job_type)
    if by_itself:
        _BY_ITSELF.add(job_type)
    else:
        _BY_ITSELF.discard(job_type)
    if exclusive:
        _EXCLUSIVE.add(job_type)
    else:
        _EXCLUSIVE.discard(job_type)


def registered_handlers() -> dict[str, Handler]:
    """The registry, copied. Nothing mutates it through here."""
    return dict(_HANDLERS)


def registered_families() -> dict[str, Family]:
    """Which family every claimed job type belongs to, copied."""
    return dict(_FAMILIES)


def counted_as(job_type: str) -> str:
    """What one file of this work is called under a bar: its declared words, or its name."""
    return _COUNTS.get(job_type) or _NAMES.get(job_type, job_type)


def registered_alone() -> set[str]:
    """The job types that may only have one running at a time, copied. See `_ALONE`."""
    return set(_ALONE)


def gated_by_readiness(job_type: str) -> bool:
    """Whether this type waits for its family to be able to run here. See `_NOT_GATED`."""
    return job_type not in _NOT_GATED


def unlisted_job_types() -> frozenset[str]:
    """The job types left off Activity's list of what is happening now. See `_UNLISTED`."""
    return frozenset(_UNLISTED)


def by_itself_job_types() -> frozenset[str]:
    """The job types that run by themselves as files arrive. See `_BY_ITSELF`."""
    return frozenset(_BY_ITSELF)


def exclusive_job_types() -> frozenset[str]:
    """The job types that have the queue to themselves. See `_EXCLUSIVE`."""
    return frozenset(_EXCLUSIVE)


def registered_product_carriers() -> frozenset[str]:
    """The job types whose payload names the products a task makes. See `_CARRIERS`."""
    return frozenset(_CARRIERS)


def products_named(job_type: str, payload: Mapping[str, Any]) -> list[str] | None:
    """The products a carrier's payload names, or None for a job that is not a carrier."""
    if job_type not in _CARRIERS:
        return None
    named = payload.get("products")
    return [str(one) for one in named] if isinstance(named, list) else []


def registered_follows() -> dict[str, str]:
    """What each job type that declared one comes after, copied. See `_FOLLOWS`."""
    return dict(_FOLLOWS)


def registered_urgency(job_type: str) -> int | None:
    """The most urgent this type ever runs, or None for a type that never said. See `_URGENCY`."""
    return _URGENCY.get(job_type)


#: Failures that mean this machine cannot do the work just now, and how many seconds to hold it.
_HOLDS: dict[type[BaseException], float] = {}


def hold_on(error: type[BaseException], *, seconds: float) -> None:
    """Declare a failure that holds a job for `seconds` instead of failing it. See `_HOLDS`."""
    if seconds <= 0:
        raise ValueError("a hold needs a positive number of seconds")
    _HOLDS[error] = seconds


def held_for(error: BaseException) -> float | None:
    """How long this failure holds its job, or None where it is an ordinary failure."""
    if isinstance(error, JobHeld):
        return error.retry_in
    for kind, seconds in _HOLDS.items():
        if isinstance(error, kind):
            return seconds
    return None


#: Where a job type that declared nothing sorts: last, so a declaration elsewhere moves nothing.
UNDECLARED_RANK = 1_000_000

#: Where a type that trails sorts: after even the types that declared nothing. See `_TRAILS`.
TRAILING_RANK = UNDECLARED_RANK + 1


def claim_rank(job_type: str) -> int:
    """How far down its declared chain a job type sits; a cycle across features is refused."""
    if job_type in _TRAILS:
        return TRAILING_RANK
    if job_type not in _FOLLOWS:
        return UNDECLARED_RANK
    rank = 1
    seen = _FOLLOWS[job_type]
    while seen in _FOLLOWS:
        seen = _FOLLOWS[seen]
        rank += 1
        if rank > len(_FOLLOWS):
            raise ValueError(f"the job types {job_type!r} follows run in a circle")
    return rank


def in_claim_order(job_types: Sequence[str]) -> list[str]:
    """These job types in the order their work should be handed out, stable for the undeclared."""
    return sorted(job_types, key=claim_rank)


def family_of(job_type: str) -> Family:
    """Which family one job type belongs to; `OTHER` for a type nothing has claimed."""
    return _FAMILIES.get(job_type, Family.OTHER)


def registered_job_names() -> dict[str, str]:
    """What every claimed job type is called on screen, copied."""
    return dict(_NAMES)


def job_name(job_type: str) -> str:
    """What one job type is called, or its own name for a row a newer version left behind."""
    return _NAMES.get(job_type, job_type)
