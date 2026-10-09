# SPDX-License-Identifier: AGPL-3.0-or-later
"""What went wrong with a download, in three tiers.

Tier 1 is the status code and its standard phrase; tier 2 what HTTP has no code for; tier 3
a Site's own walls, recognised from the tool's words per Site where somebody tested them.
Every sentence names the Site and the code; what a code means on one Site is on its record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus

from sift.slices.download.sources.registry import classify as attribute
from sift.slices.download.sources.sites.catalog import Meaning, SiteRecord, Wall, match

#: Statuses whose standard phrase does not help, or that no standard names.
NAMED_CODES: dict[int, str] = {
    419: "the page's session expired before the request was made",
    509: "the Site has used up its bandwidth allowance",
    520: "the Site behind the network returned something the network could not read",
    521: "the Site behind the network is down; try again later",
    522: "the Site behind the network did not answer in time; try again later",
    523: "the Site behind the network could not be reached at all",
    524: "the Site behind the network took too long to answer; try again later",
    525: "the secure connection to the Site could not be set up",
    526: "the Site's security certificate could not be verified",
    527: "the connection was interrupted between the network and the Site",
    530: "the Site refused this connection outright",
    999: "the Site refused without giving a reason, which Sites use for automated access",
}

#: What a standard code means for a download; not 403, which means different things
#: on different Sites. "Try again" only where it is true.
STATUS_MEANS: dict[int, str] = {
    401: "the Site shows this only when you are signed in",
    404: "there is nothing at that address; it was removed, or never existed",
    408: "the request timed out; try again later",
    410: "the post has been removed permanently",
    429: "the Site is limiting how many requests it accepts; try again in a few minutes",
    451: "the Site withholds this where the request came from, for legal reasons",
    500: "the Site had an error of its own; try again later",
    502: "the Site is having trouble; try again later",
    503: "the Site is having trouble; try again later",
    504: "the Site is having trouble; try again later",
}

#: Final statuses: asking again gets the same answer, so they are not retried.
_FINAL_STATUSES = frozenset({404, 410})

#: Refusals of who or where the request is from: the ones a tunnel may answer.
_LOOKS_LIKE_A_WALL = frozenset({403, 451, 530, 999})

#: The refusals a jar of cookies answers, where the Site is one cookies help on;
#: an age gate is lifted by a signed-in browser's jar like the others.
COOKIES_ANSWER: frozenset[str] = frozenset({"wall-login", "wall-age", "http-401", "http-403"})

#: Codes the reader one layer up assigns, declared here so each is minted once.
CODE_UNSUPPORTED = "unsupported"
CODE_COOKIES_REFUSED = "cookies-refused"
CODE_NO_ANSWER = "no-answer"
#: An address Sift's own reader does not read; nothing was asked of the Site.
CODE_ADDRESS_NOT_READ = "address-not-read"
INTERPRETED_CODES: frozenset[str] = frozenset(
    {CODE_UNSUPPORTED, CODE_COOKIES_REFUSED, CODE_NO_ANSWER, CODE_ADDRESS_NOT_READ}
)


@dataclass(frozen=True, slots=True)
class Failure:
    """One failure, classified: a stable `code` for search, and the `sentence` a person reads."""

    #: 1, 2 or 3: how far to trust the sentence.
    tier: int
    code: str
    sentence: str
    #: Whether a tunnel would fix it; the caller knows whether the Site went out directly.
    a_tunnel_would_help: bool = False
    #: Whether cookies get past this here; whether any are saved is the caller's to know.
    cookies_would_help: bool = False
    #: The code in words as given ("410 Gone"), so a row never loses it.
    said: str | None = None
    final: bool = False


#: Read from the tools' output: the response itself is gone by then.
_STATUS = re.compile(
    r"\bHTTP Error (\d{3})\b|\b(\d{3}):? (?:Client|Server) Error\b|\bstatus[= ](\d{3})\b"
)

#: Tier 2 conditions that are not statuses; the flag is true only where the failure is
#: about where the request came from. `{site}` is filled in by `classify`.
_CONDITIONS: tuple[tuple[str, str, str, bool], ...] = (
    (
        "ddos-guard",
        r"ddos.?guard|just a moment|checking your browser|cf.?challenge",
        "The Site put a bot check in front of the download. It usually clears by itself. Try again "
        "in a few minutes.",
        False,
    ),
    (
        "login-failed",
        r"login failed|unable to log ?in|invalid (?:username|password|credentials)",
        "{site} refused the saved cookies. Export them from your browser again and replace them in "
        "Settings > Sites and Tunnels > Cookies.",
        False,
    ),
    (
        "password-protected",
        r"password.?protected|requires a password|wrong password",
        "The post is protected by a password, so Sift cannot download it.",
        False,
    ),
    (
        "no-space",
        r"no space left|insufficient (?:free )?space|disk (?:is )?full",
        "The disk is full. Free some space and try again.",
        False,
    ),
    (
        "unreadable-type",
        r"invalid content type|unexpected mime|not a (?:media|video|image) file",
        "The Site sent something that is not media where the file should be. This usually means "
        "a placeholder page, not a broken file.",
        False,
    ),
    (
        # A redirect to the front page is a refusal reported as a redirect.
        "sent-to-the-front-page",
        r"redirection detected|redirected to (?:the )?(?:home|front) ?page",
        "The Site redirected to its front page instead of the video. Usually the video is not "
        "shown in the region the request came from, or it has been removed.",
        True,
    ),
    (
        "too-slow",
        r"download (?:is )?too slow|slow download|throttl",
        "The Site sent this too slowly, so Sift canceled the download. Try again later.",
        False,
    ),
)

_CONDITION_PATTERNS = tuple(
    (code, re.compile(pattern, re.IGNORECASE), sentence, tunnel)
    for code, pattern, sentence, tunnel in _CONDITIONS
)


#: The reasons the tools name in their own words, read before the status that rides with them.
#: Region and age write a Site wall's codes, at tier 2: (code, pattern, meaning, final, tunnel).
_TOOL_REASONS: tuple[tuple[str, str, str, bool, bool], ...] = (
    (
        "wall-region",
        r"not available in your (?:country|region|location)|geo.?restrict|"
        r"blocked (?:in|from) your (?:country|region)",
        "it is not shown in the region the request came from; a tunnel gets past it",
        False,
        # The meaning already says a tunnel gets past it.
        False,
    ),
    (
        "wall-age",
        r"confirm your age|age.?restricted|inappropriate for some users",
        "it wants proof of age; cookies from a signed-in browser get past it",
        False,
        False,
    ),
    (
        "wall-login",
        r"requires authentication|login required|log ?in to (?:view|see|watch)|"
        r"only available for registered users",
        "it shows this only when you are signed in; cookies get past it",
        False,
        False,
    ),
    (
        "private",
        r"\bprivate video\b|this (?:video|post|account|profile) is private|\bis a private\b",
        "the post is private",
        True,
        False,
    ),
    (
        "gone",
        r"\bis unavailable\b|\bvideo unavailable\b|\bno longer available\b|"
        r"(?:has|have) been (?:removed|deleted|disabled|taken down)|\[error\] '?unavailable'?",
        "the post was removed, deleted or made private",
        True,
        False,
    ),
    (
        # Not "too many requests": a 429 present is read as the status.
        "rate-limited",
        r"rate.?limit",
        "it is limiting how many requests it accepts; try again in a few minutes",
        False,
        False,
    ),
    (
        # The page changed or the video went; not final, since an updated tool reads a changed page.
        "unreadable-page",
        r"unable to extract|please report this issue",
        "the downloader could not find the video on the page; the page changed, or the video "
        "was removed",
        False,
        False,
    ),
)

_TOOL_REASON_PATTERNS = tuple(
    (code, re.compile(pattern, re.IGNORECASE), meaning, final, tunnel)
    for code, pattern, meaning, final, tunnel in _TOOL_REASONS
)

#: Each tool's failure line; only the part after the extractor and the id is the reason.
_YTDLP_ERROR = re.compile(r"^ERROR:\s*(?:\[[^\]]*\]\s*)?(?:[\w-]+:\s+)?(.+)$", re.MULTILINE)
_GALLERYDL_ERROR = re.compile(r"^\[[\w.-]+\]\[error\]\s*(.+)$", re.MULTILINE)
#: Cut before quoting: an address, yt-dlp's bracketed cause, its report-this line.
_CUT = (
    re.compile(r"\s*\(caused by .*$", re.IGNORECASE),
    re.compile(r"[;.]?\s*(?:please report this issue|use --cookies|confirm you are on).*$", re.I),
    re.compile(r"https?://\S+"),
    re.compile(r"'?[A-Za-z]:[\\/][^'\n]*'?"),
    re.compile(r"'?(?:/[\w.~-]+){2,}/?'?"),
)
_QUOTE_LIMIT = 90


def classify(url: str, output: str) -> Failure | None:
    """What a tool's output says went wrong, the most specific reading first."""
    record = match(url)
    site = attribute(url).site or "The Site"
    wall = _wall(record, output)
    if wall is not None:
        code = f"wall-{wall.kind}"
        return Failure(
            tier=3,
            code=code,
            sentence=wall.sentence,
            a_tunnel_would_help=wall.a_tunnel_would_help,
            cookies_would_help=_cookies_would_help(record, code),
            said=tool_words(output),
        )

    for code, pattern, sentence, tunnel in _CONDITION_PATTERNS:
        if pattern.search(output):
            return Failure(
                tier=2,
                code=code,
                sentence=sentence.format(site=site),
                a_tunnel_would_help=tunnel,
                cookies_would_help=_cookies_would_help(record, code),
                said=tool_words(output),
            )

    for code, pattern, meaning, final, tunnel in _TOOL_REASON_PATTERNS:
        if pattern.search(output):
            said = tool_words(output)
            here = _meaning_on(record, code)
            return Failure(
                tier=3 if here is not None else 2,
                code=code,
                sentence=_named(site, here.meaning if here is not None else meaning, said),
                a_tunnel_would_help=here.a_tunnel_would_help if here is not None else tunnel,
                cookies_would_help=_cookies_would_help(record, code),
                said=said,
                final=final,
            )

    status = _status(output)
    if status is None:
        return None
    return explain_status(url, status)


