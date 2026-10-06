# SPDX-License-Identifier: AGPL-3.0-or-later
"""A synthetic library of any size, shaped like a real one in its counts and ratios.

The real schema through the real migrations, then rows in the proportions a lived-in library has:
a few roots and a long-tailed folder spread, people on two files in three with one person on a
tenth of them, tags, Photo Sets, Sites and Usernames, songs, jobs, faces, the Smart Search index,
audio fingerprints, plays, Organize decisions, duplicate pairs, one admin and five guests who each
see about a fifth. Names come from the invented cast. The same seed builds the same library.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import random
import struct
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sift.main  # noqa: F401 (every component registers itself)
from sift.kernel.access import Repository, Role, Viewer, visibility
from sift.kernel.access.constraints import NO_FILTER, AssetFilter, Where
from sift.kernel.access.repository.assets import (
    assets_query,
    drive_for,
    point_query,
    seek_anchor,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key

#: What one file brings with it, per file, as a library of about a hundred thousand has them.
LOCATIONS_PER_FILE = 1.16
FOLDERS_PER_FILE = 146 / 102_579
PEOPLE_PER_FILE = 887 / 102_579
FILES_WITH_A_PERSON = 0.68
SECOND_PERSON = 0.03
#: The most-filed person's share of every person link.
TOP_PERSON_SHARE = 0.116
TAG_LINKS_PER_FILE = 1_827 / 102_579
PHOTO_SET_LINKS_PER_FILE = 70_692 / 102_579
COLLECTION_LINKS_PER_FILE = 68 / 102_579
USERNAME_LINKS_PER_FILE = 9_768 / 102_579
SONG_LINKS_PER_FILE = 137 / 102_579
JOBS_PER_FILE = 37_048 / 102_579
FACE_TRACKS_PER_FILE = 15_047 / 102_579
FACE_DETECTIONS_PER_TRACK = 22_810 / 15_047
FACE_SCANS_PER_FILE = 19_618 / 102_579
FACE_REFERENCES_PER_PERSON = 8_510 / 887
SEMANTIC_INDEXED_PER_FILE = 18_910 / 102_579
SEMANTIC_POOLED_PER_FILE = 3_913 / 102_579
FINGERPRINTED_PER_FILE = 409 / 102_579
KEYS_PER_FINGERPRINT = 980
DERIVATIVES_PER_FILE = 1.10
PLAYS_PER_FILE = 4_109 / 102_579
DECISIONS_PER_FILE = 44_907 / 102_579
SUBJECTS_PER_DECISION = 60_753 / 44_907
DUPLICATES_PER_FILE = 37_132 / 102_579
FILE_STATES_PER_FILE = 3_702 / 102_579

ROOTS = 14
TAGS = 64
PHOTO_SETS = 66
COLLECTIONS = 16
SITES = 54
USERNAMES = 107
SONGS = 99
GUESTS = 5
#: What each guest is shared, as a share of the library's files.
GUEST_SHARE = 0.2

#: Every row is dated from this moment, so the same seed gives the same rows.
EPOCH = 1_700_000_000

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: A face description's width, as packed 32-bit floats.
_EMBEDDING_FLOATS = 512

_CAST = Path(__file__).resolve().parents[3] / "tests" / "gates" / "data" / "names_cast.txt"


@dataclass(frozen=True)
class FixtureLibrary:
    """What was built, and the ids a walk or a probe asks about."""

    path: Path
    files: int
    admin: str
    guests: tuple[str, ...]
    #: The most-filed person, and one with a single file.
    person: str
    small_person: str
    tag: str
    folder: str
    root: str
    collection: str
    photo_set: str
    site: str
    username: str
    song: str
    #: The first file built, and a video.
    asset: str
    video: str
    #: Ten files every press is tried on, and a person to merge into `small_person`.
    press_files: tuple[str, ...]
    merged_person: str
    build_seconds: float


class _Ids:
    """ULID-shaped ids drawn from the seed: ordered by the build, never by the clock."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._made = 0

    def __call__(self) -> str:
        self._made += 1
        stamp = EPOCH * 1000 + self._made
        rest = self._rng.getrandbits(80)
        return _encode(stamp, 10) + _encode(rest, 16)


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _cast() -> list[str]:
    """The invented names, and only those."""
    lines = _CAST.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


