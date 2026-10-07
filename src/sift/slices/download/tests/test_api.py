# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download endpoints, driven against the real application.

The guarantee under test: this whole slice is admin-only, enforced on the server, with no
setting that opens it. So every endpoint is called directly (no interface in the way) as a guest
and signed out, and every one of them refuses. The other property worth its own test is that a saved
cookie can go in and never comes back out.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel import wiring
from sift.kernel.ids import new_id
from sift.kernel.tunnels import TunnelError
from sift.kernel.wiring import part_of_app
from sift.slices.download import bulk
from sift.slices.download.models import MAX_PASTED_LINKS
from sift.slices.download.service import DownloadService
from sift.slices.download.tests.api_support import (
    _A_LINK,
    _ENDPOINTS,
    _a_folder_in,
    _aim_on_the_ledger,
    _call,
    _fail_the_row,
    _landed_in,
    _ledger_rows,
    _made,
    _on_the_apps_loop,
    _paste_choices,
    _write_row,
    sign_in,
)
from sift.testing.library import seed_asset, seed_root

pytestmark = [pytest.mark.integration]


@pytest.mark.parametrize(
    ("method", "path", "body"), _ENDPOINTS, ids=[e[1] + e[0] for e in _ENDPOINTS]
)
def test_a_guest_is_refused_every_endpoint(
    client: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    sign_in(client, "guest")
    assert _call(client, method, path, body) in {401, 403}


@pytest.mark.parametrize(
    ("method", "path", "body"), _ENDPOINTS, ids=[e[1] + e[0] for e in _ENDPOINTS]
)
def test_signed_out_is_refused_every_endpoint(
    client: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    assert _call(client, method, path, body) in {401, 403}


def test_an_admin_can_submit_and_see_the_download(client: TestClient) -> None:
    sign_in(client, "admin")
    created = client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert created.status_code == 201
    download_id = created.json()["id"]

    listing = client.get("/api/downloads")
    assert listing.status_code == 200
    items = listing.json()["downloads"]
    ids = [item["id"] for item in items]
    assert download_id in ids
    # The address comes back, and a hash of one never does.
    #
    # The address is shown because the queue screen says what each row fetched. This slice is
    # admin-only, so the only reader is the person who pasted it in the box on the same screen,
    # and a history that will not say what it fetched cannot be searched for the download
    # somebody is thinking of.
    #
    # The hash is a different thing and stays out. It is what the ledger dedupes on, so anyone
    # holding a list of links could hash their own copies and read off which of them this Sift had
    # been asked to fetch, and no screen has ever displayed it, so nothing is lost by refusing to
    # send it.
    assert all("url_hash" not in item for item in items)
    assert any(item["url"] == "https://www.tiktok.com/@a/video/1" for item in items)


def test_the_list_is_narrowed_by_what_the_address_asks(client: TestClient) -> None:
    """The tab, the Site, the search and the order reach the ledger's read, and a narrowing that
    matches nothing is an empty page with a count of nought, never the whole queue.

    FastAPI drops a parameter no handler names without a word, so a route that forgot one would
    answer every narrowed request with everything; the waiting row below is what would show.
    """
    sign_in(client, "admin")
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})
    client.post("/api/downloads", json={"url": "https://www.youtube.com/watch?v=abc"})

    whole = client.get("/api/downloads").json()
    assert whole["matched"] == whole["total"] == 2
    assert {one["name"] for one in whole["sites"]} == {"TikTok", "YouTube"}

    tiktok = client.get("/api/downloads", params={"site": "TikTok"}).json()
    assert [one["url"] for one in tiktok["downloads"]] == ["https://www.tiktok.com/@a/video/1"]
    assert tiktok["matched"] == 1
    assert tiktok["total"] == 2

    done = client.get("/api/downloads", params={"show": "done"}).json()
    assert (done["downloads"], done["matched"]) == ([], 0)

    found = client.get("/api/downloads", params={"q": "watch?v=abc", "sort": "site"}).json()
    assert [one["url"] for one in found["downloads"]] == ["https://www.youtube.com/watch?v=abc"]

    assert client.get("/api/downloads", params={"show": "everything"}).status_code == 422

    # Two Sites ticked in the filter panel arrive as a repeated `site`, and are either of them.
    either = client.get("/api/downloads", params=[("site", "TikTok"), ("site", "YouTube")]).json()
    assert either["matched"] == 2
    assert client.get("/api/downloads", params={"site": "x" * 201}).status_code == 422


def test_the_filter_panel_counts_the_queue_by_site(client: TestClient) -> None:
    """The Downloads screen's Site column: every Site inside the lit tab, as a facet answer.

    The state is the tab strip and has no column, so asking for one is refused in the words an
    invented dimension is refused in everywhere else."""
    sign_in(client, "admin")
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})
    client.post("/api/downloads", json={"url": "https://www.youtube.com/watch?v=abc"})

    counted = client.get("/api/downloads/facets", params={"facet": "site"}).json()
    assert counted["facet"] == "site"
    assert {(one["value"], one["count"]) for one in counted["values"]} == {
        ("TikTok", 1),
        ("YouTube", 1),
    }

    done = client.get("/api/downloads/facets", params={"facet": "site", "show": "done"}).json()
    assert done["values"] == []

    assert client.get("/api/downloads/facets", params={"facet": "state"}).status_code == 422


