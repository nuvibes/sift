# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serving the browser client: one route answers every address the API did not claim, turning a
path read off the network into a file on disk."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift import client
from sift.kernel.config import get_settings
from sift.main import create_app
from sift.testing.tools import POSIX_ONLY, WINDOWS_ONLY, junction

pytestmark = pytest.mark.integration

built = pytest.mark.skipif(not client.is_built(), reason="the browser client is not built")


def _write(page: Path, markup: str) -> None:
    """Write a page as a build does: new bytes at a plainly later time, since two quick same-size
    writes can share a modification time."""
    before = page.stat().st_mtime if page.exists() else 0.0
    page.write_text(markup, encoding="utf-8")
    # Past the later of the new time and the previous step, which may sit in the future.
    moved = max(page.stat().st_mtime, before) + 1
    os.utime(page, (moved, moved))


class _Counting:
    """Stands in for the built page and counts its `stat` calls and reads."""

    def __init__(self, page: Path) -> None:
        self._page = page
        self.stats = 0
        self.reads = 0

    def stat(self) -> os.stat_result:
        self.stats += 1
        return self._page.stat()

    def read_bytes(self) -> bytes:
        self.reads += 1
        return self._page.read_bytes()

    def __str__(self) -> str:
        return str(self._page)


@pytest.fixture
def http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


# --- the path is not to be trusted ----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../../etc/passwd",
        "../../../../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "/etc/passwd",
        "..\\..\\windows\\win.ini",
        "web/../../../etc/passwd",
        "\x00etc/passwd",
    ],
)
def test_no_path_reaches_a_file_outside_the_client(path: str) -> None:
    """No path a scanner sends resolves to a file outside the client: the answer is the page or
    nothing. Asserted on the function, since an HTTP client normalises some paths first."""
    asset = client.asset_for(path, client.list_files())
    if asset is None:
        return
    assert asset == client.INDEX, f"{path!r} resolved to {asset}, which is outside the client"


def test_a_traversal_attempt_over_http_is_answered_with_the_page(http: TestClient) -> None:
    """The same, through the whole stack."""
    response = http.get("/../../etc/passwd")
    assert response.status_code in (200, 503)
    assert "root:" not in response.text


# --- a checkout with no client is not a broken one ------------------------------------------


