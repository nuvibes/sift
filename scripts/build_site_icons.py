# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the site-icon pack that ships with Sift: one logo per site, fetched once, by hand.

    # Everything, with the stash-boxes (asks for an admin password, never written or logged;
    # SIFT_ICON_PASSWORD is read instead for an unattended rerun):
    cmd.exe /c "cd /d <checkout> && set SIFT_DATA_DIR=<a library's data folder>&& \\
        .venv\\Scripts\\python.exe scripts\\build_site_icons.py"
    # Redo every icon already in the pack, no password:
    .venv\\Scripts\\python.exe scripts\\build_site_icons.py --without-stash-boxes
    # Redo one or a few:            ... build_site_icons.py --without-stash-boxes --only onlyfans,x
    # What each icon came from:     ... build_site_icons.py --report
    # Look at every icon:           ... build_site_icons.py --contact-sheet out.png --per-sheet 60

A maintainer's script: nothing in Sift runs it. Look at the contact sheet before committing the
pack: a wrong logo is worse than none, and nothing in the bytes can tell one from the right one.
The pack holds every site the downloader supports, the sites the stash-boxes list, their
top-level studios above a file-count floor, and the sites named by hand.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import site_icon_art as art  # noqa: E402
import site_icon_catalog as catalog  # noqa: E402
from site_icon_boxes import (  # noqa: E402
    REQUESTS_PER_MINUTE,
    SCENE_FLOOR,
    BoxPictures,
    Pace,
    _boxes,
    _library,
    box_sites,
    box_studios,
    label_images,
    own_address,
    studio_images,
)
from site_icon_pages import (  # noqa: E402
    declared_icons,
    manifest_addresses,
    manifest_icons,
    og_image,
    page_logos,
)
from site_icon_sites import (  # noqa: E402
    Candidate,
    by_name_only,
    held_sites,
    merge,
    named_sites,
    sift_supported,
    slug_of,
    withheld_entry,
    withheld_reason,
)
from site_icon_sources import (  # noqa: E402
    _LAST_RESORT,
    OG_SQUARENESS,
    Elsewhere,
    Made,
    icon_candidates,
    icon_tiers,
    make_picture,
    picture_sources,
)

from sift.kernel.site_icons import ICON_PIXELS, ICONS_DIR, MANIFEST  # noqa: E402

#: Read from this module by the tests, though only a sibling uses them.
__all__ = [
    "OG_SQUARENESS",
    "declared_icons",
    "icon_candidates",
    "icon_tiers",
    "manifest_addresses",
    "manifest_icons",
    "og_image",
    "own_address",
    "page_logos",
    "picture_sources",
]


def _renderer(node: str | None) -> art.Renderer:
    """Chromium, through the Playwright the client's tests already install."""
    playwright = REPO / "frontend" / "node_modules" / "playwright" / "index.mjs"
    if not playwright.is_file():
        raise SystemExit(f"no Playwright at {playwright}: run `npm ci` in frontend first")
    found = node or shutil.which("node") or shutil.which("node.exe")
    if not found:
        raise SystemExit("no node on the PATH: name one with --node")
    return art.Renderer(found, Path(__file__).resolve().parent / "site_icon_render.mjs", playwright)


def entry_for(one: Candidate, made: Made, fetched_at: str) -> dict[str, object]:
    """One manifest entry: who the site is, and everything about where its picture came from."""
    return {
        "slug": one.slug,
        "name": one.name,
        "hosts": sorted(one.hosts),
        # Only where there are any; `site_icons` reads a missing key as none.
        **({"aliases": sorted(one.aliases)} if one.aliases else {}),
        "source": one.source,
        "studio_id": one.studio_id,
        "files": one.scenes,
        "fetched_at": fetched_at,
        "picture": made.source,
        "picture_url": made.url,
        "size": art.SIZE,
        "source_pixels": made.icon.source_pixels,
        "quality": made.icon.quality,
        "plate_removed": made.icon.plate_removed,
        "tile": made.icon.tile,
        "tone": art.tone_of(made.icon.rgba),
    }


