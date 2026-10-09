# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning an SVG from outside into a PNG in memory, with nothing in it reaching anything.

The cover door's ffmpeg has no SVG decoder, so the mark is drawn here into a PNG that then goes
through the door. resvg reads a file an `<image>` names, so the document is rewritten first: no
script or `foreignObject`, and no reference that points outside it. A light mark is drawn on a
dark ground, anything else on white, since a JPEG has no transparency.
"""

from __future__ import annotations

import re
import struct
import zlib
from xml.etree import ElementTree

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

#: The longest side the mark is drawn at, above the door's 720 high; the door does the rest.
LONGEST_SIDE = 1024

#: The longest side of the small drawing the mark's lightness is read from.
LOOK_SIDE = 64

#: The most text a rewritten document may be: what an internal entity can expand into.
_MAX_TEXT = 4 * 1024 * 1024

#: The grounds, as RGBA; the dark one is near Sift's darkest surface.
_WHITE = (255, 255, 255, 255)
_DARK = (32, 34, 38, 255)

#: Above this mean lightness (0 to 1) of what was drawn, the mark is a light one.
_LIGHT_MARK = 0.6

_DROPPED = frozenset({"script", "foreignObject"})
_PICTURE_ELEMENTS = frozenset({"image", "feImage"})
_INLINE_PICTURE = re.compile(r"^\s*data:image/(png|jpeg|jpg|gif|webp);base64,", re.IGNORECASE)
_URL = re.compile(r"url\(\s*['\"]?\s*([^)'\"\s]*)", re.IGNORECASE)
_NUMBER = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")


class SvgRefused(ValueError):
    """The bytes are not an SVG this can draw, or it drew nothing."""


def looks_like_svg(raw: bytes) -> bool:
    """Whether the start of these bytes is an SVG document, never judged by a name or a type."""
    head = raw[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith(b"<"):
        return False
    return re.search(rb"<svg[\s>/]", head) is not None


def rasterise(raw: bytes) -> bytes:
    """The PNG an SVG draws, on a ground chosen from the mark; blocking. Raises `SvgRefused`."""
    root = _made_safe(raw)
    # Looked at small, then drawn at full size once on the ground that chose.
    lightness = _mean_lightness(_drawn(root, LOOK_SIDE, ground=None))
    if lightness is None:
        raise SvgRefused("it draws nothing")
    return _drawn(root, LONGEST_SIDE, ground=_DARK if lightness > _LIGHT_MARK else _WHITE)


def _drawn(
    root: ElementTree.Element, longest: int, *, ground: tuple[int, int, int, int] | None
) -> bytes:
    """The document drawn with its longest side at `longest`, on `ground` or on nothing."""
    from resvg import render, usvg

    _sized(root, longest)
    text = ElementTree.tostring(root, encoding="unicode")
    if len(text) > _MAX_TEXT:
        raise SvgRefused("it's too large once read")
    options = usvg.Options.default()
    # System fonts only; no folder is given to resolve a name against.
    options.load_system_fonts()
    try:
        tree = usvg.Tree.from_str(text, options)
    except BaseException as failure:
        # The binding panics (a BaseException no handler above this door would stop) where it
        # cannot read a document, a width of nought among them; an interrupt still goes on up.
        if not isinstance(failure, Exception) and type(failure).__name__ != "PanicException":
            raise
        raise SvgRefused("the renderer couldn't read it") from failure
    width, height = tree.int_size()
    if width <= 0 or height <= 0:  # pragma: no cover (a parsed tree is at least one pixel each way)
        raise SvgRefused("it has no size")
    # The affine is row-major: x' = a*x + b*y + c, y' = d*x + e*y + f.
    identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    if ground is None:
        return bytes(render(tree, identity))
    return bytes(render(tree, identity, bg_color=ground))


def _made_safe(raw: bytes) -> ElementTree.Element:
    """The document with nothing in it that points outside it."""
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError as failure:
        raise SvgRefused("it isn't text") from failure
    try:
        # The standard parser fetches no external entity and no DTD, and the expat under it
        # refuses a runaway expansion of an internal one.
        root = ElementTree.fromstring(source)  # noqa: S314 (see the comment above)
    except ElementTree.ParseError as failure:
        raise SvgRefused("it isn't well-formed") from failure
    if root.tag != f"{{{SVG_NS}}}svg":
        raise SvgRefused("it isn't an SVG")
    _strip(root)
    ElementTree.register_namespace("", SVG_NS)
    ElementTree.register_namespace("xlink", XLINK_NS)
    return root


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _strip(parent: ElementTree.Element) -> None:
    """Remove, in place, everything under `parent` that could reach outside the document."""
    for child in list(parent):
        name = _local(child.tag)
        if name in _DROPPED or (name == "style" and _reaches_out(child.text or "")):
            parent.remove(child)
            continue
        refs = [key for key in child.attrib if _local(key) == "href"]
        if name in _PICTURE_ELEMENTS and any(
            not _INLINE_PICTURE.match(child.attrib[key]) for key in refs
        ):
            parent.remove(child)
            continue
        for key in list(child.attrib):
            value = child.attrib[key]
            outside_ref = (
                _local(key) == "href"
                and name not in _PICTURE_ELEMENTS
                and not value.strip().startswith("#")
            )
            if outside_ref or _reaches_out(value) or _local(key).startswith("on"):
                del child.attrib[key]
        _strip(child)
    for key in list(parent.attrib):
        if _reaches_out(parent.attrib[key]) or _local(key).startswith("on"):
            del parent.attrib[key]


def _reaches_out(value: str) -> bool:
    """Whether a style or an attribute names something outside the document."""
    if "@import" in value.lower():
        return True
    return any(not target.startswith("#") for target in _URL.findall(value))


def _sized(root: ElementTree.Element, longest: int) -> None:
    """Draw the mark with its longest side at `longest`, writing a view box where there is none."""
    box = root.get("viewBox")
    if box is None:
        width, height = _length(root.get("width")), _length(root.get("height"))
        if width is None or height is None:
            return
        root.set("viewBox", f"0 0 {width:g} {height:g}")
    else:
        try:
            _x, _y, width, height = (float(one) for one in box.replace(",", " ").split())
        except ValueError:
            return
    if width <= 0 or height <= 0:
        return
    scale = longest / max(width, height)
    root.set("width", f"{max(1, round(width * scale))}")
    root.set("height", f"{max(1, round(height * scale))}")


def _length(value: str | None) -> float | None:
    found = _NUMBER.match(value or "")
    return float(found.group(1)) if found else None


def _mean_lightness(png: bytes) -> float | None:
    """The mean lightness (0 to 1) of the renderer's own PNG, weighted by opacity; None if empty."""
    width, _height, rows = _rgba_rows(png)
    weight = 0.0
    total = 0.0
    for row in rows:
        for x in range(0, width * 4, 4):
            alpha = row[x + 3] / 255
            if alpha:
                red, green, blue = row[x] / 255, row[x + 1] / 255, row[x + 2] / 255
                total += alpha * (0.2126 * red + 0.7152 * green + 0.0722 * blue)
                weight += alpha
    return None if weight == 0 else total / weight


