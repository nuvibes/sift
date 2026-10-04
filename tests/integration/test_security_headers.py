# SPDX-License-Identifier: AGPL-3.0-or-later
"""The running app returns the security headers, and CORS is locked, asserted on the wire as a
hostile client with hand-set headers sees it."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift import client as web_client
from sift.kernel.config import get_settings
from sift.main import (
    HSTS_HEADER_NAME,
    HSTS_HEADER_VALUE,
    content_security_policy,
    create_app,
    security_headers,
)

pytestmark = pytest.mark.integration


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(tmp_path, monkeypatch)


# --- the static headers ---------------------------------------------------------------------


def test_every_response_carries_the_static_security_headers(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    for name, value in security_headers().items():
        assert response.headers.get(name) == value


def test_the_csp_confines_everything_to_sifts_own_origin(client: TestClient) -> None:
    """The one header worth reading rather than counting: it is the XSS control."""
    csp = client.get("/health").headers.get("content-security-policy")
    assert csp == security_headers()["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


# --- HSTS: only over HTTPS ------------------------------------------------------------------


def test_hsts_is_sent_when_the_request_arrived_over_https(client: TestClient) -> None:
    """HSTS is sent when X-Forwarded-Proto says https, the signal that marks the cookie Secure."""
    response = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert response.headers.get(HSTS_HEADER_NAME) == HSTS_HEADER_VALUE


def test_hsts_is_absent_over_plain_http(client: TestClient) -> None:
    """HSTS is not sent over plain http: a LAN install cannot keep that promise."""
    response = client.get("/health")
    assert HSTS_HEADER_NAME not in response.headers


# --- CORS: locked by default, never a wildcard ----------------------------------------------


def test_a_cross_origin_request_is_not_answered_by_default(client: TestClient) -> None:
    """No origins configured, so no other site may read a response. The header that would grant
    that is simply not present, and it is never the wildcard that would grant it to everyone."""
    response = client.get("/health", headers={"origin": "https://somewhere-else.example"})
    allow_origin = response.headers.get("access-control-allow-origin")
    assert allow_origin != "*"
    assert allow_origin != "https://somewhere-else.example"


def test_a_preflight_from_an_unlisted_origin_is_not_granted(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "origin": "https://somewhere-else.example",
            "access-control-request-method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "*"
    assert response.headers.get("access-control-allow-origin") != "https://somewhere-else.example"


def test_a_listed_origin_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction: an origin the operator listed does get access. Proves the allowlist is
    a real control and not simply CORS turned off."""
    for client in _client(
        tmp_path, monkeypatch, SIFT_CORS_ALLOWED_ORIGINS="https://friend.example"
    ):
        response = client.get("/health", headers={"origin": "https://friend.example"})
        assert response.headers.get("access-control-allow-origin") == "https://friend.example"


# --- the policy and the client have to agree ------------------------------------------------


def test_the_header_names_the_page_that_is_on_disk_now_not_the_one_at_import(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A client rebuilt under a running server is named by the next response's policy, with no
    restart; the same server answers twice with the page replaced in between."""
    page = tmp_path / "index.html"
    page.write_text("<meta content=\"'sha256-BUILDONE'\">", encoding="utf-8")
    monkeypatch.setattr(web_client, "INDEX", page)

    first = client.get("/health").headers["content-security-policy"]
    assert "'sha256-BUILDONE'" in first

    page.write_text("<meta content=\"'sha256-BUILDTWO'\">", encoding="utf-8")
    moved = page.stat().st_mtime + 1
    os.utime(page, (moved, moved))

    second = client.get("/health").headers["content-security-policy"]
    assert "'sha256-BUILDTWO'" in second
    assert "'sha256-BUILDONE'" not in second, (
        "the header still names the previous build's script. The browser refuses the one the page "
        "actually carries and every page renders blank."
    )


def test_a_missing_client_leaves_the_header_admitting_no_inline_script(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no client on disk the policy admits no inline script: it never falls open."""
    monkeypatch.setattr(web_client, "INDEX", tmp_path / "not-built" / "index.html")

    policy = client.get("/health").headers["content-security-policy"]

    assert "script-src 'self';" in policy
    assert "sha256-" not in policy
    assert "unsafe-inline" not in policy.split("script-src")[1].split(";")[0]


def test_the_policy_names_the_inline_scripts_the_client_actually_has() -> None:
    """The policy names the hash of the inline script the client page actually has, or the browser
    refuses the app. It runs whether or not the client is built, and says which."""
    if not web_client.is_built():
        # No client, so no inline script to allow, and the policy must not be carrying a stale one.
        assert "sha256-" not in security_headers()["content-security-policy"]
        return

    page = web_client.INDEX.read_text(encoding="utf-8")
    named_in_page = set(re.findall(r"'(sha(?:256|384|512)-[A-Za-z0-9+/=]+)'", page))
    assert named_in_page, (
        "the built page names no script hashes. Either the build stopped emitting them, in which "
        "case the policy now blocks the client, or its bootstrap is no longer inline."
    )

    for digest in named_in_page:
        assert f"'{digest}'" in security_headers()["content-security-policy"], (
            f"the page runs an inline script the policy does not allow ({digest}). The browser will "
            "refuse it and the app will not start."
        )


def test_the_policy_keeps_its_shape_whatever_the_client_needs() -> None:
    """Apart from the hash the policy is fixed, so making the client work never widens it."""
    policy = content_security_policy(("sha256-anything",))

    assert "default-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'self'" in policy
    assert "unsafe-eval" not in policy
    assert "*" not in policy

    # And with nothing to allow, script-src does not fall open.
    assert "script-src 'self';" in content_security_policy(())


def test_no_inline_script_is_ever_allowed_wholesale() -> None:
    """`script-src` never allows inline scripts wholesale. `style-src` does, for the framework's
    announcer, bounded because `img-src` allows no other host."""
    for hashes in ((), ("sha256-anything",)):
        policy = content_security_policy(hashes)
        directives: dict[str, str] = {}
        for part in policy.split(";"):
            name, _, values = part.strip().partition(" ")
            if name:
                directives[name] = values

        assert "unsafe-inline" not in directives["script-src"], (
            "script-src allows any inline script. An injected one now runs, which is the thing the "
            "policy exists to stop."
        )
        assert "unsafe-inline" not in directives["default-src"], (
            "default-src allows inline. Every directive that is not written out falls back to it, "
            "so this quietly loosens the ones nobody listed."
        )


# --- a genuine 500 is not a way past the headers --------------------------------------------


def test_an_unhandled_error_still_carries_the_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unhandled error's 500 still carries the security headers and the correlation id, and leaks
    neither the path nor a traceback."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("a genuine bug mentioning /home/realname/secret.txt")

    # Moved to the front, above the client catch-all, where a real route is registered.
    app.router.routes.insert(0, app.router.routes.pop())

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    get_settings.cache_clear()

    assert response.status_code == 500
    for name, value in security_headers().items():
        assert response.headers.get(name) == value
    assert response.headers.get("x-correlation-id")
    assert "realname" not in response.text  # the detail is logged, not returned
    assert "Traceback" not in response.text


# --- the check can fail ---------------------------------------------------------------------


def test_the_header_check_would_notice_them_missing() -> None:
    """A gate that cannot fail is decoration. A bare app with none of Sift's middleware is missing
    the headers, and the same assertion the tests above make refuses it."""
    bare = FastAPI()

    @bare.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(bare) as client:
        response = client.get("/health")

    with pytest.raises(AssertionError):
        for name, value in security_headers().items():
            assert response.headers.get(name) == value
