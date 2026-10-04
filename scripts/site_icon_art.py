# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture half of the site-icon build: any logo in, one clean 256-pixel square out.

A MAINTAINER'S HELPER, imported only by `scripts/build_site_icons.py`. Nothing in Sift imports it.
It is separate from the crawl because the two fail differently: the crawl decides WHICH picture of
a site is its logo and talks to the internet; this decides what that picture becomes and talks to
nothing. Every function here is pure except `Renderer`, so the rules below are held by tests that
need no network (`tests/gates/test_site_icon_build.py`).

## The bar, and the measurement behind it

A Sites card draws a mark at `30cqi`, about 96 CSS pixels, and an entity header at about 59. At a
device pixel ratio of 2 that is 192 device pixels, so a pack capped at 128 would be UPSCALED on
every HiDPI screen, and a 16-pixel favicon twelve times. So every icon is exactly `SIZE` square,
made by DOWNSCALING something bigger or by drawing a vector.

## What happens to one picture, in order

1. **Read** by Chromium (`site_icon_render.mjs`), into straight RGBA, SVG drawn at `DRAW_LONG`.
2. **Worked at `WORK_LONG` at most**, so the plate step below costs the same for a 4000-pixel
   banner as for a favicon.
3. **An opaque plate is taken off.** A logo served on a white or light-grey square reads on the
   tinted tile as a light square with a logo in it. See `remove_plate` for which plates are kept
   and why.
4. **Trimmed** to the mark itself, so every mark is drawn at one measure whatever padding the site
   put around it.
5. **Fitted** into the square with `MARGIN` on every side, resampled with a Lanczos-3 filter on
   PREMULTIPLIED colour: a straight-alpha resample drags the colour of invisible pixels into the
   edge and draws a dark or light fringe round every mark.
6. **Written** as a full-colour RGBA PNG. Not quantised: a palette step thresholds alpha at one
   bit (`paletteuse ... alpha_threshold=128`), which is a jagged edge by construction.
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

#: Every icon in the pack is exactly this many pixels square. The package that SERVES the pack
#: owns the number and says why it is what it is; this reads it rather than keeping a second copy.
SIZE = ICON_PIXELS

#: Transparent pixels left round the mark on every side. Small, because the client already sits the
#: mark in a box of its own; enough that an anti-aliased edge is never cut by the square's edge.
MARGIN = 8

#: What an SVG's longer side is drawn at. Big enough that a mark with generous padding in its
#: viewBox still arrives larger than `SIZE` once trimmed.
DRAW_LONG = 1024

#: The largest a picture is worked at before the plate step. Three times the output: the plate's
#: edge is found at this size and its anti-aliasing is then carried down by the resample.
WORK_LONG = 768

#: A mark drawn from fewer pixels than this across its longer side is recorded `quality: low`, and
#: the build goes on looking for a better source.
#:
#: 160, and the reason is what the tile is judged at: about 96 CSS pixels, so about 180 device
#: pixels on a HiDPI screen once the margin is taken off. A clean 180-pixel touch icon from the site
#: itself (the commonest big picture a site has) therefore reads sharp on the tile and is kept
#: rather than traded for a hunt after a vector; what is refused is the 16, 32 and 48-pixel favicon
#: that draws as a smear. Not 192, the tile's device size exactly: that would send every 180 touch
#: icon with a little padding on to a one-colour glyph instead.
LOW_BELOW = 160

#: How far (0-255, largest channel) a pixel may be from the plate colour and still BE the plate.
PLATE_TOLERANCE = 40

#: Alpha at or under this is empty space for trimming. Not zero: a site's own soft glow or a
#: JPEG-ish halo of nearly-transparent pixels would otherwise stop a trim at the picture's edge.
EMPTY_ALPHA = 8

Pixels = npt.NDArray[np.uint8]


# --- reading ------------------------------------------------------------------------------------


class Renderer:
    """One Chromium for the whole build, behind a lock: bytes in, an RGBA array out.

    A process rather than a library because the renderer is a browser. One for the run because a
    browser costs about a second to start and a build reads a thousand pictures.
    """

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
        """A CONTACT SHEET of icons as PNG bytes: each at `cell` pixels with its label under it.

        For a person to LOOK at the pack with, which nothing in the bytes can do for them: a
        wrong logo, a blurred one and a photograph all pass every gate. Each cell is
        `{png: base64, label, note, ground: "#rrggbb", low: bool}`.
        """
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
    """The (out x in) matrix of a Lanczos-3 resample along one axis, each row summing to one.

    Widened by the scale factor when shrinking, which is what makes it a DOWNSCALE filter rather
    than a point sample: every input pixel under an output pixel contributes to it.
    """
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
    """This picture at `width` x `height`, resampled on premultiplied colour.

    Premultiplied because a transparent pixel's colour is meaningless (often black, sometimes
    white), and a straight-alpha filter averages it into the visible edge beside it.
    """
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
    """Everything in `allowed` reachable from `seeds` through `allowed`, four-connected.

    Grown one pixel a step, so the cost is the region's width in steps; on a big picture the
    coarse half is done first at a quarter of the size or less (`_coarse_flood`) and only the last
    few pixels are grown here.
    """
    region = seeds & allowed
    for _ in range(4 * sum(allowed.shape)):
        grown = _dilate(region) & allowed
        if np.array_equal(grown, region):
            break
        region = grown
    return region


