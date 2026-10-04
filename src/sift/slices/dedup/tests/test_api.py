# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four endpoints, over HTTP, against a real application.

Run this way rather than against the service object because the thing being asserted here lives in
the router and its dependencies, not in the service: who is refused. A service-level test would
exercise the half that was never in question and would pass whether or not the routes were guarded
at all.

The refusals are the point of the file. Both surfaces describe the library as a whole (what is in
it, what looks like what, and where on disk every copy sits), so both are admin-only, refused on
the server. The client hides the Maintenance section from a guest as a courtesy; these four checks
are what actually stop them.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.access.catalog import MADE_BY_A_PERSON, attribute_assets_on, create_person_on
from sift.kernel.config import get_settings
from sift.kernel.content.duplicates import DuplicateReads
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.settings_registry import get_registered
from sift.main import create_app
from sift.slices.dedup import MAX_DURATION_GAP_KEY
from sift.slices.dedup.service import DedupService, NotAllowed
from sift.testing.auth import establish_session, hide_for_caller

#: The router MODULE, reached through the import system rather than by attribute.
#:
#: `sift.slices.dedup` re-exports the APIRouter under the name `router`, which shadows the submodule
#: of the same name, so a `from` import hands back the APIRouter object and patching a name on it
#: fails with an AttributeError that reads as the function not existing.
dedup_router = importlib.import_module("sift.slices.dedup.router")

pytestmark = pytest.mark.integration

_EPOCH = 1_700_000_000
PASSWORD = "A-Dedup-Test-Passw0rd!"

#: Well-formed and never minted, so a refusal for it cannot be mistaken for a lucky miss.
NEVER_EXISTED = "01HX0000000000000000000099"

