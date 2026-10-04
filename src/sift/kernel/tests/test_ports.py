# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who holds a port: the operating system's answer read back, and the readers on their own."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import pytest

from sift.kernel import ports
from sift.kernel.subprocess import SubprocessError

pytestmark = pytest.mark.unit


def test_the_windows_answer_is_read_as_id_name_and_command_line() -> None:
    text = (
        "\r\n28828\twireproxy.exe\t"
        'C:\\Users\\ada\\AppData\\Local\\Programs\\Sift\\resources\\vendor\\bin\\wireproxy.exe -c "C:\\x\\tunnel.conf" '
        "-i 127.0.0.1:47101 -s\r\n"
    )

    holder = ports.parse_windows(text)

    assert holder is not None
    assert holder.pid == 28828
    assert holder.name == "wireproxy.exe"
    assert holder.command_line.endswith("-i 127.0.0.1:47101 -s")


def test_nothing_listening_reads_as_nobody() -> None:
    assert ports.parse_windows("") is None
    assert ports.parse_windows("\r\n") is None
    assert ports.parse_ss("") is None


def test_a_process_whose_command_line_cannot_be_read_still_has_a_name() -> None:
    holder = ports.parse_windows("4\tSystem\t")
    assert holder is not None and holder.name == "System" and holder.command_line == ""


def test_the_linux_answer_is_read_as_name_and_id() -> None:
    line = 'LISTEN 0 4096 127.0.0.1:47101 0.0.0.0:* users:(("wireproxy",pid=4242,fd=7))\n'

    assert ports.parse_ss(line) == ("wireproxy", 4242)


async def test_the_process_behind_a_port_is_this_one_when_this_one_is_listening() -> None:
    """The known positive: the operating system's own tool, asked about a port this process holds,
    names this process. A lookup that silently answered nobody would read exactly like a free
    port, so the real path is proved here and not only its reader."""

    async def handle(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]

        holder = await ports.holder_of(port)

        assert holder is not None, "the tool answered nobody for a port this process holds"
        assert holder.pid == os.getpid()
    finally:
        server.close()
        await server.wait_closed()


def test_a_port_that_is_not_a_port_is_refused() -> None:
    with pytest.raises(ValueError):
        asyncio.run(ports.holder_of(0))


# --- the half of this module the platform it ships on never runs -------------------------------
#
# Sift runs natively on Windows, so `_ON_WINDOWS` is true here and the POSIX arms below it are code
# nothing executes: most of this file, and every line of it about a port Sift cannot free. The
# tunnel fault this module exists for (an orphaned client holding its ports, and the sites routed
# through it refusing by name) is not a Windows-only fault, and the container still ships.
#
# So the platform is faked rather than skipped. `_ON_WINDOWS` is settled at import from
# `sys.platform` for exactly this reason (the comment above it says both halves are meant to stay
# ordinary code the checker reads on every platform), and these are the tests that read them.


class _Answer:
    """What `run` hands back: enough of it for these, and no more."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


async def test_the_linux_lookup_asks_ss_and_names_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole POSIX arm, which on this platform is never reached by anything else."""
    asked: list[list[str]] = []

    async def answer(argv: list[str], **_: object) -> _Answer:
        asked.append(argv)
        return _Answer(
            b'LISTEN 0 4096 127.0.0.1:47101 0.0.0.0:* users:(("wireproxy",pid=4242,fd=7))\n'
        )

    monkeypatch.setattr(ports, "_ON_WINDOWS", False)
    monkeypatch.setattr(ports, "run", answer)
    monkeypatch.setattr(ports, "_command_line_of", lambda pid: f"wireproxy -c {pid}.conf")

    holder = await ports.holder_of(47101)

    assert holder == ports.PortHolder(
        pid=4242, name="wireproxy", command_line="wireproxy -c 4242.conf"
    )
    # Filtered to the port in the query rather than listing every socket and filtering after.
    assert asked == [["ss", "-Hltnp", "sport = :47101"]]


async def test_a_linux_port_nothing_is_listening_on_is_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def answer(_argv: list[str], **_: object) -> _Answer:
        return _Answer(b"")

    monkeypatch.setattr(ports, "_ON_WINDOWS", False)
    monkeypatch.setattr(ports, "run", answer)

    assert await ports.holder_of(47101) is None


async def test_a_lookup_that_fails_is_nobody_rather_than_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None is the honest answer for "nothing is listening" and for "this cannot be told", and the
    caller treats them the same way. An exception here would fail a tunnel start over a missing
    diagnostic tool."""

    async def refuse(_argv: list[str], **_: object) -> _Answer:
        raise SubprocessError("ss is not installed")

    monkeypatch.setattr(ports, "_ON_WINDOWS", False)
    monkeypatch.setattr(ports, "run", refuse)

    assert await ports.holder_of(47101) is None


def test_a_command_line_that_cannot_be_read_is_empty_rather_than_a_failure() -> None:
    """There is no `/proc` on Windows at all, so this is the arm that runs here, and it is the
    same arm a Linux machine takes for a process that has gone between the two reads."""
    assert ports._command_line_of(4242) == ""


def test_a_command_line_is_read_off_the_process_filesystem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """NUL-separated, which is what `/proc/<pid>/cmdline` holds, and the reason this is not a
    plain read: a command line split on spaces loses every argument with a space in it, which on
    this platform is most of them."""
    written = tmp_path / "cmdline"
    written.write_bytes(b"wireproxy\x00-c\x00/etc/tunnel with a space.conf\x00")
    monkeypatch.setattr(ports, "Path", lambda _path: written)

    assert ports._command_line_of(4242) == "wireproxy -c /etc/tunnel with a space.conf"


async def test_ending_a_process_on_linux_asks_for_a_term(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(ports, "_ON_WINDOWS", False)
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))

    assert await ports.end_process(4242) is True
    assert sent == [(4242, signal.SIGTERM)]


async def test_ending_a_process_on_windows_asks_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[str]] = []

    async def answer(argv: list[str], **_: object) -> _Answer:
        asked.append(argv)
        return _Answer(returncode=0)

    monkeypatch.setattr(ports, "_ON_WINDOWS", True)
    monkeypatch.setattr(ports, "run", answer)

    assert await ports.end_process(4242) is True
    assert asked == [["taskkill.exe", "/PID", "4242", "/F"]]


async def test_a_taskkill_that_refused_is_reported_as_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one this must not get wrong. Answering True for a process that is still standing sends
    the caller on to bind a port that is still held, which is the fault this module exists for."""

    async def answer(_argv: list[str], **_: object) -> _Answer:
        return _Answer(returncode=1)

    monkeypatch.setattr(ports, "_ON_WINDOWS", True)
    monkeypatch.setattr(ports, "run", answer)

    assert await ports.end_process(4242) is False


async def test_a_process_that_has_already_gone_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gone(_pid: int, _sig: int) -> None:
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(ports, "_ON_WINDOWS", False)
    monkeypatch.setattr(os, "kill", gone)

    assert await ports.end_process(4242) is False


async def test_a_pid_that_is_not_a_pid_is_refused_before_anything_is_asked() -> None:
    """Zero and negative ids are whole PROCESS GROUPS to `kill`, so a fallen-through default here
    would signal every process in the caller's group, Sift included."""
    assert await ports.end_process(0) is False
    assert await ports.end_process(-1) is False
