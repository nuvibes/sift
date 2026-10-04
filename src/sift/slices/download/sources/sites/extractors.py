# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Derived from cyberdrop-dl (GPL-3.0): the endpoints and page selectors below were followed from its
# crawlers. See the package header and NOTICE.
"""The per-site extractors. Each `extract_*` coroutine takes `(url, session, ctx)` and returns the
direct media it found as `ExtractedFile`s.

The session is the guarded one, so every request an extractor makes is vetted and pinned. An extractor
that finds nothing returns an empty list, and `build_resolved_media` turns that into the ordinary
"nothing to download".
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup

from sift.slices.download.sources.policy import PACE_MS_MAX
from sift.slices.download.sources.progress import Progress, Report, nowhere
from sift.slices.download.sources.sites.common import (
    ExtractContext,
    ExtractedFile,
    decrypt_jpg5_xor,
    load_session_cookie,
    pick_rung,
    source_host,
    source_origin,
)
from sift.slices.download.sources.tuning import QUALITY_COMPATIBLE

# Coomer/Kemono gate their JSON behind a quirky header: a plain `Accept: application/json` gets a 403
# telling the client to use `text/css`. The body is still JSON, read via json.loads.
_COOMER_ACCEPT = "text/css"

_JPG5_DATA_SRC_RE = re.compile(r'data-src="([^"]+)"')

#: The most a page read and an API answer may take, whole. A ceiling because a control read is a few
#: kilobytes and a server trickling one byte at a time would otherwise hold a download for ever; the
#: connection timeout setting is not this (it is how long a connection may say NOTHING), and it
#: applies inside the ceiling (`_within`).
_PAGE_CEILING = 30.0
_API_CEILING = 15.0
#: Room inside a ceiling for the wait between requests, which is spent inside the request's own
#: budget (the session paces as a request starts). The setting's own maximum, so the longest wait
#: somebody can choose never eats a read's time.
_PACE_ROOM = PACE_MS_MAX / 1000


def _within(session: aiohttp.ClientSession, ceiling: float) -> aiohttp.ClientTimeout:
    """A read's time budget: its own ceiling, and the connection timeout setting inside it.

    The setting reaches these reads through the session a download opens (`net.guarded_session`
    with the download's policy), whose budget to connect and between reads IS the setting. Read
    from the session rather than passed in, so the twenty reads below cannot disagree with it. A
    session with no policy has no read budget, and the read keeps the ceiling alone. A setting
    above the ceiling raises the ceiling, so a longer timeout is never cut short.
    """
    stuck = session.timeout.sock_read
    if stuck is None:
        return aiohttp.ClientTimeout(total=ceiling)
    return aiohttp.ClientTimeout(
        total=max(ceiling, stuck) + _PACE_ROOM, sock_connect=stuck, sock_read=stuck
    )


_PIXELDRAIN_FILE = "https://pixeldrain.com/api/file/{item_id}"
_PIXELDRAIN_LIST = "https://pixeldrain.com/api/list/{item_id}"
_PIXELDRAIN_INFO = "https://pixeldrain.com/api/file/{item_id}/info"
# Cyberdrop's API entrypoint for every cyberdrop domain (.cr/.me/.to), as cyberdrop-dl uses it.
_CYBERDROP_API = "https://api.cyberdrop.cr/api"
_GOFILE_API = "https://api.gofile.io"
_GOFILE_LANG = "en-US"
# A public constant from GoFile's own JavaScript (not a secret), and the 4-hour window the site-token
# hash is keyed to. Both are cyberdrop-dl's values.
_GOFILE_SALT = "g4f8fd9f12h14g"  # a public site constant, not a credential
_GOFILE_TOKEN_BUCKET_SEC = 14400
_TURBOVID_PRIMARY = "https://turbovid.cr"
_CYBERFILE_BASE = "https://cyberfile.me"
# A CDN video URL embedded as a plain string in a PMVHaven Nuxt data array, and a broad HTML fallback.
_PMV_VIDEO_RE = re.compile(
    r'^https?://[^\s"\'<>]+\.(?:mp4|m3u8|webm|mov|mkv)(?:\?[^\s"\'<>]*)?$', re.IGNORECASE
)
_PMV_FALLBACK_RE = re.compile(
    r'https?://[^\s"\'<>\[\]]+\.(?:mp4|m3u8|webm|mov)(?:\?[^\s"\'<>\[\]]*)?', re.IGNORECASE
)
# HQporner. A video page (`/hdporn/<id>-<slug>.html`) holds no media: it embeds a player page from a
# separate host as an iframe in `#playerWrapper`, and the player page writes a `<video>` whose
# `<source>` tags are the quality ladder, each labelled `360p`, `720p HD`, `1080p60`, `2160p60`...
# The tags sit inside a JavaScript string, so their quotes arrive backslash-escaped.
_HQPORNER_VIDEO_PATH_RE = re.compile(r"^/hdporn/\d+[^/]*\.html$")
# The same player address the page's "alternative player" buttons pass to the site's own AJAX; read
# when the iframe itself is missing.
_HQPORNER_PLAYER_PARAM_RE = re.compile(r"""(?:alt|native)player\.php\?i=(//[^'"&\s]+)""")
_HQPORNER_SOURCE_RE = re.compile(
    r"""<source\s+src=\\?["']([^"'\\]+)\\?["']\s+title=\\?["']([^"'\\]*)""", re.IGNORECASE
)
_HQPORNER_LABEL_HEIGHT_RE = re.compile(r"(\d{3,4})p")
_HQPORNER_FILE_HEIGHT_RE = re.compile(r"/(\d{3,4})\.\w+$")
_HQPORNER_TITLE_SUFFIX_RE = re.compile(r"\s*-\s*HQporner\.com\s*$", re.IGNORECASE)
# A PMVHaven page's `<title>` is the video's title and then the site's own name.
_PMVHAVEN_TITLE_SUFFIX_RE = re.compile(r"\s*-\s*PMVHaven\s*$", re.IGNORECASE)
# A cyberfile page calls showFileInformation(<id>); the id feeds a file_details AJAX whose HTML
# carries an openUrl('...download_token=...') the download button fires.
_CYBERFILE_FILEID_RE = re.compile(r"showFileInformation\((\d+)\)")
_CYBERFILE_DL_RE = re.compile(r"""openUrl\(['"](https://[^'"]+?download_token=[^'"]+?)['"]\)""")
# Bunkr hosts to retry a page against when one is DNS- or DDoS-blocked; the download/sign APIs; and the
# headers those APIs gate on.
_BUNKR_PAGE_HOSTS = ("bunkr.cr", "bunkr.site", "bunkr.ph")
_BUNKR_DL_ENDPOINT = "https://dl.bunkr.cr/api/_001_v2"
_BUNKR_SIGN_ENDPOINT = "https://glb-apisign.cdn.cr/sign"
_BUNKR_API_HEADERS = {"Referer": "https://dl.bunkr.cr/", "Origin": "https://dl.bunkr.cr"}
# `var name = "value";` (quoted or a bare expr) inside a file page's script.
_BUNKR_JS_VAR_RE = re.compile(
    r"""var\s+(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^;]+);""", re.DOTALL
)
# window.albumFiles is a JS object literal (unquoted keys); fold the known keys into valid JSON.
_BUNKR_FILE_FIELDS = (
    "id",
    "name",
    "original",
    "slug",
    "type",
    "extension",
    "size",
    "timestamp",
    "thumbnail",
    "cdnEndpoint",
)


def _attr(tag: Any, name: str) -> str | None:
    """One BeautifulSoup attribute as a single string. An attribute can be a str, a list (multi-valued
    like `class`), or absent, so this normalises to the first string or None."""
    value = tag.get(name)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


async def extract_pixeldrain(
    url: str, session: aiohttp.ClientSession, _ctx: ExtractContext
) -> list[ExtractedFile]:
    """A Pixeldrain file (`/u/<id>`) is a direct API address, named from its info. A list
    (`/l/<id>`) is read from the list API, one item per file, each carrying its own name.

    The name is the file's own, not the ID the download address ends in: a file uploaded as
    `holiday.mp4` would otherwise arrive as `<id>.mp4`. The ID is kept as well, as the `{id}`
    naming word.
    """
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) < 2:
        return []
    kind, item_id = segments[0], segments[1]
    if kind == "u":
        return [
            ExtractedFile(
                _PIXELDRAIN_FILE.format(item_id=item_id),
                filename=await _pixeldrain_name(session, item_id),
                id=item_id,
            )
        ]
    if kind == "l":
        async with session.get(
            _PIXELDRAIN_LIST.format(item_id=item_id), timeout=_within(session, _API_CEILING)
        ) as r:
            if not r.ok:
                return []
            data = json.loads(await r.text())
        files = data.get("files", []) if isinstance(data, dict) else []
        return [
            ExtractedFile(
                _PIXELDRAIN_FILE.format(item_id=entry["id"]),
                filename=_name_in(entry),
                id=str(entry["id"]),
            )
            for entry in files
            if isinstance(entry, dict) and entry.get("id")
        ]
    return []


async def _pixeldrain_name(session: aiohttp.ClientSession, item_id: str) -> str | None:
    """The name a Pixeldrain file was uploaded under, from its info. None on any failure at all:
    the file downloads either way, and only its name falls back to the ID."""
    try:
        async with session.get(
            _PIXELDRAIN_INFO.format(item_id=item_id), timeout=_within(session, _API_CEILING)
        ) as answer:
            if not answer.ok:
                return None
            data = json.loads(await answer.text())
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
    return _name_in(data)


def _name_in(entry: object) -> str | None:
    """The `name` field of a Pixeldrain file entry, or None where it has none."""
    name = entry.get("name") if isinstance(entry, dict) else None
    return name if isinstance(name, str) and name.strip() else None


async def extract_fapello(
    url: str, session: aiohttp.ClientSession, _ctx: ExtractContext
) -> list[ExtractedFile]:
    """A Fapello post page carries its media as plain `<img>`/`<video><source>` tags in the content
    block; read their `src`."""
    async with session.get(url, timeout=_within(session, _PAGE_CEILING)) as r:
        if not r.ok:
            return []
        soup = _soup(await r.text())
    found: list[ExtractedFile] = []
    for tag in soup.select("div#content img, div#content video > source"):
        src = _attr(tag, "src")
        if src:
            found.append(ExtractedFile(src))
    return found


async def extract_cyberdrop(
    url: str, session: aiohttp.ClientSession, ctx: ExtractContext
) -> list[ExtractedFile]:
    """A Cyberdrop file (`/f/` or `/e/`) resolves through the API in two steps: `file/info` for the
    real name, then `file/auth` for the signed CDN address. An album (`/a/`) lists its files from the
    page and resolves each the same way."""
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) >= 2 and segments[0] in ("f", "e"):
        return await _cyberdrop_file(segments[1], session)
    if segments and segments[0] == "a":
        return await _cyberdrop_album(url, session, ctx)
    return []


