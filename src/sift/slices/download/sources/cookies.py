# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading exported cookies before they are stored, and saying what was understood without showing them.

The downloader tools want a cookies file in the Netscape format that browser extensions export.
What a person actually has to hand varies: the exported file itself, its contents pasted into a box,
or the `name=value; name=value` string the browser's own developer tools give out. Only the first
two are the format the tools read.

**And the tools are no help at all when it is wrong.** Handed the header form, one stops with a hard
error about the format while the other shrugs and carries on signed out; handed nothing at all, one
of them proceeds silently as though no cookies had been asked for. Both then report a sign-in
problem, when the real fault was a malformed or missing file, so the message that reaches a person
sends them to export their cookies again when the ones they had were fine.

So the cookies are read HERE, at the moment they are saved, where somebody is looking at the box
they just filled in. A header string is converted rather than refused: it is what most people will
reach for first, and turning it into the real format is a few lines. Anything that is neither is
refused with a sentence saying what was expected.

What comes back with it is a description, never the cookies: how many there are, which sites they
are for, and when the first one runs out. That is enough to tell an export that was saved wrong from
one that was saved right, without any part of it being rendered back to a screen.

Sift never holds an account name or a password for a site. A jar of cookies is the whole of what it
is given, which is why every word on every surface says cookies: a screen that said "login" would
send people looking for a sign-in form that does not exist.

Two traps, both real, both handled here:

* a session cookie has no expiry at all, so anything said about dates has to be said only about the
  cookies that have one: a jar made entirely of session cookies gets a count and nothing more;
* the standard parser DROPS expired cookies as it reads, so a dead jar parses as a smaller healthy
  one. It is read with that behaviour turned off, and the expiry is judged here.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
import warnings
from dataclasses import dataclass
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path

#: The first line every exported cookies file carries. The tools look for it, and one of them
#: refuses outright without it.
NETSCAPE_HEADER = "# Netscape HTTP Cookie File"

#: One `name=value` pair, as a browser's own tools hand it out. A value may hold anything except the
#: separator, which is what makes the header form ambiguous in general and readable in practice.
_PAIR = re.compile(r"^\s*([^=;\s]+)=([^;]*)\s*$")

#: How long a converted header string's cookies are treated as good for. The header form carries no
#: expiry (the browser knows them and does not hand them over), so one has to be chosen, and a
#: year is long enough not to expire a working jar early while still being a real date rather than
#: a claim that it never expires.
_ASSUMED_LIFETIME_SECONDS = 365 * 24 * 60 * 60


class CookieInvalid(Exception):
    """Pasted cookies could not be understood. The message is shown to a person, so it says what
    was expected rather than what failed."""


@dataclass(frozen=True, slots=True)
class CookieSummary:
    """What was understood about a jar. Derived facts only, never any part of the cookies."""

    count: int
    domains: tuple[str, ...]
    #: When the first cookie that HAS an expiry runs out. None when none of them carries one, which
    #: is an ordinary session jar and not a fault.
    #:
    #: On its own this number MISREPRESENTS a jar, which is why the one below exists. A site
    #: behind a challenge issues a clearance cookie good for half an hour beside a session cookie
    #: good for a year, so the soonest expiry in the jar is nearly always the throwaway, and the
    #: site reissues that one on the next request. Reported alone it says a healthy jar is dying
    #: today, every time, on every site with a challenge in front of it.
    expires_at: int | None
    #: When the LAST cookie that has an expiry runs out: the point past which nothing in the jar is
    #: any use. `expired` below is this one having passed, so the two agree by construction rather
    #: than by two readings of the same list disagreeing about which end matters.
    expires_last: int | None
    #: Whether every cookie that carries an expiry has already passed it. A jar that is already
    #: dead is worth saying so at the moment it is pasted rather than at the next download.
    expired: bool


