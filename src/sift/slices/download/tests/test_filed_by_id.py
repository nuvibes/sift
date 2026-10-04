# SPDX-License-Identifier: AGPL-3.0-or-later
"""A download is filed under its Site and person by ID, and a renamed Site stays that Site.

Joined by name, renaming a Site would break every row from it, and the next download from the
site, looking the Site up by the catalog's name for it, would make a SECOND Site under the old name
and could not find the cookies saved on the first. So the library remembers which Site each site
files under (`download_sites`), and every download reads the Site's name through that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.download.schema import _CREATE_DOWNLOADS, initialize_download
from sift.slices.download.service import DownloadService
from sift.slices.download.sources import url_hash
from sift.slices.download.sources.registry import classify, site_key

_URL = "https://www.tiktok.com/@riverbend/video/1"


async def _asset(db: Database) -> str:
    asset_id = new_id()
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        (asset_id, f"hash-{asset_id}"),
    )
    return asset_id


async def _sites(db: Database) -> list[str]:
    return [str(row["name"]) for row in await db.fetch_all("SELECT name FROM sites ORDER BY name")]


async def test_a_renamed_site_is_still_where_the_next_download_files_and_its_cookies_are(
    download_service: DownloadService, temp_db: Database, master_key: bytes, admin_id: str
) -> None:
    attribution = classify(_URL)
    assert attribution.site == "TikTok"
    first = await _asset(temp_db)
    await download_service.attribute(
        asset_id=first, site="TikTok", username="riverbend", address=_URL
    )
    await download_service.save_connection(
        site="TikTok", cookie="c=1", master_key=master_key, by=admin_id
    )
    site = await temp_db.fetch_one("SELECT id FROM sites WHERE name = 'TikTok'")
    assert site is not None
    await temp_db.execute("UPDATE sites SET name = 'Riverbend Clips' WHERE id = ?", (site["id"],))

    filed = await download_service.filed_as(_URL, attribution)
    assert filed.site == "Riverbend Clips"
    assert filed.username == attribution.username
    # The cookies saved on the Site are found under the name it has now.
    found = await download_service.connection_for_site(filed.site)
    assert found is not None and found.secret_id is not None

    second = await _asset(temp_db)
    await download_service.attribute(
        asset_id=second, site=filed.site, username="riverbend", address=_URL
    )
    assert await _sites(temp_db) == ["Riverbend Clips"]


async def test_a_site_nothing_was_filed_from_keeps_the_catalogs_name(
    download_service: DownloadService,
) -> None:
    attribution = classify(_URL)
    assert await download_service.filed_as(_URL, attribution) == attribution


async def test_a_finished_download_keeps_its_site_and_person_by_id(
    download_service: DownloadService, temp_db: Database
) -> None:
    download_id = new_id()
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, created_at) VALUES (?, ?, ?, 'running', 0)",
        (download_id, _URL, url_hash(_URL)),
    )
    asset_id = await _asset(temp_db)
    await download_service.attribute(
        asset_id=asset_id,
        site="TikTok",
        username="riverbend",
        address=_URL,
        username_is_a_person=True,
    )
    await download_service.mark_done(
        download_id, asset_id=asset_id, site="TikTok", username="riverbend", filename="a.mp4"
    )
    row = await temp_db.fetch_one(
        "SELECT d.site_id, d.person_id, s.name AS site, p.name AS person FROM downloads d"
        " JOIN sites s ON s.id = d.site_id JOIN people p ON p.id = d.person_id WHERE d.id = ?",
        (download_id,),
    )
    assert row is not None
    assert (row["site"], row["person"]) == ("TikTok", "riverbend")

    await temp_db.execute(
        "UPDATE sites SET name = 'Riverbend Clips' WHERE id = ?", (row["site_id"],)
    )
    view = await download_service.get(download_id)
    assert view is not None and view.site_id == row["site_id"]
    page = await download_service.list_downloads(limit=50)
    listed = next(one for one in page.downloads if one.id == download_id)
    assert (listed.site, listed.site_name, listed.site_id) == (
        "Riverbend Clips",
        "Riverbend Clips",
        row["site_id"],
    )
    assert listed.person_id == row["person_id"]


async def test_the_step_fills_the_ids_and_the_sites_from_the_names(tmp_path: Path) -> None:
    """Version 34, over rows kept by name: the Site and the person of each, and which Site each site
    files under, the newest filing's where two differ."""
    database = Database(tmp_path / "at33.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            for owner in (
                "CREATE TABLE folders (id TEXT PRIMARY KEY)",
                "CREATE TABLE assets (id TEXT PRIMARY KEY)",
                "CREATE TABLE jobs (id TEXT PRIMARY KEY)",
                "CREATE TABLE users (id TEXT PRIMARY KEY)",
                "CREATE TABLE sites (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE)",
                "CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT NOT NULL)",
                "CREATE TABLE usernames (id TEXT PRIMARY KEY, site_id TEXT, name TEXT,"
                " person_id TEXT)",
            ):
                await connection.execute(owner)
            await connection.execute(_CREATE_DOWNLOADS)
            from sift.slices.download.schema import _LEFT_OUT, _NAMED_FROM, _REQUESTED_BY

            for statement in (*_NAMED_FROM, *_LEFT_OUT, *_REQUESTED_BY):
                await connection.execute(statement)
            await connection.execute("INSERT INTO sites (id, name) VALUES ('s1', 'TikTok')")
            await connection.execute("INSERT INTO people (id, name) VALUES ('p1', 'Riverbend')")
            await connection.execute(
                "INSERT INTO downloads (id, url, url_hash, state, site, username, created_at)"
                " VALUES ('d1', ?, 'h', 'done', 'tiktok', 'riverbend', 0)",
                (_URL,),
            )
            await initialize_download(connection, 33)
        row = await database.fetch_one("SELECT site_id, person_id FROM downloads WHERE id = 'd1'")
        assert row is not None and (row["site_id"], row["person_id"]) == ("s1", "p1")
        kept = await database.fetch_one(
            "SELECT site_id FROM download_sites WHERE key = ?", (site_key(_URL),)
        )
        assert kept is not None and kept["site_id"] == "s1"
    finally:
        await database.close()