_PHASH = "0f0f0f0f0f0f0f0f"
_NEAR = "0f0f0f0f0f0f0f0e"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as running:
        yield running


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def write(path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Seed through a connection of the test's own.

    The client drives the application on its own event loop, and a write issued from this loop
    would meet a lock held on that one, which fails for a reason that has nothing to do with what
    is being tested.
    """

    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            for sql, params in statements:
                await database.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


def sign_in(client: TestClient, role: str) -> str:
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"dedup-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def seed_near_duplicate(client: TestClient) -> tuple[str, str]:
    """Two assets that look alike, and the root and locations to make them real.

    Each asset is given a location, because a real one always has one: an asset with no copy
    anywhere is not shown in the library at all, so a fixture without one is not a smaller version
    of a real asset: it is one the access layer would never resolve.
    """
    root_id, folder_id = new_id(), new_id()
    first, second = new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root_id, "Videos", "/library/videos", _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, NULL, '', 'Videos')",
                (folder_id, root_id),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, phash, size_bytes, added_at) "
                "VALUES (?, ?, 'image', ?, 1000, ?)",
                (first, f"digest-{first}", _PHASH, _EPOCH),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, phash, size_bytes, added_at) "
                "VALUES (?, ?, 'image', ?, 1000, ?)",
                (second, f"digest-{second}", _NEAR, _EPOCH),
            ),
            *[
                (
                    "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                    "filename, size_bytes, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1000, ?, ?)",
                    (new_id(), asset_id, root_id, folder_id, name, name, _EPOCH, _EPOCH),
                )
                for asset_id, name in ((first, "first.jpg"), (second, "second.jpg"))
            ],
        ],
    )
    return first, second


def _another_near_duplicate(client: TestClient) -> tuple[str, str]:
    """A second pair, in a root of its own: a root's path is unique, so the helper above cannot
    simply be called twice."""
    root_id, folder_id = new_id(), new_id()
    first, second = new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root_id, "More", "/library/more", _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, NULL, '', 'More')",
                (folder_id, root_id),
            ),
            # Far from the first pair's pictures, so the scan files exactly two pairs.
            (
                "INSERT INTO assets (id, identity, media_type, phash, size_bytes, added_at) "
                "VALUES (?, ?, 'image', ?, 1000, ?)",
                (first, f"digest-{first}", "f0f0f0f0f0f0f0f0", _EPOCH),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, phash, size_bytes, added_at) "
                "VALUES (?, ?, 'image', ?, 1000, ?)",
                (second, f"digest-{second}", "f0f0f0f0f0f0f0f1", _EPOCH),
            ),
            *[
                (
                    "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                    "filename, size_bytes, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1000, ?, ?)",
                    (new_id(), asset_id, root_id, folder_id, name, name, _EPOCH, _EPOCH),
                )
                for asset_id, name in ((first, "third.jpg"), (second, "fourth.jpg"))
            ],
        ],
    )
    return first, second


def measure_assets(client: TestClient, sizes: dict[str, tuple[int, int, int]]) -> None:
    """Give the seeded assets a size and a shape, so a keeper rule has something to compare.

    The seeds above deliberately make every file 1000 bytes with no dimensions at all, which is what
    a rule cannot separate, so a test about the rule DECIDING has to say what it is deciding on.
    """
    write(
        db_path(client),
        [
            (
                "UPDATE assets SET size_bytes = ?, width = ?, height = ? WHERE id = ?",
                (size, width, height, asset_id),
            )
            for asset_id, (size, width, height) in sizes.items()
        ],
    )


def seed_exact_duplicate(client: TestClient) -> str:
    """One asset sitting in two places: the reclaim case."""
    root_id, folder_id, asset_id = new_id(), new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root_id, "Videos", "/library/videos", _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, NULL, '', 'Videos')",
                (folder_id, root_id),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, phash, size_bytes, added_at) "
                "VALUES (?, ?, 'video', ?, 5000, ?)",
                (asset_id, f"digest-{asset_id}", _PHASH, _EPOCH),
            ),
            *[
                (
                    "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                    "filename, size_bytes, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 5000, ?, ?)",
                    (new_id(), asset_id, root_id, folder_id, name, name, _EPOCH, _EPOCH),
                )
                for name in ("one.mp4", "two.mp4")
            ],
        ],
    )
    return asset_id


# --- the refusals -----------------------------------------------------------------------------


def test_a_guest_cannot_see_the_review_queue(client: TestClient) -> None:
    """Refused on the server, not merely absent from a menu.

    The queue describes what is in the library and what resembles what, which is as revealing as
    the library itself, so a guest is turned away rather than shown an empty list.
    """
    sign_in(client, "guest")

    assert client.get("/api/dedup/groups").status_code == 403


def test_a_guest_cannot_see_what_could_be_reclaimed(client: TestClient) -> None:
    """This one names where on disk every copy of every file sits, which is the library's shape."""
    sign_in(client, "guest")

    assert client.get("/api/reclaim").status_code == 403


def test_a_guest_cannot_settle_a_group(client: TestClient) -> None:
    """Both presses, because they are two routes and a check on one proves nothing about the other.

    Confirming deletes files and dismissing writes an answer that lasts for ever. Neither is a
    guest's to make about somebody else's library.
    """
    sign_in(client, "guest")
    body = {"groups": [{"ids": [NEVER_EXISTED], "keep": NEVER_EXISTED}]}

    assert client.post("/api/dedup/groups/confirm", json=body).status_code == 403
    assert client.post("/api/dedup/groups/dismiss", json=body).status_code == 403


def test_a_guest_cannot_release_a_copy(client: TestClient) -> None:
    """The only route here that can remove anything at all."""
    sign_in(client, "guest")

    response = client.post(
        f"/api/reclaim/{NEVER_EXISTED}/release", json={"location_id": NEVER_EXISTED}
    )

    assert response.status_code == 403


def test_nobody_signed_in_is_refused_from_both_surfaces(client: TestClient) -> None:
    """Not a 404 and not an empty list: a refusal."""
    assert client.get("/api/dedup/groups").status_code in (401, 403)
    assert client.get("/api/reclaim").status_code in (401, 403)


# --- the queue --------------------------------------------------------------------------------


def test_an_admin_sees_a_near_duplicate_as_one_group(client: TestClient) -> None:
    """Two files that look alike are ONE question, with everything needed to answer it on the group.

    The facts come WITH the group rather than being fetched per file. Asked for each file on its
    own, a page of a hundred pairs would be two hundred requests, each a recursive permission
    walk, so the number of requests is part of what is under test here.
    """
    first, second = seed_near_duplicate(client)
    sign_in(client, "admin")

    scan(client)
    response = client.get("/api/dedup/groups")

    assert response.status_code == 200
    body = response.json()
    (group,) = body["groups"]
    assert {one["id"] for one in group["files"]} == {first, second}
    assert group["method"] == "phash"
    assert group["distance"] == 1
    assert group["too_big"] is False
    assert body["total"] == 1
    # The path each file sits at, which on this screen is regularly the only thing that tells two
    # of them apart: the pictures are the same picture, that is why they are here. The full path,
    # for an admin (`kernel.where`).
    assert {one["where"] for one in group["files"]} == {
        os.sep.join(["/library/videos", "first.jpg"]),
        os.sep.join(["/library/videos", "second.jpg"]),
    }


def test_the_rule_marks_a_keeper_and_the_stored_setting_says_which(client: TestClient) -> None:
    """The inversion, end to end: the machine proposes on every group and the person confirms.

    A rule that only ran as one press over the whole queue would leave every group to be decided
    from scratch, so the keeper is marked on each group as it is read.
    """
    first, second = seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)
    # `first` is the higher resolution and `second` is the bigger file, so the two rules disagree,
    # which is the only arrangement that can tell the setting is being read at all.
    measure_assets(client, {first: (1000, 1920, 1080), second: (9000, 1280, 720)})

    (by_pixels,) = client.get("/api/dedup/groups").json()["groups"]
    assert by_pixels["keeper"] == first, "the default rule keeps the higher resolution"

    set_setting(client, "dedup.keep", "larger")
    body = client.get("/api/dedup/groups").json()

    assert body["rule"] == "larger"
    assert body["groups"][0]["keeper"] == second, "and the stored rule is what decides"
    # Nothing was compared again to answer differently: the rows are the same rows.
    assert body["total"] == 1


def test_the_rules_come_with_the_queue_so_the_screen_holds_no_second_list(
    client: TestClient,
) -> None:
    """A stored value is a word like `higher_res` and the reader is shown "Higher resolution".

    A screen holding that mapping itself is a list that drifts the first time a rule is added and
    nobody edits both, so the choices and their names travel with the queue: from the settings
    registry, which is where they are declared.
    """
    seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    body = client.get("/api/dedup/groups").json()

    assert [one["key"] for one in body["rules"]] == [
        "higher_res",
        "larger",
        "smaller",
        "newer",
        "older",
    ]
    assert body["rules"][0]["label"] == "Higher resolution"


def test_the_groups_a_rule_could_not_settle_are_counted_and_can_be_asked_for_alone(
    client: TestClient,
) -> None:
    """The honest number for a screen whose promise is that it empties, and the filter behind it.

    Every group waits for a person (nothing is ever deleted without a press), but the ones the
    rule could not separate are the only ones that cost a real decision rather than a skim. So the
    count is said, and `needs_you` narrows the page to exactly those.
    """
    first, second = seed_near_duplicate(client)
    third, fourth = _another_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)
    # One group the rule can settle, and one it cannot: identical in every measure it has.
    measure_assets(
        client,
        {
            first: (1000, 1920, 1080),
            second: (2000, 1280, 720),
            third: (5000, 1920, 1080),
            fourth: (5000, 1920, 1080),
        },
    )

    body = client.get("/api/dedup/groups").json()
    assert body["total"] == 2
    assert body["needs_you"] == 1

    narrowed = client.get("/api/dedup/groups", params={"needs_you": True}).json()
    assert narrowed["total"] == 1
    (group,) = narrowed["groups"]
    assert group["keeper"] is None
    assert {one["id"] for one in group["files"]} == {third, fourth}


