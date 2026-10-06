# SPDX-License-Identifier: AGPL-3.0-or-later
"""A product's bar on Activity: its two ends range over one set of files.

The count of what is lacking reads only read files with a copy there, and so does the denominator:
a whole library's would count every unread file, every file with no copy and every photo as done.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    VerdictProduct,
    identity,
    lacks_derivative,
    lacks_fingerprint,
)
from sift.kernel.content.identity import RECIPE_VERSIONS, picture_verdict
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000

#: The seek into a file's copies, as an older and a newer SQLite each word it.
_SEEKS_LOCATIONS = re.compile(r"SEARCH l (?:EXISTS )?USING INDEX ix_loc_asset \(asset_id=\?\)")


async def _file(
    database: Database,
    root: LibraryRoot,
    media_type: str,
    *,
    read: bool = True,
    present: bool = True,
    pictures: tuple[DerivativeKind, ...] = (),
) -> str:
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, duration_ms, added_at,"
        " probed_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
        (
            asset_id,
            f"digest-{asset_id}",
            media_type,
            None if media_type == "image" else 5000,
            _EPOCH,
            _EPOCH if read else None,
        ),
    )
    await database.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_id(),
            asset_id,
            root.id,
            f"{asset_id}.bin",
            f"{asset_id}.bin",
            "present" if present else "missing",
            _EPOCH,
            _EPOCH,
        ),
    )
    for kind in pictures:
        await database.execute(
            "INSERT INTO derivatives"
            " (id, asset_id, kind, rel_cache_path, created_at, recipe_version)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                asset_id,
                kind.value,
                f"{kind.value}/{asset_id}",
                _EPOCH,
                RECIPE_VERSIONS[kind],
            ),
        )
    return asset_id


async def _given_up(database: Database, asset_id: str, product: str) -> None:
    await database.execute(
        "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
        " VALUES (?, ?, 'unreadable', 'test', 0, ?)",
        (asset_id, product, _EPOCH),
    )


@pytest.mark.parametrize("kind", [DerivativeKind.THUMB, DerivativeKind.PREVIEW])
async def test_done_is_the_files_that_have_it_while_a_first_import_is_still_being_read(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, kind: DerivativeKind
) -> None:
    both = (DerivativeKind.THUMB, DerivativeKind.PREVIEW)
    has_it = {
        await _file(temp_db, library_root, "video", pictures=both),
        await _file(temp_db, library_root, "image", pictures=(DerivativeKind.THUMB,)),
    }
    await _file(temp_db, library_root, "video")  # read, and lacking both
    await _file(temp_db, library_root, "video", read=False)  # coming
    await _file(temp_db, library_root, "image", read=False)  # coming, for a thumbnail only
    # Neither end counts a file with no copy there, nor one the read or this work gave up on.
    await _file(temp_db, library_root, "video", present=False, pictures=both)
    await _given_up(
        temp_db, await _file(temp_db, library_root, "video", read=False), VerdictProduct.PROBE
    )
    await _given_up(
        temp_db, await _file(temp_db, library_root, "video"), picture_verdict(kind).value
    )

    term = lacks_derivative([kind])
    assert term is not None
    verdict = picture_verdict(kind).value
    wanting = await content_store.wanting_count(verdict)
    # Filed under its product, as the registry files every term it counts.
    lacking = (await content_store.count_lacking([replace(term, product=verdict)])).files
    coming = await content_store.coming_count(verdict)

    photos_count = kind is DerivativeKind.THUMB
    assert coming == (2 if photos_count else 1)
    assert lacking == 1
    assert wanting - lacking - coming == len(has_it) - (0 if photos_count else 1)


async def test_every_file_coming_wants_its_fingerprints(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    await _file(temp_db, library_root, "image")
    await _file(temp_db, library_root, "video", read=False)
    await _file(temp_db, library_root, "image", read=False)

    fingerprints = VerdictProduct.FINGERPRINTS.value
    assert await content_store.wanting_count(fingerprints) == 3
    assert await content_store.coming_count(fingerprints) == 2


async def test_the_library_s_files_are_the_ones_browse_shows(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    await _file(temp_db, library_root, "video")
    await _file(temp_db, library_root, "image", present=False)
    removed = await _file(temp_db, library_root, "video")
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (removed,))
    await temp_db.execute("UPDATE assets SET acodec = 'aac' WHERE media_type = 'video'")
    refusing_none = identity.wanted_outside([["no-such-folder"]])

    assert await content_store.asset_count() == 2
    assert await content_store.asset_count(refusing_none) == 2
    assert await content_store.wanting_count("music") == 1


async def test_a_count_among_some_files_seeks_only_those(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    lacking = await _file(temp_db, library_root, "video")
    await _file(temp_db, library_root, "video")
    has_it = await _file(temp_db, library_root, "image", pictures=(DerivativeKind.THUMB,))
    term = lacks_derivative([DerivativeKind.THUMB])
    assert term is not None
    term = replace(term, product=picture_verdict(DerivativeKind.THUMB).value)
    among = [lacking, has_it, "gone"]

    split = await content_store.count_lacking_by_kind([term], among=among)
    statement = identity._lacking_statement(
        [term], [True], statement=identity._COUNT_LACKING_BY_KIND, among=among
    )
    plan = await temp_db.fetch_all(
        "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
        (*identity._term_params([term]), json.dumps(among)),
    )

    assert {kind: one.files for kind, one in split.items()} == {"image": 0, "video": 1}
    assert not [row["detail"] for row in plan if str(row["detail"]).startswith("SCAN a")]


def _counts_a_bar_is_drawn_from() -> list[tuple[str, str, tuple[object, ...]]]:
    """Every statement the Activity screen's bars are counted by, with values to bind."""
    probe = VerdictProduct.PROBE.value
    statements: list[tuple[str, str, tuple[object, ...]]] = [
        ("unread", identity._COUNT_UNREAD, (probe,)),
    ]
    for kind in (DerivativeKind.THUMB, DerivativeKind.PREVIEW, DerivativeKind.SPRITE, None):
        verdict = VerdictProduct.FINGERPRINTS.value if kind is None else picture_verdict(kind).value
        term = lacks_fingerprint() if kind is None else lacks_derivative([kind])
        assert term is not None
        term = replace(term, product=verdict)
        params = tuple(identity._term_params([term]))
        label = "fingerprints" if kind is None else kind.value
        narrowed = verdict if verdict in identity._COUNT_WANTING_OF else None
        statements += [
            (f"wanting {label}", identity._COUNT_WANTING_OF[narrowed], (probe, verdict)),
            (f"coming {label}", identity._COUNT_COMING_OF[narrowed], (probe, verdict)),
            (f"lacking {label}", identity._lacking_statement([term], [True]), params),
            (
                f"lacking {label} by kind",
                identity._lacking_statement(
                    [term], [True], statement=identity._COUNT_LACKING_BY_KIND
                ),
                params,
            ),
        ]
    statements += [
        ("wanting music", identity._COUNT_WANTING_OF["music"], (probe, "music")),
        ("coming music", identity._COUNT_COMING_OF["music"], (probe, "music")),
    ]
    return statements


