# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger key: a normalised URL, hashed.

The normalization itself is `sift.kernel.urls`, because a file's own record shows the address it
was fetched from too, and a slice may not import another slice. What is here is the part that is
the ledger's: turning that canonical string into the key a re-dropped link is recognized by.

The key is a hash of `normalize_url`'s output, so anything that widens what that strips re-keys
every URL already stored that carries the newly stripped parameter. The check that recognises a
re-drop then misses, and the file is fetched a second time with nothing on any screen to say why.
That is why the record's own cleaner is `cleaned_for_record`, a separate function with a separate
list, rather than a wider version of this one.

`normalize_url` and `without_signature` are re-exported here for the callers that take them from
this module, so the one place to read about the ledger key is the one place that computes it.
"""

from __future__ import annotations

import hashlib

from sift.kernel.urls import cleaned_for_record, normalize_url, without_signature

# Length of the hex digest kept as the ledger key. A full SHA-256 is 64 hex characters; half of it
# is far more than enough to make a collision between two real URLs not a thing that happens, and it
# keeps the stored key and the index over it small.
_HASH_LENGTH = 32


def url_hash(url: str) -> str:
    """The ledger key for a URL: a hash of its normalised form.

    Two links that point at the same media normalise to the same string and so hash the same, which
    is what lets the ledger recognize a re-drop without keeping the URLs themselves around to
    compare.
    """
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_HASH_LENGTH]


__all__ = ["cleaned_for_record", "normalize_url", "url_hash", "without_signature"]
