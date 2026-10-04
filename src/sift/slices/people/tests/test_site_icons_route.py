# SPDX-License-Identifier: AGPL-3.0-or-later
"""The site logos over the wire: a Site with no picture is drawn as its site's logo, and the icon
address cannot be shadowed by a Site's own routes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel import site_icons
from sift.kernel.access.viewer import Viewer
from sift.slices.people.router import router as people_router
from sift.slices.people.service import PeopleService, Site
from sift.slices.people.tests.conftest import sign_in
from sift.testing.auth import TEST_PIN

A_SITE = "Quillhouse"
A_HOST = "quillhouse.example"
A_SLUG = "quillhouse"
A_PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A pack of one invented site, in place of the real one. Cleared afterwards by the readers."""
    icons = tmp_path / "pack" / "icons"
    icons.mkdir(parents=True)
    (icons / f"{A_SLUG}.png").write_bytes(A_PNG)
    (tmp_path / "pack" / "manifest.json").write_text(
        json.dumps({"icons": [{"slug": A_SLUG, "name": A_SITE, "hosts": [A_HOST]}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(site_icons, "MANIFEST", tmp_path / "pack" / "manifest.json")
    monkeypatch.setattr(site_icons, "ICONS_DIR", icons)
    for reader in (
        site_icons.every,
        site_icons._by_host,
        site_icons._by_name,
        site_icons._slugs,
        site_icons._by_slug,
    ):
        reader.cache_clear()
    yield tmp_path / "pack"
    for reader in (
        site_icons.every,
        site_icons._by_host,
        site_icons._by_name,
        site_icons._slugs,
        site_icons._by_slug,
    ):
        reader.cache_clear()


def _make_site(client: TestClient, name: str) -> str:
    response = client.post("/api/sites", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_an_icon_in_the_pack_is_served(client: TestClient, pack: Path) -> None:
    sign_in(client)

    answer = client.get(f"/api/sites/icons/{A_SLUG}")

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "image/png"
    assert answer.content == A_PNG


def test_an_icon_the_pack_does_not_hold_is_a_404(client: TestClient, pack: Path) -> None:
    sign_in(client)

    assert client.get("/api/sites/icons/nobody").status_code == 404
    assert client.get("/api/sites/icons/..%2F..%2Fmanifest").status_code == 404


def test_the_icon_address_may_be_kept_for_a_week(client: TestClient, pack: Path) -> None:
    """It names a shipped file, which changes only when Sift is upgraded, unlike a cover address,
    which comes to mean whatever picture somebody chooses next."""
    sign_in(client)

    answer = client.get(f"/api/sites/icons/{A_SLUG}")

    assert "immutable" in answer.headers["cache-control"]
    assert "private" in answer.headers["cache-control"]


def test_a_links_host_is_answered_with_its_sites_logo(client: TestClient, pack: Path) -> None:
    """A link's host, `www.` or subdomain, is answered with its site's logo; never a look-alike."""
    sign_in(client)

    for host in (A_HOST, f"www.{A_HOST}", f"en.{A_HOST}", f"a.b.{A_HOST}", A_HOST.upper()):
        answer = client.get("/api/sites/icons/for", params={"host": host})
        assert answer.status_code == 200, host
        assert answer.content == A_PNG
        assert "immutable" in answer.headers["cache-control"]

    for host in (f"evil{A_HOST}", "example", "nobody.example", "[", "quillhouse"):
        assert client.get("/api/sites/icons/for", params={"host": host}).status_code == 404, host


def test_a_links_host_the_pack_does_not_hold_is_a_miss_the_browser_may_keep(
    client: TestClient, pack: Path
) -> None:
    """A person's page draws one of these per link. A miss re-asked on every visit is a request per
    unknown site per page for an answer that only an upgrade can change, so it is kept too."""
    sign_in(client)

    miss = client.get("/api/sites/icons/for", params={"host": "nobody.example"})

    assert miss.status_code == 404
    assert "immutable" in miss.headers["cache-control"]
    assert "private" in miss.headers["cache-control"]


def test_the_host_route_is_not_taken_for_a_slug_nor_a_slug_for_it() -> None:
    """`/sites/icons/for` is declared before the slug route, and no shipped slug is `for`."""
    paths = [str(getattr(route, "path", "")) for route in people_router.routes]

    assert paths.index("/sites/icons/for") < paths.index("/sites/icons/{slug}")
    assert "for" not in {icon.slug for icon in site_icons.every()}


def test_a_site_with_no_cover_is_drawn_as_its_sites_logo(client: TestClient, pack: Path) -> None:
    """The whole point of the feature, asked the way a wall asks it. The Site is matched by
    NAME here, which is the case that carries a real library: a site made by a download has a name
    and no address at all."""
    sign_in(client)
    site_id = _make_site(client, A_SITE)

    answer = client.get(f"/api/sites/{site_id}/cover")

    assert answer.status_code == 200
    assert answer.content == A_PNG


def test_the_sites_listing_names_the_logo_and_the_cover_is_kept_under_that_name(
    client: TestClient, pack: Path
) -> None:
    """The Sites listing carries the logo token, and that address is answered keepable."""
    sign_in(client)
    site_id = _make_site(client, A_SITE)
    _make_site(client, "Marrowvale Studios")

    rows = {row["name"]: row for row in client.get("/api/sites").json()["items"]}
    ours = rows[A_SITE]

    assert ours["icon"] == site_icons.token_of(pack / "icons" / f"{A_SLUG}.png")
    assert rows["Marrowvale Studios"]["icon"] is None

    kept = client.get(f"/api/sites/{site_id}/cover", params={"v": f"{ours['art']}.{ours['icon']}"})
    assert kept.status_code == 200
    assert kept.content == A_PNG
    assert "immutable" in kept.headers["cache-control"]

    bare = client.get(f"/api/sites/{site_id}/cover", params={"v": ours["art"]})
    assert "immutable" not in bare.headers["cache-control"]


def test_a_write_routes_view_names_the_logo_its_cover_route_serves(
    client: TestClient, pack: Path
) -> None:
    """A rename's reply asks the pack by address and name, as the listing does."""
    sign_in(client)
    site_id = _make_site(client, "Marrowvale Studios")
    addressed = client.put(
        f"/api/sites/{site_id}/details", json={"notes": None, "links": [f"https://{A_HOST}/"]}
    )
    assert addressed.status_code in (200, 204), addressed.text

    renamed = client.put(f"/api/sites/{site_id}", json={"name": "Marrowvale Studio"})
    assert renamed.status_code == 200, renamed.text

    token = site_icons.token_of(pack / "icons" / f"{A_SLUG}.png")
    assert renamed.json()["icon"] == token
    listed = {row["id"]: row for row in client.get("/api/sites").json()["items"]}[site_id]
    assert listed["icon"] == token
    kept = client.get(f"/api/sites/{site_id}/cover", params={"v": f"{listed['art']}.{token}"})
    assert kept.content == A_PNG
    assert "immutable" in kept.headers["cache-control"]


def test_a_create_and_a_rename_answer_with_the_walls_own_row(
    client: TestClient, pack: Path
) -> None:
    """Create and rename answer with the wall's own row."""
    sign_in(client)
    made = client.post("/api/sites", json={"name": A_SITE})
    assert made.status_code == 201, made.text
    site_id = made.json()["id"]
    listed = {row["id"]: row for row in client.get("/api/sites").json()["items"]}[site_id]
    assert made.json() == listed
    assert made.json()["art"] is not None
    assert made.json()["icon"] == site_icons.token_of(pack / "icons" / f"{A_SLUG}.png")

    renamed = client.put(f"/api/sites/{site_id}", json={"name": f"{A_SITE} Studio"})
    assert renamed.status_code == 200, renamed.text
    listed = {row["id"]: row for row in client.get("/api/sites").json()["items"]}[site_id]
    # The rename's reply is the wall's row plus the site's record, as the one-site read carries it:
    # a parent saved in the same write is a reference whose id only the server knows.
    assert {**renamed.json(), "record": None} == listed
    assert renamed.json()["record"] == client.get(f"/api/sites/{site_id}").json()["record"]
    assert renamed.json()["name"] == f"{A_SITE} Studio"
    assert renamed.json()["art"] is not None


def test_a_rename_of_a_site_hidden_behind_a_locked_vault_is_the_404_an_unknown_id_gets(
    client: TestClient, pack: Path
) -> None:
    """The rename resolves the site through the scoped read before it writes, like every site write.

    Reading the table would let a site this user hid behind a locked Hidden section be renamed
    (and answered with the thin view) while the hide route refused the same id.
    """
    sign_in(client)
    site_id = _make_site(client, A_SITE)
    assert client.put(f"/api/sites/{site_id}/vault", json={"vault": True}).status_code == 204

    renamed = client.put(f"/api/sites/{site_id}", json={"name": f"{A_SITE} Studio"})

    assert renamed.status_code == 404, renamed.text
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    assert client.get(f"/api/sites/{site_id}").json()["name"] == A_SITE, "nothing was written"


def test_a_site_hidden_between_the_rename_and_its_reply_answers_the_thin_view(
    client: TestClient, pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A site hidden between the rename and its reply answers the thin view, never a 404."""
    sign_in(client)
    site_id = _make_site(client, A_SITE)
    rename = PeopleService.update_site

    async def rename_then_hide(
        self: PeopleService, viewer: Viewer, site_id: str, name: str
    ) -> Site:
        renamed = await rename(self, viewer, site_id, name)
        assert await self.set_site_vault(viewer, site_id, vault=True)
        return renamed

    monkeypatch.setattr(PeopleService, "update_site", rename_then_hide)

    renamed = client.put(f"/api/sites/{site_id}", json={"name": f"{A_SITE} Studio"})

    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["id"] == site_id
    assert body["name"] == f"{A_SITE} Studio"
    assert body["art"] is None
    assert body["cover_asset_id"] is None
    assert body["cover_upload_id"] is None
    assert client.get(f"/api/sites/{site_id}").status_code == 404, "the double really hid it"


def test_a_site_the_pack_has_never_heard_of_is_still_a_404(client: TestClient, pack: Path) -> None:
    """A library holds whatever it holds and the pack is a few hundred sites, so this is the common
    answer, and it has to be exactly what it was before the pack existed, or every screen's
    fallback to a letter stops working."""
    sign_in(client)
    site_id = _make_site(client, "Marrowvale Studios")

    assert client.get(f"/api/sites/{site_id}/cover").status_code == 404


def test_no_slug_in_the_shipped_pack_is_swallowed_by_a_sites_own_route() -> None:
    """No slug in the shipped pack is swallowed by a Site's own route, asked of the router."""
    reserved = {
        str(getattr(route, "path", "")).rsplit("/", 1)[-1]
        for route in people_router.routes
        if str(getattr(route, "path", "")).startswith("/sites/{site_id}/")
    }

    assert "cover" in reserved, "the site routes moved: this check is reading nothing"
    clashing = sorted(icon.slug for icon in site_icons.every() if icon.slug in reserved)
    assert clashing == [], f"these icon slugs can never be reached: {clashing}"