class _Weighted:
    """A draw from fixed weights, by bisection over their running total."""

    def __init__(self, rng: random.Random, weights: Sequence[float]) -> None:
        self._rng = rng
        self._totals: list[float] = []
        running = 0.0
        for weight in weights:
            running += weight
            self._totals.append(running)

    def draw(self) -> int:
        return bisect.bisect_left(self._totals, self._rng.random() * self._totals[-1])


def _zipf(count: int, exponent: float) -> list[float]:
    return [1.0 / (rank + 1) ** exponent for rank in range(count)]


def _lognormal(rng: random.Random, count: int, sigma: float) -> list[float]:
    return [rng.lognormvariate(0.0, sigma) for _ in range(count)]


def _scaled(files: int, per_file: float, *, least: int = 1) -> int:
    return max(least, round(files * per_file))


#: How many distinct face descriptions the library draws from: enough to differ, cheap to build.
_EMBEDDINGS = 64


def _embeddings(rng: random.Random) -> list[bytes]:
    return [
        struct.pack(f"<{_EMBEDDING_FLOATS}f", *(rng.random() for _ in range(_EMBEDDING_FLOATS)))
        for _ in range(_EMBEDDINGS)
    ]


@dataclass
class _Rows:
    """Every insert, by statement, in the order the foreign keys need."""

    batches: list[tuple[str, list[tuple[Any, ...]]]]

    def add(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        if rows:
            self.batches.append((sql, rows))


@dataclass
class _Build:
    """The seed's draws and the rows so far, which every phase of the plan shares."""

    files: int
    rng: random.Random
    new: _Ids
    rows: _Rows
    described: list[bytes]

    def catalog(self, table: str, count: int, word: str) -> list[str]:
        ids = [self.new() for _ in range(count)]
        self.rows.add(
            f"INSERT INTO {table} (id, name, created_at, name_sort) VALUES (?, ?, ?, ?)",  # noqa: S608 (module constants)
            [(i, f"{word} {n}", EPOCH, sort_key(f"{word} {n}")) for n, i in enumerate(ids)],
        )
        return ids

    def links(
        self, per_file: float, targets: Sequence[str], weights: list[float], *, pool: Sequence[str]
    ) -> list[tuple[str, str]]:
        pick = _Weighted(self.rng, weights)
        found: set[tuple[str, str]] = set()
        for _ in range(_scaled(self.files, per_file, least=len(targets))):
            found.add((targets[pick.draw()], self.rng.choice(pool)))
        return sorted(found)

    def sample(self, per_file: float, pool: Sequence[str]) -> list[str]:
        return self.rng.sample(list(pool), min(len(pool), _scaled(self.files, per_file)))


_Folder = tuple[str, str, str | None, str, str]


@dataclass(frozen=True)
class _Tree:
    admin: str
    guests: list[str]
    roots: list[str]
    folders: list[_Folder]
    by_root: dict[str, list[str]]
    paths: dict[str, str]
    pick: _Weighted


@dataclass(frozen=True)
class _Catalogs:
    people: list[str]
    person_pick: _Weighted
    tags: list[str]
    photo_sets: list[str]
    collections: list[str]
    songs: list[str]
    sites: list[str]
    usernames: list[str]


@dataclass(frozen=True)
class _Files:
    ids: list[str]
    videos: list[str]
    locations: list[tuple[Any, ...]]
    asset_people: list[tuple[str, str]]


def _plan(files: int, seed: int) -> tuple[_Rows, dict[str, Any]]:
    """Every row of the library, decided before anything is written."""
    rng = random.Random(seed)  # noqa: S311 (reproducible, not secret)
    new = _Ids(rng)
    names = _cast()
    build = _Build(files, rng, new, _Rows([]), _embeddings(rng))
    tree = _folders(build)
    catalogs = _catalogs(build, names)
    made = _files(build, seed, tree, catalogs)
    set_links, sung = _links(build, catalogs, made)
    _jobs_and_faces(build, catalogs, made.ids)
    _signals(build, tree.admin, made)
    _decisions(build, tree.admin, catalogs.people, made.ids)
    _grants(build, tree, catalogs, made.locations)
    return build.rows, _chosen(tree, catalogs, made, set_links, sung)


def _folders(build: _Build) -> _Tree:
    """Users, roots, and folders one to four deep under each root's top one, median three."""
    new, rng, rows = build.new, build.rng, build.rows
    admin = new()
    guests = [new() for _ in range(GUESTS)]
    rows.add(
        "INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, 'x', ?, ?)",
        [(admin, "fixture-admin", "admin", EPOCH)]
        + [(guest, f"fixture-guest-{n}", "guest", EPOCH) for n, guest in enumerate(guests)],
    )
    roots = [new() for _ in range(ROOTS)]
    rows.add(
        "INSERT INTO library_roots (id, name, abs_path, kind, created_at)"
        " VALUES (?, ?, ?, 'local', ?)",
        [(root, f"root{n}", f"/fixture/root{n}", EPOCH) for n, root in enumerate(roots)],
    )
    folder_count = max(ROOTS, _scaled(build.files, FOLDERS_PER_FILE))
    folders: list[_Folder] = []
    depth: dict[str, int] = {}
    by_root: dict[str, list[str]] = {root: [] for root in roots}
    paths: dict[str, str] = {}
    for n in range(folder_count):
        root = roots[n % ROOTS]
        fid = new()
        kin = [f for f in by_root[root] if depth[f] < 4]
        parent = rng.choice(kin) if kin and n >= ROOTS else None
        name = f"folder{n}"
        rel = name if parent is None else f"{paths[parent]}/{name}"
        depth[fid] = 1 if parent is None else depth[parent] + 1
        folders.append((fid, root, parent, rel, name))
        by_root[root].append(fid)
        paths[fid] = rel
    rows.add(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
        folders,
    )
    # Files per folder: median a third of the mean, the largest about ten times the median.
    pick = _Weighted(rng, _lognormal(rng, folder_count, 1.3))
    return _Tree(admin, guests, roots, folders, by_root, paths, pick)


def _catalogs(build: _Build, names: list[str]) -> _Catalogs:
    new, rows = build.new, build.rows
    people = [new() for _ in range(max(10, _scaled(build.files, PEOPLE_PER_FILE)))]
    rows.add(
        "INSERT INTO people (id, name, created_at, name_sort) VALUES (?, ?, ?, ?)",
        [
            (pid, f"{names[n % len(names)]} {n}", EPOCH, sort_key(f"{names[n % len(names)]} {n}"))
            for n, pid in enumerate(people)
        ],
    )
    person_weights = _zipf(len(people), 1.9)
    rest = sum(person_weights[1:])
    person_weights[0] = rest * TOP_PERSON_SHARE / (1 - TOP_PERSON_SHARE)
    person_pick = _Weighted(build.rng, person_weights)
    tags = build.catalog("tags", TAGS, "tag")
    photo_sets = build.catalog("photo_sets", PHOTO_SETS, "set")
    collections = build.catalog("collections", COLLECTIONS, "collection")
    songs = build.catalog("songs", SONGS, "song")
    sites = [new() for _ in range(SITES)]
    rows.add(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        [(s, f"site{n}.example", sort_key(f"site{n}.example"), EPOCH) for n, s in enumerate(sites)],
    )
    usernames = [new() for _ in range(USERNAMES)]
    rows.add(
        "INSERT INTO usernames (id, site_id, name, name_sort, person_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                u,
                sites[n % SITES],
                f"handle_{n}",
                sort_key(f"handle_{n}"),
                people[n % len(people)] if n % 3 == 0 else None,
                EPOCH,
            )
            for n, u in enumerate(usernames)
        ],
    )
    return _Catalogs(people, person_pick, tags, photo_sets, collections, songs, sites, usernames)