#: Refusals by a rule rather than by the network: a partial run drops the held entry.
_RULE_REFUSALS = ("a photograph rather than a mark", "a banner rather than a mark")


def _slugs(asked: str) -> set[str]:
    """The slugs `--only` names: comma or space separated, or `@file` for a long list."""
    if asked.startswith("@"):
        asked = Path(asked[1:]).read_text("utf-8")
    return {word.strip() for word in re.split(r"[,\s]+", asked) if word.strip()}


def report() -> int:
    """What every icon came from, from the manifest alone. No network, no keys."""
    held = json.loads(MANIFEST.read_text("utf-8"))
    icons = held.get("icons", [])
    print(f"{'slug':34} {'picture':22} {'px':>5} {'quality':7} plate tile tone")
    for one in icons:
        print(
            f"{one['slug'][:34]:34} {str(one.get('picture'))[:22]:22} "
            f"{one.get('source_pixels', 0):>5} {one.get('quality', '?'):7} "
            f"{'yes' if one.get('plate_removed') else 'no':5} "
            f"{'yes' if one.get('tile') else 'no':4} {one.get('tone', '?')}"
        )
    low = sum(1 for one in icons if one.get("quality") == "low")
    print(f"{len(icons)} icons, {low} low, {len(held.get('missing', []))} missing")
    for one in held.get("missing", []):
        print(f"  missing  {one['host']:32} {one['name'][:28]:28} {one['why']}")
    withheld = held.get("withheld", [])
    print(f"{len(withheld)} withheld (no picture ships)")
    for one in withheld:
        print(f"  withheld {one['slug']:32} {one['name'][:28]:28} {one['reason']}")
    return 0


#: Each mark's ground by its recorded `tone`, the same answer the client's tiles give.
SHEET_GROUNDS = {"light": "#1f2228", "dark": "#eceef1", "colour": "#2b2e35"}


def contact_sheet(
    out: Path, renderer: art.Renderer, *, only: set[str], per_sheet: int
) -> list[Path]:
    """Every icon at 96 pixels with its slug under it, twelve to a row, a `low` one in red."""
    held = json.loads(MANIFEST.read_text("utf-8"))
    icons = [one for one in held.get("icons", []) if not only or one["slug"] in only]
    icons.sort(key=lambda one: str(one["slug"]))
    cells = [
        {
            "png": base64.b64encode((ICONS_DIR / f"{one['slug']}.png").read_bytes()).decode(
                "ascii"
            ),
            "label": str(one["slug"]),
            "note": f"{one.get('source_pixels', 0)}px {str(one.get('picture', ''))[:13]}",
            "ground": SHEET_GROUNDS.get(str(one.get("tone")), SHEET_GROUNDS["colour"]),
            "low": one.get("quality") == "low",
        }
        for one in icons
    ]
    size = per_sheet if per_sheet > 0 else max(1, len(cells))
    pages = [cells[at : at + size] for at in range(0, len(cells), size)] or [[]]
    written: list[Path] = []
    for number, page in enumerate(pages, start=1):
        target = out if len(pages) == 1 else out.with_name(f"{out.stem}-{number:02d}{out.suffix}")
        target.write_bytes(renderer.sheet(page, columns=12, cell=96))
        written.append(target)
    return written


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Sift's site-icon pack.")
    parser.add_argument("--cache", type=Path, default=None, help="where the file counts are kept")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many sites")
    parser.add_argument(
        "--pictures",
        type=Path,
        default=None,
        help="where the stash-boxes' picture addresses are kept",
    )
    parser.add_argument("--node", default=None, help="the node to run the renderer with")
    parser.add_argument(
        "--only",
        default="",
        help="redo these slugs (comma separated, or @file) and keep the rest",
    )
    parser.add_argument(
        "--report", action="store_true", help="print what each icon came from, and stop"
    )
    parser.add_argument(
        "--keep-studios",
        action="store_true",
        help="ask the stash-boxes for sites and logos, but keep the pack's studios as they are",
    )
    parser.add_argument(
        "--without-stash-boxes",
        action="store_true",
        help="rebuild the sites already in the pack without asking the stash-boxes",
    )
    parser.add_argument(
        "--contact-sheet",
        type=Path,
        default=None,
        help="draw the pack (or the --only slugs) at 96 px with each slug under it, and stop",
    )
    parser.add_argument(
        "--per-sheet",
        type=int,
        default=0,
        help="with --contact-sheet: this many icons a sheet, numbered (0 = one sheet)",
    )
    return parser.parse_args()


