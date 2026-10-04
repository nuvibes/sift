# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a directory is served from another machine, which decides whether a folder is watched
or polled; a share wrongly treated as local silently never notices new files. Each case writes a
real mount table, so the parser's escaping and ordering are exercised."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

from sift.kernel.content import mounts


@pytest.fixture
def table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Stand a mount table of your own in front of this machine's, on the POSIX branch, so the
    parser is exercised on Windows too."""
    monkeypatch.setattr(mounts, "_WINDOWS", False)
    # On Windows `Path("/media").resolve()` answers `C:\\media`, which no `/proc` line matches.
    monkeypatch.setattr(Path, "resolve", lambda self, *_a, **_k: self)

    def write(text: str) -> None:
        at = tmp_path / "mounts"
        at.write_text(text, encoding="utf-8")
        monkeypatch.setattr(mounts, "_MOUNTS", at)

    return write


def test_a_local_filesystem_is_not_remote(table) -> None:  # type: ignore[no-untyped-def]
    table("/dev/sda1 / ext4 rw 0 0\n")
    assert mounts.is_remote(Path("/")) is False


def test_a_network_share_is_remote(table) -> None:  # type: ignore[no-untyped-def]
    table("/dev/sda1 / ext4 rw 0 0\n//nas/media /media cifs rw 0 0\n")
    assert mounts.is_remote(Path("/media")) is True


def test_the_longest_matching_mount_wins(table) -> None:  # type: ignore[no-untyped-def]
    """The longest matching mount wins: `/media/nas` on nfs under `/media` on ext4 is a share."""
    table("/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /media ext4 rw 0 0\n//nas/x /media/nas nfs4 rw 0 0\n")
    assert mounts.is_remote(Path("/media/nas")) is True
    assert mounts.is_remote(Path("/media")) is False


def test_a_filesystem_nobody_here_knows_is_treated_as_local(table) -> None:  # type: ignore[no-untyped-def]
    """An unknown filesystem is local, the safer mistake: it behaves as every folder did before."""
    table("none / somethingnew rw 0 0\n")
    assert mounts.is_remote(Path("/")) is False


def test_a_bare_fuse_mount_is_not_assumed_remote(table) -> None:  # type: ignore[no-untyped-def]
    table("/dev/sda1 / ext4 rw 0 0\nnone /mnt/thing fuse rw 0 0\n")
    assert mounts.is_remote(Path("/mnt/thing")) is False


def test_a_named_fuse_network_driver_is_remote(table) -> None:  # type: ignore[no-untyped-def]
    table("/dev/sda1 / ext4 rw 0 0\nuser@host:/ /mnt/remote fuse.sshfs rw 0 0\n")
    assert mounts.is_remote(Path("/mnt/remote")) is True


def test_no_mount_table_reads_as_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """No readable mount table reads as local, asked on the POSIX branch."""
    monkeypatch.setattr(mounts, "_WINDOWS", False)
    monkeypatch.setattr(mounts, "_MOUNTS", Path("/nonexistent/mounts"))
    assert mounts.is_remote(Path("/")) is False


def test_a_mount_point_with_a_space_is_read(table) -> None:  # type: ignore[no-untyped-def]
    r"""The table escapes a space as \040."""
    table("/dev/sda1 / ext4 rw 0 0\n//nas/x /media/Sift\\040Downloads cifs rw 0 0\n")
    assert mounts.is_remote(Path("/media/Sift Downloads")) is True
    assert mounts.filesystem_of(Path("/media/Sift Downloads")) == "cifs"


def test_a_short_line_is_skipped_rather_than_crashing(table) -> None:  # type: ignore[no-untyped-def]
    table("/dev/sda1 / ext4 rw 0 0\ngarbage\n")
    assert mounts.is_remote(Path("/")) is False


def test_a_path_that_cannot_be_resolved_reads_as_unknown(  # type: ignore[no-untyped-def]
    table,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path that cannot be resolved reads as None, which callers treat as local."""
    table("/dev/sda1 / ext4 rw 0 0\n")

    def refuse(self: Path, *_args: object, **_kwargs: object) -> Path:
        raise OSError("too many levels of symbolic links")

    monkeypatch.setattr(Path, "resolve", refuse)

    assert mounts.filesystem_of(Path("/anywhere")) is None
    assert mounts.is_remote(Path("/anywhere")) is False