def _files(build: _Build, seed: int, tree: _Tree, catalogs: _Catalogs) -> _Files:
    new, rng = build.new, build.rng
    folder_root = {fid: root for fid, root, _p, _r, _n in tree.folders}
    people, person_pick = catalogs.people, catalogs.person_pick
    made = _Files([], [], [], [])
    assets: list[tuple[Any, ...]] = []
    derivatives: list[tuple[Any, ...]] = []
    for i in range(build.files):
        aid = new()
        made.ids.append(aid)
        draw = rng.random()
        kind = "image" if draw < 0.788 else ("video" if draw < 0.997 else "gif")
        if kind == "video":
            made.videos.append(aid)
        ext = {"image": "jpg", "video": "mp4", "gif": "gif"}[kind]
        name = f"file_{rng.randrange(10**9):09d}.{ext}"
        assets.append(
            (
                aid,
                f"fx_{seed}_{i:012d}",
                kind,
                rng.randrange(400, 4000),
                rng.randrange(400, 4000),
                rng.randrange(1000, 3_600_000) if kind == "video" else None,
                rng.randrange(10_000, 50_000_000 if kind == "video" else 8_000_000),
                name,
                EPOCH - rng.randrange(0, 90_000_000),
                sort_key(name),
            )
        )
        copies = 1 + (rng.random() < 0.135) + (rng.random() < 0.012)
        for copy in range(copies):
            folder = tree.folders[tree.pick.draw()][0]
            path = f"{tree.paths[folder]}/{i}_{copy}/{name}"
            made.locations.append(
                (new(), aid, folder_root[folder], folder, path, name, EPOCH, EPOCH)
            )
        if rng.random() < FILES_WITH_A_PERSON:
            first = person_pick.draw()
            made.asset_people.append((aid, people[first]))
            if rng.random() < SECOND_PERSON:
                second = person_pick.draw()
                if second != first:
                    made.asset_people.append((aid, people[second]))
        cached = f"{aid[-4:-2]}/{aid[-2:]}/{aid}"
        derivatives.append((new(), aid, "thumb", f"{cached}/thumb.jpg", EPOCH))
        if rng.random() < DERIVATIVES_PER_FILE - 1:
            derivatives.append((new(), aid, "preview", f"{cached}/preview.mp4", EPOCH))
    _file_rows(build.rows, assets, made, derivatives)
    return made