def _draw_contact_sheet(args: argparse.Namespace) -> int:
    renderer = _renderer(args.node)
    try:
        for written in contact_sheet(
            args.contact_sheet, renderer, only=_slugs(args.only), per_sheet=args.per_sheet
        ):
            print(f"  wrote {written}")
    finally:
        renderer.close()
    return 0


def _ask_the_boxes(
    args: argparse.Namespace,
    data_dir: Path,
    layers: list[list[Candidate]],
    pictures: BoxPictures,
    held: dict[str, Any],
) -> None:
    if not data_dir.is_dir():
        raise SystemExit("point SIFT_DATA_DIR at a library whose settings hold the stash-box keys")
    cache_path = args.cache or data_dir.parent / "site-icon-file-counts.json"
    cache: dict[str, int] = {}
    if cache_path.is_file():
        cache = {
            key: int(value) for key, value in json.loads(cache_path.read_text("utf-8")).items()
        }
    password = os.environ.get("SIFT_ICON_PASSWORD") or getpass.getpass("admin password: ")
    connection = _library(data_dir)
    boxes = _boxes(connection, password)
    del password
    print(f"asking {len(boxes)} stash-boxes")
    for box in boxes:
        pace = Pace(REQUESTS_PER_MINUTE)
        layers.append(box_sites(box, pace))
        pictures.labels.update(label_images(box, pace, layers[1]))
        if args.keep_studios:
            # The pack's studios and their logos, without the per-network file counts.
            pictures.studios.update(studio_images(box, pace))
            pictures.learn(layers[-1])
        else:
            layers.append(box_studios(box, pace, cache, cache_path))
            pictures.learn(layers[-2] + layers[-1])
    pictures.save()
    if args.keep_studios:
        layers.append(held_sites(held))


def _wanted(args: argparse.Namespace, held: dict[str, Any]) -> list[Candidate]:
    layers: list[list[Candidate]] = [sift_supported(), named_sites()]
    print(f"  Sift's own catalog: {len(layers[0])} sites")
    print(f"  named by hand: {len(layers[1])} sites")
    data_dir = Path(os.environ.get("SIFT_DATA_DIR", ""))
    # Working state, beside the file-count cache and never in the repository.
    pictures = BoxPictures(
        args.pictures
        or (
            args.cache.parent
            if args.cache
            else data_dir.parent
            if data_dir.is_dir()
            else REPO.parent
        )
        / "site-icon-box-pictures.json"
    )
    if not args.without_stash_boxes:
        _ask_the_boxes(args, data_dir, layers, pictures, held)
    else:
        # A partial run rebuilds what the pack holds and never drops an entry.
        layers.append(held_sites(held))
        print(f"  held in the pack: {len(layers[-1])} sites (no stash-box asked)")
    wanted = merge(layers)
    for one in wanted:
        pictures.apply(one)
    return wanted