def test_a_group_holding_a_file_shown_as_a_locked_placeholder_carries_no_keeper(
    client: TestClient,
) -> None:
    """The boundary between a whole-library answer and a fact about one session.

    The rule works from measurements taken across the library, which is what makes one answer safe
    to keep and hand to every admin. Whether a particular user may SEE a particular file is a
    fact about their vault. A viewer who asked for locked placeholders is shown the group with the
    file still in it (so the size of the group is honest), and marking a keeper there would be
    proposing to delete files on the strength of a comparison against something they cannot look at.
    """
    first, second = seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)
    measure_assets(client, {first: (1000, 1920, 1080), second: (9000, 1280, 720)})
    set_setting(client, "vault.concealment", "placeholder")
    hide_for_caller(client, "asset", first)

    (group,) = client.get("/api/dedup/groups").json()["groups"]

    assert len(group["files"]) == 2, "the group is its real size"
    assert group["keeper"] is None
    hidden = next(one for one in group["files"] if one["id"] == first)
    assert hidden["concealed"] is True
    # And nothing about it is printed: not its name, not where it sits, not its size.
    assert hidden["where"] is None
    assert hidden["original_filename"] is None
    assert hidden["size_bytes"] is None


def test_a_group_holding_a_placeholder_cannot_be_settled_either(client: TestClient) -> None:
    """Withholding the keeper is not enough on its own: a request can name one anyway.

    So the write applies the same rule the read does. Without it, the one file somebody may not look
    at is deletable by anybody who knows its id.
    """
    first, second = seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)
    set_setting(client, "vault.concealment", "placeholder")
    hide_for_caller(client, "asset", first)

    response = client.post(
        "/api/dedup/groups/confirm",
        json={"groups": [{"ids": [first, second], "keep": second}]},
    )

    assert response.json() == {"settled": 0, "removed": 0, "refused": 0, "unknown": 1}
    assert len(client.get("/api/dedup/groups").json()["groups"]) == 1


def test_a_vaulted_near_duplicate_is_absent_from_the_queue_while_the_vault_is_shut(
    client: TestClient,
) -> None:
    """The scan files the pair regardless: it must, or it would miss duplicates in the vault. But
    the queue is a screen, and a screen honours the vault: with one side concealed and the vault not
    opened this session, the pair is not shown, the same absence the grid gives it."""
    first, _second = seed_near_duplicate(client)
    sign_in(client, "admin")
    hide_for_caller(client, "asset", first)
    scan(client)

    body = client.get("/api/dedup/groups").json()
    assert body["groups"] == []
    # Counted rather than quietly missing, so a short page says a number instead of looking like
    # the end of the list.
    assert body["concealed"] == 1


def test_a_vaulted_group_is_stepped_over_and_the_rest_of_the_page_still_arrives(
    client: TestClient,
) -> None:
    """A concealed group does not take the page down with it, and it is counted rather than hidden.

    A queue with no pager would have to fill its page by WALKING past concealed rows: cut once, the
    hundred nearest pairs could all be in a vault and it would show nothing while counting
    thousands, for ever. A page that is a position in a pager cannot get stuck like that, so this
    is the simpler rule, and `concealed` is what keeps it honest.
    """
    first_pair = seed_near_duplicate(client)
    second_pair = _another_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)
    hide_for_caller(client, "asset", first_pair[0])

    body = client.get("/api/dedup/groups").json()

    (group,) = body["groups"]
    assert {one["id"] for one in group["files"]} == set(second_pair)
    assert body["concealed"] == 1
    assert body["total"] == 2, "the concealed group is still counted in what the dials make"


def test_the_board_draws_nothing_of_a_group_the_shut_vault_keeps_off_the_queue(
    client: TestClient,
) -> None:
    """The card on the Organize board follows the page it leads to.

    The page leaves out a group with a file the vault holds back (the test above). A card that drew
    that group's OTHER file anyway, pointed at `#group-<method>:<smallest file>`, would name the id
    of the very file being held back on the board, and lead to a group the page does not show.
    The second pair is the positive control: a card that drew nothing at all would pass the first
    half on its own.
    """
    held_back, beside_it = seed_near_duplicate(client)
    shown_pair = _another_near_duplicate(client)
    # Stills built for all four: the card sends only files whose still exists, and a card that
    # drew nothing at all must fail the positive control for the vault's reason, not that one.
    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, params, size_bytes, "
                "created_at) VALUES (?, ?, 'thumb', ?, '{}', 10, ?)",
                (new_id(), asset_id, f"{asset_id}/thumb.jpg", _EPOCH),
            )
            for asset_id in (held_back, beside_it, *shown_pair)
        ],
    )
    sign_in(client, "admin")
    scan(client)
    hide_for_caller(client, "asset", held_back)

    board = client.get("/api/workbench")

    assert board.status_code == 200, board.text
    assert held_back not in board.text, "the board names a file the shut vault holds back"
    assert beside_it not in board.text, "the board draws a group the queue's page leaves out"
    (card,) = [one for one in board.json()["queues"] if one["name"] == "duplicates"]
    assert {one["id"] for one in card["preview"]} == set(shown_pair)
    assert {one["href"] for one in card["preview"]} == {
        f"/organize/duplicates#group-phash:{min(shown_pair)}"
    }


