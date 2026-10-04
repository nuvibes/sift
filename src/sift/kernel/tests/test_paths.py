# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared path-confinement control: a path is proved inside its root, or it is refused."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sift.kernel import paths
from sift.kernel.paths import (
    PathEscape,
    confine,
    is_writable,
    mount_is_readonly,
    same_filesystem,
)


def test_a_file_inside_the_root_comes_back_resolved(tmp_path: Path) -> None:
    inside = tmp_path / "sub" / "file.txt"
    inside.parent.mkdir()
    inside.write_text("x")
    assert confine(tmp_path, inside) == inside.resolve()


def test_the_root_itself_counts_as_inside(tmp_path: Path) -> None:
    assert confine(tmp_path, tmp_path) == tmp_path.resolve()


def test_a_dotdot_escape_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathEscape):
        confine(root, root / ".." / "outside.txt")


def test_an_absolute_path_outside_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathEscape):
        confine(root, tmp_path / "elsewhere.txt")


WINDOWS = sys.platform == "win32"

POSIX_ONLY = pytest.mark.skipif(
    WINDOWS,
    reason=(
        "Creating a symbolic link on Windows needs a privilege an ordinary account does not hold "
        "(WinError 1314), and chmod does not make a directory unwritable there, so these two "
        "mechanisms cannot be set up at all. What they PROVE is proved on Windows by the "
        "junction and monkeypatched tests below, which need no privilege. The refusal itself is "
        "a security property, so it is tested on both platforms, by whichever mechanism each one "
        "actually has."
    ),
)


@POSIX_ONLY
def test_a_symlink_pointing_out_of_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    link = root / "link.txt"
    os.symlink(secret, link)
    with pytest.raises(PathEscape):
        confine(root, link)


@POSIX_ONLY
def test_a_path_that_will_not_resolve_is_refused(tmp_path: Path) -> None:
    """Fail-closed: a symlink loop cannot be resolved, so it is an escape, not something to serve."""
    loop = tmp_path / "loop"
    os.symlink(loop, loop)
    with pytest.raises(PathEscape):
        confine(tmp_path, loop)


@POSIX_ONLY
def test_a_resolve_that_gives_up_and_hands_the_link_back_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink loop handed back unresolved is refused: some Pythons return it rather than raise,
    and the answer still being a symlink is the tell. Forced, whatever interpreter runs."""
    loop = tmp_path / "loop"
    os.symlink(loop, loop)
    resolve = Path.resolve

    def hands_it_back(self: Path, strict: bool = False) -> Path:
        return loop if self == loop else resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", hands_it_back)
    with pytest.raises(PathEscape):
        confine(tmp_path, loop)


def test_a_resolve_that_hands_a_link_back_is_refused_on_any_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same guard where no link can be built (Windows needs a privilege for one): `resolve()`
    never returns a symlink on success, so one coming back is refused whatever produced it."""
    handed_back = tmp_path / "loop"
    resolve = Path.resolve

    def gives_up(self: Path, strict: bool = False) -> Path:
        return handed_back if self == handed_back else resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", gives_up)
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == handed_back)

    with pytest.raises(PathEscape):
        confine(tmp_path, handed_back)


# --- whether anything can be written here ------------------------------------------------------


def test_an_ordinary_folder_is_writable(tmp_path: Path) -> None:
    assert mount_is_readonly(tmp_path) is False
    assert is_writable(tmp_path) is True


@POSIX_ONLY
def test_a_folder_this_account_may_not_write_in_is_not_writable(tmp_path: Path) -> None:
    """A read-write filesystem is not permission to write: a handed-over folder's files can belong
    to somebody else."""
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        assert mount_is_readonly(locked) is False
        assert is_writable(locked) is False
    finally:
        locked.chmod(0o700)


@pytest.mark.skipif(not WINDOWS, reason="a junction is a Windows reparse point")
def test_a_junction_pointing_out_of_the_root_is_refused(tmp_path: Path) -> None:
    """A Windows junction (`mklink /J`, no privilege needed) is refused: `is_symlink()` is False for
    it, so the resolved-path-under-the-root check alone refuses it, which this proves."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")

    link = root / "jlink"
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr
    assert link.is_symlink() is False  # the whole point: it does not look like a link

    with pytest.raises(PathEscape):
        confine(root, link / "secret.txt")


def test_a_folder_the_filesystem_refuses_to_describe_is_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mount the operating system describes as read-only answers read-only, at the seam, on every
    platform."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("sift.kernel.paths.os.stat", refuse, raising=False)
    if not WINDOWS:
        monkeypatch.setattr("sift.kernel.paths.os.statvfs", refuse, raising=False)

    assert mount_is_readonly(tmp_path) is True
    assert is_writable(tmp_path) is False


def test_a_folder_that_is_not_there_reads_as_read_only(tmp_path: Path) -> None:
    """Fail closed. Not being able to ask must never come back as "yes, go ahead"."""
    assert mount_is_readonly(tmp_path / "never-made") is True
    assert is_writable(tmp_path / "never-made") is False


def test_the_answer_is_about_the_filesystem_and_not_the_directory(tmp_path: Path) -> None:
    """A subdirectory gets its mount's answer."""
    nested = tmp_path / "one" / "two"
    nested.mkdir(parents=True)

    assert mount_is_readonly(nested) == mount_is_readonly(tmp_path)