def _coarse_flood(allowed: npt.NDArray[np.bool_]) -> npt.NDArray[np.bool_]:
    """The plate region of a big picture, flooded at a small size and finished at the full one.

    A block of the small mask is allowed only when EVERY pixel under it is, so a thin line of the
    mark still blocks the fill at the small size rather than being averaged away and leaked through.
    The region found there is enlarged back, cut to what is allowed at full size, and then grown the
    last few pixels to the true edge, which is where the anti-aliasing is decided, so that part is
    never approximated.
    """
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
    """The colour of an opaque plate the picture sits on, or None when it has none.

    Read off the picture's outer RING (every pixel within `ring` of an edge) rather than off
    its four corners, because sites draw a hairline frame round a plate often enough to matter:
    CamSoda's touch icon is a light-grey square with a one-pixel white line along two of its edges,
    so its corners disagree about their colour while nine tenths of its ring is one grey.

    A plate is a ring that is opaque nearly everywhere and one colour over four fifths of it.
    A picture with a transparent edge, or a ring of many colours (a photograph, a gradient, a
    mark drawn to the edge of its canvas), has none.
    """
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
    """`light`, `dark` or `colour`.

    LIGHT is any plate a page could be the colour of: a grey from 170 up, or a PALE tint of any hue
    from 200 up. CamSoda's big touch icon is a pale-blue square with its ring on it, and on a tile
    that reads exactly as a grey square does: a background, not the brand.
    A saturated square (a red tile behind a white play button) is a colour, and stays.
    """
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
    """Take an opaque LIGHT plate off a logo, anti-aliasing the new edge. Keep any other.

    ## Which plates go, and which are a brand's own tile

    **A light plate goes, always.** White or light grey behind a logo is somebody's page colour, not
    the brand: it is what makes a logo read as a light square on the tile.

    **A dark plate or a coloured one STAYS, and the picture is recorded as a tile.** A coloured
    square is a brand tile by design (a red square with a white play button is what that brand
    looks like). A black one nearly always carries a WHITE mark, and the ground a mark is drawn on is
    the page colour under a scrim, so on a light page, taking the black away leaves a white mark
    on white, which is no logo at all.

    ## How it goes

    Flood-filled from the four corners through pixels within `PLATE_TOLERANCE` of the plate colour,
    so a white shape INSIDE the mark (the camera body in a camera icon) is never reached. Then the
    two-pixel band just inside the filled region is un-blended from the plate colour ("colour to
    alpha"): an anti-aliased edge pixel that was half mark, half white becomes the mark's colour at
    half opacity, rather than a hard cut with a white fringe.

    `force` takes ANY plate off, for a site named in `site_icon_catalog.PLATE_OFF`: a decision
    about one brand, made by looking at it, never a rule about colours.
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
    # Colour to alpha: the least opacity at which this pixel, laid over the plate colour, would
    # show as it does. Per channel, how far it sits from the plate towards 0 or towards 255.
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


#: How nearly one colour a picture's opaque pixels must be, and how much of its own box it must
#: fill, to be a BLANK TILE rather than a mark. See `is_blank`.
BLANK_SPREAD = 12
BLANK_FILL = 0.8


def is_blank(mark: Pixels) -> bool:
    """A filled shape of one flat colour with nothing on it: a tile whose mark is missing.

    Not hypothetical: one site's own 192-pixel icon is a yellow rounded square and nothing else,
    which draws as exactly that: a picture that identifies no site. A one-colour GLYPH
    (Simple Icons, the link-kind glyphs) is never refused by this, because a glyph is lines and
    holes and covers well under four fifths of its own box.
    """
    solid = mark[..., 3] >= 250
    if not solid.any() or float(solid.mean()) < BLANK_FILL:
        return False
    colours = mark[solid][:, :3].astype(np.int16)
    if int((colours.max(axis=0) - colours.min(axis=0)).max()) > BLANK_SPREAD:
        return False
    # ...AND NO HOLES. A mark CUT OUT of a flat tile is one colour too (Idol Erotic's icon is a
    # pink square with its mark punched through it), and it is a logo. Transparency the edge
    # cannot reach is a hole; a blank tile has none.
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
    """An 8-bit RGBA PNG, each row given whichever of None, Sub and Up compresses it best.

    Three of PNG's five filters, not all five, and the two left out are the ones that cannot be
    undone a whole row at a time: Average and Paeth each depend on the pixel just decoded to their
    left. Keeping to these three is what lets `decode_png` read the whole pack back in numpy in a
    few seconds, which is what lets a gate check every icon's pixels rather than a sample of them.
    """
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
    """Read back a PNG that `encode_png` wrote, as an H x W x 4 array.

    For the gates and the report: they check what is ON DISK, so they read it without a browser.
    8-bit RGB or RGBA, not interlaced, rows filtered None, Sub or Up, exactly what `encode_png`
    writes. Anything else RAISES rather than answering: an icon in the pack that this cannot read
    is an icon that did not come out of this build, and that is itself the finding.
    """
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
        line = flat[row, 1:]
        how = int(flat[row, 0])
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
        out[row] = current
        previous = current
    pixels = out.reshape(height, width, channels)
    if channels == 3:
        pixels = np.concatenate([pixels, np.full((height, width, 1), 255, np.uint8)], axis=2)
    return pixels


def fills_its_box(rgba: Pixels) -> bool:
    """True when all four corners of the mark's own box are opaque: a full-bleed square picture.

    What such a picture IS depends on where it came from, which this cannot see: from a site's
    declared icon it is the brand's tile; from `og:image` it is nearly always a photograph. The
    build decides with the source in hand (`build_site_icons.make_picture`).
    """
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
    """`light`, `dark` or `colour`: what the visible part of a mark is, for a ground to answer.

    Recorded in the manifest so that whatever draws the mark can tell a white mark (which needs a
    dark ground) and a black one (which needs a light ground) from a coloured one (which reads on
    either). Weighted by opacity, so an anti-aliased edge does not count as much as the body.
    """
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
