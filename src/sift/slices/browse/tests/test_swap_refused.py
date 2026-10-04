# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file no swap will send says so on its tile, for an admin.

Swap mode marks every tile that will not go (Kept local, or "Don't swap", on the file or on anything
it is filed under) with nobody pressing anything, so the fact rides on the tile, read once for the
page. A swap is an admin's, so a guest's tiles never carry it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.browse.tests.conftest import Library, db_path, share, sign_in, write


def _refused(client: TestClient) -> dict[str, bool]:
    answer = client.get("/api/assets")
    assert answer.status_code == 200, answer.text
    return {row["id"]: row["swap_refused"] for row in answer.json()["items"]}


def test_a_file_kept_out_of_swaps_says_so_and_the_one_beside_it_does_not(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    write(
        db_path(client),
        [("UPDATE assets SET keep_from_swaps = 1 WHERE id = ?", (library.private,))],
    )
    assert _refused(client) == {library.private: True, library.shared: False}

    # Kept local keeps it out of swaps too.
    write(
        db_path(client),
        [
            ("UPDATE assets SET keep_from_swaps = 0 WHERE id = ?", (library.private,)),
            ("UPDATE assets SET keep_local = 1 WHERE id = ?", (library.shared,)),
        ],
    )
    assert _refused(client) == {library.private: False, library.shared: True}


def test_a_guest_is_never_told(client: TestClient, library: Library) -> None:
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)
    write(db_path(client), [("UPDATE assets SET keep_local = 1 WHERE id = ?", (library.shared,))])
    assert _refused(client) == {library.shared: False}
