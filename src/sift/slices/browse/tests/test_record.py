# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's own record: the two fields somebody types onto it, and who may type them.

The rest of the record is measured (a size, a codec, a frame rate) and is not writable at all,
which is why there is nothing here about it. What is worth proving is the half that can be wrong:
who is allowed to write, what a partial write does to the field it did not mention, and whether a
name somebody typed can then be searched for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.changes import About
from sift.kernel.wiring import CHANGES, part_of_app
from sift.slices.browse.tests.conftest import PASSWORD, Library, db_path, sign_in
from sift.testing.auth import establish_session


def test_a_new_file_has_no_title_rather_than_an_empty_one(
    client: TestClient, library: Library
) -> None:
    """Absent and blank have to be one thing, or every screen tests for two."""
    sign_in(client, "admin")

    body = client.get(f"/api/assets/{library.shared}").json()

    assert body["title"] is None
    assert body["download_url"] is None


def test_an_admin_names_a_file_and_it_comes_back_named(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")

    written = client.put(f"/api/assets/{library.shared}", json={"title": "A Better Name"})

    assert written.status_code == 204
    assert client.get(f"/api/assets/{library.shared}").json()["title"] == "A Better Name"


def test_a_title_of_spaces_is_stored_as_nothing(client: TestClient, library: Library) -> None:
    """A record that draws an empty box where a dash belongs, and a search index carrying a row of
    spaces, are both worse than the absence they are pretending not to be."""
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}", json={"title": "A Name"})

    client.put(f"/api/assets/{library.shared}", json={"title": "   "})

    assert client.get(f"/api/assets/{library.shared}").json()["title"] is None


def test_writing_only_the_title_leaves_the_download_link_alone(
    client: TestClient, library: Library
) -> None:
    """IMPORTANT: The trap this route is written against.

    Two optional fields defaulting to None, read attribute by attribute, is a full-row writer in a
    partial writer's clothes: a request carrying a title alone would silently erase an address it
    never mentioned. Nothing on any screen would say so, and the address is not recoverable: the
    ledger's copy is a receipt for the fetch, not a backup of a field somebody corrected.
    """
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}", json={"download_url": "https://example.com/a"})

    client.put(f"/api/assets/{library.shared}", json={"title": "A Name"})

    body = client.get(f"/api/assets/{library.shared}").json()
    assert body["title"] == "A Name"
    assert body["download_url"] == "https://example.com/a"


def test_writing_only_the_download_link_leaves_the_title_alone(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}", json={"title": "A Name"})

    client.put(f"/api/assets/{library.shared}", json={"download_url": "https://example.com/a"})

    body = client.get(f"/api/assets/{library.shared}").json()
    assert body["title"] == "A Name"
    assert body["download_url"] == "https://example.com/a"


def test_a_typed_address_is_stored_cleaned_the_way_a_seeded_one_is(
    client: TestClient, library: Library
) -> None:
    """A typed address is cleaned as a seeded one is.

    Otherwise one column would hold two kinds of value depending on which way it arrived. Nothing
    on any screen would say so, because both spellings work
    perfectly well when you click them: one just carries a tracking parameter and the name of the
    screen it came from, to a field a guest can read.
    """
    sign_in(client, "admin")

    client.put(
        f"/api/assets/{library.shared}",
        json={"download_url": "https://example.com/watch/1?from=downloads&utm_source=x&page=2"},
    )

    stored = client.get(f"/api/assets/{library.shared}").json()["download_url"]
    assert stored == "https://example.com/watch/1?page=2"


def test_a_null_clears_a_field_where_leaving_it_out_does_not(
    client: TestClient, library: Library
) -> None:
    """The two have to mean different things, or there is no way to clear a field at all."""
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}", json={"title": "A Name"})

    client.put(f"/api/assets/{library.shared}", json={"title": None})

    assert client.get(f"/api/assets/{library.shared}").json()["title"] is None


