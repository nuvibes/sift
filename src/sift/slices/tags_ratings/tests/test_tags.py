# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tags: one parent at most, unique without regard to case, seeded with nothing, moving no files."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Repository
from sift.kernel.db import Database
from sift.slices.tags_ratings.service import TagService
from sift.slices.tags_ratings.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    db_path,
    grant_on_tag,
    grants_naming,
    make_tag,
    read,
    sign_in,
    write,
)
from sift.testing.auth import TEST_PIN, give_pin
from sift.testing.fixtures import Actors
from sift.testing.library import hidden_row

pytestmark = [pytest.mark.integration]


# --- a tree, and empty to begin with -----------------------------------------------------------


def test_a_fresh_install_has_no_tags(client: TestClient) -> None:
    """There is no starter set. Every tag in a library was made by somebody there."""
    sign_in(client)

    assert client.get("/api/tags").json()["items"] == []


def test_the_schema_holds_one_parent_and_nothing_more(client: TestClient) -> None:
    """A tag has at most one parent, held by the schema's columns, asserted whole."""
    sign_in(client)
    columns = {
        str(row["name"])
        for row in read(db_path(client), "SELECT name FROM pragma_table_info('tags')")
    }

    # Exhaustive, so an added column turns up here. Hiding a tag lives in a row of its own.
    assert columns == {
        "id",
        "name",
        # No colour column. `sort_name` orders it: `COLLATE NOCASE` folds ASCII only.
        "name_sort",
        "cover_asset_id",
        # Which moment of that file, or an uploaded picture; null is the file's own picture.
        "cover_upload_id",
        "cover_at_ms",
        # The window of that picture the card is drawn as (catalog v64, a REFRAMED cover).
        "cover_frame",
        "created_at",
        # What it means, and its one category: a word, not a link to another tag.
        "description",
        "category",
        # WHICH stash-box invented it, as against which ones have since described it.
        "created_by_box_id",
        # Who made it: which kind of maker, which box and which user.
        "created_by_kind",
        "created_by_user_id",
        # Which pass, for a tag Sift made itself: `produced`, `watermark` or `stash`.
        "created_by_via",
        # Kept local: no stash-box may write to this tag again, read from the row itself.
        "keep_local",
        # The ONE tag this one is filed under (catalog v71). The only pointer at another tag.
        "parent_id",
        # DO NOT SWAP: a yes/no kept on the row for the reason `keep_local` is.
        "keep_from_swaps",
        # The cover's own bookkeeping: when somebody took the cover off, and which file the
        # default rule drew it from. Neither points at another tag.
        "cover_cleared_at",
        "cover_by_default",
        # When the record was last edited (`kernel.access.edited`).
        "edited_at",
        # Which act made a tag the `produced` pass made (`compress` or `edit`), beside its via.
        "created_by_act",
    }
    assert not columns & {"parent", "path", "depth", "lft", "rgt", "tag_id"}


# --- one tag, whatever case it is typed in ------------------------------------------------------


def test_two_spellings_of_one_name_are_one_tag(client: TestClient) -> None:
    """ "Beach" and "beach" are the same tag."""
    sign_in(client)
    make_tag(client, "Beach")

    clash = client.post("/api/tags", json={"name": "beach"})

    assert clash.status_code == 409
    assert len(client.get("/api/tags").json()["items"]) == 1


def test_a_tag_made_on_the_screen_says_which_account_typed_it(client: TestClient) -> None:
    """A tag made on the screen records this viewer as its maker."""
    who = sign_in(client)
    tag_id = make_tag(client, "Beach")

    row = read(
        db_path(client),
        "SELECT created_by_kind, created_by_via, created_by_user_id FROM tags WHERE id = ?",
        (tag_id,),
    )[0]

    assert (row["created_by_kind"], row["created_by_via"], row["created_by_user_id"]) == (
        "user",
        None,
        who,
    )


