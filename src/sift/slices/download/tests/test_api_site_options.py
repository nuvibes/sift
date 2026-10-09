# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one Site does differently, the Sites table and the pictures, against the real application."""

from __future__ import annotations

import functools
import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.slices.download.tests.api_support import (
    _a_folder_in,
    _finished_from,
    _named_from,
    _on_the_apps_loop,
    _write_row,
    sign_in,
)
from sift.testing.library import seed_art

pytestmark = [pytest.mark.integration]


def test_the_sites_sift_knows_are_readable_and_say_what_has_been_tried(
    client: TestClient,
) -> None:
    sign_in(client, "admin")
    listing = client.get("/api/supported-sites")

    assert listing.status_code == 200
    sites = {site["name"]: site for site in listing.json()}
    assert sites["YouTube"]["bulk"] is True
    assert sites["RedGIFs"]["bulk"] is True
    assert sites["Coomer"]["bulk"] is True
    # Two exceptions, and the list is asserted whole so a third cannot be added quietly. Instagram
    # answers being asked for a whole profile by locking the account rather than by refusing the
    # download; Discord has no profile to take at all, since an address there names one attachment.
    assert sites["Instagram"]["bulk"] is False
    assert sites["Discord"]["bulk"] is False
    assert sorted(name for name, site in sites.items() if not site["bulk"]) == [
        "Discord",
        "Instagram",
    ]
    # A per-site fact that reaches the wire in both states, which is the only thing this endpoint
    # can be asked. Bunkr has been run and found working; RedTube is listed so its traffic can be
    # routed and has never been tried. A matrix that marks a site tested because the code looks
    # right is worth less than none, because it is believed.
    assert sites["Bunkr"]["tested"] is True
    assert sites["RedTube"]["tested"] is False


