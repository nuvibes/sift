# SPDX-License-Identifier: AGPL-3.0-or-later
"""Matching a URL's host to a site: the one way, so no two callers can do it differently.

The suffix/label-boundary match is the security-relevant part of this slice: which site's
resolver, and which stored site login, a pasted URL is routed to turns on it. A plain substring test
(`"tiktok.com" in host`) is the classic mistake, because it lets `nottiktok.com` and
`tiktok.com.attacker.example` through. So the rule lives once, here, and every resolver, the tool
registry and the cookie switch call it rather than each re-deriving a check that has to be exactly
right in nine places instead of one.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def source_host(url: str) -> str:
    """A URL's host, lowercased, or the empty string when it has none."""
    return (urlsplit(url).hostname or "").lower()


def host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    """Whether `host` is one of `suffixes` or a subdomain of one.

    `cdn.bunkr.cr` matches `bunkr.cr`; `evil-bunkr.cr` and `bunkr.cr.attacker.example` do not. The
    label boundary (the leading dot before the suffix) is what makes it a match on the host and
    not a substring of it.
    """
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


__all__ = ["host_matches", "source_host"]