def test_a_link_check_that_cannot_be_answered_does_not_refuse_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing to ask whether the resolved path is still a link is not an escape: a folder gone
    between the calls refuses the question, and "cannot tell" is not "a link"."""
    inside = tmp_path / "clips"
    inside.mkdir()

    def refuse(self: Path) -> bool:
        raise OSError("stat failed")

    monkeypatch.setattr(Path, "is_symlink", refuse)

    assert confine(tmp_path, inside) == inside.resolve()


def test_two_folders_on_one_disk_are_one_filesystem(tmp_path: Path) -> None:
    """What decides whether a move is a rename or a copy of every byte."""
    here = tmp_path / "a"
    there = tmp_path / "b"
    here.mkdir()
    there.mkdir()

    assert same_filesystem(here, there) is True


def test_a_path_that_cannot_be_asked_is_treated_as_a_different_filesystem(
    tmp_path: Path,
) -> None:
    """Fail closed: "same" would promise a rename that fails with EXDEV half-way, which is worse
    than refusing a possible move."""
    assert same_filesystem(tmp_path, tmp_path / "was never there") is False


# --- the arms this platform cannot reach on its own, driven through the flag that chooses


def test_a_path_whose_link_status_cannot_be_read_is_still_confined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where the filesystem will not answer whether a handed-back path is a link, the containment
    check alone decides."""
    inside = tmp_path / "inside"
    inside.mkdir()
    # On the concrete class, because `Path` on a given platform is a subclass that defines its own.
    monkeypatch.setattr(
        type(inside), "is_symlink", lambda _self: (_ for _ in ()).throw(OSError("cannot say"))
    )

    assert confine(tmp_path, inside) == inside.resolve()


def test_a_posix_filesystem_is_asked_through_statvfs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The POSIX arm, on whichever platform this runs. `statvfs` is not in `os` on Windows, so it
    is stood in for: what is being checked is that the read-only flag is what decides."""
    monkeypatch.setattr(paths, "_WINDOWS", False)
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda _path: SimpleNamespace(f_flag=os.ST_RDONLY if hasattr(os, "ST_RDONLY") else 1),
        raising=False,
    )
    monkeypatch.setattr(os, "ST_RDONLY", 1, raising=False)

    assert paths.mount_is_readonly(Path("/anywhere")) is True

    monkeypatch.setattr(os, "statvfs", lambda _path: SimpleNamespace(f_flag=0), raising=False)
    assert paths.mount_is_readonly(Path("/anywhere")) is False


def test_a_posix_filesystem_that_will_not_answer_reads_as_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: withholding a capability is the safe wrong answer, and offering a write that
    fails at the moment somebody needs it is the other one."""
    monkeypatch.setattr(paths, "_WINDOWS", False)
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda _path: (_ for _ in ()).throw(OSError("gone")),
        raising=False,
    )

    assert paths.mount_is_readonly(Path("/anywhere")) is True


def test_a_volume_that_will_not_describe_itself_reads_as_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows arm fails closed when the volume cannot be named or described."""
    calls = {"volume": 1, "info": 1}

    class Kernel32:
        def GetVolumePathNameW(self, *_args: object) -> int:
            return calls["volume"]

        def GetVolumeInformationW(self, *_args: object) -> int:
            return calls["info"]

    monkeypatch.setattr(paths, "_WINDOWS", True)
    # On the real `ctypes`, because the function imports it itself, which is what keeps the
    # module importable on a platform that has no `WinDLL` at all.
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: Kernel32(), raising=False)

    calls["volume"] = 0
    assert paths.mount_is_readonly(tmp_path) is True

    calls["volume"] = 1
    calls["info"] = 0
    assert paths.mount_is_readonly(tmp_path) is True


def test_a_volume_question_that_raises_reads_as_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ctypes.ArgumentError` is not a subclass of ValueError and a path holding a NUL byte raises
    exactly that, so the guard lists what it means rather than what looks obvious."""
    monkeypatch.setattr(paths, "_WINDOWS", True)
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no such library")),
        raising=False,
    )

    assert paths.mount_is_readonly(tmp_path) is True