def _more_near_duplicates(client: TestClient, count: int) -> list[tuple[str, str]]:
    """Further pairs, each in a root of its own and far from every other pair's pictures, so the
    scan files exactly one pair per call."""
    pairs: list[tuple[str, str]] = []
    for index in range(count):
        root_id, folder_id = new_id(), new_id()
        first, second = new_id(), new_id()
        # Alternating byte patterns: 32 bits from the two pairs above and 64 from each other.
        base = ("ff00" if index % 2 == 0 else "00ff") * 4
        write(
            db_path(client),
            [
                (
                    "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                    (root_id, f"Extra {index}", f"/library/extra-{index}", _EPOCH),
                ),
                (
                    "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                    "VALUES (?, ?, NULL, '', ?)",
                    (folder_id, root_id, f"Extra {index}"),
                ),
                (
                    "INSERT INTO assets (id, identity, media_type, phash, size_bytes, added_at) "
                    "VALUES (?, ?, 'image', ?, 1000, ?)",
                    (first, f"digest-{first}", base, _EPOCH),
                ),
                (
                    "INSERT INTO assets (id, identity, media_type, phash, size_bytes, added_at) "
                    "VALUES (?, ?, 'image', ?, 1000, ?)",
                    (
                        second,
                        f"digest-{second}",
                        base[:-1] + ("1" if base.endswith("0") else "e"),
                        _EPOCH,
                    ),
                ),
                *[
                    (
                        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                        "filename, size_bytes, first_seen_at, last_seen_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 1000, ?, ?)",
                        (new_id(), asset_id, root_id, folder_id, name, name, _EPOCH, _EPOCH),
                    )
                    for asset_id, name in ((first, "fifth.jpg"), (second, "sixth.jpg"))
                ],
            ],
        )
        pairs.append((first, second))
    return pairs


def test_one_group_can_be_opened_by_its_method_and_smallest_file(client: TestClient) -> None:
    """The name a chain's own screen is reached by, answered with the same view the list gives.

    A group has no id (it is computed from the pair table at the dials in force), so its name
    is its method and its smallest file, and a name that answers to nothing at these dials is a
    404 rather than an empty group.
    """
    seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    listed = client.get("/api/dedup/groups").json()["groups"]
    assert listed, "the seeded pair is a group"
    one = listed[0]

    answer = client.get(f"/api/dedup/groups/{one['method']}/{one['files'][0]['id']}")

    assert answer.status_code == 200, answer.text
    assert [f["id"] for f in answer.json()["files"]] == [f["id"] for f in one["files"]]
    assert client.get(f"/api/dedup/groups/{one['method']}/{NEVER_EXISTED}").status_code == 404


def test_a_group_too_long_a_chain_to_judge_is_refused_by_the_bulk_press_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal from the service becomes the route's own, with its status and its sentence kept.

    A group past the cap is a CHAIN of pairs whose ends look nothing alike, so "keep this one"
    would be deleting hundreds of files on the strength of a transitive hop. The single press is
    refused for it; so is the bulk one, which is the half that would otherwise be a way around.

    The refusal is RAISED rather than counted, unlike a set that is not one of the server's own
    groups. That is deliberate: not-a-group is innocent (a dial moved while the screen was open),
    and this is a request the server will never honour, so the caller is told rather than handed a
    tally with a silent nought in it.
    """
    first, _second = seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)
    (group,) = client.get("/api/dedup/groups").json()["groups"]
    ids = [one["id"] for one in group["files"]]

    def too_long(*_args: object, **_kwargs: object) -> None:
        raise NotAllowed("That group is too long a chain to settle in one press.")

    monkeypatch.setattr(DedupService, "settle_group", too_long)

    response = client.post(
        "/api/dedup/groups/confirm",
        json={"groups": [{"ids": ids, "keep": first}]},
    )

    # 403 is `NotAllowed`'s own status, kept by `_refusal` rather than flattened to a 400.
    assert response.status_code == 403, response.text
    assert "too long a chain" in response.json()["detail"]


def test_a_registry_that_has_lost_the_dial_still_draws_the_queue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The queue is perfectly readable without its dial, and a screen that will not draw at all
    because a preference is missing is the worse of the two.

    The words on the dial are the settings screen's own and are read from the registry rather than
    listed here, because a second copy of them is one that drifts. So the case worth holding is the
    registry not having them: an older release, a setting renamed, a component that failed to
    register. The list comes back empty and the groups still arrive.
    """
    seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    monkeypatch.setattr(dedup_router, "get_registered", lambda _key: None)
    body = client.get("/api/dedup/groups").json()

    assert body["levels"] == []
    assert body["max_duration_gap_word"] is None
    assert len(body["groups"]) == 1, "the queue stopped drawing because a preference was missing"


def test_the_queue_names_the_length_dials_zero_in_the_settings_words(client: TestClient) -> None:
    """The dial on the queue says the same word for "lengths are not compared" as the settings
    pane, because both read the one the setting declares."""
    seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)
    declared = get_registered(MAX_DURATION_GAP_KEY)
    assert declared is not None and declared.automatic_label

    body = client.get("/api/dedup/groups").json()

    assert body["max_duration_gap_word"] == declared.automatic_label


def test_opening_a_group_with_a_vaulted_file_in_it_is_a_404_not_a_short_group(
    client: TestClient,
) -> None:
    """A chain with a file this user may not see is not a set anybody can judge.

    The list steps over such a group; this is the same rule on the route that opens ONE, reached by
    a name somebody could have kept from before the file was vaulted. Answering with the group
    minus the hidden file would be worse than useless: "keep this one" over a set that is missing a
    member is a press that deletes the wrong things, and the shortened list would itself say that
    something had been taken out.

    The 404 is the same answer a name that matches nothing gets, deliberately: a 403 here would
    confirm the group exists and that a file in it is being kept back.
    """
    first, _second = seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)
    listed = client.get("/api/dedup/groups").json()["groups"]
    assert listed, "the seeded pair is a group"
    one = listed[0]

    # Vaulted AFTER the name was read, which is exactly the gap a kept address falls into.
    hide_for_caller(client, "asset", first)

    answer = client.get(f"/api/dedup/groups/{one['method']}/{one['files'][0]['id']}")

    assert answer.status_code == 404
    assert "isn't yours to see" in answer.json()["detail"]


