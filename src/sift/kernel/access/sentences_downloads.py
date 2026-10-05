# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lines for a download: the file it landed, and the reason one gave up."""

from __future__ import annotations

import re
from collections.abc import Mapping

from sift.kernel.access.sentences_pieces import (
    VANTAGE_ENTITY,
    VANTAGE_PERSON,
    Line,
    Part,
    _fill,
    files,
    said,
)
from sift.kernel.text import non_empty_str

#: The kind of a thing said before its name in the feed's delete line, because a name alone can
#: lie. Public: a decision's worded line says the kind the same way (`Named.kind_said`).
KIND_BEFORE: Mapping[str, str] = {
    "asset": "the file ",
    "person": "the person ",
    "tag": "the tag ",
    "site": "the Site ",
    "collection": "the Collection ",
    "photo_set": "the Photo Set ",
    "song": "the song ",
    "saved_filter": "the saved filter ",
}

#: What a download says on a page away from its file.
_DOWNLOAD_AWAY: Mapping[str, Mapping[str, str]] = {
    "downloaded": {
        VANTAGE_PERSON: "{by} downloaded a file of theirs",
        VANTAGE_ENTITY: "{by} downloaded a file from it",
    },
    "download_failed": {
        VANTAGE_PERSON: "{by} could not download a file of theirs",
        VANTAGE_ENTITY: "{by} could not download a file from it",
    },
}

#: What a single download's file is called in the list it opens to.
A_FILE = "a file"


def downloads_folded(by: str, count: int, vantage: str) -> tuple[Line, str]:
    """A day's downloads as one line, and the phrase in it that heads the files it opens to. A
    file's own page never folds, so a vantage with no folded wording is a KeyError."""
    away = _DOWNLOAD_AWAY["downloaded"][vantage]
    if count == 1:
        return _fill(away, {"by": said(by)}), A_FILE
    counted = files(count)
    if vantage == VANTAGE_PERSON:
        return said(by, f" downloaded {counted} of theirs"), counted
    return said(by, f" downloaded {counted} from it"), counted


#: WHY A DOWNLOAD GAVE UP, after "because", by the code its writer records. `{it}` is the Site where
#: the line names one; a status code is said by `failed_because`, a code with no entry by the act.
FAILED_BECAUSE: Mapping[str, str] = {
    "wall-login": "{it} wants cookies",
    "login-failed": "{it} turned the saved cookies away",
    "cookies-refused": "{it} turned the saved cookies away",
    "wall-age": "{it} wants proof of age",
    "wall-region": "{it} does not show this in your region",
    "unsupported": "Sift cannot download from {it} yet",
    "no-answer": "{it} stopped responding",
}


def failed_because(payload: Mapping[str, object], *, site_named: bool) -> str:
    """ " because it answered 410 Gone", " because it wants cookies", or nothing where no code."""
    from http import HTTPStatus

    code = non_empty_str(payload.get("code"))
    if code is None:
        return ""
    it = "it" if site_named else "the site"
    status = re.fullmatch(r"http-(\d{3})", code)
    if status is not None:
        number = int(status.group(1))
        try:
            phrase = f" {HTTPStatus(number).phrase}"
        except ValueError:
            phrase = ""
        return f" because {it} answered {number}{phrase}"
    reason = FAILED_BECAUSE.get(code)
    return "" if reason is None else f" because {reason.format(it=it)}"


def download_line(
    by: str,
    verb: str,
    vantage: str | None,
    site: Part,
    subject: Part = None,
    payload: Mapping[str, object] | None = None,
) -> Line:
    """A download that landed or gave up, on the file, away from it, or in the feed; one that gave
    up says why where its code was recorded."""
    why = (
        failed_because(payload or {}, site_named=bool(site) or vantage == VANTAGE_ENTITY)
        if verb == "download_failed"
        else ""
    )
    if vantage in (VANTAGE_PERSON, VANTAGE_ENTITY):
        return said(_fill(_DOWNLOAD_AWAY[verb][vantage], {"by": said(by)}), why)
    what: Part = subject if vantage is None and subject is not None else "this file"
    if verb == "downloaded":
        return said(by, " downloaded ", what, " from ", site or "the web")
    if not site:
        return said(by, " could not download ", what, why)
    return said(by, " could not download ", what, " from ", site, why)


