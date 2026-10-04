# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the site-icon pack that ships with Sift: one logo per site, fetched once, by hand.

## MAINTAINER'S NOTE: read this first

    # Everything, with the stash-boxes (asks for an admin password; see "Running it" below):
    cmd.exe /c "cd /d <checkout> && set SIFT_DATA_DIR=<a library's data folder>&& \\
        .venv\\Scripts\\python.exe scripts\\build_site_icons.py"
    # Redo every icon already in the pack, no password (reads the held manifest and the
    # stash-box picture cache a keyed run left beside the data directory):
    .venv\\Scripts\\python.exe scripts\\build_site_icons.py --without-stash-boxes
    # Redo one or a few:            ... build_site_icons.py --without-stash-boxes --only onlyfans,x
    # What each icon came from:     ... build_site_icons.py --report

- **Every icon is 256x256 RGBA**, a transparent square with the mark fitted inside it. The picture
  rules live in `site_icon_art.py`; what is known by hand (joins, aliases, Commons titles, Simple
  Icons slugs, the entries withheld from the pack) lives in `site_icon_catalog.py`; SVG and every raster
  format are read by Chromium through `site_icon_render.mjs` (Playwright from `frontend/`).
- **Sources, best first, per site** (`picture_sources`): a picture chosen by hand in
  `catalog.PICKED`; the stash-box's own logo for a STUDIO; the site's own SVG icon; the brand's
  vector on Wikimedia Commons; the site's biggest raster icon; the stash-box's icon for the site;
  Simple Icons' glyph in the brand colour; the logo in the site's own page header; and last the
  site's favicon or `og:image`. The first that makes a `high` icon wins; otherwise the best `low`
  one is kept and the manifest says so. A site in `catalog.NOT_ITS_MARK` is never drawn with its
  own icons (they are another brand's mark, or a photograph).
- **Some entries ship NO picture** (`withheld` in the manifest, each with its reason): a site
  whose own picture is a photograph of a real person (`catalog.PHOTOGRAPHS`), a site whose mark is
  a drawn figure (`catalog.MASCOTS`), and the link kinds (`catalog.LINK_KINDS`). Nothing is fetched
  for them; their names and hosts are kept, so the lookup still knows which site a row or a link
  is, and a Site tile draws its first letter.
- **LOOK at every icon** with `--contact-sheet out.png --per-sheet 60`: every icon at 96 px with
  its slug, its source size and where it came from under it, a `low` one in red.
- **LOOK at the result** before committing it. A wrong logo is worse than none, and nothing in the
  bytes can tell a site's logo from its network's or from a placeholder. Rasterise a sheet of
  them and read it.

This is a MAINTAINER'S script. It runs on the machine a release is cut from, it talks to the open
internet, and what it writes is committed: `src/sift/kernel/site_icons/icons/*.png` and the
manifest beside them. Nothing in Sift ever runs this, and no installed copy ever asks a site for
anything (see that package's own note for why that is the shape of the feature rather than a
performance choice).

## What goes in the pack, in three layers

1. **Every site Sift supports out of the box.** The download catalog
   (`slices/download/sources/sites/catalog.py`) is the one list of those, so it is read rather than
   copied: a site added there next year arrives here on the next build with nothing to remember.
2. **The stash-boxes' own site lists**: `querySites`, which is where a box files "this URL is an
   OnlyFans page, this one is an IAFD page". A few hundred entries across the three, each already
   curated by the people who run those services, each one a site a link in a library can point at.
   No floor: the list is short and every entry on it earns its place by existing.
3. **The stash-boxes' studios, above a popularity floor.** This is the long tail (the production
   companies and networks whose names end up on files), and it is the layer that needs a floor,
   because StashDB alone has 14,609 studios and most of them have a handful of files anywhere.

## Three things about the live services

**A studio has no scene count.** The `Studio` type carries id, name, aliases, urls, parent,
sub_studios, images, deleted, is_favorite, created, updated and performers, and no count of files at
all; `StudioSortEnum` offers NAME, CREATED_AT and UPDATED_AT and nothing about popularity. So the
count is asked for per studio (`queryScenes(parentStudio: <id>)` returns it for a network and
everything under it), and that is the expensive half of this script.

**Only TOP-LEVEL studios are asked about.** `queryScenes(parentStudio:)` counts a studio's children
with it, so a network is one question rather than four hundred, and a child's files are counted
under the network whose icon they would be drawn with anyway. StashDB: 7,187 top-level studios
against 14,609 in total. FansDB: 83 against 394,727, because its top level is the fan SITES
themselves, which is exactly the list wanted here, and everything below it is an individual
creator.

**A box whose studios are PEOPLE is skipped for layer 3.** On PMVStash the studios are the creators
(`known_boxes.sites_are_for` is the one place that knows this), and a creator is not a site: an
icon pack built from their personal pages would be a pack of somebody's link-in-bio. Its
`querySites` list is still read, because that layer really is sites.

## Where each site's own address comes from

