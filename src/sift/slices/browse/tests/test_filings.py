# SPDX-License-Identifier: AGPL-3.0-or-later
"""A History line's number opens exactly the files it counted.

A line saying "4 files" must open those four, not the whole Site, tag or person: the Files wall has
to be able to say "by this source, on this day". `?filed=`, `?tagged=` and `?named=` say it
(`constraints.Filing`), and these hold the path end to end: the line the History draws, its link
followed to the wall, and the wall's total equal to the line's number, inside the viewer's own
scope and behind a shut vault.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.browse.tests.conftest import Library, db_path, share, sign_in, write
from sift.testing.auth import hide_for_caller

pytestmark = [pytest.mark.integration]

#: Two days, a day apart, on the suite's UTC clock. 1,700,000,100 is in mid-November 2023.
DAY_ONE = 1_700_000_100
DAY_TWO = DAY_ONE + 86_400

_SITE = "INSERT INTO sites (id, name) VALUES (?, ?)"
_USERNAME = "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 0)"
_FILED = (
    "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at) VALUES (?, ?, ?, ?)"
)
_TAG = "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)"
_TAGGED = "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)"
_PERSON = "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)"
_NAMED = "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)"
_BOX = "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)"
_MATCH = (
    "INSERT INTO asset_stash_box_matches (asset_id, box_id, remote_id, payload, grade, state,"
    " found_at) VALUES (?, ?, 'remote', '{}', 'certain', 'applied', 0)"
)


def _site_filed(client: TestClient, filings: list[tuple[str, str | None, int | None]]) -> str:
    """A Site with one username, and these files filed under it by (source, moment)."""
    site_id, username_id = new_id(), new_id()
    write(
        db_path(client),
        [
            (_SITE, (site_id, "SomeSite")),
            (_USERNAME, (username_id, site_id, "esmewrenfield")),
            *((_FILED, (asset, username_id, source, at)) for asset, source, at in filings),
        ],
    )
    return site_id


def _ids(client: TestClient, **params: str) -> list[str]:
    body = client.get("/api/assets", params=params).json()
    ids = sorted(one["id"] for one in body["items"])
    assert body["total"] == len(ids), "the total counts a different set than the page draws"
    return ids


def _counted_lines(client: TestClient, site_id: str) -> list[dict[str, Any]]:
    answer = client.get(f"/api/sites/{site_id}/history")
    assert answer.status_code == 200
    # The things a line names are its PIECES, each where it sits.
    return [
        {**piece, "name": piece["text"]}
        for event in answer.json()
        for piece in event["pieces"]
        if piece["kind"] == "files" and piece.get("href")
    ]


def test_a_history_count_opens_exactly_the_files_it_counted(
    client: TestClient, library: Library
) -> None:
    """The line's number, pressed, is the wall's total.

    Two filings by one source on two days are two lines; each opens its own day's file and not the
    Site's two. The plain Site filter is asked beside it to show the difference is real."""
    sign_in(client, "admin")
    site_id = _site_filed(
        client, [(library.shared, "folder", DAY_ONE), (library.private, "folder", DAY_TWO)]
    )

    lines = _counted_lines(client, site_id)
    assert len(lines) == 2
    for line in lines:
        query = urlsplit(line["href"]).query
        wall = client.get(f"/api/assets?{query}").json()
        assert line["name"] == "1 file"
        assert wall["total"] == 1, f"{line['href']} opened {wall['total']} files, not 1"

    first = client.get("/api/assets", params={"filed": f"{site_id}~folder~2023-11-14"}).json()
    assert [one["id"] for one in first["items"]] == [library.shared]
    assert _ids(client, filed=f"{site_id}~folder~2023-11-15") == [library.private]
    # The same Site's whole set, which is what the line must not open.
    assert _ids(client, sites="SomeSite") == sorted([library.shared, library.private])
    # And it narrows rather than replaces: a word nothing matches still empties it.
    assert _ids(client, filed=f"{site_id}~folder~2023-11-14", q="nothing-like-this") == []


#: 23:30 on 15 September 2026 on a machine in New York: 03:30 on the 16th in UTC.
HALF_PAST_ELEVEN = 1_789_529_400


