# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning an SVG from outside into a PNG in memory, with nothing in it reaching anything.

## Why this exists

A stash-box keeps some studios' logos as SVG only, and the cover door re-encodes every picture
through the vendored ffmpeg, which has no SVG decoder. That re-encode is the door's security
property (what lands on disk is Sift's own JPEG, never a stranger's file), so the SVG is drawn here
ONCE into a PNG in memory, and that PNG goes through the same door (`covers.CoverPictures.receive`).

## What an SVG can do, and what this lets it do

An SVG is a document, not a picture: it can carry a script, a page of HTML (`foreignObject`) and
references to other resources (an `<image>` or a filter's `feImage` naming an address or a FILE, a
`<use>` naming another document, a `url(...)` in a style). The renderer, resvg, runs no script and
has no network code, but it DOES read a file an `<image>` names by path (measured, not assumed), so
the document is parsed and rewritten here before resvg sees it; only what is inside it survives:

- `script` and `foreignObject` are removed.
- A reference is kept only where it points inside the document (`#id`), or, for a picture
  element, at a picture carried inside it (`data:image/png;base64,...` and its raster kin). Any
  other `<image>` or `feImage` is removed; any other `href` attribute is dropped.
- A `url(...)` that does not start with `#`, anywhere in an attribute or a `<style>`, removes that
  attribute or that style, and so does `@import`.

No folder is ever given to resvg, and it is handed the rewritten text, never a path.

## The ground a logo is drawn on

A JPEG cover has no transparency: what was transparent comes out as whatever colour lay under
it, usually black, and a black mark on black is no picture. So the mark is drawn once to be LOOKED
AT, then again on a ground chosen from it: a light mark on a dark ground, anything else on white.
"""

from __future__ import annotations

import re
import struct
import zlib
from xml.etree import ElementTree

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

#: The longest side the mark is drawn at. The cover door scales a picture to at most 720 high, and a
#: logo is usually wide, so the longest side is given room above that; the door does the rest.
LONGEST_SIDE = 1024

#: The longest side of the small drawing the mark's lightness is read from.
LOOK_SIDE = 64

#: The most text a rewritten document may be. The door caps what it READS; this caps what an
#: internal entity can expand that into, which the cap on the input cannot see.
_MAX_TEXT = 4 * 1024 * 1024

#: The grounds, as RGBA. The dark one is the colour of Sift's darkest surface family, near enough
#: that a light mark drawn on it looks at home beside the pack's own.
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
    """Whether these bytes are an SVG document rather than a picture ffmpeg reads itself.

    By what the start of the bytes says, never by a name or a declared type: a picture's bytes say
    what it is, and a stranger chooses the rest. An XML declaration, a comment or a doctype may
    come first, so the root element is looked for in the first four kilobytes.
    """
    head = raw[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith(b"<"):
        return False
    return re.search(rb"<svg[\s>/]", head) is not None


def rasterise(raw: bytes) -> bytes:
    """The PNG an SVG draws, on a ground chosen from the mark. Raises `SvgRefused`.

    Blocking: the caller runs it in a thread.
    """
    root = _made_safe(raw)
    # Looked at small (that pass wants one number and reads every pixel in Python), then drawn at
    # full size once, on the ground that number chose.
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
    # System fonts, so a logo keeping its lettering as text draws it; nothing the document names
    # is read, and no folder is given to resolve a name against.
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
    """Draw the mark with its longest side at `longest`, its proportions kept.

    resvg draws at the document's own size, and a logo is often written at a few dozen pixels.
    The view box says what the drawing's own coordinates are; where there is none, the width and
    height are it, so one is written from them before they are replaced.
    """
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
    """The mean lightness (0 to 1) of what a PNG draws, weighted by how opaque it is.

    None when nothing is drawn at all. Reads only the PNG the renderer itself wrote (8-bit RGBA, not
    interlaced), which is the only kind it hands back.
    """
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
        rows.append(bytes(line))
        previous = line
    return width, height, rows
