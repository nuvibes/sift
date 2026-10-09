# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture half of the site-icon build: any logo in, one clean 256-pixel square out.

Pure but for `Renderer`, so its rules are tested without a network. Each picture is read by
Chromium, worked at `WORK_LONG` at most, its light plate taken off, trimmed to the mark, fitted
with `MARGIN` by a Lanczos-3 resample on premultiplied colour, and written as full-colour RGBA:
a palette step would threshold alpha at one bit, a jagged edge by construction.
"""

from __future__ import annotations

import base64
import json
import struct
import subprocess  # nosec (node, on an argv list written in this file)
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from sift.kernel.site_icons import ICON_PIXELS

#: Read from the package that serves the pack, which owns the number.
SIZE = ICON_PIXELS

#: Enough that an anti-aliased edge is never cut by the square's edge.
MARGIN = 8

#: Big enough that a vector with generous padding still arrives larger than `SIZE` once trimmed.
DRAW_LONG = 1024

#: Three times the output: the plate's edge is found here and carried down by the resample.
WORK_LONG = 768

#: Below this many source pixels a mark is `quality: low`: a clean 180-pixel touch icon is sharp on
#: a HiDPI tile, a 48-pixel favicon is a smear.
LOW_BELOW = 160

#: How far (0-255, largest channel) a pixel may be from the plate colour and still BE the plate.
PLATE_TOLERANCE = 40

#: Not zero: a soft glow of nearly transparent pixels would stop a trim at the picture's edge.
EMPTY_ALPHA = 8

Pixels = npt.NDArray[np.uint8]


# --- reading ------------------------------------------------------------------------------------


class Renderer:
    """One Chromium for the whole build, behind a lock: bytes in, an RGBA array out."""

    def __init__(self, node: str, script: Path, playwright: Path) -> None:
        self._process = subprocess.Popen(
            [node, str(script), str(playwright)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._lock = threading.Lock()
        self._next = 0

    def _ask(self, asked: dict[str, object]) -> dict[str, object]:
        """One request to the browser and its one-line answer. Serialised: one browser, one page."""
        with self._lock:
            self._next += 1
            asked = {"id": self._next, **asked}
            if self._process.stdin is None or self._process.stdout is None:
                raise RuntimeError("the renderer has no pipes")
            self._process.stdin.write(json.dumps(asked).encode("ascii") + b"\n")
            self._process.stdin.flush()
            line = self._process.stdout.readline()
        if not line:
            raise RuntimeError("the renderer stopped")
        answer = json.loads(line)
        return answer if isinstance(answer, dict) else {"error": "not an answer"}

    def sheet(self, cells: list[dict[str, object]], *, columns: int, cell: int) -> bytes:
        """A contact sheet of icons as PNG bytes, each at `cell` pixels with its label under it."""
        answer = self._ask({"sheet": {"cells": cells, "columns": columns, "cell": cell}})
        if "png" not in answer:
            raise RuntimeError(f"the contact sheet was not drawn: {answer.get('error')}")
        return base64.b64decode(str(answer["png"]))

    def read(self, raw: bytes, *, svg: bool) -> Pixels | None:
        """The picture in these bytes as an H x W x 4 array, or None when it is not a picture."""
        answer = self._ask(
            {"svg": svg, "data": base64.b64encode(raw).decode("ascii"), "long": DRAW_LONG}
        )
        if "error" in answer or not answer.get("width"):
            return None
        width, height = int(str(answer["width"])), int(str(answer["height"]))
        flat = np.frombuffer(base64.b64decode(str(answer["rgba"])), dtype=np.uint8)
        if flat.size != width * height * 4:
            return None
        return flat.reshape(height, width, 4).copy()

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover (a browser that will not quit)
            self._process.kill()


def looks_like_svg(raw: bytes) -> bool:
    """An SVG by its content, not by what a server called it: a site's content type is a claim."""
    head = raw[:1024].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in raw[:4096].lower())


# --- resampling -----------------------------------------------------------------------------------