def _file_rows(
    rows: _Rows, assets: list[tuple[Any, ...]], made: _Files, derivatives: list[tuple[Any, ...]]
) -> None:
    rows.add(
        "INSERT INTO assets (id, identity, media_type, width, height, duration_ms, size_bytes,"
        " original_filename, added_at, filename_sort) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        assets,
    )
    rows.add(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        made.locations,
    )
    rows.add("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", made.asset_people)
    rows.add(
        "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at) VALUES (?, ?, ?, ?, ?)",
        derivatives,
    )


def _links(
    build: _Build, catalogs: _Catalogs, made: _Files
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    rows, rng, ids = build.rows, build.rng, made.ids
    tag_links = build.links(TAG_LINKS_PER_FILE, catalogs.tags, _zipf(TAGS, 0.9), pool=ids)
    rows.add("INSERT INTO asset_tags (tag_id, asset_id) VALUES (?, ?)", tag_links)
    set_links = build.links(
        PHOTO_SET_LINKS_PER_FILE, catalogs.photo_sets, _lognormal(rng, PHOTO_SETS, 2.2), pool=ids
    )
    rows.add(
        "INSERT INTO photo_set_items (photo_set_id, asset_id, added_at) VALUES (?, ?, ?)",
        [(a, b, EPOCH) for a, b in set_links],
    )
    collection_links = build.links(
        COLLECTION_LINKS_PER_FILE, catalogs.collections, _zipf(COLLECTIONS, 1.0), pool=ids
    )
    rows.add(
        "INSERT INTO collection_items (collection_id, asset_id, added_at) VALUES (?, ?, ?)",
        [(a, b, EPOCH) for a, b in collection_links],
    )
    username_links = build.links(
        USERNAME_LINKS_PER_FILE, catalogs.usernames, _zipf(USERNAMES, 1.0), pool=ids
    )
    rows.add("INSERT INTO asset_usernames (username_id, asset_id) VALUES (?, ?)", username_links)
    sung = {
        asset: song
        for song, asset in build.links(
            SONG_LINKS_PER_FILE, catalogs.songs, _zipf(SONGS, 0.8), pool=made.videos or ids
        )
    }
    rows.add(
        "INSERT INTO song_files (asset_id, song_id, added_at) VALUES (?, ?, ?)",
        [(a, b, EPOCH) for a, b in sorted(sung.items())],
    )
    return set_links, sung


def _jobs_and_faces(build: _Build, catalogs: _Catalogs, asset_ids: list[str]) -> None:
    """Jobs as a library's history reads, and faces: scans, tracks, detections, references."""
    new, rng, rows = build.new, build.rng, build.rows
    people, person_pick, described = catalogs.people, catalogs.person_pick, build.described
    job_rows = []
    for n, aid in enumerate(build.sample(JOBS_PER_FILE, asset_ids)):
        draw = rng.random()
        state = "done" if draw < 0.633 else ("canceled" if draw < 0.997 else "failed")
        state = "queued" if n < 2 else state
        job_type = ("thumbnail", "probe", "scan")[n % 3]
        job_rows.append(
            (new(), job_type, state, json.dumps({"asset_id": aid}), EPOCH - n, EPOCH - n)
        )
    rows.add(
        "INSERT INTO jobs (id, type, state, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        job_rows,
    )
    scans = build.sample(FACE_SCANS_PER_FILE, asset_ids)
    rows.add(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count,"
        " identified_count, detector, recognizer, settings_digest, scanned_at)"
        " VALUES (?, 'none_identified', 'fast', 1.0, 1, 1, 0, 'fixture', 'fixture', 'fixture', ?)",
        [(aid, EPOCH) for aid in scans],
    )
    tracks: list[tuple[Any, ...]] = []
    detections: list[tuple[Any, ...]] = []
    track_pool = scans or asset_ids
    for _ in range(_scaled(build.files, FACE_TRACKS_PER_FILE)):
        tid = new()
        named = people[person_pick.draw()] if rng.random() < 0.5 else None
        tracks.append(
            (tid, rng.choice(track_pool), rng.random(), named, "matched" if named else None, EPOCH)
        )
        for _ in range(1 + (rng.random() < FACE_DETECTIONS_PER_TRACK - 1)):
            detections.append(
                (new(), tid, f"detected/{tid[:2]}/{tid}.jpg", rng.choice(described), EPOCH)
            )
    rows.add(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
        " attribution, created_at) VALUES (?, ?, 0, 0, 1, ?, ?, ?, ?)",
        tracks,
    )
    rows.add(
        "INSERT INTO face_detections (id, track_id, timestamp_ms, box_x, box_y, box_w, box_h,"
        " score, quality, crop_path, crop_digest, embedding, created_at)"
        " VALUES (?, ?, 0, 0, 0, 64, 64, 0.9, 0.8, ?, 'fixture', ?, ?)",
        detections,
    )
    rows.add(
        "INSERT INTO face_references (id, person_id, crop_digest, embedding, quality, origin,"
        " recognizer, created_at) VALUES (?, ?, ?, ?, 0.8, 'added', 'fixture', ?)",
        [
            (new(), people[person_pick.draw()], f"ref{n}", rng.choice(described), EPOCH)
            for n in range(round(len(people) * FACE_REFERENCES_PER_PERSON))
        ],
    )


def _signals(build: _Build, admin: str, made: _Files) -> None:
    new, rng, rows, ids = build.new, build.rng, build.rows, made.ids
    rows.add(
        "INSERT INTO semantic_indexed (asset_id, revision, frames, indexed_at) VALUES (?, 'fixture', 4, ?)",
        [(aid, EPOCH) for aid in build.sample(SEMANTIC_INDEXED_PER_FILE, ids)],
    )
    rows.add(
        "INSERT INTO semantic_pooled (asset_id, revision, pooled) VALUES (?, 'fixture', ?)",
        [(aid, rng.choice(build.described)) for aid in build.sample(SEMANTIC_POOLED_PER_FILE, ids)],
    )
    printed = build.sample(FINGERPRINTED_PER_FILE, made.videos or ids)
    rows.add(
        "INSERT INTO audio_fingerprints (asset_id, algorithm, tool, duration_ms, fingerprint,"
        " computed_at) VALUES (?, 2, 'fixture', 60000, ?, ?)",
        [(aid, rng.randbytes(KEYS_PER_FINGERPRINT * 4), EPOCH) for aid in printed],
    )
    rows.add(
        "INSERT OR IGNORE INTO audio_fingerprint_keys (key, asset_id) VALUES (?, ?)",
        [(rng.getrandbits(31), aid) for aid in printed for _ in range(KEYS_PER_FINGERPRINT)],
    )
    rows.add(
        "INSERT INTO plays (id, user_id, asset_id, started_at, duration_ms, made_at, kind)"
        " VALUES (?, ?, ?, ?, 30000, ?, 'video')",
        [
            (new(), admin, aid, EPOCH, EPOCH)
            for aid in build.sample(PLAYS_PER_FILE, made.videos or ids)
        ],
    )


def _decisions(build: _Build, admin: str, people: list[str], asset_ids: list[str]) -> None:
    new, rng, rows = build.new, build.rng, build.rows
    decisions: list[tuple[Any, ...]] = []
    subjects: list[tuple[Any, ...]] = []
    queues = ("folders", "things", "filenames", "copies", "duplicates")
    for n in range(_scaled(build.files, DECISIONS_PER_FILE)):
        did = new()
        decisions.append((did, queues[n % len(queues)], admin, EPOCH - n))
        subjects.append((did, "asset", rng.choice(asset_ids)))
        if rng.random() < SUBJECTS_PER_DECISION - 1:
            subjects.append((did, "person", rng.choice(people)))
    rows.add(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at)"
        " VALUES (?, ?, ?, 'Decided', 'Decided.', '{}', ?)",
        decisions,
    )
    rows.add(
        "INSERT OR IGNORE INTO workbench_decision_subjects (decision_id, kind, subject_id) VALUES (?, ?, ?)",
        subjects,
    )
    pairs: set[tuple[str, str]] = set()
    for _ in range(_scaled(build.files, DUPLICATES_PER_FILE)):
        a, b = rng.sample(asset_ids, 2) if len(asset_ids) > 1 else (asset_ids[0], asset_ids[0])
        if a != b:
            pairs.add((min(a, b), max(a, b)))
    rows.add(
        "INSERT INTO dedup_candidates (id, asset_a, asset_b, method, distance, created_at)"
        " VALUES (?, ?, ?, 'phash', 4, ?)",
        [(new(), a, b, EPOCH) for a, b in sorted(pairs)],
    )
    rows.add(
        "INSERT INTO asset_user_state (asset_id, user_id, favorite, rating, view_count, hidden,"
        " hidden_at, last_viewed_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                aid,
                admin,
                n % 3 == 0,
                1 + n % 10 if n % 4 == 0 else None,
                n % 5,
                n % 17 == 0,
                EPOCH if n % 17 == 0 else None,
                EPOCH - n,
                EPOCH,
            )
            for n, aid in enumerate(build.sample(FILE_STATES_PER_FILE, asset_ids))
        ],
    )


