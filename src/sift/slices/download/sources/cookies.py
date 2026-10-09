# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading pasted Cookies as they are saved, and describing them without showing them.

Read here because the tools misreport a malformed jar as a sign-in problem."""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
import warnings
from dataclasses import dataclass
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path

#: One of the tools refuses a file without it.
NETSCAPE_HEADER = "# Netscape HTTP Cookie File"

_PAIR = re.compile(r"^\s*([^=;\s]+)=([^;]*)\s*$")

#: The header form carries no expiry; a year is a real date that will not expire a working jar.
_ASSUMED_LIFETIME_SECONDS = 365 * 24 * 60 * 60


class CookieInvalid(Exception):
    """Pasted cookies could not be understood; the message says what was expected."""


@dataclass(frozen=True, slots=True)
class CookieSummary:
    """What was understood about a jar. Derived facts only, never any part of the cookies."""

    count: int
    domains: tuple[str, ...]
    #: Never reported alone: the soonest expiry is usually a half-hour clearance cookie.
    expires_at: int | None
    expires_last: int | None
    expired: bool


async def understand(text: str, *, domain: str | None = None) -> tuple[str, CookieSummary]:
    """Turn pasted text into a cookies file the tools read, and describe it."""
    return await asyncio.to_thread(_understand, text, domain)


def _understand(text: str, domain: str | None) -> tuple[str, CookieSummary]:
    body = text.strip()
    if not body:
        raise CookieInvalid("There is nothing here to save.")
    if _looks_like_a_header_string(body):
        body = _from_header_string(body, domain)
    elif not body.startswith("#"):
        body = f"{NETSCAPE_HEADER}\n{body}"
    return body, _describe(body)


def _looks_like_a_header_string(body: str) -> bool:
    """Whether this is the one-line `name=value; name=value` form; an exported file has tabs."""
    if "\t" in body or "\n" in body:
        return False
    return any(_PAIR.match(part) for part in body.split(";"))


def _from_header_string(body: str, domain: str | None) -> str:
    """Convert `name=value; name=value` into an exported file for one known site."""
    if not domain:
        raise CookieInvalid(
            "That looks like the short form a browser's developer tools give you, which does not "
            "say which site it is for. Sift does not recognize this site, so it cannot work it "
            "out \u2014 export a cookies file from the site instead."
        )
    pairs = [match.groups() for part in body.split(";") if (match := _PAIR.match(part))]
    expiry = int(time.time()) + _ASSUMED_LIFETIME_SECONDS
    lines = [NETSCAPE_HEADER]
    lines += [
        "\t".join([f".{domain}", "TRUE", "/", "TRUE", str(expiry), name, value])
        for name, value in pairs
    ]
    return "\n".join(lines) + "\n"


def _describe(body: str) -> CookieSummary:
    return _summarize(_read(body))


def _read(body: str) -> list[Cookie]:
    """Every cookie in an exported file, the expired ones included."""
    with tempfile.TemporaryDirectory(prefix="sift-cookie-") as workspace:
        path = Path(workspace) / "cookies.txt"
        path.write_text(body, encoding="utf-8")
        jar = MozillaCookieJar(str(path))
        try:
            # A bad line only warns, so emptiness is checked below instead.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # Otherwise a dead jar reads back as a smaller, healthy one.
                jar.load(ignore_discard=True, ignore_expires=True)
        except Exception as exc:
            raise CookieInvalid(_expected_message()) from exc
        cookies = list(jar)
    if not cookies:
        raise CookieInvalid(_expected_message())
    return cookies


def _summarize(cookies: list[Cookie]) -> CookieSummary:
    expiries = sorted(cookie.expires for cookie in cookies if cookie.expires)
    now = int(time.time())
    return CookieSummary(
        count=len(cookies),
        domains=tuple(sorted({cookie.domain.lstrip(".") for cookie in cookies if cookie.domain})),
        expires_at=expiries[0] if expiries else None,
        expires_last=expiries[-1] if expiries else None,
        expired=bool(expiries) and expiries[-1] <= now,
    )


def header_for(body: str, host: str) -> str:
    """The `Cookie:` header for one host, matched on a label boundary, expired cookies kept."""
    wanted = host.lower().lstrip(".")
    pairs = []
    for cookie in _read(body):
        domain = (cookie.domain or "").lower().lstrip(".")
        if domain and (wanted == domain or wanted.endswith("." + domain)):
            pairs.append(cookie.name + "=" + (cookie.value or ""))
    return "; ".join(pairs)


def _expected_message() -> str:
    return (
        "Sift could not read that as cookies. Paste the contents of a cookies.txt file exported "
        "from your browser, or the short name=value; name=value line your browser's developer "
        "tools show you."
    )


__all__ = ["NETSCAPE_HEADER", "CookieInvalid", "CookieSummary", "header_for", "understand"]