def test_a_bad_page_is_refused(client: TestClient) -> None:
    sign_in(client, "admin")
    assert client.get("/api/downloads", params={"limit": 0}).status_code == 422


def test_an_admin_can_cancel_a_download(client: TestClient) -> None:
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]

    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204
    # Idempotent: a second cancel (or one racing completion) is still a success.
    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204

    listing = client.get("/api/downloads").json()
    assert listing["downloads"][0]["status"] == "canceled"


def test_an_admin_can_pause_a_download_and_start_it_again(client: TestClient) -> None:
    """The two verbs, driven, and the row's word after each.

    A fresh download is queued, which is one of the two states a pause may be taken on: somebody
    pausing a queue before it reaches a link is the ordinary way this is used, and it needs no
    worker to be running for the row to be right.
    """
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]

    assert client.post(f"/api/downloads/{download_id}/pause").status_code == 204
    assert client.get("/api/downloads").json()["downloads"][0]["status"] == "paused"

    assert client.post(f"/api/downloads/{download_id}/resume").status_code == 204
    assert client.get("/api/downloads").json()["downloads"][0]["status"] == "queued"


def test_a_paused_row_says_what_it_kept_when_only_the_disk_knows(
    app: FastAPI, client: TestClient
) -> None:
    """A pause survives a restart and its figure did not: the progress registry is memory. This row
    never ran in this process, which is exactly a paused row after a restart (nothing in the
    registry and the bytes in the job's workspace), and the list reads them there."""
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/2"}
    ).json()["id"]
    assert client.post(f"/api/downloads/{download_id}/pause").status_code == 204
    row = client.get("/api/downloads").json()["downloads"][0]
    assert row["progress"] is None

    workspaces = part_of_app(app, wiring.POOL).workspaces
    assert workspaces is not None
    media = workspaces.of(row["job_id"]) / "media"
    media.mkdir()
    (media / "clip.mp4.part").write_bytes(b"x" * 5000)

    progress = client.get("/api/downloads").json()["downloads"][0]["progress"]
    assert progress["done_bytes"] == 5000
    # Nothing is claimed about a fetch that is not running.
    assert progress["total_bytes"] is None
    assert progress["bytes_per_second"] is None

    # And a row that is not paused says nothing from the disk, whatever is there.
    assert client.post(f"/api/downloads/{download_id}/resume").status_code == 204
    assert client.get("/api/downloads").json()["downloads"][0]["progress"] is None


def test_pausing_something_that_is_not_running_is_refused_with_a_sentence(
    client: TestClient,
) -> None:
    """Not idempotent, unlike every other verb on this row, and that is the decision.

    Telling somebody a download is paused when it has in fact finished is a screen that is simply
    wrong, and the row they are looking at would go on saying `done` under a message saying it
    was paused. So it refuses, and the sentence says what is true of every state it refuses for.
    """
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]
    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204

    refused = client.post(f"/api/downloads/{download_id}/pause")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "This download is not running"
    # And a row nothing knows about, which is the same answer: there is nothing running.
    assert client.post("/api/downloads/01HX0000000000000000000009/pause").status_code == 409


def test_resuming_one_that_was_never_paused_is_refused(client: TestClient) -> None:
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]

    refused = client.post(f"/api/downloads/{download_id}/resume")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "This download is not paused"


def test_a_removed_row_can_be_put_back_and_only_once(client: TestClient) -> None:
    """Remove's undo, and the one place in this slice a verb answers 404 for a row it cannot act on.

    There is no screen listing what has been put away, so the only way here is the message that
    says one just was. An answer of "fine" to a press that restored nothing would leave somebody
    looking at a list for a row that is not coming back.
    """
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]
    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204
    assert client.post(f"/api/downloads/{download_id}/remove").status_code == 204
    assert client.get("/api/downloads").json()["downloads"] == []

    assert client.post(f"/api/downloads/{download_id}/restore").status_code == 204
    assert [one["id"] for one in client.get("/api/downloads").json()["downloads"]] == [download_id]

    refused = client.post(f"/api/downloads/{download_id}/restore")
    assert refused.status_code == 404
    assert refused.json()["detail"] == "This download is not one that was removed"


def test_a_waiting_download_says_where_it_is_in_the_line(client: TestClient, app: FastAPI) -> None:
    """The number the row draws as "next in line" or "N ahead", from the wire and never counted in
    the browser: the queue's order is the queue's, and a client counting rows on a page would be
    answering about the page."""
    sign_in(client, "admin")
    # The workers start as soon as the app is ready and the fixture's handler finishes immediately, so
    # the three would be done before the list is read: the pool is stopped first, on the app's loop.
    portal = client.portal
    assert portal is not None
    portal.call(part_of_app(app, wiring.POOL).stop)
    for index in range(3):
        client.post("/api/downloads", json={"url": f"https://www.tiktok.com/@a/video/{index}"})

    rows = client.get("/api/downloads").json()["downloads"]
    # Newest first on the screen, so the oldest of the three is the one at the front of the line.
    assert [one["position"] for one in rows] == [3, 2, 1]