# --- Windows: no mount table, so the volume's own drive type answers
#
# A share treated as local gets a filesystem watcher that SMB does not reliably feed.


def test_on_windows_a_remote_drive_is_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mapped letter and a UNC path are the same answer."""
    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(mounts, "_drive_type", lambda _path: mounts._DRIVE_REMOTE)
    assert mounts.is_remote(Path(".")) is True


@pytest.mark.parametrize(
    ("drive_type", "what"),
    [(3, "a fixed disk"), (2, "a removable disk"), (5, "a CD"), (6, "a RAM disk"), (1, "no root")],
)
def test_on_windows_every_other_drive_type_is_local(
    monkeypatch: pytest.MonkeyPatch, drive_type: int, what: str
) -> None:
    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(mounts, "_drive_type", lambda _path: drive_type)
    assert mounts.is_remote(Path(".")) is False, what


def test_on_windows_a_drive_windows_will_not_describe_reads_as_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed lookup reads as local, the harmless direction."""
    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(mounts, "_drive_type", lambda _path: None)
    assert mounts.is_remote(Path(".")) is False


def test_on_windows_a_path_that_cannot_be_resolved_reads_as_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mounts, "_WINDOWS", True)

    def refuse(self: Path, *_args: object, **_kwargs: object) -> Path:
        raise OSError("the volume has gone away")

    monkeypatch.setattr(Path, "resolve", refuse)
    assert mounts.is_remote(Path("Z:\\gone")) is False


def test_on_windows_the_real_lookup_calls_this_machine(tmp_path: Path) -> None:
    """The real Windows call answers a fixed drive for a temporary folder, so the stand-ins replace
    something that works."""
    if not mounts._WINDOWS:
        pytest.skip("GetDriveTypeW is a Windows call; the branch above is tested with a stand-in")
    assert mounts._drive_type(tmp_path) == 3  # DRIVE_FIXED


def test_on_windows_the_real_lookup_refuses_a_path_it_cannot_parse(tmp_path: Path) -> None:
    """A NUL in the path is the `ValueError` ctypes raises and the guard catches."""
    if not mounts._WINDOWS:
        pytest.skip("ctypes' unicode buffer only refuses this on Windows")
    assert mounts._drive_type(Path("C:\\a\x00b")) is None


def test_a_path_under_no_mount_at_all_has_no_filesystem(table) -> None:  # type: ignore[no-untyped-def]
    """A path under no mount has no filesystem, which callers read as local."""
    table("//nas/x /media/share cifs rw 0 0\n")

    assert mounts.filesystem_of(Path("/somewhere/else")) is None


def test_on_windows_a_path_whose_volume_cannot_be_named_has_no_drive_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Windows will not name a path's volume, the drive type is not asked with an empty
    buffer."""

    class Kernel32:
        def GetVolumePathNameW(self, *_args: object) -> int:
            return 0

        def GetDriveTypeW(self, *_args: object) -> int:
            raise AssertionError("asked about a volume Windows would not name")

    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: Kernel32(), raising=False)

    assert mounts._drive_type(Path("Z:\\somewhere")) is None


def test_on_windows_a_volume_that_refuses_the_drive_type_answers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the type call fails after the volume is named, the answer is "cannot tell", not an
    exception that would stop a library scan."""

    class Kernel32:
        def GetVolumePathNameW(self, _path: object, buffer: object, _size: object) -> int:
            buffer.value = "D:\\"  # type: ignore[attr-defined]
            return 1

        def GetDriveTypeW(self, *_args: object) -> int:
            raise OSError("the volume went away between the two calls")

    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: Kernel32(), raising=False)

    assert mounts._drive_type(Path("D:\\library")) is None


# --- The volume root, which the drive type is worked out from