async def _cyberdrop_file(file_id: str, session: aiohttp.ClientSession) -> list[ExtractedFile]:
    name: str | None = None
    async with session.get(
        f"{_CYBERDROP_API}/file/info/{file_id}", timeout=_within(session, _API_CEILING)
    ) as r:
        if r.ok:  # the name is best-effort; a miss leaves the filename to the hash rule
            info = json.loads(await r.text())
            if isinstance(info, dict) and isinstance(info.get("name"), str) and info["name"]:
                name = info["name"]
    async with session.get(
        f"{_CYBERDROP_API}/file/auth/{file_id}", timeout=_within(session, _API_CEILING)
    ) as r:
        if not r.ok:
            return []
        data = json.loads(await r.text())
    direct = data.get("url") if isinstance(data, dict) else None
    return [ExtractedFile(direct, filename=name)] if isinstance(direct, str) and direct else []


async def _cyberdrop_album(
    url: str, session: aiohttp.ClientSession, ctx: ExtractContext
) -> list[ExtractedFile]:
    async with session.get(url, timeout=_within(session, _PAGE_CEILING)) as r:
        if not r.ok:
            return []
        soup = _soup(await r.text())
    found: list[ExtractedFile] = []
    for anchor in soup.select("a.image"):
        href = _attr(anchor, "href")
        if href:
            found.extend(await extract_cyberdrop(urljoin(url, href), session, ctx))
    return found