def test_a_settled_row_has_no_place_in_the_line(client: TestClient) -> None:
    """The known negative. Without it the test above passes on a number that is always there, and
    a finished download drawn as "4th in line" is a fact about nothing."""
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]
    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204

    assert client.get("/api/downloads").json()["downloads"][0]["position"] is None


def test_an_admin_can_fetch_a_skipped_link_anyway(client: TestClient) -> None:
    """The route is driven rather than merely declared, and its idempotence is the same as cancel's.

    An id naming no row is a success that does nothing: answering differently would turn this into
    a way of asking which download ids exist.
    """
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]
    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204

    assert client.post(f"/api/downloads/{download_id}/anyway").status_code == 204
    assert client.get("/api/downloads").json()["downloads"][0]["status"] == "queued"

    assert client.post("/api/downloads/01HX0000000000000000000009/anyway").status_code == 204


def test_a_site_that_is_not_taken_whole_is_refused_before_anything_is_asked(
    client: TestClient,
) -> None:
    """The refusal happens without a single request to the site, which is the point. Being asked
    for everything a creator has posted is what gets an account blocked."""
    sign_in(client, "admin")
    refused = client.post(
        "/api/downloads/bulk-preview", json={"url": "https://www.instagram.com/someone/"}
    )
    assert refused.status_code == 400
    assert "Instagram" in refused.json()["detail"]


def test_a_playlist_is_counted_first_and_queued_one_download_per_item(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two steps on purpose. Taking everything a creator has posted is a decision, and the number
    is what somebody makes it with, so the count costs one listing request and no media, and
    nothing is queued until they say yes."""
    found = ["https://youtu.be/one", "https://youtu.be/two", "https://youtu.be/three"]

    async def fake_listing(_url: str, **_kwargs: object) -> list[str]:
        return found

    monkeypatch.setattr("sift.slices.download.bulk.find_items", fake_listing)
    sign_in(client, "admin")
    playlist = {"url": "https://www.youtube.com/playlist?list=PL123"}

    preview = client.post("/api/downloads/bulk-preview", json=playlist)
    assert preview.status_code == 200
    # The limit rides on the answer rather than being spelled out in the browser: the sentence the
    # screen draws is about what is going to happen here, and a copy of the number in the client is
    # one that goes stale silently the day this one moves.
    assert preview.json() == {
        "site": "YouTube",
        "count": 3,
        "truncated": False,
        "limit": bulk.MAX_ITEMS,
    }
    assert client.get("/api/downloads").json()["total"] == 0  # counting queues nothing

    queued = client.post("/api/downloads/bulk", json=playlist)
    assert queued.status_code == 201
    assert queued.json()["queued"] == 3
    # One row each, so a failure is one video rather than all of them.
    assert client.get("/api/downloads").json()["total"] == 3


def test_a_playlist_that_cannot_be_read_is_refused_rather_than_queued_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuses(_url: str, **_kwargs: object) -> list[str]:
        raise bulk.BulkRefused("There is nothing in that playlist or channel to download.")

    monkeypatch.setattr("sift.slices.download.bulk.find_items", refuses)
    sign_in(client, "admin")
    playlist = {"url": "https://www.youtube.com/playlist?list=PL123"}

    assert client.post("/api/downloads/bulk-preview", json=playlist).status_code == 400
    assert client.post("/api/downloads/bulk", json=playlist).status_code == 400
    assert client.get("/api/downloads").json()["total"] == 0


def test_asking_about_a_playlist_refuses_when_its_tunnel_is_down(client: TestClient) -> None:
    """A listing is a request to that site like any other. Going out directly because a tunnel was
    down would name the machine's own address to the one site it was routed away from."""

    class _DownTunnel:
        """A router whose tunnel is not up. Refuses when the route is taken, exactly as the real one
        does, which is the whole of fail-closed: no way out at all rather than the wrong one."""

        def route_for(self, _url: str) -> _DownTunnel:
            return self

        async def __aenter__(self) -> str | None:
            raise TunnelError("Sweden is not connected.")

        async def __aexit__(self, *_exc: object) -> None:
            return None

    sign_in(client, "admin")
    client.app.state.egress = _DownTunnel()  # type: ignore[attr-defined]
    playlist = {"url": "https://www.youtube.com/playlist?list=PL123"}

    for path in ("/api/downloads/bulk-preview", "/api/downloads/bulk"):
        refused = client.post(path, json=playlist)
        assert refused.status_code == 400
        assert "Sweden" in refused.json()["detail"]


def test_a_playlist_longer_than_one_go_says_so_and_queues_what_it_promised(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The number shown has to be the number queued. Reporting the whole list and taking fewer
    would be a decision made against a figure that is not what happens."""
    too_many = [f"https://youtu.be/{n}" for n in range(bulk.MAX_ITEMS + 7)]

    async def fake_listing(_url: str, **_kwargs: object) -> list[str]:
        return too_many

    monkeypatch.setattr("sift.slices.download.bulk.find_items", fake_listing)
    sign_in(client, "admin")
    playlist = {"url": "https://www.youtube.com/playlist?list=PL123"}

    preview = client.post("/api/downloads/bulk-preview", json=playlist).json()
    assert preview["count"] == bulk.MAX_ITEMS
    assert preview["truncated"] is True

    assert client.post("/api/downloads/bulk", json=playlist).json()["queued"] == bulk.MAX_ITEMS


# --- the rail, the row's other verbs, and what a row carries -------------------------------------


def test_the_rail_turns_for_nothing_the_paused_queue_holds_and_the_dot_goes_out_when_seen(
    client: TestClient,
) -> None:
    """The route reads the pause from the setting the Pause control writes, and the seen mark is
    on the rows, so every window's dot answers to one press."""
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]
    assert client.get("/api/downloads/glance").json()["downloading"] == 1

    paused = client.put("/api/settings", json={"values": {"download.paused": True}})
    assert paused.status_code == 204
    assert client.get("/api/downloads/glance").json()["downloading"] == 0

    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204
    assert client.post(f"/api/downloads/{download_id}/retry").status_code == 204
    database = client.app.state.database  # type: ignore[attr-defined]
    _on_the_apps_loop(client, _fail_the_row, database, download_id)
    assert client.get("/api/downloads/glance").json()["failed_unseen"] == 1

    assert client.post("/api/downloads/seen").status_code == 204
    assert client.get("/api/downloads/glance").json() == {
        "downloading": 0,
        "waiting_for_cookies": 0,
        "landed_unseen": 0,
        "failed_unseen": 0,
    }


def test_an_admin_can_try_a_failed_download_again(client: TestClient) -> None:
    """As itself, not as a second row. One link has one history, and two records of it with two
    different endings is the thing this shape exists to prevent."""
    sign_in(client, "admin")
    download_id = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}
    ).json()["id"]
    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204

    assert client.post(f"/api/downloads/{download_id}/retry").status_code == 204
    listing = client.get("/api/downloads").json()["downloads"]
    assert [one["id"] for one in listing] == [download_id]
    assert listing[0]["status"] == "queued"

    # An id naming nothing is a success that does nothing, like every other row action here.
    assert client.post("/api/downloads/01HX0000000000000000000009/retry").status_code == 204


