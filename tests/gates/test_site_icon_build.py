# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the site-icon crawl ASKS a site for: the half of the pack's quality that is not encoding.

`scripts/build_site_icons.py` is a maintainer's script: it runs by hand, talks to the open internet
and is never part of the product, so nothing else in the suite reads it. Much of what decides the
pack's quality is still pure logic, above all the ORDER the crawl tries addresses in, and none of
it needs the network to check.

So these hold the rules the crawl runs under (which address a site is asked for first, and what
a picture becomes on its way into the pack) and, at the bottom, the PACK ITSELF: every icon read
back off the disk and held to the bar it was built to (256 square, an alpha channel, no plate).
Everything that needs a site at the other end stays out.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    """The script, imported by path. It is not a package, so there is no name to import it by."""
    spec = importlib.util.spec_from_file_location(
        "sift_build_site_icons", ROOT / "scripts" / "build_site_icons.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build() -> ModuleType:
    return _script()


def _asked(build: ModuleType, host: str, page: str) -> list[str]:
    """The addresses the crawl would try, in order, for a site whose home page says this.

    Through the crawl's OWN two functions, never a copy of the rule: a test holding a second
    spelling of an ordering holds whatever it was spelled as on the day, which is not the thing
    anybody wants held.
    """
    return build.icon_candidates(host, build.declared_icons(page, f"https://{host}/"))


_SMALL = '<link rel="icon" sizes="32x32" href="/small.png">'
_BIG = '<link rel="icon" sizes="192x192" href="/big.png">'


def test_a_declared_icon_says_how_big_it_claims_to_be(build: ModuleType) -> None:
    """The size rides with the address, which is what the ordering rule below reads."""
    found = build.declared_icons(_SMALL + _BIG, "https://example.test/")

    assert found == [
        (192, "https://example.test/big.png"),
        (32, "https://example.test/small.png"),
    ]


def test_a_site_declaring_only_a_small_icon_is_asked_for_the_big_conventional_one_first(
    build: ModuleType,
) -> None:
    """A 32-pixel favicon never wins over a 180-pixel one."""
    asked = _asked(build, "example.test", _SMALL)

    assert asked[0] == "https://example.test/apple-touch-icon.png"
    assert "https://example.test/small.png" in asked, "it is still asked for, second"


def test_a_site_declaring_a_big_icon_keeps_the_one_it_chose(build: ModuleType) -> None:
    """Only the ORDER changed: what a site declares still wins when it is big enough."""
    asked = _asked(build, "example.test", _BIG)

    assert asked[0] == "https://example.test/big.png"


def test_a_site_declaring_nothing_is_asked_for_the_three_conventional_addresses(
    build: ModuleType,
) -> None:
    assert _asked(build, "example.test", "<html></html>") == [
        "https://example.test/apple-touch-icon.png",
        "https://example.test/favicon.png",
        "https://example.test/favicon.ico",
    ]


_MANIFEST_LINK = '<link rel="manifest" href="/data/manifest.json">'
_OG = '<meta property="og:image" content="https://cdn.example.test/card.png">'
_SVG = '<link rel="icon" href="/logo.svg">'


def test_a_web_manifest_is_read_for_the_icons_it_declares(build: ModuleType) -> None:
    """Where the two biggest sites in the pack keep their real picture. See `manifest_icons`."""
    found = build.manifest_icons(
        b'{"icons": [{"sizes": "192x192", "src": "/small.png"},'
        b' {"sizes": "512x512", "src": "https://cdn.example.test/big.png"}]}',
        "https://example.test/manifest.json",
    )

    assert found == [
        (512, "https://cdn.example.test/big.png"),
        (192, "https://example.test/small.png"),
    ]


def test_anything_that_is_not_a_manifest_reads_as_no_icons(build: ModuleType) -> None:
    """A site with no manifest answers its own HTML at every address, so the parse IS the check,
    exactly as the decode is the check for a picture."""
    assert build.manifest_icons(b"<!doctype html><html><head>", "https://example.test/") == []
    assert build.manifest_icons(b'{"name": "a site with no icons"}', "https://example.test/") == []


def test_the_manifest_the_page_names_is_asked_for_before_the_conventional_ones(
    build: ModuleType,
) -> None:
    """Instagram's is at `/data/manifest.json`, which neither conventional path would ever find."""
    asked = build.manifest_addresses(_MANIFEST_LINK, "https://example.test/")

    assert asked[0] == "https://example.test/data/manifest.json"
    assert "https://example.test/site.webmanifest" in asked


def test_a_declared_svg_is_kept_apart_from_the_rasters(build: ModuleType) -> None:
    """An SVG is weighed -1, below every raster, which is how `picture_sources` finds them to ask
    for FIRST. Declared by extension or by `type`, and a Safari mask icon is never one: it is a
    one-colour silhouette that draws as a black blob."""
    found = build.declared_icons(
        _SVG
        + _SMALL
        + '<link rel="icon" type="image/svg+xml" href="/mark">'
        + '<link rel="mask-icon" href="/pinned.svg" color="#000">',
        "https://example.test/",
    )

    assert (-1, "https://example.test/logo.svg") in found
    assert (-1, "https://example.test/mark") in found
    assert all("pinned" not in address for _, address in found)
    assert found[0] == (32, "https://example.test/small.png")


def test_a_site_is_asked_for_its_own_svg_before_its_rasters(build: ModuleType) -> None:
    """The order IS the judgement: the first source that makes a sharp icon wins, so a vector the
    site draws itself has to be asked for before any raster it serves."""
    one = build.Candidate(
        host="example.test", name="Quillhouse", source="sift", hosts={"example.test"}
    )
    one.slug = "quillhouse"
    sources = build.picture_sources(one, _SVG + _BIG, _NoElsewhere())

    first = next(sources)
    second = next(sources)

    assert first == ("site svg", "https://example.test/logo.svg")
    assert second[0] == "site", "and only then its rasters"


def test_a_studio_nobody_knows_as_a_site_is_drawn_with_its_stash_box_logo_first(
    build: ModuleType,
) -> None:
    """Two labels of one network share the network's favicon, so the site's own icon is the one
    source that CANNOT tell them apart. The box's curated logo can, and goes first, for a studio.
    A site Sift knows that is ALSO a studio on a box keeps its own mark first."""
    label = build.Candidate(
        host="example.test",
        name="Quillhouse",
        source="stashdb",
        hosts={"example.test"},
        studio_id="s-1",
        studio_image="https://box.example.test/images/s-1",
    )
    label.slug = "quillhouse"
    known_site = build.Candidate(
        host="example.test",
        name="Quillhouse",
        source="sift",
        hosts={"example.test"},
        studio_id="s-2",
        studio_image="https://box.example.test/images/s-2",
    )
    known_site.slug = "quillhouse"

    assert next(build.picture_sources(label, _BIG, _NoElsewhere()))[0] == "stash-box studio logo"
    assert next(build.picture_sources(known_site, _BIG, _NoElsewhere()))[0] == "site"


class _NoElsewhere:
    """Stands in for Commons and Simple Icons: this test is about ORDER and needs no network."""

    def commons(self, title: str) -> None:
        return None


def test_the_addresses_a_site_merely_answers_are_a_group_of_their_own(build: ModuleType) -> None:
    """THE IMGUR RULE: a conventional address can answer with a placeholder.

    `imgur.com/favicon.png` is a 128-pixel grey rectangle reading "The image you are requesting
    does not exist"; the real logo is at `/apple-touch-icon.png` at 100 pixels. Both decode, so
    "keep the biggest" would keep the placeholder, and the two are not judged together.
    """
    trusted, resort = build.icon_tiers("example.test", build.declared_icons(_BIG, "https://x/"))

    assert trusted[0].endswith("/big.png"), "what the site declares is trusted"
    assert "https://example.test/apple-touch-icon.png" in trusted, "and so is this one address"
    assert resort == [
        "https://example.test/favicon.png",
        "https://example.test/favicon.ico",
    ], "these two are not, because a soft 404 lives at them as readily as a logo"


def test_the_open_graph_picture_is_the_very_last_thing_asked_for(build: ModuleType) -> None:
    """And it is in the group where size is not consulted: it is not evidence of anything."""
    declared = build.declared_icons(_SMALL, "https://example.test/")
    og = build.og_image(_OG, "https://example.test/")
    trusted, resort = build.icon_tiers("example.test", declared, og=og)

    assert og == "https://cdn.example.test/card.png"
    assert og not in trusted
    assert resort[-1] == og


def test_an_open_graph_banner_is_refused_rather_than_padded_into_a_square(
    build: ModuleType,
) -> None:
    """A 1200x630 card pads into a 1200 square with the logo in a fifth of it: a correct picture
    of the wrong thing, and one nothing downstream could tell from a mark."""
    assert 630 * build.OG_SQUARENESS < 1200, "a card is refused"
    assert 300 * build.OG_SQUARENESS >= 300, "a square logo is kept"


# --- what a picture becomes: `scripts/site_icon_art.py` --------------------------------------------


@pytest.fixture(scope="module")
def art(build: ModuleType) -> ModuleType:
    """The picture half of the build. Imported through the build, which puts `scripts/` on the path."""
    return sys.modules["site_icon_art"]


def _canvas(size: int, colour: tuple[int, int, int, int]):  # type: ignore[no-untyped-def]
    import numpy as np

    return np.tile(np.array(colour, dtype=np.uint8), (size, size, 1))


def test_a_logo_on_a_white_plate_comes_out_on_nothing(art: ModuleType) -> None:
    """The Instagram, AllMyLinks and CamSoda fault: a light square behind the mark, drawn on the tile.

    A red disc on white. The white round it goes; the white INSIDE the mark (a hole no flood from
    the edge can reach) stays, because that is part of the mark.
    """
    import numpy as np

    # Big enough that the plate is found coarse first and finished at full size (`_coarse_flood`).
    picture = _canvas(700, (255, 255, 255, 255))
    yy, xx = np.mgrid[:700, :700]
    disc = (yy - 350) ** 2 + (xx - 350) ** 2 <= 250**2
    hole = (yy - 350) ** 2 + (xx - 350) ** 2 <= 60**2
    picture[disc] = (220, 30, 40, 255)
    picture[hole] = (255, 255, 255, 255)

    made = art.remove_plate(picture)

    assert made.removed and not made.tile
    assert made.rgba[0, 0, 3] == 0 and made.rgba[350, 99, 3] == 0, "the plate is gone, to the edge"
    assert made.rgba[350, 350, 3] == 255, "the white inside the mark is kept"
    assert made.rgba[350, 150, 3] == 255 and made.rgba[350, 150, 0] == 220


def test_a_plate_edge_is_anti_aliased_rather_than_cut(art: ModuleType) -> None:
    """A pixel that was half mark and half white becomes the mark's colour at half opacity, not a
    whitish pixel at full opacity, which is the fringe a hard cut leaves round every mark."""
    picture = _canvas(100, (255, 255, 255, 255))
    picture[30:70, 30:70] = (0, 0, 200, 255)
    picture[30:70, 29] = (128, 128, 228, 255)  # half blue, half white

    made = art.remove_plate(picture)
    edge = made.rgba[50, 29]

    assert 100 <= edge[3] <= 160, "about half opaque"
    assert edge[0] < 40 and edge[2] > 160, "and blue, not whitish"


def test_a_coloured_square_is_a_brand_tile_and_is_kept(art: ModuleType) -> None:
    """A saturated square is what some brands look like. Kept whole, and recorded as a tile."""
    picture = _canvas(100, (230, 20, 20, 255))
    picture[40:60, 40:60] = (255, 255, 255, 255)

    made = art.remove_plate(picture)

    assert made.tile and not made.removed
    assert made.rgba[0, 0, 3] == 255


def test_a_dark_square_is_kept_because_its_mark_is_nearly_always_white(art: ModuleType) -> None:
    """Taking the black away leaves a white mark, and the tile's ground on a light page is light."""
    picture = _canvas(100, (5, 5, 5, 255))
    picture[40:60, 40:60] = (255, 255, 255, 255)

    made = art.remove_plate(picture)

    assert made.tile and not made.removed


def test_every_icon_is_the_packs_size_with_the_mark_fitted_inside(art: ModuleType) -> None:
    """Exactly the pack's size, a wide mark fitted to its width, centred, the margin left clear."""
    import numpy as np

    wide = np.zeros((100, 400, 4), dtype=np.uint8)
    wide[:, :] = (20, 120, 220, 255)
    wide[40:60, 50:350] = (255, 255, 255, 255)  # a mark on it, or it is a blank tile

    made = art.make_icon(wide)

    assert made is not None
    assert made.rgba.shape == (art.SIZE, art.SIZE, 4)
    assert made.rgba[:, : art.MARGIN, 3].max() == 0, "the margin is clear"
    assert made.rgba[art.SIZE // 2, art.SIZE // 2, 3] == 255
    assert made.rgba[0, art.SIZE // 2, 3] == 0, "a wide mark leaves the top empty"


def test_a_mark_drawn_from_too_few_pixels_is_recorded_low(art: ModuleType) -> None:
    """A 32-pixel favicon can only make the square by being enlarged, and the manifest says so."""

    def mark(size: int):  # type: ignore[no-untyped-def]
        picture = _canvas(size, (20, 120, 220, 255))
        picture[0:2] = (0, 0, 0, 0)  # not a plate: a transparent row
        picture[size // 3 : size // 2] = (255, 255, 255, 255)  # and a mark on it
        return picture

    made = art.make_icon(mark(32))

    assert made is not None and made.quality == "low"
    assert art.make_icon(mark(art.LOW_BELOW)).quality == "high"


def test_a_tile_with_no_mark_on_it_is_not_a_logo(art: ModuleType) -> None:
    """A flat square of one colour identifies no site, so the build refuses it and asks the next
    source. A one-colour GLYPH is lines and holes, and is kept."""
    import numpy as np

    blank = _canvas(200, (250, 208, 44, 255))
    glyph = np.zeros((200, 200, 4), dtype=np.uint8)
    glyph[20:180, 20:40] = (143, 154, 168, 255)
    glyph[20:40, 20:180] = (143, 154, 168, 255)

    cut_out = _canvas(200, (250, 208, 44, 255))
    cut_out[60:140, 90:110] = (0, 0, 0, 0)  # a mark punched through the tile

    assert art.make_icon(blank) is None
    assert art.make_icon(glyph) is not None
    assert art.make_icon(cut_out) is not None, "a mark cut out of a flat tile is still a mark"


def test_a_site_a_box_names_by_a_link_kind_is_called_by_its_own_name(build: ModuleType) -> None:
    """A box files one site as "... profile" and "... video"; the pack calls it by its name and
    keeps the kind words as aliases, so a row under either still finds it."""
    host = next(iter(build.catalog.NAMES))
    one = build.Candidate(host=host, name="Quillhouse profile", source="pmvstash", hosts={host})

    merged = build.merge([[one]])[0]

    assert merged.name == build.catalog.NAMES[host]
    assert "Quillhouse profile" in merged.aliases


def test_a_resample_does_not_pull_the_colour_of_invisible_pixels_into_the_edge(
    art: ModuleType,
) -> None:
    """Straight-alpha resampling averages a transparent pixel's colour (often black) into the
    visible edge beside it, and every mark gets a dark fringe. Premultiplied, it does not."""
    import numpy as np

    picture = np.zeros((64, 64, 4), dtype=np.uint8)  # transparent BLACK
    picture[:, 32:] = (255, 255, 255, 255)
    inverse = np.full((64, 64, 4), 255, dtype=np.uint8)  # transparent WHITE
    inverse[..., 3] = 0
    inverse[:, 32:] = (0, 0, 0, 255)

    small = art.resample(picture, 16, 16)
    dark = art.resample(inverse, 16, 16)

    assert small[small[..., 3] > 0][:, :3].min() >= 250, "no grey fringe on a white mark"
    assert dark[dark[..., 3] > 0][:, :3].max() <= 5, "and no light one on a black mark"


def test_the_png_writer_and_reader_agree(art: ModuleType) -> None:
    import numpy as np

    rng = np.random.default_rng(7)
    picture = rng.integers(0, 256, size=(40, 30, 4), dtype=np.uint8)

    assert np.array_equal(art.decode_png(art.encode_png(picture)), picture)


# --- the pack itself, held to the bar it was built to ----------------------------------------------


def _manifest() -> dict[str, Any]:
    import json

    return dict(
        json.loads(
            (ROOT / "src" / "sift" / "kernel" / "site_icons" / "manifest.json").read_text("utf-8")
        )
    )


def _pack(art: ModuleType):  # type: ignore[no-untyped-def]
    icons = ROOT / "src" / "sift" / "kernel" / "site_icons" / "icons"
    for entry in _manifest()["icons"]:
        yield entry, art.decode_png((icons / f"{entry['slug']}.png").read_bytes())


def test_every_icon_in_the_pack_is_a_square_of_the_packs_size_with_an_alpha_channel(
    art: ModuleType,
) -> None:
    """The bar: 256 square, RGBA, and something in it actually transparent. A 16-pixel favicon, or a
    picture with no transparency anywhere, is a file this build never makes."""
    wrong = []
    for entry, pixels in _pack(art):
        if pixels.shape != (art.SIZE, art.SIZE, 4) or int(pixels[..., 3].min()) == 255:
            wrong.append(entry["slug"])

    assert wrong == []


def test_no_icon_in_the_pack_sits_on_an_opaque_plate_unless_it_is_a_tile(art: ModuleType) -> None:
    """The four corners of the MARK (its trimmed box, not the square, whose margin is always clear)
    are not all opaque, unless the manifest says the picture is a brand tile. A logo on a white
    square fails this; a round or irregular mark passes; a coloured app tile is declared."""
    wrong = []
    for entry, pixels in _pack(art):
        box = art.content_box(pixels)
        assert box is not None, f"{entry['slug']} is empty"
        top, left, bottom, right = box
        corners = [
            pixels[top, left, 3],
            pixels[top, right - 1, 3],
            pixels[bottom - 1, left, 3],
            pixels[bottom - 1, right - 1, 3],
        ]
        if min(int(one) for one in corners) >= 250 and not entry.get("tile"):
            wrong.append(entry["slug"])

    assert wrong == []


def test_every_entry_says_where_its_picture_came_from_and_how_good_it_is(art: ModuleType) -> None:
    """What the report reads. `low` is exactly "drawn from fewer pixels than the bar", so the list of
    soft icons can never disagree with the pictures."""
    for entry in _manifest()["icons"]:
        assert entry["size"] == art.SIZE, entry["slug"]
        assert entry["picture"], entry["slug"]
        assert isinstance(entry["plate_removed"], bool) and isinstance(entry["tile"], bool)
        assert entry["tone"] in ("light", "dark", "colour")
        low = entry["source_pixels"] < art.LOW_BELOW
        assert entry["quality"] == ("low" if low else "high"), entry["slug"]


# --- whose mark it is -------------------------------------------------------------------------------


def test_a_studios_page_on_a_site_it_sells_through_is_not_its_own_address(
    build: ModuleType,
) -> None:
    """A studio whose Home is its page ON a site it sells through would claim that site's host,
    and every link to the site would wear the studio's mark. A page whose host does not carry the
    studio's name is somebody else's site; the root, or a page on a host that names it, is its own."""
    sold = {
        "name": "Marrowvale",
        "urls": [{"url": "https://storefront.example/studio/marrowvale/"}],
    }
    named = {
        "name": "Harbour Films",
        "urls": [{"url": "https://www.harbourfilms.example/en/", "site": {"name": "Home"}}],
    }
    rooted = {"name": "Quillhouse", "urls": [{"url": "https://unrelated.example/"}]}

    assert build.own_address(sold) is None
    assert build.own_address(named) == "https://www.harbourfilms.example/en/"
    assert build.own_address(rooted) == "https://unrelated.example/"


def test_a_country_ending_does_not_become_the_slug(build: ModuleType) -> None:
    """Under a country's ending, `com` is part of the suffix: filed as `com`, one site would take
    the word, and every other site under that ending would have to wear its whole host."""
    assert build.slug_of("quillmoss.com.br", {}) == "quillmoss"
    assert build.slug_of("quillhouse.co.uk", {}) == "quillhouse"
    assert build.slug_of("quill.house.example", {}) == "house"


_HEADER = (
    '<header><a class="brand-logo" href="/"><svg viewBox="0 0 10 4"><path d="M0 0h10v4H0z"/></svg>'
    '</a><a href="https://ads.example/"><img src="/img/partner-logo.png" alt="Somebody Else"></a>'
    '<div id="logo"></div><div class="headerLogo"><button><svg width="16" height="16"></svg>'
    '</button></div><span class="logo-chevron"><svg width="16" height="16"><path d="M0 0h1v1z"/>'
    '</svg></span><footer><div class="footer-logo"><img src="/img/over18.png"></div></footer>'
)


def test_a_page_logo_is_the_one_the_page_marks_as_its_own(build: ModuleType) -> None:
    """Inline SVG inside the element marked logo is taken; an advertiser's picture with "logo" in
    its file name is not; a menu button's icon inside a `...Logo` block is not, nor any SVG drawn
    at icon size; an age badge in a footer logo block is not."""
    found = build.page_logos(_HEADER, "https://quillhouse.example/", ("Quillhouse",))

    assert len(found) == 1
    assert found[0].startswith("data:image/svg+xml;base64,")


def test_a_page_logo_named_for_the_site_is_found_by_its_alt_text(build: ModuleType) -> None:
    page = '<img src="/a/b.png" alt="Quillhouse logo"><img src="/c.png" alt="Quillhouse">'

    assert build.page_logos(page, "https://quillhouse.example/", ("Quillhouse",)) == [
        "https://quillhouse.example/a/b.png"
    ]


def test_a_site_whose_own_icons_are_not_its_mark_is_never_asked_for_them(build: ModuleType) -> None:
    """A site whose manifest serves its network's mark is asked for nothing it serves as an icon:
    not its head, its manifest, its favicon, nor the box's copy of it."""
    host = next(iter(build.catalog.NOT_ITS_MARK))
    one = build.Candidate(host=host, name="Quillhouse", source="stashdb", hosts={host})
    one.slug = "quillhouse"
    one.box_icons = ["https://box.example.test/images/site/1"]

    sources = [source for source, _ in build.picture_sources(one, _SVG + _BIG, _NoElsewhere())]

    assert not {"site", "site svg", "site fallback", "og image", "stash-box site icon"} & set(
        sources
    )


def test_a_picked_picture_is_asked_for_first(build: ModuleType) -> None:
    host = next(iter(build.catalog.PICKED))
    one = build.Candidate(host=host, name="Quillhouse", source="stashdb", hosts={host})
    one.slug = "quillhouse"

    assert next(build.picture_sources(one, _BIG, _NoElsewhere())) == (
        "picked",
        build.catalog.PICKED[host][0],
    )


def test_a_favicon_generators_big_files_are_asked_for_beside_its_small_ones(
    build: ModuleType,
) -> None:
    """A favicon generator's set declares a 16, a 32 and a 180; the 512 of the same mark sits
    beside them."""
    declared = [(32, "https://cdn.example.test/icons/favicon-32x32.png?v=4")]

    trusted, _ = build.icon_tiers("example.test", declared)

    assert trusted[0] == "https://cdn.example.test/icons/android-chrome-512x512.png"
    assert "https://cdn.example.test/icons/android-chrome-192x192.png" in trusted


def test_a_keyless_rerun_keeps_the_picture_a_box_gave(build: ModuleType) -> None:
    """The working cache of box pictures can be gone; the manifest itself says where a studio's
    logo came from, so a rerun of one entry does not trade it for the network's favicon."""
    held = {
        "icons": [
            {
                "slug": "quillhouse",
                "name": "Quillhouse",
                "hosts": ["quillhouse.example"],
                "picture": "stash-box studio logo",
                "picture_url": "https://box.example.test/images/q",
            }
        ]
    }

    (one,) = build.held_sites(held)

    assert one.studio_image == "https://box.example.test/images/q"


def test_nothing_in_the_pack_from_a_page_or_og_image_is_an_opaque_square(art: ModuleType) -> None:
    """A page's picture or `og:image` that fills its own box is a photograph or a banner, such as a
    strip of performers' photographs. The build refuses one; this holds the pack."""
    wrong = [
        entry["slug"]
        for entry, pixels in _pack(art)
        if entry["picture"] in ("page logo", "og image")
        and (entry["tile"] or art.fills_its_box(pixels))
    ]

    assert wrong == []


# --- a glyph is no logo -----------------------------------------------------------------------------


def test_no_icon_the_pack_ships_is_a_glyph() -> None:
    """A generic glyph never stands in for a photograph or for a link kind. It says "a website"
    where a letter says which site, so it is not a logo and never ships as one."""
    assert [e["slug"] for e in _manifest()["icons"] if e["picture"] == "glyph"] == []


def test_every_withheld_entry_says_why_and_ships_no_file(build: ModuleType) -> None:
    icons = ROOT / "src" / "sift" / "kernel" / "site_icons" / "icons"
    for entry in _manifest()["withheld"]:
        assert entry["reason"] in build.catalog.WITHHELD_WHY, entry["slug"]
        assert entry["why"] == build.catalog.WITHHELD_WHY[entry["reason"]], entry["slug"]
        assert not (icons / f"{entry['slug']}.png").exists(), entry["slug"]


def test_every_listed_photograph_and_mascot_the_pack_knows_is_withheld(build: ModuleType) -> None:
    """The catalog's lists are the decision; a host on one of them that the manifest ships a picture
    for is a face, or a figure, back in the pack."""
    listed = build.catalog.PHOTOGRAPHS | build.catalog.MASCOTS
    shipped = {host for e in _manifest()["icons"] for host in e["hosts"]}

    assert listed & shipped == set()


def test_a_withheld_site_is_never_fetched(build: ModuleType) -> None:
    """Refused before any request: no renderer and no network are handed over, and none is used."""
    mascot = next(iter(build.catalog.MASCOTS))
    photo = next(iter(build.catalog.PHOTOGRAPHS))
    by_second_host = build.Candidate(
        host="quillhouse.example", name="Quillhouse", source="stashdb", hosts={mascot}
    )
    kind = build.Candidate(host="", name="Studio", source="kind")
    face = build.Candidate(host=photo, name="Quillhouse", source="stashdb", hosts={photo})

    assert build.make_picture(by_second_host, None, None) == (None, "withheld: mascot")
    assert build.make_picture(kind, None, None) == (None, "withheld: link kind")
    assert build.make_picture(face, None, None) == (None, "withheld: photograph")


def test_a_keyless_rerun_keeps_the_withheld_sites(build: ModuleType) -> None:
    """A rerun without keys rebuilds from the manifest; one that read only `icons` would drop a
    withheld site from the manifest altogether."""
    held = {
        "withheld": [
            {"slug": "quillhouse", "name": "Quillhouse", "hosts": ["quillhouse.example"]},
            {"slug": "kind-studio", "name": "Studio", "hosts": []},
        ]
    }

    (one,) = build.held_sites(held)

    assert one.slug == "quillhouse" and one.hosts == {"quillhouse.example"}


def test_a_withheld_record_carries_what_a_rerun_needs(build: ModuleType) -> None:
    one = build.Candidate(
        host="quillhouse.example",
        name="Quillhouse",
        source="stashdb",
        hosts={"quillhouse.example"},
        aliases=("Marrowvale",),
        studio_id="s-1",
        scenes=77,
        slug="quillhouse",
    )

    entry = build.withheld_entry(one, "mascot")

    assert entry["hosts"] == ["quillhouse.example"] and entry["aliases"] == ["Marrowvale"]
    assert (entry["studio_id"], entry["files"], entry["reason"]) == ("s-1", 77, "mascot")
    assert entry["why"] == build.catalog.WITHHELD_WHY["mascot"]
