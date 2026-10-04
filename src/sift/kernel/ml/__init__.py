# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running a model on this machine, for whichever feature wants one.

Two features need this and neither may import the other, which is the whole reason it sits here:
recognising faces, and describing pictures and words as numbers so they can be searched by
meaning. What they share is not their models but the machinery around them: choosing a device
and refusing to pretend when it is missing, obtaining a file nobody ships, proving the file is
what it claims to be, and holding a loaded model so it is prepared once rather than per call.

**Nothing here knows what any model is for.** A model is described to it: an address, a digest, a
size, a licence, and a word for what the file does within its set. The words in that description
belong to the feature that wrote them; this only ever repeats them back in a message. That is what
keeps a face-shaped assumption from arriving in the kernel by accident and then being inherited by
something that is not about faces at all.
"""

from __future__ import annotations

from sift.kernel.ml.runtime import DeviceUnavailable, Loaded, Runner, resolve_provider
from sift.kernel.ml.weights import (
    Progress,
    SessionFactory,
    Weight,
    WeightError,
    WeightStore,
    digest_of,
)

__all__ = [
    "DeviceUnavailable",
    "Loaded",
    "Progress",
    "Runner",
    "SessionFactory",
    "Weight",
    "WeightError",
    "WeightStore",
    "digest_of",
    "resolve_provider",
]