def _grants(
    build: _Build, tree: _Tree, catalogs: _Catalogs, locations: list[tuple[Any, ...]]
) -> None:
    """Each guest is shared whole roots until about a fifth of the files, a few things besides,
    and one folder kept from them: a grant table of about forty-five rows."""
    new, roots = build.new, tree.roots
    root_files: dict[str, int] = {root: 0 for root in roots}
    for row in locations:
        root_files[row[2]] += 1
    grants: list[tuple[Any, ...]] = []
    for g, guest in enumerate(tree.guests):
        seen = 0
        order = roots[g:] + roots[:g]
        for root in order:
            if seen >= GUEST_SHARE * len(locations):
                break
            grants.append((new(), "root", root, guest, "share", EPOCH))
            seen += root_files[root]
        for kind, ids in (
            ("person", catalogs.people),
            ("tag", catalogs.tags),
            ("collection", catalogs.collections),
            ("site", catalogs.sites),
        ):
            grants.append((new(), kind, ids[(g * 7) % len(ids)], guest, "share", EPOCH))
        grants.append((new(), "folder", tree.by_root[order[0]][-1], guest, "restrict", EPOCH))
    build.rows.add(
        "INSERT OR IGNORE INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?)",
        grants,
    )
    build.rows.add(
        "INSERT INTO person_user_state (person_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        [(catalogs.people[-1], tree.admin, EPOCH, EPOCH)],
    )