def _rgba_rows(png: bytes) -> tuple[int, int, list[bytes]]:
    """The unfiltered rows of an 8-bit RGBA PNG. Raises `SvgRefused` for anything else."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise SvgRefused("the renderer answered with no picture")
    at, width, height, packed = 8, 0, 0, bytearray()
    while at + 8 <= len(png):
        (size,) = struct.unpack(">I", png[at : at + 4])
        kind, body = png[at + 4 : at + 8], png[at + 8 : at + 8 + size]
        if kind == b"IHDR":
            width, height, depth, colour, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or colour != 6 or interlace != 0:
                raise SvgRefused("the renderer answered with an unexpected picture")
        elif kind == b"IDAT":
            packed += body
        at += 12 + size
    data = zlib.decompress(bytes(packed))
    stride = width * 4
    rows: list[bytes] = []
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filtering, line = data[start], bytearray(data[start + 1 : start + 1 + stride])
        _unfilter(filtering, line, previous, stride)
        rows.append(bytes(line))
        previous = line
    return width, height, rows


def _unfilter(filtering: int, line: bytearray, previous: bytearray, stride: int) -> None:
    """Undo one row's PNG filter in place, against the row before it."""
    for x in range(stride):
        left = line[x - 4] if x >= 4 else 0
        up = previous[x]
        corner = previous[x - 4] if x >= 4 else 0
        if filtering == 1:
            line[x] = (line[x] + left) & 0xFF
        elif filtering == 2:
            line[x] = (line[x] + up) & 0xFF
        elif filtering == 3:
            line[x] = (line[x] + (left + up) // 2) & 0xFF
        elif filtering == 4:
            guess = left + up - corner
            near = min(
                (abs(guess - left), 0, left),
                (abs(guess - up), 1, up),
                (abs(guess - corner), 2, corner),
            )
            line[x] = (line[x] + near[2]) & 0xFF
