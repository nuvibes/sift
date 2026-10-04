# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one client that speaks to a stash-box, and the only thing in Sift that knows their words.

All three services Sift can be pointed at run the same software, and the types Sift reads
(`Performer`, `Studio`, `Scene`, `Fingerprint`, `Image`, `URL`) are identical across them. So this
is one adapter with three endpoints, and an endpoint is configuration rather than a code path. They
are three different VERSIONS of that software, though, so a query asks only for fields all three
have: anything one of them lacks makes the query fail for that one alone, which reads as "this
person is not in there".

## Three rules that are not negotiable

A 200 is not a success. GraphQL answers `200 OK` with an `errors` array, so a dead key comes back
looking like an empty result. `errors` is inspected before `data` on every response, or Sift would
report "not found" for a key that expired.

Nothing about a response is ever logged. `me { api_key }` returns the key itself, so a response body
in a log is a key in a log. What is logged is which box was asked, what kind of question, and
whether it worked, never the names.

Sift imposes its own pace. The services send no rate-limit header, so there is no budget to read;
the default, 240 requests a minute, is the pace Stash's own client keeps against the same services.
A `429` is a hard stop with a long backoff rather than a retry, because what gets restricted is the
account of the person running Sift.

## What it does not do

No writes: no edits, no fingerprint submissions, no votes, even though a key may carry the rights.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

from sift.kernel.access.creator_studios import Verdict, read_studio
from sift.kernel.content.perceptual import distance
from sift.kernel.http import read_capped
from sift.kernel.log import get_logger
from sift.kernel.ratelimit import HostRateLimiter, parse_retry_after
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.site_icons import is_a_label, name_for, slug_for_name
from sift.kernel.urls import about_somebody, username_in

log = get_logger(__name__)

#: How long one request may take. Generous: these are public services and a slow answer is still an
#: answer, while a short timeout turns a busy afternoon into "this person is not in there".
REQUEST_TIMEOUT_SECONDS = 20.0

#: What Stash's own client uses against these same services, and therefore what Sift uses.
DEFAULT_REQUESTS_PER_MINUTE = 240

#: How long a box is left alone after it says "too many requests" and gives no `Retry-After`.
#:
#: Fifteen minutes, where an ordinary site refusal gets five seconds: a stash-box refusing is the
#: person's own account being told to stop, which asking again does not recover.
REFUSAL_BACKOFF_SECONDS = 15 * 60.0

#: What a picture may be. An allowlist, so an answer claiming to be something else is not served
#: from Sift's own origin, which is the whole point of fetching it here rather than linking to it.
_PICTURE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"})

#: The one more type a COVER fetch takes: a studio's logo that a box keeps only as a vector.
#:
#: Only where the bytes go on to the cover door (`covers.CoverPictures.receive`), which draws the
#: SVG into a PNG in memory with nothing it names reached and re-encodes that like any picture.
#: Never for the chooser's picture route, which hands a box's bytes to a browser as they are: an
#: SVG served from Sift's own address is a document running under Sift's origin.
_VECTOR_TYPE = "image/svg+xml"

#: How much of a picture is read before it is refused. Stash-box images are photographs, not videos.
MAX_PICTURE_BYTES = 12 * 1024 * 1024


#: A fingerprint that matches everything matches nothing.
#:
#: An all-zero perceptual hash is answered with dozens of unrelated scenes, because the services'
#: matching is fuzzy and capped: the cap is what you get back when a hash is degenerate. Refused
#: before it is sent rather than filtered after, so a useless question is never asked at all.
_DEGENERATE = frozenset({"", "0" * 16, "f" * 16, "0" * 32, "f" * 32})

#: Above this many answers, a fingerprint question was a no.
#:
#: A true perceptual hash is answered with one scene, and so is an exact-file hash. A pile of
#: answers is the fuzzy matcher shrugging, and presenting a shrug as a list of candidates
#: is how somebody ends up attaching the wrong name to a file.
_TOO_MANY_TO_MEAN_ANYTHING = 5

#: How sure the adapter is that a scene is THIS file, as the one number `service.grade_of` reads.
#:
#: `EXACT` only where the scene itself carries one of the file's exact-file hashes (oshash or md5).
#: Every hash is sent in one question and the box answers with the scenes that matched ANY of them,
#: so having SENT an exact hash says nothing about why a scene came back: most come back on the
#: perceptual hash alone. The scene's own fingerprints are what say which one agreed.
EXACT = 1.0
#: The only scene the box answered with, matched on how it looks.
LONE = 0.8
#: One of several scenes matched on how it looks: a candidate somebody still has to agree with.
ONE_OF_SEVERAL = 0.4

#: How many bits two perceptual hashes may differ by and still be read as the same picture.
#:
#: The figure Stash's own tagger shows a perceptual match under. A box matches within its own
#: distance, so a scene whose nearest perceptual hash is further than this is not evidence here.
CLOSE_BITS = 8

#: The algorithms whose agreement is an identity rather than a resemblance.
_EXACT_ALGORITHMS = frozenset({"OSHASH", "MD5"})


class SessionFactory(Protocol):
    """Opens a session whose connections are vetted before they are made.

    The application's guarded connector, handed in at boot. `proxy` is how a box is reached through
    a tunnel; None is a direct connection, which is the default. There is no exempt host: a box's
    endpoint is held to the same public-only rule as every other address, because the boxes Sift
    asks are the official public ones and an endpoint on a private or loopback address is refused.
    """

    def __call__(
        self, *, user_agent: str | None = None, proxy: str | None = None
    ) -> AbstractAsyncContextManager[aiohttp.ClientSession]: ...


