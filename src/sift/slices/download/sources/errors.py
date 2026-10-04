# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a download can fail with, in words the person running Sift can act on.

A tool's own error is a stack trace with the URL in it: it leaks the URL into a shareable log and
tells a non-developer nothing to do. So a failure becomes one of these, a category and a sentence
saying what to do next, carrying no URL or path; the raw output is redacted and logged once.
"""

from __future__ import annotations


class DownloadError(Exception):
    """A download did not complete. The base of the small taxonomy below.

    The message is for a screen: plain words and, where there is one, what to do; never the URL.
    `code` is short and stable (`http-403`) so a ledger row is searchable whatever the wording, and
    `a_tunnel_would_help` marks failures about where the request came from. The exception's TYPE
    alone decides what happens (retry, wait for a login), so a richer explanation never changes it.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        tier: int | None = None,
        a_tunnel_would_help: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        #: How much the sentence is worth (`failures.Failure.tier`). Carried, not derived from the
        #: code, since one code can be a standard phrase on one site and a known meaning on another.
        self.tier = tier
        self.a_tunnel_would_help = a_tunnel_would_help


#: What a person reads when a tool tried to reach this device or the private network and the proxy
#: every tool runs through refused it; shared by the single download and the playlist listing.
PRIVATE_NETWORK_REFUSED = (
    "This link tried to reach this device or its private network, and Sift stopped it."
)


class PrivateNetworkRefused(DownloadError):
    """The tool was refused a connection into this device or the private network.

    The tool sees a 403 from its proxy and words like a login wall; the proxy says what happened.
    Not retryable, and never a reason to replace a cookie jar: the address is the problem.
    """


class UnsupportedURL(DownloadError):
    """Nothing here knows how to fetch this address.

    Not a media link, or a site no shipped tool reads. Not retryable.
    """


class LoginRequired(DownloadError):
    """The site will not serve this without being logged in, and there is no saved login for it.

    With nobody signed in the saved login cannot be decrypted, so the job is parked rather than
    failed; with the login available, it has expired and needs replacing.
    """


class CookiesNeeded(LoginRequired):
    """The site refused, cookies are what get past it here, and none are saved for it.

    A wait, not a failure: the handler parks the job as "Waiting for cookies" with Add cookies, and
    it continues once they are saved. A `LoginRequired` so a handler unaware of it still treats it
    as a login wall, which means a handler that knows it must catch it FIRST, or it becomes a plain
    failure again.
    """


class NothingFound(DownloadError):
    """The address was reachable, but no media came back from it.

    A deleted post, a private profile, a page with nothing to download. A retry does not fix it.
    """


class FetchFailed(DownloadError):
    """A direct fetch of a media address did not complete: a network drop, an HTTP error, or a
    transfer that stopped short. Often transient, so the caller may retry it."""


class NoAnswer(FetchFailed):
    """The Site stopped answering for the whole of Stopped responding for, after any ask worth a
    second wait had been made.

    Final for this job, not retried by the queue: each attempt would sit through the same wait.
    Try again on the row is the next ask, when the person chooses.
    """


#: What a person reads when a Site went quiet and Sift stopped waiting on it.
NO_ANSWER_MESSAGE = (
    "The Site stopped responding, so Sift stopped waiting. Try again in a little while."
)


def nothing_message(site: str) -> str:
    """The words shown when a resolver reached a post but it held no downloadable media.

    One wording for every site, only the name changing.
    """
    return (
        f"Nothing could be downloaded from {site}. The post may be private, deleted, or hold "
        "no media."
    )


def busy_message(site: str) -> str:
    """The words shown when a site rate-limited the resolve so it could not even be started.

    Transient by nature, so it says to try again, one wording for every site.
    """
    return f"{site} is busy, so the download could not start. Try again in a few minutes."