def _chosen(
    tree: _Tree,
    catalogs: _Catalogs,
    made: _Files,
    set_links: list[tuple[str, str]],
    sung: dict[str, str],
) -> dict[str, Any]:
    people, photo_sets = catalogs.people, catalogs.photo_sets
    filed: dict[str, int] = {}
    for _aid, pid in made.asset_people:
        filed[pid] = filed.get(pid, 0) + 1
    few = sorted((pid for pid in people[1:-1] if filed.get(pid)), key=lambda pid: (filed[pid], pid))
    biggest_set = max(photo_sets, key=lambda s: sum(1 for x, _a in set_links if x == s))
    return {
        "admin": tree.admin,
        "guests": tuple(tree.guests),
        "person": people[0],
        "small_person": few[0],
        "merged_person": few[1],
        "press_files": tuple(made.ids[1:11]),
        "tag": catalogs.tags[0],
        "folder": _shared_folder(tree.folders, made.locations),
        "root": tree.roots[0],
        "collection": catalogs.collections[0],
        "photo_set": biggest_set,
        "site": catalogs.sites[0],
        "username": catalogs.usernames[0],
        "song": next(iter(sung.values()), catalogs.songs[0]),
        "asset": made.ids[0],
        "video": made.videos[0] if made.videos else made.ids[0],
    }