def test_an_admin_can_move_a_waiting_download_to_the_front(client: TestClient) -> None:
    sign_in(client, "admin")
    first = client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"}).json()[
        "id"
    ]
    second = client.post(
        "/api/downloads", json={"url": "https://www.tiktok.com/@a/video/2"}
    ).json()["id"]

    assert client.post(f"/api/downloads/{second}/first").status_code == 204
    # Both still there, and the one that was not moved is untouched.
    assert {one["id"] for one in client.get("/api/downloads").json()["downloads"]} == {
        first,
        second,
    }
    # And moving something that is not waiting is a success that does nothing.
    assert client.post("/api/downloads/01HX0000000000000000000009/first").status_code == 204


def test_the_queue_reports_how_much_is_still_to_happen(client: TestClient) -> None:
    """About the whole queue rather than the page being shown: a paste of five hundred draws twenty
    rows, and "how much is left" is a question about all five hundred."""
    sign_in(client, "admin")
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})

    summary = client.get("/api/downloads").json()["summary"]
    assert summary["queued"] == 1
    assert summary["running"] == 0
    assert summary["bytes_per_second"] == 0


def test_a_row_carries_the_site_key_from_the_moment_it_is_queued(client: TestClient) -> None:
    """The mark beside a row is an address built from that key. Taken from what the download was
    filed under it would only appear once the download had finished, which is after the moment
    anybody is watching the row."""
    sign_in(client, "admin")
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})

    row = client.get("/api/downloads").json()["downloads"][0]
    assert row["status"] == "queued"
    assert row["site"] is None, "nothing is filed under a site until it has succeeded"
    # The key, which is a different question from the Site above and is answered from the ADDRESS.
    assert row["site_key"] == "tiktok"


def test_a_row_is_named_from_its_address_before_anything_has_finished(client: TestClient) -> None:
    """A site the catalog has no record of still gets a name, and it is the same one the ledger
    would eventually write.

    The `site` column is only filled in when a download succeeds, so without this every queued,
    running and failed row would read as "Link" while the address has said which site it is since
    it was pasted. The two-label ending matters here: `labels[-2]` on a `.co.uk` address is the
    country registry, so every British, Australian and Japanese address would be named `Co`.
    """
    sign_in(client, "admin")
    client.post("/api/downloads", json={"url": "https://videos.example.co.uk/watch/1"})

    row = client.get("/api/downloads").json()["downloads"][0]
    assert row["site"] is None
    assert row["site_name"] == "Example"


