# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one client that speaks to a stash-box; `reading` puts its replies into Sift's words.

The three services run different versions of the same software, so a query asks only for fields all
three have. `errors` is read before `data`, since a dead key answers 200. No response is ever
logged, as one can carry the key. Sift keeps its own pace and treats a 429 as a long stop. It never
writes to a box.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

from sift.kernel.http import read_capped
from sift.kernel.log import get_logger
from sift.kernel.ratelimit import HostRateLimiter, parse_retry_after
from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.reading import USERNAME_REFS as USERNAME_REFS
from sift.slices.stash_boxes.reading import _evidence as _evidence
from sift.slices.stash_boxes.reading import (
    _host_of,
    _person,
    _person_from_studio,
    _scene,
    _site,
    _tag,
)
from sift.slices.stash_boxes.reading import _picture as _picture
from sift.slices.stash_boxes.reading import _pictures as _pictures
from sift.slices.stash_boxes.reading import creator_account as creator_account
from sift.slices.stash_boxes.reading import network_name as network_name
from sift.slices.stash_boxes.reading import ranked as ranked

log = get_logger(__name__)

#: How long one request may take; a short timeout would read a busy service as "not in there".
REQUEST_TIMEOUT_SECONDS = 20.0

#: The pace Stash's own client keeps against these same services.
DEFAULT_REQUESTS_PER_MINUTE = 240

#: How long a box is left alone after a 429 with no `Retry-After`: its account was told to stop.
REFUSAL_BACKOFF_SECONDS = 15 * 60.0

#: What a picture may be, so nothing else is served from Sift's own origin.
_PICTURE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"})

#: The one more type a cover fetch takes, a studio's vector logo, since the cover door redraws it
#: (`covers.CoverPictures.receive`); never for the chooser's picture route.
_VECTOR_TYPE = "image/svg+xml"

MAX_PICTURE_BYTES = 12 * 1024 * 1024


#: An all-zero perceptual hash matches dozens of unrelated scenes, so it is never sent.
_DEGENERATE = frozenset({"", "0" * 16, "f" * 16, "0" * 32, "f" * 32})

#: Above this many answers, a fingerprint question was the fuzzy matcher hitting its cap.
_TOO_MANY_TO_MEAN_ANYTHING = 5

#: How sure the adapter is that a scene is this file, read by `service.grade_of`; `EXACT` only where
#: the scene itself carries one of the file's exact-file hashes.
EXACT = 1.0
#: The only scene the box answered with, matched on how it looks.
LONE = 0.8
#: One of several scenes matched on how it looks: a candidate somebody still has to agree with.
ONE_OF_SEVERAL = 0.4

#: How many bits two perceptual hashes may differ by and still match, as Stash's tagger shows.
CLOSE_BITS = 8


class SessionFactory(Protocol):
    """Opens a session whose connections are vetted before they are made; `proxy` None is direct."""

    def __call__(
        self, *, user_agent: str | None = None, proxy: str | None = None
    ) -> AbstractAsyncContextManager[aiohttp.ClientSession]: ...


#: Holds one stored route for a request and yields the proxy address, or None for direct; the
#: downloads' own resolver, so a tunnel id means the same here.
RouteOpener = Callable[[str | None], AbstractAsyncContextManager[str | None]]


@asynccontextmanager
async def _no_tunnels(route: str | None) -> AsyncIterator[str | None]:
    """The route opener when none is handed in: direct only, and a tunnel id refuses."""
    if route is not None:
        raise LookupError("no tunnels are available here")
    yield None


class StashBoxUnreachable(Exception):
    """The box could not be asked, or refused to answer. The message is meant to be read."""


class StashBoxRefused(StashBoxUnreachable):
    """The box said "too many requests" and is off limits for a while."""


@dataclass(frozen=True, slots=True)
class Box:
    """One configured stash-box, with its key already unsealed. Never stored in this shape."""

    id: str
    name: str
    endpoint: str
    api_key: str | None
    #: The box's way out as stored: None, a tunnel id, or an older proxy address. See `_way_out`.
    route: str | None = None
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    #: Whether this box keeps people under studios, declared per box (PMVStash's studios are
    #: creators).
    studios_are_people: bool = False


