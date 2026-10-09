# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a folder tree and a set of filenames for the names hiding in them."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from sift.kernel.text import clean_stored_text
from sift.slices.suggestions.naming_filenames import (
    Posted,
    Username,
    mirror_in_folder,
    one_post,
    person_in_filename,
    posted_in_filename,
    posts_among,
    repeated_prefix,
    username_and_number_in_filename,
    username_in_filename,
)
from sift.slices.suggestions.naming_known import (
    NEAR_MISS_EDITS,
    KnownName,
    fold_known,
    known_names_in,
    known_people_in,
    near_misses,
)
from sift.slices.suggestions.naming_words import (
    PARSER_VERSION,
    STOP_WORDS,
    Reading,
    Segment,
    classify,
    fold,
    is_date_like,
    is_stop_folder,
    reads_like_a_name,
    strip_noise,
)

#: How much of a folder name the people found inside it have to account for.
_KNOWN_COVERAGE = 0.5


def people_in(name: str, *, known: Sequence[KnownName] = ()) -> list[str]:
    """The names a single folder claims, which is usually one and is sometimes two.

    **The joiner needs corroboration too.**
    """
    stripped = strip_noise(name)
    # The library recognising itself. Whole words, longest first, and enough of the name to be
    # what the folder is ABOUT rather than a word that happens to be in it.
    recognised = known_people_in(name, known)
    covered = sum(len(fold(found)) for _, found in recognised)
    if recognised and covered >= len(stripped) * _KNOWN_COVERAGE:
        return [found for _, found in recognised]
    # Split on the name AS WRITTEN, before folding. `&` and `+` are punctuation and folding turns
    # them into the space between two words, so a duo written `Nadia & Sarah` would arrive here as
    # one name with nothing left to split on.
    written = clean_stored_text(name)
    parts = [
        strip_noise(part)
        for part in re.split(r"\s(?:and|&|\+)\s|&|\+", written, flags=re.IGNORECASE)
    ]
    halves = [part for part in parts if part and reads_like_a_name(part)]
    if len(halves) > 1 and any(known_people_in(half, known) for half in halves):
        return halves
    return [stripped] if stripped and reads_like_a_name(stripped) else []


def _deepest_name(chain: Sequence[str], kinds: Sequence[Segment]) -> int | None:
    """The deepest segment that reads like a name, stepping past at most one word that does not."""
    name_at: int | None = None
    skipped_one = False
    for index in range(len(chain) - 1, -1, -1):
        if kinds[index] is not Segment.WORD:
            continue
        if reads_like_a_name(chain[index]):
            name_at = index
            break
        if skipped_one:
            break
        skipped_one = True
    return name_at


def _known_above(
    chain: Sequence[str], kinds: Sequence[Segment], name_at: int, known: Sequence[KnownName]
) -> int:
    """Where a person the library already holds sits above the picked word, else the word."""
    by_name = {one.folded for one in known}
    if fold(strip_noise(chain[name_at])) not in by_name:
        for index in range(name_at - 1, -1, -1):
            if kinds[index] is not Segment.WORD:
                continue
            folded = fold(strip_noise(chain[index]))
            if folded and folded in by_name:
                name_at = index
                break
    return name_at


def read_chain(
    chain: Sequence[str],
    *,
    sites: Iterable[str] = (),
    known: Sequence[KnownName] = (),
) -> Reading:
    """What a folder's whole path claims, read from the files upwards.

    **From the files upwards, not from the top down.**
    """
    kinds = [classify(part, sites=sites) for part in chain]

    # The deepest word that could be somebody. Walking PAST one that could not is the whole
    # difference between reading `Northlight/Orla Fennimore/My Rise in the Ranks` and reading nothing:
    # a scene title is a word, it is not a name, and the person is the folder above it.
    name_at = _deepest_name(chain, kinds)

    # A person the library ALREADY holds, sitting above the word that was picked.
    if name_at is not None and known:
        name_at = _known_above(chain, kinds, name_at, known)

    site_at: int | None = None
    for index in range(len(chain) - 1, -1, -1):
        if kinds[index] is Segment.SITE:
            site_at = index
            break

    if name_at is None:
        site = clean_stored_text(chain[site_at]).strip() if site_at is not None else ""
        return Reading(site=site)

    # No second check here. The walk above already refused anything that does not read like a
    # name, and asking again would make an unreadable segment abandon the whole chain rather than
    # be stepped over.
    name = strip_noise(chain[name_at])

    site = ""
    is_username = False
    if site_at is not None:
        site = clean_stored_text(chain[site_at]).strip()
        # A username is a name written directly under its site, ignoring anything junk in between:
        # `instagram/harlowquin` and `instagram/2023/harlowquin` are the same claim about the same
        # username. The other way round (`Nadia Vance/Instagram`) is a person who happens to
        # have a folder per site, and her folder name is a display name rather than a username.
        is_username = site_at < name_at and all(
            kind is Segment.JUNK for kind in kinds[site_at + 1 : name_at]
        )

    return Reading(name=name, site=site, is_username=is_username, depth=name_at)


__all__ = [
    "NEAR_MISS_EDITS",
    "PARSER_VERSION",
    "STOP_WORDS",
    "KnownName",
    "Posted",
    "Reading",
    "Segment",
    "Username",
    "classify",
    "fold",
    "fold_known",
    "is_date_like",
    "is_stop_folder",
    "known_names_in",
    "known_people_in",
    "mirror_in_folder",
    "near_misses",
    "one_post",
    "person_in_filename",
    "posted_in_filename",
    "posts_among",
    "reads_like_a_name",
    "repeated_prefix",
    "strip_noise",
    "username_and_number_in_filename",
    "username_in_filename",
]