def test_an_admin_sets_what_one_site_does_differently(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.put("/api/site-options/youtube", json={"naming": "{creator}"}).status_code == 204
    options = client.get("/api/site-options").json()
    assert [one["scope"] for one in options["sites"]] == ["youtube"]
    assert options["sites"][0]["naming"] == "{creator}"
    # The tokens come back with the values, so a screen never holds its own copy of the list.
    assert "site" in options["tokens"]

    assert client.delete("/api/site-options/youtube").status_code == 204
    assert client.get("/api/site-options").json()["sites"] == []


def test_downloads_cannot_be_pointed_at_a_folder_that_is_not_there(client: TestClient) -> None:
    """Checked in the server rather than only in the chooser.

    The chooser offers nothing else, which is a courtesy to whoever is looking at it. This is what
    makes it true of a request that never went near one, and without it the setting stores a
    folder every download into it is refused by, with the refusal arriving later and somewhere else.
    """
    sign_in(client, "admin")

    answer = client.put(
        "/api/site-options/youtube",
        json={"naming": "{creator}", "dest_folder_id": "01HX000000000000000000GONE"},
    )

    assert answer.status_code == 400
    assert "not there any more" in answer.json()["detail"]


def test_downloads_cannot_be_pointed_into_a_folder_sift_cannot_write_in(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder the filesystem will not let Sift write in is a real folder downloads can never
    land in, so it is refused when it is chosen rather than at the first download."""
    sign_in(client, "admin")
    folder_id = _a_folder_in(client, tmp_path)
    monkeypatch.setattr("sift.kernel.content.library.is_writable", lambda _path: False)

    answer = client.put(
        "/api/site-options/youtube",
        json={"naming": "{creator}", "dest_folder_id": folder_id},
    )

    assert answer.status_code == 400
    assert "not allowed to write" in answer.json()["detail"]


def test_downloads_cannot_be_pointed_at_a_folder_whose_library_went_in_between(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder is found and its library is not, which is a race rather than a state.

    A folder row does not outlive its library (the reference cascades), so removing one takes the
    folder too and the check above answers first. What this guards is the gap between the two reads.
    Without it the setting would store a folder resolved against a library that is gone, and every
    download into it would be refused somewhere else entirely.
    """
    sign_in(client, "admin")
    folder_id = _a_folder_in(client, tmp_path)

    async def gone(_root_id: str) -> None:
        return None

    monkeypatch.setattr(client.app.state.library, "get_root", gone)  # type: ignore[attr-defined]

    answer = client.put(
        "/api/site-options/youtube",
        json={"naming": "{creator}", "dest_folder_id": folder_id},
    )

    assert answer.status_code == 400
    assert "not there any more" in answer.json()["detail"]


def test_downloads_can_be_pointed_at_a_folder_sift_may_write_in(
    client: TestClient, tmp_path: Path
) -> None:
    """The case the two refusals above exist to let through."""
    sign_in(client, "admin")
    folder_id = _a_folder_in(client, tmp_path)

    answer = client.put(
        "/api/site-options/youtube",
        json={"naming": "{creator}", "dest_folder_id": folder_id},
    )

    assert answer.status_code == 204
    stored = client.get("/api/site-options").json()["sites"][0]
    assert stored["dest_folder_id"] == folder_id


def test_a_setting_whose_old_folder_went_in_between_is_still_moved_to_the_new_one(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The History line names each folder the change touched; one gone since it was chosen has
    no name left to say, and that costs the line its word, never the change."""
    sign_in(client, "admin")
    first = _a_folder_in(client, tmp_path / "one")
    second = _a_folder_in(client, tmp_path / "two")
    assert (
        client.put(
            "/api/site-options/youtube", json={"naming": "{creator}", "dest_folder_id": first}
        ).status_code
        == 204
    )
    library = client.app.state.library  # type: ignore[attr-defined]
    real = library.get_folder

    async def first_gone(folder_id: str) -> object:
        return None if folder_id == first else await real(folder_id)

    monkeypatch.setattr(library, "get_folder", first_gone)

    answer = client.put(
        "/api/site-options/youtube", json={"naming": "{creator}", "dest_folder_id": second}
    )

    assert answer.status_code == 204, answer.text
    assert client.get("/api/site-options").json()["sites"][0]["dest_folder_id"] == second


def test_a_scope_naming_no_site_is_refused(client: TestClient) -> None:
    """A rule stored against nothing is a rule that looks set and is never read, which is
    indistinguishable on screen from one being ignored."""
    sign_in(client, "admin")
    assert client.put("/api/site-options/not-a-site", json={"naming": "x"}).status_code == 404
    assert client.delete("/api/site-options/not-a-site").status_code == 204


def test_a_creator_is_refused_on_a_site_that_never_says_who_posted(client: TestClient) -> None:
    """`{creator}` on Discord or a file host is empty on every download, so it is refused where it
    is saved (in any case, beside any other word) and allowed where somebody can fill it."""
    sign_in(client, "admin")
    for scope, template in (("discord", "{site}{creator}"), ("bunkr", "{Creator} - {name}")):
        refused = client.put(f"/api/site-options/{scope}", json={"naming": template})
        assert refused.status_code == 400, scope
        assert "{creator} would always be empty" in refused.json()["detail"]
    assert client.get("/api/site-options").json()["sites"] == []

    # The same Sites take every other word, and "keep the name" is not a template at all.
    assert (
        client.put("/api/site-options/discord", json={"naming": "{site} - {name}"}).status_code
        == 204
    )
    assert client.put("/api/site-options/bunkr", json={"naming": ""}).status_code == 204
    # A Site whose uploader is a person, and the rule everything follows, keep the word.
    assert client.put("/api/site-options/tiktok", json={"naming": "{creator}"}).status_code == 204
    assert (
        client.put("/api/site-options/%2Adefault%2A", json={"naming": "{creator}"}).status_code
        == 204
    )


def test_the_default_cannot_be_cleared_because_everything_has_to_follow_something(
    client: TestClient,
) -> None:
    sign_in(client, "admin")
    assert client.delete("/api/site-options/%2Adefault%2A").status_code == 400


def test_the_preview_is_built_from_the_site_it_is_asked_about(client: TestClient) -> None:
    """The one thing a template can promise that a Site cannot keep.

    `{creator}` is filled from whoever posted a file, and seventeen of the twenty-six Sites have
    nobody to name: a file host's uploader is a bucket, Reddit's username is a board. A preview
    that always showed a creator would teach the opposite of what happens, on two Sites in three,
    and it would be discovered as a run of files all missing the same part of their name.
    """
    sign_in(client, "admin")
    body = {"naming": "{creator} - {name}"}

    # A Site whose username IS a person, and a name in TikTok's own shape: its files arrive as a
    # twelve-character code, never as a title.
    on_tiktok = client.post("/api/site-options/preview", json={**body, "scope": "tiktok"})
    assert on_tiktok.status_code == 200
    assert on_tiktok.json()["example"] == "someone - 3f9a1c7e5b2d"

    # A file host, whose uploader is a bucket, and Reddit, whose username is a SUBREDDIT: the
    # preview refuses the word in the words the save refuses it with, rather than showing a name
    # the save would never accept.
    for scope in ("bunkr", "reddit"):
        refused = client.post("/api/site-options/preview", json={**body, "scope": scope})
        saved = client.put(f"/api/site-options/{scope}", json=body)
        assert refused.status_code == 400, scope
        assert refused.json()["detail"] == saved.json()["detail"], scope

    # And the Site's own name is used, not a stand-in.
    named = client.post("/api/site-options/preview", json={"naming": "{site}", "scope": "bunkr"})
    assert named.json()["example"] == "Bunkr"


def test_a_preview_for_a_site_that_is_not_there_still_previews(client: TestClient) -> None:
    """A preview is the cheapest thing on the screen and the worst it should do when handed a name
    nobody recognises is show a less specific example. Refusing would leave the box with no feedback
    at exactly the moment somebody is typing into it."""
    sign_in(client, "admin")

    shown = client.post(
        "/api/site-options/preview", json={"naming": "{site}", "scope": "not-a-site"}
    )
    assert shown.status_code == 200
    assert shown.json()["example"] == "Vimeo"


def test_a_name_template_is_checked_at_the_setting_rather_than_a_hundred_files_later(
    client: TestClient,
) -> None:
    sign_in(client, "admin")

    # With no Site named, the example is an address Sift has no Site for (the only addresses
    # the rule for all Sites still governs), filed under its own label the way the catch-all
    # files one.
    shown = client.post("/api/site-options/preview", json={"naming": "{site} - {name}"})
    assert shown.status_code == 200
    assert shown.json()["example"] == "Vimeo - A_video_title"

    # An empty template is a real answer (keep the name the site gave it), and the preview
    # says so by producing nothing rather than by refusing.
    assert client.post("/api/site-options/preview", json={"naming": ""}).json()["example"] == ""


def test_the_sites_table_carries_the_refusals_each_site_is_known_to_give(
    client: TestClient,
) -> None:
    """The question in front of that table is rarely "is this supported": it is "why did that not
    work", and an age gate answers with an ordinary 403 that says nothing at all."""
    sign_in(client, "admin")
    sites = {one["key"]: one for one in client.get("/api/supported-sites").json()}
    assert any(site["walls"] for site in sites.values())
    assert sites["gofile"]["walls"] == []


def test_the_sites_table_says_what_each_site_does_without_cookies(client: TestClient) -> None:
    """The catalog's declaration reaches the wire for every Site, in the catalog's own words.

    The wire names the three answers as a literal rather than importing the catalog's enum into the
    models, so the two lists are held equal here: a fourth answer added to one and not the other
    would otherwise reach the screen as a word it has no sentence for.
    """
    from typing import get_args

    from sift.slices.download.models import SupportedSite
    from sift.slices.download.sources.sites.catalog import SITES, CookieNeed

    assert set(get_args(SupportedSite.model_fields["cookies"].annotation)) == {
        need.value for need in CookieNeed
    }
    sign_in(client, "admin")
    sites = {one["key"]: one for one in client.get("/api/supported-sites").json()}
    assert {key: site["cookies"] for key, site in sites.items()} == {
        record.key: record.cookies.value for record in SITES
    }
    # And the sentence and the second answer ride with it, in the catalog's own words.
    assert {key: site["cookies_why"] for key, site in sites.items()} == {
        record.key: record.cookies_why for record in SITES
    }
    assert sites["instagram"]["cookies"] == "not_needed"
    assert sites["instagram"]["cookies_with_a_tool"] == "required"
    assert sites["youtube"]["cookies"] == "partial"
    assert sites["youtube"]["cookies_with_a_tool"] is None


def test_which_names_have_a_picture_is_one_question(client: TestClient, tmp_path: Path) -> None:
    """One request for a whole screen. Without it a page of twenty People makes twenty requests
    and nearly all of them are answered "no picture", which is a slow screen built out of correct
    answers. A creator is filed as `site:username`; a scope with no colon is not a creator and is
    never listed: a Site's mark is the shipped icon pack, and nothing serves a fetched one."""
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_art(db_path, "youtube", cache_dir=tmp_path / "cache")
    seed_art(db_path, "youtube:someone", cache_dir=tmp_path / "cache")

    assert client.get("/api/creator-art").json()["usernames"] == ["someone"]
    assert client.get("/api/site-art").status_code == 404
    assert client.get("/api/site-art/youtube").status_code == 404


def test_a_creators_picture_is_found_by_their_name_alone(
    client: TestClient, tmp_path: Path
) -> None:
    """For the screens that show People, which know a name and nothing about where a file came
    from. A name Sift has no picture for is answered 404 and keeps its monogram."""
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_art(db_path, "youtube:someone", cache_dir=tmp_path / "cache")

    assert client.get("/api/creator-art/someone").status_code == 200
    assert client.get("/api/creator-art/nobody").status_code == 404


def test_a_usernames_picture_on_one_site_is_that_sites_alone(
    client: TestClient, tmp_path: Path
) -> None:
    """Asked with the Site, the picture is the one kept for that username on that Site: the same
    name on another Site may be somebody else, and a stranger's face is worse than a letter."""
    sign_in(client, "admin")
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    seed_art(db_path, "youtube:someone", cache_dir=tmp_path / "cache")

    here = client.get("/api/creator-art/someone", params={"site": "YouTube"})
    elsewhere = client.get("/api/creator-art/someone", params={"site": "Instagram"})

    # Red on the hosted runner only: the answer says which of the three it was.
    from sift.slices.download.art import creator_scope_for

    seeded = tmp_path / "cache" / "covers" / "youtube_someone.jpg"
    saw = (
        f"scope={creator_scope_for(site='YouTube', username='someone', address=None)!r}"
        f" seeded={seeded.exists()} cache={os.environ.get('SIFT_CACHE_DIR')}"
    )
    assert here.status_code == 200, f"{here.text} {saw}"
    assert elsewhere.status_code == 404, elsewhere.text


def test_a_downloader_sift_does_not_have_is_refused_rather_than_stored(client: TestClient) -> None:
    """A tool name nothing runs would be stored, shown back on the screen as the chosen answer, and
    ignored by every download, which is indistinguishable from the setting not working.

    Checked at the door for the same reason the destination folder is: the alternative is a refusal
    that arrives later, somewhere else, on a download somebody has stopped watching.
    """
    sign_in(client, "admin")

    refused = client.put(
        "/api/site-options/youtube", json={"naming": "{creator}", "downloader": "wget"}
    )

    assert refused.status_code == 400, refused.text
    assert "downloader" in refused.json()["detail"].lower()
    # And nothing was stored: a refused answer leaves the Site following the catalog.
    assert client.get("/api/site-options").json()["sites"] == []

    # The known positive, so this is a refusal rather than a route that rejects every tool.
    assert (
        client.put(
            "/api/site-options/youtube", json={"naming": "{creator}", "downloader": "ytdlp"}
        ).status_code
        == 204
    )


def test_a_row_names_the_folder_it_goes_into_never_the_default(
    client: TestClient, tmp_path: Path
) -> None:
    """A waiting row says WHICH folder, by name and path, worked out by the job's own rule.

    Nothing was chosen for this download, so it follows the folder set for everything, and the row
    says that folder's name rather than "the default folder", which named nothing.
    """
    sign_in(client, "admin")
    folder_id = _a_folder_in(client, tmp_path)
    assert (
        client.put(
            "/api/site-options/*default*", json={"naming": "{name}", "dest_folder_id": folder_id}
        ).status_code
        == 204
    )
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})

    row = client.get("/api/downloads").json()["downloads"][0]

    assert row["dest_folder_id"] is None
    assert row["folder"]["id"] == folder_id
    assert row["folder"]["name"]
    assert Path(row["folder"]["path"]).samefile(tmp_path / "library")


def test_a_landed_row_names_the_folder_its_job_recorded_and_a_waiting_one_the_rule(
    client: TestClient, tmp_path: Path
) -> None:
    """ "Saved to" is a record: a row that landed names the folder its job resolved,
    and the setting changed since does not move it. A row still to run names where the rule sends
    it now, because running it resolves the folder again."""
    sign_in(client, "admin")
    now_folder = _a_folder_in(client, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    made = client.post("/api/library/roots", json={"abs_path": str(elsewhere)})
    assert made.status_code == 201, made.text
    then_folder = str(
        client.get("/api/library/folders", params={"root": made.json()["id"]}).json()["folders"][0][
            "id"
        ]
    )
    assert (
        client.put(
            "/api/site-options/*default*", json={"naming": "{name}", "dest_folder_id": now_folder}
        ).status_code
        == 204
    )
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/2"})
    rows = {row["url"]: row for row in client.get("/api/downloads").json()["downloads"]}
    landed = rows["https://www.tiktok.com/@a/video/1"]["id"]
    waiting = rows["https://www.tiktok.com/@a/video/2"]["id"]
    # Both recorded against the folder the setting named before it was changed; one has landed.
    _write_row(
        client,
        "UPDATE downloads SET folder_id = ?, state = 'done' WHERE id = ?",
        (then_folder, landed),
    )
    _write_row(client, "UPDATE downloads SET folder_id = ? WHERE id = ?", (then_folder, waiting))

    by_id = {row["id"]: row for row in client.get("/api/downloads").json()["downloads"]}

    assert by_id[landed]["folder"]["id"] == then_folder
    assert by_id[waiting]["folder"]["id"] == now_folder


def test_a_preview_fills_the_words_about_the_post(client: TestClient) -> None:
    """`{id}`, `{title}` and `{posted}` preview from the made-up post, so a template using them
    shows its shape before the first download. `{posted}` is a date that is plainly not today's."""
    sign_in(client, "admin")
    shown = client.post(
        "/api/site-options/preview",
        json={"naming": "{creator} - {posted} - {id} {title}", "scope": "tiktok"},
    )
    assert shown.json()["example"] == "someone - 2026-08-13 - 7401234567890123456 A post caption"


def test_a_preview_borrows_the_newest_real_creator_of_the_site(client: TestClient) -> None:
    """The one real fact a downloads row holds is who posted it, so the example reads like this
    library's files: the newest finished download from the Site lends its creator, and a Site
    nothing has finished from keeps the invented one."""
    sign_in(client, "admin")

    def shown(template: str, scope: str) -> str:
        answer = client.post("/api/site-options/preview", json={"naming": template, "scope": scope})
        assert answer.status_code == 200, scope
        return str(answer.json()["example"])

    invented = shown("{creator}", "tiktok")
    assert invented and invented != "harlowquin"

    older = client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}).json()
    newer = client.post("/api/downloads", json={"url": "https://www.tiktok.com/@b/video/2"}).json()
    database = client.app.state.database  # type: ignore[attr-defined]
    for row, poster in ((older, "marchfield"), (newer, "harlowquin")):
        _on_the_apps_loop(
            client,
            functools.partial(_finished_from, site="TikTok", username=poster),
            database,
            row["id"],
        )

    assert shown("{creator}", "tiktok") == "harlowquin"
    # Another Site is untouched by TikTok's downloads.
    assert (
        shown("{creator}", "youtube") == invented or shown("{creator}", "youtube") != "harlowquin"
    )