def test_a_request_that_mentions_nothing_changes_nothing(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}", json={"title": "A Name"})

    assert client.put(f"/api/assets/{library.shared}", json={}).status_code == 204
    assert client.get(f"/api/assets/{library.shared}").json()["title"] == "A Name"


def test_a_guest_may_read_a_record_and_may_not_write_one(
    client: TestClient, library: Library
) -> None:
    """What a file is called is a statement about the library, not a preference of whoever is
    looking at it. Reading it is not admin-only; a guest sees the whole record."""
    admin_side = sign_in(client, "admin")
    assert admin_side
    client.put(f"/api/assets/{library.shared}", json={"title": "A Name"})
    guest = sign_in(client, "guest")
    _share(client, library.shared, guest)

    assert client.get(f"/api/assets/{library.shared}").json()["title"] == "A Name"
    assert client.put(f"/api/assets/{library.shared}", json={"title": "Mine"}).status_code == 403


def test_a_file_this_account_may_not_see_answers_no_such_file(
    client: TestClient, library: Library
) -> None:
    """The scoped read comes first and is doing real work: a file a user may not be shown
    answers "no such file" rather than being written to."""
    sign_in(client, "admin")

    answer = client.put("/api/assets/01HX0000000000000000000009", json={"title": "A Name"})

    assert answer.status_code == 404


def test_a_typed_title_can_then_be_searched_for(client: TestClient, library: Library) -> None:
    """An imported title is searchable, and this is the same column. A name
    somebody typed onto a file and then cannot find is worse than no field at all."""
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}", json={"title": "Rafe Underhill"})

    found = client.get("/api/assets", params={"q": "Rafe Underhill"}).json()

    assert library.shared in [one["id"] for one in found["items"]]


def test_a_typed_song_can_then_be_searched_for_too(client: TestClient, library: Library) -> None:
    """The index carries the music beside the title, so a write that changes only the music must
    reindex too, or the old words stay findable."""
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}", json={"music": "Night Drive"})

    found = client.get("/api/assets", params={"q": "Night Drive"}).json()

    assert library.shared in [one["id"] for one in found["items"]]


def _name_shared(path: Path, asset_id: str, song: str, from_id: str = "elsewhere") -> None:
    """A song shared onto this file from another, exactly as the spread writes it: the receipt on
    the ledger, the name in the field."""
    import asyncio

    from sift.kernel.content.identity import MUSIC_SHARED, seed_music_on
    from sift.kernel.db import Database
    from sift.kernel.ledger import Object, Reversal

    async def seed() -> None:
        database = Database(path)
        await database.connect()
        try:
            async with database.write() as connection:
                assert await seed_music_on(
                    connection,
                    asset_id,
                    song,
                    source=MUSIC_SHARED,
                    from_asset=Object(kind="asset", id=from_id, name="other.mp4"),
                    receipt=Reversal(queue="music_names", title="t", detail="d"),
                )
        finally:
            await database.close()

    asyncio.run(seed())


def test_a_shared_song_name_hands_an_admin_its_undo_door_and_a_guest_none(
    client: TestClient, library: Library
) -> None:
    """The line under the Music field offers Undo through the receipt the spread wrote, handed
    over by the server rather than guessed from History's words. A reversal is an admin's act, so
    a guest is told where the name came from and nothing to press."""
    _name_shared(db_path(client), library.shared, "Blue - Marla Quist")
    guest = sign_in(client, "guest")

    sign_in(client, "admin")
    _share(client, library.shared, guest)
    answer = client.get(f"/api/assets/{library.shared}")
    seen = answer.json()
    assert answer.status_code == 200 and "music_source" in seen, (answer.status_code, seen)
    assert seen["music_source"] == "shared"
    assert seen["music_undo"]["kind"] == "decision" and seen["music_undo"]["id"]

    sign_in(client, "guest")
    answer = client.get(f"/api/assets/{library.shared}")
    seen = answer.json()
    assert answer.status_code == 200 and "music_source" in seen, (answer.status_code, seen)
    assert seen["music_source"] == "shared" and seen["music_undo"] is None