def _name_the_slugs(wanted: list[Candidate], held: dict[str, Any]) -> None:
    # Slugs are stable, so a rebuild does not rename files for no reason.
    held_slug = {
        str(host): str(entry["slug"])
        for entry in [*held.get("icons", []), *held.get("withheld", [])]
        for host in entry.get("hosts") or []
    }
    taken: dict[str, str] = {slug: slug for slug, *_ in catalog.LINK_KINDS}
    for one in wanted:
        # A join keeps the name of the site it was joined into.
        kept = held_slug.get(one.host) or next(
            (held_slug[h] for h in sorted(one.hosts) if h in held_slug), None
        )
        if kept and kept not in taken:
            one.slug = kept
            taken[kept] = one.host
    for one in wanted:
        if not one.slug:
            one.slug = slug_of(one.host, taken)
            taken[one.slug] = one.host


@dataclass
class _SetAside:
    """What a run sets aside before it fetches anything, and what it still makes."""

    withheld: list[dict[str, object]]
    kept_back: set[str]
    kept_back_hosts: set[str]
    still_made: set[str]


def _set_aside(wanted: list[Candidate]) -> tuple[list[Candidate], _SetAside]:
    # Over the whole list, not the `--only` part, so a site is never withheld and shipped.
    aside = [(one, reason) for one in wanted if (reason := withheld_reason(one)) is not None]
    withheld = sorted(
        (withheld_entry(one, reason) for one, reason in aside), key=lambda one: str(one["slug"])
    )
    kept_back = {one.slug for one, _ in aside}
    kept_back_hosts = {host for one, _ in aside for host in one.hosts}
    wanted = [one for one in wanted if one.slug not in kept_back]
    # Read before `--only` narrows the list: a slug no layer makes any more has left the pack.
    still_made = {one.slug for one in wanted}
    print(f"{len(withheld)} withheld: no picture ships for them")
    return wanted, _SetAside(withheld, kept_back, kept_back_hosts, still_made)


