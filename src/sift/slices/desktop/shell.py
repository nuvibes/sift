# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking the desktop app that started this backend for an act only it can do, by named act."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import aiohttp

from sift.kernel.config import Settings
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: The firewall read starts PowerShell, which the app gives 15 seconds.
READ_SECONDS = 30.0

#: The admin answers Windows' own prompt at the computer, which the app waits three minutes for.
PROMPT_SECONDS = 200.0

#: The app downloads the installer and checks its signature and hash before it answers.
UPDATE_SECONDS = 900.0

Scope = Literal["private", "any"]


class NoShell(Exception):
    """This backend was not started by the desktop app, so there is no app to ask."""


class ShellUnreachable(Exception):
    """The desktop app did not answer the ask."""


@dataclass(frozen=True)
class ShellLink:
    """Where the app answers, and this launch's secret. Both or neither."""

    url: str | None
    token: str | None

    @classmethod
    def from_settings(cls, settings: Settings) -> ShellLink:
        token = settings.shell_token.get_secret_value() if settings.shell_token else None
        return cls(url=settings.shell_url, token=token)

    @property
    def present(self) -> bool:
        return bool(self.url) and bool(self.token)

    async def _ask(
        self,
        method: str,
        act: str,
        body: dict[str, Any] | None,
        seconds: float,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.present:
            raise NoShell
        timeout = aiohttp.ClientTimeout(total=seconds)
        headers = {"authorization": f"Bearer {self.token}"}
        address = f"{self.url}{act}"
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.request(method, address, json=body, headers=headers, params=query) as reply,
            ):
                if reply.status != 200:
                    log.warning("desktop.shell_refused", act=act, status=reply.status)
                    raise ShellUnreachable
                answer: dict[str, Any] = await reply.json()
                return answer
        except (aiohttp.ClientError, TimeoutError) as error:
            log.warning("desktop.shell_unreachable", act=act, reason=type(error).__name__)
            raise ShellUnreachable from error

    async def facts(self) -> dict[str, Any]:
        """The computer's name, whether Sift starts with Windows there, and its sharing."""
        return await self._ask("GET", "/facts", None, READ_SECONDS)

    async def start_with_windows(self, on: bool) -> bool | None:
        """Turn the login item on or off, and answer what Windows says afterwards."""
        answer = await self._ask("PUT", "/start-with-windows", {"on": on}, READ_SECONDS)
        now = answer.get("startsWithWindows")
        return now if isinstance(now, bool) else None

    async def firewall(self) -> dict[str, Any]:
        """Says whether Windows lets other computers reach Sift, and on which networks."""
        return await self._ask("GET", "/firewall", None, READ_SECONDS)

    async def open_firewall(self, scope: Scope) -> dict[str, Any]:
        """Windows' own prompt is raised ON THE COMPUTER RUNNING SIFT, and the state after it is answered."""
        return await self._ask("POST", "/firewall", {"scope": scope}, PROMPT_SECONDS)

    async def set_sharing(self, on: bool) -> dict[str, Any]:
        """Offer the library to the network or stop. Answered first; Sift restarts there after."""
        return await self._ask("PUT", "/sharing", {"on": on}, READ_SECONDS)

    async def storage(self) -> dict[str, Any]:
        """Where the two storage folders are, their sizes, and what the last move came to."""
        return await self._ask("GET", "/storage", None, READ_SECONDS)

    async def move_storage(self, folder: str) -> dict[str, Any]:
        """Move both storage folders into `folder`: refused in words, or answered, then moved."""
        return await self._ask("POST", "/storage/move", {"folder": folder}, READ_SECONDS)

    async def update(self) -> dict[str, Any]:
        """Install a newer release there: the app reads its own feed and checks the signature."""
        return await self._ask("POST", "/update", {}, UPDATE_SECONDS)

    async def log(self, lines: int) -> dict[str, Any]:
        """The end of the app's own log there."""
        return await self._ask("GET", "/log", None, READ_SECONDS, {"lines": str(lines)})

    async def libraries(self) -> dict[str, Any]:
        """Every library the app there has opened, and which one is open."""
        return await self._ask("GET", "/libraries", None, READ_SECONDS)

    async def open_library(self, data_dir: str) -> dict[str, Any]:
        """Open a library the app there has opened before. Answered first; Sift restarts after."""
        return await self._ask("POST", "/libraries/open", {"dataDir": data_dir}, READ_SECONDS)
