# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one site does differently, and the two ways that can be got wrong.

The first is treating a row as an all-or-nothing override, so that giving a site a folder quietly
undoes a naming rule set for everything. The second is forgetting that an empty template is a real
answer: somebody choosing to keep the fetcher's name is not the same as somebody having no
opinion, and collapsing the two takes the choice away.

The name has a step the folder and the tool do not: Sift ships one per Site. A rule typed for the
Site wins, then the shipped name, then the rule for all Sites, which therefore reaches only an
address the catalog does not know.
"""

from __future__ import annotations

import json

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's tables, for the History lines)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.download.site_options import DEFAULT_SCOPE, SiteOptionStore
from sift.slices.download.sources.sites.catalog import by_key
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio


async def _a_folder(database: Database) -> str:
    """A real folder to point at. The destination is a foreign key on purpose: a folder somebody
    deletes must not leave a site pointing at somewhere that is not there any more."""
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        ("root-1", "Library", "/library", 0),
    )
    await database.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
        ("folder-1", "root-1", None, "", "Library"),
    )
    return "folder-1"


@pytest.fixture
async def store(temp_db: Database) -> SiteOptionStore:
    await temp_db.initialize_schema()
    return SiteOptionStore(temp_db)


def _shipped(key: str) -> str:
    record = by_key(key)
    assert record is not None
    return record.default_naming


async def test_a_site_with_nothing_set_gets_the_name_sift_ships_for_it(
    store: SiteOptionStore,
) -> None:
    """Above the rule for all Sites: no one template suits a random TikTok code and a file host's
    real file name alike."""
    await store.set(DEFAULT_SCOPE, naming="{site} - {name}", dest_folder_id=None)
    assert (await store.resolve("tiktok")).naming == "{creator} - {posted} - {id}"
    assert (await store.resolve("tiktok")).naming == _shipped("tiktok")


async def test_a_typed_rule_wins_over_the_shipped_name(store: SiteOptionStore) -> None:
    await store.set("tiktok", naming="{name}", dest_folder_id=None)
    assert (await store.resolve("tiktok")).naming == "{name}"


async def test_an_empty_rule_keeps_the_name_against_the_shipped_one(
    store: SiteOptionStore,
) -> None:
    """Somebody choosing "keep the name" on a Site Sift ships a template for. Read as "no rule" it
    would fall through to that template, which is the one thing they did not want."""
    await store.set("tiktok", naming="", dest_folder_id=None)
    assert (await store.resolve("tiktok")).naming == ""


async def test_a_site_shipped_to_keep_its_name_keeps_it_under_a_rule_for_all_sites(
    store: SiteOptionStore,
) -> None:
    """A shipped "keep the name" is an answer too, and it sits above the rule for all Sites."""
    await store.set(DEFAULT_SCOPE, naming="{site} - {name}", dest_folder_id=None)
    assert _shipped("bunkr") == ""
    assert (await store.resolve("bunkr")).naming == ""


async def test_the_rule_for_all_sites_reaches_an_address_the_catalog_does_not_know(
    store: SiteOptionStore,
) -> None:
    """No record, or a key the catalog no longer has: a Site removed, a newer Sift's database."""
    await store.set(DEFAULT_SCOPE, naming="{site} - {name}", dest_folder_id=None)
    await store.set("no-such-site", naming=None, dest_folder_id=None)
    assert (await store.resolve(None)).naming == "{site} - {name}"
    assert (await store.resolve("no-such-site")).naming == "{site} - {name}"


async def test_with_nothing_set_anywhere_an_unknown_address_keeps_its_name(
    store: SiteOptionStore,
) -> None:
    assert (await store.resolve(None)).naming == ""


async def test_a_site_of_its_own_wins_over_the_default(store: SiteOptionStore) -> None:
    await store.set(DEFAULT_SCOPE, naming="{site} - {name}", dest_folder_id=None)
    await store.set("youtube", naming="{creator}", dest_folder_id=None)
    assert (await store.resolve("youtube")).naming == "{creator}"
    assert (await store.resolve("redgifs")).naming == _shipped("redgifs")


async def test_the_two_answers_fall_back_independently(
    store: SiteOptionStore, temp_db: Database
) -> None:
    """The failure this prevents: choosing a folder for one site silently stops it following the
    naming rule set for everything, and nothing anywhere says so."""
    folder = await _a_folder(temp_db)
    await store.set(DEFAULT_SCOPE, naming="{site} - {name}", dest_folder_id=None)
    await store.set("youtube", naming=None, dest_folder_id=folder)

    resolved = await store.resolve("youtube")
    assert resolved.naming == _shipped("youtube")
    assert resolved.dest_folder_id == folder


async def test_an_empty_template_is_a_choice_rather_than_an_absence(store: SiteOptionStore) -> None:
    """Keeping the name the fetcher gave it is a decision. Read as "no opinion" it would be
    overruled by the default, which is the one thing the person choosing it did not want."""
    await store.set(DEFAULT_SCOPE, naming="{site} - {name}", dest_folder_id=None)
    await store.set("youtube", naming="", dest_folder_id=None)
    assert (await store.resolve("youtube")).naming == ""