def explain_status(url: str, status: int) -> Failure:
    """What a status code means for a download from this address, however it reached Sift."""
    record = match(url)
    site = attribute(url).site or "The Site"
    code = f"http-{status}"
    here = _meaning_on(record, code)
    general = NAMED_CODES.get(status) or STATUS_MEANS.get(status)
    meaning = here.meaning if here is not None else general
    said = _status_words(status)
    return Failure(
        tier=3 if here is not None else 2 if general is not None else 1,
        code=code,
        sentence=f"{site} answered {said}"
        + (
            f": {meaning}." if meaning else ", not a standard code." if said == str(status) else "."
        ),
        # The Site's own reading decides the tunnel either way.
        a_tunnel_would_help=(
            here.a_tunnel_would_help if here is not None else status in _LOOKS_LIKE_A_WALL
        ),
        cookies_would_help=_cookies_would_help(record, code),
        said=said,
        final=status in _FINAL_STATUSES,
    )


def reading_now(url: str, code: str | None) -> Failure | None:
    """What a failure recorded with this code says now, from the code and Site; None if unknown."""
    if not code:
        return None
    if code.startswith("http-"):
        try:
            status = int(code.removeprefix("http-"))
        except ValueError:
            return None
        return explain_status(url, status)
    record = match(url)
    if code.startswith("wall-"):
        return _wall_reading(record, code)
    for known, _pattern, sentence, tunnel in _CONDITION_PATTERNS:
        if known == code:
            return Failure(
                tier=2,
                code=code,
                sentence=sentence.format(site=attribute(url).site or "The Site"),
                a_tunnel_would_help=tunnel,
                cookies_would_help=_cookies_would_help(record, code),
            )
    for known, _pattern, meaning, final, tunnel in _TOOL_REASON_PATTERNS:
        if known == code:
            here = _meaning_on(record, code)
            return Failure(
                tier=3 if here is not None else 2,
                code=code,
                sentence=_named(
                    attribute(url).site or "The Site",
                    here.meaning if here is not None else meaning,
                    None,
                ),
                a_tunnel_would_help=here.a_tunnel_would_help if here is not None else tunnel,
                cookies_would_help=_cookies_would_help(record, code),
                final=final,
            )
    return None