def test_a_signed_address_is_shown_short_and_still_links_to_the_whole_thing(
    client: TestClient,
) -> None:
    """Some hosts hand out a link that carries an expiry and a signature, regenerated every time the
    page it sits on is loaded. Eighty characters of hex that say nothing and push the filename off
    the end of the row, so the row shows the short form and links to the real one, because the
    host answers 404 to the short one."""
    sign_in(client, "admin")
    signed = "https://cdn.discordapp.com/attachments/1/2/clip.mp4?ex=a1&is=b2&hm=c3"
    client.post("/api/downloads", json={"url": signed})

    row = client.get("/api/downloads").json()["downloads"][0]
    assert row["url"] == signed, "the address that works is the one kept"
    assert row["shown_url"] == "https://cdn.discordapp.com/attachments/1/2/clip.mp4"


def test_an_ordinary_address_carries_no_second_form(client: TestClient) -> None:
    """None rather than a copy, so a row carries the short form only when there is one worth
    carrying and the screen has one thing to test rather than two strings to compare."""
    sign_in(client, "admin")
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})

    assert client.get("/api/downloads").json()["downloads"][0]["shown_url"] is None


def test_one_paste_that_produced_nothing_yet_lists_no_files(client: TestClient) -> None:
    """Asked for when a row is opened rather than with the list, because most rows produced one file
    and the list is read once a second."""
    sign_in(client, "admin")
    submitted = client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})

    answer = client.get(f"/api/downloads/{submitted.json()['id']}/files")

    assert answer.status_code == 200
    assert answer.json()["files"] == []


def test_a_paste_of_several_links_queues_them_all(client: TestClient) -> None:
    """One download each, exactly as pasting them one at a time would make."""
    sign_in(client, "admin")
    answer = client.post(
        "/api/downloads/links",
        json={
            "urls": [
                "https://www.tiktok.com/@a/video/1",
                "https://www.tiktok.com/@a/video/2",
                "https://www.tiktok.com/@a/video/3",
            ]
        },
    )
    assert answer.status_code == 201
    assert answer.json()["queued"] == 3
    assert answer.json()["refused"] == []
    ids = [item["id"] for item in client.get("/api/downloads").json()["downloads"]]
    assert len(ids) == 3


def test_one_bad_line_does_not_lose_the_others(client: TestClient) -> None:
    """The behaviour that decides whether anybody uses this rather than pasting one at a time.

    A list copied out of a page or a notes file has a stray word in it. Refusing the whole paste
    over that line throws away the thirty-nine that were fine.
    """
    sign_in(client, "admin")
    answer = client.post(
        "/api/downloads/links",
        json={
            "urls": [
                "https://www.tiktok.com/@a/video/1",
                "not a link at all",
                "ftp://example.com/x",
                "https://www.tiktok.com/@a/video/2",
            ]
        },
    )
    assert answer.status_code == 201
    body = answer.json()
    assert body["queued"] == 2
    # Named one by one, because "2 were refused" tells nobody which two.
    assert [one["url"] for one in body["refused"]] == ["not a link at all", "ftp://example.com/x"]
    assert all(one["reason"] for one in body["refused"])


@pytest.mark.parametrize(
    "line", ["this is not a link", "ftp://example.com/x", "https:example.com/x"]
)
def test_one_pasted_line_is_refused_as_a_paste_of_several_refuses_it_before_any_row(
    client: TestClient, line: str
) -> None:
    """One line that cannot be a download is refused up front by the single route, in the same
    sentence a paste of two gives it, and nothing is written down: no row reading "Queued"."""
    sign_in(client, "admin")
    several = client.post("/api/downloads/links", json={"urls": [line]}).json()["refused"]
    one = client.post("/api/downloads", json={"url": line})
    assert one.status_code == 422
    assert one.json()["detail"] == several[0]["reason"]
    assert client.get("/api/downloads").json()["total"] == 0


def test_the_pastes_choice_travels_with_it_and_the_setting_is_not_touched(
    client: TestClient,
) -> None:
    """The page decides for THIS download: both routes the paste box uses write its choice on each
    row, and the stored settings stay as they were, so flipping the switch for one link changes
    no later download."""
    sign_in(client, "admin")
    before = client.get("/api/settings").json()
    client.post(
        "/api/downloads",
        json={"url": "https://www.tiktok.com/@a/video/1", "remember": False},
    )
    client.post(
        "/api/downloads/links",
        json={
            "urls": ["https://www.tiktok.com/@a/video/2", "https://www.tiktok.com/@a/video/3"],
            "remember": True,
        },
    )
    # A drop, the extension: nothing chosen, so the job follows the setting.
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/4"})

    assert _paste_choices(client) == [0, 1, 1, None]
    assert client.get("/api/settings").json() == before


