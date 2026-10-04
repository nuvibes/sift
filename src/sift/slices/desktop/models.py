# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the desktop routes send and take. Every field is always sent (see `sift.kernel.wire`)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.wire import Wire


class ShellSharing(Wire):
    """Whether the computer running Sift offers the library to its network, as its app knows it."""

    enabled: bool
    live: bool
    address: str | None
    port: int


class DesktopView(Wire):
    """The computer running Sift, as the desktop app there describes it.

    `has_app` false is a backend no app started (run by hand, in a container): there is nothing to
    ask, and every other field is null rather than a switch drawn off.
    """

    has_app: bool
    machine: str | None
    starts_with_windows: bool | None
    sharing: ShellSharing | None


class StartWithWindows(Wire):
    """Say whether Sift starts when somebody signs in to the computer running it."""

    on: bool


class FirewallView(Wire):
    """Says whether Windows on the computer running Sift lets other computers reach it."""

    state: Literal["open", "closed", "unknown"]
    networks: list[Literal["Private", "Public", "Domain"]] | None
    scope: Literal["private", "any"] | None


class OpenFirewall(Wire):
    """Which networks the rule opens the port on: home networks only, or every one."""

    scope: Literal["private", "any"] = "private"


class SharingChange(Wire):
    """Offer the library to the network from the computer running Sift, or stop."""

    on: bool


class ActTaken(Wire):
    """What the app there said to an act that restarts Sift on that computer.

    `ok` is taken on: the app answered first, and Sift restarts there a moment later, so the page
    waits for a new run of the server. Otherwise `refusal` says why, in the app's own words, and
    nothing stopped.
    """

    ok: bool
    refusal: str | None


class StorageView(Wire):
    """Where Sift keeps its two folders on the computer running it, and what the last move asked
    from another computer came to (null: none was asked since the app started)."""

    data_dir: str
    cache_dir: str
    data_bytes: int
    cache_bytes: int
    last_move: ActTaken | None


class MoveStorage(Wire):
    """The empty folder to move both storage folders into, chosen in the server's folder browser."""

    folder: str = Field(min_length=1, max_length=1024)


class UpdateTaken(Wire):
    """What the app there said to an update: the version whose installer it is opening THERE, or
    the reason it installs nothing (`none` is no release newer than the one running)."""

    ok: bool
    version: str | None
    reason: Literal["none", "unreachable", "incomplete", "unverified", "failed"] | None


class AppLog(Wire):
    """The end of the Sift app's own log on the computer running Sift."""

    lines: list[str]
    path: str
    size: int
    present: bool


class RememberedLibrary(Wire):
    """A library the Sift app there has opened before."""

    data_dir: str
    cache_dir: str
    name: str
    last_opened: int


class RememberedLibraries(Wire):
    """Every library the Sift app there has opened, and the data folder of the one it has open."""

    current: str | None
    libraries: list[RememberedLibrary]


class OpenRemembered(Wire):
    """A library the app there has opened before, named by its data folder as the app listed it."""

    data_dir: str = Field(min_length=1, max_length=1024)
