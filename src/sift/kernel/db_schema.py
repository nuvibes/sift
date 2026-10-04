# SPDX-License-Identifier: AGPL-3.0-or-later
"""The schema registry: each feature's tables and how to bring them forward, and the invariants."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from sift.kernel.db_base import Connection, DatabaseError, Initializer


@dataclass(frozen=True)
class SchemaComponent:
    """One feature's tables, and how to bring them up to date."""

    name: str
    version: int
    initialize: Initializer
    depends_on: tuple[str, ...] = field(default=())
    #: The version its first step creates; a library recorded below it is refused. None: from 1.
    baseline: int | None = None
    #: Whether it comes up before every component it does not depend on (`leads`).
    leads: bool = False


_REGISTRY: dict[str, SchemaComponent] = {}

#: The last release that carried every step from each component's first version.
STEPS_LAST_SHIPPED_IN = "0.1.199"


def too_old_to_bring_forward(on_disk: Mapping[str, int]) -> str | None:
    """Why a library is too old for this build's baselines, or None: asked of every component
    before any changes, by the boot and the switcher alike."""
    for name in sorted(on_disk):
        component = _REGISTRY.get(name)
        if component is None or component.baseline is None:
            continue
        if 0 < on_disk[name] < component.baseline:
            return (
                "This library was last opened by a version of Sift too old for this one to bring "
                f"forward ({name} schema {on_disk[name]}; this version starts from "
                f"{component.baseline}). Open it once with Sift {STEPS_LAST_SHIPPED_IN}, which "
                "brings it up to date, then open it here. Nothing has been changed."
            )
    return None


def register_schema_initializer(
    name: str,
    version: int,
    initialize: Initializer,
    *,
    depends_on: Sequence[str] = (),
    baseline: int | None = None,
    leads: bool = False,
) -> None:
    """Register a feature's schema: `initialize(connection, on_disk)` brings it from the version on
    disk to `version` (its first step creates `baseline` from 0), and the kernel records it.
    `depends_on` orders initializers across foreign keys; `leads` is the one component every step
    may write into unnamed (the component holding `record_event`'s table), so it comes up first."""
    if name in _REGISTRY:
        raise ValueError(f"schema component {name!r} is registered twice")
    if version < 1:
        raise ValueError(f"schema version for {name!r} must be 1 or greater")
    if baseline is not None and not 1 <= baseline <= version:
        raise ValueError(f"the baseline for {name!r} must be between 1 and its version")
    if leads and any(one.leads for one in _REGISTRY.values()):
        raise ValueError(f"schema component {name!r} leads, and another already does")
    _REGISTRY[name] = SchemaComponent(name, version, initialize, tuple(depends_on), baseline, leads)


def add_schema_dependency(name: str, on: str) -> None:
    """One component saying, after both are registered, that it must come up after another: for
    a trigger or a count kept over ANOTHER component's table. Resolved when a database opens."""
    component = _REGISTRY.get(name)
    if component is None:
        raise ValueError(f"schema component {name!r} is not registered")
    if on not in component.depends_on:
        _REGISTRY[name] = replace(component, depends_on=(*component.depends_on, on))


def registered_components() -> dict[str, SchemaComponent]:
    """The registry, copied. Tests read it; nothing mutates it through here."""
    return dict(_REGISTRY)


# What must hold on every boot whatever the versions (a table rebuilt by another component drops
# its triggers): run after every component is current, every time, cheap and idempotent.

Invariant = Callable[[Connection], Awaitable[None]]

_INVARIANTS: dict[str, Invariant] = {}


def register_schema_invariant(name: str, apply: Invariant) -> None:
    """Register something to hold on every boot, after every initializer has run."""
    if name in _INVARIANTS:
        raise ValueError(f"schema invariant {name!r} is registered twice")
    _INVARIANTS[name] = apply


def registered_invariants() -> dict[str, Invariant]:
    """The invariants, copied. Tests read it; nothing mutates it through here."""
    return dict(_INVARIANTS)


def _ordered_components() -> list[SchemaComponent]:
    """Dependency order, with a cycle raising at boot rather than deadlocking or half-applying."""
    ordered: list[SchemaComponent] = []
    done: set[str] = set()
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name) :], name])
            raise DatabaseError(f"schema components depend on each other in a cycle: {cycle}")

        component = _REGISTRY.get(name)
        if component is None:
            required_by = visiting[-1] if visiting else "?"
            raise DatabaseError(
                f"schema component {required_by!r} depends on {name!r}, which is not registered"
            )

        visiting.append(name)
        for dependency in component.depends_on:
            visit(dependency)
        visiting.pop()

        done.add(name)
        ordered.append(component)

    # The leader first, with what it depends on (see `register_schema_initializer`).
    for name in sorted(_REGISTRY, key=lambda one: (not _REGISTRY[one].leads, one)):
        visit(name)
    return ordered


# Extensions, loaded into every connection, registered by the feature that needs one. Attempted,
# never required: a failure costs that feature and not the application.

#: Loads one extension into a connection, async so the driver opens the file off the loop.
ExtensionLoader = Callable[[Connection], Awaitable[None]]

_EXTENSIONS: dict[str, ExtensionLoader] = {}


def register_connection_extension(name: str, load: ExtensionLoader) -> None:
    """Register an extension to be loaded into every connection this process opens."""
    if name in _EXTENSIONS:
        raise ValueError(f"connection extension {name!r} is registered twice")
    _EXTENSIONS[name] = load