def test_a_link_with_a_scheme_and_no_website_says_which_half_is_missing(
    client: TestClient,
) -> None:
    """The second of the two shape refusals, and it says a different thing from the first.

    `https:example.com/x` (the two slashes dropped somewhere between a page and the box) has a
    scheme Sift will follow and nothing to follow it to. Told only "only http and https links can
    be downloaded" somebody reads their own line, sees `https`, and has nowhere to go from there.
    """
    sign_in(client, "admin")
    answer = client.post(
        "/api/downloads/links",
        json={"urls": ["https:example.com/x", "https://www.tiktok.com/@a/video/1"]},
    )

    assert answer.status_code == 201
    body = answer.json()
    assert body["queued"] == 1
    assert [one["url"] for one in body["refused"]] == ["https:example.com/x"]
    assert body["refused"][0]["reason"] == "That line has no website in it."


def test_the_same_link_twice_in_one_paste_is_one_download(client: TestClient) -> None:
    """Compared as it will be FETCHED, not as it was typed: two lines differing only by a tracking
    parameter are one post, and queueing both fetches it twice before the ledger sees either."""
    sign_in(client, "admin")
    answer = client.post(
        "/api/downloads/links",
        json={
            "urls": [
                "https://www.tiktok.com/@a/video/1",
                "https://www.tiktok.com/@a/video/1?utm_source=x",
                "https://www.tiktok.com/@a/video/2",
            ]
        },
    )
    assert answer.json()["queued"] == 2
    assert answer.json()["duplicates"] == 1


def test_a_paste_of_blank_lines_queues_nothing_and_complains_about_nothing(
    client: TestClient,
) -> None:
    """A pasted block ends in a newline. That is not a refusal to report at somebody."""
    sign_in(client, "admin")
    answer = client.post("/api/downloads/links", json={"urls": ["", "   ", ""]})
    assert answer.status_code == 201
    assert answer.json() == {"queued": 0, "refused": [], "duplicates": 0, "left_over": []}


def test_a_paste_longer_than_one_go_takes_what_it_can_and_hands_back_the_rest(
    client: TestClient,
) -> None:
    """The cap is not a wall: a paste over it is not refused whole by the request validator. The
    first five hundred go in and the rest come back, so what is left in the box is exactly what is
    still to do."""
    sign_in(client, "admin")
    urls = [f"https://www.tiktok.com/@a/video/{n}" for n in range(MAX_PASTED_LINKS + 3)]
    answer = client.post("/api/downloads/links", json={"urls": urls})

    assert answer.status_code == 201
    body = answer.json()
    assert body["queued"] == MAX_PASTED_LINKS
    assert body["left_over"] == urls[MAX_PASTED_LINKS:]