def test_a_preview_is_the_newest_download_from_the_site_as_it_was_named(
    client: TestClient,
) -> None:
    """A finished download keeps what its file was named from, so the preview is a wholly real name
    from this library: its ID, title, posting date and the name it arrived with, not only its
    creator. A Site with no such row keeps its invented example."""
    sign_in(client, "admin")

    def shown(template: str, scope: str) -> str:
        answer = client.post("/api/site-options/preview", json={"naming": template, "scope": scope})
        assert answer.status_code == 200, scope
        return str(answer.json()["example"])

    rule = "{creator} - {posted} - {id} - {title} - {name}"
    row = client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}).json()
    database = client.app.state.database  # type: ignore[attr-defined]
    _on_the_apps_loop(client, _named_from, database, row["id"])

    assert shown(rule, "tiktok") == (
        "harlowquin - 2026-09-01 - 7409999999999999999 - Tide pools - b7c1e9a2f4d6"
    )
    assert shown("{name}", "bunkr") == "holiday_clip_04"


def test_a_preview_leaves_empty_what_the_site_never_says(client: TestClient) -> None:
    """Each word fills only where the Site can fill it. Instagram never says when a post was made
    and a file host says nothing but a file name, so the preview shows them that way. The same
    template on TikTok is a different, fuller name, which is the point of asking per Site."""
    sign_in(client, "admin")

    def shown(template: str, scope: str) -> str:
        answer = client.post("/api/site-options/preview", json={"naming": template, "scope": scope})
        assert answer.status_code == 200, scope
        return str(answer.json()["example"])

    rule = "{site} - {posted} - {id} - {title}"
    assert shown(rule, "instagram") == "Instagram - CxY7kLm2PqR"
    assert shown(rule, "tiktok") == "TikTok - 2026-08-13 - 7401234567890123456 - A post caption"
    assert shown(rule, "bunkr") == "Bunkr"
    # And a file host's `{name}` is the uploader's file name, not a video title.
    assert shown("{name}", "bunkr") == "holiday_clip_04"


def test_every_site_has_an_example_in_its_own_shape() -> None:
    """The examples are keyed by catalog key, so a Site added to the catalog without one would
    preview as something it is not. And a Site that fills `{id}` has an ID to show for it."""
    from sift.slices.download.sources.sites.catalog import SITES, words_filled

    router = importlib.import_module("sift.slices.download.router_site_options")

    assert set(router._EXAMPLES) == {record.key for record in SITES}
    for record in SITES:
        shape = router._EXAMPLES[record.key]
        assert ("id" in words_filled(record)) == (shape.id is not None), record.key
        if "title" in words_filled(record):
            assert shape.title, record.key
