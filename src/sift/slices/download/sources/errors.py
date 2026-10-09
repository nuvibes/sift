# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a download can fail with, in words a person can act on; never the URL or a path."""

from __future__ import annotations


class DownloadError(Exception):
    """A download did not complete; the type alone decides retry or wait, never the wording."""

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
        self.tier = tier
        self.a_tunnel_would_help = a_tunnel_would_help


PRIVATE_NETWORK_REFUSED = (
    "This link tried to reach this device or its private network, and Sift stopped it."
)


class PrivateNetworkRefused(DownloadError):
    """The proxy refused a connection into this device or the private network; never retried."""


class UnsupportedURL(DownloadError):
    """Nothing here knows how to fetch this address. Not retryable."""


class LoginRequired(DownloadError):
    """The site needs a login and there is no usable saved one."""


class CookiesNeeded(LoginRequired):
    """The site refused and no Cookies are saved: a wait, so catch it before LoginRequired."""


class NothingFound(DownloadError):
    """The address was reachable, but no media came back from it. Not retryable."""


class FetchFailed(DownloadError):
    """A direct fetch of a media address did not complete; often transient, so retryable."""


class NoAnswer(FetchFailed):
    """The Site went quiet past Stopped responding for; final, since a retry waits the same."""


NO_ANSWER_MESSAGE = (
    "The Site stopped responding, so Sift stopped waiting. Try again in a little while."
)


def nothing_message(site: str) -> str:
    return (
        f"Nothing could be downloaded from {site}. The post may be private, deleted, or hold "
        "no media."
    )


def busy_message(site: str) -> str:
    return f"{site} is busy, so the download could not start. Try again in a few minutes."
