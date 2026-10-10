# SPDX-License-Identifier: AGPL-3.0-or-later
"""A child that runs for as long as Sift does, contained so it cannot outlive it."""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
from typing import IO


def _contain(process: subprocess.Popen[bytes]) -> None:
    """The tool runner's containment, asked at the call so a stand-in for it is the one used."""
    from sift.kernel import subprocess as tools

    tools._contain(process)


def _release(process: subprocess.Popen[bytes]) -> None:
    from sift.kernel import subprocess as tools

    tools._release(process)


class LongLivedChild:
    """A child that runs until told to stop; on Windows, dropping this ends it with its job."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        """The exit status, or None while it runs. Asked of the process each time, never cached."""
        return self._process.poll()

    def terminate(self) -> None:
        """Ask it to stop. On Windows there is no asking: this ends it, the same as `kill`."""
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    async def wait(self, *, time_limit: float | None = None) -> int:
        """Wait for it to exit on a thread and release its job; `TimeoutError` past `time_limit`."""
        try:
            code = await asyncio.to_thread(self._process.wait, time_limit)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"process {self.pid} did not exit in {time_limit} s") from None
        _release(self._process)
        return code


async def start_long_lived(
    argv: list[str],
    *,
    stdout: IO[bytes] | int = subprocess.DEVNULL,
    stderr: IO[bytes] | int = subprocess.DEVNULL,
) -> LongLivedChild:
    """Start a child that runs until told to stop, contained so it cannot outlive Sift.

    Its input is closed: the desktop shell uses this process's input to ask it to stop.
    """
    # Here, not at the top: the tool runner re-exports this module.
    from sift.kernel.subprocess import SubprocessError

    def launch() -> subprocess.Popen[bytes]:
        child = subprocess.Popen(  # noqa: S603 (a list, never a shell; see the module header)
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        _contain(child)
        return child

    launching = asyncio.ensure_future(asyncio.to_thread(launch))
    try:
        started = await asyncio.shield(launching)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            abandoned = await launching
            abandoned.kill()
            await asyncio.to_thread(abandoned.wait)
            _release(abandoned)
        raise
    except OSError as exc:
        raise SubprocessError(f"could not run {argv[0]!r}") from exc
    return LongLivedChild(started)
