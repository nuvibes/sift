# SPDX-License-Identifier: AGPL-3.0-or-later
"""One asset per unique file, however many places it sits and whatever it is called.

A path is not an identity. Files get renamed, dragged into other folders, copied to a second
drive, and carried on a NAS that is not always mounted. If the path were the identity then every
rename would orphan its tags, every remount would re-import the library, and the same clip in two
folders would be two different things.

So the identity is the content (a BLAKE3 digest of a sample of the bytes: the size,
both ends and fourteen positions through the middle; `kernel/content/hashing.py` says why not
every byte), and the places it physically sits are a separate table. That one decision is what
makes the rest fall out:

    the same bytes imported twice     one asset, two locations. Not a second asset.
    a file renamed or moved           the digest still matches, so the tags follow it.
    a NAS unplugged                   its locations go missing; the assets stay, with everything
                                      anyone ever put on them, and reconnect when it comes back.
    an exact duplicate                visible as such, which is what makes reclaiming the space
                                      an offer Sift can make rather than a guess.

Near-duplicates are not this module's business. Different bytes are a different asset, full stop.
Deciding that two different files are *nearly* the same is a question with no correct answer and
a review queue attached; keeping it out of here is what lets identity stay small and exactly right.

Nothing outside the kernel reads these tables. They carry no permission with them: an asset is
only as visible as the least visible folder it sits in, and that is resolved a layer up. Read
assets through the access layer, which is the only thing that knows who is asking.
"""

from __future__ import annotations

from sift.kernel.content.identity_arrivals import Arrivals
from sift.kernel.content.identity_counts import (
    _COUNT_COMING_OF,
    _COUNT_LACKING_BY_KIND,
    _COUNT_UNREAD,
    _COUNT_WANTING_OF,
    Counts,
    _lacking_statement,
    _term_params,
    lacks_derivative,
    lacks_fingerprint,
    wanted_outside,
)
from sift.kernel.content.identity_derivatives import Derivatives
from sift.kernel.content.identity_fields import (
    MUSIC_FROM_ACOUSTID,
    MUSIC_SHARED,
    MUSIC_TYPED,
    Fields,
    MusicProvenance,
    cleaned_song,
    music_provenance,
    seed_music_on,
    song_and_length,
    songs_of,
    unseed_music_on,
)
from sift.kernel.content.identity_models import (
    PROBE_VERSION,
    PROBE_VERSION_NOT_A_JPEG,
    RECIPE_VERSIONS,
    Asset,
    Derivative,
    DerivativeKind,
    FolderMedia,
    Ingested,
    Lack,
    Lacking,
    Location,
    LocationStatus,
    ProbeKeep,
    Verdict,
    VerdictProduct,
    Within,
    asset_from_row,
    location_from_row,
    made_for,
    picture_verdict,
)
from sift.kernel.content.identity_paths import check_rel_path, derivative_relpath, params_key
from sift.kernel.content.identity_places import Places, _keep_under_budget
from sift.kernel.content.identity_probes import Probes
from sift.kernel.content.identity_verdicts import Verdicts

__all__ = [
    "MUSIC_FROM_ACOUSTID",
    "MUSIC_SHARED",
    "MUSIC_TYPED",
    "PROBE_VERSION",
    "PROBE_VERSION_NOT_A_JPEG",
    "RECIPE_VERSIONS",
    "_COUNT_COMING_OF",
    "_COUNT_LACKING_BY_KIND",
    "_COUNT_UNREAD",
    "_COUNT_WANTING_OF",
    "Asset",
    "ContentStore",
    "Derivative",
    "DerivativeKind",
    "FolderMedia",
    "Ingested",
    "Lack",
    "Lacking",
    "Location",
    "LocationStatus",
    "MusicProvenance",
    "ProbeKeep",
    "Verdict",
    "VerdictProduct",
    "Within",
    "_keep_under_budget",
    "_lacking_statement",
    "_term_params",
    "asset_from_row",
    "check_rel_path",
    "cleaned_song",
    "derivative_relpath",
    "lacks_derivative",
    "lacks_fingerprint",
    "location_from_row",
    "made_for",
    "music_provenance",
    "params_key",
    "picture_verdict",
    "seed_music_on",
    "song_and_length",
    "songs_of",
    "unseed_music_on",
    "wanted_outside",
]


class ContentStore(Arrivals, Places, Derivatives, Verdicts, Probes, Fields, Counts):
    """The content model. One per database.

    Held on the application rather than reached for through a global, for the same reason the job
    queue is: a global would have to hold a database handle, and a test that wanted a different
    one would have to reach in and swap it out.
    """