async def test_a_site_put_back_follows_the_default_again(store: SiteOptionStore) -> None:
    await store.set(DEFAULT_SCOPE, naming="{name}", dest_folder_id=None)
    await store.set("youtube", naming="{creator}", dest_folder_id=None)
    await store.clear("youtube")
    assert (await store.resolve("youtube")).naming == _shipped("youtube")


async def test_an_unrecognised_site_gets_the_default(store: SiteOptionStore) -> None:
    """A link from a site with no record still has to be named by something."""
    await store.set(DEFAULT_SCOPE, naming="{name}", dest_folder_id=None)
    assert (await store.resolve(None)).naming == "{name}"


async def test_setting_a_site_twice_replaces_rather_than_stacks(store: SiteOptionStore) -> None:
    await store.set("youtube", naming="{name}", dest_folder_id=None)
    await store.set("youtube", naming="{creator}", dest_folder_id=None)
    assert (await store.resolve("youtube")).naming == "{creator}"
    assert list((await store.all()).keys()) == ["youtube"]


async def test_a_site_given_a_tool_still_follows_the_shared_naming_and_folder(
    temp_db: Database, store: SiteOptionStore
) -> None:
    """The third answer falls back on its own terms, exactly as the first two do.

    An all-or-nothing row is the failure this whole store is shaped against: picking a downloader
    for one Site must not quietly detach it from its naming or from a folder set for everything.
    """
    folder = await _a_folder(temp_db)
    await store.set(DEFAULT_SCOPE, naming="{site}", dest_folder_id=folder, downloader=None)
    await store.set("bunkr", naming=None, dest_folder_id=None, downloader="ytdlp")

    resolved = await store.resolve("bunkr")
    assert resolved.downloader == "ytdlp"
    assert resolved.naming == _shipped("bunkr")
    assert resolved.dest_folder_id == folder


async def test_a_tool_set_for_everything_reaches_a_site_with_no_row(
    store: SiteOptionStore,
) -> None:
    """And the other way round: the default scope is a real answer for a Site nobody has
    touched, and for an address the catalog does not know at all."""
    await store.set(DEFAULT_SCOPE, naming=None, dest_folder_id=None, downloader="gallerydl")

    assert (await store.resolve("bunkr")).downloader == "gallerydl"
    assert (await store.resolve(None)).downloader == "gallerydl"


# --- what a save says in History ------------------------------------------------------------------


async def _lines(database: Database) -> list[tuple[str, str, dict[str, object]]]:
    """Every setting line written, oldest first: its key, the words it names, and its payload."""
    rows = await database.fetch_all(
        "SELECT s.subject_id AS key, s.name AS name, d.payload AS payload"
        " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'edited' AND s.kind = 'setting' ORDER BY d.decided_at, d.rowid"
    )
    return [(str(row["key"]), str(row["name"]), json.loads(row["payload"])) for row in rows]


async def test_saving_a_sites_settings_writes_one_history_line_per_answer_that_moved(
    store: SiteOptionStore, temp_db: Database, actors: Actors
) -> None:
    """The settings hub's shape: the setting as the subject, its key, what it was and what it is.
    A save that moves nothing says nothing; a folder is said by its name, never its id."""
    folder = await _a_folder(temp_db)
    by = Actor.user(actors.admin.id)

    await store.set("tiktok", naming="{name}", dest_folder_id=None, actor=by)
    await store.set("tiktok", naming="{name}", dest_folder_id=None, actor=by)
    await store.set(
        "tiktok", naming="{name}", dest_folder_id=folder, actor=by, folders={folder: "Library"}
    )
    await store.clear("tiktok", actor=by, folders={folder: "Library"})

    lines = await _lines(temp_db)
    assert [(key, name) for key, name, _ in lines] == [
        ("site_options.tiktok.naming", "TikTok's name template"),
        ("site_options.tiktok.dest_folder_id", "TikTok's download folder"),
        ("site_options.tiktok.naming", "TikTok's name template"),
        ("site_options.tiktok.dest_folder_id", "TikTok's download folder"),
    ]
    first, folder_set, _, folder_cleared = (payload for _, _, payload in lines)
    assert first == {
        "key": "site_options.tiktok.naming",
        "before": None,
        "after": "{name}",
        "before_said": "Sift's name",
        "after_said": "{name}",
    }
    assert (folder_set["before_said"], folder_set["after_said"]) == (
        "the default downloads folder",
        "Library",
    )
    assert folder_set["after"] is None
    assert folder_cleared["before_said"] == "Library"


async def test_the_rule_for_other_addresses_and_a_kept_name_are_said_in_words(
    store: SiteOptionStore, temp_db: Database, actors: Actors
) -> None:
    by = Actor.user(actors.admin.id)

    await store.set(DEFAULT_SCOPE, naming="{site} - {name}", dest_folder_id=None, actor=by)
    await store.set("tiktok", naming="", dest_folder_id=None, actor=by)

    (_, other, _), (_, _, kept) = await _lines(temp_db)
    assert other == "The name template for other addresses"
    assert kept["after_said"] == "the name the Site gave it"


async def test_a_save_with_no_person_behind_it_writes_no_line(
    store: SiteOptionStore, temp_db: Database
) -> None:
    await store.set("tiktok", naming="{name}", dest_folder_id=None)

    assert await _lines(temp_db) == []