async def understand(text: str, *, domain: str | None = None) -> tuple[str, CookieSummary]:
    """Turn what somebody pasted into a cookies file the tools can read, and describe it.

    Returns the file's contents and the description. Raises `CookieInvalid` if it is neither an
    exported file nor a header string. `domain` is the site the cookies are for, needed only to
    convert a header string: that form says nothing about which site it belongs to.

    Reading a cookie file means writing one and letting the standard parser read it back, which
    touches the disk, so the whole of it happens off the loop.
    """
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
    """Whether this is the `name=value; name=value` form rather than an exported file.

    Told apart by shape, not by guessing: an exported file is one cookie per line with tab-separated
    fields, and this form is a single line of semicolon-separated pairs. A file that happens to hold
    one line still has tabs in it.
    """
    if "\t" in body or "\n" in body:
        return False
    return any(_PAIR.match(part) for part in body.split(";"))


def _from_header_string(body: str, domain: str | None) -> str:
    """Convert `name=value; name=value` into an exported file for one site.

    Refused rather than guessed when the site is not known: a cookie file says which site each
    cookie belongs to, and inventing that would produce a file the tools read happily and send to
    nobody.
    """
    if not domain:
        raise CookieInvalid(
            "That looks like the short form a browser's developer tools give you, which does not "
            "say which site it is for. Sift does not recognize this site, so it cannot work it "
            "out \u2014 export a cookies file from the site instead."
        )
    # At least one pair by construction: this is only reached when the shape check above found one,
    # and that check is the same expression. A second guard here would be a branch no input can
    # take, which is worse than none: it reads as a case somebody has thought about.
    pairs = [match.groups() for part in body.split(";") if (match := _PAIR.match(part))]
    expiry = int(time.time()) + _ASSUMED_LIFETIME_SECONDS
    lines = [NETSCAPE_HEADER]
    lines += [
        # domain, whether every subdomain gets it, path, secure-only, expiry, name, value: the
        # seven tab-separated fields of the format, in order.
        "\t".join([f".{domain}", "TRUE", "/", "TRUE", str(expiry), name, value])
        for name, value in pairs
    ]
    return "\n".join(lines) + "\n"


def _describe(body: str) -> CookieSummary:
    """Read a cookies file back with the standard parser and report what is in it."""
    return _summarize(_read(body))


def _read(body: str) -> list[Cookie]:
    """Every cookie in an exported file, the expired ones included.

    Raises `CookieInvalid` when it is not one, which is the same refusal whether the caller wanted
    a description of the jar or a header built from it: one reading, so the two cannot disagree
    about what counts as readable.
    """
    with tempfile.TemporaryDirectory(prefix="sift-cookie-") as workspace:
        path = Path(workspace) / "cookies.txt"
        path.write_text(body, encoding="utf-8")
        jar = MozillaCookieJar(str(path))
        try:
            # The parser reports a line it cannot read by WARNING and carrying on, so a file of
            # nonsense loads successfully with nothing in it, which is why the emptiness below is
            # checked rather than trusted to raise. The warning itself is noise here: this is a
            # person pasting the wrong thing into a box, which is answered with a sentence.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # Without `ignore_expires` the parser silently drops anything already expired, so a
                # jar that is entirely dead reads back as a smaller, healthy one, and the
                # person is told nothing at all.
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
        # Only ever said about the cookies that carry a date. A jar made of session cookies has
        # nothing to judge, and calling that expired would be inventing a fact.
        expired=bool(expiries) and expiries[-1] <= now,
    )


def header_for(body: str, host: str) -> str:
    """The `Cookie:` header value for one host, built from an exported file.

    The tools take a FILE and an ordinary request takes a HEADER, and the one place that knows how
    to read the file is this module, so the check that asks a site whether it still accepts what
    is saved asks here rather than teaching a second module the format.

    Matched on a label boundary, the way the site catalog matches a hostname: a cookie kept for
    `example.com` is sent to `www.example.com`, and a look-alike domain that merely ENDS in the
    same letters gets nothing. A cookie with no domain at all is skipped rather than sent to
    everybody.

    Expired cookies are kept. They are part of what was saved, and the whole point of the request
    is to let the SITE say whether what is saved still works: dropping them here would answer the
    question before asking it, with the most optimistic possible reading of the jar.

    Empty when nothing in the jar belongs to this host, which the caller reads as "there is nothing
    to ask with" rather than sending a request that proves only that the site serves its home page.
    """
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