async def extract_xbunkr(
    url: str, session: aiohttp.ClientSession, _ctx: ExtractContext
) -> list[ExtractedFile]:
    """An XBunkr media host address is already direct. An album page lists its files as `a.image`
    anchors whose `href` is the direct address."""
    if source_host(url).split(".")[0].startswith("media"):
        return [ExtractedFile(url)]
    async with session.get(url, timeout=_within(session, _PAGE_CEILING)) as r:
        if not r.ok:
            return []
        soup = _soup(await r.text())
    found: list[ExtractedFile] = []
    for anchor in soup.select("a.image"):
        href = _attr(anchor, "href")
        if href:
            found.append(ExtractedFile(href))
    return found


def _jpg5_encrypted_blobs(soup: BeautifulSoup) -> list[str]:
    """The obfuscated media blobs a JPG5 image page exposes: the download button's `href` first (the
    canonical one), then every lazy image's `data-src`."""
    blobs: list[str] = []
    button = soup.select_one("a.btn-download")
    if button is not None:
        href = _attr(button, "href")
        if href:
            blobs.append(href)
    for image in soup.select("img[data-src]"):
        data_src = _attr(image, "data-src")
        if data_src:
            blobs.append(data_src)
    return blobs


async def extract_jpg5(
    url: str, session: aiohttp.ClientSession, _ctx: ExtractContext
) -> list[ExtractedFile]:
    """A JPG5 image page hides the real address as base64+hex+XOR. Read it from the obfuscated script
    if present, else from the download button / lazy image blobs, else from a plain image `src`."""
    async with session.get(url, timeout=_within(session, _PAGE_CEILING)) as r:
        if not r.ok:
            return []
        soup = _soup(await r.text())
    for script in soup.find_all("script"):
        if "obfuscated" in (script.text or ""):
            match = _JPG5_DATA_SRC_RE.search(script.text)
            if match:
                decrypted = decrypt_jpg5_xor(match.group(1))
                if decrypted:
                    return [ExtractedFile(decrypted)]
    for blob in _jpg5_encrypted_blobs(soup):
        decrypted = decrypt_jpg5_xor(blob)
        if decrypted.startswith("http"):
            return [ExtractedFile(decrypted)]
    image = soup.select_one("img[data-src]") or soup.select_one("img.image-viewer")
    if image is not None:
        src = _attr(image, "src")
        if src and src.startswith("http"):
            return [ExtractedFile(src)]
    return []