#: Holds one stored route (None or a tunnel id) for the length of a request and yields the
#: proxy address to use, or None for a direct connection. Raises when the tunnel is gone or down.
#:
#: The downloads' own resolver, handed in at boot, so a tunnel id means the same thing to a
#: stash-box as it does to a site: the same lookup, the same "start it if it should be running",
#: the same refusal when it is down, and the same lease that makes turning a tunnel off wait for
#: the questions already on it.
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
    """The box said "too many requests". It is now off limits for a while, and that is deliberate."""


@dataclass(frozen=True, slots=True)
class Box:
    """One configured stash-box, with its key already unsealed. Never stored in this shape."""

    id: str
    name: str
    endpoint: str
    api_key: str | None
    #: The box's way out as stored: None for direct, a tunnel id, or (on a row written before
    #: tunnels were named here) a proxy address. Never handed to the network as it stands; see
    #: `StashBoxAdapter._way_out`.
    route: str | None = None
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    #: Whether the entries this box files under STUDIOS are people rather than sites.
    #:
    #: A fact about the box, declared rather than guessed, that decides which half of the service a
    #: question is put to. On PMVStash the studios are the creators, so a person is looked up among
    #: studios. See the column of the same name for why this cannot be inferred.
    studios_are_people: bool = False


# Only fields every one of the three has. `fetchSiteFavicons` and the `SiteFavicon` type exist on
# two of them and not the third, so neither is asked for anywhere here.
#
# `scene_count`, `studios`, `created` and `updated` are asked for and Sift keeps no column for them:
# each is a statement a particular stash-box makes, and boxes disagree about every one. They live in
# the stored answer and are drawn beside the name of the box that said them when "show every field"
# is on.
#
# `death_date` is not asked for and has no column.
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

# `searchPerformers`, and NOT `searchPerformer`.
#
# The singular one is DEPRECATED on all three services, checked against each of them by asking
# their own schemas, not assumed. It still answers today, and the day it stops it will not answer
# with an error a person would recognise: a query naming a field that has gone comes back as a 200
# with an `errors` array, which is the shape this adapter turns into "that box refused the
# question". A deprecated call is a working call until it is a silent wrong answer.
#
# The plural returns a wrapper with a count beside the list rather than a bare list, so the reading
# below goes one level deeper. `limit` is asked for explicitly: without it the service picks, and a
# chooser somebody has to read is not improved by fifty entries.
_SEARCH = f"""
query($term: String!, $limit: Int!) {{
  searchPerformers(term: $term, limit: $limit) {{ performers {{ {_PERFORMER_FIELDS} }} }}
}}
"""
_BY_ID = f"query($id: ID!) {{ findPerformer(id: $id) {{ {_PERFORMER_FIELDS} }} }}"

#: How many candidates one search asks for.
#:
#: Ten. A chooser is read by a person deciding which of these is the one they mean, and past about
#: ten the answer is not further down the list: it is that the term was wrong. It also bounds the
#: pictures: every entry drawn is a fetch through the same per-host budget as the query itself.
SEARCH_LIMIT = 10

# A site, as all three call it. Exactly the fields every one of them has, asked of each service's
# own schema rather than assumed, because a query naming a field one of them lacks fails for that
# one alone and reads as "this site is not in there".
#
# `sub_studios` is deliberately not asked for. Sift has no place to put a list of children, and a
# studio with four hundred of them is four hundred entries fetched to be thrown away.
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

# A tag. `category` is ONE value with a group above it, and that is the whole of the hierarchy any
# of the three has: there are no parent tags and no sub-tags anywhere in this software, so Sift
# takes the category and does not invent a tree it could never fill.
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

# The batch shape: one GROUP of fingerprints per file, one list of candidate scenes back per group,
# in the order the groups went in. Sift sends one group, because it asks about one file at a time.
#
# Only `name` off a tag: a scene's tag is a WORD on the file, and the tag's own record is fetched
# when a tag is linked. `fingerprints` says WHY a scene came back: the box answers with every
# scene that matched any hash in the group, so without the scene's own hashes a perceptual match
# reads like an exact one. See `_evidence`. A scene fetched by its id is asked for the same fields,
# so it is read by the same mapper.
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

#: Their word for it, on the wire only. `oshash` is the exact-file hash and `phash` the perceptual
#: one; Sift computes both already and calls them by those names internally too.
_ALGORITHMS = {"oshash": "OSHASH", "phash": "PHASH", "md5": "MD5"}