def test_a_checkout_with_no_client_says_so_and_does_not_look_like_a_refusal(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unbuilt checkout answers 503, not 404: 404 means "nothing you may see", and this public
    route must not turn anyone away."""
    monkeypatch.setattr(client, "CLIENT_DIR", tmp_path / "not-built")
    monkeypatch.setattr(client, "INDEX", tmp_path / "not-built" / "index.html")

    response = http.get("/browse")

    assert response.status_code == 503, "an unbuilt client must not answer like a refusal"
    assert "not built" in response.text
    assert "/api" in response.text  # and it says where the working half is


@built
@POSIX_ONLY
def test_a_symlink_out_of_the_client_directory_is_not_followed(tmp_path: Path) -> None:
    """A symlink out of the client directory is not followed: only the resolved path shows it."""
    escape = client.CLIENT_DIR / "escape-for-a-test.txt"
    escape.unlink(missing_ok=True)
    escape.symlink_to("/etc/passwd")
    try:
        assert client.asset_for("escape-for-a-test.txt", client.list_files()) == client.INDEX
    finally:
        escape.unlink(missing_ok=True)


@built
@WINDOWS_ONLY
def test_a_junction_out_of_the_client_directory_is_not_followed(tmp_path: Path) -> None:
    """A junction out of the client directory is not followed: it is not a symlink, so only the
    resolved path refuses it. Whatever comes back is never the file behind it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(b"not part of the client")
    escape = client.CLIENT_DIR / "escape-for-a-test"
    junction(escape, outside)
    try:
        answer = client.asset_for("escape-for-a-test/secret.txt", client.list_files())
        assert answer != secret, "the junction was followed out of the client directory"
        assert answer == client.INDEX
    finally:
        escape.rmdir()


# --- a request is a lookup, not a question put to the disk ------------------------------------


def _a_build(root: Path) -> Path:
    """A small client build: the page shell, a hashed script and a font."""
    (root / "_app" / "immutable").mkdir(parents=True)
    (root / "fonts").mkdir()
    (root / "_app" / "immutable" / "app-1a2b.js").write_bytes(b"boot()")
    (root / "fonts" / "face.woff2").write_bytes(b"wOF2")
    index = root / "index.html"
    _write(index, "<script src='/_app/immutable/app-1a2b.js'></script>")
    return index


def test_a_request_never_asks_the_disk_where_its_path_lands(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A request is a lookup in the build's listing, made off the loop; it never resolves a path,
    which reads the disk."""
    root = tmp_path / "web"
    index = _a_build(root)
    monkeypatch.setattr(client, "CLIENT_DIR", root)
    monkeypatch.setattr(client, "INDEX", index)
    client.list_files()

    def refuse(*_args: object) -> Path:
        raise AssertionError("a request resolved a path")

    monkeypatch.setattr(client, "confine", refuse)

    script = http.get("/_app/immutable/app-1a2b.js")
    assert script.status_code == 200
    assert script.content == b"boot()"
    assert script.headers["cache-control"] == client.FOREVER
    assert http.get("/fonts/face.woff2").headers["content-type"] == "font/woff2"
    for page in ("/", "/browse", "/_app/immutable/missing.js", "/fonts/../index.html"):
        answer = http.get(page)
        assert answer.status_code == 200, page
        assert answer.headers["content-type"].startswith("text/html"), page


def test_a_rebuild_is_listed_again_by_the_next_request(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rebuild under a running server is listed again by the next request."""
    root = tmp_path / "web"
    index = _a_build(root)
    monkeypatch.setattr(client, "CLIENT_DIR", root)
    monkeypatch.setattr(client, "INDEX", index)
    assert http.get("/_app/immutable/app-1a2b.js").content == b"boot()"

    (root / "_app" / "immutable" / "app-3c4d.js").write_bytes(b"boot(2)")
    _write(index, "<script src='/_app/immutable/app-3c4d.js'></script>")

    assert http.get("/_app/immutable/app-3c4d.js").content == b"boot(2)"


def test_the_listing_names_only_what_the_build_holds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The listing names files by request path, with forward slashes, inside the build only."""
    root = tmp_path / "web"
    index = _a_build(root)
    (tmp_path / "beside.txt").write_bytes(b"not part of the client")
    monkeypatch.setattr(client, "CLIENT_DIR", root)
    monkeypatch.setattr(client, "INDEX", index)

    listing = client.list_files()

    assert listing.built
    assert sorted(listing.files) == ["_app/immutable/app-1a2b.js", "fonts/face.woff2", "index.html"]
    assert client.asset_for("../beside.txt", listing) == index
    assert client.current_files() is listing


def test_what_the_walk_cannot_open_or_prove_inside_is_left_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable directory, a non-file entry and a refused file are skipped; the rest is
    listed."""
    from sift.kernel.paths import PathEscape

    root = tmp_path / "web"
    index = _a_build(root)
    (root / "locked").mkdir()
    (root / "locked" / "inside.js").write_bytes(b"never listed")
    (root / "refused.js").write_bytes(b"refused")
    (root / "a-pipe").write_bytes(b"")
    monkeypatch.setattr(client, "CLIENT_DIR", root)
    monkeypatch.setattr(client, "INDEX", index)

    real_scandir = os.scandir

    class NotAFile:
        """A directory entry for something that is neither a file nor a directory: a pipe."""

        def __init__(self, entry: os.DirEntry[str]) -> None:
            self.path = entry.path

        def is_symlink(self) -> bool:
            return False

        def is_junction(self) -> bool:
            return False

        def is_dir(self, *, follow_symlinks: bool = True) -> bool:
            return False

        def is_file(self, *, follow_symlinks: bool = True) -> bool:
            return False

    def scandir(directory: Path) -> list[object]:
        if Path(directory).name == "locked":
            raise PermissionError("access is denied")
        return [
            NotAFile(entry) if entry.name == "a-pipe" else entry
            for entry in real_scandir(directory)
        ]

    from sift.kernel.paths import confine as real_confine

    def confine(base: Path, path: Path) -> Path:
        if path.name == "refused.js":
            raise PathEscape("lands outside the client")
        return real_confine(base, path)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(client, "confine", confine)

    listing = client.list_files()

    assert sorted(listing.files) == ["_app/immutable/app-1a2b.js", "fonts/face.woff2", "index.html"]


# --- what the route is for ------------------------------------------------------------------


@built
def test_the_front_door_serves_the_client(http: TestClient) -> None:
    response = http.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@built
@pytest.mark.parametrize("path", ["/browse", "/asset/01HX", "/settings/playback", "/library/01HX"])
def test_a_deep_link_opened_cold_gets_the_app(http: TestClient, path: str) -> None:
    """A deep link opened cold gets the app: these are the client's addresses."""
    response = http.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@built
def test_a_real_file_is_served_as_itself(http: TestClient) -> None:
    """A real file is served as itself."""
    response = http.get("/fonts/material-symbols-rounded-subset.woff2")
    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"
    assert len(response.content) > 0


# --- how long a browser may keep it ----------------------------------------------------------


@built
def test_the_page_shell_is_fetched_on_every_visit(http: TestClient) -> None:
    """The page shell is `no-store` at every address the client routes, so neither a cache's guess
    nor a restored tab's history load serves the previous release."""
    for path in ("/", "/browse", "/settings/privacy", "/index.html"):
        response = http.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-store", path


@built
def test_a_file_outside_the_hashed_directory_is_checked_on_every_visit(http: TestClient) -> None:
    """A file outside the hashed directory is kept and revalidated by its tag."""
    response = http.get("/_app/version.json")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_a_file_that_carries_its_own_hash_is_kept_forever(http: TestClient) -> None:
    """A file with its content hash in its name is kept forever."""
    built = client.CLIENT_DIR / "_app" / "immutable"
    if not built.is_dir():  # pragma: no cover (a checkout with no client build)
        pytest.skip("the client is not built")
    hashed = next((f for f in built.rglob("*") if f.is_file()), None)
    assert hashed is not None, "the immutable directory has no files in it"

    response = http.get(f"/_app/immutable/{hashed.relative_to(built).as_posix()}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == client.FOREVER


def test_the_brand_icons_are_kept_a_week_and_nothing_else_is() -> None:
    """The icons asked for on every step through files are kept without asking; an unhashed file
    anywhere else is still checked, and the page shell never kept."""
    icon = client.CLIENT_DIR / "brand" / "favicon.svg"
    assert client.cache_control_for("brand/favicon.svg", icon) == client.BRAND
    assert "max-age=604800" in client.BRAND
    other = client.CLIENT_DIR / "fonts" / "brand.woff2"
    assert client.cache_control_for("fonts/brand.woff2", other) == client.REVALIDATE
    assert client.cache_control_for("brand", client.INDEX) == "no-store"


@built
def test_a_brand_icon_is_served_with_its_week(http: TestClient) -> None:
    """Served, the icon carries the week; the page shell beside it does not."""
    response = http.get("/brand/favicon.svg")
    assert response.status_code == 200
    assert response.headers["cache-control"] == client.BRAND
    assert http.get("/").headers["cache-control"] == "no-store"


# --- the API is not the client --------------------------------------------------------------


def test_an_unknown_api_path_gets_an_api_answer(http: TestClient) -> None:
    """An unknown API path gets an API 404, not the client's HTML."""
    response = http.get("/api/nonsense")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_the_api_still_answers_for_itself(http: TestClient) -> None:
    """The fallback sits last, so it never shadows the API."""
    assert http.get("/health").status_code == 200
    assert http.post("/api/auth/login", json={}).status_code != 200
    assert http.get("/api/auth/me").status_code == 401


def test_an_unbuilt_client_names_no_script_for_the_policy_to_allow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unbuilt client names no inline script for the policy, rather than failing the boot."""
    monkeypatch.setattr(client, "INDEX", tmp_path / "not-built" / "index.html")

    assert client.script_hashes() == ()


def test_an_unbuilt_client_has_no_build_to_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unbuilt client's build id is empty, so the client never offers a reload."""
    monkeypatch.setattr(client, "INDEX", tmp_path / "not-built" / "index.html")

    assert client.build_id() == ""


def test_the_build_id_changes_with_the_page_shell_and_not_otherwise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The build id changes with the page shell and not otherwise."""
    index = tmp_path / "index.html"
    _write(index, "<script src='/app-aaaa.js'></script>")
    monkeypatch.setattr(client, "INDEX", index)

    first = client.build_id()
    assert client.build_id() == first, "the same bytes answered two different ids"

    _write(index, "<script src='/app-bbbb.js'></script>")
    second = client.build_id()

    assert first and second
    assert second != first
    assert len(first) == 16, "a blake2b digest of eight bytes, written as hex"


# --- the page is read again when the page changes -------------------------------------------


def test_a_client_rebuilt_under_a_running_server_is_named_by_the_next_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A client rebuilt under a running server is named by the next response, so the policy names
    the new page's inline script."""
    index = tmp_path / "index.html"
    _write(index, "<meta content=\"'sha256-FIRSTBUILD'\"><script>boot(1)</script>")
    monkeypatch.setattr(client, "INDEX", index)

    assert client.script_hashes() == ("sha256-FIRSTBUILD",)
    before = client.build_id()

    _write(index, "<meta content=\"'sha256-SECONDBUILD'\"><script>boot(2)</script>")

    assert client.script_hashes() == ("sha256-SECONDBUILD",)
    assert client.build_id() != before


def test_a_rebuild_of_exactly_the_same_size_is_seen_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rebuild of the same size is seen too, since the write time moves."""
    index = tmp_path / "index.html"
    _write(index, "<meta content=\"'sha256-AAAAAAAAAA'\">")
    monkeypatch.setattr(client, "INDEX", index)

    assert client.script_hashes() == ("sha256-AAAAAAAAAA",)

    replacement = "<meta content=\"'sha256-BBBBBBBBBB'\">"
    _write(index, replacement)
    assert index.stat().st_size == len(replacement), "the test no longer makes its own point"

    assert client.script_hashes() == ("sha256-BBBBBBBBBB",)


def test_a_page_that_stands_still_costs_one_stat_and_no_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A page that stands still costs one `stat` per response and no read."""
    index = tmp_path / "index.html"
    _write(index, "<meta content=\"'sha256-COUNTED'\">")
    counting = _Counting(index)
    monkeypatch.setattr(client, "INDEX", counting)

    assert client.script_hashes() == ("sha256-COUNTED",)
    assert (counting.stats, counting.reads) == (1, 1), "the first reading is one stat and one read"

    for _ in range(10):
        assert client.script_hashes() == ("sha256-COUNTED",)

    assert counting.reads == 1, "the page was read again although nothing had changed"
    assert counting.stats == 11, "a settled page costs exactly one stat per call"


def test_a_page_that_cannot_be_read_names_no_inline_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable page names no inline script, and nothing is remembered, so the next request
    reads it again."""
    index = tmp_path / "index.html"
    index.write_bytes(b"\xff\xfe\x00 not utf-8 at all")
    monkeypatch.setattr(client, "INDEX", index)

    assert client.script_hashes() == ()
    assert client.build_id() == ""

    _write(index, "<meta content=\"'sha256-READABLE'\">")
    assert client.script_hashes() == ("sha256-READABLE",)