def _lanczos(x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    out = np.sinc(x) * np.sinc(x / 3.0)
    out[np.abs(x) >= 3.0] = 0.0
    return out


def _weights(size_in: int, size_out: int) -> npt.NDArray[np.float64]:
    """The (out x in) Lanczos-3 matrix along one axis, widened by the scale when shrinking."""
    scale = size_out / size_in
    stretch = min(scale, 1.0)
    support = 3.0 / stretch
    matrix = np.zeros((size_out, size_in), dtype=np.float64)
    for index in range(size_out):
        centre = (index + 0.5) / scale - 0.5
        first = int(np.floor(centre - support))
        last = int(np.ceil(centre + support))
        taps = np.arange(first, last + 1)
        weights = _lanczos((taps - centre) * stretch)
        total = weights.sum()
        if total == 0:
            continue
        for tap, weight in zip(np.clip(taps, 0, size_in - 1), weights / total, strict=True):
            matrix[index, tap] += weight
    return matrix


def resample(rgba: Pixels, width: int, height: int) -> Pixels:
    """This picture at `width` x `height`, resampled on premultiplied colour: no fringe."""
    work = rgba.astype(np.float64) / 255.0
    alpha = work[..., 3:4]
    work[..., :3] *= alpha
    rows = _weights(rgba.shape[0], height)
    cols = _weights(rgba.shape[1], width)
    out = np.einsum("oh,hwc->owc", rows, work)
    out = np.einsum("pw,owc->opc", cols, out)
    out = np.clip(out, 0.0, 1.0)
    alpha = out[..., 3:4]
    safe = np.where(alpha > 1e-6, alpha, 1.0)
    out[..., :3] = np.where(alpha > 1e-6, np.clip(out[..., :3] / safe, 0.0, 1.0), 0.0)
    return np.round(out * 255.0).astype(np.uint8)


def shrink_to(rgba: Pixels, longest: int) -> Pixels:
    """At most `longest` pixels on the longer side. Never enlarged."""
    height, width = rgba.shape[:2]
    if max(height, width) <= longest:
        return rgba
    scale = longest / max(height, width)
    return resample(rgba, max(1, round(width * scale)), max(1, round(height * scale)))


# --- the plate -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Plate:
    """What `remove_plate` found and did."""

    #: The picture, with the plate taken off where it was.
    rgba: Pixels
    #: True when a plate was taken off.
    removed: bool
    #: True when the picture is a brand TILE: a mark on a square of its own colour, kept whole.
    tile: bool


def _dilate(mask: npt.NDArray[np.bool_]) -> npt.NDArray[np.bool_]:
    grown = mask.copy()
    grown[1:, :] |= mask[:-1, :]
    grown[:-1, :] |= mask[1:, :]
    grown[:, 1:] |= mask[:, :-1]
    grown[:, :-1] |= mask[:, 1:]
    return grown


def _flood(allowed: npt.NDArray[np.bool_], seeds: npt.NDArray[np.bool_]) -> npt.NDArray[np.bool_]:
    """Everything in `allowed` reachable from `seeds` through `allowed`, four-connected."""
    region = seeds & allowed
    for _ in range(4 * sum(allowed.shape)):
        grown = _dilate(region) & allowed
        if np.array_equal(grown, region):
            break
        region = grown
    return region


def _coarse_flood(allowed: npt.NDArray[np.bool_]) -> npt.NDArray[np.bool_]:
    """The plate region, flooded small (a block allowed only when all of it is), then full size."""
    height, width = allowed.shape
    step = max(1, max(height, width) // 256)
    if step == 1:
        seeds = np.zeros_like(allowed)
        seeds[0, :] = seeds[-1, :] = True
        seeds[:, 0] = seeds[:, -1] = True
        return _flood(allowed, seeds)
    tall, wide = -(-height // step), -(-width // step)
    padded = np.ones((tall * step, wide * step), dtype=bool)
    padded[:height, :width] = allowed
    small = padded.reshape(tall, step, wide, step).all(axis=(1, 3))
    seeds = np.zeros_like(small)
    seeds[0, :] = seeds[-1, :] = True
    seeds[:, 0] = seeds[:, -1] = True
    coarse = _flood(small, seeds)
    region = np.repeat(np.repeat(coarse, step, axis=0), step, axis=1)[:height, :width] & allowed
    edge = np.zeros_like(allowed)
    edge[0, :] = edge[-1, :] = True
    edge[:, 0] = edge[:, -1] = True
    region |= edge & allowed
    for _ in range(3 * step):
        grown = _dilate(region) & allowed
        if np.array_equal(grown, region):
            break
        region = grown
    return region


def plate_colour(rgba: Pixels) -> npt.NDArray[np.float64] | None:
    """An opaque plate's colour off the outer ring (corners can carry a frame), or None."""
    height, width = rgba.shape[:2]
    ring = max(1, min(height, width) // 50)
    edge = np.concatenate(
        [
            rgba[:ring].reshape(-1, 4),
            rgba[height - ring :].reshape(-1, 4),
            rgba[:, :ring].reshape(-1, 4),
            rgba[:, width - ring :].reshape(-1, 4),
        ]
    )
    if float((edge[:, 3] >= 250).mean()) < 0.95:
        return None
    colour = np.median(edge[:, :3].astype(np.float64), axis=0)
    near = np.abs(edge[:, :3].astype(np.float64) - colour).max(axis=1) <= PLATE_TOLERANCE
    if float(near.mean()) < 0.8:
        return None
    return np.asarray(colour, dtype=np.float64)


def kind_of_plate(colour: npt.NDArray[np.float64]) -> str:
    """`light`, `dark` or `colour`; a pale tint of any hue reads on a tile as a light plate."""
    spread = float(colour.max() - colour.min())
    level = float(colour.mean())
    if level >= 200:
        return "light"
    if spread > 30:
        return "colour"
    if level >= 170:
        return "light"
    if level <= 60:
        return "dark"
    return "colour"


def remove_plate(rgba: Pixels, *, force: bool = False) -> Plate:
    """Take an opaque light plate off a logo, anti-aliasing the new edge; keep any other as a tile.

    A dark or coloured plate is the brand's own tile (a black one carries a white mark). The fill
    starts at the corners, so a white shape inside the mark is never reached; `force` takes any
    plate off, for a site in `site_icon_catalog.PLATE_OFF`.
    """
    colour = plate_colour(rgba)
    if colour is None:
        return Plate(rgba, removed=False, tile=False)
    kind = kind_of_plate(colour)
    if kind != "light" and not force:
        return Plate(rgba, removed=False, tile=True)

    work = rgba.astype(np.float64)
    distance = np.abs(work[..., :3] - colour).max(axis=2)
    allowed = distance <= PLATE_TOLERANCE
    plate = _coarse_flood(allowed)
    if not plate.any():
        return Plate(rgba, removed=False, tile=False)

    band = _dilate(_dilate(plate)) & ~plate
    # Colour to alpha: the least opacity at which this pixel over the plate shows as it does.
    above = np.where(
        work[..., :3] > colour, (work[..., :3] - colour) / np.maximum(255.0 - colour, 1.0), 0.0
    )
    below = np.where(
        work[..., :3] < colour, (colour - work[..., :3]) / np.maximum(colour, 1.0), 0.0
    )
    need = np.clip(np.maximum(above, below).max(axis=2), 0.0, 1.0)
    out = work.copy()
    opacity = work[..., 3] / 255.0
    edge = band & (need < 1.0)
    new_alpha = np.minimum(opacity, need)
    safe = np.maximum(new_alpha, 1e-6)[..., None]
    unblended = np.clip((work[..., :3] - colour * (1.0 - new_alpha[..., None])) / safe, 0.0, 255.0)
    out[..., :3] = np.where(edge[..., None], unblended, out[..., :3])
    out[..., 3] = np.where(edge, new_alpha * 255.0, out[..., 3])
    out[..., 3] = np.where(plate, 0.0, out[..., 3])
    return Plate(np.round(out).astype(np.uint8), removed=True, tile=False)


# --- trimming, fitting, writing -------------------------------------------------------------------


def content_box(rgba: Pixels) -> tuple[int, int, int, int] | None:
    """(top, left, bottom, right) of everything not empty, bottom and right exclusive."""
    solid = rgba[..., 3] > EMPTY_ALPHA
    if not solid.any():
        return None
    rows = np.flatnonzero(solid.any(axis=1))
    cols = np.flatnonzero(solid.any(axis=0))
    return int(rows[0]), int(cols[0]), int(rows[-1]) + 1, int(cols[-1]) + 1


@dataclass(frozen=True, slots=True)
class Icon:
    """One finished icon and what is recorded about it."""

    rgba: Pixels
    #: How many source pixels the mark spanned on its longer side, before it was fitted.
    source_pixels: int
    plate_removed: bool
    tile: bool

    @property
    def quality(self) -> str:
        return "low" if self.source_pixels < LOW_BELOW else "high"


#: How nearly one colour, and how much of its box, a picture must be to be a blank tile.
BLANK_SPREAD = 12
BLANK_FILL = 0.8


def is_blank(mark: Pixels) -> bool:
    """A filled shape of one flat colour with nothing on it: a tile whose mark is missing."""
    solid = mark[..., 3] >= 250
    if not solid.any() or float(solid.mean()) < BLANK_FILL:
        return False
    colours = mark[solid][:, :3].astype(np.int16)
    if int((colours.max(axis=0) - colours.min(axis=0)).max()) > BLANK_SPREAD:
        return False
    # And no holes: a mark cut out of a flat tile is a logo.
    clear = mark[..., 3] < 128
    holes = clear & ~_coarse_flood(clear)
    return float(holes.mean()) < 0.01


def make_icon(rgba: Pixels, *, plate_off: bool = False) -> Icon | None:
    """A picture, made into the pack's square. None when there is nothing in it to draw."""
    rgba = shrink_to(rgba, WORK_LONG)
    plate = remove_plate(rgba, force=plate_off)
    box = content_box(plate.rgba)
    if box is None:
        return None
    top, left, bottom, right = box
    mark = plate.rgba[top:bottom, left:right]
    if is_blank(mark):
        return None
    tall, wide = mark.shape[:2]
    inner = SIZE - 2 * MARGIN
    scale = inner / max(tall, wide)
    width = max(1, round(wide * scale))
    height = max(1, round(tall * scale))
    fitted = resample(mark, width, height)
    square = np.zeros((SIZE, SIZE, 4), dtype=np.uint8)
    y = (SIZE - height) // 2
    x = (SIZE - width) // 2
    square[y : y + height, x : x + width] = fitted
    return Icon(square, max(tall, wide), plate.removed, plate.tile)


def encode_png(rgba: Pixels) -> bytes:
    """An 8-bit RGBA PNG, rows filtered None, Sub or Up: the three undone a row at a time."""
    height, width = rgba.shape[:2]
    data = rgba.astype(np.int16).reshape(height, width * 4)
    left = np.zeros_like(data)
    left[:, 4:] = data[:, :-4]
    up = np.zeros_like(data)
    up[1:] = data[:-1]
    filtered = [((data - base) % 256).astype(np.uint8) for base in (np.zeros_like(data), left, up)]
    cost = np.stack(
        [np.minimum(one, 256 - one.astype(np.int16)).sum(axis=1) for one in filtered], axis=0
    )
    choice = cost.argmin(axis=0)
    raw = b"".join(
        bytes([int(kind)]) + filtered[int(kind)][row].tobytes() for row, kind in enumerate(choice)
    )

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + body))
        return struct.pack(">I", len(body)) + kind + body + crc

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def decode_png(raw: bytes) -> Pixels:
    """Read back a PNG that `encode_png` wrote, as H x W x 4; anything else raises."""
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    at = 8
    width = height = kind = 0
    idat = b""
    while at < len(raw):
        (length,) = struct.unpack(">I", raw[at : at + 4])
        name = raw[at + 4 : at + 8]
        body = raw[at + 8 : at + 8 + length]
        at += 12 + length
        if name == b"IHDR":
            width, height, depth, kind, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or kind not in (2, 6) or interlace:
                raise ValueError("only 8-bit RGB or RGBA, not interlaced")
        elif name == b"IDAT":
            idat += body
        elif name == b"IEND":
            break
    channels = 4 if kind == 6 else 3
    stride = width * channels
    flat = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(height, stride + 1)
    out = np.zeros((height, stride), dtype=np.uint8)
    previous = np.zeros(stride, dtype=np.uint8)
    for row in range(height):
        current = _unfiltered(flat[row], row, previous, width, channels)
        out[row] = current
        previous = current
    pixels = out.reshape(height, width, channels)
    if channels == 3:
        pixels = np.concatenate([pixels, np.full((height, width, 1), 255, np.uint8)], axis=2)
    return pixels


def _unfiltered(filtered: Pixels, row: int, previous: Pixels, width: int, channels: int) -> Pixels:
    """One row with its None, Sub or Up filter undone."""
    stride = width * channels
    line = filtered[1:]
    how = int(filtered[0])
    if how == 0:
        current = line.copy()
    elif how == 1:
        current = (
            (np.cumsum(line.reshape(width, channels).astype(np.int64), axis=0) % 256)
            .astype(np.uint8)
            .reshape(stride)
        )
    elif how == 2:
        current = ((line.astype(np.int16) + previous) % 256).astype(np.uint8)
    else:
        raise ValueError(f"row {row} is filtered {how}, which this build never writes")
    return current


def fills_its_box(rgba: Pixels) -> bool:
    """True when all four corners of the mark's own box are opaque: a full-bleed square."""
    box = content_box(rgba)
    if box is None:
        return False
    top, left, bottom, right = box
    corners = (
        rgba[top, left, 3],
        rgba[top, right - 1, 3],
        rgba[bottom - 1, left, 3],
        rgba[bottom - 1, right - 1, 3],
    )
    return min(int(one) for one in corners) >= 250


def tone_of(rgba: Pixels) -> str:
    """`light`, `dark` or `colour`: a mark's visible part, by opacity, for a ground to answer."""
    alpha = rgba[..., 3].astype(np.float64) / 255.0
    weight = alpha.sum()
    if weight <= 0:
        return "colour"
    colour = rgba[..., :3].astype(np.float64)
    spread = (colour.max(axis=2) - colour.min(axis=2)) * alpha
    level = colour.mean(axis=2)
    if spread.sum() / weight > 40:
        return "colour"
    mean_level = float((level * alpha).sum() / weight)
    if mean_level >= 190:
        return "light"
    if mean_level <= 70:
        return "dark"
    return "colour"