A studio's `urls` carry a `site` each: Twitter, IAFD, ThePornDB, Home. The one that is the site
ITSELF is `Home` where there is one, and otherwise the single URL a studio with exactly one has
(which is how FansDB spells it: one URL per network, filed under the network's own name). A studio
with several URLs and no `Home` has no address of its own that can be told from the others, and is
skipped. Anything on `web.archive.org` is skipped too: it is a site that no longer exists, kept
for the record, and the favicon on that page belongs to the archive rather than to the site.

## Running it

    cmd.exe /c "cd /d <checkout> && set SIFT_DATA_DIR=<a library's data folder>&& \\
        .venv\\Scripts\\python.exe scripts\\build_site_icons.py"

`SIFT_DATA_DIR` names a library whose settings hold the stash-box keys. The keys are sealed under
an admin's master key, so that admin's password is asked for at the terminal and is never written
down, never passed as an argument and never logged; neither is any key it opens.
`SIFT_ICON_PASSWORD` is read instead when it is there, for an unattended re-run, and that is the
only concession.

It is resumable. The scene counts are the slow part (one request per top-level studio, paced at
the rate Sift's own client keeps), so they are written to a cache file as they arrive and a second
run costs nothing for what it already has. `--cache` names the file; the default sits beside the
data directory rather than in the repository, because it is working state and not a product.
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
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]


sys.path.insert(0, str(REPO / "src"))


sys.path.insert(0, str(Path(__file__).resolve().parent))


import site_icon_art as art  # noqa: E402
import site_icon_catalog as catalog  # noqa: E402
from site_icon_pages import (  # noqa: E402
    USER_AGENT,
    _get,
    declared_icons,
    host_of,
    manifest_addresses,
    manifest_icons,
    og_image,
    page_logos,
)

from sift.kernel.db import DATABASE_FILENAME  # noqa: E402
from sift.kernel.secrets import open_secret  # noqa: E402
from sift.kernel.site_icons import ICON_PIXELS, ICONS_DIR, MANIFEST  # noqa: E402
from sift.slices.auth.crypto import WrappedKey, unwrap_master_key  # noqa: E402
from sift.slices.download.sources.sites.catalog import SITES  # noqa: E402
from sift.slices.stash_boxes.known_boxes import (  # noqa: E402
    SITES_ARE_SITES,
    sites_are_for,
)

#: How many files a top-level studio's network must have before its site earns an icon.
#:
#: Fifty, and the number is a decision about the SIZE OF THE PACK rather than a judgement about any
#: site. Against StashDB and FansDB, over about three thousand sites that have both an address of
#: their own and a count: a floor of 50 keeps 954 of them, 100 keeps 730, 200 keeps 531
#: and 1,000 keeps 197. Fifty is the generous end of "the most popular": with the two layers above
#: it the pack lands at roughly eleven hundred pictures and a handful of megabytes in a repository
#: people clone, and a site with fifty files on a public index is a site somebody has a folder of.
#: Below it the tail is thousands of sites that three files in the world are filed under.
#:
#: It is a floor on the NETWORK, not on the studio: `queryScenes(parentStudio:)` counts everything
#: underneath, so a network of forty small imprints is weighed as the network somebody recognises.
SCENE_FLOOR = 50


#: Requests a minute to one stash-box. What the adapter paces itself at, and for its reason: the
#: account being throttled is the maintainer's own, and no retry recovers it.
REQUESTS_PER_MINUTE = 240


#: Pictures chosen by hand (Reddit's undeclared 192-pixel favicon among them) are rows of
#: `catalog.PICKED`. A hard-coded address rots silently, which is why every row there says what it
#: is and was looked at; they exist because some brands' own icons are another brand's mark, a
#: photograph or a 16-pixel favicon while a better picture of the same mark exists where no rule can
#: reach it (a flag on Commons, a header behind a challenge).

#: How many addresses one site is fetched from before the best of them is kept.
#:
#: Six. The run does not stop at the FIRST one that decodes, but it stops early on anything that
#: reaches `ICON_PIXELS`, which is the common case, so the ordinary site still costs one or two
#: requests.
HOW_MANY_TRIES = 6


#: How far from square a picture may be and still be somebody's mark.
#:
#: Only `og:image` is held to it, and it is why that source is last and guarded rather than simply
#: last. An Open Graph picture is whatever a site wants shown when a link to it is pasted, and for
#: most sites that is a 1200x630 BANNER: Discord and PMVHaven both serve one. `to_png` pads to the
#: longer side, so a banner becomes a 1200-square with the logo occupying a fifth of it, scaled down
#: to 128: a correct picture of the wrong thing, and one nothing downstream can tell from a real
#: mark. A quarter is generous enough for a logo with a wordmark beside it and refuses every banner
#: seen.
OG_SQUARENESS = 1.25


#: What is read from a favicon before it is refused. A favicon is a few kilobytes; a megabyte of it
#: is a page pretending to be one.
MAX_ICON_BYTES = 1024 * 1024


#: A site that answers a missing favicon with its home page instead of a 404. Caught by trying to
#: DECODE what came back rather than by trusting the content type, the same rule the cover store
#: follows, and for the same reason: a zero exit is not proof there is a picture.
_NOT_A_PICTURE = "could not be read as a picture"


#: Hosts that are never a site's own address, however a studio files them.
_NOT_A_SITE = ("web.archive.org", "archive.org", "localhost")


# Only the fields every stash-box has. A studio's own address, and enough to name it.
_STUDIO_PAGE = """
query($page: Int!) {
  queryStudios(input: {page: $page, per_page: 100, has_parent: false, sort: NAME, direction: ASC}) {
    count
    studios { id name urls { url site { name } } images { url width height } }
  }
}
"""


_SCENE_COUNT = """
query($studio: String!) {
  queryScenes(input: {page: 1, per_page: 1, parentStudio: $studio}) { count }
}
"""


_SITES = "{ querySites { sites { name url icon } } }"


_STUDIO_BY_NAME = """
query($name: String!) {
  queryStudios(input: {names: $name, page: 1, per_page: 100}) {
    studios { id name urls { url } images { url } }
  }
}
"""


@dataclass(frozen=True, slots=True)
class Box:
    """One configured stash-box, with its key open in memory and nowhere else."""

    slug: str
    name: str
    endpoint: str
    api_key: str
    studios_are_sites: bool


@dataclass
class Candidate:
    """A site on its way into the pack: where it lives, what it is called, who named it."""

    host: str
    name: str
    #: `sift` for a site the downloader supports, otherwise the box's word.
    source: str
    #: The studio this came from, where it came from a studio at all.
    studio_id: str | None = None
    #: How many files the network has, where that was measured.
    scenes: int | None = None
    hosts: set[str] = field(default_factory=set)
    #: What this site has ALSO been called. Kept in the manifest so a library row still wearing an
    #: old word draws the right picture (see `site_icons._by_name`).
    aliases: tuple[str, ...] = ()
    #: What its picture is filed under. Decided once everything is merged, so that two sites
    #: wanting one word can be told apart by the whole set rather than by whoever arrived first.
    slug: str = ""
    #: The icon each stash-box files this SITE under (`querySites ... icon`), best-known first.
    box_icons: list[str] = field(default_factory=list)
    #: The stash-box's own logo for this STUDIO, where it came from a studio, curated by the
    #: box, and the one source that tells a label from the network whose favicon it shares.
    studio_image: str | None = None


#: SITES THE THREE LAYERS CANNOT REACH, NAMED BY HAND.
#:
#: A fourth layer, and every entry is here because one of the three above it asks a question that
#: this site is not the answer to, not because somebody liked it. Each carries its reason.
#:
#: **Why it is not a widening of layer three.** Layer three asks each stash-box for its TOP-LEVEL
#: studios (`has_parent: false`) and counts a network's files with its children's, which is right
#: for the question "which networks are popular" and is exactly wrong for the question a library
#: asks. `kernel/access/sites.py` states the rule in its first paragraph: a file is filed under the
#: LABEL that released it, never under the network. So the names a library's Site rows actually
#: wear are the labels (Blacked, Tushy, Slayed), and the pack was fetching their parent.
#:
#: THE DURABLE FIX IS TO ASK FOR THE CHILDREN of every network that clears the floor: it needs a
#: key for every box and a full crawl. When it lands, the Vixen rows below are subsumed by it and
#: should go; Imgur is not, and stays.
#:
#: The `aliases` are one site under the name it uses now, with its former names kept so nothing that
#: found it stops finding it. Each one as `(host, name, other names it answers to)`. Plain data
#: rather than `Candidate` values, because a `Candidate` is mutable and `merge` writes to the ones
#: it is handed: a module-level constant that a merge edits is a constant for one run only.
PACK_EXTRAS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # Sift has no downloader for Imgur and no stash-box files anything under it, so neither layer
    # one nor layer three reaches it, and a library gets a Site called Imgur the moment anybody
    # saves a picture from one, so it is named here.
    ("imgur.com", "Imgur", ()),
    # Vixen Media Group's labels. The NETWORK is in the pack already (4,534 files on StashDB, well
    # over the floor) and not one of its labels is, for the reason written above.
    ("vixen.com", "Vixen", ()),
    ("tushy.com", "Tushy", ()),
    ("blacked.com", "Blacked", ()),
    ("deeper.com", "Deeper", ()),
    ("slayed.com", "Slayed", ()),
    ("milfy.com", "Milfy", ()),
    ("blackedraw.com", "BlackedRaw", ("Blacked Raw",)),
    ("tushyraw.com", "TushyRaw", ("Tushy Raw",)),
)


def named_sites() -> list[Candidate]:
    """Layer four: the sites above and `catalog.NAMED_SITES`, as fresh candidates."""
    return [
        Candidate(host=host, name=name, source="named", hosts={host}, aliases=aliases)
        for host, name, aliases in (*PACK_EXTRAS, *catalog.NAMED_SITES)
    ]


def by_name_only() -> list[Candidate]:
    """The entries with no address at all: every link kind (withheld).

    Twitter was one of these once, a name-only entry drawing the old bird. It is X's old name for
    the same site, so it is an alias of the `x` entry now (`catalog.ALIASES`): a Site row still
    called Twitter draws X's mark, one site with two addresses and one picture.
    """
    return [
        Candidate(host="", name=name, source="kind", slug=slug, aliases=also)
        for slug, name, also in catalog.LINK_KINDS
    ]