#: How this adapter reads a box, as one word the answer cache is kept under.
#:
#: The cache holds records already read into Sift's words, not the box's raw replies, so an answer
#: read by an older adapter keeps the older reading until it expires. Every question this adapter
#: puts is part of the word, so a changed query changes it by itself; a change to how a reply is
#: read, with the questions left alone, moves `_READING`. See `StashBoxService._one`.
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
    """Asks a stash-box, at Sift's pace, and answers in Sift's words.

    One instance for the application. The limiter is held here rather than per box so that two
    boxes on one host (which should not happen, and which a unique endpoint mostly prevents)
    still share one pace.
    """

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
        """People a stash-box knows by this name. Empty when it knows none, which is not an error.

        On a box whose studios are people, the STUDIOS are asked instead: that is where the
        creators are, and asking performers there returns nothing however famous somebody is. The
        answer still comes back as a person, because that is what the entry describes here; the
        stash-box's own word for the shelf it keeps them on stops at this file.
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
        """Sites a stash-box knows by this name.

        Their word for one is a studio and it stops at this file: what comes back is a Site, in
        Sift's own field keys, exactly as a performer comes back as a person.

        Nothing at all on a box whose studios are people. Answering with the creators would fill
        somebody's Sites list with humans, which is the mirror of the fault this flag exists to fix,
        and an empty answer is the truth: that box has no sites in it.
        """
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

        A stored id can be superseded: a stash-box merges two entries and the old id starts
        answering with `merged_into_id` set. Followed once, because a chain of merges that loops
        is not something to hang on.

        On a box whose studios are people the id is a studio's, because `search` reads studios
        there. The search and the fetch are one question about one shelf, so they read the same one.
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
        """One scene by the id this box files it under, or None if it does not know it. Read as
        certain: the id is somebody's own answer, never a resemblance."""
        data = await self._ask(box, _SCENE_BY_ID, {"id": remote_id})
        raw = data.get("findScene")
        return _scene(box, raw, confidence=EXACT) if isinstance(raw, dict) else None

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        """What a stash-box makes of a FILE, from hashes Sift has already computed.

        Two refusals live here rather than at the caller. A degenerate hash is never sent, and an
        answer in which no scene carries one of the file's exact hashes is discarded when it holds
        a pile of entries: their perceptual matching is fuzzy and capped, so a long list means the
        matcher found nothing and hit its ceiling. Returning it would look like a rich set of
        candidates.

        How sure each scene is comes from the scene's OWN fingerprints (`_evidence`), never from
        which hashes were sent: every hash goes out in one question, so a file whose oshash no box
        has ever seen still comes back with whatever its perceptual hash resembles. The surest
        scene leads, because the caller keeps the first.
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
        # An exact-file hash on the scene is an identity. A perceptual one is strong only where it
        # is the one scene answered and its hash is close; anything else is a candidate.
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

        `vector` is True only for a fetch whose bytes go to the cover door, and lets an SVG through
        (`_VECTOR_TYPE` says why nowhere else).

        Sift's own pages are served under `img-src 'self'`, which allows no remote host at all,
        deliberately, because it is what makes injected CSS harmless: there is nowhere to send data
        to. So a picture is fetched HERE and served from Sift's own address rather than the policy
        being widened for somebody else's domain.

        Three limits, and none of them is decoration. The host must be the box's own, so a doctored
        stash-box entry cannot point this at an address on the machine's own network. The type must
        be an image. And the read stops at a cap, so a hostile or broken answer cannot be streamed
        into memory without end.

        Needs no key: stash-box images are public, and asking for one with the key attached would
        spend the key on a request that does not need it.
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
                # `read(n)` would return the first PACKET, not n bytes. See `read_capped`.
                # A portrait read that way arrives truncated: a 200, a correct content type,
                # and a picture the browser cannot finish drawing.
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

        The stored route is a tunnel id (the settings screen offers the tunnels by id), and a
        tunnel id is not an address, so it is resolved by the downloads' own resolver.

        A value that is already an address (`http://...`) is used as it stands: rows and callers
        that stored a proxy directly keep working, and an id never contains `://`.

        A tunnel that is gone or down is a box that cannot be asked, in words that name the box,
        never a fall back to a direct connection, for the same reason a download refuses: the
        setting exists so this box does not see the machine's own address.
        """
        if box.route is not None and "://" in box.route:
            yield box.route
            return
        async with AsyncExitStack() as stack:
            try:
                proxy = await stack.enter_async_context(self._through(box.route))
            except Exception as exc:
                # Only the ENTRY is caught: the opener's refusal comes from the tunnels, whose own
                # error type this slice may not import, and it is worded for a download.
                log.info("stashbox.route.unavailable", box=box.name, reason=type(exc).__name__)
                raise StashBoxUnreachable(
                    f"{box.name} is set to use a tunnel that isn't running or no"
                    " longer exists, so Sift didn't search it. Turn the tunnel on in Settings >"
                    " Sites, or choose another tunnel for it in Settings > Stash-boxes."
                ) from exc
            yield proxy

    async def _ask(self, box: Box, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """One request, paced, with `errors` read before `data`.

        The host is the throttle's key, so every question to one service queues behind the last one
        however many callers there are.
        """
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
            # The message and not the exception: a client error can carry the URL, and the URL can
            # carry a key when somebody has configured one that way.
            raise StashBoxUnreachable(f"Sift could not reach {box.name}.") from failure
        except TimeoutError as failure:
            raise StashBoxUnreachable(f"{box.name} did not answer in time.") from failure

        return _payload(box, body)


def _payload(box: Box, body: Any) -> dict[str, Any]:
    """`errors` first, `data` second, and never the body in a log.

    An unauthenticated query answers 200 with `{"errors": [{"message": "not authorized"}]}`, so
    reading `data` first makes a dead key indistinguishable from a subject that is not there.
    """
    if not isinstance(body, dict):
        raise StashBoxUnreachable(f"{box.name} sent something that was not an answer.")
    problems = body.get("errors")
    if problems:
        first = problems[0] if isinstance(problems, list) and problems else {}
        said = first.get("message") if isinstance(first, dict) else None
        # Their sentence is shown because it is the only thing that distinguishes an expired key
        # from a query this version does not understand. It is a message, never a body.
        raise StashBoxUnreachable(f"{box.name} refused the question: {said or 'no reason given'}.")
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _host_of(endpoint: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(endpoint).hostname or endpoint).lower()


#: Which sites are reference databases, and how a name is read off an address, are the kernel's
#: (`sift.kernel.urls`: `about_somebody`, `username_in`), because the catalog's repair of stored
#: usernames (v65) reads the same hosts and names and may not import this slice.


def _site_of(one: Mapping[str, Any]) -> dict[str, Any]:
    """The `site` object beside a URL, or an empty one.

    Reached for rather than assumed. Every one of the three carries it, and an entry without one is
    still an address worth keeping. Read ONLY for its address and category (`_posting_site`): its
    name is the box's word for the kind of link, and never says which Site an address is under.
    """
    site = one.get("site")
    return site if isinstance(site, dict) else {}


def _posting_site(one: Mapping[str, Any]) -> bool:
    """Whether this address is somewhere the person POSTS, as opposed to somewhere about them.

    The box's category for the site answers first where it files one. Then the site's own address
    rather than the entry's, because that is the stable half: a performer's page moves and gets
    query strings on it, and the site it belongs to does not.
    """
    site = _site_of(one)
    return not about_somebody(str(site.get("url") or one.get("url") or ""), site.get("category"))


def _accounts(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    """Their `urls[]`, as the usernames a person posts under.

    **This is the mapping that turns a flat list of addresses into rows a library can be filed by.**
    An address with a name in it, on a site somebody posts to, is a Username, and the Site it is
    under is decided by the ADDRESS'S OWN HOST, named the way Sift names that site
    (`site_icons.name_for`: "Reddit", "Kink", "Eporner").

    **NEVER BY THE `site` OBJECT BESIDE IT**, and that object is the trap. Its name is the box's
    word for the KIND of link, not for a site: "Reddit User", "Studio Profile", "Modeling Agency",
    "Artist website", "Home", "Eporner profile". Filed by it, a library would grow a Site called
    "Studio Profile" holding usernames from unrelated sites side by side, and one called "Modeling
    Agency" holding every agency: none of them a site, none with a file.

    **A host the pack does not know is answered with a blank site**, and that is not "no site": the
    library may already have a Site living at that host (a download made it), which only the writer
    can see (`catalog.site_for_address`). The writer files the entry there, or keeps the address as
    a plain link on the person when no Site lives there: a person's own domain, an agency.

    Only the sites somebody POSTS on. The reference databases in the same list become plain links,
    which is what they are: a page about somebody rather than a page by them. So does an address
    with no username in it (`_reference_links`).
    """
    out: list[dict[str, str]] = []
    for one in raw.get("urls") or []:
        if not isinstance(one, dict) or not one.get("url") or not _posting_site(one):
            continue
        url = str(one.get("url"))
        handle = username_in(url)
        if not handle:
            continue
        out.append({"site": name_for(url) or "", "handle": handle, "url": url})
    return out


def _reference_links(raw: Mapping[str, Any]) -> list[str]:
    """Their `urls[]`, minus the ones that became usernames: pages ABOUT somebody, and bare pages.

    A bare page is an address on a site somebody posts to with no username in it: a person's
    own home page at the root of their domain, a numbered page. Dropping it would lose the one
    address a person's own site is ever given as; a plain link is what it is.
    """
    return [
        str(one.get("url"))
        for one in raw.get("urls") or []
        if isinstance(one, dict)
        and one.get("url")
        and (not _posting_site(one) or not username_in(str(one.get("url"))))
    ]


def _person(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """Their performer, as Sift's person.

    Every key in `fields` is one of Sift's own field keys, so a proposal can be laid beside what
    Sift already holds with no second mapping in between.
    """
    measurements = [
        raw.get("band_size"),
        raw.get("cup_size"),
        raw.get("waist_size"),
        raw.get("hip_size"),
    ]
    fields: dict[str, object] = {
        "name": raw.get("name"),
        "aliases": [one for one in (raw.get("aliases") or []) if isinstance(one, str)],
        # In `fields` as well as on the record below, and the second is not a duplicate of the
        # first. The attribute is what a CHOOSER draws to tell two people of one name apart; this
        # is what an IMPORT writes. With only the attribute, the settings screen would offer a
        # rule for importing a disambiguation that had nothing to act on.
        "disambiguation": raw.get("disambiguation"),
        "gender": raw.get("gender"),
        "birth_date": raw.get("birth_date"),
        "country": raw.get("country"),
        "ethnicity": raw.get("ethnicity"),
        "eye_color": raw.get("eye_color"),
        "hair_color": raw.get("hair_color"),
        "height_cm": raw.get("height"),
        "measurements": "-".join(str(one) for one in measurements if one),
        "breast_type": raw.get("breast_type"),
        "career_start_year": raw.get("career_start_year"),
        "career_end_year": raw.get("career_end_year"),
        "tattoos": _marks(raw.get("tattoos")),
        "piercings": _marks(raw.get("piercings")),
        # Split, and the split is the point. A page somebody publishes to is a username on a site;
        # a page in a reference database is a link. Flattening both into one list of addresses
        # would throw away the `site` object beside each one, and that object IS the mapping.
        "links": _reference_links(raw),
        "accounts": _accounts(raw),
    }
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.PERSON,
        name=str(raw.get("name") or ""),
        disambiguation=raw.get("disambiguation") or None,
        image_url=_picture(raw.get("images")),
        # Every usable one, for starter references: a performer's pictures are of her face. A
        # studio's (on either mapping of one) are a logo, and are left to `image_url` alone.
        pictures=_pictures(raw.get("images")),
        file_count=raw.get("scene_count"),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        extra=_extra(raw, taken=_PERSON_TAKEN),
        confidence=confidence,
    )


def _site(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """Their studio, as Sift's Site.

    The parent comes across as a NAME rather than as their id. Sift's own parent is a reference to a
    Site in this library, and a stash-box's id names a row in somebody else's, so the name is
    what a person can be shown and asked about, and resolving it to a real Site is a decision
    somebody makes rather than a foreign key invented from a string.
    """
    parent = raw.get("parent") if isinstance(raw.get("parent"), dict) else {}
    own = str(raw.get("name") or "").strip()
    aliases = [one for one in (raw.get("aliases") or []) if isinstance(one, str)]
    # A network is called by its own name here; the box's spelling stays findable as an alias.
    name = own if parent else network_name(own)
    if name != own:
        aliases.append(own)
    box_parent = str((parent or {}).get("name") or "").strip()
    parent_name = network_name(box_parent) if box_parent else ""
    # A network and its flagship studio can share a name, and a Site's name never carries the
    # box's bracket, so the two are ONE Site: the flagship IS the network, and naming itself as
    # its own parent would be a loop. It has no parent; its studios name it as theirs.
    if parent_name.casefold() == (name or own).casefold():
        parent_name = ""
    fields: dict[str, object] = {
        "name": name or None,
        "aliases": aliases,
        "parent": parent_name or None,
        "links": [
            one.get("url")
            for one in (raw.get("urls") or [])
            if isinstance(one, dict) and one.get("url")
        ],
    }
    # The box's id for the parent, under both spellings, so a parent Site this answer invents is
    # linked to that studio by id and brings its record and picture (`enrich.linkable`).
    parent_id = str((parent or {}).get("id") or "")
    refs = (
        {Subject.SITE.value: dict.fromkeys((parent_name, box_parent), parent_id)}
        if parent_name and parent_id
        else {}
    )
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.SITE,
        name=str(raw.get("name") or ""),
        disambiguation=(parent or {}).get("name"),
        image_url=_picture(raw.get("images")),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        confidence=confidence,
        refs=refs,
    )


#: The bracket a box puts after a network's name to tell it from the studio of the same name.
_NETWORK_BRACKET = re.compile(r"\s*\(\s*network\s*\)\s*$", re.IGNORECASE)


def network_name(name: str) -> str:
    """What Sift calls a network a box names: its name without the box's "(Network)" bracket.

    Only the bracket goes. A network whose own name ends in Network says so on its site too, and
    keeps it. **A Site's name never carries the bracket**, not even where a network and its
    flagship studio share a name: the network is a mark on the Site (the Sites within it), never a
    word in its name, so the two are one Site (see `_site`, which gives such a flagship no parent).
    A name that is nothing but the bracket is left as it is, since taking it off leaves no name.
    """
    cleaned = name.strip()
    bare = _NETWORK_BRACKET.sub("", cleaned).strip()
    return bare or cleaned


def _person_from_studio(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """A studio entry, as Sift's PERSON, on a box that keeps its creators there.

    The same raw shape `_site` reads, mapped onto a person's fields instead. Only the three a
    studio actually carries are mapped, and the mapping is deliberately narrow:

    `aliases` are other names either way: a person's other names and a studio's are the same
    idea, and it is the field that makes somebody findable by the spelling on the files.

    `links` are where they can be found, which is true of a creator exactly as it is of a site.

    The PARENT is dropped rather than carried anywhere. On a box like this it names the network a
    creator posts under, and a person has no such field. Writing it into `disambiguation` would
    put a site's name under somebody's name on the record, which reads as another person of the
    same name being distinguished. It is still shown in the chooser, where it helps tell two
    entries apart and claims nothing.
    """
    parent = raw.get("parent") if isinstance(raw.get("parent"), dict) else {}
    fields: dict[str, object] = {
        "name": raw.get("name"),
        "aliases": [one for one in (raw.get("aliases") or []) if isinstance(one, str)],
        "links": [
            one.get("url")
            for one in (raw.get("urls") or [])
            if isinstance(one, dict) and one.get("url")
        ],
    }
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.PERSON,
        name=str(raw.get("name") or ""),
        disambiguation=(parent or {}).get("name"),
        image_url=_picture(raw.get("images")),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        confidence=confidence,
    )


def _tag(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """Their tag, as Sift's.

    The category is ONE word, taken from the category's own name. Its `group` (PEOPLE, SCENE,
    ACTION) is deliberately not carried: it is a grouping of categories rather than of tags, Sift
    has nowhere to keep it, and a second word beside the first would be a field nothing fills in.
    """
    category = raw.get("category") if isinstance(raw.get("category"), dict) else {}
    fields: dict[str, object] = {
        "name": raw.get("name"),
        "description": raw.get("description"),
        "aliases": [one for one in (raw.get("aliases") or []) if isinstance(one, str)],
        "category": (category or {}).get("name"),
    }
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.TAG,
        name=str(raw.get("name") or ""),
        disambiguation=(category or {}).get("name"),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        confidence=confidence,
    )


def _evidence(raw: Mapping[str, Any], sent: Mapping[str, str]) -> tuple[bool, int | None]:
    """Why a scene came back: whether it carries one of the exact hashes sent, and how many bits
    its nearest perceptual hash is from the one sent (None where it carries none to compare).

    Read off the scene's own fingerprints, because the question carried every hash at once and the
    answer does not say which of them matched.
    """
    exact = False
    nearest: int | None = None
    for one in raw.get("fingerprints") or ():
        if not isinstance(one, Mapping):
            continue
        algorithm = str(one.get("algorithm") or "").upper()
        value = str(one.get("hash") or "").lower()
        if not value:
            continue
        if algorithm in _EXACT_ALGORITHMS and sent.get(algorithm) == value:
            exact = True
        elif algorithm == "PHASH" and "PHASH" in sent:
            apart = distance(sent["PHASH"], value)
            if apart is not None and (nearest is None or apart < nearest):
                nearest = apart
    return exact, nearest


def _scene(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """Their scene, as one of Sift's files.

    ## On a box that keeps its creators as studios, the studio goes to `people`

    `Box.studios_are_people` is decided from the box's address in `known_boxes`. The search routes
    ask `searchStudio` on such a box, and this mapper (the one the file scan goes through) files
    the studio as a person too, or every file recognized there would name its creator as a site.

    Appended to the performers and placed first: a box can carry both the creator of the edit and
    whoever appears in it, and the creator is the primary attribution.

    ## The answer names the creator in a field of its own

    `creator` says which of the names makes the edits, so nothing downstream has to infer it from
    the order of `people`; the order still says the same thing for screens that only draw a list.
    It is absent on every other box, as `site` is absent here: the studio there is a site.

    ## A creator filed as a studio under a network is a username

    Some boxes keep every creator as a studio of their own beneath a studio for the network they
    post on (`creator_account` reads that shape). Such a studio is not a Site: it is the creator's
    username on the network's Site, and it goes to `accounts` in the shape a person's usernames
    take, with `site` absent. A studio of a producer stays in `site`.

    ## A creator's own store is her username too

    A box keeps many creators as a studio of their own with no network above it, named after
    them, its links her pages on the stores she sells on. A scene of such a studio that credits
    her by the studio's name, from a studio with a store page, is filed under her username on the
    store's Site (`creator_store`); anything less stays a Site, and Organize asks about it once
    its scenes are counted (`kernel.access.creator_studios`).

    `disambiguation` keeps the studio name either way. It is what tells two scenes of one title
    apart, which is a different job from who made it, and it is shown rather than written.
    """
    studio = raw.get("studio") if isinstance(raw.get("studio"), dict) else {}
    creator = (studio or {}).get("name") if box.studios_are_people else None
    people = [
        one.get("as") or (one.get("performer") or {}).get("name")
        for one in (raw.get("performers") or [])
        if isinstance(one, dict)
    ]
    # A creator filed as a studio under a network is a username on the network's own Site, and so
    # is a creator's own store with her credited on this scene (`creator_store`). Never on a box
    # whose studios are people: there the studio is the creator as a PERSON, read above.
    account = (
        None
        if box.studios_are_people
        else creator_account(studio or {}) or creator_store(studio or {}, _credited(raw))
    )
    # The box's own id for every one of them, kept beside the names (`FoundRecord.refs`): a person a
    # confirmed match creates needs the box's id to be linked, and the cover picture comes with the
    # link. The id of a credit is the performer's, whatever name the credit uses, so it is keyed by
    # the name that goes into `people` below.
    refs = _scene_refs(box, raw, studio or {}, creator, account)
    fields: dict[str, object] = {
        "title": raw.get("title"),
        "details": raw.get("details"),
        "release_date": raw.get("release_date"),
        "production_date": raw.get("production_date"),
        "site_code": raw.get("code"),
        "duration_ms": (raw.get("duration") or 0) * 1000 or None,
        # Absent, not empty, on a box whose studios are people: `site` is what makes the
        # enricher invent a site, and there is no site in this answer to invent one from. Absent
        # for a creator under a network too: the file is filed under their username instead,
        # which carries its Site with it.
        "site": None
        if box.studios_are_people or account is not None
        else (studio or {}).get("name"),
        # The creator's username, in the shape a person's usernames take, so one entry says the
        # Site, the name and the page together and the writer needs nothing else to file it.
        "accounts": [account.entry()] if account is not None else None,
        # Who MADE it, said in words rather than by where they sit in the list below.
        "creator": creator,
        "people": [one for one in [creator, *people] if one],
        # A word each. The tag's own description and category are its own record's business and are
        # fetched by the query that links a tag; here a tag is a word on this file.
        "tags": [
            str(one.get("name"))
            for one in (raw.get("tags") or [])
            if isinstance(one, dict) and one.get("name")
        ],
        # Under `links`, a field a file has for exactly this. `download_url` is a different
        # claim: where SIFT fetched this copy, not where the release lives.
        "links": [
            one.get("url")
            for one in (raw.get("urls") or [])
            if isinstance(one, dict) and one.get("url")
        ],
    }
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.ASSET,
        name=str(raw.get("title") or "Untitled"),
        disambiguation=(studio or {}).get("name"),
        image_url=_picture(raw.get("images")),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        confidence=confidence,
        refs=refs,
    )


def _scene_refs(
    box: Box,
    raw: Mapping[str, Any],
    studio: Mapping[str, Any],
    creator: object,
    account: CreatorAccount | None = None,
) -> dict[str, dict[str, str]]:
    """The box's id for each person, site and username a scene names, by kind and then by name.

    The studio is a PERSON on a box that keeps its creators there, a USERNAME where it is a creator
    under a network, and a SITE everywhere else, the same reading `_scene` gives its name, so the
    id lands under the kind the name does. The network's Site gets no id: the box's id is the
    creator's, and linking the Site by it would give the Site that creator's record. An entry with
    no id, or no name to key it by, is left out: an id nobody can find by name is an id nothing
    will ever read, and a name with no id is the ordinary case a search still answers.
    """
    people: dict[str, str] = {}
    sites: dict[str, str] = {}
    usernames: dict[str, str] = {}
    studio_id = str(studio.get("id") or "")
    if creator and studio_id:
        people[str(creator)] = studio_id
    elif account is not None and studio_id:
        usernames[account.handle] = studio_id
    elif not box.studios_are_people and studio.get("name") and studio_id:
        sites[str(studio["name"])] = studio_id
    for one in raw.get("performers") or []:
        if not isinstance(one, dict):
            continue
        performer = one.get("performer") if isinstance(one.get("performer"), dict) else {}
        name = one.get("as") or (performer or {}).get("name")
        remote_id = str((performer or {}).get("id") or "")
        if name and remote_id:
            people.setdefault(str(name), remote_id)
    refs: dict[str, dict[str, str]] = {}
    if people:
        refs[Subject.PERSON.value] = people
    if sites:
        refs[Subject.SITE.value] = sites
    if usernames:
        refs[USERNAME_REFS] = usernames
    return refs


#: The kind a creator's username is keyed under in `FoundRecord.refs`. Not a `Subject`: a username
#: has no record of its own on a box, so nothing links one; the id is kept with the answer.
USERNAME_REFS = "username"

#: A studio named for a creator on a network: "<handle> (<network>)", the network in brackets.
_CREATOR_STUDIO = re.compile(r"(?P<handle>[^()]+?)\s+\((?P<network>[^()]+)\)")

#: The word a box puts after a network's name, in brackets or not: "<name> (network)".
_NETWORK_WORD = re.compile(r"\s*\(?\s*network\s*\)?\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CreatorAccount:
    """A creator a box files as a studio, read as what it is: a username on a Site."""

    #: What Sift calls the network's Site ("OnlyFans"), from the creator's own page where the box
    #: gave one.
    site: str
    #: The name the creator goes by there.
    handle: str
    #: The creator's page on that Site, where the box gave one.
    url: str | None

    def entry(self) -> dict[str, str]:
        """The username as an `accounts` entry: the shape a person's usernames are offered in."""
        return {"site": self.site, "handle": self.handle, "url": self.url or ""}


def creator_account(studio: Mapping[str, Any]) -> CreatorAccount | None:
    """The username a studio stands for, or None where the studio is a studio.

    Some boxes file every creator as a studio of their own, named "<handle> (<network>)" beneath a
    studio for the network. That shape is the whole test: a parent, and the studio's own name
    carrying the parent's name in brackets. A studio of a real producer under its network (a
    label under its owner) never carries its parent's name that way, so it stays a Site.

    The Site is the one the creator's own page is on, named the way Sift names that host
    (`site_icons.name_for`), because a username's Site is its address's host. With no page on a
    host Sift knows, the network's own name serves where the icon pack names a site by it; a
    network nobody knows as a site leaves the studio a Site rather than inventing one.
    """
    parent = studio.get("parent") if isinstance(studio.get("parent"), dict) else {}
    network = _NETWORK_WORD.sub("", str((parent or {}).get("name") or "")).strip()
    shaped = _CREATOR_STUDIO.fullmatch(str(studio.get("name") or "").strip())
    if not network or shaped is None:
        return None
    if shaped["network"].strip().casefold() != network.casefold():
        return None
    handle = shaped["handle"].strip()
    for one in studio.get("urls") or []:
        url = str(one.get("url") or "").strip() if isinstance(one, dict) else ""
        site = name_for(url) if url else None
        if site:
            return CreatorAccount(site=site, handle=handle, url=url)
    if is_a_label(network) or slug_for_name(network) is None:
        return None
    return CreatorAccount(site=network, handle=handle, url=None)


def _credited(raw: Mapping[str, Any]) -> list[str]:
    """Every name a scene credits a performer by: the name it used, and the performer's own."""
    names: list[str] = []
    for one in raw.get("performers") or []:
        if not isinstance(one, dict):
            continue
        performer = one.get("performer") if isinstance(one.get("performer"), dict) else {}
        for name in (one.get("as"), (performer or {}).get("name")):
            if isinstance(name, str) and name.strip():
                names.append(name)
    return names


def creator_store(studio: Mapping[str, Any], credited: list[str]) -> CreatorAccount | None:
    """The username a creator's own studio stands for, read off ONE scene; None for a Site.

    Her store page among the studio's links, and her credited on this scene by the studio's name
    or an alias: both signs at once (`creator_studios.read_studio` over one scene). The Site is the
    store's, named the way Sift names its host, and the handle is the name her page there carries.
    """
    name = str(studio.get("name") or "").strip()
    if not name:
        return None
    aliases = [one for one in (studio.get("aliases") or []) if isinstance(one, str)]
    links = [
        str(one.get("url"))
        for one in (studio.get("urls") or [])
        if isinstance(one, dict) and one.get("url")
    ]
    reading = read_studio(name, aliases, links, [credited])
    if reading.verdict is not Verdict.USERNAME or reading.home is None:
        return None
    home = reading.home
    return CreatorAccount(site=home.site, handle=home.handle, url=home.url)


#: Every key of a performer that lands somewhere in Sift, so `_extra` can report the rest.
#:
#: Written out rather than derived. The mapping above is not one-to-one (four measurement keys
#: become one field, two url keys become links and usernames, `id` becomes the remote id), so
#: nothing could work this out from the field names, and a rule that tried would quietly start
#: reporting a mapped field as unmapped the first time a mapping got cleverer.
_PERSON_TAKEN = frozenset(
    {
        "id",
        "name",
        "disambiguation",
        "aliases",
        "gender",
        "birth_date",
        "country",
        "ethnicity",
        "eye_color",
        "hair_color",
        "height",
        "cup_size",
        "band_size",
        "waist_size",
        "hip_size",
        "breast_type",
        "career_start_year",
        "career_end_year",
        "tattoos",
        "piercings",
        "images",
        "urls",
        "merged_into_id",
    }
)


def _extra(raw: Mapping[str, Any], *, taken: frozenset[str]) -> dict[str, object]:
    """Everything the stash-box sent that nothing in Sift claims.

    Kept so the "show every field" switch has something honest to show: what was actually said,
    under the stash-box's own key, rather than a subset somebody remembered to map.

    Empty values are dropped for the reason `fields` drops them (a row reading nothing is a row
    worth not drawing), and so is an empty list or an empty object, which is what a stash-box
    sends for "we hold none of these".
    """
    out: dict[str, object] = {}
    for key, value in raw.items():
        if key in taken or value is None or value == "" or value == [] or value == {}:
            continue
        out[key] = value
    return out


def ranked(found: list[FoundRecord], term: str) -> list[FoundRecord]:
    """The entries that answer everything that was typed, first, and the rest marked as not.

    Public, because the service applies it to a CACHED answer as well as a fresh one: the mark is
    worked out from the term and the record, not stored. A cached row read without it would mark
    every entry as certain (ten "Neve"s for "Neve Arbogast"), which would make every unattended
    enrichment decline the name as ambiguous.

    ## What is actually wrong with the list that comes back

    A stash-box's search is built to complete a word somebody is typing, so it matches on ANY of
    them. Ask one for a two-word name and it answers with ten entries: the person, and nine others
    who share a first name or a surname with them. The right one is usually first (their ranking
    is fine), but the nine below it look exactly as much like answers, and a chooser that a person
    has to read nine wrong rows out of is a chooser that gets misread.

    ## Why they are marked rather than dropped

    Because the fuzziness is sometimes doing exactly what it is for. A person filed on that service
    under a spelling nobody here would type (an accent, a middle name, a maiden name) comes back
    only through the fuzzy half, and dropping it would make them unreachable through Sift while the
    service itself finds them. So every entry comes back and the screen draws the rest behind a
    press, with a count.

    Matched against the name, what tells two of that name apart, and every other name the entry
    goes by, because "any of them finds it" is the rule everywhere else in Sift, and a person
    typing a stage name should not be told there is no such person.
    """
    words = [one for one in term.casefold().split() if one]
    if not words:
        return found
    marked = [
        dataclasses.replace(one, every_word=all(word in _every_name(one) for word in words))
        for one in found
    ]
    # A stable partition rather than a sort: within each half the service's own ranking is kept,
    # because their relevance order is better than anything that could be worked out from a name.
    return [one for one in marked if one.every_word] + [one for one in marked if not one.every_word]


def _every_name(found: FoundRecord) -> str:
    """Every spelling an entry answers to, folded and run together, for a substring test."""
    aliases = found.fields.get("aliases")
    spellings = [found.name, found.disambiguation or ""]
    if isinstance(aliases, list):
        spellings.extend(str(one) for one in aliases)
    return " ".join(spellings).casefold()


def _marks(raw: Any) -> list[str]:
    """Tattoos and piercings, each as one readable line."""
    out = []
    for one in raw or []:
        if not isinstance(one, dict):
            continue
        where, what = one.get("location"), one.get("description")
        out.append(f"{where}: {what}" if where and what else str(where or what or ""))
    return [one for one in out if one]


def _picture(images: Any) -> str | None:
    """The largest usable picture, and never by dimensions alone.

    A stash-box reports `width: -1` on some images, so sorting by size would put those first. They
    are ranked AFTER every picture with positive dimensions instead (see `_pictures`).
    """
    every = _pictures(images)
    return every[0] if every else None


def _pictures(images: Any) -> tuple[str, ...]:
    """Every usable picture, largest first, then every picture the box gives no size for.

    Its first is `_picture`'s answer, so the one picture a chooser shows and the list are one
    reading of the reply.

    **A picture with no size is ranked last, never dropped.** A box reports `width: -1` for a
    vector: a studio whose only logo is an SVG has exactly one such picture and nothing else, and
    dropping it would leave such a Site with no picture at all. Last, so a raster of any size wins
    wherever there is one; and a vector is fetched only where the cover door can draw it
    (`_VECTOR_TYPE`), so anywhere else it is a picture that will not come, the same as none.
    """
    sized: list[dict[str, Any]] = []
    unsized: list[dict[str, Any]] = []
    for one in images or []:
        if not isinstance(one, dict) or not one.get("url"):
            continue
        width, height = one.get("width"), one.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            sized.append(one)
        else:
            unsized.append(one)
    # Stable, so two of one size keep the box's own order, and so do the unsized.
    ranked = sorted(sized, key=lambda one: -(one["width"] * one["height"])) + unsized
    return tuple(dict.fromkeys(str(one["url"]) for one in ranked))


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
                # Kept, or a match read back out of Sift's own table has lost the ids a row it
                # invents is linked by. See `FoundRecord.refs`.
                "refs": {kind: dict(named) for kind, named in one.refs.items()},
            }
            for one in records
        ]
    )


