# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drawing an SVG from outside into a PNG, with nothing in it reaching anything.

The renderer runs no script and has no network code, and it DOES read a file an `<image>` names by
path: that was measured, which is why the document is rewritten before it is drawn. These hold the
rewrite: a script and an address in the document are inert, a path to a file on this machine is
never read, and what comes back is a PNG of the mark on a ground a JPEG can keep.
"""

from __future__ import annotations

import socket
import struct
import threading
import zlib
from pathlib import Path

import pytest

from sift.kernel import svg_raster
from sift.kernel.svg_raster import SvgRefused, looks_like_svg, rasterise


def _pixel(png: bytes, x: int, y: int) -> tuple[int, ...]:
    width, _height, rows = svg_raster._rgba_rows(png)
    assert 0 <= x < width
    return tuple(rows[y][x * 4 : x * 4 + 4])


def _size(png: bytes) -> tuple[int, int]:
    width, height, _rows = svg_raster._rgba_rows(png)
    return width, height


class _Listener:
    """A socket on this machine that records whether anything ever connected to it."""

    def __init__(self) -> None:
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.server.settimeout(0.2)
        self.port = self.server.getsockname()[1]
        self.connected = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self.server.accept()
            except OSError:
                continue
            self.connected = True
            client.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        self.server.close()


def test_a_script_and_an_outside_address_in_the_document_are_inert() -> None:
    """The whole proof: a script, an `<image>` on an address, a filter picture, a `<use>`
    of another document and a style `url(...)`, all pointing at a socket on this machine. The mark
    is drawn and nothing ever connects."""
    listener = _Listener()
    where = f"http://127.0.0.1:{listener.port}"
    try:
        png = rasterise(
            (
                '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
                f' viewBox="0 0 100 50" onload="fetch(&apos;{where}/onload&apos;)">'
                f"<script>fetch('{where}/script')</script>"
                f'<style>@import url("{where}/style.css"); rect {{ fill: url({where}/p) }}</style>'
                '<rect width="100" height="50" fill="#204080"/>'
                f'<image href="{where}/a.png" width="100" height="50"/>'
                f'<image xlink:href="{where}/b.png" width="100" height="50"/>'
                f'<filter id="f"><feImage href="{where}/c.png"/></filter>'
                f'<use href="{where}/other.svg#x"/>'
                f"<foreignObject><iframe xmlns='http://www.w3.org/1999/xhtml' src='{where}/f'/>"
                "</foreignObject>"
                "</svg>"
            ).encode()
        )
    finally:
        listener.close()

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert not listener.connected
    # The rectangle, which is inside the document, is what was drawn.
    assert _pixel(png, 500, 250)[:3] == (0x20, 0x40, 0x80)


def test_a_file_on_this_machine_named_by_the_document_is_never_read(tmp_path: Path) -> None:
    """Measured, not assumed: the renderer reads an absolute path an `<image>` names. A picture
    carried inside the document is the only kind that survives the rewrite."""
    secret = tmp_path / "secret.svg"
    secret.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<rect width="10" height="10" fill="#00ff00"/></svg>',
        encoding="utf-8",
    )
    for href in (str(secret), secret.as_posix(), secret.as_uri()):
        png = rasterise(
            (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                '<rect width="10" height="10" fill="#c02020"/>'
                f'<image href="{href}" width="10" height="10"/></svg>'
            ).encode()
        )

        assert _pixel(png, 500, 500)[:3] == (0xC0, 0x20, 0x20), href


def test_a_picture_carried_inside_the_document_is_drawn() -> None:
    inner = rasterise(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
        b'<rect width="4" height="4" fill="#102030"/></svg>'
    )
    import base64

    carried = base64.b64encode(inner).decode()
    png = rasterise(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            f'<image href="data:image/png;base64,{carried}" width="10" height="10"/></svg>'
        ).encode()
    )

    assert _pixel(png, 500, 500)[:3] == (0x10, 0x20, 0x30)


def test_a_light_mark_is_drawn_on_a_dark_ground_and_a_dark_one_on_white() -> None:
    """A JPEG keeps no transparency: a white logo on white, or a black one on black, is no picture."""
    light = rasterise(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        b'<circle cx="5" cy="5" r="2" fill="#ffffff"/></svg>'
    )
    dark = rasterise(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        b'<circle cx="5" cy="5" r="2" fill="#101010"/></svg>'
    )

    assert _pixel(light, 5, 5) == svg_raster._DARK
    assert _pixel(dark, 5, 5) == svg_raster._WHITE


def test_the_mark_is_drawn_with_its_longest_side_at_the_size_the_door_wants() -> None:
    """A logo written at a few dozen pixels would reach the cover as a smudge."""
    wide = rasterise(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="40px" height="10">'
        b'<rect width="40" height="10" fill="#333"/></svg>'
    )

    assert _size(wide) == (svg_raster.LONGEST_SIDE, svg_raster.LONGEST_SIDE // 4)


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "draws nothing"),
        (b'<html xmlns="http://www.w3.org/1999/xhtml"/>', "isn't an SVG"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'><rect", "isn't well-formed"),
        (b"\xff\xfe<\x00s\x00", "isn't text"),
    ],
)
def test_what_is_not_a_drawable_svg_is_refused(raw: bytes, why: str) -> None:
    with pytest.raises(SvgRefused) as refused:
        rasterise(raw)

    assert why in str(refused.value)


def test_an_svg_is_known_by_its_bytes_not_its_name() -> None:
    assert looks_like_svg(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    assert looks_like_svg(b'\xef\xbb\xbf<?xml version="1.0"?>\n<!-- a logo -->\n<svg>')
    assert not looks_like_svg(b"\x89PNG\r\n\x1a\n")
    assert not looks_like_svg(b"<html><body>svg</body></html>")
    assert not looks_like_svg(b"")


def test_a_width_of_nought_is_refused_rather_than_crashing_the_renderer() -> None:
    """The renderer's binding panics on a size it cannot draw, and a panic passes every ordinary
    handler: the door must answer with a refusal like any other bad picture."""
    with pytest.raises(SvgRefused, match="couldn't read it"):
        rasterise(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="10">'
            b'<rect width="5" height="5" fill="#333"/></svg>'
        )


class _Renderer:
    """The renderer's reading half, failing the way it is told to."""

    def __init__(self, failure: BaseException) -> None:
        failing = failure

        class Options:
            @staticmethod
            def default() -> Options:
                return Options()

            def load_system_fonts(self) -> None:
                return None

        class Tree:
            @staticmethod
            def from_str(_text: str, _options: object) -> object:
                raise failing

        self.Options = Options
        self.Tree = Tree


