# SPDX-License-Identifier: AGPL-3.0-or-later
"""What went wrong with a download, in three tiers, and only the third costs anything to keep.

A download fails for one of three kinds of reason, and they are worth telling apart because the
maintenance each needs is completely different.

**Tier 1: the status code.** Free. A number and its standard phrase come out of the standard
library; nothing here is hand-written and nothing goes stale. 401 and 403 are kept distinct, because
"you are not signed in" and "you are signed in and still not allowed" send somebody to different
places.

**Tier 2: what HTTP has no code for.** A short named list: the codes the standard library names
badly or not at all (a content delivery network's own 520 to 530, and the three codes different
sites invented for rate limiting), plus the handful of conditions that are real, recurring, and not
a status at all: a login that failed rather than one that was missing, a post behind a password, a
disk with no room, a file whose type is not what was asked for.

**Tier 3: the walls that never appear as a status code at all.** This is the one that matters and
the one that costs. A site's age gate answers 403, byte for byte identically to every other 403.
Several large sites answer **200** with a page saying no. The truth exists only as words in the
tool's output, so it is recognised from those words, per site, from the site's own record, and
fought for only where somebody has actually tested it. Everywhere else gets tiers 1 and 2 and the
tool's own last lines, which is honest rather than confidently wrong.

**And a wall on a site set to go out directly gets an action rather than a shrug.** A region block or
an age gate is the one failure Sift can do something about, because the other half of this slice is
a tunnel: it says so, and says which setting to change. That is the one place the two halves meet.

**Every sentence names the site and the code.** A reading says who answered and what they answered,
and what that means where anything is known: "Pornhub answered 410 Gone: it refuses the
downloader's own way of connecting, so Sift has to connect the way a browser does." (the site
answers 410 to the downloader's own handshake from every address, tunnel included, and serves a
browser-style one, so the "removed video" a 410 means elsewhere is exactly wrong there). What a
code means on one site lives on that site's record (`SiteRecord.failure_words`), and what it means
everywhere lives here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus

from sift.slices.download.sources.registry import classify as attribute
from sift.slices.download.sources.sites.catalog import Meaning, SiteRecord, Wall, match

#: Statuses whose standard phrase is not what a person needs to read, or which no standard names.
#:
#: The first four are a content delivery network's own inventions: it sits in front of a large part
#: of the internet, and these are what it answers when the site behind it is the problem rather than
#: the request. The rest are three different sites' three different ways of saying the same thing,
#: none of which is the standard code for it.
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
    # This one is the odd one out and the reason the list exists: it is not a fault, it is a refusal
    # aimed at this machine in particular, and it is the one a tunnel actually answers.
    527: "the connection was interrupted between the network and the Site",
    530: "the Site refused this connection outright",
    999: "the Site refused without giving a reason, which Sites use for automated access",
}

#: What the standard codes MEAN for a download, where the standard phrase alone does not say.
#:
#: Deliberately not every code. A 403 is left without one: on a site cookies help on it is a wait
#: for cookies, on a file host it is a refusal nobody can answer, and a meaning written here would
#: be wrong for one of the two. A code with no meaning still shows ("answered 403 Forbidden")
#: and the reader one layer up adds what the type of failure knows. "Try again" is written only
#: where it is TRUE: a busy or failing server recovers by itself, and a removed video never does.
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

#: The statuses that are FINAL: asking again gets the same answer, so a retry is three runs of the
#: tool to learn nothing. A 404 is here although a mistyped address also answers it, because a
#: mistyped address is just as permanent.
_FINAL_STATUSES = frozenset({404, 410})

#: Statuses that mean the request is being turned away for who or where it is, rather than for
#: anything about the request. These are the ones worth offering a tunnel for.
_LOOKS_LIKE_A_WALL = frozenset({403, 451, 530, 999})

#: The refusals a jar of cookies is the answer to, where the site is one cookies help on.
#:
#: Three, and deliberately not every refusal that mentions being signed in. These are the readings
#: that mean "you are nobody here": the two statuses HTTP has for it, and a site's own wall written
#: down as a login wall by somebody who watched it. A bot check, a rate limit and a post behind a
#: password all say something different, and offering cookies for them would be asking for a thing
#: that cannot help.
#:
#: `wall-age` is here. An age gate on a site cookies help on is lifted by a jar from a signed-in
#: browser, the same one-press fix the three above wait for, and a YouTube link failing on one
#: refusal while waiting on another would read as arbitrary from the row. The waiting row carries
#: the age wall's own sentence as its reason, so the state and the reason are both true at once.
#: With a jar already saved and the gate still up it fails, exactly as a 403 does: there is
#: nothing left to add.
COOKIES_ANSWER: frozenset[str] = frozenset({"wall-login", "wall-age", "http-401", "http-403"})

#: The codes nothing here classifies, assigned by the reader one layer up from its own markers.
#:
#: Declared beside the rest all the same, because a code is a word a row is searched by and a
#: sentence is keyed on, and two places minting them is how one ends up written twice with one
#: letter different. `unsupported` says no tool Sift ships can read this address; `cookies-refused`
#: says cookies were saved, were sent, and the site turned them away anyway.
CODE_UNSUPPORTED = "unsupported"
CODE_COOKIES_REFUSED = "cookies-refused"
#: The Site went quiet for the whole timeout and Sift stopped waiting on it (`errors.NoAnswer`).
CODE_NO_ANSWER = "no-answer"
#: A site Sift reads itself, handed an address its reader does not read: a playlist where it
#: reads videos, a profile where it reads posts. Nothing was asked of the site at all, so nothing
#: the site said can be quoted, and "nothing could be downloaded" would blame a site that was never
#: asked.
CODE_ADDRESS_NOT_READ = "address-not-read"
INTERPRETED_CODES: frozenset[str] = frozenset(
    {CODE_UNSUPPORTED, CODE_COOKIES_REFUSED, CODE_NO_ANSWER, CODE_ADDRESS_NOT_READ}
)


@dataclass(frozen=True, slots=True)
class Failure:
    """One failure, classified. What a screen shows and what the ledger keeps.

    `code` is short and stable, meant for the row and for a search (`http-403`, `wall-age`,
    `disk-full`), and never shown to anyone as it is. `sentence` is the whole of what a person
    reads, and it says what to do wherever there is something to do.
    """

    #: 1, 2 or 3. Kept because it says how much to trust the sentence: a tier 3 phrase was written
    #: about this exact site by somebody who tested it, and a tier 1 phrase was not.
    tier: int
    code: str
    sentence: str
    #: Whether routing this site through a tunnel is the thing that would fix it. Only ever true
    #: when the site is going out directly: the caller knows that and this does not.
    a_tunnel_would_help: bool = False
    #: Whether a jar of cookies is what gets past this, on this site. True only when the refusal is
    #: one of `COOKIES_ANSWER` and the site's own record says cookies help there.
    #:
    #: Whether any are SAVED is not asked here and must not be: this says what would fix it, and
    #: the caller is the one that knows what it already had. Folding the two together would make a
    #: reading of a failure depend on the state of a keyring, so the same 403 would classify two
    #: different ways on two days.
    cookies_would_help: bool = False
    #: The code in words, as the site or the tool gave it: "410 Gone", "yt-dlp: Video #1 is
    #: unavailable". What a reader one layer up appends when its own sentence wins, so the code is
    #: never the thing a row loses. None only where there is nothing to quote.
    said: str | None = None
    #: Whether the answer is final: the same address asked again gets the same answer. A removed or
    #: private post is; a busy server is not. What decides that a download is NOT retried.
    final: bool = False


#: What the tools say when they have hit something at a network level. Read from their output rather
#: than from a response, because by the time Sift sees it the response is gone.
_STATUS = re.compile(
    r"\bHTTP Error (\d{3})\b|\b(\d{3}):? (?:Client|Server) Error\b|\bstatus[= ](\d{3})\b"
)

#: Tier 2 conditions that are not statuses. Each is a real, recurring failure with an answer, and
#: the last field says whether the answer is a tunnel, true only where the failure is about WHERE
#: the request came from, which is the one kind this application can actually do something about.
#: `{site}` in a sentence is the site's own name, filled in by `classify`.
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
        # Seen on a large adult site from an ordinary connection: the video page answers 302 to the
        # front page, and the tool reports a redirect rather than a refusal. Nothing about it says
        # "you are not welcome here", which is what it means.
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


#: The reasons the tools NAME, in their own words, that are not a status and not one of the
#: conditions above: what yt-dlp and gallery-dl say when a site answered with a page rather than a
#: code. Each row is (code, pattern, what it means, final, tunnel).
#:
#: Read after the conditions and before the status, because a named reason is more specific than
#: the status that often rides with it: "HTTP Error 403 ... not available in your country" is a
#: region block, and the 403 is the least of what it says.
#:
#: The region and age rows write the SAME codes a site's own wall writes (`wall-region`,
#: `wall-age`), deliberately: one code means one thing wherever it was recognised, and the sentence
#: table upstairs already answers both. Only the tier differs: 2 here, because these words were
#: written about the tools, not watched on one site.
_TOOL_REASONS: tuple[tuple[str, str, str, bool, bool], ...] = (
    (
        "wall-region",
        r"not available in your (?:country|region|location)|geo.?restrict|"
        r"blocked (?:in|from) your (?:country|region)",
        "it is not shown in the region the request came from; a tunnel gets past it",
        False,
        # False although this IS the tunnel case, for the reason the region wall gives: the
        # meaning already says so, and the flag would append the same advice a second time.
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
        # Not "too many requests": that is the 429's own phrase, and a status that is present is
        # read as the status: the code is the more checkable of the two.
        "rate-limited",
        r"rate.?limit",
        "it is limiting how many requests it accepts; try again in a few minutes",
        False,
        False,
    ),
    (
        # The tool reached the page and could not find the media on it. The site changed its page,
        # or the video was taken down and the page left standing: the tool cannot tell which, so
        # neither is claimed, and it is not final: an updated tool reads a changed page.
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

#: The line each tool writes a failure on, and where its own words for it start.
#:
#: yt-dlp: `ERROR: [PornHub] 3c7f0b9d2e815: Unable to download webpage: HTTP Error 410: Gone (caused
#: by <HTTPError 410: Gone>)`. gallery-dl: `[twitter][error] 'Unavailable'`. Only the part after the
#: extractor and the id is the reason; the rest names the page, which a stored sentence must not.
_YTDLP_ERROR = re.compile(r"^ERROR:\s*(?:\[[^\]]*\]\s*)?(?:[\w-]+:\s+)?(.+)$", re.MULTILINE)
_GALLERYDL_ERROR = re.compile(r"^\[[\w.-]+\]\[error\]\s*(.+)$", re.MULTILINE)
#: What is cut off a tool's own words before they are quoted: an address (a stored sentence never
#: carries one), yt-dlp's cause in brackets, and its closing line asking for the issue to be reported.
_CUT = (
    re.compile(r"\s*\(caused by .*$", re.IGNORECASE),
    re.compile(r"[;.]?\s*(?:please report this issue|use --cookies|confirm you are on).*$", re.I),
    re.compile(r"https?://\S+"),
    # A path names the account it sits under, and a stored sentence carries none.
    re.compile(r"'?[A-Za-z]:[\\/][^'\n]*'?"),
    re.compile(r"'?(?:/[\w.~-]+){2,}/?'?"),
)
#: A quote is a clue, not a transcript. Past this it is cut at a word.
_QUOTE_LIMIT = 90


def classify(url: str, output: str) -> Failure | None:
    """What a tool's output says went wrong, from the most specific reading to the least.

    Returns None when nothing here recognises it, which is a real answer: the caller then keeps its
    own plain sentence, and the tool's own last lines stay one click away. Confidently mislabelling a
    failure is worse than saying only what is known.

    The order is the whole design. A site's own wall is checked first because it is the only reading
    written about that site by somebody who tested it; then the conditions, which are the same
    everywhere; then the reasons the tools name; then the status, which is free and always
    available and least specific. Whatever the reading, the site's own record is then asked what
    that code means THERE, and a meaning it gives makes the sentence the site's.
    """
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
    """What a status code means for a download from this address: the site, the code, the meaning.

    Its own entry point because a status reaches Sift two ways (in a tool's output, read above,
    and as the answer to a request Sift made itself, which the site readers see directly), and the
    two must say the same thing about the same code on the same site.
    """
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
        # The site's own reading of the code decides this where it gives one: a status means what
        # the site says it means here, in either direction: a site's reading can offer a tunnel
        # for a code that usually needs none, or refuse one for a code that usually does.
        a_tunnel_would_help=(
            here.a_tunnel_would_help if here is not None else status in _LOOKS_LIKE_A_WALL
        ),
        cookies_would_help=_cookies_would_help(record, code),
        said=said,
        final=status in _FINAL_STATUSES,
    )


def reading_now(url: str, code: str | None) -> Failure | None:
    """What a failure RECORDED with this code says now, from the code and the site alone.

    The Downloads row's words are rendered when the row is shown, from the code the failure was
    recorded with and the site the address is on: a stored sentence keeps its words for good,
    where a reading made now says what this build knows about that code on that site today, the
    site's own record first.

    The same readings `classify` and `explain_status` make, looked up by CODE rather than found in
    a tool's output: a status is explained; a wall is that site's wall of that kind; a condition
    and a named reason are their own sentences, the reason said without the tool's quote: the
    quote is in the stored sentence and in the log, and a code does not carry it.

    None where the code alone cannot say it: no code, a wall this site has no record of, a code
    minted by the reader one layer up (`INTERPRETED_CODES`, whose words live with that reader), or
    one this build has never heard of. The caller then keeps the stored sentence.
    """
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
    for known, _pattern, sentence, tunnel in _CONDITION_PATTERNS:
        if known == code:
            return Failure(
                tier=2,
                code=code,
                # A condition sentence names its site, as the live classifier fills it in.
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
    """The tool's own words for why it stopped, quoted with the tool's name and nothing else.

    The LAST such line, because a tool that tried twice reports the attempt that decided it last.
    Cut of any address, of the cause yt-dlp appends in brackets and of its line asking for the issue to be reported,
    and cut short at a word past a sentence's worth: this is a clue for somebody reading a row,
    and the whole output is in the log.
    """
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
    """Whether cookies are the answer to this refusal on this site.

    Both halves are needed and neither is enough. A 403 is a 403 everywhere, so the code alone
    would offer cookies for a file host that has no account to hold any; and a site cookies help on
    still answers 404 for a post that is gone, so the record alone would offer them for a failure
    they cannot touch.
    """
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
    """The code and its standard phrase ("410 Gone"), which the standard library already knows.

    Nothing hand-written: writing out "404 Not Found" by hand is a list to maintain for no gain, and
    the phrases are standardised precisely so that everyone uses the same ones. A number no
    standard names is given as the number alone rather than dressed up as one; whether to
    say so is the sentence's call, where a known meaning may still attach to it.
    """
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
