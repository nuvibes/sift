# SPDX-License-Identifier: AGPL-3.0-or-later
"""The HEIF door: a phone's photograph read whole, in a process of its own, with nothing kept.

Real files and the real decoders throughout. A HEIC here is laid out as a phone lays one out, a
grid of tiles (see `heif_fixture`), because a photograph coded as one picture reads the same
through any door and would prove nothing.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from sift.kernel import heif
from sift.kernel import subprocess as kernel_subprocess
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, DerivativeKind
from sift.kernel.ids import new_id
from sift.kernel.tests.heif_fixture import TILE_COLOURS, grid_heic


def _first_stream(path: Path) -> tuple[int, int]:
    said = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    stream = json.loads(said.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _near(one: object, colour: tuple[int, int, int]) -> bool:
    assert isinstance(one, tuple)
    return all(abs(int(a) - b) <= 24 for a, b in zip(one, colour, strict=True))


async def test_a_phones_grid_is_one_tile_to_ffprobe_and_one_whole_picture_through_the_door(
    tmp_path: Path,
) -> None:
    """The fault and the answer to it, side by side: ffprobe's first stream is one 64-pixel
    tile, and the door hands back all six of them, each in its own place."""
    from PIL import Image

    photo = grid_heic(tmp_path / "IMG_0059.heic")
    assert _first_stream(photo) == (64, 64)

    made = await heif.decode(photo, tmp_path / "whole.jpg")

    assert (made.width, made.height) == (192, 128)
    with Image.open(made.path) as drawn:
        assert drawn.format == "JPEG"
        assert drawn.size == (192, 128)
        places = [(32, 32), (96, 32), (160, 32), (32, 96), (96, 96), (160, 96)]
        for place, colour in zip(places, TILE_COLOURS, strict=True):
            assert _near(drawn.getpixel(place), colour), f"the tile at {place} is not in its place"


async def test_an_avif_photograph_goes_through_the_same_door(tmp_path: Path) -> None:
    from PIL import Image

    photo = tmp_path / "still.avif"
    Image.new("RGB", (96, 64), (200, 40, 40)).save(photo)

    made = await heif.decode(photo, tmp_path / "whole.jpg")

    assert (made.width, made.height) == (96, 64)


async def test_the_copy_carries_nothing_the_camera_wrote(tmp_path: Path) -> None:
    """No EXIF at all in the copy, so no place: the copy is what a browser is handed, and a
    photograph's place never leaves Sift in anything Sift writes."""
    import pillow_heif
    from PIL import Image

    exif = Image.Exif()
    exif[0x010F] = "A camera maker"
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}
    photo = tmp_path / "placed.heic"
    pillow_heif.from_pillow(Image.new("RGB", (64, 64), (10, 120, 200))).save(
        photo, quality=90, exif=exif.tobytes()
    )
    assert b"Exif" in photo.read_bytes() or b"A camera maker" in photo.read_bytes()

    made = await heif.decode(photo, tmp_path / "whole.jpg")

    with Image.open(made.path) as drawn:
        assert "exif" not in drawn.info
        assert len(drawn.getexif()) == 0
    assert b"A camera maker" not in made.path.read_bytes()


async def test_a_file_the_decoder_refuses_is_refused_in_the_decoders_own_words(
    tmp_path: Path,
) -> None:
    """The decode runs in a child, so a file that breaks the decoder breaks the child. What the
    server is left with is a refusal it can write on the job, never a crash."""
    broken = tmp_path / "broken.heic"
    broken.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64)

    with pytest.raises(heif.HeifError, match=r"couldn.t be decoded"):
        await heif.decode(broken, tmp_path / "whole.jpg")
    assert not (tmp_path / "whole.jpg").exists()


def test_the_decoder_is_never_run_through_a_shell_and_is_given_absolute_paths(
    tmp_path: Path,
) -> None:
    argv = heif.decode_args(Path("-rf.heic"), tmp_path / "out.jpg")
    assert argv[1] == "-c"
    assert Path(argv[3]).is_absolute() and Path(argv[4]).is_absolute()
    assert not argv[3].startswith("-")