def test_a_document_the_renderer_fails_on_is_refused_and_an_interrupt_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import resvg

    mark = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="5"/></svg>'
    monkeypatch.setattr(resvg, "usvg", _Renderer(ValueError("unreadable")))
    with pytest.raises(SvgRefused, match="couldn't read it"):
        rasterise(mark)

    monkeypatch.setattr(resvg, "usvg", _Renderer(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        rasterise(mark)


def test_a_document_too_large_once_its_entities_are_expanded_is_refused() -> None:
    """The door caps the bytes it reads; an internal entity used many times can still expand a
    small document into one too large to hand the renderer."""
    chunk = "x" * 100_000
    uses = "&big;" * 45
    raw = (
        f'<!DOCTYPE svg [<!ENTITY big "{chunk}">]>'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><desc>{uses}</desc>'
        '<rect width="10" height="10" fill="#333"/></svg>'
    ).encode()
    assert len(raw) < svg_raster._MAX_TEXT

    with pytest.raises(SvgRefused, match="too large once read"):
        rasterise(raw)


@pytest.mark.parametrize("box", ["0 0 wide tall", "0 0 0 10", "0 0 -5 10"])
def test_a_view_box_that_says_no_size_leaves_the_mark_at_its_own_size(box: str) -> None:
    """Not four numbers, or no area: nothing to scale from, so the mark is drawn as written
    rather than refused or stretched from a guess."""
    png = rasterise(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}" width="10" height="10">'
        '<rect width="10" height="10" fill="#333"/></svg>'.encode()
    )

    assert _size(png) == (10, 10)


def _png(width: int, rows: list[tuple[int, bytes]], *, colour: int = 6) -> bytes:
    """An 8-bit PNG of these rows, each written with the filter it is paired with."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", width, len(rows), 8, colour, 0, 0, 0)
    packed = b"".join(bytes([filtering]) + line for filtering, line in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(packed))
        + chunk(b"IEND", b"")
    )


def test_every_row_filter_a_png_may_use_is_undone() -> None:
    """The lightness is read off the renderer's own PNG, whose rows may use any of the five
    filters: one undone wrongly is a mark judged light or dark by noise."""
    pixel = bytes([10, 20, 30, 255])
    plain = pixel * 2
    rows = [
        (0, plain),
        (1, pixel + bytes(4)),
        (2, bytes(8)),
        (3, bytes([5, 10, 15, 128]) + bytes([0, 0, 0, 0])),
        (4, bytes(8)),
    ]

    width, height, decoded = svg_raster._rgba_rows(_png(2, rows))

    assert (width, height) == (2, 5)
    assert decoded == [plain] * 5


def test_a_renderer_answer_that_is_not_an_rgba_png_is_refused() -> None:
    with pytest.raises(SvgRefused, match="no picture"):
        svg_raster._rgba_rows(b"GIF89a")
    with pytest.raises(SvgRefused, match="unexpected picture"):
        svg_raster._rgba_rows(_png(1, [(0, b"\x80")], colour=0))