#: What a site in the pack has ALSO been called, by the slug its picture is filed under.
#:
#: A SPELLING or an OLD NAME, never a second site. `BlackedRaw` and `Blacked Raw` are one studio
#: written two ways, and `Twitter` is what X used to be called, so a library row under either word
#: draws the one mark.
#:
#: The table is `catalog.ALIASES`, keyed by HOST rather than by slug: a slug is
#: decided by the build, a host is a fact about the site, so a table keyed by slug could be pointed
#: at the wrong entry by nothing more than a change in the order sites arrive.


# --- the library, and the keys in it -----------------------------------------------------------


def _library(data_dir: Path) -> sqlite3.Connection:
    """The library's database, opened READ ONLY.

    Read only because this script has no business writing to somebody's library, and because it is
    pointed at a copy of a live one: a URI connection in `mode=ro` cannot create a journal, cannot
    upgrade a schema, and fails loudly rather than quietly if anything here ever tries to.
    """
    path = data_dir / DATABASE_FILENAME
    if not path.is_file():
        raise SystemExit(f"no library at {path}: point SIFT_DATA_DIR at one")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _boxes(connection: sqlite3.Connection, password: str) -> list[Box]:
    """Every switched-on stash-box, with its key unsealed.

    The key never leaves this function's caller: it is held on a `Box`, sent as a header, and
    nothing prints, logs or writes one. The failure a wrong password gives is the only thing said
    about them out loud.
    """
    row = connection.execute(
        "SELECT mk_wrapped, mk_nonce, mk_kdf_salt FROM users"
        " WHERE role = 'admin' AND disabled = 0 ORDER BY created_at, id"
    ).fetchone()
    if row is None or row["mk_wrapped"] is None:
        raise SystemExit("that library has no admin with a master key")
    master = unwrap_master_key(
        WrappedKey(row["mk_wrapped"], row["mk_nonce"], row["mk_kdf_salt"]), password
    )
    if master is None:
        raise SystemExit("that password does not open this library's key")

    found: list[Box] = []
    for box in connection.execute(
        "SELECT name, endpoint, secret_id, slug FROM stash_boxes WHERE enabled = 1"
    ):
        if box["secret_id"] is None:
            print(f"  {box['name']}: no key saved, skipped")
            continue
        sealed = connection.execute(
            "SELECT ciphertext, nonce FROM secrets WHERE id = ?", (box["secret_id"],)
        ).fetchone()
        opened = (
            None if sealed is None else open_secret(master, sealed["ciphertext"], sealed["nonce"])
        )
        if opened is None:
            print(f"  {box['name']}: its key could not be opened, skipped")
            continue
        found.append(
            Box(
                slug=str(box["slug"] or box["endpoint"]),
                name=str(box["name"]),
                endpoint=str(box["endpoint"]),
                api_key=opened.decode("utf-8"),
                studios_are_sites=sites_are_for(str(box["endpoint"])) == SITES_ARE_SITES,
            )
        )
    return found


# --- asking a stash-box ------------------------------------------------------------------------


class Pace:
    """One permit every quarter of a second, shared by every thread asking one box."""

    def __init__(self, per_minute: int) -> None:
        self._gap = 60.0 / per_minute
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            when = max(now, self._next)
            self._next = when + self._gap
        time.sleep(max(0.0, when - now))


def ask(box: Box, query: str, variables: dict[str, object] | None = None) -> dict[str, object]:
    """One GraphQL question. Raises on anything that is not an answer.

    **A 200 is not a success**, which is the adapter's first rule and holds here for the same
    reason: a stash-box answers `200 OK` with an `errors` array, so a dead key comes back looking
    exactly like a service with nothing in it.
    """
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 (a configured stash-box endpoint)
        box.endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "ApiKey": box.api_key,
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310
        payload = json.loads(answer.read())
    if payload.get("errors"):
        # The message is the box's, and it never carries a key: what it names is the query.
        raise RuntimeError(f"{box.name} refused the question: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{box.name} answered with no data")
    return data


def top_level_studios(box: Box, pace: Pace) -> list[dict[str, object]]:
    """Every studio with no parent, in pages of a hundred."""
    found: list[dict[str, object]] = []
    page = 1
    while True:
        pace.wait()
        block = cast(dict[str, Any], ask(box, _STUDIO_PAGE, {"page": page})["queryStudios"])
        studios = cast(list[dict[str, Any]], block["studios"])
        found.extend(studios)
        if not studios or len(found) >= int(block["count"]):
            return found
        page += 1


def own_address(studio: dict[str, object]) -> str | None:
    """A studio's own home page, or None when its URLs do not say which one that is.

    **And None when that "home" is a page on SOMEBODY ELSE'S site.** A box files a studio's Home
    as whatever the studio has, and for a studio with no site of its own that is its page on the
    site it sells through (`vrporn.com/studio/dreamcam/`, `fr.pornhub.com/model/...`,
    `store.evilangelvideo.com/...`). Taken as the studio's own address, the studio would CLAIM that
    site's host, so every link to VRPorn, POVR, R18, HotMovies and PissVids would be drawn with one
    studio's logo and named after it.
    `is_own_address` is the rule.
    """
    urls = studio.get("urls")
    if not isinstance(urls, list):
        return None
    home = [
        str(one["url"])
        for one in urls
        if isinstance(one, dict) and (one.get("site") or {}).get("name") == "Home"
    ]
    found = home[0] if home else None
    if found is None and len(urls) == 1 and isinstance(urls[0], dict):
        found = str(urls[0]["url"])
    if found is None or not is_own_address(found, str(studio.get("name") or "")):
        return None
    return found


def is_own_address(address: str, name: str) -> bool:
    """Whether an address can be a site's OWN, rather than its page on another site.

    The site's root is always its own. A deeper page is its own only when the host carries a word
    of the name: `dorcel.com/en/` and `bang.com/originals` are Dorcel's and Bang's; `vrporn.com/
    studio/dreamcam/` is a page ON VRPorn. A word is three letters or more, compared letters only,
    so a studio whose host is its INITIALS (`mvg.jp/top/`) is refused, which costs that studio
    its logo, and is the safe direction: no logo rather than another site's.
    """
    parts = urllib.parse.urlsplit(address.strip() if "//" in address else f"//{address.strip()}")
    if parts.path in ("", "/"):
        return True
    host = re.sub(r"[^a-z0-9]+", "", (parts.hostname or "").lower())
    words = [word for word in re.split(r"[^a-z0-9]+", name.lower()) if len(word) >= 3]
    return any(word in host for word in words)


def scene_counts(
    box: Box, studio_ids: list[str], cache: dict[str, int], cache_path: Path, pace: Pace
) -> None:
    """Fill in how many files each of these networks has. Cached, because it is one request each.

    Six threads against one paced permit queue: the pace is what bounds the load on the service, and
    the threads are only there so that a 400 ms round trip does not become the bound instead.
    """
    todo: queue.Queue[str] = queue.Queue()
    for studio_id in studio_ids:
        if f"{box.slug}:{studio_id}" not in cache:
            todo.put(studio_id)
    total = todo.qsize()
    if not total:
        return
    print(f"  {box.name}: counting files for {total} networks")
    lock = threading.Lock()
    done = [0]

    def worker() -> None:
        while True:
            try:
                studio_id = todo.get_nowait()
            except queue.Empty:
                return
            pace.wait()
            try:
                block = cast(
                    dict[str, Any], ask(box, _SCENE_COUNT, {"studio": studio_id})["queryScenes"]
                )
                count = int(block["count"])
            except (urllib.error.URLError, RuntimeError, TimeoutError, KeyError, ValueError):
                # Unknown rather than zero. A network that could not be counted is left out of the
                # pack rather than silently judged too small, and a later run asks again.
                count = -1
            with lock:
                cache[f"{box.slug}:{studio_id}"] = count
                done[0] += 1
                if done[0] % 250 == 0:
                    cache_path.write_text(json.dumps(cache), encoding="utf-8")
                    print(f"    {done[0]} of {total}")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    cache_path.write_text(json.dumps(cache), encoding="utf-8")


