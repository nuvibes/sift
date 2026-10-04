# SPDX-License-Identifier: AGPL-3.0-or-later
"""What version of Sift this is, read from the one place that declares it.

The number is declared in pyproject.toml and nowhere else. Everything that needs it reads the
installed package's own metadata, which is that declaration after packaging, so a build cannot
report one version while its installer claims another. The desktop installer takes the same number
from the same file at build time; see scripts/release.py.

One function rather than one per caller: two, in different features, could give different
answers for the same situation, and neither would know about the other.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

__all__ = ["app_version", "release_of"]


def app_version() -> str:
    """The running version, or an empty string when Sift is not installed as a package.

    The empty string is the honest answer rather than a placeholder: it parses as no version at
    all, so a comparison against it declines to claim anything instead of inventing an ordering.
    Callers that put the version in front of a person say what they show for it.
    """
    try:
        return package_version("sift")
    except PackageNotFoundError:
        return ""


#: A release's three numbers, and nothing after them: a version Sift itself declares or ran as.
_RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def release_of(text: str) -> tuple[int, int, int] | None:
    """A release's three numbers, for ordering two of Sift's own versions, or None where `text` is
    not one. Not a reader of feeds or pre-releases (the update check has its own): only the plain
    versions Sift is built as, such as the version a setting's default last changed in."""
    found = _RELEASE.match(text.strip())
    if found is None:
        return None
    return int(found[1]), int(found[2]), int(found[3])