def test_a_rename_onto_a_name_that_is_taken_is_refused(client: TestClient) -> None:
    """The same rule on the way through, where it is easier to forget."""
    sign_in(client)
    make_tag(client, "Beach")
    other = make_tag(client, "Sunset")

    clash = client.put(f"/api/tags/{other}", json={"name": "BEACH"})

    assert clash.status_code == 409
    assert {tag["name"] for tag in client.get("/api/tags").json()["items"]} == {"Beach", "Sunset"}


def test_a_name_is_trimmed_before_it_is_stored(client: TestClient) -> None:
    """A trailing space is invisible everywhere a tag is drawn, so it cannot be what separates two
    of them."""
    sign_in(client)
    make_tag(client, "Beach")

    clash = client.post("/api/tags", json={"name": "  beach  "})

    assert clash.status_code == 409


def test_a_name_of_nothing_but_spaces_is_refused(client: TestClient) -> None:
    sign_in(client)

    assert client.post("/api/tags", json={"name": "   "}).status_code == 422


# --- renaming and deleting ---------------------------------------------------------------------


def test_a_tag_can_be_renamed(client: TestClient) -> None:
    sign_in(client)
    tag_id = make_tag(client, "Beech")

    updated = client.put(f"/api/tags/{tag_id}", json={"name": "Beach"})

    assert updated.status_code == 200
    assert updated.json()["name"] == "Beach"


def test_a_tag_can_be_saved_under_its_own_name(client: TestClient) -> None:
    """A tag saved under its own name is not a conflict with itself."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")

    updated = client.put(f"/api/tags/{tag_id}", json={"name": "Beach", "description": "Sand."})

    assert updated.status_code == 200
    assert updated.json()["name"] == "Beach"


def test_a_tag_can_be_recased_without_colliding_with_itself(client: TestClient) -> None:
    """Same rule, on the edge where the collation decides: "beach" -> "Beach" is one tag renaming
    itself, not a second tag claiming a name."""
    sign_in(client)
    tag_id = make_tag(client, "beach")

    updated = client.put(f"/api/tags/{tag_id}", json={"name": "Beach"})

    assert updated.status_code == 200
    assert updated.json()["name"] == "Beach"


def test_more_tags_than_one_call_may_apply_is_refused(client: TestClient, library: Library) -> None:
    """The work is the product of the two lists, and one request is one write lock."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")

    refused = assign(client, [library.shared], [tag_id] * 51)

    assert refused.status_code == 422


def test_renaming_a_tag_that_is_not_there_reads_like_any_other_miss(client: TestClient) -> None:
    sign_in(client)

    assert client.put(f"/api/tags/{NEVER_EXISTED}", json={"name": "Beach"}).status_code == 404
    assert client.delete(f"/api/tags/{NEVER_EXISTED}").status_code == 404