#: The generic labels a country's ending carries under it (`.com.br`, `.co.uk`, `.net.au`).
_SECOND_LEVEL = frozenset({"com", "co", "net", "org", "ac", "gov", "edu", "ne", "or", "go"})


def slug_of(host: str, taken: dict[str, str]) -> str:
    """`onlyfans.com` -> `onlyfans`, and something readable when two sites want one word.

    The label before the suffix is what a person calls a site, so that is the slug. Two sites can
    want the same one (`saint.to` and `saint.cr` are different sites), so the second gets its
    suffix joined on rather than a number, which would be a name nobody could read back.
    """
    labels = [part for part in host.split(".") if part]
    # `mundomais.com.br` is Mundomais, not `com`: under a country's two-letter ending, a short
    # generic label (`com`, `co`, `net`, `org`, ...) is part of the suffix. The first build filed
    # it as `com`, which then pushed four other Brazilian sites onto their whole hosts.
    if len(labels) > 2 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        labels = labels[:-1]
    stem = labels[-2] if len(labels) > 1 else labels[0]
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "site"
    if taken.get(base) in (None, host):
        return base
    widened = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
    return widened


_GENERATED = re.compile(r"/(?:favicon-\d+x\d+|apple-touch-icon[^/]*|android-chrome-\d+x\d+)\.png$")


def _generator_siblings(declared: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The two big files a favicon GENERATOR writes beside the small ones a page declares.

    One convention (realfavicongenerator's, and the ones that copy it) writes `favicon-16x16.png`,
    `favicon-32x32.png`, `apple-touch-icon.png` AND `android-chrome-192x192.png` and `-512x512`
    into one directory, and a page declares the small ones, while the big ones are named only in
    a web manifest that is often missing. ManyVids is the case: it declares a 16, a 32
    and a 180, and a 512 of the same mark sits beside them. Claimed at their conventional sizes, so
    they are asked for first and cost one missing-file answer where the site does not have them.
    """
    found: list[tuple[int, str]] = []
    for _, address in declared:
        path = urllib.parse.urlsplit(address).path
        if _GENERATED.search(path):
            folder = urllib.parse.urljoin(address, ".")
            found += [
                (512, f"{folder}android-chrome-512x512.png"),
                (192, f"{folder}android-chrome-192x192.png"),
            ]
    return found


def icon_tiers(
    host: str,
    declared: list[tuple[int, str]],
    *,
    from_manifest: list[tuple[int, str]] | None = None,
    og: str | None = None,
) -> tuple[list[str], list[str]]:
    """Where a site's picture may be, in TWO GROUPS that are not judged the same way.

    ## Why two groups and not one ranked list

    **A bigger file is not a better logo, and Imgur is the proof.** "Fetch every candidate and keep
    the largest that decodes" gives Imgur a 128-pixel grey rectangle reading "The image you are
    requesting does not exist or is no longer available". That is what `imgur.com/favicon.png`
    serves, it is a perfectly valid PNG, and it is bigger than the real Imgur logo sitting at
    `/apple-touch-icon.png` at 100 pixels. Nothing in the bytes distinguishes the two; only
    RASTERISING the pack and looking at it can.

    So the sources are grouped by how much their CONTENT can be trusted, and size only decides
    between sources of equal standing:

    **Trusted.** An override, everything the head declares, everything the web manifest declares,
    and `/apple-touch-icon.png`. Each of those is a site saying "this is my icon": the first
    three in so many words, the fourth by a convention nobody serves a 404 page at. Ranked by the
    size each CLAIMS, but decided by the size each turns out to be, because a `sizes` attribute is
    a claim and sites get it wrong (Instagram declares a 192 that is a 32).

    **Last resort.** `/favicon.png`, `/favicon.ico` and `og:image`. Every one of these is routinely
    whatever the site happens to have at a path nobody maintains, so among them the FIRST that
    decodes is taken and size is not consulted at all, because it is not evidence of anything here. Used
    only when nothing trusted answered.

    **Two further rules hold inside that shape.** A site declaring one small icon must not beat
    `/apple-touch-icon.png`: both are in the trusted group and the bigger of the two wins
    whichever declared it. And the biggest source a site declares wins over the first one in
    document order, within the trusted group, where it belongs.
    """
    trusted = [
        *declared,
        *_generator_siblings(declared),
        *(from_manifest or []),
        # 180 rather than nothing: the one conventional address with a size convention behind it,
        # so ranking it as unknown put it below every declared 32.
        (180, f"https://{host}/apple-touch-icon.png"),
    ]
    # Stable within a size, so a tie keeps the order above: the head, then the manifest, then the
    # conventional address.
    trusted.sort(key=lambda one: -one[0])
    resort = [f"https://{host}/favicon.png", f"https://{host}/favicon.ico", *([og] if og else [])]
    return (
        list(dict.fromkeys(url for _, url in trusted)),
        list(dict.fromkeys(resort)),
    )


def icon_candidates(
    host: str,
    declared: list[tuple[int, str]],
    *,
    from_manifest: list[tuple[int, str]] | None = None,
    og: str | None = None,
) -> list[str]:
    """Every address this site will be asked for its picture, in the order they are worth trying.

    The two groups above, flattened. One rule, two views of it: this is the ORDER, which is what a
    test can hold without a site at the other end (`tests/gates/test_site_icon_build.py`), and
    `icon_tiers` is the same rule with the grouping still visible, which is what `fetch_icon`
    needs in order to judge the two groups differently.
    """
    trusted, resort = icon_tiers(host, declared, from_manifest=from_manifest, og=og)
    return list(dict.fromkeys([*trusted, *resort]))


# --- where a better picture lives ------------------------------------------------------------------


_COMMONS_API = "https://commons.wikimedia.org/w/api.php"


_SIMPLE_ICONS = "https://cdn.jsdelivr.net/npm/simple-icons@{version}/{path}"


class Elsewhere:
    """The two sources that are not the site: Wikimedia Commons and Simple Icons. Asked lazily.

    One per build, shared by every thread: a Commons title is resolved once, and Simple Icons'
    list of brands (a single JSON file for the pinned release) is read once, on first use.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._commons: dict[str, str | None] = {}
        self._brands: dict[str, tuple[str, str]] | None = None

    def commons(self, title: str) -> str | None:
        """The file address of a Commons title, or None when there is no such file."""
        with self._lock:
            if title in self._commons:
                return self._commons[title]
        query = urllib.parse.urlencode(
            {
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "iiprop": "url",
                "titles": f"File:{title}",
            }
        )
        found: str | None = None
        try:
            answer = json.loads(_get(f"{_COMMONS_API}?{query}", 200_000))
            for page in answer["query"]["pages"].values():
                info = page.get("imageinfo") or []
                if info:
                    found = str(info[0]["url"])
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, KeyError):
            found = None
        with self._lock:
            self._commons[title] = found
        return found

    def brands(self) -> dict[str, tuple[str, str]]:
        """Simple Icons' brands: lower-case title (and every alias) -> (slug, hex colour)."""
        with self._lock:
            if self._brands is not None:
                return self._brands
        index: dict[str, tuple[str, str]] = {}
        try:
            address = _SIMPLE_ICONS.format(
                version=catalog.SIMPLE_ICONS_VERSION, path="data/simple-icons.json"
            )
            for one in json.loads(_get(address, 4_000_000)):
                pair = (str(one["slug"]), str(one["hex"]))
                index.setdefault(str(one["title"]).strip().lower(), pair)
                index.setdefault(f"slug:{one['slug']}", pair)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, KeyError, TypeError):
            index = {}
        with self._lock:
            self._brands = index
        return index

    def simple_icon(self, slug: str, hex_colour: str) -> bytes | None:
        """One Simple Icons glyph as an SVG, filled with its brand colour."""
        address = _SIMPLE_ICONS.format(
            version=catalog.SIMPLE_ICONS_VERSION, path=f"icons/{slug}.svg"
        )
        raw, _ = _read(address)
        if raw is None or not re.fullmatch(r"[0-9A-Fa-f]{6}", hex_colour):
            return None
        return raw.replace(b"<svg ", f'<svg fill="#{hex_colour}" '.encode("ascii"), 1)