def test_paging_the_groups_neither_skips_one_nor_shows_one_twice(client: TestClient) -> None:
    """A queue with no pager would read the first hundred and stop, so a library with thousands of
    duplicates would be offered a hundred of them for ever.

    Groups are ordered by their closest pair and then by their smallest file id, which is a TOTAL
    order: without the second half, two groups equally alike could swap places between two reads and
    a page turn would skip one and repeat another.
    """
    seed_near_duplicate(client)
    _another_near_duplicate(client)
    _more_near_duplicates(client, 2)
    sign_in(client, "admin")
    scan(client)

    whole = client.get("/api/dedup/groups").json()
    assert whole["total"] == 4

    seen: list[str] = []
    for offset in (0, 2):
        page = client.get("/api/dedup/groups", params={"limit": 2, "offset": offset}).json()
        assert page["offset"] == offset
        assert page["total"] == 4
        assert len(page["groups"]) == 2
        seen.extend(one["files"][0]["id"] for one in page["groups"])

    assert len(set(seen)) == 4, "two pages of two covered every group exactly once"
    assert seen == [one["files"][0]["id"] for one in whole["groups"]]


def test_dismissing_a_group_takes_it_out_of_the_queue_for_good(client: TestClient) -> None:
    seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    group = client.get("/api/dedup/groups").json()["groups"][0]
    settled = client.post(
        "/api/dedup/groups/dismiss",
        json={"groups": [{"ids": [one["id"] for one in group["files"]]}]},
    )

    assert settled.status_code == 200
    assert settled.json() == {"settled": 1, "removed": 0, "refused": 0, "unknown": 0}
    assert client.get("/api/dedup/groups").json()["groups"] == []

    # And the next scan leaves it alone.
    scan(client)
    assert client.get("/api/dedup/groups").json()["groups"] == []


def test_a_set_of_files_that_is_not_one_of_the_servers_own_groups_is_never_acted_on(
    client: TestClient,
) -> None:
    """The whole of the safety in the two presses, and it has to be tested from outside.

    A request names a SET OF FILES, and nothing about a set of files makes it safe to delete all but
    one of them. So the server clusters again and acts only on sets that are its own groups. Without
    this, "confirm these, keeping X" is a general-purpose delete of everything else named.

    Refused quietly rather than as an error, because the ordinary cause is innocent: a dial moved
    while the screen was open, or another window settled the group first. The count is what says so.
    """
    first, second = seed_near_duplicate(client)
    elsewhere, _its_twin = _another_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    # A real group with a third file smuggled into it. The smuggled one is a real asset in a real
    # group OF ITS OWN, which is the sharper version: nothing about it is malformed, and the only
    # thing wrong is that these three are not a group.
    response = client.post(
        "/api/dedup/groups/confirm",
        json={"groups": [{"ids": [first, second, elsewhere], "keep": first}]},
    )

    assert response.status_code == 200
    assert response.json() == {"settled": 0, "removed": 0, "refused": 0, "unknown": 1}
    # And both real groups are untouched.
    assert len(client.get("/api/dedup/groups").json()["groups"]) == 2


def test_a_keeper_that_is_not_in_the_group_settles_nothing(client: TestClient) -> None:
    """The other half of the same rule: the group is real and the file to keep is not in it.

    Acting on it would delete every file in the group and keep nothing, which is the worst possible
    reading of a press whose whole promise is that one copy survives.
    """
    first, second = seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    response = client.post(
        "/api/dedup/groups/confirm",
        json={"groups": [{"ids": [first, second], "keep": NEVER_EXISTED}]},
    )

    assert response.json()["settled"] == 0
    assert response.json()["unknown"] == 1
    assert len(client.get("/api/dedup/groups").json()["groups"]) == 1


def test_confirming_a_real_group_settles_it_and_says_what_went(client: TestClient) -> None:
    """THE POSITIVE CONTROL for the two tests above.

    Both of those assert that a request is REFUSED, and a route that refused everything would
    satisfy them as readily as one that refuses the right things. This is the same press over a
    group the server really did cluster, with a keeper really in it, and it has to settle.
    """
    first, second = seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    (group,) = client.get("/api/dedup/groups").json()["groups"]
    ids = [one["id"] for one in group["files"]]
    response = client.post(
        "/api/dedup/groups/confirm",
        json={"groups": [{"ids": ids, "keep": first}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["settled"], body["unknown"]) == (1, 0)
    # The group WAS acted on, which is all the refusals above cannot show. The copy is reported as
    # refused rather than removed here because this library is not a folder Sift may write in,
    # and a refusal is COUNTED rather than fatal, which is the other half worth pinning: one file
    # that cannot be removed must not abandon the rest of the request.
    assert (body["removed"], body["refused"]) == (0, 1)
    assert second != first


# --- reclaim ----------------------------------------------------------------------------------


def test_an_admin_sees_what_a_second_copy_is_costing(client: TestClient) -> None:
    """The number is the entire point of this screen."""
    asset_id = seed_exact_duplicate(client)
    sign_in(client, "admin")

    response = client.get("/api/reclaim")

    assert response.status_code == 200
    body = response.json()
    (entry,) = body["assets"]
    assert entry["asset_id"] == asset_id
    assert len(entry["copies"]) == 2
    assert entry["reclaimable_bytes"] == 5000
    assert body["total_reclaimable_bytes"] == 5000


def test_a_reclaim_tile_carries_the_token_its_still_is_kept_by(client: TestClient) -> None:
    """A bare `/thumb` is answered the careful way: one conditional request per tile on every
    visit. The token is what lets the browser keep the
    picture, and it is the access layer's own, so it moves when the picture or the user's view
    of it does. A file with no recorded picture has none, and its address stays bare."""
    asset_id = seed_exact_duplicate(client)
    sign_in(client, "admin")
    assert client.get("/api/reclaim").json()["assets"][0]["art"] is None

    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, params, size_bytes, "
                "created_at, content_hash) VALUES (?, ?, 'thumb', ?, '{}', 10, ?, 'abc123')",
                (new_id(), asset_id, f"{asset_id}/thumb.jpg", _EPOCH),
            )
        ],
    )
    token = client.get("/api/reclaim").json()["assets"][0]["art"]
    assert isinstance(token, str) and token