def test_a_link_that_cannot_be_written_down_does_not_lose_the_paste(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline of this route: one bad line never loses the rest.

    The shape check catches a line that is not an address. Anything else that goes wrong while a
    link is being written down must not escape as a 500 that says nothing about the links that
    had already gone in, which is the very failure the route exists to prevent, arriving by a
    different door.
    """
    sign_in(client, "admin")
    real = DownloadService.submit_url

    async def one_of_them_fails(self: DownloadService, *, url: str, **rest: object) -> str:
        if url.endswith("/2"):
            raise RuntimeError("the queue was busy")
        return await real(self, url=url, **rest)  # type: ignore[arg-type]

    monkeypatch.setattr(DownloadService, "submit_url", one_of_them_fails)
    answer = client.post(
        "/api/downloads/links",
        json={
            "urls": [
                "https://www.tiktok.com/@a/video/1",
                "https://www.tiktok.com/@a/video/2",
                "https://www.tiktok.com/@a/video/3",
            ]
        },
    )

    assert answer.status_code == 201
    body = answer.json()
    assert body["queued"] == 2
    assert [one["url"] for one in body["refused"]] == ["https://www.tiktok.com/@a/video/2"]


def test_a_link_aimed_at_nothing_is_refused(client: TestClient) -> None:
    """A target that is not there gets the 404 an unknown id gets, so ids teach nothing."""
    sign_in(client, "admin")

    answer = client.post(
        "/api/downloads",
        json={"url": _A_LINK, "aimed_kind": "person", "aimed_id": "01JQZZZZZZZZZZZZZZZZZZZZZZ"},
    )
    assert answer.status_code == 404


def test_the_two_halves_of_an_aim_are_refused_apart(client: TestClient) -> None:
    """A kind with no id files nothing and an id with no kind names a row without saying what kind.

    Both would fail silently minutes later inside a job, which is the whole reason they are refused
    at the door rather than ignored.
    """
    sign_in(client, "admin")

    assert (
        client.post("/api/downloads", json={"url": _A_LINK, "aimed_kind": "person"}).status_code
        == 422
    )
    assert (
        client.post("/api/downloads", json={"url": _A_LINK, "aimed_id": "abc"}).status_code == 422
    )


def test_favorites_is_a_place_and_takes_no_id(client: TestClient) -> None:
    """The one kind with no row behind it, and the one that must not be handed one.

    A caller sending an id with it meant something this cannot do (there is no "the favorites of
    that thing"), so it is told rather than quietly having the id dropped.
    """
    sign_in(client, "admin")

    assert (
        client.post("/api/downloads", json={"url": _A_LINK, "aimed_kind": "favorite"}).status_code
        == 201
    )
    assert (
        client.post(
            "/api/downloads",
            json={"url": _A_LINK, "aimed_kind": "favorite", "aimed_id": "anything"},
        ).status_code
        == 422
    )


def test_a_kind_this_version_does_not_know_is_refused_by_the_model(client: TestClient) -> None:
    """A closed set, so an unrecognised word cannot reach a ledger row nothing will ever act on."""
    sign_in(client, "admin")

    answer = client.post(
        "/api/downloads", json={"url": _A_LINK, "aimed_kind": "sideways", "aimed_id": "abc"}
    )
    assert answer.status_code == 422


@pytest.mark.parametrize("kind", ["person", "site", "collection", "photo_set", "tag", "song"])
def test_a_link_aimed_at_something_real_is_queued_with_the_aim_on_its_row(
    client: TestClient, kind: str
) -> None:
    """Every kind, and it is a parametrize rather than one case for the reason the statements
    behind the pin are one test each: each branch here calls a DIFFERENT scoped read, and one
    of them pointed at the wrong table is an aim resolved against something the caller cannot see.

    What is asserted is the row rather than the status, because a 201 is what an aim that was
    quietly dropped would also give: the whole point of resolving it here is that the job which
    files the result runs minutes later with no request and no viewer behind it.
    """
    sign_in(client, "admin")
    target = {
        "person": lambda: _made(client, "/api/people", {"name": "Nadia Vance", "vault": False}),
        "site": lambda: _made(client, "/api/sites", {"name": "Somewhere", "kind": None}),
        "collection": lambda: _made(client, "/api/collections", {"name": "Best of"}),
        "photo_set": lambda: _made(client, "/api/photo-sets", {"name": "A shoot"}),
        "tag": lambda: _made(client, "/api/tags", {"name": "beach"}),
        "song": lambda: _made(client, "/api/songs", {"name": "Blue - Marla Quist"}),
    }[kind]()

    answer = client.post(
        "/api/downloads", json={"url": _A_LINK, "aimed_kind": kind, "aimed_id": target}
    )

    assert answer.status_code == 201, answer.text
    assert _aim_on_the_ledger(client) == (kind, target)


def test_rows_in_two_folders_of_one_library_are_both_named_from_one_read_of_it(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each row names its own folder and where that is on disk, and the library the two share is
    read once for the page rather than once per folder."""
    sign_in(client, "admin")
    path = client.app.state.database.path  # type: ignore[attr-defined]
    root, top, clips = new_id(), new_id(), new_id()
    seed_root(path, root, folder_id=top, path=tmp_path)
    _write_row(
        client,
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
        (clips, root, top, "Clips", "Clips"),
    )
    into_top = _landed_in(client, "https://www.tiktok.com/@a/video/1", top)
    into_clips = _landed_in(client, "https://www.tiktok.com/@a/video/2", clips)
    library = client.app.state.library  # type: ignore[attr-defined]
    real_get_root = library.get_root
    asked: list[str] = []

    async def counted(root_id: str) -> object:
        asked.append(root_id)
        return await real_get_root(root_id)

    monkeypatch.setattr(library, "get_root", counted)

    by_id = {row["id"]: row for row in client.get("/api/downloads").json()["downloads"]}

    assert by_id[into_top]["folder"]["id"] == top
    assert Path(by_id[into_top]["folder"]["path"]) == tmp_path
    assert by_id[into_clips]["folder"]["id"] == clips
    assert Path(by_id[into_clips]["folder"]["path"]) == tmp_path / "Clips"
    assert asked == [root]


def test_a_row_whose_recorded_folder_is_gone_names_no_folder(client: TestClient) -> None:
    """The recorded folder carries no reference to the folder table, so it outlives a folder removed
    since; the row then names none rather than a folder that is not there."""
    sign_in(client, "admin")
    _landed_in(client, "https://www.tiktok.com/@a/video/1", "01HX000000000000000000GONE")

    assert client.get("/api/downloads").json()["downloads"][0]["folder"] is None


def test_a_row_whose_library_went_between_the_two_reads_names_no_folder(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder is found and its library is not: the race between the reads, answered with no
    folder rather than a path under a library that is not there."""
    sign_in(client, "admin")
    folder_id = _a_folder_in(client, tmp_path)
    _landed_in(client, "https://www.tiktok.com/@a/video/1", folder_id)

    async def gone(_root_id: str) -> None:
        return None

    monkeypatch.setattr(client.app.state.library, "get_root", gone)  # type: ignore[attr-defined]

    assert client.get("/api/downloads").json()["downloads"][0]["folder"] is None


def test_downloads_sorted_by_name_follow_the_files_the_viewer_may_see(
    client: TestClient, tmp_path: Path
) -> None:
    """A to Z by the file each row fetched, read through the access layer: the same read the row
    itself names its file from."""
    sign_in(client, "admin")
    path = client.app.state.database.path  # type: ignore[attr-defined]
    cache = client.app.state.settings.cache_dir  # type: ignore[attr-defined]
    root, folder = new_id(), new_id()
    seed_root(path, root, folder_id=folder, path=tmp_path)
    ids = {}
    for nth, filename in enumerate(("zebra.mp4", "apple.mp4", "mango.mp4"), start=1):
        asset = new_id()
        seed_asset(
            path,
            asset,
            root_id=root,
            folder_id=folder,
            root_path=tmp_path,
            cache_dir=cache,
            filename=filename,
        )
        download_id = client.post(
            "/api/downloads", json={"url": f"https://www.tiktok.com/@a/video/{nth}"}
        ).json()["id"]
        _write_row(
            client,
            "UPDATE downloads SET asset_id = ?, state = 'done' WHERE id = ?",
            (asset, download_id),
        )
        ids[download_id] = filename

    listed = client.get("/api/downloads", params={"show": "done", "sort": "name_az"}).json()

    assert [ids[row["id"]] for row in listed["downloads"]] == [
        "apple.mp4",
        "mango.mp4",
        "zebra.mp4",
    ]


# --- Remove from the list -----------------------------------------------------------------------


def test_a_download_still_going_cannot_be_removed(client: TestClient) -> None:
    """Cancel it first. A queued row made to disappear is something fetching with nowhere left to
    watch it or stop it, and the answer says the one thing that gets there."""
    sign_in(client, "admin")
    queued = client.post("/api/downloads", json={"url": _A_LINK})
    download_id = queued.json()["id"]

    refused = client.post(f"/api/downloads/{download_id}/remove")

    assert refused.status_code == 409
    assert refused.json()["detail"] == "Cancel it first"
    assert [one["id"] for one in client.get("/api/downloads").json()["downloads"]] == [download_id]


def test_a_removed_download_leaves_the_list_and_the_total(client: TestClient) -> None:
    """The row stays in the ledger and stops being drawn. The total goes with it, because it is what
    a pager divides into pages: a count that kept the row would offer a last page with nothing on
    it."""
    sign_in(client, "admin")
    download_id = client.post("/api/downloads", json={"url": _A_LINK}).json()["id"]
    assert client.post(f"/api/downloads/{download_id}/cancel").status_code == 204

    assert client.post(f"/api/downloads/{download_id}/remove").status_code == 204

    page = client.get("/api/downloads").json()
    assert page["downloads"] == []
    assert page["total"] == 0
    # Twice is a success, the same way cancelling twice is.
    assert client.post(f"/api/downloads/{download_id}/remove").status_code == 204
    # And the ledger still holds it, which is what stops the same link being fetched again.
    assert _ledger_rows(client) == 1


def test_a_row_with_nowhere_to_go_names_no_folder(client: TestClient) -> None:
    """No folder chosen and none set: the row carries none, rather than a word standing in."""
    sign_in(client, "admin")
    client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})

    assert client.get("/api/downloads").json()["downloads"][0]["folder"] is None


def test_a_row_is_named_by_the_file_on_disk_now_not_the_name_it_arrived_under(
    client: TestClient, tmp_path: Path
) -> None:
    """A file renamed on disk (by the pass that takes the tool's id off, say) is shown by its name
    now, not the arrival name the asset row keeps for ever. The list and a row's own file list both
    name the file as it is called now; the arrival name is the fallback only while no copy can be
    seen.
    """
    sign_in(client, "admin")
    submitted = client.post("/api/downloads", json={"url": "https://www.tiktok.com/@a/video/1"})
    download_id = submitted.json()["id"]
    path = client.app.state.database.path  # type: ignore[attr-defined]
    cache = client.app.state.settings.cache_dir  # type: ignore[attr-defined]
    root, folder, asset = new_id(), new_id(), new_id()
    seed_root(path, root, folder_id=folder, path=tmp_path)
    seed_asset(
        path,
        asset,
        root_id=root,
        folder_id=folder,
        root_path=tmp_path,
        cache_dir=cache,
        filename="Some_title _3c7f0b9d2e815_.mp4",
    )
    _write_row(
        client,
        "UPDATE downloads SET asset_id = ?, state = 'done' WHERE id = ?",
        (asset, download_id),
    )
    _write_row(
        client,
        "UPDATE asset_locations SET rel_path = ?, filename = ? WHERE asset_id = ?",
        ("Some_title.mp4", "Some_title.mp4", asset),
    )
    # The paste's own record of what it produced, which is what a row's file list reads.
    _write_row(
        client,
        "INSERT INTO download_items (url_hash, media_key, asset_id, created_at)"
        " SELECT url_hash, 'one', ?, 1 FROM downloads WHERE id = ?",
        (asset, download_id),
    )

    row = client.get("/api/downloads").json()["downloads"][0]
    files = client.get(f"/api/downloads/{download_id}/files").json()["files"]

    assert row["filename"] == "Some_title.mp4"
    assert files[0]["filename"] == "Some_title.mp4"
    # Every copy gone from where Sift can see: the arrival name is the only name anybody has.
    _write_row(
        client,
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?",
        (asset,),
    )
    assert client.get("/api/downloads").json()["downloads"][0]["filename"] == (
        "Some_title _3c7f0b9d2e815_.mp4"
    )
