# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two checks the release makes on the executable it packed: the fuse wire is what it should
be, and the shell actually boots, since a bad fuse or a bundle without its hash installs fine and is
wrong. The wire is written by hand and the launcher stood in for.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load() -> ModuleType:
    """The script, imported by path. It is an operator tool rather than part of the package."""
    spec = importlib.util.spec_from_file_location("sift_release_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


def _binary(folder: Path, wire: str) -> Path:
    """A stand-in for a packed Sift.exe carrying one fuse wire in @electron/fuses' shape: sentinel,
    version byte, length byte, one byte per fuse (`0` off, `1` on, `r` removed, space never set)."""
    states = bytes(0x90 if one == " " else ord(one) for one in wire)
    shell = folder / "win-unpacked" / "Sift.exe"
    shell.parent.mkdir(parents=True, exist_ok=True)
    shell.write_bytes(b"MZ" + b"\0" * 64 + release.FUSE_SENTINEL + bytes([1, len(states)]) + states)
    return shell


#: The wire a good build carries: the three doors shut, the two archive fuses on, and the two
#: Electron's own defaults leave alone.
GOOD = "000011011"


@pytest.fixture
def packed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The artefacts folder, pointed somewhere nothing real can be reached from."""
    monkeypatch.setattr(release, "ARTIFACTS", tmp_path)
    return tmp_path


class TestReadingTheFuseWire:
    def test_it_reads_every_fuse_in_the_wire(self, tmp_path: Path) -> None:
        wire = release.read_fuse_wire(_binary(tmp_path, GOOD))

        assert len(wire) == len(GOOD)
        assert wire[0] == release.FUSE_OFF
        assert wire[4] == release.FUSE_ON
        assert wire[5] == release.FUSE_ON

    def test_a_binary_with_no_wire_is_refused_rather_than_read_as_empty(
        self, tmp_path: Path
    ) -> None:
        """The failure that matters most: a file this cannot find a wire in must not read as a file
        whose fuses are all fine. Nothing is shipped on a wire nobody could read."""
        nowhere = tmp_path / "Sift.exe"
        nowhere.write_bytes(b"MZ" + b"\0" * 4096)

        with pytest.raises(release.ReleaseFailed, match="no fuse wire"):
            release.read_fuse_wire(nowhere)

    def test_an_empty_file_is_refused(self, tmp_path: Path) -> None:
        empty = tmp_path / "Sift.exe"
        empty.write_bytes(b"")

        with pytest.raises(release.ReleaseFailed, match="empty"):
            release.read_fuse_wire(empty)

    def test_a_wire_from_a_later_electron_is_refused_rather_than_guessed_at(
        self, tmp_path: Path
    ) -> None:
        """A version this reader does not know means the bytes after it mean something else. Reading
        them anyway would report confident nonsense about a security setting."""
        odd = tmp_path / "Sift.exe"
        odd.write_bytes(release.FUSE_SENTINEL + bytes([2, 3]) + b"111")

        with pytest.raises(release.ReleaseFailed, match="version 2"):
            release.read_fuse_wire(odd)


class TestCheckingTheFuses:
    def test_the_intended_wire_passes(self, packed: Path) -> None:
        _binary(packed, GOOD)

        release._check_the_fuses_are_flipped()

    def test_a_fuse_that_did_not_flip_is_named(self, packed: Path) -> None:
        """The whole build looks identical either way. The message has to say WHICH one."""
        _binary(packed, "000001011")

        with pytest.raises(release.ReleaseFailed) as refused:
            release._check_the_fuses_are_flipped()

        assert "EnableEmbeddedAsarIntegrityValidation" in str(refused.value)
        assert "OnlyLoadAppFromAsar" not in str(refused.value)

    def test_a_door_left_open_is_named(self, packed: Path) -> None:
        _binary(packed, "100011011")

        with pytest.raises(release.ReleaseFailed, match="RunAsNode"):
            release._check_the_fuses_are_flipped()

    def test_a_fuse_nothing_ever_set_is_refused(self, packed: Path) -> None:
        """ "Left at whatever Electron was built with" is not the same as off, and a check that read
        the two as one would pass a configuration that had stopped being applied."""
        _binary(packed, "00001 011")

        with pytest.raises(release.ReleaseFailed, match="never set"):
            release._check_the_fuses_are_flipped()

    def test_a_fuse_electron_has_removed_is_refused(self, packed: Path) -> None:
        """Setting a removed fuse is a no-op, and @electron/fuses says so in a warning nobody reads
        during a forty-line build. The state is in the binary and cannot be missed."""
        _binary(packed, "0000r1011")

        with pytest.raises(release.ReleaseFailed, match="removed"):
            release._check_the_fuses_are_flipped()

    def test_a_wire_too_short_to_carry_a_fuse_is_refused(self, packed: Path) -> None:
        """An older Electron has fewer fuses, and the packer's own error for this is easy to lose.
        A fuse that is not in the wire was not set, whatever the configuration says."""
        _binary(packed, "0000")

        with pytest.raises(release.ReleaseFailed, match="too short"):
            release._check_the_fuses_are_flipped()

    def test_there_is_nothing_to_check_without_a_packed_executable(self, packed: Path) -> None:
        with pytest.raises(release.ReleaseFailed, match=r"no Sift\.exe"):
            release._check_the_fuses_are_flipped()


class TestCheckingTheShellBoots:
    """The launcher is faked, so every one of these is one line of the real check's judgement."""

    def _launcher(self, exit_code: int | None, output: str) -> Any:
        """A launch that went one particular way, with the call itself checked on the way past."""

        def launch(command: list[str], timeout_s: float) -> Any:
            assert command[0].endswith("Sift.exe")
            assert command[1] == "--smoke"
            assert timeout_s == release.SMOKE_TIMEOUT_S
            return release.ShellRun(exit_code, output)

        return launch

    def test_a_shell_that_says_it_started_is_accepted(self, packed: Path) -> None:
        _binary(packed, GOOD)
        marker = release.smoke_marker()

        release._check_the_shell_boots(self._launcher(0, f"\n{marker}\n"))

    def test_a_clean_exit_with_nothing_said_is_refused(self, packed: Path) -> None:
        """Exit code 0 is also what this shell does when another copy holds the single-instance
        lock. A check that took it as proof would pass on any machine with Sift open, which is
        most of them, and it would never have been able to fail."""
        _binary(packed, GOOD)

        with pytest.raises(release.ReleaseFailed, match="never said it had started"):
            release._check_the_shell_boots(self._launcher(0, ""))

    def test_a_shell_that_would_not_start_is_refused_and_quotes_it(self, packed: Path) -> None:
        _binary(packed, GOOD)
        said = "ASAR Integrity Violation: got a hash mismatch (aa vs bb)"

        with pytest.raises(release.ReleaseFailed) as refused:
            release._check_the_shell_boots(self._launcher(1, said))

        assert "exited with 1" in str(refused.value)
        assert said in str(refused.value)

    def test_a_shell_that_had_to_be_taken_down_is_refused(self, packed: Path) -> None:
        """A run that never ended is not a pass and is not a diagnosis either: the message says it
        was taken down and names both ordinary causes rather than picking one."""
        _binary(packed, GOOD)

        with pytest.raises(release.ReleaseFailed, match="did not stop"):
            release._check_the_shell_boots(self._launcher(None, ""))

    def test_there_is_nothing_to_boot_without_a_packed_executable(self, packed: Path) -> None:
        with pytest.raises(release.ReleaseFailed, match=r"no Sift\.exe"):
            release._check_the_shell_boots(self._launcher(0, ""))


def test_the_marker_is_read_from_the_shell_and_not_copied_here() -> None:
    """Two declarations of one string is how a boot check ends up looking for a line nothing writes.

    This reads the real module, so moving or renaming the constant fails here rather than at the
    last step of a release.
    """
    marker = release.smoke_marker()

    assert marker
    source = _SCRIPT.parents[1] / "desktop" / "src" / "smoke.ts"
    assert f"'{marker}'" in source.read_text(encoding="utf-8")