def test_a_vaulted_redundancy_is_absent_from_reclaim_while_the_vault_is_shut(
    client: TestClient,
) -> None:
    """Reclaim names where every copy of a file sits, which for a vaulted file is exactly what the
    vault withholds. With the vault shut it is gone from the LIST (not a row with its paths and
    sizes blanked, absent), and the page says how many it dropped for that reason, so a page that
    comes back short does not read as the end of the list.

    `total` and `total_reclaimable_bytes` are deliberately NOT held to the vault, and this asserts
    that too rather than leaving it unsaid. They are whole-library figures over a population, the
    same as the review queue's own totals: they say how many and how much, never which. Scoping
    them would mean resolving permission for every asset in the library to draw one sentence.
    """
    asset_id = seed_exact_duplicate(client)
    sign_in(client, "admin")
    hide_for_caller(client, "asset", asset_id)

    body = client.get("/api/reclaim").json()
    assert body["assets"] == []
    assert body["concealed"] == 1
    assert body["total"] == 1
    assert body["total_reclaimable_bytes"] > 0


def test_a_vaulted_redundancy_is_absent_from_reclaim_where_hidden_leaves_a_locked_tile(
    client: TestClient,
) -> None:
    """The same with a locked tile asked for: a file shown as a tile still has its copies' paths
    on this page, so it is dropped here too."""
    asset_id = seed_exact_duplicate(client)
    sign_in(client, "admin")
    changed = client.put("/api/settings", json={"values": {"vault.concealment": "placeholder"}})
    assert changed.status_code == 204
    hide_for_caller(client, "asset", asset_id)

    body = client.get("/api/reclaim").json()
    assert body["assets"] == []
    assert body["concealed"] == 1


def test_reclaim_names_each_copy_by_its_full_path_for_an_admin(client: TestClient) -> None:
    """Two copies of one file usually differ only in where they sit, so each is named by the
    whole path (`kernel.where`)."""
    seed_exact_duplicate(client)
    sign_in(client, "admin")

    copies = client.get("/api/reclaim").json()["assets"][0]["copies"]
    paths = sorted(copy["path"] for copy in copies)
    assert [path.startswith("/library/videos") for path in paths] == [True, True]
    assert all(
        path.endswith(name) for path, name in zip(paths, ("one.mp4", "two.mp4"), strict=True)
    )


def test_an_exact_duplicate_is_absent_from_the_queue(client: TestClient) -> None:
    """The distinction, over HTTP: it is in reclaim, and it is not a question."""
    seed_exact_duplicate(client)
    sign_in(client, "admin")

    scan(client)

    assert client.get("/api/dedup/groups").json()["groups"] == []
    assert client.get("/api/reclaim").json()["assets"] != []


def seed_exact_duplicate_on_disk(client: TestClient) -> str:
    """The same case as `seed_exact_duplicate`, with REAL FILES behind the two rows.

    Every other test in this file is about a refusal or a read, so none of them has ever needed
    the bytes to be there. Letting a copy go DELETES one, and a test of that against paths nothing
    is at would be a test of the error path wearing the success path's name.
    """
    root = db_path(client).parent.parent / "copies"
    root.mkdir(parents=True, exist_ok=True)
    for name in ("one.mp4", "two.mp4"):
        (root / name).write_bytes(b"x" * 5000)

    root_id, folder_id, asset_id = new_id(), new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root_id, "Copies", str(root), _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, NULL, '', 'Copies')",
                (folder_id, root_id),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, phash, size_bytes, added_at) "
                "VALUES (?, ?, 'video', ?, 5000, ?)",
                (asset_id, f"digest-{asset_id}", _PHASH, _EPOCH),
            ),
            *[
                (
                    "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                    "filename, size_bytes, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 5000, ?, ?)",
                    (new_id(), asset_id, root_id, folder_id, name, name, _EPOCH, _EPOCH),
                )
                for name in ("one.mp4", "two.mp4")
            ],
        ],
    )
    return asset_id