def _wall_reading(record: SiteRecord | None, code: str) -> Failure | None:
    """A recorded wall code read as this Site's own wall of that kind, or None."""
    for wall in record.walls if record is not None else ():
        if f"wall-{wall.kind}" == code:
            return Failure(
                tier=3,
                code=code,
                sentence=wall.sentence,
                a_tunnel_would_help=wall.a_tunnel_would_help,
                cookies_would_help=_cookies_would_help(record, code),
            )
    return None


def _meaning_on(record: SiteRecord | None, code: str) -> Meaning | None:
    """What this code means on this site, if the site's record says. See `Meaning`."""
    if record is None:
        return None
    for entry in record.failure_words:
        if entry.code == code:
            return entry
    return None


def _named(site: str, meaning: str, said: str | None) -> str:
    """A named reason as a sentence: the site, what it means, and the tool's own words for it."""
    return f"{site}: {meaning}" + (f" ({said})." if said else ".")


def tool_words(output: str) -> str | None:
    """The tool's own last words for why it stopped, cut of addresses and paths and kept short."""
    found: tuple[int, str, str] | None = None
    for tool, pattern in (("yt-dlp", _YTDLP_ERROR), ("gallery-dl", _GALLERYDL_ERROR)):
        for hit in pattern.finditer(output):
            if found is None or hit.start() > found[0]:
                found = (hit.start(), tool, hit.group(1))
    if found is None:
        return None
    _where, tool, words = found
    for cut in _CUT:
        words = cut.sub("", words)
    words = words.strip().strip("'\"").rstrip(" :;,").strip()
    if not words:
        return None
    if len(words) > _QUOTE_LIMIT:
        words = words[:_QUOTE_LIMIT].rsplit(" ", 1)[0] + "..."
    return f"{tool}: {words}"