async def test_the_step_files_a_site_renamed_before_it_by_the_record_of_its_rename(
    tmp_path: Path,
) -> None:
    """Rows kept under a name a Site has since given up name nothing by it, so the record of
    renames says which Site carried it. A name two Sites both carried, or a record that cannot
    be read, files nothing rather than guessing; a row whose address names no site keeps its Site
    and teaches no site where to file."""
    database = Database(tmp_path / "at33.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            for owner in (
                "CREATE TABLE folders (id TEXT PRIMARY KEY)",
                "CREATE TABLE assets (id TEXT PRIMARY KEY)",
                "CREATE TABLE jobs (id TEXT PRIMARY KEY)",
                "CREATE TABLE users (id TEXT PRIMARY KEY)",
                "CREATE TABLE sites (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE)",
                "CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT NOT NULL)",
                "CREATE TABLE usernames (id TEXT PRIMARY KEY, site_id TEXT, name TEXT,"
                " person_id TEXT)",
                "CREATE TABLE workbench_decisions (id TEXT PRIMARY KEY, verb TEXT, payload TEXT,"
                " decided_at INTEGER)",
                "CREATE TABLE workbench_decision_subjects (decision_id TEXT, kind TEXT,"
                " subject_id TEXT)",
            ):
                await connection.execute(owner)
            await connection.execute(_CREATE_DOWNLOADS)
            from sift.slices.download.schema import _LEFT_OUT, _NAMED_FROM, _REQUESTED_BY

            for statement in (*_NAMED_FROM, *_LEFT_OUT, *_REQUESTED_BY):
                await connection.execute(statement)
            for site_id, name in (("s1", "Clipdeck"), ("s2", "Reelbox"), ("s3", "Framewell")):
                await connection.execute(
                    "INSERT INTO sites (id, name) VALUES (?, ?)", (site_id, name)
                )
            for act, site_id, payload in (
                ("a1", "s1", '{"before": "TikTok"}'),
                ("a2", "s2", '{"before": "Vimeo"}'),
                ("a3", "s3", '{"before": "vimeo "}'),
                ("a4", "s3", "not json"),
                ("a5", "s1", '"a rename with no before"'),
                ("a6", "s2", '{"before": "  "}'),
            ):
                await connection.execute(
                    "INSERT INTO workbench_decisions (id, verb, payload, decided_at)"
                    " VALUES (?, 'renamed', ?, 1)",
                    (act, payload),
                )
                await connection.execute(
                    "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
                    " VALUES (?, 'site', ?)",
                    (act, site_id),
                )
            for download_id, url, site in (
                ("d1", _URL, "tiktok"),
                ("d2", "https://vimeo.com/1", "vimeo"),
                ("d3", "not an address", "Clipdeck"),
            ):
                await connection.execute(
                    "INSERT INTO downloads (id, url, url_hash, state, site, created_at)"
                    " VALUES (?, ?, ?, 'done', ?, 0)",
                    (download_id, url, download_id, site),
                )
            await initialize_download(connection, 33)

        rows = await database.fetch_all("SELECT id, site_id FROM downloads ORDER BY id")
        assert [(row["id"], row["site_id"]) for row in rows] == [
            ("d1", "s1"),
            ("d2", None),
            ("d3", "s1"),
        ]
        kept = await database.fetch_all("SELECT key, site_id FROM download_sites")
        assert [(row["key"], row["site_id"]) for row in kept] == [(site_key(_URL), "s1")]
    finally:
        await database.close()


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("https://www.tiktok.com/@riverbend/video/1", "tiktok"),
        ("https://www.example.org/a", "@example.org"),
        ("not an address", None),
    ],
)
def test_a_sites_key_is_the_catalogs_or_the_host(address: str, key: str | None) -> None:
    assert site_key(address) == key


