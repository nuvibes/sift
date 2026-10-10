# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an archive is allowed to be, and what it is refused for: each attack is paired with the
innocent thing it must not be confused with, since a gate refusing everything passes every
attack."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from sift.kernel import archives
from sift.kernel.archives import ArchiveRefused

PICTURES = frozenset({".jpg", ".png"})


def _zip(path: Path, members: dict[str, bytes], *, packed: bool = True) -> Path:
    """An archive of exactly these members, compressed by default: a `ZIP_STORED` bomb has a ratio
    of one and would test nothing."""
    how = zipfile.ZIP_DEFLATED if packed else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", compression=how) as writing:
        for name, payload in members.items():
            writing.writestr(name, payload)
    return path


def _mark_encrypted(path: Path) -> Path:
    """Set the "encrypted" bit by hand (`zipfile` will not write one), at offset 6 of each local
    header and 8 of each central-directory header."""
    raw = bytearray(path.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = raw.find(signature)
        while at != -1:
            raw[at + offset] |= 0x1
            at = raw.find(signature, at + 1)
    path.write_bytes(bytes(raw))
    return path


def test_an_ordinary_gallery_lists_its_pictures_in_order(tmp_path: Path) -> None:
    """An ordinary gallery lists its pictures in the archive's own order."""
    archive = _zip(
        tmp_path / "shoot.zip",
        {"01.jpg": b"a", "02.jpg": b"b", "03.png": b"c", "readme.txt": b"notes"},
    )
    found = archives.inspect(archive, wanted=PICTURES)
    assert [member.path for member in found] == ["01.jpg", "02.jpg", "03.png"]
    assert found[0].name == "01.jpg"


def test_a_member_that_climbs_out_refuses_the_whole_archive(tmp_path: Path) -> None:
    """A member that climbs out refuses the whole archive: `../..` is somebody trying something."""
    archive = tmp_path / "slip.zip"
    with zipfile.ZipFile(archive, "w") as writing:
        writing.writestr("good.jpg", b"a")
        writing.writestr("../../escaped.jpg", b"b")
    with pytest.raises(ArchiveRefused, match="outside itself"):
        archives.inspect(archive, wanted=PICTURES)


def test_a_backslash_separator_is_normalised_before_it_is_judged(tmp_path: Path) -> None:
    """Backslashes become separators before judging, or `..\\..\\x` has no `..` component."""
    archive = tmp_path / "windows.zip"
    with zipfile.ZipFile(archive, "w") as writing:
        writing.writestr("..\\..\\escaped.jpg", b"b")
    with pytest.raises(ArchiveRefused, match="outside itself"):
        archives.inspect(archive, wanted=PICTURES)


def test_an_absolute_or_drive_qualified_name_is_refused(tmp_path: Path) -> None:
    """The other two ways out, neither with a `..`."""
    for name in ("/etc/passwd.jpg", "C:/windows/system32/x.jpg"):
        archive = tmp_path / f"{name.count('/')}-abs.zip"
        with zipfile.ZipFile(archive, "w") as writing:
            writing.writestr(name, b"b")
        with pytest.raises(ArchiveRefused, match="outside itself"):
            archives.inspect(archive, wanted=PICTURES)


def test_a_nested_archive_is_skipped_rather_than_opened(tmp_path: Path) -> None:
    """A nested archive is skipped, never opened, or every cap could be nested under."""
    archive = _zip(tmp_path / "outer.zip", {"a.jpg": b"a", "inner.zip": b"PK\x03\x04"})
    assert [member.path for member in archives.inspect(archive, wanted=PICTURES)] == ["a.jpg"]


def test_a_password_protected_archive_is_refused_with_a_sentence(tmp_path: Path) -> None:
    """A password-protected archive is refused: Sift has nowhere to ask for one."""
    locked = _mark_encrypted(_zip(tmp_path / "locked.zip", {"a.jpg": b"a" * 200}))
    with pytest.raises(ArchiveRefused, match="password-protected"):
        archives.inspect(locked, wanted=PICTURES)


def test_too_many_members_is_refused_before_anything_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read off the index."""
    monkeypatch.setattr(archives, "MAX_MEMBERS", 2)
    archive = _zip(tmp_path / "many.zip", {"a.jpg": b"a", "b.jpg": b"b", "c.jpg": b"c"})
    with pytest.raises(ArchiveRefused, match="more than Sift will index"):
        archives.inspect(archive, wanted=PICTURES)


def test_a_member_claiming_to_be_enormous_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archives, "MAX_MEMBER_BYTES", 4)
    archive = _zip(tmp_path / "big.zip", {"a.jpg": b"0123456789"})
    with pytest.raises(ArchiveRefused, match="too big to index"):
        archives.inspect(archive, wanted=PICTURES)


def test_an_archive_claiming_to_unpack_to_too_much_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archives, "MAX_TOTAL_BYTES", 12)
    archive = _zip(tmp_path / "total.zip", {"a.jpg": b"0123456789", "b.jpg": b"0123456789"})
    with pytest.raises(ArchiveRefused, match="more than Sift will index"):
        archives.inspect(archive, wanted=PICTURES)


def test_a_bomb_ratio_is_refused_and_a_real_gallery_is_not(tmp_path: Path) -> None:
    """A bomb's ratio is refused and a real gallery's (pictures sit near one) is not."""
    bomb = _zip(tmp_path / "bomb.zip", {"a.jpg": b"\x00" * 5_000_000})
    with pytest.raises(ArchiveRefused, match="unpacks to far more"):
        archives.inspect(bomb, wanted=PICTURES)

    # Incompressible bytes, compressed, so this goes down the bomb's code path.
    gallery = _zip(tmp_path / "real.zip", {f"{n}.jpg": os.urandom(20_000) for n in range(5)})
    assert len(archives.inspect(gallery, wanted=PICTURES)) == 5


def test_a_file_that_is_not_an_archive_is_refused_rather_than_crashing(tmp_path: Path) -> None:
    not_one = tmp_path / "picture.zip"
    not_one.write_bytes(b"not a zip at all")
    with pytest.raises(ArchiveRefused, match="not a readable archive"):
        archives.inspect(not_one, wanted=PICTURES)


def test_extracting_writes_the_member_and_reports_its_real_size(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "s.zip", {"in/a.jpg": b"hello"})
    cache = tmp_path / "cache"
    written = archives.extract_member(archive, "in/a.jpg", cache / "a.jpg", cache_dir=cache)
    assert written == 5
    assert (cache / "a.jpg").read_bytes() == b"hello"


def test_extracting_refuses_a_destination_outside_the_cache(tmp_path: Path) -> None:
    """Extraction refuses a destination outside the cache, whatever the caller built."""
    archive = _zip(tmp_path / "s.zip", {"a.jpg": b"hello"})
    cache = tmp_path / "cache"
    cache.mkdir()
    with pytest.raises(ArchiveRefused, match="inside the cache"):
        archives.extract_member(archive, "a.jpg", tmp_path / "elsewhere.jpg", cache_dir=cache)


def test_a_member_bigger_than_it_promised_is_cut_off_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Size is measured as it is written, and a half-written picture is removed."""
    archive = _zip(tmp_path / "s.zip", {"a.jpg": b"0123456789"})
    cache = tmp_path / "cache"
    monkeypatch.setattr(archives, "MAX_MEMBER_BYTES", 4)
    destination = cache / "a.jpg"
    with pytest.raises(ArchiveRefused, match="bigger than it claimed"):
        archives.extract_member(archive, "a.jpg", destination, cache_dir=cache)
    assert not destination.exists()


def test_an_index_that_lies_about_a_size_is_refused_and_nothing_is_kept(tmp_path: Path) -> None:
    """An index that lies about a size is caught by measuring what came out, and the file is thrown
    away; built by overwriting the central directory's uncompressed size, which `getinfo` reads."""
    archive = _zip(tmp_path / "liar.zip", {"a.jpg": b"0123456789"}, packed=False)
    raw = bytearray(archive.read_bytes())
    at = raw.find(b"PK\x01\x02")
    assert at != -1, "no central directory to edit: the fixture, not the code"
    raw[at + 24 : at + 28] = (9_000).to_bytes(4, "little")
    archive.write_bytes(bytes(raw))

    cache = tmp_path / "cache"
    destination = cache / "a.jpg"
    with pytest.raises(ArchiveRefused):
        archives.extract_member(archive, "a.jpg", destination, cache_dir=cache)
    assert not destination.exists(), "a picture that did not match its promise was kept"


def test_asking_for_a_member_that_is_not_there_is_refused_not_a_crash(tmp_path: Path) -> None:
    """A database row can outlive the archive it names a member of."""
    archive = _zip(tmp_path / "s.zip", {"a.jpg": b"hello"})
    cache = tmp_path / "cache"
    with pytest.raises(ArchiveRefused, match="could not be read"):
        archives.extract_member(archive, "gone.jpg", cache / "gone.jpg", cache_dir=cache)


def test_a_folder_entry_inside_an_archive_is_not_a_picture(tmp_path: Path) -> None:
    """A directory entry is not a picture, or it would be an empty tile."""
    archive = tmp_path / "nested.zip"
    with zipfile.ZipFile(archive, "w") as writing:
        writing.writestr("shoot/", b"")
        writing.writestr("shoot/01.jpg", b"a")
    assert [m.path for m in archives.inspect(archive, wanted=PICTURES)] == ["shoot/01.jpg"]


def test_a_member_whose_name_is_nothing_but_separators_is_refused(tmp_path: Path) -> None:
    """A name of only separators normalises to nothing and would join to the cache directory."""
    archive = tmp_path / "empty-name.zip"
    with zipfile.ZipFile(archive, "w") as writing:
        # `.`, not `//`: a trailing slash is a directory entry, skipped earlier.
        writing.writestr(".", b"a")
    with pytest.raises(ArchiveRefused, match="a file with no name"):
        archives.inspect(archive, wanted=PICTURES)


def test_only_a_zip_is_something_to_open() -> None:
    assert archives.is_archive(Path("a/b/gallery.ZIP"))
    assert not archives.is_archive(Path("a/b/gallery.rar"))
    assert not archives.is_archive(Path("a/b/holiday.jpg"))


# --- the four numbers the refusals are made of: too high never fires, too low refuses galleries


def test_every_cap_is_a_real_number_and_none_of_them_is_off() -> None:
    """Every cap is a real number, or the check cannot fire."""
    for cap in (
        archives.MAX_MEMBERS,
        archives.MAX_TOTAL_BYTES,
        archives.MAX_MEMBER_BYTES,
        archives.MAX_RATIO,
    ):
        assert isinstance(cap, int)
        assert cap > 0


def test_no_single_member_may_be_as_big_as_everything_together() -> None:
    """The per-member cap is below the total, or it decides nothing."""
    assert archives.MAX_MEMBER_BYTES < archives.MAX_TOTAL_BYTES


def test_the_caps_are_wide_enough_for_a_real_shoot_and_narrow_enough_to_matter() -> None:
    """The caps fit a real shoot (hundreds of pictures of a few megabytes) and stop a bomb."""
    a_big_shoot = 1_000
    a_large_picture = 200 * 1024**2
    a_raw_frame = 30 * 1024**2
    assert a_big_shoot <= archives.MAX_MEMBERS
    assert archives.MAX_MEMBERS <= 100_000, "past this, walking the index is the cost"
    assert a_large_picture <= archives.MAX_MEMBER_BYTES
    assert a_big_shoot * a_raw_frame <= archives.MAX_TOTAL_BYTES
    # A real gallery sits near a ratio of 1, a bomb in the thousands.
    assert 1 < archives.MAX_RATIO < 1_000


def _fstat_saying(monkeypatch: pytest.MonkeyPatch, **changed: int) -> None:
    """`os.fstat` in the copy answering with these fields changed, as another file would."""
    real = os.fstat

    def saying(fd: int) -> os.stat_result:
        found = real(fd)
        values = list(found)
        for field, value in changed.items():
            values[{"st_mode": 0, "st_size": 6}[field]] = value
        times = {
            name: getattr(found, name) for name in ("st_atime_ns", "st_mtime_ns", "st_ctime_ns")
        }
        return os.stat_result(values, times)

    monkeypatch.setattr("sift.kernel.archives.os.fstat", saying)


def test_a_copy_of_a_pipe_is_refused_before_a_byte_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pipe"
    source.write_bytes(b"x" * 10)
    _fstat_saying(monkeypatch, st_mode=0o010644)
    with pytest.raises(archives.CopyChanged, match="not a regular file"):
        archives.copy_settled(source, tmp_path / "out", size=10)
    assert not (tmp_path / "out").exists()


def test_a_file_that_shrinks_while_it_is_copied_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "one.mp4"
    source.write_bytes(b"x" * 10)
    _fstat_saying(monkeypatch, st_size=20)
    with pytest.raises(archives.CopyChanged, match="still being written"):
        archives.copy_settled(source, tmp_path / "out.mp4", size=20)
    assert [one.name for one in tmp_path.iterdir()] == ["one.mp4"]