def test_a_shared_song_names_the_file_it_came_from_only_to_a_viewer_who_may_see_that_file(
    client: TestClient, library: Library
) -> None:
    """The source file is named and linked where the viewer may open it; to anybody else the
    name would be a disclosure, so only the fact that it was shared is said."""
    _name_shared(db_path(client), library.shared, "Blue - Marla Quist", from_id=library.private)
    guest = sign_in(client, "guest")

    sign_in(client, "admin")
    _share(client, library.shared, guest)
    seen = client.get(f"/api/assets/{library.shared}").json()
    assert seen["music_from"] == {"id": library.private, "name": "private.mp4"}

    sign_in(client, "guest")
    seen = client.get(f"/api/assets/{library.shared}").json()
    assert seen["music_source"] == "shared"
    assert seen["music_from"] is None


def _edits(client: TestClient) -> int:
    """How many edits the record holds, read through the application's own database handle."""
    database = client.app.state.database  # type: ignore[attr-defined]
    portal = client.portal
    assert portal is not None, "the client is used inside its context, where its portal is open"
    row = portal.call(
        database.fetch_one, "SELECT COUNT(*) AS n FROM workbench_decisions WHERE verb = 'edited'"
    )
    assert row is not None
    return int(row["n"])


def test_a_save_that_moves_nothing_writes_no_edit(client: TestClient, library: Library) -> None:
    """Unchanged fields are not an edit: pressing Save on the record as it stands adds nothing to
    the history."""
    sign_in(client, "admin")
    client.put(f"/api/assets/{library.shared}", json={"title": "A Name"})
    before = _edits(client)

    assert client.put(f"/api/assets/{library.shared}", json={"title": "A Name"}).status_code == 204

    assert _edits(client) == before