#: The share of the library under the folder a share is tried on, so it grows with the library.
SHARED_FOLDER_SHARE = 1 / 36


def _shared_folder(folders: list[_Folder], locations: list[tuple[Any, ...]]) -> str:
    """The folder whose tree holds nearest a thirty-sixth of the copies."""
    parent = {fid: up for fid, _root, up, _rel, _name in folders}
    under: dict[str, int] = dict.fromkeys(parent, 0)
    for row in locations:
        folder: str | None = row[3]
        while folder is not None:
            under[folder] += 1
            folder = parent[folder]
    wanted = len(locations) * SHARED_FOLDER_SHARE
    return min(under, key=lambda fid: (abs(under[fid] - wanted), fid))


async def fixture_library(files: int, seed: int, into: Path) -> FixtureLibrary:
    """Build a library of `files` files at `into` (a database file): the schema by its own
    migrations, every row by the plan above, the stored answers by the backfill."""
    started = time.perf_counter()
    rows, ids = await asyncio.to_thread(_plan, files, seed)
    database = Database(into, readers=1)
    await database.connect()
    try:
        await database.initialize_schema()
        async with database.write() as connection:
            # Without the triggers: a bulk build re-decides once at the end, as the backfill does.
            await visibility._drop_triggers(connection)
            for sql, batch in rows.batches:
                await connection.executemany(sql, batch)
            await visibility.refresh_everything(connection)
        await database.refresh_statistics(reason="fixture", every_table=True, force=True)
    finally:
        await database.close()
    return FixtureLibrary(
        path=into, files=files, build_seconds=round(time.perf_counter() - started, 1), **ids
    )


# --- the questions that must not grow with the library ------------------------------------------

#: A question: a name and the read or write it makes, run once per call.
Question = Callable[[], Awaitable[object]]


def _params(viewer: str, **more: object) -> dict[str, object]:
    _where, bound = NO_FILTER.predicate()
    base: dict[str, object] = {
        "viewer": viewer,
        "asset_id": None,
        "reveal": 0,
        "limit": 50,
        "offset": 0,
        "tag_id": None,
        "collection_id": None,
        "photo_set_id": None,
        "hidden_only": 0,
        "pinned_first": 0,
        "reveal_named": 0,
        "is_admin": 0,
        "prefix": "",
        "like": "%",
        "person_id": None,
        "person_ids": None,
        "site_id": None,
        "list_empty": 0,
        "entity_sort": "name",
        **bound,
    }
    base.update(more)
    return base


def _there[T](found: T | None, what: str) -> T:
    if found is None:
        raise RuntimeError(f"the fixture library has no {what}")
    return found


async def questions(
    database: Database, access: Repository, lib: FixtureLibrary
) -> dict[str, Question]:
    """The reads a page, a count, a thumbnail and a wall make, and the three writes the triggers
    answer, each bound as the application binds it. Asked by the scale probe in milliseconds and
    by the statement ledger in steps; the writes last, since they change the library."""
    return {**await _reads(database, access, lib), **_writes(database, lib)}