def from_json(payload: str) -> list[FoundRecord]:
    """A KEPT answer, back in the shape the rest of Sift reads. Nothing at all if it will not read.

    **Every caller of this reads a row out of Sift's own database, and every one of them is
    written to cope with an empty answer**: `links_of` skips the row, `linked_subjects` skips it,
    and `_match` substitutes a placeholder record. So this answers empty rather than raising where
    `json.loads` would raise on a truncated write, `one["name"]` on a record written by a version
    that named the field something else, and `Subject(...)` on a kind this build does not have.

    That is not a small failure. A single unreadable link row would take out the reconcile screen,
    the pile waiting to be enriched, and therefore the WHOLE Organize board (every panel on it,
    including the ones with nothing wrong). One row must not do that.

    Logged rather than swallowed silently, because a record that cannot be read is a real thing to
    know about even though it is not a reason to refuse a screen.

    Fresh answers off the network do NOT come through here; they are built by the adapter from the
    reply. So there is no case where this hides a parsing fault in something Sift has just been
    handed: it only ever forgives something Sift itself stored.
    """
    try:
        raw = json.loads(payload)
        return _records_in(raw)
    except (ValueError, KeyError, TypeError) as unreadable:
        log.warning("stash_box.kept_record_unreadable", error=str(unreadable))
        return []


def _subject_of(word: str) -> Subject:
    """The kind a kept record names, in this build's words. Raises on a word nobody ever used.

    A retired word in kept JSON is rewritten in the rows by a schema step (v14 rewrote the old word
    for a Site to `site`) rather than translated on every read, which would keep the old word alive
    and hide the next one. So this reads exactly what this build writes, and a word nobody writes is
    logged by `from_json` as the unreadable record it is.
    """
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
    """The ids a kept record carries. Nothing where it carries none (every record kept before
    they were), and nothing of a shape this build did not write, rather than a record that will
    not read: an unreadable id costs a link, and an unreadable record costs a whole screen."""
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