@pytest.mark.parametrize(
    ("label", "statement", "params"),
    _counts_a_bar_is_drawn_from(),
    ids=[one[0] for one in _counts_a_bar_is_drawn_from()],
)
async def test_a_count_a_bar_is_drawn_from_walks_the_library_once_and_seeks_the_rest(
    temp_db: Database,
    content_store: ContentStore,
    label: str,
    statement: str,
    params: tuple[object, ...],
) -> None:
    """Each count is one pass over the files and an index seek per file into every other table.

    About twenty of these make one count of what is still to come, which is over a second on a
    library of a hundred thousand files already. One of them walking a second table per file
    instead of seeking it would turn that second into minutes, and nothing on screen would say why.
    """
    del content_store  # the store is what builds the schema the statements read
    plan = await temp_db.fetch_all(
        "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
        params,
    )
    steps = [str(row["detail"]) for row in plan]
    walks = [step for step in steps if step.startswith("SCAN ")]
    assert walks == ["SCAN a"], (label, steps)
    if "asset_locations" in statement:
        # A newer SQLite runs a correlated EXISTS as a semi-join and names the step
        # "SEARCH l EXISTS USING ...": the same seek per file, so either wording is the seek.
        assert any(_SEEKS_LOCATIONS.fullmatch(s) for s in steps), steps