def test_a_tag_deleted_by_somebody_else_mid_delete_reads_like_any_other_miss(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two people deleting one tag together: the second passes the visibility check, and by its own
    delete the row is gone. It answers the miss an unknown id does, not a success for a delete
    that removed nothing."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    real = TagService.assets_with

    async def gone_meanwhile(self: TagService, tag: str) -> list[str]:
        found = await real(self, tag)
        await self._db.execute("DELETE FROM tags WHERE id = ?", (tag,))
        return found

    monkeypatch.setattr(TagService, "assets_with", gone_meanwhile)

    assert client.delete(f"/api/tags/{tag_id}").status_code == 404


def test_deleting_a_tag_takes_its_assignments_and_leaves_the_files(
    client: TestClient, library: Library
) -> None:
    """Deleting a tag takes its assignments and leaves the files."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    assign(client, [library.shared, library.private], [tag_id])

    assert client.delete(f"/api/tags/{tag_id}").status_code == 204

    assert read(db_path(client), "SELECT * FROM asset_tags WHERE tag_id = ?", (tag_id,)) == []
    remaining = read(db_path(client), "SELECT id FROM assets")
    assert {str(row["id"]) for row in remaining} == {library.shared, library.private}
    assert library.path_of("shared").exists()
    assert library.path_of("private").exists()


def test_deleting_a_tag_forgets_every_grant_that_named_it(
    client: TestClient, library: Library
) -> None:
    """Deleting a tag forgets every grant naming it: `acl_grants.object_id` has no foreign key."""
    guest = sign_in(client, "guest")
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    grant_on_tag(client, tag_id, guest, "share")
    grant_on_tag(client, tag_id, guest, "restrict")
    assert len(grants_naming(client, tag_id)) == 2

    assert client.delete(f"/api/tags/{tag_id}").status_code == 204

    assert grants_naming(client, tag_id) == [], "grants outlived the tag they named"


def _stamp_of(client: TestClient, user_id: str) -> int:
    """How many times what this user may see has changed. Read straight from the row, because
    nothing puts it in a response: it reaches a screen only inside a picture's address."""
    rows = read(db_path(client), "SELECT cache_stamp FROM users WHERE id = ?", (user_id,))
    assert rows, "no such account"
    return int(str(rows[0]["cache_stamp"]))


def test_untagging_reaches_the_guest_the_tag_was_shared_with(
    client: TestClient, library: Library
) -> None:
    """Untagging raises the guest's stamp, revoking cached pictures of that file."""
    guest = sign_in(client, "guest")
    admin = sign_in(client)
    tag_id = make_tag(client, "Beach")
    assert assign(client, [library.shared], [tag_id]).status_code == 200
    grant_on_tag(client, tag_id, guest, "share")
    before_guest = _stamp_of(client, guest)
    before_admin = _stamp_of(client, admin)

    assert assign(client, [library.shared], [tag_id], add=False).status_code == 200

    assert _stamp_of(client, guest) > before_guest, (
        "the guest's picture addresses still work after the tag that reached them was taken off"
    )
    assert _stamp_of(client, admin) == before_admin, (
        "the admin's whole grid was thrown away for a change to somebody else's access"
    )


def test_tagging_something_that_already_carries_it_costs_nobody_anything(
    client: TestClient, library: Library
) -> None:
    """Nothing changed, so no address needs to move. Dragging a clip onto a chip it already has is
    an ordinary slip, and it must not empty the grid of everyone the tag reaches."""
    guest = sign_in(client, "guest")
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    assert assign(client, [library.shared], [tag_id]).status_code == 200
    grant_on_tag(client, tag_id, guest, "share")
    before = _stamp_of(client, guest)

    assert assign(client, [library.shared], [tag_id]).status_code == 200

    assert _stamp_of(client, guest) == before, "a repeated tag invalidated everybody's pictures"


# --- assigning: the promise that nothing on disk moves -------------------------------------------


def test_tagging_moves_no_file_and_changes_no_location(
    client: TestClient, library: Library
) -> None:
    """Tagging moves no file and changes no location: bytes, path and row are all unchanged."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    before_bytes = library.path_of("shared").read_bytes()
    before_locations = read(db_path(client), "SELECT * FROM asset_locations ORDER BY id")

    assert assign(client, [library.shared], [tag_id]).json() == {
        "changed": 1,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }

    assert library.path_of("shared").read_bytes() == before_bytes
    assert library.path_of("shared").exists()
    assert read(db_path(client), "SELECT * FROM asset_locations ORDER BY id") == before_locations
    assert [row["asset_id"] for row in read(db_path(client), "SELECT * FROM asset_tags")] == [
        library.shared
    ]


def test_one_call_tags_a_whole_selection(client: TestClient, library: Library) -> None:
    """Tagging two hundred selected clips is an ordinary thing to do, so it is one request."""
    sign_in(client)
    first, second = make_tag(client, "Beach"), make_tag(client, "Sunset")

    done = assign(client, [library.shared, library.private], [first, second])

    assert done.json() == {
        "changed": 4,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert len(read(db_path(client), "SELECT * FROM asset_tags")) == 4


def test_tagging_something_twice_changes_nothing_the_second_time(
    client: TestClient, library: Library
) -> None:
    """Dropping the same clip on the same chip again is a no-op, not an error."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    assign(client, [library.shared], [tag_id])

    assert assign(client, [library.shared], [tag_id]).json() == {
        "changed": 0,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert len(read(db_path(client), "SELECT * FROM asset_tags")) == 1


def test_a_tag_can_be_taken_off_again(client: TestClient, library: Library) -> None:
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    assign(client, [library.shared], [tag_id])

    assert assign(client, [library.shared], [tag_id], add=False).json() == {
        "changed": 1,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }

    assert read(db_path(client), "SELECT * FROM asset_tags") == []
    assert library.path_of("shared").exists()


def test_assigning_a_tag_that_does_not_exist_is_a_miss(
    client: TestClient, library: Library
) -> None:
    sign_in(client)

    assert assign(client, [library.shared], [NEVER_EXISTED]).status_code == 404


def test_the_chips_on_an_asset_come_back_in_name_order(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    sunset, beach = make_tag(client, "Sunset"), make_tag(client, "beach")
    assign(client, [library.shared], [sunset, beach])

    chips = client.get(f"/api/assets/{library.shared}/tags").json()

    assert [chip["name"] for chip in chips] == ["beach", "Sunset"]


# --- the list and the suggester ------------------------------------------------------------------


def test_the_tag_list_carries_a_count_and_orders_by_it(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    busy, quiet = make_tag(client, "busy"), make_tag(client, "quiet")
    assign(client, [library.shared, library.private], [busy])
    assign(client, [library.shared], [quiet])

    listed = client.get("/api/tags").json()["items"]

    assert [(tag["name"], tag["asset_count"]) for tag in listed] == [("busy", 2), ("quiet", 1)]


def test_the_suggester_matches_a_prefix_in_any_case(client: TestClient) -> None:
    sign_in(client)
    make_tag(client, "Beach")
    make_tag(client, "Sunset")

    assert [tag["name"] for tag in client.get("/api/tags?prefix=be").json()["items"]] == ["Beach"]
    assert [tag["name"] for tag in client.get("/api/tags?prefix=BE").json()["items"]] == ["Beach"]
    assert client.get("/api/tags?prefix=zz").json()["items"] == []


def test_the_wall_of_tags_matches_words_anywhere_in_a_name(client: TestClient) -> None:
    """The wall's box sends `anywhere`, as the People wall's does; a completion does not."""
    sign_in(client)
    make_tag(client, "Beach")
    make_tag(client, "Sunset")

    assert client.get("/api/tags?prefix=set").json()["items"] == []
    found = client.get("/api/tags?prefix=SET&anywhere=true").json()["items"]
    assert [tag["name"] for tag in found] == ["Sunset"]
    counted = client.get("/api/tags/facets?facet=created&prefix=set&anywhere=true").json()
    assert sum(one["count"] for one in counted["values"]) == 1


# --- names that could never be searched for --------------------------------------------------


def test_a_name_carrying_a_control_character_is_cleaned_rather_than_refused(
    client: TestClient,
) -> None:
    """A control character in a name is cleaned out rather than refused."""
    sign_in(client)

    made = client.post("/api/tags", json={"name": "be\x00ach"})

    assert made.status_code == 201
    assert made.json()["name"] == "beach"


def test_a_name_that_is_only_control_characters_is_refused_as_blank(client: TestClient) -> None:
    sign_in(client)

    refused = client.post("/api/tags", json={"name": "\x00\x01"})

    assert refused.status_code == 422
    assert "blank" in refused.text


def test_a_double_quote_is_refused_out_loud(client: TestClient) -> None:
    """Visible, typed on purpose, and unwritable as a filter token, so it is refused rather than
    quietly deleted. The message says which character and why."""
    sign_in(client)

    refused = client.post("/api/tags", json={"name": 'say "hi"'})

    assert refused.status_code == 422
    assert "double quote" in refused.text


def test_an_apostrophe_is_kept(client: TestClient) -> None:
    """The other half of the rule. Real names carry these, and the parser does not spend them."""
    sign_in(client)

    made = client.post("/api/tags", json={"name": "O'Brien"})

    assert made.status_code == 201
    assert made.json()["name"] == "O'Brien"


def test_a_tag_has_no_colour_to_write_or_to_read(client: TestClient) -> None:
    """A tag has no colour: one sent is ignored, and none comes back."""
    sign_in(client)

    made = client.post("/api/tags", json={"name": "beach", "color": "tag-3"})

    assert made.status_code == 201
    assert "color" not in made.json()
    renamed = client.put(f"/api/tags/{made.json()['id']}", json={"name": "shore", "color": "tag-1"})
    assert renamed.status_code == 200
    assert "color" not in renamed.json()
    assert all("color" not in one for one in client.get("/api/tags").json()["items"])


# --- the vault -------------------------------------------------------------------------------


def _set_vault(client: TestClient, tag_id: str, vault: bool) -> int:
    return int(client.put(f"/api/tags/{tag_id}/vault", json={"vault": vault}).status_code)


def _tag_ids(client: TestClient) -> list[str]:
    return [tag["id"] for tag in client.get("/api/tags").json()["items"]]


def test_a_vaulted_tag_is_absent_from_the_list(client: TestClient, library: Library) -> None:
    """A vaulted tag is absent from the list, admins included."""
    user_id = sign_in(client)
    give_pin(db_path(client), user_id)
    tag_id = make_tag(client, "private")
    assign(client, [library.shared], [tag_id])

    assert _set_vault(client, tag_id, True) == 204

    assert _tag_ids(client) == []


def test_a_vaulted_tag_conceals_every_file_carrying_it(
    client: TestClient, library: Library
) -> None:
    """Which is the point, and the half a list test cannot show.

    Concealing the label and leaving the files on the grid would hide the word and nothing else.
    """
    user_id = sign_in(client)
    give_pin(db_path(client), user_id)
    tag_id = make_tag(client, "private")
    assign(client, [library.shared], [tag_id])

    _set_vault(client, tag_id, True)

    shown = [item["id"] for item in client.get("/api/assets").json()["items"]]
    assert library.shared not in shown
    assert library.private in shown, "the untagged file went too, so this hid the whole library"


def test_a_vaulted_tag_cannot_be_taken_back_out_while_the_vault_is_locked(
    client: TestClient, library: Library
) -> None:
    """A vaulted tag cannot be taken out while the vault is locked: a 404, as an unknown id."""
    user_id = sign_in(client)
    give_pin(db_path(client), user_id)
    tag_id = make_tag(client, "private")
    _set_vault(client, tag_id, True)

    assert _set_vault(client, tag_id, False) == 404
    assert _tag_ids(client) == [], "still concealed after the refused write"


def test_a_vaulted_tag_answers_every_write_the_way_an_unknown_id_does(
    client: TestClient, library: Library
) -> None:
    """One id, one answer, in both directions."""
    user_id = sign_in(client)
    give_pin(db_path(client), user_id)
    tag_id = make_tag(client, "private")
    _set_vault(client, tag_id, True)

    for concealed in (tag_id, NEVER_EXISTED):
        assert _set_vault(client, concealed, True) == 404, concealed
        assert _set_vault(client, concealed, False) == 404, concealed


def test_an_unlocked_vault_brings_the_tag_and_its_files_back(
    client: TestClient, library: Library
) -> None:
    """The half that says the rows were withheld rather than never there."""
    user_id = sign_in(client)
    give_pin(db_path(client), user_id)
    tag_id = make_tag(client, "private")
    assign(client, [library.shared], [tag_id])
    _set_vault(client, tag_id, True)

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200

    assert _tag_ids(client) == [tag_id]
    shown = [item["id"] for item in client.get("/api/assets").json()["items"]]
    assert library.shared in shown

    # And now it can come back out, which is the only way out there is.
    assert _set_vault(client, tag_id, False) == 204


def test_hiding_a_tag_needs_a_pin_to_exist_first(client: TestClient, library: Library) -> None:
    """Without one there is nothing to open the vault with, so this is not hiding: it is losing
    the tag and everything filed under it."""
    sign_in(client)
    tag_id = make_tag(client, "private")

    assert _set_vault(client, tag_id, True) == 409
    assert _tag_ids(client) == [tag_id]


@pytest.fixture
async def tag_service(temp_db: Database, access: Repository) -> TagService:
    """The service on a database of its own, with no application around it."""
    return TagService(temp_db, access)


async def test_hiding_a_tag_that_is_not_there_answers_nothing(
    tag_service: TagService, actors: Actors
) -> None:
    """Hiding a tag that is not there answers nothing rather than writing a row."""
    assert await tag_service.set_vault(actors.admin, NEVER_EXISTED, vault=True) is None


# --- a tag is found by every name it goes by ---------------------------------------------------


def test_a_tags_other_name_finds_the_tag_and_not_only_its_files(
    client: TestClient, library: Library
) -> None:
    """A tag's other name finds the tag itself through the shared suggester, not only its files."""
    sign_in(client)
    # The other name shares no letters with the name on the row, deliberately: a fixture whose two
    # spellings overlap cannot tell an alias match from a name match.
    tag = make_tag(client, "Beach")
    assign(client, [library.shared], [tag])
    saved = client.put(f"/api/tags/{tag}", json={"name": "Beach", "aliases": ["seaside"]})
    assert saved.status_code == 200, saved.text

    found = client.get("/api/search/suggest", params={"field": "tags", "prefix": "seasi"})

    assert found.status_code == 200, found.text
    offered = found.json()["matches"]
    assert [one["value"] for one in offered] == ["Beach"]
    # And it says WHY it is there. A row that comes back for a word it does not contain reads as
    # the box answering a different question.
    assert offered[0]["detail"] == "seaside"


def test_the_tags_own_name_still_wins_and_says_nothing_extra(
    client: TestClient, library: Library
) -> None:
    """Matching the name is the ordinary case, and it carries no "matched as" line: the row
    already says the word that was typed."""
    sign_in(client)
    tag = make_tag(client, "Beach")
    assign(client, [library.shared], [tag])
    client.put(f"/api/tags/{tag}", json={"name": "Beach", "aliases": ["seaside"]})

    offered = client.get("/api/search/suggest", params={"field": "tags", "prefix": "bea"}).json()

    assert [one["value"] for one in offered["matches"]] == ["Beach"]
    assert offered["matches"][0]["detail"] is None


# --- one hidden file does not stop the whole selection --------------------------------------------


def test_a_selection_with_one_file_in_the_vault_tags_the_rest_and_says_what_it_left(
    client: TestClient, library: Library
) -> None:
    """Tagging three files with one of them hidden tags the other two and says why one was not.

    A partial success saying nothing would be worse than a refusal; a reported one is the answer.
    What is asserted is all three halves: the reachable file IS tagged, the hidden one is NOT, and
    the reply carries the count and the one reason a person can actually act on.
    """
    admin = sign_in(client)
    tag_id = make_tag(client, "Keepers")
    write(db_path(client), [hidden_row("asset", library.private, admin)])

    answer = assign(client, [library.shared, library.private], [tag_id])

    assert answer.status_code == 200
    assert answer.json() == {
        "changed": 1,
        "skipped": 1,
        "reason": "It is in your vault. Unlock the vault to include it.",
        "reason_many": "They are in your vault. Unlock the vault to include them.",
        "vault_locked": True,
    }
    tagged = {
        str(row["asset_id"]) for row in read(db_path(client), "SELECT asset_id FROM asset_tags")
    }
    assert tagged == {library.shared}, (
        "the hidden file must not be tagged, which was never at issue"
    )


def test_the_same_selection_tags_everything_once_the_vault_is_open(
    client: TestClient, library: Library
) -> None:
    """The other side of the same coin, and what makes the Unlock button worth offering: the thing
    the message tells somebody to do actually changes the answer."""
    admin = sign_in(client)
    tag_id = make_tag(client, "Keepers")
    write(db_path(client), [hidden_row("asset", library.private, admin)])
    give_pin(db_path(client), admin)
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200

    answer = assign(client, [library.shared, library.private], [tag_id])

    assert answer.json()["skipped"] == 0
    assert answer.json()["vault_locked"] is False
    tagged = {
        str(row["asset_id"]) for row in read(db_path(client), "SELECT asset_id FROM asset_tags")
    }
    assert tagged == {library.shared, library.private}