def test_a_path_that_will_not_resolve_at_all_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolve` can fail rather than answer: a loop the filesystem gives up on, a name it will
    not parse. Refused, because a path that cannot be resolved cannot be proved to be inside."""
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setattr(
        type(inside), "resolve", lambda _self: (_ for _ in ()).throw(OSError("gave up"))
    )

    with pytest.raises(PathEscape, match="does not resolve"):
        confine(tmp_path, inside)


def test_a_resolve_that_gave_up_and_left_a_link_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link `resolve` stopped at is refused even inside the root by every text check."""
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setattr(type(inside), "is_symlink", lambda _self: True)

    with pytest.raises(PathEscape, match="does not resolve"):
        confine(tmp_path, inside)


def test_absence_is_a_missing_file_and_not_a_share_that_is_off() -> None:
    """Only a definite no is absence. A refusal is not, and on Windows neither is the class an
    unplugged drive or a sleeping share raises, which is FileNotFoundError like a missing file."""
    assert paths.is_absence(FileNotFoundError(2, "No such file or directory"))
    assert not paths.is_absence(PermissionError(13, "Permission denied"))
    assert not paths.is_absence(OSError(5, "Input/output error"))
    if sys.platform == "win32":
        for code in (3, 53, 64, 1231):
            error = FileNotFoundError(2, "the drive or share did not answer", "x", code)
            assert not paths.is_absence(error), f"winerror {code} is not absence"
        assert paths.is_absence(FileNotFoundError(2, "The system cannot find the file", "x", 2))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows error codes")
def test_path_not_found_under_an_answering_root_is_absence(tmp_path: Path) -> None:
    """Windows raises "path not found" (3) both for an unplugged drive letter and for a file whose
    folder is gone on a drive that is there. The root tells them apart: one that answers means the
    folder went, which is absence; one that does not, or none to ask, keeps the careful reading,
    or every file under a deleted folder would stay "present" for good."""
    gone_folder = FileNotFoundError(2, "The system cannot find the path", "x", 3)
    assert paths.is_absence(gone_folder, under=tmp_path)
    assert not paths.is_absence(gone_folder, under=tmp_path / "unplugged")
    assert not paths.is_absence(gone_folder)
    # A share that is off is never absence, whatever the root says.
    share_off = FileNotFoundError(2, "The network path was not found", "x", 53)
    assert not paths.is_absence(share_off, under=tmp_path)


def test_a_folder_that_answers_is_here_and_one_that_is_gone_is_missing(tmp_path: Path) -> None:
    """The two answers any disk can prove: the folder is there, or the place holding it answered
    and it is not. A file where the folder was is the folder missing, not the folder here."""
    (tmp_path / "a-file").write_text("x")

    assert paths.presence(tmp_path) == "here"
    assert paths.presence(tmp_path / "gone") == "missing"
    assert paths.presence(tmp_path / "a-file") == "missing"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows error codes")
def test_a_share_that_did_not_answer_is_silent_never_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share that is off raises the same class as a missing folder; only its code differs. Read
    as missing, the screen would tell somebody their folder is gone when it is their network."""
    folder = tmp_path / "share" / "Media"
    real_stat = os.stat

    def share_off(target: object, *args: object, **kwargs: object) -> os.stat_result:
        if Path(str(target)) == folder:
            raise FileNotFoundError(2, "The network path was not found", str(folder), 53)
        return real_stat(target, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", share_off)

    assert paths.presence(folder) == "silent"


def test_without_codes_a_folder_is_missing_only_where_its_parent_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no code to tell a mount that went from a folder that went, the folder holding it has
    to answer before its absence counts; a whole missing branch is silent.

    The rule reads the error's shape, not the platform, so every error is handed over without a
    code, as a platform that has none raises it: the same test on every platform."""
    real_stat = os.stat

    def without_codes(target: object, *args: object, **kwargs: object) -> os.stat_result:
        try:
            return real_stat(target, *args, **kwargs)  # type: ignore[arg-type]
        except FileNotFoundError as error:
            raise FileNotFoundError(error.errno, error.strerror, error.filename) from None

    monkeypatch.setattr(os, "stat", without_codes)

    assert paths.presence(tmp_path / "gone") == "missing"
    assert paths.presence(tmp_path / "no-branch" / "gone") == "silent"