# Only fields all three have. `scene_count`, `studios`, `created` and `updated` are kept in the
# stored answer only, since boxes disagree about them.
_PERFORMER_FIELDS = """
  id
  name
  disambiguation
  aliases
  gender
  birth_date
  country
  ethnicity
  eye_color
  hair_color
  height
  cup_size
  band_size
  waist_size
  hip_size
  breast_type
  career_start_year
  career_end_year
  tattoos { location description }
  piercings { location description }
  images { id url width height }
  urls { url site { id name url category { name } } }
  scene_count
  studios { studio { id name } scene_count }
  created
  updated
  merged_into_id
"""

# `searchPerformers`, not the deprecated singular, which would fail as a silent 200 with errors.
_SEARCH = f"""
query($term: String!, $limit: Int!) {{
  searchPerformers(term: $term, limit: $limit) {{ performers {{ {_PERFORMER_FIELDS} }} }}
}}
"""
_BY_ID = f"query($id: ID!) {{ findPerformer(id: $id) {{ {_PERFORMER_FIELDS} }} }}"

#: How many candidates one search asks for: a chooser past ten means the term was wrong.
SEARCH_LIMIT = 10

# A site, by exactly the fields all three have; `sub_studios` is not asked for.
_STUDIO_FIELDS = """
  id
  name
  aliases
  urls { url site { id name url } }
  parent { id name }
  images { id url width height }
"""

_SEARCH_STUDIO = (
    f"query($term: String!, $limit: Int!)"
    f" {{ searchStudio(term: $term, limit: $limit) {{ {_STUDIO_FIELDS} }} }}"
)
_STUDIO_BY_ID = f"query($id: ID!) {{ findStudio(id: $id) {{ {_STUDIO_FIELDS} }} }}"

# A tag; `category` is the whole of the hierarchy any of the three has.
_TAG_FIELDS = """
  id
  name
  description
  aliases
  category { id name group }
"""

_SEARCH_TAG = (
    f"query($term: String!, $limit: Int!)"
    f" {{ searchTag(term: $term, limit: $limit) {{ {_TAG_FIELDS} }} }}"
)
_TAG_BY_ID = f"query($id: ID!) {{ findTag(id: $id) {{ {_TAG_FIELDS} }} }}"

# One group of fingerprints per file and a list of scenes back per group. A scene's own
# `fingerprints` say why it came back; see `_evidence`.
_SCENE_FIELDS = """
    id
    title
    details
    release_date
    production_date
    duration
    code
    urls { url site { id name url } }
    studio { id name aliases parent { id name } urls { url } }
    tags { name }
    images { id url width height }
    performers { as performer { id name } }
    fingerprints { hash algorithm duration }
"""
_BY_FINGERPRINT = (
    "\nquery($groups: [[FingerprintQueryInput!]!]!) {\n"
    "  findScenesBySceneFingerprints(fingerprints: $groups) {" + _SCENE_FIELDS + "  }\n}\n"
)
_SCENE_BY_ID = f"query($id: ID!) {{ findScene(id: $id) {{ {_SCENE_FIELDS} }} }}"

#: Their word for it, on the wire only: `oshash` the exact-file hash, `phash` the perceptual one.
_ALGORITHMS = {"oshash": "OSHASH", "phash": "PHASH", "md5": "MD5"}

#: How this adapter reads a box, as the word the answer cache is kept under. Every query is part of
#: it; a change to how a reply is read moves `_READING`.
_READING = 1
ANSWER_SHAPE = hashlib.sha256(
    "\n".join(
        (
            str(_READING),
            _SEARCH,
            _BY_ID,
            _SEARCH_STUDIO,
            _STUDIO_BY_ID,
            _SEARCH_TAG,
            _TAG_BY_ID,
            _BY_FINGERPRINT,
        )
    ).encode("utf-8")
).hexdigest()[:16]