def _asset(media_type: str, mime: str) -> Asset:
    return Asset(
        id="01HX00000000000000000000AA",
        identity="digest",
        media_type=media_type,
        mime=mime,
        width=None,
        height=None,
        duration_ms=None,
        fps=None,
        size_bytes=None,
        container=None,
        vcodec=None,
        acodec=None,
        bit_depth=None,
        phash=None,
        videohash=None,
        original_filename=None,
        added_at=0,
        probed_at=None,
    )


@pytest.mark.parametrize(
    ("media_type", "mime", "expected"),
    [
        ("image", "image/heic", True),
        ("image", "image/avif", True),
        # An animated one is a sequence of whole frames, which ffmpeg reads correctly.
        ("gif", "image/avif", False),
        ("gif", "image/heic", False),
        ("image", "image/jpeg", False),
        ("image", "image/webp", False),
        ("video", "video/mp4", False),
    ],
)
def test_only_a_heif_photograph_goes_through_the_door(
    media_type: str, mime: str, expected: bool
) -> None:
    assert heif.is_heif_still(_asset(media_type, mime)) is expected


async def _a_heic_asset(store: ContentStore) -> Asset:
    asset_id = new_id()
    await store._db.execute(
        "INSERT INTO assets (id, identity, media_type, mime, added_at) "
        "VALUES (?, ?, 'image', 'image/heic', 0)",
        (asset_id, f"digest-{asset_id}"),
    )
    got = await store.get(asset_id)
    assert got is not None
    return got


async def test_the_readable_copy_is_made_once_recorded_and_made_again_when_swept(
    content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """Made at the read and found by every stage after it: decoding a 48-megapixel photograph for
    the tile, the faces, the description and the fingerprints in turn would be four decodes of
    one import. And the cache is disposable, so a swept copy is made again, never trusted."""
    photo = grid_heic(tmp_path / "IMG_0003.heic")
    asset = await _a_heic_asset(content_store)

    first = await heif.readable_copy(content_store, asset, photo, settings=settings)
    again = await heif.readable_copy(content_store, asset, photo, settings=settings)

    assert again == first and first.suffix == ".jpg"
    kinds = [one.kind for one in await content_store.derivatives(asset.id)]
    assert kinds == [DerivativeKind.RENDITION]

    first.unlink()
    remade = await heif.readable_copy(content_store, asset, photo, settings=settings)
    assert remade.is_file()


def test_the_fixture_is_a_real_heif_that_libheif_opens(tmp_path: Path) -> None:
    """The grid is written by hand, so it is checked on its own: libheif reads it as one picture
    of the size its grid item states."""
    import pillow_heif

    photo = grid_heic(tmp_path / "grid.heic")
    opened = pillow_heif.open_heif(io.BytesIO(photo.read_bytes()))
    assert opened.size == (192, 128)


async def test_a_decoder_that_cannot_start_or_says_nothing_is_a_heif_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child that cannot be started, and one that ends well but names no size: both refused."""
    from types import SimpleNamespace

    async def not_started(*args: object, **kwargs: object) -> object:
        raise kernel_subprocess.SubprocessError("no interpreter")

    monkeypatch.setattr(kernel_subprocess, "run", not_started)
    with pytest.raises(heif.HeifError, match="no interpreter"):
        await heif.decode(tmp_path / "a.heic", tmp_path / "a.jpg")

    async def silent(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(kernel_subprocess, "run", silent)
    with pytest.raises(heif.HeifError, match="without saying"):
        await heif.decode(tmp_path / "a.heic", tmp_path / "a.jpg")


async def test_another_kind_of_derivative_is_stepped_over_on_the_way_to_the_copy(
    content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    """A photograph's other derivatives come first in the list; only the rendition is the copy."""
    photo = grid_heic(tmp_path / "IMG_0004.heic")
    asset = await _a_heic_asset(content_store)
    await content_store.add_derivative(
        asset.id, DerivativeKind.THUMB, extension="jpg", size_bytes=1
    )

    copy = await heif.readable_copy(content_store, asset, photo, settings=settings)

    assert copy.is_file()
    kinds = sorted(one.kind.value for one in await content_store.derivatives(asset.id))
    assert kinds == sorted([DerivativeKind.THUMB.value, DerivativeKind.RENDITION.value])