def gofile_web_token(user_agent: str, api_token: str, *, now: float | None = None) -> str:
    """GoFile's `X-Website-Token`: `sha256(ua::lang::token::bucket::salt)`, where the bucket is a
    4-hour window. The user agent must be the one the contents request sends, or GoFile rejects it.
    `now` is injectable so the value is tested without the clock."""
    moment = now if now is not None else time.time()
    bucket = int(moment // _GOFILE_TOKEN_BUCKET_SEC)
    raw = f"{user_agent}::{_GOFILE_LANG}::{api_token}::{bucket}::{_GOFILE_SALT}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _gofile_content_id(url: str) -> str | None:
    """`gofile.io/d/<id>` or `gofile.io/<id>` -> the content id (a folder or a file)."""
    segments = [segment for segment in urlsplit(url).path.strip("/").split("/") if segment]
    if not segments:
        return None
    return segments[1] if segments[0] == "d" and len(segments) >= 2 else segments[0]


def gofile_files(node: Any, cookie: str) -> list[ExtractedFile]:
    """Every downloadable file under a GoFile content node: one file, or a folder whose `children`
    are walked. Each file's CDN link needs the guest `accountToken` cookie, so it rides the item."""
    found: list[ExtractedFile] = []
    if not isinstance(node, dict):
        return found
    if node.get("type") == "file":
        link = node.get("link")
        if (not link or link == "overloaded") and isinstance(node.get("directLink"), str):
            link = node["directLink"]  # the overloaded-node fallback
        if isinstance(link, str) and link:
            name = node.get("name")
            found.append(
                ExtractedFile(link, name if isinstance(name, str) else None, cookie=cookie)
            )
        return found
    children = node.get("children")
    if isinstance(children, dict):
        for child in children.values():
            found.extend(gofile_files(child, cookie))
    return found


async def extract_gofile(
    url: str, session: aiohttp.ClientSession, ctx: ExtractContext
) -> list[ExtractedFile]:
    """Resolve a GoFile folder or file: mint a guest token, compute the site-token from it, read the
    contents API, then hand back each file's CDN link with the guest cookie it needs on the fetch."""
    content_id = _gofile_content_id(url)
    if not content_id:
        return []
    async with session.post(
        f"{_GOFILE_API}/accounts", json={}, timeout=_within(session, _API_CEILING)
    ) as r:
        if not r.ok:
            return []
        account = json.loads(await r.text())
    token = account.get("data", {}).get("token") if isinstance(account, dict) else None
    if not isinstance(token, str) or not token:
        return []
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Website-Token": gofile_web_token(ctx.user_agent, token),
        "X-BL": _GOFILE_LANG,
    }
    params = urlencode(
        {
            "contentFilter": "",
            "sortField": "name",
            "sortDirection": "1",
            "pageSize": "1000",
            "page": "1",
        }
    )
    # This request carries the account token. The API answers directly, so redirects are not
    # followed: a redirect would send the Authorization header on to wherever it pointed, and the
    # only reason to accept one from an API that does not use them is to have it lead somewhere else.
    async with session.get(
        f"{_GOFILE_API}/contents/{content_id}?{params}",
        headers=headers,
        timeout=_within(session, _API_CEILING),
        allow_redirects=False,
    ) as r:
        if not r.ok:
            return []
        data = json.loads(await r.text())
    if not isinstance(data, dict) or data.get("status") != "ok":
        return []
    return gofile_files(data.get("data"), f"accountToken={token}")


def _bunkr_meta_title(soup: BeautifulSoup) -> str | None:
    tag = soup.find("meta", attrs={"property": "og:title"})
    return _attr(tag, "content") if tag else None


def _bunkr_js_vars(soup: BeautifulSoup) -> dict[str, str]:
    """The `var jsCDN = "..."` block on a file page -> `{name: value}`, quotes stripped and escaped
    slashes fixed. Empty when the page carries no such script (then the download API is used)."""
    for script in soup.find_all("script"):
        text = script.string or ""
        if "var jsCDN" in text:
            return {
                name: raw.strip().strip("\"'").replace("\\/", "/")
                for name, raw in _BUNKR_JS_VAR_RE.findall(text)
            }
    return {}


async def _bunkr_fetch_page(
    session: aiohttp.ClientSession, url: str, *, advanced: bool = False
) -> BeautifulSoup | None:
    """GET a Bunkr page, retrying across mirror hosts when one is blocked. `advanced` asks an album
    page to inline its `window.albumFiles`."""
    parsed = urlsplit(url)
    primary = parsed.hostname or _BUNKR_PAGE_HOSTS[0]
    query = "?advanced=1" if advanced else (f"?{parsed.query}" if parsed.query else "")
    for host in (primary, *(h for h in _BUNKR_PAGE_HOSTS if h != primary)):
        target = f"{parsed.scheme or 'https'}://{host}{parsed.path}{query}"
        try:
            async with session.get(target, timeout=_within(session, _PAGE_CEILING)) as r:
                if r.ok:
                    return _soup(await r.text())
        except (aiohttp.ClientError, TimeoutError):
            continue
    return None


async def _bunkr_sign(session: aiohttp.ClientSession, path: str) -> tuple[str, int] | None:
    """Sign a CDN media path -> `(token, ex)` the download URL must carry."""
    try:
        async with session.get(
            f"{_BUNKR_SIGN_ENDPOINT}?{urlencode({'path': path})}",
            headers=_BUNKR_API_HEADERS,
            timeout=_within(session, _API_CEILING),
        ) as r:
            if not r.ok:
                return None
            data = json.loads(await r.text())
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
    token, ex = data.get("token"), data.get("ex")
    return (token, ex) if isinstance(token, str) and token and isinstance(ex, int) else None