class StashBoxAdapter:
    """Asks a stash-box, at Sift's pace, and answers in Sift's words; one limiter for every box."""

    def __init__(
        self,
        open_session: SessionFactory,
        *,
        through: RouteOpener = _no_tunnels,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._open_session = open_session
        self._through = through
        self._limiter = HostRateLimiter(clock=clock)
        self._clock = clock

    async def search(self, box: Box, term: str) -> list[FoundRecord]:
        """People a stash-box knows by this name; on a box whose studios are people, the studios are
        asked.
        """
        term = term.strip()
        if not term:
            return []
        if box.studios_are_people:
            data = await self._ask(box, _SEARCH_STUDIO, {"term": term, "limit": SEARCH_LIMIT})
            found = data.get("searchStudio") or []
            return ranked(
                [
                    _person_from_studio(box, one, confidence=0.5)
                    for one in found
                    if isinstance(one, dict)
                ],
                term,
            )
        data = await self._ask(box, _SEARCH, {"term": term, "limit": SEARCH_LIMIT})
        wrapper = data.get("searchPerformers")
        found_people = wrapper.get("performers") if isinstance(wrapper, dict) else None
        return ranked(
            [
                _person(box, one, confidence=0.5)
                for one in (found_people or [])
                if isinstance(one, dict)
            ],
            term,
        )

    async def search_sites(self, box: Box, term: str) -> list[FoundRecord]:
        """Sites a stash-box knows by this name; nothing on a box whose studios are people."""
        term = term.strip()
        if not term or box.studios_are_people:
            return []
        data = await self._ask(box, _SEARCH_STUDIO, {"term": term, "limit": SEARCH_LIMIT})
        found = data.get("searchStudio") or []
        return ranked(
            [_site(box, one, confidence=0.5) for one in found if isinstance(one, dict)], term
        )

    async def search_tags(self, box: Box, term: str) -> list[FoundRecord]:
        """Tags a stash-box knows by this name."""
        term = term.strip()
        if not term:
            return []
        data = await self._ask(box, _SEARCH_TAG, {"term": term, "limit": SEARCH_LIMIT})
        found = data.get("searchTag") or []
        return ranked(
            [_tag(box, one, confidence=0.5) for one in found if isinstance(one, dict)], term
        )

    async def site(self, box: Box, remote_id: str) -> FoundRecord | None:
        """One site by the id this box files it under, or None if it does not know it."""
        data = await self._ask(box, _STUDIO_BY_ID, {"id": remote_id})
        raw = data.get("findStudio")
        return _site(box, raw, confidence=1.0) if isinstance(raw, dict) else None

    async def tag(self, box: Box, remote_id: str) -> FoundRecord | None:
        """One tag by the id this box files it under, or None if it does not know it."""
        data = await self._ask(box, _TAG_BY_ID, {"id": remote_id})
        raw = data.get("findTag")
        return _tag(box, raw, confidence=1.0) if isinstance(raw, dict) else None

    async def person(self, box: Box, remote_id: str) -> FoundRecord | None:
        """One person by the id this box files them under, or None if it does not know it.

        A merged id is followed once. On a box whose studios are people the id is a studio's.
        """
        if box.studios_are_people:
            data = await self._ask(box, _STUDIO_BY_ID, {"id": remote_id})
            raw_studio = data.get("findStudio")
            if not isinstance(raw_studio, dict):
                return None
            return _person_from_studio(box, raw_studio, confidence=1.0)
        data = await self._ask(box, _BY_ID, {"id": remote_id})
        raw = data.get("findPerformer")
        if not isinstance(raw, dict):
            return None
        moved = raw.get("merged_into_id")
        if isinstance(moved, str) and moved and moved != remote_id:
            data = await self._ask(box, _BY_ID, {"id": moved})
            raw = data.get("findPerformer")
            if not isinstance(raw, dict):
                return None
        return _person(box, raw, confidence=1.0)

    async def scene(self, box: Box, remote_id: str) -> FoundRecord | None:
        """One scene by the id this box files it under, or None; read as certain."""
        data = await self._ask(box, _SCENE_BY_ID, {"id": remote_id})
        raw = data.get("findScene")
        return _scene(box, raw, confidence=EXACT) if isinstance(raw, dict) else None

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        """What a stash-box makes of a file, from hashes Sift has already computed.

        A degenerate hash is never sent, and a pile of answers with no exact match is discarded. How
        sure each scene is comes from its own fingerprints (`_evidence`); the surest leads.
        """
        groups = [
            {"hash": value.lower(), "algorithm": _ALGORITHMS[name]}
            for name, value in hashes.items()
            if name in _ALGORITHMS and value and value.lower() not in _DEGENERATE
        ]
        if not groups:
            return []

        data = await self._ask(box, _BY_FINGERPRINT, {"groups": [groups]})
        batches = data.get("findScenesBySceneFingerprints") or []
        first = batches[0] if batches and isinstance(batches[0], list) else []
        scenes = [one for one in first if isinstance(one, dict)]
        sent = {one["algorithm"]: one["hash"] for one in groups}
        read = [(one, *_evidence(one, sent)) for one in scenes]

        if not any(exact for _, exact, _ in read) and len(scenes) > _TOO_MANY_TO_MEAN_ANYTHING:
            log.info(
                "stashbox.recognise.too_many",
                box=box.name,
                answers=len(scenes),
                reason="perceptual-only answer above the ceiling reads as no match",
            )
            return []
        # An exact-file hash on the scene is an identity; a perceptual one is strong only alone and
        # near.
        found = [
            _scene(
                box,
                one,
                confidence=EXACT
                if exact
                else LONE
                if len(scenes) == 1 and nearest is not None and nearest <= CLOSE_BITS
                else ONE_OF_SEVERAL,
            )
            for one, exact, nearest in sorted(
                read, key=lambda each: (not each[1], CLOSE_BITS + 1 if each[2] is None else each[2])
            )
        ]
        return found

    async def picture(
        self, box: Box, url: str, *, vector: bool = False
    ) -> tuple[bytes, str] | None:
        """Fetch one picture from a stash-box, for Sift to serve itself. None if it will not come.

        Served from Sift's own address so `img-src 'self'` holds. The host must be the box's own,
        the type an image (`vector` lets an SVG through for the cover door), and the read is capped.
        No key needed.
        """
        if _host_of(url) != _host_of(box.endpoint):
            log.info("stashbox.picture.refused", box=box.name, reason="not the box's own host")
            return None

        self._limiter.pace(_host_of(box.endpoint), 60.0 / max(1, box.requests_per_minute))
        await self._limiter.acquire(_host_of(box.endpoint))
        try:
            async with (
                self._way_out(box) as proxy,
                self._open_session(proxy=proxy) as session,
                session.get(
                    url,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as answer,
            ):
                kind = (answer.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                wanted = _PICTURE_TYPES | {_VECTOR_TYPE} if vector else _PICTURE_TYPES
                if answer.status != 200 or kind not in wanted:
                    return None
                # `read(n)` would return the first packet only, truncating the picture (see
                # `read_capped`).
                body = await read_capped(answer.content, MAX_PICTURE_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError, StashBoxUnreachable):
            # A picture that will not come is a missing picture, never a broken screen.
            return None

        if len(body) > MAX_PICTURE_BYTES:
            log.info("stashbox.picture.refused", box=box.name, reason="over the size cap")
            return None
        return body, kind

    @asynccontextmanager
    async def _way_out(self, box: Box) -> AsyncIterator[str | None]:
        """Hold this box's route for one request and yield the proxy address, or None for direct.

        A tunnel id is resolved by the downloads' resolver; a value with `://` is used as it stands.
        A tunnel that is gone or down refuses, never falling back to direct.
        """
        if box.route is not None and "://" in box.route:
            yield box.route
            return
        async with AsyncExitStack() as stack:
            try:
                proxy = await stack.enter_async_context(self._through(box.route))
            except Exception as exc:
                # Only the entry is caught: the opener's refusal is worded for a download.
                log.info("stashbox.route.unavailable", box=box.name, reason=type(exc).__name__)
                raise StashBoxUnreachable(
                    f"{box.name} is set to use a tunnel that isn't running or no"
                    " longer exists, so Sift didn't search it. Turn the tunnel on in Settings >"
                    " Sites, or choose another tunnel for it in Settings > Stash-boxes."
                ) from exc
            yield proxy

    async def _ask(self, box: Box, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """One request, paced per host, with `errors` read before `data`."""
        host = _host_of(box.endpoint)
        self._limiter.pace(host, 60.0 / max(1, box.requests_per_minute))
        await self._limiter.acquire(host)

        headers = {"Content-Type": "application/json"}
        if box.api_key:
            headers["ApiKey"] = box.api_key

        try:
            async with (
                self._way_out(box) as proxy,
                self._open_session(proxy=proxy) as session,
                session.post(
                    box.endpoint,
                    json={"query": query, "variables": variables},
                    headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as answer,
            ):
                if answer.status == 429:
                    wait = parse_retry_after(answer.headers.get("Retry-After"))
                    self._limiter.note_retry_after(host, wait or REFUSAL_BACKOFF_SECONDS)
                    log.warning(
                        "stashbox.refused", box=box.name, backoff_s=wait or REFUSAL_BACKOFF_SECONDS
                    )
                    raise StashBoxRefused(
                        f"{box.name} asked Sift to slow down, so Sift waits before searching it"
                        " again. Searching sooner would not help, because the limit is on your key."
                    )
                if answer.status >= 400:
                    raise StashBoxUnreachable(f"{box.name} answered {answer.status}.")
                body = await answer.json(content_type=None)
        except aiohttp.ClientError as failure:
            # The message, not the exception: a client error can carry a URL holding a key.
            raise StashBoxUnreachable(f"Sift could not reach {box.name}.") from failure
        except TimeoutError as failure:
            raise StashBoxUnreachable(f"{box.name} did not answer in time.") from failure

        return _payload(box, body)


def _payload(box: Box, body: Any) -> dict[str, Any]:
    """`errors` first, `data` second, and never the body in a log."""
    if not isinstance(body, dict):
        raise StashBoxUnreachable(f"{box.name} sent something that was not an answer.")
    problems = body.get("errors")
    if problems:
        first = problems[0] if isinstance(problems, list) and problems else {}
        said = first.get("message") if isinstance(first, dict) else None
        # Their sentence tells an expired key from a query this version does not understand.
        raise StashBoxUnreachable(f"{box.name} refused the question: {said or 'no reason given'}.")
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def as_json(records: list[FoundRecord]) -> str:
    """Cacheable form. A dataclass with a mapping in it is not JSON on its own."""
    return json.dumps(
        [
            {
                "source_id": one.source_id,
                "remote_id": one.remote_id,
                "subject": str(one.subject),
                "name": one.name,
                "disambiguation": one.disambiguation,
                "image_url": one.image_url,
                "pictures": list(one.pictures),
                "file_count": one.file_count,
                "fields": dict(one.fields),
                "confidence": one.confidence,
                # Kept, or a match read back has lost the ids its invented rows are linked by.
                "refs": {kind: dict(named) for kind, named in one.refs.items()},
            }
            for one in records
        ]
    )


def from_json(payload: str) -> list[FoundRecord]:
    """A kept answer, back in the shape the rest of Sift reads. Nothing at all if it will not read.

    Every caller reads Sift's own rows and copes with empty, and one bad row must not take out the
    Organize board, so this logs and answers empty. Fresh answers never come through here.
    """
    try:
        raw = json.loads(payload)
        return _records_in(raw)
    except (ValueError, KeyError, TypeError) as unreadable:
        log.warning("stash_box.kept_record_unreadable", error=str(unreadable))
        return []


def _subject_of(word: str) -> Subject:
    """The kind a kept record names, in this build's words; raises on a word nobody ever used."""
    return Subject(word)


def _records_in(raw: object) -> list[FoundRecord]:
    """The records in a decoded payload. Raises on anything it cannot read; see `from_json`."""
    if not isinstance(raw, list):
        raise TypeError("a kept answer is a list of records")
    return [
        FoundRecord(
            source_id=one["source_id"],
            remote_id=one["remote_id"],
            subject=_subject_of(one["subject"]),
            name=one["name"],
            disambiguation=one.get("disambiguation"),
            image_url=one.get("image_url"),
            pictures=tuple(str(url) for url in (one.get("pictures") or []) if url),
            file_count=one.get("file_count"),
            fields=one.get("fields") or {},
            confidence=one.get("confidence") or 0.0,
            refs=_refs_in(one.get("refs")),
        )
        for one in raw
    ]


def _refs_in(raw: object) -> dict[str, dict[str, str]]:
    """The ids a kept record carries, or nothing where it carries none or of an unknown shape."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for kind, named in raw.items():
        if not isinstance(named, dict):
            continue
        kept = {str(name): str(remote_id) for name, remote_id in named.items() if remote_id}
        if kept:
            out[str(kind)] = kept
    return out