def test_a_page_of_copies_is_let_go_of_in_one_press(client: TestClient) -> None:
    """The exact-copies queue settles a page at a time, like the near-duplicate queue beside it:
    the two are tabs of one job."""
    asset_id = seed_exact_duplicate_on_disk(client)
    sign_in(client, "admin")
    copies = client.get("/api/reclaim").json()["assets"][0]["copies"]
    going = copies[1:]

    response = client.post(
        "/api/reclaim/release-many",
        json={
            "releases": [{"asset_id": asset_id, "location_id": one["location_id"]} for one in going]
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"released": len(going), "refused": 0}
    # And the file is down to one place, which is what the screen said it would do.
    assert client.get("/api/reclaim").json()["assets"] == []


def test_a_copy_that_cannot_go_is_counted_rather_than_stopping_the_page(
    client: TestClient,
) -> None:
    """A refusal counts and the page carries on: the copies after it are still somebody's
    decision, and a page that stopped at the first refusal would leave them undone with no way to
    tell which. The last copy of a file is never let go, which is the refusal used here."""
    asset_id = seed_exact_duplicate_on_disk(client)
    sign_in(client, "admin")
    copies = client.get("/api/reclaim").json()["assets"][0]["copies"]

    response = client.post(
        "/api/reclaim/release-many",
        json={
            "releases": [
                {"asset_id": asset_id, "location_id": NEVER_EXISTED},
                {"asset_id": asset_id, "location_id": copies[1]["location_id"]},
            ]
        },
    )

    assert response.json() == {"released": 1, "refused": 1}


def test_letting_a_page_of_copies_go_is_refused_for_a_guest(client: TestClient) -> None:
    """It deletes files from the disk. Admin, like every other way of doing that."""
    sign_in(client, "guest")

    answer = client.post("/api/reclaim/release-many", json={"releases": []})

    assert answer.status_code == 403


def test_releasing_a_copy_that_is_not_there_is_a_miss(client: TestClient) -> None:
    asset_id = seed_exact_duplicate(client)
    sign_in(client, "admin")

    response = client.post(f"/api/reclaim/{asset_id}/release", json={"location_id": NEVER_EXISTED})

    assert response.status_code == 404


def scan(client: TestClient) -> None:
    """Run a real scan, on a connection of this test's own.

    Deliberately not `client.app.state.dedup.scan()`. That service holds the running application's
    database handle, which is bound to the event loop the test client drives it on: reaching for
    it from here means a lock held on one loop being awaited from another, which fails as "bound to
    a different event loop" for reasons that have nothing to do with duplicate-finding. It happens
    to work while the app's loop is idle, which is the worst kind of passing test: correct until
    something else in the suite makes the timing less kind.

    So this builds the same service over its own connection, the same way the seeding above writes
    through one. It is a real scan against the real database, just not one reaching across a loop
    boundary to get there.
    """

    async def run() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            service = DedupService(database, DuplicateReads(database), _NeverRemoves())
            await service.scan()
        finally:
            await database.close()

    asyncio.run(run())


class _NeverRemoves:
    """The removal seam, for a scan that has no business removing anything.

    A scan never calls it (finding duplicates and acting on them are separate operations, which is
    the point of the whole feature), so this raises rather than recording. If a scan ever reaches
    it, that is the "Sift never auto-deletes" promise breaking, and it should break loudly here
    rather than be quietly tallied.
    """

    async def remove(self, asset_id: str, **kwargs: object) -> None:
        raise AssertionError("a scan asked to remove a file; it must never do that")


# --- what the queue says about itself ----------------------------------------------------------


def set_setting(client: TestClient, key: str, value: object) -> None:
    """Change an instance-wide preference the way the settings screen does."""
    response = client.put("/api/settings", json={"values": {key: value}})
    assert response.status_code == 204, response.text


def test_the_queue_says_what_the_settings_are_hiding(client: TestClient) -> None:
    """The number that stops a filtered-empty screen reading as a library with no duplicates.

    The seeded pair is one bit apart, so `high` shows it and `exact` does not, and at `exact` the
    screen must still be able to say that one pair is waiting, or it is telling somebody they have
    no near duplicates when what happened is that they set the dial too tight.
    """
    seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    set_setting(client, "dedup.level", "high")
    loose = client.get("/api/dedup/groups").json()
    assert (loose["matching"], loose["pending_total"]) == (1, 1)
    assert len(loose["groups"]) == 1
    assert loose["level"] == "high"

    set_setting(client, "dedup.level", "exact")
    tight = client.get("/api/dedup/groups").json()
    assert tight["groups"] == []
    assert (tight["matching"], tight["pending_total"]) == (0, 1), "the pair is hidden, not gone"
    assert tight["level"] == "exact"


def test_moving_the_level_needs_no_second_scan(client: TestClient) -> None:
    """The property the whole arrangement exists for, asserted end to end.

    One scan, then the dial moved twice, and the pair comes back the second time from the row the
    first scan wrote. Nothing compares anything in between.
    """
    seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    set_setting(client, "dedup.level", "exact")
    assert client.get("/api/dedup/groups").json()["groups"] == []

    set_setting(client, "dedup.level", "low")
    assert len(client.get("/api/dedup/groups").json()["groups"]) == 1


def test_the_length_rule_can_be_switched_off_with_a_zero(client: TestClient) -> None:
    """Zero seconds means do not compare lengths at all, and the response says so with a null.

    The seeded pair is two photographs, which have no duration, so this pins the plumbing rather
    than the filtering: the value reaches the read as "no rule" instead of as "they must run for
    exactly the same zero milliseconds", which would be a rule that hides everything.
    """
    seed_near_duplicate(client)
    sign_in(client, "admin")
    scan(client)

    set_setting(client, "dedup.max_duration_gap_seconds", 0)
    off = client.get("/api/dedup/groups").json()
    assert off["max_duration_gap_ms"] is None
    assert len(off["groups"]) == 1

    set_setting(client, "dedup.max_duration_gap_seconds", 10)
    assert client.get("/api/dedup/groups").json()["max_duration_gap_ms"] == 10_000


def test_a_concealed_group_is_counted_rather_than_silently_missing(client: TestClient) -> None:
    """A page that is quietly shorter is a page nobody can trust.

    The vault has to remove the group (that is what a vault is for), but the screen may still say
    a number, because "one group is in a vault you have not opened" gives up nothing that unlocking
    the vault would not, and silence here looks exactly like a clean library.
    """
    first, _second = seed_near_duplicate(client)
    sign_in(client, "admin")
    hide_for_caller(client, "asset", first)
    scan(client)

    body = client.get("/api/dedup/groups").json()
    assert body["groups"] == []
    assert body["concealed"] == 1


def test_videos_nothing_has_fingerprinted_are_counted(client: TestClient) -> None:
    """Told apart from an empty library, because the two draw the same empty screen."""
    sign_in(client, "admin")
    assert client.get("/api/dedup/groups").json()["awaiting_fingerprint"] == 0

    root_id, folder_id, asset_id = new_id(), new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root_id, "Videos", "/library/videos", _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
                "VALUES (?, ?, NULL, '', 'Videos')",
                (folder_id, root_id),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
                "VALUES (?, ?, 'video', 5000, ?)",
                (asset_id, f"digest-{asset_id}", _EPOCH),
            ),
        ],
    )

    assert client.get("/api/dedup/groups").json()["awaiting_fingerprint"] == 1

    # Written off: the decoder refused this file's frames, so it is not waiting for anything and
    # the queue says so in its own sentence. Two numbers rather than one, because "not looked at
    # yet" and "cannot ever be compared" ask the reader for opposite things.
    write(
        db_path(client),
        [
            (
                "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
                " VALUES (?, 'fingerprints', 'not_decodable', 'the frames would not decode', 0, ?)",
                (asset_id, _EPOCH),
            )
        ],
    )

    body = client.get("/api/dedup/groups").json()
    assert body["awaiting_fingerprint"] == 0
    assert body["cannot_fingerprint"] == 1