async def _bunkr_api_download(
    session: aiohttp.ClientSession, file_id: str
) -> tuple[str, str | None] | None:
    """The `_001_v2` download API: `{id}` -> `(cdn_url, original_filename)`, used when a file page
    shows only a download button (no `jsCDN` var)."""
    try:
        async with session.post(
            _BUNKR_DL_ENDPOINT,
            headers=_BUNKR_API_HEADERS,
            json={"id": file_id},
            timeout=_within(session, _API_CEILING),
        ) as r:
            if not r.ok:
                return None
            data = json.loads(await r.text())
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
    media, path = data.get("mediafiles"), data.get("path")
    if not isinstance(media, str) or not isinstance(path, str):
        return None
    src = media.rstrip("/") + (path if path.startswith("/") else f"/{path}")
    original = data.get("original")
    return src, original if isinstance(original, str) else None


async def _bunkr_finalize(
    session: aiohttp.ClientSession, src: str, name: str | None, page: str
) -> list[ExtractedFile]:
    """Sign a resolved CDN url and emit it as a named file, or nothing when signing fails (a
    deleted or expired file). A signature lasts about two hours and a large album takes longer
    than that to fetch, so each file keeps the `page` it was signed from, to be signed again."""
    base = src.split("?", 1)[0]
    signed = await _bunkr_sign(session, urlsplit(base).path)
    if signed is None:
        return []
    token, ex = signed
    fname = name or base.rsplit("/", 1)[-1]
    final = f"{base}?{urlencode({'token': token, 'ex': ex, 'n': fname})}"
    return [ExtractedFile(final, filename=fname, page=page)]


async def _bunkr_file(url: str, session: aiohttp.ClientSession) -> list[ExtractedFile]:
    """Resolve a single Bunkr file page to a signed CDN url, from the page's `jsCDN` var, or the
    download API when only a download button is present."""
    soup = await _bunkr_fetch_page(session, url)
    if soup is None or "Server under maintenance" in soup.get_text():
        return []
    cdn = _bunkr_js_vars(soup).get("jsCDN")
    if cdn:
        return await _bunkr_finalize(session, cdn, _bunkr_meta_title(soup), url)
    button = soup.select_one("a.btn.ic-download-01")
    href = _attr(button, "href") if button else None
    if not href:
        return []
    file_id = urlsplit(urljoin(url, href)).path.rstrip("/").split("/")[-1]
    got = await _bunkr_api_download(session, file_id)
    return await _bunkr_finalize(session, *got, url) if got else []