async def _reads(
    database: Database, access: Repository, lib: FixtureLibrary
) -> dict[str, Question]:
    where, _bound = NO_FILTER.predicate()
    stats = _there(
        await database.fetch_one(
            "SELECT permitted, (SELECT MAX(rowid) FROM assets) AS library FROM viewer_stats"
            " WHERE user_id = ?",
            (lib.admin,),
        ),
        "stats",
    )
    drive = drive_for(int(stats["permitted"]), int(stats["library"]))
    middle = _there(
        await database.fetch_one(
            "SELECT id FROM assets ORDER BY added_at DESC, id DESC LIMIT 1 OFFSET ?",
            (lib.files // 2,),
        ),
        "middle",
    )
    anchor = _there(await database.fetch_one(seek_anchor("newest"), (middle["id"],)), "anchor")
    guest = lib.guests[0]
    guest_stats = _there(
        await database.fetch_one("SELECT permitted FROM viewer_stats WHERE user_id = ?", (guest,)),
        "guest stats",
    )
    guest_drive = drive_for(int(guest_stats["permitted"]), int(stats["library"]))
    person_where, person_bound = AssetFilter(where=Where("people", (lib.person,))).predicate()
    admin = Viewer(id=lib.admin, role=Role.ADMIN)

    def read(sql: str, params: dict[str, object] | tuple[object, ...]) -> Question:
        return lambda: database.fetch_all(sql, params)

    return {
        "admin page, newest": read(
            assets_query("newest", where, arranged=False, counted=False, drive=drive),
            _params(lib.admin),
        ),
        "admin page, by name": read(
            assets_query("name_az", where, arranged=False, counted=False, drive=drive),
            _params(lib.admin),
        ),
        "admin page continued (keyset)": read(
            assets_query(
                "newest", where, arranged=False, counted=False, drive=drive, continued=True
            ),
            _params(lib.admin, after_key=anchor["key"], after_id=middle["id"]),
        ),
        "admin count, un-narrowed": read(
            "SELECT permitted, concealed FROM viewer_stats WHERE user_id = ?", (lib.admin,)
        ),
        "guest page": read(
            assets_query("newest", where, arranged=False, counted=False, drive=guest_drive),
            _params(guest),
        ),
        "point check (thumbnail)": read(point_query(where), _params(lib.admin, asset_id=lib.asset)),
        "one person's files": read(
            assets_query("newest", person_where, arranged=False, counted=False, drive=drive),
            _params(lib.admin, **person_bound),
        ),
        "people wall": lambda: access.suggest_people(admin, limit=50),
        "tags wall": lambda: access.list_tags(admin, limit=50),
    }


def _writes(database: Database, lib: FixtureLibrary) -> dict[str, Question]:
    def write(sql: str, params: tuple[object, ...]) -> Question:
        return lambda: database.execute(sql, params)

    return {
        "hide one file (trigger)": write(
            "INSERT INTO asset_user_state (asset_id, user_id, hidden, updated_at)"
            " VALUES (?, ?, 1, 0) ON CONFLICT(asset_id, user_id) DO UPDATE SET hidden = 1",
            (lib.asset, lib.admin),
        ),
        "tag one file (trigger)": write(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
            (lib.asset, lib.tag),
        ),
        "share a folder (trigger)": write(
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES (?, 'folder', ?, ?, 'share', 0)",
            (new_id(), lib.folder, lib.guests[-1]),
        ),
    }


async def files_under(database: Database, lib: FixtureLibrary) -> dict[str, int]:
    """What the two per-member questions are priced by: the person's files, the folder's copies."""
    person = await database.fetch_one(
        "SELECT COUNT(*) AS n FROM asset_people WHERE person_id = ?", (lib.person,)
    )
    folder = await database.fetch_one(
        "SELECT COUNT(*) AS n FROM asset_locations WHERE folder_id = ? OR folder_id IN"
        " (SELECT folder_id FROM folder_ancestry WHERE ancestor_id = ?)",
        (lib.folder, lib.folder),
    )
    return {
        "one person's files": int(_there(person, "person")["n"]),
        "share a folder (trigger)": int(_there(folder, "folder")["n"]),
    }