def test_a_file_whose_row_vanishes_around_the_save_still_records_the_links_it_moved(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scoped read allowed the save, and the row is then gone when the record is read back (a
    delete landing between the two). The fields cannot be compared, the links still can, and
    the edit is still written down rather than lost."""
    from sift.kernel.content.identity import ContentStore

    sign_in(client, "admin")
    before = _edits(client)

    async def gone(self: ContentStore, asset_id: str) -> None:
        return None

    monkeypatch.setattr(ContentStore, "get", gone)
    written = client.put(
        f"/api/assets/{library.shared}", json={"links": ["https://example.com/one"]}
    )

    assert written.status_code == 204
    assert _edits(client) == before + 1


def _share(client: TestClient, asset_id: str, user_id: str) -> None:
    from sift.slices.browse.tests.conftest import share

    share(client, asset_id, user_id)


def test_when_what_is_in_a_file_came_out_is_its_own_field(
    client: TestClient, library: Library
) -> None:
    """`added_at` says when this library first saw the file, which is a different question.

    A clip downloaded last night can be ten years old, so sorting a library by when it was
    collected is not the same as sorting it by when it came out. Only an external source knows the
    second one, which is why it is typed or imported rather than measured.
    """
    sign_in(client, "admin")

    written = client.put(f"/api/assets/{library.shared}", json={"release_date": "1991-02-02"})

    assert written.status_code == 204, written.text
    assert client.get(f"/api/assets/{library.shared}").json()["release_date"] == "1991-02-02"


def test_writing_only_the_release_date_leaves_the_other_two_alone(
    client: TestClient, library: Library
) -> None:
    """The same partial-write rule the other fields are held to. Three optional fields read
    attribute by attribute is a full-row writer wearing a partial writer's clothes."""
    sign_in(client, "admin")
    client.put(
        f"/api/assets/{library.shared}",
        json={"title": "A Name", "download_url": "https://example.com/a"},
    )

    client.put(f"/api/assets/{library.shared}", json={"release_date": "1991-02-02"})

    body = client.get(f"/api/assets/{library.shared}").json()
    assert body["title"] == "A Name"
    assert body["download_url"] == "https://example.com/a"
    assert body["release_date"] == "1991-02-02"


def test_the_rest_of_the_record_is_written_and_read_back(
    client: TestClient, library: Library
) -> None:
    """The five fields the record surface added, in one request.

    Written together because that is how the form saves: one press, every field it holds. What is
    worth asserting is that each one lands where it was addressed: a route that read them in the
    wrong order would put a date into a code and nothing would say so.
    """
    sign_in(client, "admin")

    written = client.put(
        f"/api/assets/{library.shared}",
        json={
            "details": "What it is about.",
            "production_date": "1990-06-01",
            "site_code": "SITE-1234",
            "music": "Plain Jane - Someone",
            "links": ["https://example.com/one", "https://example.com/two"],
        },
    )

    assert written.status_code == 204, written.text
    body = client.get(f"/api/assets/{library.shared}").json()
    assert body["details"] == "What it is about."
    assert body["production_date"] == "1990-06-01"
    assert body["site_code"] == "SITE-1234"
    assert body["music"] == "Plain Jane - Someone"
    assert body["links"] == ["https://example.com/one", "https://example.com/two"]


def test_writing_one_of_them_leaves_the_other_four_alone(
    client: TestClient, library: Library
) -> None:
    """The same partial-write rule every field on this route is held to, and the reason it has to
    be asserted per field rather than once: the trap is a route that reads an optional field
    attribute by attribute, and each field is its own chance to fall into it."""
    sign_in(client, "admin")
    client.put(
        f"/api/assets/{library.shared}",
        json={
            "details": "What it is about.",
            "production_date": "1990-06-01",
            "site_code": "SITE-1234",
            "music": "Plain Jane - Someone",
            "links": ["https://example.com/one"],
        },
    )

    client.put(f"/api/assets/{library.shared}", json={"music": "Something Else"})

    body = client.get(f"/api/assets/{library.shared}").json()
    assert body["music"] == "Something Else"
    assert body["details"] == "What it is about."
    assert body["production_date"] == "1990-06-01"
    assert body["site_code"] == "SITE-1234"
    assert body["links"] == ["https://example.com/one"]


def test_clearing_every_link_sends_nothing_and_means_an_empty_set(
    client: TestClient, library: Library
) -> None:
    """A list is replaced as a whole, and a form that cleared every row sends `null` rather than a
    list of nothing, so `null` has to mean "none of them" here and not "leave them alone".

    The two are told apart by the field being MENTIONED at all, which is the same rule the other
    four follow. Not mentioning it is what leaves it as it was; see the test above.
    """
    sign_in(client, "admin")
    client.put(
        f"/api/assets/{library.shared}",
        json={"links": ["https://example.com/one", "https://example.com/two"]},
    )

    client.put(f"/api/assets/{library.shared}", json={"links": None})

    assert client.get(f"/api/assets/{library.shared}").json()["links"] == []


def test_a_record_edit_is_told_to_the_other_tabs_of_everyone_who_may_see_the_file(
    client: TestClient, library: Library
) -> None:
    """A renamed file reaches every other open tab, not only the History pane.

    Told to every admin (for the pane) and to nobody else, a guest the file is shared with would
    keep drawing the old title until reloaded. The audience is every admin and every
    user given anything; a user given nothing is not told.
    """
    admin = sign_in(client, "admin")
    guest = sign_in(client, "guest")
    loner, _token, _csrf = establish_session(
        db_path(client), role="guest", username="browse-loner", password=PASSWORD
    )
    _share(client, library.shared, guest)
    sign_in(client, "admin")
    bus = part_of_app(client.app, CHANGES)  # type: ignore[arg-type]
    admin_tab, guest_tab, loner_tab = (bus.subscribe(one) for one in (admin, guest, loner))

    written = client.put(f"/api/assets/{library.shared}", json={"title": "A Better Name"})

    assert written.status_code == 204
    assert About.LIBRARY in admin_tab.take(as_admin=True).about
    assert About.LIBRARY in guest_tab.take(as_admin=False).about
    assert About.LIBRARY not in loner_tab.take(as_admin=False).about