def _bunkr_album_files(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Parse the inline `window.albumFiles = [...]` JS array into dicts (unquoted keys folded to
    JSON, cyberdrop-dl's translation)."""
    text = ""
    for script in soup.find_all("script"):
        body = script.string or ""
        if "window.albumFiles" in body:
            text = body
            break
    start = text.find("[", text.find("window.albumFiles"))
    end = text.rfind("];")
    if start < 0 or end < 0:
        return []
    raw = text[start : end + 1].replace("\\'", "'")
    for field in _BUNKR_FILE_FIELDS:
        raw = raw.replace(f" {field}: ", f'"{field}": ')
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [entry for entry in data if isinstance(entry, dict)]


async def _bunkr_album(
    url: str, session: aiohttp.ClientSession, report: Report = nowhere
) -> list[ExtractedFile]:
    """Enumerate an album's files from `window.albumFiles` and resolve each via its file page (the
    per-file signing is authoritative; a dead file drops out).

    **It says how far through it is.** Listing the album is one quick request and signing each file
    is another, so a large album is minutes of work before any media is asked for, and in silence
    that is indistinguishable from a download that has hung. The listing lands in a fraction of a
    second and each file takes most of one, so the progress report is a real count from the first
    second rather than an estimate.
    """
    soup = await _bunkr_fetch_page(session, url, advanced=True)
    if soup is None:
        return []
    origin = source_origin(url).rstrip("/")
    entries = [
        entry.get("slug")
        for entry in _bunkr_album_files(soup)
        if isinstance(entry.get("slug"), str) and entry.get("slug")
    ]
    found: list[ExtractedFile] = []
    # Said before the first file rather than after it, so an album that is slow from the very start
    # has a total on screen while it is still on its first request.
    report(Progress(done_files=0, total_files=len(entries)))
    for done, slug in enumerate(entries, start=1):
        found.extend(await _bunkr_file(f"{origin}/f/{slug}", session))
        report(Progress(done_files=done, total_files=len(entries)))
    return found


async def extract_bunkr(
    url: str, session: aiohttp.ClientSession, _ctx: ExtractContext
) -> list[ExtractedFile]:
    """Resolve a Bunkr file, album, or internal download link to signed CDN addresses."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return []
    scheme = parsed.scheme or "https"
    # An internal download link (get./dl. host, /file/<id>) goes straight to the download API.
    if segments[0] == "file" and len(segments) >= 2 and (host.startswith(("get.", "dl."))):
        got = await _bunkr_api_download(session, segments[1])
        return await _bunkr_finalize(session, *got, url) if got else []
    if segments[0] == "a" and len(segments) >= 2:
        return await _bunkr_album(url, session, _ctx.report)
    # File pages: /f/<slug>; /v /d /i are stream/redirect aliases of the same file.
    if segments[0] in ("f", "v", "d", "i") and len(segments) >= 2:
        return await _bunkr_file(f"{scheme}://{host}/f/{segments[-1]}", session)
    if len(segments) == 1:  # a bare /<slug> stream redirect
        return await _bunkr_file(f"{scheme}://{host}/f/{segments[0]}", session)
    return []


def _coomer_file(
    file_info: Any, fallback_base: str, servers: dict[str, str]
) -> ExtractedFile | None:
    """Build a Coomer/Kemono CDN address + name from a file entry: `<server>/data<path>?f=<name>`. The
    per-file server is null in the current API, so the real node comes from the `servers` registry;
    when neither has it the main domain is used, which 302-redirects to a node."""
    if not isinstance(file_info, dict):
        return None
    path = file_info.get("path")
    if not isinstance(path, str) or not path:
        return None
    inline = file_info.get("server")
    server = (inline if isinstance(inline, str) and inline else servers.get(path)) or fallback_base
    name = file_info.get("name") or path.split("/")[-1]
    query = f"?{urlencode({'f': name})}" if name else ""
    return ExtractedFile(f"{server.rstrip('/')}/data{path}{query}", name or None)


def _coomer_server_registry(data: dict[str, Any]) -> dict[str, str]:
    """Map each media `path` to its CDN `server` from the response's top-level preview/attachment/
    video lists: the only place the current API carries the real per-path node."""
    servers: dict[str, str] = {}
    for key in ("previews", "attachments", "videos"):
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            path, server = item.get("path"), item.get("server")
            if isinstance(path, str) and path and isinstance(server, str) and server:
                servers.setdefault(path, server)
    return servers


def parse_coomer_post(post_data: Any, fallback_base: str) -> list[ExtractedFile]:
    """Map a Coomer/Kemono post response to its file and attachments. Handles the current wrapped
    shape (`{"post": {...}, "previews": [...]}`) and the legacy flat post-or-list shape."""
    if isinstance(post_data, dict) and "post" in post_data:
        servers = _coomer_server_registry(post_data)
        posts = [post_data.get("post")]
    else:
        posts = post_data if isinstance(post_data, list) else [post_data]
        servers = {}
    found: list[ExtractedFile] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        for info in (post.get("file"), *(post.get("attachments") or [])):
            extracted = _coomer_file(info, fallback_base, servers)
            if extracted:
                found.append(extracted)
    return found


async def _coomer_like(
    url: str,
    session: aiohttp.ClientSession,
    ctx: ExtractContext,
    *,
    api_base: str,
    cookie_domain: str,
    passthrough: tuple[str, ...],
) -> list[ExtractedFile]:
    """The shared Coomer/Kemono flow: a direct CDN link passes through; a post URL is read from the
    API (with the saved `session` cookie when one exists: an authed post needs it)."""
    parsed = urlsplit(url)
    if any(segment in parsed.path for segment in passthrough):
        return [ExtractedFile(url)]  # already a direct CDN link
    segments = parsed.path.strip("/").split("/")
    if len(segments) < 5 or segments[-2] != "post":
        return []
    service, _, user_id, _, post_id = segments[:5]
    headers = {"Accept": _COOMER_ACCEPT}
    session_cookie = load_session_cookie(ctx.cookies_file, cookie_domain)
    if session_cookie:
        headers["Cookie"] = f"session={session_cookie}"
    api_url = f"{api_base}/api/v1/{service}/user/{user_id}/post/{post_id}"
    # When a saved login is present this request carries its session cookie. The API answers
    # directly, so redirects are not followed: a redirect would carry the cookie on to another host.
    async with session.get(
        api_url, headers=headers, timeout=_within(session, _API_CEILING), allow_redirects=False
    ) as r:
        if not r.ok:
            return []
        data = json.loads(await r.text())
    return parse_coomer_post(data, api_base)


async def extract_coomer(
    url: str, session: aiohttp.ClientSession, ctx: ExtractContext
) -> list[ExtractedFile]:
    return await _coomer_like(
        url,
        session,
        ctx,
        api_base="https://coomer.st",
        cookie_domain="coomer.st",
        passthrough=("/data/", "/files/"),
    )


async def extract_kemono(
    url: str, session: aiohttp.ClientSession, ctx: ExtractContext
) -> list[ExtractedFile]:
    return await _coomer_like(
        url,
        session,
        ctx,
        api_base="https://kemono.cr",
        cookie_domain="kemono.cr",
        passthrough=("/data/", "/thumbnail/"),
    )


def find_pmvhaven_video(html: str) -> str | None:
    """The CDN video URL on a PMVHaven page: the Nuxt data array first (URLs appear as plain strings),
    then a broad HTML regex (the longest match, best quality) as a fallback."""
    soup = _soup(html)
    nuxt = soup.find("script", id="__NUXT_DATA__")
    if nuxt is not None:
        try:
            array = json.loads(nuxt.string or nuxt.get_text())
        except (ValueError, TypeError):
            array = None
        if isinstance(array, list):
            for item in array:
                if isinstance(item, str) and _PMV_VIDEO_RE.match(item):
                    return item
    matches = _PMV_FALLBACK_RE.findall(html)
    return max(matches, key=len) if matches else None


def pmvhaven_title(html: str) -> str | None:
    """The video's title from its page: the `<title>` less the site's own name, or None.

    The file's name, because the address it is stored at is not one. PMVHaven keeps a video as
    `<uploader>_-_<title>__<upload ms>_<random>.mp4`, so a file named after it and then given the
    "Creator - Name" preset would read like `someone - someone_-_A_title__1781234567890_4kq2v.mp4`.
    The page's title is the name the site shows for it, and `{name}` promises the site's own title.
    """
    soup = _soup(html)
    title = soup.title.get_text(strip=True) if soup.title is not None else ""
    return _PMVHAVEN_TITLE_SUFFIX_RE.sub("", title) or None


async def extract_pmvhaven(
    url: str, session: aiohttp.ClientSession, _ctx: ExtractContext
) -> list[ExtractedFile]:
    """A PMVHaven `/video/` page carries its CDN address in the Nuxt data; read it from the page,
    and the video's title from the same page as the file's name (`pmvhaven_title`)."""
    if "/video/" not in urlsplit(url).path:
        return []
    headers = {"Referer": "https://pmvhaven.com/", "Accept-Language": "en-US,en;q=0.9"}
    async with session.get(url, headers=headers, timeout=_within(session, _PAGE_CEILING)) as r:
        if not r.ok:
            return []
        html = await r.text()
    video = find_pmvhaven_video(html)
    if not video:
        return []
    title = pmvhaven_title(html)
    # The extension rides on the name, as HQporner's does, so a title with a dot in it is not cut
    # at that dot when the name is turned into a file name.
    extension = PurePosixPath(urlsplit(video).path).suffix
    return [ExtractedFile(video, filename=f"{title}{extension}" if title else None, title=title)]


def hqporner_player(html: str, page_url: str) -> str | None:
    """The player page a HQporner video page embeds, as an absolute address, or None.

    The iframe in `#playerWrapper` first; the address the page's own "alternative player" script
    passes along when the iframe is missing. Only an http(s) address is followed.
    """
    soup = _soup(html)
    frame = soup.select_one("#playerWrapper iframe[src]")
    src = _attr(frame, "src") if frame is not None else None
    if not src:
        match = _HQPORNER_PLAYER_PARAM_RE.search(html)
        src = match.group(1) if match else None
    if not src:
        return None
    player = urljoin(page_url, src)
    return player if urlsplit(player).scheme in ("http", "https") else None


def hqporner_title(html: str) -> str | None:
    """The video's name from its page: the `<title>` less the site's suffix, else the heading."""
    soup = _soup(html)
    title = soup.title.get_text(strip=True) if soup.title is not None else ""
    title = _HQPORNER_TITLE_SUFFIX_RE.sub("", title)
    if not title:
        heading = soup.select_one("h1.main-h1")
        title = heading.get_text(" ", strip=True) if heading is not None else ""
    return title or None


def hqporner_best_source(
    html: str, player_url: str, quality: str = QUALITY_COMPATIBLE
) -> str | None:
    """The rung of a HQporner player's quality ladder Video quality asks for, as an absolute
    address, or None (`common.pick_rung`).

    The height is read from each source's label (`1080p60`), else from its file name
    (`/1080.mp4`); a source with neither ranks below every one that says. A ladder runs
    360/720/1080, and 2160 as well on a video the site marks 4K, and the adblock
    branch of the same script names only the 360, so taking the first source, or the last, is
    the wrong answer on some page.
    """
    rungs: list[tuple[int, str]] = []
    for src, label in _HQPORNER_SOURCE_RE.findall(html):
        address = urljoin(player_url, src)
        if urlsplit(address).scheme not in ("http", "https"):
            continue
        said = _HQPORNER_LABEL_HEIGHT_RE.search(label) or _HQPORNER_FILE_HEIGHT_RE.search(
            urlsplit(address).path
        )
        rungs.append((int(said.group(1)) if said else 0, address))
    return pick_rung(rungs, quality)


async def extract_hqporner(
    url: str, session: aiohttp.ClientSession, ctx: ExtractContext
) -> list[ExtractedFile]:
    """A HQporner video page: read its embedded player page, take the source Video quality asks
    for, name it after the video.

    Neither tool reads the site (yt-dlp answers "Unsupported URL"), so this is the whole of how Sift
    fetches it.

    The player page is asked with the video page as its Referer, and that is load-bearing: asked
    without one, the player host answers **200** with a page reading "This domain has been blocked"
    and no sources at all (any Referer is accepted). A 200 holding nothing is the one answer an
    empty result cannot explain, so the header is not optional.

    Only a video page is read. A listing, a search or a page of one person's videos is not asked
    about at all, which the empty-result reader turns into "paste the link of one post or video".
    """
    if not _HQPORNER_VIDEO_PATH_RE.match(urlsplit(url).path):
        return []
    async with session.get(url, timeout=_within(session, _PAGE_CEILING)) as r:
        if not r.ok:
            return []
        page = await r.text()
    player = hqporner_player(page, url)
    if player is None:
        return []
    async with session.get(
        player, headers={"Referer": url}, timeout=_within(session, _PAGE_CEILING)
    ) as r:
        if not r.ok:
            return []
        source = hqporner_best_source(await r.text(), player, ctx.quality)
    if source is None:
        return []
    title = hqporner_title(page)
    # The extension rides on the name so a title with a dot in it ("Part 2. The End") is not cut
    # at that dot when the name is turned into a file name.
    extension = PurePosixPath(urlsplit(source).path).suffix
    return [ExtractedFile(source, filename=f"{title}{extension}" if title else None, title=title)]


def parse_sign_response(data: Any) -> tuple[str, str] | None:
    """A TurboVid `/api/sign` response -> `(signed_url, filename)`, or None when it names no URL."""
    if not isinstance(data, dict):
        return None
    signed = data.get("url")
    if not isinstance(signed, str) or not signed:
        return None
    filename = data.get("original_filename") or data.get("filename") or ""
    return signed, str(filename)


def turbovid_sign_origins(url: str) -> list[str]:
    """Where a TurboVid file is signed, nearest first: the host the address was pasted from, then
    the primary.

    The primary is only one of the site's several domains, and one domain can answer **521** (the
    origin behind the CDN down) to every request while another signs the same file at once. The
    pasted host is the one known to be up, because the person just read the page there.
    """
    own = source_origin(url).rstrip("/")
    return list(dict.fromkeys([own, _TURBOVID_PRIMARY]))


async def _turbovid_sign(
    file_id: str, session: aiohttp.ClientSession, origins: Sequence[str]
) -> ExtractedFile | None:
    """Sign one file on the first origin that answers. A host that is down, refuses, or cannot be
    reached hands the question to the next rather than ending it."""
    for origin in origins:
        try:
            async with session.get(
                f"{origin}/api/sign?v={file_id}", timeout=_within(session, _API_CEILING)
            ) as r:
                if not r.ok:
                    continue
                parsed = parse_sign_response(json.loads(await r.text()))
        except (aiohttp.ClientError, TimeoutError, ValueError):
            continue
        if parsed is None:
            continue
        signed, filename = parsed
        return ExtractedFile(signed, filename or None)
    return None


async def extract_turbovid(
    url: str, session: aiohttp.ClientSession, _ctx: ExtractContext
) -> list[ExtractedFile]:
    """A TurboVid/Saint file (`/d/`, `/v/`, `/embed/`) is signed through the API; an album (`/a/`)
    lists its files as table rows and signs each."""
    segments = [segment for segment in urlsplit(url).path.strip("/").split("/") if segment]
    origins = turbovid_sign_origins(url)
    if segments and segments[0] in ("d", "v", "embed"):
        if len(segments) < 2:
            return []
        found = await _turbovid_sign(segments[1], session, origins)
        return [found] if found else []
    if segments and segments[0] == "a":
        async with session.get(url, timeout=_within(session, _PAGE_CEILING)) as r:
            if not r.ok:
                return []
            soup = _soup(await r.text())
        found_files: list[ExtractedFile] = []
        for row in soup.select("#fileTbody tr[data-id]"):
            data_id = _attr(row, "data-id")
            if data_id:
                found = await _turbovid_sign(data_id, session, origins)
                if found:
                    found_files.append(found)
        return found_files
    return []


def parse_cyberfile_details(html: str) -> list[ExtractedFile]:
    """The tokenised direct URL out of a cyberfile `file_details` blob: the `openUrl('...')` the
    download button fires. The token is minted per request, so the address needs no cookie."""
    match = _CYBERFILE_DL_RE.search(html)
    if not match:
        return []
    direct = match.group(1)
    name = urlsplit(direct).path.rsplit("/", 1)[-1] or None
    return [ExtractedFile(direct, name)]


async def extract_cyberfile(
    url: str, session: aiohttp.ClientSession, _ctx: ExtractContext
) -> list[ExtractedFile]:
    """Resolve a cyberfile page: scrape its file id, POST it to the file_details AJAX, then read the
    tokenised direct URL from the returned HTML."""
    async with session.get(url, timeout=_within(session, _PAGE_CEILING)) as r:
        if not r.ok:
            return []
        page = await r.text()
    match = _CYBERFILE_FILEID_RE.search(page)
    if not match:
        return []
    async with session.post(
        f"{_CYBERFILE_BASE}/account/ajax/file_details",
        data={"u": match.group(1)},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=_within(session, _API_CEILING),
    ) as r:
        if not r.ok:
            return []
        detail = json.loads(await r.text())
    html = detail.get("html") if isinstance(detail, dict) else None
    return parse_cyberfile_details(html) if isinstance(html, str) else []


__all__ = [
    "extract_bunkr",
    "extract_coomer",
    "extract_cyberdrop",
    "extract_cyberfile",
    "extract_fapello",
    "extract_gofile",
    "extract_hqporner",
    "extract_jpg5",
    "extract_kemono",
    "extract_pixeldrain",
    "extract_pmvhaven",
    "extract_turbovid",
    "extract_xbunkr",
    "find_pmvhaven_video",
    "gofile_files",
    "gofile_web_token",
    "hqporner_best_source",
    "hqporner_player",
    "hqporner_title",
    "parse_coomer_post",
    "parse_cyberfile_details",
    "parse_sign_response",
    "pmvhaven_title",
    "turbovid_sign_origins",
]
