# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the site-icon build asks the stash-boxes: their sites, their studios and their logos."""

from __future__ import annotations

import json
import queue
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import site_icon_catalog as catalog
from site_icon_pages import USER_AGENT, host_of
from site_icon_sites import Candidate

from sift.kernel.db import DATABASE_FILENAME
from sift.kernel.secrets import open_secret
from sift.slices.auth.crypto import WrappedKey, unwrap_master_key
from sift.slices.stash_boxes.known_boxes import SITES_ARE_SITES, sites_are_for

#: Files a top-level studio's network needs before its site earns an icon: a choice of pack size
#: (about eleven hundred pictures), counted over the network, not the studio.
SCENE_FLOOR = 50


#: The adapter's own pace: the account a throttle would hit is the maintainer's.
REQUESTS_PER_MINUTE = 240


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


def _library(data_dir: Path) -> sqlite3.Connection:
    """The library's database, opened read only: this script never writes to a library."""
    path = data_dir / DATABASE_FILENAME
    if not path.is_file():
        raise SystemExit(f"no library at {path}: point SIFT_DATA_DIR at one")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _boxes(connection: sqlite3.Connection, password: str) -> list[Box]:
    """Every switched-on stash-box, its key unsealed; nothing prints, logs or writes a key."""
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
    """One GraphQL question; a stash-box answers `200 OK` with an `errors` array too."""
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
    """A studio's own home page, or None, including when its "home" is a page on another site."""
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
    """Whether an address is a site's own: its root, or a page whose host carries its name."""
    parts = urllib.parse.urlsplit(address.strip() if "//" in address else f"//{address.strip()}")
    if parts.path in ("", "/"):
        return True
    host = re.sub(r"[^a-z0-9]+", "", (parts.hostname or "").lower())
    words = [word for word in re.split(r"[^a-z0-9]+", name.lower()) if len(word) >= 3]
    return any(word in host for word in words)


def scene_counts(
    box: Box, studio_ids: list[str], cache: dict[str, int], cache_path: Path, pace: Pace
) -> None:
    """Count each network's files, cached: six threads share one paced permit queue."""
    todo = _not_counted(box, studio_ids, cache)
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
                # Unknown, not zero: left out of the pack, and asked again by a later run.
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


def _not_counted(box: Box, studio_ids: list[str], cache: dict[str, int]) -> queue.Queue[str]:
    todo: queue.Queue[str] = queue.Queue()
    for studio_id in studio_ids:
        if f"{box.slug}:{studio_id}" not in cache:
            todo.put(studio_id)
    return todo


def box_sites(box: Box, pace: Pace) -> list[Candidate]:
    """Layer two: the sites a stash-box files links under, each with the box's icon for it."""
    pace.wait()
    block = cast(dict[str, Any], ask(box, _SITES)["querySites"])
    sites = cast(list[dict[str, Any]], block["sites"])
    found: list[Candidate] = []
    for site in sites:
        host = host_of(str(site.get("url") or ""))
        # A zero-width space has been seen at the front of a box's site name.
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


def label_images(box: Box, pace: Pace, named: list[Candidate]) -> dict[str, str]:
    """The box's logo for each site named by hand, from a studio of that name linking its host."""
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
            # Anywhere in a link: a box may file a label with only an IAFD page naming its host.
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
    """What the stash-boxes said about pictures, kept for a run without keys; addresses only."""

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
