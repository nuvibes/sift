# SPDX-License-Identifier: AGPL-3.0-or-later
"""The heart and the stars: independent of each other, and one set per user."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.tags_ratings.tests.conftest import (
    NEVER_EXISTED,
    Library,
    db_path,
    share,
    sign_in,
)
from sift.testing.auth import give_pin

pytestmark = [pytest.mark.integration]


def _rate(client: TestClient, asset_id: str, rating: int | None):  # type: ignore[no-untyped-def]
    return client.put(f"/api/assets/{asset_id}/rating", json={"rating": rating})


def _heart(client: TestClient, asset_id: str, favorite: bool):  # type: ignore[no-untyped-def]
    return client.put(f"/api/assets/{asset_id}/favorite", json={"favorite": favorite})


def _pin(client: TestClient, asset_id: str, pinned: bool):  # type: ignore[no-untyped-def]
    return client.put(f"/api/assets/{asset_id}/pin", json={"pinned": pinned})


# --- independent --------------------------------------------------------------------------------


def _opinion(reply: object, asset_id: str) -> dict[str, object]:
    """What the reply says about the file, with its id checked and set aside."""
    answered = reply.json()  # type: ignore[attr-defined]
    assert answered.pop("asset_id") == asset_id
    return answered  # type: ignore[no-any-return]


def test_favouriting_leaves_the_rating_alone(client: TestClient, library: Library) -> None:
    """A three-star clip can be a favorite, and saying so does not restate the rating.

    They answer different questions (a shortlist and a judgement about quality) and coupling
    them would mean one control silently editing the other's value.
    """
    sign_in(client)
    _rate(client, library.shared, 3)

    hearted = _heart(client, library.shared, True)

    assert _opinion(hearted, library.shared) == {
        "favorite": True,
        "rating": 3,
        "views": 0,
        "pinned": False,
        "o_count": 0,
    }


def test_clearing_the_heart_leaves_the_stars(client: TestClient, library: Library) -> None:
    sign_in(client)
    _rate(client, library.shared, 4)
    _heart(client, library.shared, True)

    cleared = _heart(client, library.shared, False)

    assert _opinion(cleared, library.shared) == {
        "favorite": False,
        "rating": 4,
        "views": 0,
        "pinned": False,
        "o_count": 0,
    }


def test_rating_leaves_the_heart_alone(client: TestClient, library: Library) -> None:
    sign_in(client)
    _heart(client, library.shared, True)

    rated = _rate(client, library.shared, 2)

    assert _opinion(rated, library.shared) == {
        "favorite": True,
        "rating": 2,
        "views": 0,
        "pinned": False,
        "o_count": 0,
    }


def test_a_rating_can_be_cleared_without_touching_the_heart(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    _heart(client, library.shared, True)
    _rate(client, library.shared, 5)

    cleared = _rate(client, library.shared, None)

    assert _opinion(cleared, library.shared) == {
        "favorite": True,
        "rating": None,
        "views": 0,
        "pinned": False,
        "o_count": 0,
    }


# --- what a rating is allowed to be --------------------------------------------------------------


@pytest.mark.parametrize("stars", [1, 2, 3, 4, 5])
def test_every_whole_star_is_accepted(client: TestClient, library: Library, stars: int) -> None:
    sign_in(client)

    assert _rate(client, library.shared, stars).json()["rating"] == stars


def test_zero_is_refused_rather_than_read_as_unrated(client: TestClient, library: Library) -> None:
    """Clearing a rating says null. Zero is the value a caller reaches for to mean the same thing,
    and stored it would sort and filter as a real rating for ever after, so it is a mistake that
    gets told about rather than one that is quietly accepted."""
    sign_in(client)

    assert _rate(client, library.shared, 0).status_code == 422


# Eleven rather than six: a rating is STORED out of ten whichever scale is drawn, so six is
# an ordinary rating now: three stars on a five-star screen.
@pytest.mark.parametrize("stars", [-1, 11, 100])
def test_a_rating_off_the_scale_is_refused(
    client: TestClient, library: Library, stars: int
) -> None:
    sign_in(client)

    assert _rate(client, library.shared, stars).status_code == 422


# --- per user ------------------------------------------------------------------------------------


def test_one_account_cannot_see_another_accounts_stars(
    client: TestClient, library: Library
) -> None:
    """The reason the state is a table rather than two columns on the asset.

    Two people rating the same file get their own answers. If this ever collapses into one shared
    value, the first guest to rate something overwrites an admin's own judgement of it.
    """
    first = sign_in(client, "guest", who="first")
    share(client, library.shared, first)
    _rate(client, library.shared, 5)
    _heart(client, library.shared, True)

    second = sign_in(client, "guest", who="second")
    share(client, library.shared, second)

    theirs = client.get(f"/api/assets/{library.shared}")
    assert theirs.json()["rating"] is None, "one account was shown another's rating"
    assert theirs.json()["favorite"] is False, "one account was shown another's heart"


def test_an_account_writing_its_own_rating_does_not_move_anybody_elses(
    client: TestClient, library: Library
) -> None:
    first = sign_in(client, "guest", who="first")
    share(client, library.shared, first)
    _rate(client, library.shared, 5)

    second = sign_in(client, "guest", who="second")
    share(client, library.shared, second)
    _rate(client, library.shared, 1)

    sign_in(client, "guest", who="first")
    assert client.get(f"/api/assets/{library.shared}").json()["rating"] == 5


# --- the pin ------------------------------------------------------------------------------------


def test_pinning_a_file_leaves_the_heart_and_the_stars_alone(
    client: TestClient, library: Library
) -> None:
    """The sixth opinion, and independent of the other five exactly as they are of each other.

    One upsert touching one column is what gives that for free, but "for free" is the kind of
    claim that stops being true the day somebody widens the statement, which is why it is asserted
    rather than reasoned about.
    """
    sign_in(client)
    _rate(client, library.shared, 4)
    _heart(client, library.shared, True)

    pinned = _pin(client, library.shared, True)

    assert _opinion(pinned, library.shared) == {
        "favorite": True,
        "rating": 4,
        "views": 0,
        "pinned": True,
        "o_count": 0,
    }


def test_a_pin_comes_off_again(client: TestClient, library: Library) -> None:
    sign_in(client)
    _pin(client, library.shared, True)

    assert _opinion(_pin(client, library.shared, False), library.shared) == {
        "favorite": False,
        "rating": None,
        "views": 0,
        "pinned": False,
        "o_count": 0,
    }


def test_a_pinned_file_comes_first_whatever_the_wall_is_sorted_by(
    client: TestClient, library: Library
) -> None:
    """A pinned file comes first under two disagreeing orders, on a wall that honours the pin."""
    sign_in(client)

    def wall(sort: str, *, curated: bool = True) -> list[str]:
        params: dict[str, object] = {"sort": sort, "limit": 50}
        if curated:
            params["pinned_first"] = True
        answered = client.get("/api/assets", params=params)
        assert answered.status_code == 200, answered.text
        return [item["id"] for item in answered.json()["items"]]

    newest = wall("newest")
    assert len(newest) > 1, "this needs more than one visible file to say anything"
    last = newest[-1]

    _pin(client, last, True)

    assert wall("newest")[0] == last
    assert wall("oldest")[0] == last, "the arm is under the chosen sort rather than in front of it"
    # And the wall that does not curate is untouched by any of it.
    assert wall("newest", curated=False)[-1] == last


def test_the_pin_is_this_account_s_own(client: TestClient, library: Library) -> None:
    """Two people sharing an install pin their own walls, exactly as they heart their own.

    It is a per-user opinion and not a fact about the file, so it must not reach anybody else,
    and the wall a guest sees must not be re-ordered by what an admin put at the top of theirs.
    """
    guest = sign_in(client, role="guest")
    share(client, library.shared, guest)

    sign_in(client)
    _pin(client, library.shared, True)

    sign_in(client, role="guest")
    answered = client.get(f"/api/assets/{library.shared}")
    assert answered.status_code == 200, answered.text
    assert answered.json()["pinned"] is False


# --- a selection, in one request ----------------------------------------------------------------
#
# One request per selection: every id written, the other opinions untouched, an unreachable file
# skipped with a reason.


def _heart_many(client: TestClient, asset_ids: list[str], favorite: bool):  # type: ignore[no-untyped-def]
    return client.post("/api/assets/favorite", json={"asset_ids": asset_ids, "favorite": favorite})


def _rate_many(client: TestClient, asset_ids: list[str], rating: int | None):  # type: ignore[no-untyped-def]
    return client.post("/api/assets/rating", json={"asset_ids": asset_ids, "rating": rating})


def _pin_many(client: TestClient, asset_ids: list[str], pinned: bool):  # type: ignore[no-untyped-def]
    return client.post("/api/assets/pin", json={"asset_ids": asset_ids, "pinned": pinned})


def _seen(client: TestClient, asset_id: str) -> dict[str, object]:
    answered = client.get(f"/api/assets/{asset_id}")
    assert answered.status_code == 200, answered.text
    return dict(answered.json())


def _pins_on_the_wall(client: TestClient) -> dict[str, bool]:
    """Which files this user has pinned, as the wall reports them."""
    answered = client.get("/api/assets", params={"limit": 50})
    assert answered.status_code == 200, answered.text
    return {item["id"]: bool(item["pinned"]) for item in answered.json()["items"]}


def test_one_call_hearts_the_whole_selection(client: TestClient, library: Library) -> None:
    sign_in(client)

    done = _heart_many(client, [library.shared, library.private], True)

    assert done.status_code == 200, done.text
    assert done.json() == {
        "changed": 2,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert _seen(client, library.shared)["favorite"] is True
    assert _seen(client, library.private)["favorite"] is True


def test_one_call_rates_the_whole_selection_and_clears_it_again(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    both = [library.shared, library.private]

    assert _rate_many(client, both, 4).json()["changed"] == 2
    assert [_seen(client, one)["rating"] for one in both] == [4, 4]

    assert _rate_many(client, both, None).json()["changed"] == 2
    assert [_seen(client, one)["rating"] for one in both] == [None, None]


def test_one_call_pins_the_whole_selection(client: TestClient, library: Library) -> None:
    sign_in(client)
    both = [library.shared, library.private]

    assert _pin_many(client, both, True).json()["changed"] == 2
    assert [_pins_on_the_wall(client)[one] for one in both] == [True, True]

    assert _pin_many(client, both, False).json()["changed"] == 2
    assert [_pins_on_the_wall(client)[one] for one in both] == [False, False]


def test_the_three_list_writes_leave_each_other_alone(client: TestClient, library: Library) -> None:
    """One statement per opinion, each touching one column: the same claim the singles make.

    Asserted rather than reasoned about, because "it touches one column" is exactly the kind of
    property that stops being true the day somebody widens the statement to save a round trip.
    """
    sign_in(client)
    both = [library.shared, library.private]
    _rate_many(client, both, 5)
    _heart_many(client, both, True)
    _pin_many(client, both, True)

    _rate_many(client, both, 2)

    pinned = _pins_on_the_wall(client)
    for one in both:
        seen = _seen(client, one)
        assert (seen["rating"], seen["favorite"], pinned[one]) == (2, True, True)


def test_a_file_the_caller_cannot_reach_is_skipped_rather_than_failing_the_call(
    client: TestClient, library: Library
) -> None:
    """The whole reason a bulk write answers counts instead of a status.

    A guest given one of the two files asks about both. The one they were given is written, the
    other is left alone and counted, and the sentence says why without saying whether the id names
    anything, which is the one fact the access model exists to withhold.
    """
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    done = _heart_many(client, [library.shared, library.private], True)

    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["changed"], body["skipped"]) == (1, 1)
    assert body["vault_locked"] is False
    assert body["reason"] and "could not find" in body["reason"].lower()
    assert _seen(client, library.shared)["favorite"] is True


def test_a_file_in_the_callers_own_vault_is_skipped_with_the_unlock_offer(
    client: TestClient, library: Library
) -> None:
    """The other skip, and the only one the person can act on.

    `vault_locked` is a flag and not a sentence to match on, because the sentence is free text:
    a screen comparing it against its own copy is two lists that must agree, and the day the
    wording improves the Unlock button silently stops appearing.
    """
    user_id = sign_in(client)
    give_pin(db_path(client), user_id)
    assert client.put(f"/api/assets/{library.private}/vault", json={"vault": True}).status_code == (
        204
    )

    done = _rate_many(client, [library.shared, library.private], 3)

    body = done.json()
    assert (body["changed"], body["skipped"]) == (1, 1)
    assert body["vault_locked"] is True
    assert body["reason"] and "vault" in body["reason"].lower()
    assert _seen(client, library.shared)["rating"] == 3


def test_the_same_file_named_twice_is_one_row_and_one_skip(
    client: TestClient, library: Library
) -> None:
    """A selection is a set. Counting the second mention would report two of one file."""
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    done = _heart_many(
        client, [library.shared, library.shared, library.private, library.private], True
    )

    assert (done.json()["changed"], done.json()["skipped"]) == (1, 1)


def test_a_rating_off_the_scale_is_refused_for_a_selection_too(
    client: TestClient, library: Library
) -> None:
    """The same bound as the single write, in the same place: the body, before anything is read."""
    sign_in(client)

    assert _rate_many(client, [library.shared], 0).status_code == 422
    assert _rate_many(client, [library.shared], 11).status_code == 422
    assert _rate_many(client, [], 3).status_code == 422


def test_the_per_file_routes_still_answer_the_whole_opinion(
    client: TestClient, library: Library
) -> None:
    """The list routes are additions, not replacements. One tile still settles on one answer."""
    sign_in(client)
    _rate_many(client, [library.shared], 3)

    assert _opinion(_heart(client, library.shared, True), library.shared) == {
        "favorite": True,
        "rating": 3,
        "views": 0,
        "pinned": False,
        "o_count": 0,
    }


# --- the O counter ------------------------------------------------------------------------------
#
# Over HTTP: the closed set of words, the resolve, and the whole opinion as the reply.


def _press(client: TestClient, asset_id: str, change: str):  # type: ignore[no-untyped-def]
    return client.put(f"/api/assets/{asset_id}/o-count", json={"change": change})


def test_a_press_counts_one_and_leaves_every_other_opinion_alone(
    client: TestClient, library: Library
) -> None:
    """A press counts one and leaves every other opinion alone."""
    sign_in(client)
    _rate(client, library.shared, 4)
    _heart(client, library.shared, True)

    assert _opinion(_press(client, library.shared, "up"), library.shared) == {
        "favorite": True,
        "rating": 4,
        "views": 0,
        "pinned": False,
        "o_count": 1,
    }


def test_presses_add_up(client: TestClient, library: Library) -> None:
    """Three presses are three, which is the whole of what a counter promises.

    It is worth a test rather than being obvious: the arithmetic is in SQL, and a statement written
    to SET the column instead of adding to it passes every single-press test there is.
    """
    sign_in(client)
    for _ in range(3):
        _press(client, library.shared, "up")

    assert _opinion(_press(client, library.shared, "up"), library.shared)["o_count"] == 4


def test_one_can_be_taken_back(client: TestClient, library: Library) -> None:
    """A press costs one click, so taking one back has to as well."""
    sign_in(client)
    _press(client, library.shared, "up")
    _press(client, library.shared, "up")

    assert _opinion(_press(client, library.shared, "down"), library.shared)["o_count"] == 1


def test_it_never_goes_below_nothing(client: TestClient, library: Library) -> None:
    """The clamp, and it is in the statement rather than in a check anybody could skip.

    A counter that can go negative is a number no screen has a way to draw, and the row would sit
    there being wrong for ever: nothing ever reads it back and corrects it.
    """
    sign_in(client)

    assert _opinion(_press(client, library.shared, "down"), library.shared)["o_count"] == 0
    assert _opinion(_press(client, library.shared, "down"), library.shared)["o_count"] == 0


def test_it_can_be_started_again(client: TestClient, library: Library) -> None:
    """Back to nothing in one act rather than in as many presses as it took to get here."""
    sign_in(client)
    for _ in range(5):
        _press(client, library.shared, "up")

    assert _opinion(_press(client, library.shared, "reset"), library.shared)["o_count"] == 0


def test_a_tally_is_this_accounts_own(client: TestClient, library: Library) -> None:
    """Two users on one install keep their own, exactly as they keep their own hearts.

    This is the decision this feature was built ON rather than a detail of it: one number per
    scene would serve one person looking, and Sift has users.
    """
    guest = sign_in(client, role="guest")
    share(client, library.shared, guest)

    sign_in(client)
    _press(client, library.shared, "up")
    _press(client, library.shared, "up")

    sign_in(client, role="guest")
    assert _opinion(_press(client, library.shared, "up"), library.shared)["o_count"] == 1


def test_an_unknown_act_is_refused_before_anything_is_written(
    client: TestClient, library: Library
) -> None:
    """A closed set of words, refused by the model rather than falling through to a default.

    Falling through is the shape worth refusing: `{"change": "reste"}` would otherwise be a press
    that silently cleared the tally.
    """
    sign_in(client)
    _press(client, library.shared, "up")

    assert _press(client, library.shared, "reste").status_code == 422
    assert _opinion(_press(client, library.shared, "up"), library.shared)["o_count"] == 2


def test_a_file_this_account_cannot_reach_is_a_404(client: TestClient, library: Library) -> None:
    """The same answer an id that was never minted gets.

    A 403 here would confirm the file is there, which for a library organised by person is most of
    what was being asked, and it would also leave a state row hanging off it.
    """
    sign_in(client, role="guest")

    assert _press(client, library.private, "up").status_code == 404
    assert _press(client, NEVER_EXISTED, "up").status_code == 404