def _cookies_would_help(record: SiteRecord | None, code: str) -> bool:
    """Whether cookies answer this refusal here: the code and the record must both say so."""
    return record is not None and record.cookies_help and code in COOKIES_ANSWER


def _wall(record: SiteRecord | None, output: str) -> Wall | None:
    """The site's own refusal, if it is one this site is known to give."""
    if record is None:
        return None
    for wall in record.walls:
        if re.search(wall.pattern, output, re.IGNORECASE):
            return wall
    return None


def _status(output: str) -> int | None:
    found = _STATUS.search(output)
    if found is None:
        return None
    for group in found.groups():
        if group:
            return int(group)
    return None  # pragma: no cover (a match always fills exactly one of the three groups)


def _status_words(status: int) -> str:
    """The code and its standard phrase ("410 Gone"), or the number alone where none is standard."""
    try:
        named = HTTPStatus(status)
    except ValueError:
        return str(status)
    return f"{status} {named.phrase}"


__all__ = [
    "CODE_ADDRESS_NOT_READ",
    "CODE_COOKIES_REFUSED",
    "CODE_UNSUPPORTED",
    "COOKIES_ANSWER",
    "INTERPRETED_CODES",
    "NAMED_CODES",
    "STATUS_MEANS",
    "Failure",
    "Wall",
    "classify",
    "explain_status",
    "reading_now",
    "tool_words",
]