def test_the_drive_type_is_asked_about_the_VOLUME_and_not_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GetDriveTypeW` is asked about the volume root: handed the path it answers
    `DRIVE_NO_ROOT_DIR`."""
    asked: list[str] = []

    class Kernel32:
        def GetVolumePathNameW(self, path: object, buffer: object, _size: object) -> int:
            asked.append(str(path))
            buffer.value = "D:\\"  # type: ignore[attr-defined]
            return 1

        def GetDriveTypeW(self, root: object) -> int:
            assert root == "D:\\", "the drive type was asked about something other than the volume"
            return 3  # DRIVE_FIXED

    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: Kernel32(), raising=False)
    monkeypatch.setattr(Path, "resolve", lambda self, *_a, **_k: self)

    assert mounts.is_remote(Path("D:\\library")) is False
    assert asked, "nothing ever asked Windows which volume the path was on"


# --- Which storage a path is on


def test_a_storage_is_named_by_its_mount_point_and_carries_the_remote_answer(table) -> None:  # type: ignore[no-untyped-def]
    """Two paths under one mount are one storage, keyed by the mount point, `remote` as `is_remote`
    says."""
    table("/dev/sda1 / ext4 rw 0 0\n//nas/x /media/share cifs rw 0 0\n")

    share = mounts.storage_of(Path("/media/share/photos"))
    assert share == mounts.Storage(key="/media/share", remote=True)
    assert share == mounts.storage_of(Path("/media/share/videos")), "one share, one key"
    assert mounts.storage_of(Path("/home/me")) == mounts.Storage(key="/", remote=False)


def test_a_path_under_no_mount_at_all_is_the_root_and_local(table) -> None:  # type: ignore[no-untyped-def]
    table("//nas/x /media/share cifs rw 0 0\n")

    assert mounts.storage_of(Path("/somewhere/else")) == mounts.Storage(key="/", remote=False)


def test_on_windows_the_storage_is_the_volume_root_and_its_drive_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(Path, "resolve", lambda self, *_a, **_k: self)
    monkeypatch.setattr(mounts, "_volume_root", lambda _path: "\\\\nas\\photos\\")
    monkeypatch.setattr(mounts, "_drive_type", lambda _path: mounts._DRIVE_REMOTE)

    assert mounts.storage_of(Path("\\\\nas\\photos\\2024")) == mounts.Storage(
        key="\\\\nas\\photos\\", remote=True
    )

    monkeypatch.setattr(mounts, "_volume_root", lambda _path: "D:\\")
    monkeypatch.setattr(mounts, "_drive_type", lambda _path: 3)  # DRIVE_FIXED
    assert mounts.storage_of(Path("D:\\library")) == mounts.Storage(key="D:\\", remote=False)


def test_on_windows_a_volume_windows_will_not_name_falls_back_to_the_drive_letter() -> None:
    """With no volume root, the key is the path's own drive letter, and it is local."""
    if not mounts._WINDOWS:
        pytest.skip("`splitdrive` finds no drive letter anywhere else")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "resolve", lambda self, *_a, **_k: self)
        monkeypatch.setattr(mounts, "_volume_root", lambda _path: None)
        monkeypatch.setattr(mounts, "_drive_type", lambda _path: pytest.fail("asked anyway"))

        assert mounts.storage_of(Path("D:\\library")) == mounts.Storage(key="D:\\", remote=False)


def test_on_windows_a_path_with_no_drive_at_all_is_the_bare_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(Path, "resolve", lambda self, *_a, **_k: self)
    monkeypatch.setattr(mounts, "_volume_root", lambda _path: None)
    monkeypatch.setattr(mounts, "_drive_type", lambda _path: pytest.fail("asked anyway"))

    assert mounts.storage_of(Path("library")) == mounts.Storage(key=os.sep, remote=False)


def test_on_windows_a_path_that_cannot_be_resolved_is_placed_by_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`storage_of` asks the volume about an unresolved name: every path needs SOME key."""
    asked: list[Path] = []

    def refuse(self: Path, *_a: object, **_k: object) -> Path:
        raise OSError("gone")

    def root(path: Path) -> str:
        asked.append(path)
        return "D:\\"

    monkeypatch.setattr(mounts, "_WINDOWS", True)
    monkeypatch.setattr(Path, "resolve", refuse)
    monkeypatch.setattr(mounts, "_volume_root", root)
    monkeypatch.setattr(mounts, "_drive_type", lambda _path: 3)  # DRIVE_FIXED

    assert mounts.storage_of(Path("D:\\library")) == mounts.Storage(key="D:\\", remote=False)
    assert asked == [Path("D:\\library")]