# --- what a download's failure says on the queue row ---------------------------------------------
#
# The downloader writes a CODE and this turns it into a line, so rewording one never touches what
# recognises a failure. Most codes get nothing: the stored sentence already says it, and this
# answers only for a bare status phrase or a retired term. THE WORD IS COOKIES, NEVER A LOGIN:
# `wall-login` and `login-failed` are the downloader's codes, and the line they produce says cookies.

#: The site a failure is about, when the row does not know which site it was.
A_SITE = "The site"

#: What a site says when cookies it was given come back refused: one sentence under two keys, for
#: two ways the downloader learns one conclusion.
_TURNED_THE_COOKIES_AWAY = "{site} turned the saved cookies away"

#: The codes this build has better words for, and the line each one produces. `{site}` is the
#: site's own name where the row knows it.
DOWNLOAD_SAID: Mapping[str, str] = {
    "no-answer": "{site} stopped responding, so Sift stopped waiting",
    # The site will not serve without cookies; the stored sentences name a screen that is gone.
    "wall-login": "{site} wants cookies before it will show this post",
    "login-failed": _TURNED_THE_COOKIES_AWAY,
    "cookies-refused": _TURNED_THE_COOKIES_AWAY,
    # A status says WHICH status, and nothing that is not true of a row that has given up.
    "http-401": "{site} answered 401 Unauthorized: it shows this only when signed in",
    # Two walls that want opposite things: an age gate is lifted by a jar from a signed-in browser,
    # a region block only by going out somewhere else. Each says which.
    "wall-age": "{site} wants proof of age; cookies from a signed-in browser help",
    "wall-region": "{site} does not show this here; a tunnel gets past it",
    # A verdict about SIFT, not the site: a line saying the site refused would send somebody to
    # fix a site that is not broken.
    "unsupported": "Sift cannot download from {site} yet",
    # Gone. Both statuses, because a site that has removed a post answers whichever it prefers and
    # the difference is nothing a person can act on.
    "http-404": "{site} answered 404 Not Found: the post is not there",
    "http-410": "{site} answered 410 Gone: the post has been removed",
    # Could not be reached: the site or the network in front of it, so the line says what happens
    # next. A network's own 52x statuses are not here: the downloader already says more.
    "http-408": "{site} answered 408 Request Timeout: try again later",
    "http-502": "{site} answered 502 Bad Gateway: try again later",
    "http-503": "{site} answered 503 Service Unavailable: try again later",
    "http-504": "{site} answered 504 Gateway Timeout: try again later",
    # Asked to slow down. Nothing to do and nothing wrong with the cookies, which is the whole
    # reason this one is worth saying in its own words.
    "http-429": "{site} answered 429 Too Many Requests: try again in a while",
}


def download_failed(code: str | None, site: str | None, tier: int | None = None) -> str | None:
    """What a failed download's row says, or None to keep the sentence the downloader stored.

    The table decides, and a code it has no line for answers None rather than a guess. One
    exception: a status a SITE was watched giving a meaning (stored at tier 3) is more than this
    table's line for that status, and it stands aside for it. Walls keep this table's line.
    """
    if tier == 3 and (code or "").startswith("http-"):
        return None
    said = DOWNLOAD_SAID.get(code or "")
    return None if said is None else said.format(site=site or A_SITE)


#: A STORED FAILURE WITH NO CODE in a retired sentence, matched WHOLE and said in today's words.
STALE_FAILURES: Mapping[str, str] = {
    (
        r"The download did not finish\. This is often temporary -- try it again in a little"
        r" while\."
    ): "The download did not finish. Try again in a few minutes.",
    (
        r"(?P<site>.+) needs you to be logged in\. Add a Platform Connection for it, then try the"
        r" download again\."
    ): (
        "{site} wants cookies before it will show this. Add cookies for it, then try the download"
        " again."
    ),
    (
        r"Sift cannot download from (?P<site>.+) -- it is not a site any of its downloaders"
        r" support\."
    ): "Sift cannot download from {site}. None of the downloaders Sift includes support it.",
}


def failure_today(stored: str | None) -> str | None:
    """A failed row's stored sentence in today's words, or None where it is today's already."""
    for pattern, now in STALE_FAILURES.items():
        found = re.fullmatch(pattern, (stored or "").strip())
        if found is not None:
            return now.format(**found.groupdict())
    return None
