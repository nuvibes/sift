# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a file is, and where it sits.

Identity is the content, not the path: one asset per unique BLAKE3 digest, and a location row for
each place those bytes physically live. Import the same file twice and it is one asset with two
locations; rename it, and it is the same asset with its tags still attached.

A feature uses two things:

    checked = verify_ingress(path, origin=Origin.SCAN, settings=settings)
    result = await store.ingest(checked, root_id=root.id, rel_path="clips/holiday.mp4")

`ingest` takes what the ingress gate returned, never a path, so nothing can be hashed or indexed
without having been checked first.

These tables carry no permissions. Reading them directly returns rows regardless of who is
asking, which is why nothing outside the kernel does: the access layer is the sanctioned read
path, and it is the only one that knows who is asking.
"""

from __future__ import annotations

from sift.kernel.content import hashing, perceptual, schema
from sift.kernel.content.duplicates import (
    Copy,
    DuplicateReads,
    Fingerprint,
    Redundancy,
)
from sift.kernel.content.entity_state import (
    EntityStateStore,
    PinnableKind,
    PinView,
    PinWrite,
)
from sift.kernel.content.hashing import CHUNK_BYTES, FileStillChanging, fingerprint, hash_file
from sift.kernel.content.identity import (
    RECIPE_VERSIONS,
    Asset,
    ContentStore,
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
    check_rel_path,
    derivative_relpath,
    lacks_derivative,
    lacks_fingerprint,
    location_from_row,
    params_key,
    wanted_outside,
)
from sift.kernel.content.library import (
    MAX_NAME_LENGTH,
    ROOT_REL_PATH,
    FolderRow,
    Grant,
    LibraryError,
    LibraryStore,
    NotAFolder,
    NotWritable,
    ReservedPath,
    Root,
    RootKind,
    RootOverlap,
    check_folder_writable,
    check_name,
    check_not_reserved,
    folder_from_row,
    overlaps,
    resolve_directory,
    root_from_row,
    subtree_prefix,
)
from sift.kernel.content.tree import FolderNode, TreeReads
from sift.kernel.content.user_state import (
    MAX_RATING,
    MIN_RATING,
    AssetUserState,
    UserStateStore,
    state_from_row,
)

__all__ = [
    "CHUNK_BYTES",
    "MAX_NAME_LENGTH",
    "MAX_RATING",
    "MIN_RATING",
    "RECIPE_VERSIONS",
    "ROOT_REL_PATH",
    "Asset",
    "AssetUserState",
    "ContentStore",
    "Copy",
    "Derivative",
    "DerivativeKind",
    "DuplicateReads",
    "EntityStateStore",
    "FileStillChanging",
    "Fingerprint",
    "FolderMedia",
    "FolderNode",
    "FolderRow",
    "Grant",
    "Ingested",
    "Lack",
    "Lacking",
    "LibraryError",
    "LibraryStore",
    "Location",
    "LocationStatus",
    "NotAFolder",
    "NotWritable",
    "PinView",
    "PinWrite",
    "PinnableKind",
    "ProbeKeep",
    "Redundancy",
    "ReservedPath",
    "Root",
    "RootKind",
    "RootOverlap",
    "TreeReads",
    "UserStateStore",
    "Verdict",
    "VerdictProduct",
    "Within",
    "asset_from_row",
    "check_folder_writable",
    "check_name",
    "check_not_reserved",
    "check_rel_path",
    "derivative_relpath",
    "fingerprint",
    "folder_from_row",
    "hash_file",
    "hashing",
    "lacks_derivative",
    "lacks_fingerprint",
    "location_from_row",
    "overlaps",
    "params_key",
    "perceptual",
    "resolve_directory",
    "root_from_row",
    "schema",
    "state_from_row",
    "subtree_prefix",
    "wanted_outside",
]