def test_both_queues_open_where_the_page_was_for_a_row_that_is_gone(client: TestClient) -> None:
    """The two tabs keep their page in their address (`from`, and `near` beside it). A group or a file that
    has been decided away names nothing, and the answer is the page it was on, never the top."""
    sign_in(client, "admin")
    cases: tuple[tuple[str, dict[str, str | bool]], ...] = (
        ("/api/dedup/groups", {}),
        ("/api/dedup/groups", {"needs_you": True}),
        ("/api/reclaim", {}),
    )
    for address, params in cases:
        answer = client.get(address, params={**params, "from": "phash:gone", "near": 2})
        assert answer.status_code == 200, answer.text
        assert answer.json()["offset"] == 2, f"{address} served the top for a row that is gone"


def test_a_page_asked_for_from_a_group_opens_at_that_group(client: TestClient) -> None:
    """`from` names a group by its method and smallest file (the name the board's stills carry),
    and the page starts there, so the way back lands where somebody left off."""
    seed_near_duplicate(client)
    _another_near_duplicate(client)
    _more_near_duplicates(client, 2)
    sign_in(client, "admin")
    scan(client)
    whole = client.get("/api/dedup/groups").json()["groups"]
    third = whole[2]

    page = client.get(
        "/api/dedup/groups",
        params={"limit": 2, "from": f"{third['method']}:{third['files'][0]['id']}"},
    ).json()

    assert page["offset"] == 2
    assert [one["files"][0]["id"] for one in page["groups"]] == [
        one["files"][0]["id"] for one in whole[2:4]
    ]


def _name_on(client: TestClient, asset_id: str) -> None:
    """Put a person on one file the way a folder read does, on a connection of the test's own."""

    async def run() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                person_id = await create_person_on(connection, "Ada Lumen", made=MADE_BY_A_PERSON)
                assert person_id is not None
                await attribute_assets_on(
                    connection, asset_ids=[asset_id], person_id=person_id, source="folder"
                )
        finally:
            await database.close()

    asyncio.run(run())


def test_the_carry_count_is_what_the_press_then_writes(client: TestClient) -> None:
    """Two files identical on the fingerprint, one of them naming somebody: the count says one
    file will change before the press, the press changes that one file, and afterwards there is
    nothing left to offer."""
    first, second = seed_near_duplicate(client)
    write(db_path(client), [("UPDATE assets SET phash = ? WHERE id = ?", (_PHASH, second))])
    _name_on(client, first)
    sign_in(client, "admin")
    scan(client)

    assert client.get("/api/dedup/carry").json() == {"groups": 1, "files": 1}

    done = client.post("/api/dedup/carry/all")
    assert done.status_code == 200, done.text
    assert done.json() == {"carried": 1, "files": 1}
    people = asyncio.run(_people_on(client, second))
    assert people == ["Ada Lumen"]
    assert client.get("/api/dedup/carry").json() == {"groups": 0, "files": 0}


def test_a_group_whose_carry_wrote_nothing_is_not_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offers are computed from one read and written one at a time from another. A group that
    changed in between writes nothing, and the count says what was written, not what was offered."""
    first, second = seed_near_duplicate(client)
    write(db_path(client), [("UPDATE assets SET phash = ? WHERE id = ?", (_PHASH, second))])
    _name_on(client, first)
    sign_in(client, "admin")
    scan(client)
    assert client.get("/api/dedup/carry").json() == {"groups": 1, "files": 1}

    async def nothing(self: DedupService, offer: object, *, actor: object) -> int:
        return 0

    monkeypatch.setattr(DedupService, "carry", nothing)
    done = client.post("/api/dedup/carry/all")
    assert done.status_code == 200, done.text
    assert done.json() == {"carried": 0, "files": 0}


async def _people_on(client: TestClient, asset_id: str) -> list[str]:
    database = Database(db_path(client), readers=1)
    await database.connect()
    try:
        rows = await database.fetch_all(
            "SELECT p.name FROM asset_people ap JOIN people p ON p.id = ap.person_id "
            "WHERE ap.asset_id = ?",
            (asset_id,),
        )
    finally:
        await database.close()
    return [str(row["name"]) for row in rows]


def test_a_guest_can_neither_count_nor_press_a_carry(client: TestClient) -> None:
    sign_in(client, "guest")

    assert client.get("/api/dedup/carry").status_code == 403
    assert client.post("/api/dedup/carry/all").status_code == 403