def test_a_filing_at_half_past_eleven_is_that_evenings_line(
    client: TestClient, library: Library, machine_zone: Callable[[str], None]
) -> None:
    """The day is the machine's, not Greenwich's: a filing at 23:30 on the 15th and one an hour
    later are two days' lines, and the evening's names and opens the 15th. By UTC both would be
    the 16th, one line, and the evening's filing would open a day it was not made on."""
    machine_zone("EST5EDT")
    sign_in(client, "admin")
    site_id = _site_filed(
        client,
        [
            (library.shared, "folder", HALF_PAST_ELEVEN),
            (library.private, "folder", HALF_PAST_ELEVEN + 3_600),
        ],
    )

    lines = _counted_lines(client, site_id)
    assert sorted(urlsplit(line["href"]).query for line in lines) == [
        f"filed={site_id}~folder~2026-09-15",
        f"filed={site_id}~folder~2026-09-16",
    ]
    assert _ids(client, filed=f"{site_id}~folder~2026-09-15") == [library.shared]
    assert _ids(client, filed=f"{site_id}~folder~2026-09-16") == [library.private]


def test_a_filing_by_hand_and_an_undated_one_are_lines_of_their_own(
    client: TestClient, library: Library
) -> None:
    """A NULL source and a NULL day are real values of the group, not "any": the line that says
    "60 files filed under it" with no date is exactly the rows with neither."""
    sign_in(client, "admin")
    site_id = _site_filed(client, [(library.shared, None, None), (library.private, None, DAY_ONE)])

    assert _ids(client, filed=f"{site_id}~~") == [library.shared]
    assert _ids(client, filed=f"{site_id}~~2023-11-14") == [library.private]


def test_a_tag_and_a_person_line_open_their_own_files_and_a_box_splits_them(
    client: TestClient, library: Library
) -> None:
    """The two siblings, and the stash-box arm: one box's filings and the "A stash-box" line (a file
    with no single applied match) on the same day are two sets, as they are two lines."""
    sign_in(client, "admin")
    tag_id, person_id, box_id = new_id(), new_id(), new_id()
    write(
        db_path(client),
        [
            (_TAG, (tag_id, "poolside")),
            (_TAGGED, (library.shared, tag_id, "sift", None)),
            (_TAGGED, (library.private, tag_id, None, DAY_ONE)),
            (_PERSON, (person_id, "Neve Alder")),
            (_BOX, (box_id, "fansdb mirror", "https://example.invalid/graphql")),
            (_MATCH, (library.shared, box_id)),
            (_NAMED, (library.shared, person_id, "stash_box", DAY_ONE)),
            (_NAMED, (library.private, person_id, "stash_box", DAY_ONE)),
        ],
    )

    assert _ids(client, tagged=f"{tag_id}~sift~") == [library.shared]
    assert _ids(client, tagged=f"{tag_id}~~2023-11-14") == [library.private]
    assert _ids(client, named=f"{person_id}~stash_box~2023-11-14~fansdb mirror") == [library.shared]
    assert _ids(client, named=f"{person_id}~stash_box~2023-11-14") == [library.private]
    # Two lines given together narrow together, like any two parameters side by side in the address.
    both = client.get(
        "/api/assets",
        params={"tagged": f"{tag_id}~sift~", "named": f"{person_id}~stash_box~2023-11-14"},
    ).json()
    assert both["items"] == []


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "{site}~download",
        "{site}~download~2023-13-45",
        "{site}~download~14-11-2023",
        "{site}~not-a-source~2023-11-14",
        # A box is only ever a stash-box's.
        "{site}~folder~2023-11-14~fansdb",
    ],
)
def test_a_filing_nobody_could_read_narrows_to_nothing(
    client: TestClient, library: Library, value: str
) -> None:
    """Never to the whole Site: an address that widened when it could not be read would reopen the
    fault it was written to close."""
    sign_in(client, "admin")
    site_id = _site_filed(client, [(library.shared, "download", DAY_ONE)])

    assert _ids(client, filed=value.format(site=site_id)) == []


def test_a_filing_holds_the_locked_vault(client: TestClient, library: Library) -> None:
    """A concealed file counted by the line stays off its wall while the vault is shut: the filing
    is one conjunct inside the same scoped read every wall uses."""
    sign_in(client, "admin")
    site_id = _site_filed(
        client, [(library.shared, "download", DAY_ONE), (library.private, "download", DAY_ONE)]
    )
    hide_for_caller(client, "asset", library.private)

    assert _ids(client, filed=f"{site_id}~download~2023-11-14") == [library.shared]


def test_a_filing_holds_a_guests_scope(client: TestClient, library: Library) -> None:
    """A guest shown one of the two files sees that one, and the address reveals nothing more."""
    sign_in(client, "admin")
    site_id = _site_filed(
        client, [(library.shared, "download", DAY_ONE), (library.private, "download", DAY_ONE)]
    )
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)

    assert _ids(client, filed=f"{site_id}~download~2023-11-14") == [library.shared]