def keys_of(one: Candidate) -> list[str]:
    """What the hand-kept tables are keyed by for this entry: its hosts, then its slug."""
    return [*sorted(one.hosts | ({one.host} if one.host else set())), one.slug]


def simple_icon_for(one: Candidate, brands: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """The Simple Icons brand for this entry, or None.

    By the hand-kept slug first. By NAME only for a site a stash-box or Sift itself names, never
    for a studio off the long tail, where a production company sharing a word with a software brand
    would be drawn with the software's logo, which is the one outcome worse than a letter.
    """
    for key in keys_of(one):
        slug = catalog.SIMPLE_ICONS.get(key)
        if slug and f"slug:{slug}" in brands:
            return brands[f"slug:{slug}"]
    if one.studio_id is not None or one.source == "kind":
        return None
    for word in (one.name, *one.aliases):
        found = brands.get(word.strip().lower())
        if found:
            return found
    return None


@dataclass(frozen=True, slots=True)
class Made:
    """One site's finished icon and where it came from."""

    icon: art.Icon
    #: Which source it came from, in the words `picture_sources` uses.
    source: str
    #: The address it was read from.
    url: str


def picture_sources(
    one: Candidate,
    page: str,
    elsewhere: Elsewhere,
    manifest_of: Callable[[], list[tuple[int, str]]] = lambda: [],
) -> Iterator[tuple[str, str]]:
    """Every place this site's picture may be, best first, as `(source, address)`.

    A generator, so a site that answers well on its first source costs one request. The order is
    the rule, and every step of it is here for a site where the step below it was WRONG:

    1. **A picture chosen by hand**, fetched and looked at. See `catalog.PICKED`.
    2. **A studio's logo from its stash-box**, for a STUDIO. The labels of one network share the
       network's favicon (Blacked and BlackedRaw were byte-identical in the pack; so were Tushy and
       TushyRaw), and the box's curated logo is the only picture that tells them apart.
    3. **The site's own SVG icon.** A vector is sharp at any size and is the site's own drawing.
    4. **The brand's vector on Wikimedia Commons**, from `catalog.COMMONS`.
    5. **The site's own raster icons**, biggest claimed first (`icon_tiers`' trusted group).
    6. **The stash-box's icon for the site** (`querySites`), usually a small favicon, but for a
       site that answers a crawler with a challenge page it is the only copy there is.
    7. **Simple Icons**, the brand's glyph in its colour: sharp, but a one-colour glyph, which is
       why it is after everything that is the site's own full-colour mark.
    8. **The logo in the site's own page header** (`page_logos`), usually a WORDMARK, which is
       why it comes after every square mark: tried for the site whose every icon is small.
    9. **The last resort**: `/favicon.png`, `/favicon.ico` and a square `og:image`.

    A site in `catalog.NOT_ITS_MARK` skips 3, 5 and 9: its own icons are not its mark.

    `manifest_of` reads the site's web manifest, and is only called when the order gets that far:
    a site whose own SVG is sharp never has its manifest asked for.
    """
    host = one.host
    for key in keys_of(one):
        picked = catalog.PICKED.get(key)
        if picked:
            yield ("picked", picked[0])
    # A site whose own icons are another brand's mark or a photograph: none of them is asked for.
    theirs = not any(key in catalog.NOT_ITS_MARK for key in keys_of(one))
    # Only for a STUDIO nobody knows as a site: a fan site (OnlyFans, ManyVids) is a studio on
    # FansDB as well, and its box logo is a wordmark where its own icon is its mark.
    studio_first = bool(one.studio_image) and one.source != "sift" and not one.box_icons
    if studio_first and one.studio_image:
        yield ("stash-box studio logo", one.studio_image)
    declared = declared_icons(page, f"https://{host}/") if host and page and theirs else []
    for weight, address in declared:
        if weight < 0:
            yield ("site svg", address)
    for key in keys_of(one):
        title = catalog.COMMONS.get(key)
        if title:
            address = elsewhere.commons(title)
            if address:
                yield ("wikimedia commons", address)
    if host and theirs:
        from_manifest = manifest_of()
        rasters = [(weight, address) for weight, address in declared if weight >= 0]
        og = og_image(page, f"https://{host}/")
        trusted, resort = icon_tiers(host, rasters, from_manifest=from_manifest, og=og)
        for address in trusted[:HOW_MANY_TRIES]:
            yield ("site", address)
    else:
        og, resort = None, []
    # !! NOT asked for a site Sift or a stash-box knows as a SITE, even after everything else.
    # It was, second, on the first 256 build: OnlyFans' own icons all came back small, and the
    # "OnlyFans" studio on StashDB (a studio the site's creators are filed under, not the site)
    # carries one creator's banner as its logo, which is big and so won. A wrong logo is worse
    # than a soft one.
    # The box's icon for a site is a copy of the site's own favicon, so it is skipped with them.
    for address in one.box_icons if theirs else []:
        yield ("stash-box site icon", address)
    yield ("simple icons", "")
    if host and page:
        for address in page_logos(page, f"https://{host}/", (one.name, *one.aliases)):
            yield ("page logo", address)
    for address in resort:
        yield ("og image" if address == og else "site fallback", address)


#: The sources whose picture is not claimed to be an icon at all, and so are held to looking like a
#: mark before they are kept. A page's logo is here beside `og:image`: one can be a banner of
#: performers' photographs, which is a full-bleed opaque picture exactly as a photo is.
#: `/favicon.ico` in the same last-resort group IS a site's icon, only an unmaintained one, and a
#: full-bleed square there is a small tile like any other.
_LAST_RESORT = ("og image", "page logo")


def make_picture(
    one: Candidate, renderer: art.Renderer, elsewhere: Elsewhere
) -> tuple[Made | None, str]:
    """This site's icon, or None and the reason there is none.

    The FIRST source that makes a `high` icon wins, because the order of `picture_sources` is the
    judgement and a later source is never better merely for being bigger (a soft 404 page at
    `/favicon.png` is routinely bigger than the logo, Imgur's for one). When nothing makes a `high`
    one, the low one drawn from the most pixels is kept, and says so.
    """
    # A WITHHELD entry is never fetched, whoever asks: `main` sets them aside before this, and this
    # refuses one anyway, because a photograph fetched by a second caller would be a face in the
    # repository, the one fault here that cannot be taken back by the next build.
    reason = withheld_reason(one)
    if reason is not None:
        return None, f"withheld: {reason}"

    page = ""
    why = "nothing to try"
    if one.host:
        try:
            page = _get(f"https://{one.host}/", 400_000).decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as failure:
            why = f"its home page: {type(failure).__name__}"

    def manifest_of() -> list[tuple[int, str]]:
        for address in manifest_addresses(page, f"https://{one.host}/")[:2]:
            raw, _ = _read(address)
            found = manifest_icons(raw, address) if raw else []
            if found:
                return found
        return []

    plate_off = any(key in catalog.PLATE_OFF for key in keys_of(one))
    best: Made | None = None
    tried: set[str] = set()
    for source, address in picture_sources(one, page, elsewhere, manifest_of):
        if source == "simple icons":
            brand = simple_icon_for(one, elsewhere.brands())
            if brand is None:
                continue
            raw = elsewhere.simple_icon(*brand)
            address = _SIMPLE_ICONS.format(
                version=catalog.SIMPLE_ICONS_VERSION, path=f"icons/{brand[0]}.svg"
            )
            if raw is None:
                why = "simple icons: not found"
                continue
        else:
            if address in tried:
                continue
            tried.add(address)
            raw, failed = _read(address)
            if raw is None:
                why = f"{source}: {failed}"
                continue
        pixels = renderer.read(raw, svg=art.looks_like_svg(raw))
        if pixels is None:
            why = f"{source}: {_NOT_A_PICTURE}"
            continue
        if source == "og image":
            tall, wide = pixels.shape[:2]
            if max(tall, wide) > min(tall, wide) * OG_SQUARENESS:
                # A banner, not a mark. See `OG_SQUARENESS`.
                why = "its only large picture is a banner rather than a mark"
                continue
        icon = art.make_icon(pixels, plate_off=plate_off)
        if icon is None:
            why = f"{source}: the picture is empty"
            continue
        if source in _LAST_RESORT and (icon.tile or art.fills_its_box(icon.rgba)):
            # From a source that never claimed to be an icon, an opaque square (with a plate of
            # its own colour or none) is a photograph or a banner. See `_LAST_RESORT`.
            why = f"{source}: a photograph rather than a mark"
            continue
        if not icon.tile and art.fills_its_box(icon.rgba):
            # A full-bleed square with no single plate colour. From anything a site or a box
            # calls its ICON, that is the brand's own tile and is declared one. (From `og:image`
            # or a page it was refused above: `og:image` handed back a scene still and a person's
            # avatar on the first 256 build, and a photo identifies no site.)
            icon = replace(icon, tile=True)
        # An inline SVG's address is the whole drawing; the manifest names the page it was in.
        where = (
            f"https://{one.host}/ (inline svg in the page)"
            if address.startswith("data:")
            else address
        )
        made = Made(icon, source, where)
        if icon.quality == "high" or source == "picked":
            # A picked picture is FINAL even when it is small: somebody looked at it and at what
            # the crawl finds instead, and chose it: Magicly's 96-pixel mark over the 715-pixel
            # phone screenshot its `og:image` is.
            return (made, "")
        if best is None or icon.source_pixels > best.icon.source_pixels:
            best = made
    return (best, "") if best is not None else (None, why)


def _read(address: str) -> tuple[bytes | None, str]:
    """The bytes at one address, or None and the sentence saying why not.

    A pair rather than an exception, because every caller here treats a failure as "try the next
    one" and the reason is kept only for the manifest's `missing` list.
    """
    if address.startswith("data:image/svg+xml;base64,"):
        # An inline SVG lifted out of a page this run already fetched (`page_logos`): no request.
        try:
            return (base64.b64decode(address.split(",", 1)[1], validate=True), "")
        except ValueError:
            return (None, "an inline svg that is not base64")
    try:
        raw = _get(address, MAX_ICON_BYTES)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as failure:
        return (None, f"{type(failure).__name__}: {str(failure)[:60]}")
    return (raw, "") if raw else (None, "the file was empty")


# --- putting it together ---------------------------------------------------------------------------


def sift_supported() -> list[Candidate]:
    """Layer one: every site the downloader knows, read off the catalog it is declared in."""
    found: list[Candidate] = []
    for record in SITES:
        hosts = {host_of(one) for one in record.hosts}
        hosts.discard("")
        if not hosts:
            continue
        # The first host is the site's own; the rest are mirrors that answer to the same picture.
        first = host_of(record.hosts[0])
        found.append(Candidate(host=first, name=record.site, source="sift", hosts=hosts))
    return found


def box_sites(box: Box, pace: Pace) -> list[Candidate]:
    """Layer two: the sites a stash-box files links under, each with the box's icon for it."""
    pace.wait()
    block = cast(dict[str, Any], ask(box, _SITES)["querySites"])
    sites = cast(list[dict[str, Any]], block["sites"])
    found: list[Candidate] = []
    for site in sites:
        host = host_of(str(site.get("url") or ""))
        # A zero-width space has been seen at the front of a box's site name ("\u200bStripchat");
        # it is invisible, and it would make a name nobody can type.
        name = str(site["name"]).replace("\u200b", "").strip()
        if host and not (host.endswith(_NOT_A_SITE) and host not in catalog.HOST_JOINS.values()):
            icon = str(site.get("icon") or "")
            found.append(
                Candidate(
                    host=host,
                    name=name,
                    source=box.slug,
                    hosts={host},
                    box_icons=[icon] if icon.startswith("https://") else [],
                )
            )
    return found


def _studio_image(studio: dict[str, object]) -> str | None:
    """A studio's logo on its stash-box: the first image it has, which is the one the box shows."""
    images = studio.get("images")
    if isinstance(images, list):
        for one in images:
            if isinstance(one, dict) and str(one.get("url") or "").startswith("https://"):
                return str(one["url"])
    return None


def box_studios(box: Box, pace: Pace, cache: dict[str, int], cache_path: Path) -> list[Candidate]:
    """Layer three: the networks with a home page and more files than the floor."""
    if not box.studios_are_sites:
        print(f"  {box.name}: its studios are people, so none of them is a site, skipped")
        return []
    studios = top_level_studios(box, pace)
    print(f"  {box.name}: {len(studios)} top-level studios")
    addressed: list[tuple[str, str, str, str | None]] = []
    for studio in studios:
        address = own_address(studio)
        host = host_of(address or "")
        if host and not host.endswith(_NOT_A_SITE):
            addressed.append((str(studio["id"]), str(studio["name"]), host, _studio_image(studio)))
    print(f"  {box.name}: {len(addressed)} of them have an address of their own")
    scene_counts(box, [one[0] for one in addressed], cache, cache_path, pace)
    found: list[Candidate] = []
    for studio_id, name, host, image in addressed:
        count = cache.get(f"{box.slug}:{studio_id}", -1)
        if count >= SCENE_FLOOR:
            found.append(
                Candidate(
                    host=host,
                    name=name,
                    source=box.slug,
                    studio_id=studio_id,
                    scenes=count,
                    hosts={host},
                    studio_image=image,
                )
            )
    print(f"  {box.name}: {len(found)} at or above {SCENE_FLOOR} files")
    return found


def merge(layers: list[list[Candidate]]) -> list[Candidate]:
    """One entry per host, the earliest layer's name winning, the largest count kept.

    Sift's own name for a site beats a stash-box's, and a stash-box's beats another's: the first
    layer is the one Sift ships a downloader for, and calling it something else on its own icon is
    the vocabulary drift this repository has a gate about.

    **Every host a candidate claims is indexed, not only the one it is filed under.** A site with
    several domains is one entry with all of them on it (Sift's catalog files X under `x.com` and
    `twitter.com`), and a later layer naming one of the others is the SAME site arriving a second
    time. Keyed on the primary host alone, StashDB's `Twitter` and Sift's `X` would both go in and
    both claim `twitter.com`: two pictures for one address and an arbitrary answer to which one a
    row gets. The test that says no two entries may claim one host holds it.

    **And `catalog.HOST_JOINS` is applied first**, so two hosts the boxes file one site under
    (`t.me` and `telegram.org`) arrive as one candidate rather than two entries wanting one name.
    """
    by_host: dict[str, Candidate] = {}
    for layer in layers:
        for one in layer:
            for host in list(one.hosts | {one.host}):
                joined = catalog.HOST_JOINS.get(host)
                if joined:
                    one.hosts.add(joined)
                    if one.host == host:
                        one.host = joined
            held = next(
                (by_host[host] for host in sorted(one.hosts | {one.host}) if host in by_host), None
            )
            if held is None:
                held = one
            elif held is not one:
                held.hosts |= one.hosts
                # Every word either of them answered to, kept: a later layer's name for a site
                # becomes an alias of the one that won rather than being dropped.
                held.aliases = tuple(dict.fromkeys((*held.aliases, *one.aliases, one.name)))
                held.box_icons = list(dict.fromkeys((*held.box_icons, *one.box_icons)))
                held.studio_image = held.studio_image or one.studio_image
                if one.scenes is not None and (held.scenes or 0) < one.scenes:
                    held.scenes = one.scenes
                    held.studio_id = held.studio_id or one.studio_id
            for host in held.hosts | {held.host}:
                by_host[host] = held
    # By identity, because a Candidate is mutable and so unhashable, and because two sites CAN
    # share every field that would make them compare equal.
    once = {id(one): one for one in by_host.values()}
    for one in once.values():
        named = next((catalog.NAMES[h] for h in sorted(one.hosts) if h in catalog.NAMES), None)
        if named and named != one.name:
            one.aliases = (*one.aliases, one.name)
            one.name = named
        extra = [word for host in sorted(one.hosts) for word in catalog.ALIASES.get(host, ())]
        own = one.name.strip().lower()
        one.aliases = tuple(
            word
            for word in dict.fromkeys((*one.aliases, *extra))
            if word.strip() and word.strip().lower() != own
        )
    return sorted(
        once.values(),
        key=lambda one: (0 if one.source == "sift" else 1, -(one.scenes or 0), one.host),
    )


def held_sites(held: dict[str, Any]) -> list[Candidate]:
    """The pack as it stands, as candidates: what a run WITHOUT the stash-box keys rebuilds.

    Every entry the last keyed run found is still a site worth a logo, and its hosts, names,
    studio and count are all in the manifest, so the pictures can be redone without asking the
    boxes again. What the boxes contributed to the PICTURE (their icon for a site, their logo for
    a studio) comes from `BoxPictures`, which a keyed run writes beside the data directory.
    """
    found: list[Candidate] = []
    # The WITHHELD entries too: a keyless rerun that forgot them would drop the site from the
    # manifest, and its hosts and names are what let the lookup say "this is that site, and there
    # is no picture of it" rather than matching a row to a stranger that shares a word.
    for entry in [*held.get("icons", []), *held.get("withheld", [])]:
        if not isinstance(entry, dict):
            continue
        hosts = [str(one) for one in entry.get("hosts") or []]
        if not hosts:
            continue
        # What the boxes gave this entry's picture, off the manifest itself: the working cache
        # `BoxPictures` keeps can be gone, and without this a keyless rerun of one studio would
        # quietly trade its curated logo for the network's favicon.
        picture, address = str(entry.get("picture") or ""), str(entry.get("picture_url") or "")
        found.append(
            Candidate(
                host=hosts[0],
                name=str(entry.get("name") or entry["slug"]),
                source=str(entry.get("source") or "held"),
                studio_id=entry.get("studio_id"),
                scenes=entry.get("files"),
                hosts=set(hosts),
                aliases=tuple(str(one) for one in entry.get("aliases") or []),
                slug=str(entry["slug"]),
                studio_image=address if picture == "stash-box studio logo" else None,
                box_icons=[address] if picture == "stash-box site icon" else [],
            )
        )
    # And every site the last run could NOT picture: a site that timed out or answered a
    # challenge then may have a source now (a stash-box icon, Simple Icons) that it did not have.
    for entry in held.get("missing", []):
        host = str(entry.get("host") or "") if isinstance(entry, dict) else ""
        if "." in host:
            found.append(
                Candidate(
                    host=host, name=str(entry.get("name") or host), source="held", hosts={host}
                )
            )
    return found


def label_images(box: Box, pace: Pace, named: list[Candidate]) -> dict[str, str]:
    """The box's logo for each site named by hand, by host: the LABELS a network's favicon hides.

    A label is not a top-level studio (Blacked is a child of its network), so `studio_images`
    never sees it. Asked by name, and taken only from a studio whose own name is that name (or
    one of its aliases: the box spells it `Blacked Raw`) AND whose links name that host: a name alone would take a namesake's logo, and a namesake's logo
    is the exact fault this exists to fix.
    """
    if not box.studios_are_sites:
        return {}
    found: dict[str, str] = {}
    for one in named:
        studios: list[dict[str, Any]] = []
        # By every word it goes by: the box's search does not find `Tushy Raw` from `TushyRaw`.
        for word in (one.name, *one.aliases):
            pace.wait()
            try:
                block = cast(
                    dict[str, Any], ask(box, _STUDIO_BY_NAME, {"name": word})["queryStudios"]
                )
            except (urllib.error.URLError, RuntimeError, TimeoutError, KeyError, ValueError):
                continue
            studios += cast(list[dict[str, Any]], block["studios"])
        for studio in studios:
            urls = [str(url.get("url") or "") for url in studio.get("urls") or []]
            image = _studio_image(studio)
            words = {word.strip().lower() for word in (one.name, *one.aliases)}
            same = str(studio.get("name") or "").strip().lower() in words
            # The host anywhere in one of its links, not only as a link's own host: a box files
            # Tushy Raw with no link to its home page, and an IAFD page ending
            # `/tushyraw.com.htm`, which is still the site saying which address it is.
            if same and image and any(one.host in url.lower() for url in urls):
                found[one.host] = image
                break
    print(f"  {box.name}: {len(found)} of {len(named)} named labels have a logo")
    return found


def studio_images(box: Box, pace: Pace) -> dict[str, str]:
    """Every top-level studio's logo on this box, by studio id. A page of a hundred per request."""
    if not box.studios_are_sites:
        return {}
    found: dict[str, str] = {}
    for studio in top_level_studios(box, pace):
        image = _studio_image(studio)
        if image:
            found[str(studio["id"])] = image
    print(f"  {box.name}: {len(found)} studio logos")
    return found


class BoxPictures:
    """What the stash-boxes said about PICTURES, kept so a run without keys can use it.

    `sites` is host -> the icon addresses the boxes file that site under; `studios` is studio id
    -> that studio's logo; `labels` is host -> the logo of the studio a site named by hand IS
    (`label_images`). Only addresses, never a key: the pictures themselves
    are public files on the boxes' own hosts and are fetched like any other.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        held: dict[str, Any] = {}
        if path.is_file():
            try:
                held = json.loads(path.read_text("utf-8"))
            except ValueError:
                held = {}
        self.sites: dict[str, list[str]] = dict(held.get("sites") or {})
        self.studios: dict[str, str] = dict(held.get("studios") or {})
        self.labels: dict[str, str] = dict(held.get("labels") or {})

    def learn(self, candidates: list[Candidate]) -> None:
        for one in candidates:
            if one.box_icons:
                self.sites[one.host] = list(
                    dict.fromkeys((*self.sites.get(one.host, []), *one.box_icons))
                )
            if one.studio_id and one.studio_image:
                self.studios[str(one.studio_id)] = one.studio_image

    def apply(self, one: Candidate) -> None:
        for host in sorted(one.hosts | {one.host}):
            one.box_icons = list(dict.fromkeys((*one.box_icons, *self.sites.get(host, []))))
        if one.studio_id and not one.studio_image:
            one.studio_image = self.studios.get(str(one.studio_id))
        if not one.studio_image and one.host in self.labels:
            one.studio_image = self.labels[one.host]

    def save(self) -> None:
        self.path.write_text(
            json.dumps(
                {"sites": self.sites, "studios": self.studios, "labels": self.labels},
                indent=1,
                sort_keys=True,
            ),
            encoding="utf-8",
            newline="\n",
        )


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
        # Only where there are any, so the manifest does not gain an empty list on most entries.
        # `site_icons` reads a missing key as none.
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


def withheld_reason(one: Candidate) -> str | None:
    """Why this entry ships no picture (`catalog.WITHHELD_WHY`), or None when it ships one."""
    return catalog.withheld_reason(one.hosts | {one.host} - {""}, kind=one.source == "kind")


def withheld_entry(one: Candidate, reason: str) -> dict[str, object]:
    """One `withheld` record: who the site is, and why no picture of it ships.

    Everything `held_sites` needs to bring the entry back on a keyless rerun (its hosts, names,
    studio and count), so a rebuild withholds it again rather than dropping it and then, one day,
    fetching it as a site it has never seen. The reason AND the sentence, written here rather than
    by hand: a hand-written reason is the one a rebuild silently drops.
    """
    return {
        "slug": one.slug,
        "name": one.name,
        "hosts": sorted(one.hosts - {""}),
        **({"aliases": sorted(one.aliases)} if one.aliases else {}),
        "source": one.source,
        "studio_id": one.studio_id,
        "files": one.scenes,
        "reason": reason,
        "why": catalog.WITHHELD_WHY[reason],
    }


#: What a site is refused on by a RULE rather than by the network, in `make_picture`'s own words.
#: A partial run drops the held entry of a site refused this way (see `main`).
_RULE_REFUSALS = ("a photograph rather than a mark", "a banner rather than a mark")


def _slugs(asked: str) -> set[str]:
    """The slugs `--only` names: comma or space separated, or `@file` for a list too long for a
    Windows command line to carry."""
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


#: The ground a contact sheet draws each mark on, by its recorded `tone`: a white one-colour mark
#: on dark, a black one on light, and a coloured one on the dark the app's own tiles use. The same
#: answer the client gives a toned mark, so the sheet shows what a tile will.
SHEET_GROUNDS = {"light": "#1f2228", "dark": "#eceef1", "colour": "#2b2e35"}


def contact_sheet(
    out: Path, renderer: art.Renderer, *, only: set[str], per_sheet: int
) -> list[Path]:
    """Every icon in the pack drawn at 96 pixels with its slug under it, twelve to a row.

    THE ONLY WAY TO KNOW THE PACK IS RIGHT, and so it is part of the build rather than a one-off:
    a wrong site's logo, a blurred favicon and a photograph are all valid 256-pixel PNGs that pass
    every gate. The label under each says the slug, then the source's size and where it came from;
    a `low` one is written in red. `per_sheet` splits the pack into numbered sheets of that many
    (`out-01.png`, ...), which is what a picture reader can take in at a legible scale.
    """
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
            # The studios the pack already holds, with their logos, without the thousands of
            # per-network file counts that deciding membership afresh costs.
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
        # A PARTIAL RUN REBUILDS WHAT THE PACK ALREADY HOLDS; IT NEVER DROPS AN ENTRY. The layers
        # that need a key are most of the pack, and a run that wrote only what it could reach
        # without one would silently throw four fifths of the manifest away.
        layers.append(held_sites(held))
        print(f"  held in the pack: {len(layers[-1])} sites (no stash-box asked)")
    wanted = merge(layers)
    for one in wanted:
        pictures.apply(one)
    return wanted


def _name_the_slugs(wanted: list[Candidate], held: dict[str, Any]) -> None:
    # Slugs are STABLE: a host the pack already files under a slug keeps it, so a rebuild does not
    # rename files (and so move every token a browser has kept) for no reason.
    held_slug = {
        str(host): str(entry["slug"])
        for entry in [*held.get("icons", []), *held.get("withheld", [])]
        for host in entry.get("hosts") or []
    }
    taken: dict[str, str] = {slug: slug for slug, *_ in catalog.LINK_KINDS}
    for one in wanted:
        # The primary host's slug first: a join (`t.me` into `telegram.org`) keeps the name of the
        # site it was joined INTO, not whichever of its hosts sorts first.
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
    # WITHHELD ENTRIES ARE SET ASIDE BEFORE ANYTHING IS FETCHED, and over the WHOLE list rather than
    # the `--only` part of it: deciding one costs no request, so every run writes the full list and
    # a partial run cannot leave a site withheld on one line and shipped on another.
    aside = [(one, reason) for one in wanted if (reason := withheld_reason(one)) is not None]
    withheld = sorted(
        (withheld_entry(one, reason) for one, reason in aside), key=lambda one: str(one["slug"])
    )
    kept_back = {one.slug for one, _ in aside}
    kept_back_hosts = {host for one, _ in aside for host in one.hosts}
    wanted = [one for one in wanted if one.slug not in kept_back]
    # Every slug SOME layer still makes, read before `--only` narrows the list: a held entry that
    # no layer makes any more (a name-only entry taken out of the catalog) has left the pack, and
    # a partial run drops it and its file rather than carrying it forward for ever.
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
            if made is not None:
                # Written beside the real file and moved into place, so a build that dies half way
                # through a write never leaves a truncated PNG where a good one was.
                destination = ICONS_DIR / f"{one.slug}.png"
                working = destination.with_name(f"{one.slug}.new.png")
                working.write_bytes(art.encode_png(made.icon.rgba))
                os.replace(working, destination)
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


def _carry_forward(
    entries: list[dict[str, object]],
    missing: list[dict[str, str]],
    held: dict[str, Any],
    aside: _SetAside,
) -> list[dict[str, str]]:
    """A partial run: what it touched wins, and everything it did not consider is kept."""
    # Including an entry that failed here and succeeded before: a site that answered yesterday and
    # timed out today has not stopped having a logo.
    seen = {str(one["slug"]) for one in entries}
    # ...EXCEPT one this run REFUSED on a rule, where what it held came from the very kind of
    # source that rule judges (`_LAST_RESORT`). A timeout says nothing about yesterday's picture;
    # "the page's picture is a photograph", with yesterday's picture being the page's picture,
    # says it was wrong.
    refused = {m["host"] for m in missing if any(rule in m["why"] for rule in _RULE_REFUSALS)}
    entries += [
        one
        for one in held.get("icons", [])
        if isinstance(one, dict)
        and str(one.get("slug")) not in seen
        # A held picture of an entry that is withheld NOW goes, with its file (below): that is
        # how an entry added to `catalog.MASCOTS` leaves the pack on the next run of any size.
        and str(one.get("slug")) not in aside.kept_back
        and str(one.get("slug")) in aside.still_made
        and not (
            one.get("picture") in _LAST_RESORT
            and refused & {str(host) for host in one.get("hosts") or []}
        )
    ]
    # And a held entry that did not survive takes its picture with it: a picture no entry
    # names is one nothing can serve, and the pack's own test refuses one.
    surviving = {str(one["slug"]) for one in entries}
    for one in held.get("icons", []):
        if isinstance(one, dict) and str(one.get("slug")) not in surviving:
            (ICONS_DIR / f"{one['slug']}.png").unlink(missing_ok=True)
    answered = {host for one in entries for host in one.get("hosts", [])}  # type: ignore[attr-defined]
    # Still missing, and said ONCE: this run's reason replaces the held one for the same host.
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
        # LF, named rather than left to the site: `write_text` translates on Windows, where this
        # is run, and the repository is LF everywhere.
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
        # A WHOLE build owns the directory: a picture no entry names is a picture nothing can
        # serve, and the pack's own test refuses one.
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