async def test_the_naming_preview_reads_a_renamed_sites_rows_under_its_name_now(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The preview's real example is the newest download from the Site, found by the Site's id and
    named with the Site's name now. By the word, a download made after the rename (which says the
    new name) would be missed for an older one, and the example would name the Site as it was."""
    await download_service.attribute(
        asset_id=await _asset(temp_db), site="TikTok", username="riverbend", address=_URL
    )
    site = await temp_db.fetch_one("SELECT id FROM sites WHERE name = 'TikTok'")
    assert site is not None
    for at, (word, poster) in enumerate(
        (("TikTok", "marchfield"), ("Riverbend Clips", "harlowquin"))
    ):
        await temp_db.execute(
            "INSERT INTO downloads (id, url, url_hash, state, site, site_id, username, original,"
            " created_at) VALUES (?, ?, ?, 'done', ?, ?, ?, 'b7c1e9a2f4d6', ?)",
            (new_id(), _URL, url_hash(_URL), word, site["id"], poster, at),
        )
    await temp_db.execute("UPDATE sites SET name = 'Riverbend Clips' WHERE id = ?", (site["id"],))

    named = await download_service.newest_named("tiktok", "TikTok")
    assert named is not None and (named.site, named.username) == ("Riverbend Clips", "harlowquin")
    assert await download_service.newest_creator("tiktok", "TikTok") == "harlowquin"
    assert await download_service.site_name_now("tiktok", "TikTok") == "Riverbend Clips"
    # A site nothing has been filed from keeps the catalog's name and its rows by the word.
    assert await download_service.site_name_now("youtube", "YouTube") == "YouTube"
    assert await download_service.newest_named("youtube", "YouTube") is None
    assert await download_service.newest_creator("youtube", "YouTube") is None