def _fetch_all(
    wanted: list[Candidate], node: str | None, fetched_at: str
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    missing: list[dict[str, str]] = []
    work: queue.Queue[Candidate] = queue.Queue()
    for one in wanted:
        work.put(one)
    lock = threading.Lock()
    renderer = _renderer(node)
    elsewhere = Elsewhere()

    def fetcher() -> None:
        while True:
            try:
                one = work.get_nowait()
            except queue.Empty:
                return
            try:
                made, why = make_picture(one, renderer, elsewhere)
            except Exception as failure:
                made, why = None, f"{type(failure).__name__}: {str(failure)[:60]}"
            _write_icon(one, made)
            with lock:
                if made is not None:
                    entries.append(entry_for(one, made, fetched_at))
                else:
                    missing.append({"host": one.host or one.slug, "name": one.name, "why": why})
                done = len(entries) + len(missing)
                if done % 50 == 0:
                    print(f"  {done} of {len(wanted)}")

    try:
        threads = [threading.Thread(target=fetcher) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        renderer.close()
    return entries, missing


def _write_icon(one: Candidate, made: Made | None) -> None:
    if made is not None:
        # Moved into place, so a build that dies mid-write never leaves a truncated PNG.
        destination = ICONS_DIR / f"{one.slug}.png"
        working = destination.with_name(f"{one.slug}.new.png")
        working.write_bytes(art.encode_png(made.icon.rgba))
        os.replace(working, destination)


def _carry_forward(
    entries: list[dict[str, object]],
    missing: list[dict[str, str]],
    held: dict[str, Any],
    aside: _SetAside,
) -> list[dict[str, str]]:
    """A partial run: what it touched wins, and everything it did not consider is kept."""
    # A site that timed out today has not stopped having yesterday's logo...
    seen = {str(one["slug"]) for one in entries}
    # ...unless a rule refused the very kind of source that logo came from (`_LAST_RESORT`).
    refused = {m["host"] for m in missing if any(rule in m["why"] for rule in _RULE_REFUSALS)}
    entries += [
        one
        for one in held.get("icons", [])
        if isinstance(one, dict)
        and str(one.get("slug")) not in seen
        # An entry withheld now leaves the pack on the next run of any size.
        and str(one.get("slug")) not in aside.kept_back
        and str(one.get("slug")) in aside.still_made
        and not (
            one.get("picture") in _LAST_RESORT
            and refused & {str(host) for host in one.get("hosts") or []}
        )
    ]
    # A picture no entry names is one nothing can serve.
    surviving = {str(one["slug"]) for one in entries}
    for one in held.get("icons", []):
        if isinstance(one, dict) and str(one.get("slug")) not in surviving:
            (ICONS_DIR / f"{one['slug']}.png").unlink(missing_ok=True)
    answered = {host for one in entries for host in one.get("hosts", [])}  # type: ignore[attr-defined]
    # Said once: this run's reason replaces the held one for the same host.
    missing = [m for m in missing if m["host"] not in answered]
    fresh = {m["host"] for m in missing}
    missing += [
        one
        for one in held.get("missing", [])
        if isinstance(one, dict)
        and str(one.get("host")) not in answered
        and str(one.get("host")) not in fresh
        and str(one.get("host")) not in aside.kept_back_hosts
    ]
    return missing


def _write_manifest(
    entries: list[dict[str, object]],
    missing: list[dict[str, str]],
    withheld: list[dict[str, object]],
    fetched_at: str,
) -> None:
    MANIFEST.write_text(
        json.dumps(
            {
                "built_at": fetched_at,
                "licence": (
                    "Each picture is a site's own mark, used only to identify the site it belongs "
                    "to. Where a site's own icon was too small to draw well, the mark was taken "
                    "from the site's stash-box entry, from Wikimedia Commons, or from Simple Icons "
                    "(CC0); `picture` and `picture_url` on each entry say which. A site that would "
                    "rather Sift did not ship its mark should say so and it will be taken out."
                ),
                # A SIZE, not a cap: every icon is exactly this square.
                "pixels": ICON_PIXELS,
                "file_floor": SCENE_FLOOR,
                "icons": entries,
                "missing": missing,
                # Known, and deliberately pictureless: see `catalog.WITHHELD_WHY`.
                "withheld": withheld,
            },
            # Two, which is what the committed manifest is in, so a one-icon change reads as one.
            indent=2,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
        # `write_text` translates on Windows, and the repository is LF.
        newline="\n",
    )


def main() -> int:
    args = _arguments()
    if args.report:
        return report()
    if args.contact_sheet is not None:
        return _draw_contact_sheet(args)
    held = json.loads(MANIFEST.read_text("utf-8")) if MANIFEST.is_file() else {}
    wanted = _wanted(args, held)
    _name_the_slugs(wanted, held)
    wanted += by_name_only()
    wanted, aside = _set_aside(wanted)
    only = _slugs(args.only)
    if only:
        wanted = [one for one in wanted if one.slug in only]
    if args.limit:
        wanted = wanted[: args.limit]
    print(f"{len(wanted)} sites to make a picture for")
    fetched_at = time.strftime("%Y-%m-%d", time.gmtime())
    entries, missing = _fetch_all(wanted, args.node, fetched_at)
    if only or args.limit:
        missing = _carry_forward(entries, missing, held, aside)
    else:
        # A whole build owns the directory: a picture no entry names cannot be served.
        named = {str(one["slug"]) for one in entries}
        for stale in ICONS_DIR.glob("*.png"):
            if stale.stem not in named:
                stale.unlink()
    entries.sort(key=lambda one: str(one["slug"]))
    missing.sort(key=lambda one: one["host"])
    _write_manifest(entries, missing, aside.withheld, fetched_at)
    weight = sum((ICONS_DIR / f"{one['slug']}.png").stat().st_size for one in entries)
    print(f"{len(entries)} icons, {len(missing)} missing, {weight / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
