# SPDX-License-Identifier: AGPL-3.0-or-later
"""One file for one viewer, behind the per-file check: its path, its derivatives, its marks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sift.kernel.access.constraints import (
    PREDICATES,
)
from sift.kernel.access.repository.assets import (
    _BEST_DERIVATIVE_OF_ASSET,
    _DERIVATIVE_OF_ASSET,
    _LOCATIONS_OF_ASSET,
)
from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.views import (
    AssetView,
    Enrichment,
    ServedDerivative,
    _asset_view,
    _is_object_id,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.content import (
    Asset,
    DerivativeKind,
    Location,
    LocationStatus,
    asset_from_row,
    location_from_row,
    params_key,
)
from sift.kernel.serving import art_version


@dataclass(frozen=True, slots=True)
class FileRecord:
    """A file's own page: the file, where its bytes sit and what its first present copy is
    called now. No places and no name for a file this viewer sees only as a placeholder."""

    view: AssetView
    locations: tuple[Location, ...] = ()
    name_on_disk: str | None = None


#: The five ways a file is enriched, in the order the screen draws them. The filter's own words.
_ENRICHED_NAMES = ("stash", "faces", "folder", "filename", "watermark")
# From the predicate table, so this and the `enriched:` filter cannot disagree. Module constants
# only, and the one value binds, which the S608 rule cannot see through a `join`.
_ENRICHED_COLUMNS = ", ".join(
    f"{PREDICATES[f'enriched_{way}']} AS {way}" for way in _ENRICHED_NAMES
)
_ENRICHED_WAYS = f"SELECT {_ENRICHED_COLUMNS} FROM assets a WHERE a.id = ?"  # noqa: S608

#: WHICH stash-box recognised this file, a row per box: only the APPLIED matches, as
#: `enriched_stash` reads, since a waiting or refused match wrote nothing. Newest decision first, so
#: the first name is the one said where only one fits; the name breaks a tie. The name is what the
#: mark says, the `slug` what it is painted in (null for a box Sift has never heard of).
_ENRICHED_BOXES = """
SELECT sb.name AS name, sb.slug AS slug
  FROM asset_stash_box_matches sm
  JOIN stash_boxes sb ON sb.id = sm.box_id
 WHERE sm.asset_id = ? AND sm.state = 'applied'
 ORDER BY sm.decided_at DESC, sb.name ASC
"""


#: The boxes the file's own filings name (`box_id`), for a file whose filings outlived its answer.
_FILING_BOXES = """
SELECT sb.name AS name, sb.slug AS slug
  FROM stash_boxes sb
 WHERE sb.id IN (
   SELECT box_id FROM asset_people WHERE asset_id = ? AND source = 'stash_box'
   UNION SELECT box_id FROM asset_tags WHERE asset_id = ? AND source = 'stash_box'
   UNION SELECT box_id FROM asset_usernames WHERE asset_id = ? AND source = 'stash_box'
 )
 ORDER BY sb.name ASC
"""


class OneFileReads(RepositoryCore):
    """The scoped reads of one file."""

    async def enriched_by(self, asset_id: str) -> list[Enrichment]:
        """Which of the ways wrote to this file, and for a stash-box, which box.

        The predicates the `enriched:` filter reads, asked of one row, so a file's marks agree with
        the count that found it. Unscoped: the route has already resolved the file through the
        scoped read. A file two boxes recognised carries two entries, each naming its box.
        """
        if not _is_object_id(asset_id):
            return []
        statement = _ENRICHED_WAYS
        # fmt: off
        row = await self._db.fetch_one(statement, (asset_id,))  # nosemgrep: sift-no-string-built-sql
        # fmt: on
        if row is None:
            return []
        found: list[Enrichment] = []
        for way in _ENRICHED_NAMES:
            if not row[way]:
                continue
            if way != "stash":
                found.append(Enrichment(via=way))
                continue
            # Asked only once the predicate says a box did something. A stash-box filing with no
            # applied match behind it is named by the box the filing names, and drawn unnamed
            # where it names none, never dropped, or the marks would disagree with the
            # `enriched:stash` count.
            boxes = await self._db.fetch_all(_ENRICHED_BOXES, (asset_id,))
            if not boxes:
                boxes = await self._db.fetch_all(_FILING_BOXES, (asset_id,) * 3)
            found.extend(
                Enrichment(
                    via=way,
                    name=str(box["name"]),
                    box=None if box["slug"] is None else str(box["slug"]),
                )
                for box in boxes
            )
            if not boxes:
                found.append(Enrichment(via=way))
        return found

    async def get_asset(self, viewer: Viewer, asset_id: str) -> AssetView | None:
        """The asset, if this viewer may know it exists. None is "not allowed" and "not there"
        alike: a 403 would confirm that it exists, so both are the 404 a made-up id gets."""
        row = await self._one(viewer, asset_id, reveal=self._reveal_existence(viewer))
        if row is None:
            self._log_denied(viewer, asset_id)
            return None
        return _asset_view(row, viewer)

    async def file_record(self, viewer: Viewer, asset_id: str) -> FileRecord | None:
        """`get_asset`, `locations` and `names_on_disk` behind ONE check, for the file's page.

        A file this viewer may have the bytes of is one the first check already answered: either
        not concealed, or concealed with the vault open, which is `open_asset`'s own question.
        """
        view = await self.get_asset(viewer, asset_id)
        if view is None:
            return None
        if view.concealed and not viewer.show_hidden:
            return FileRecord(view)
        rows = await self._db.fetch_all(_LOCATIONS_OF_ASSET, (asset_id,))
        places = tuple(location_from_row(row) for row in rows)
        present = next((one for one in places if one.status is LocationStatus.PRESENT), None)
        return FileRecord(
            view, places, None if present is None else PurePosixPath(present.rel_path).name
        )

    async def open_asset(self, viewer: Viewer, asset_id: str) -> Asset | None:
        """The asset, if this viewer may have its bytes: never a concealed one, even where its
        placeholder is on the grid."""
        row = await self._one(viewer, asset_id, reveal=1 if viewer.show_hidden else 0)
        if row is None:
            self._log_denied(viewer, asset_id)
            return None
        return asset_from_row(row)

    async def can_view(self, viewer: Viewer, asset_id: str) -> bool:
        """Whether this viewer may have the asset's content."""
        return await self.open_asset(viewer, asset_id) is not None

    async def is_concealed(self, user_id: str, asset_id: str) -> bool:
        """Whether this user is being kept from the asset by the vault, rather than by a grant: only
        a concealment is a property a copy of the file should acquire.

        Asked twice, vault locked and then open; a file that appears only when it opens was
        concealed. A user id rather than a viewer, so no caller can pass one with the vault already
        open. No denial is logged: this is Sift comparing two of its own rows.
        """
        plain = await self.load_viewer(user_id)
        if plain is None:
            # No such user, or a disabled one. There is nobody for it to be concealed from.
            return False
        if await self._one(plain, asset_id, reveal=0) is not None:
            return False
        return await self._one(plain, asset_id, reveal=1) is not None

    async def locations(self, viewer: Viewer, asset_id: str) -> list[Location]:
        """Where the asset's bytes sit, for a viewer allowed to have them; empty for anyone else.
        `locate` turns one into a file."""
        if await self.open_asset(viewer, asset_id) is None:
            return []
        rows = await self._db.fetch_all(_LOCATIONS_OF_ASSET, (asset_id,))
        return [location_from_row(row) for row in rows]

    async def locate(self, viewer: Viewer, asset_id: str) -> Path | None:
        """The file to serve for an asset this viewer may have, or None: the scoped way from an
        asset to a path, so no slice needs the content store. The first present copy wins; None is
        "not allowed" and "nowhere to read it" alike."""
        if await self.open_asset(viewer, asset_id) is None:
            return None
        rows = await self._db.fetch_all(_LOCATIONS_OF_ASSET, (asset_id,))
        for row in rows:
            location = location_from_row(row)
            if location.status is LocationStatus.PRESENT:
                return await self._content.path_of(location)
        return None

    async def locate_derivative(
        self,
        viewer: Viewer,
        asset_id: str,
        kind: DerivativeKind,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Path | None:
        """The thumbnail, preview or sprite to serve, or None. Scoped by `open_asset`, as `locate`
        is, since a picture of a file says what it is: a placeholder gets no art. None is also a
        derivative not built yet, alike, or the answer would say which assets exist."""
        served = await self.serve_derivative(viewer, asset_id, kind, params=params)
        return served.path if served is not None else None

    async def serve_derivative(
        self,
        viewer: Viewer,
        asset_id: str,
        kind: DerivativeKind,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> ServedDerivative | None:
        """The same picture, with what a response has to know to say how long it may be kept; the
        concealment flag off the row the check returned, never a second question."""
        return await self._served(
            viewer, asset_id, _DERIVATIVE_OF_ASSET, (asset_id, kind.value, params_key(params))
        )

    async def serve_newest_derivative(
        self, viewer: Viewer, asset_id: str, kind: DerivativeKind
    ) -> ServedDerivative | None:
        """The most recently built picture of this kind, whatever settings built it: for a recipe
        that can change (a hover clip's length), so the old one plays until its replacement lands.
        Only where any version is a valid answer, never for a rendition whose settings say what it
        IS."""
        return await self._served(
            viewer, asset_id, _BEST_DERIVATIVE_OF_ASSET, (asset_id, kind.value)
        )

    async def _served(
        self,
        viewer: Viewer,
        asset_id: str,
        statement: Any,
        arguments: tuple[Any, ...],
    ) -> ServedDerivative | None:
        """The shared half of the two questions above: scope, find, and describe the caching rule."""
        row = await self._one(viewer, asset_id, reveal=1 if viewer.show_hidden else 0)
        if row is None:
            self._log_denied(viewer, asset_id)
            return None
        found = await self._db.fetch_one(statement, arguments)
        if found is None:
            return None
        path = await self._content.derivative_at(found["rel_cache_path"])
        if path is None:
            return None
        # The token is the asset's, but whether THIS picture may be kept is this picture's: one with
        # no recorded digest is not named by the token, so it gets the careful rule.
        keepable = found["content_hash"] is not None
        return ServedDerivative(
            path=path,
            version=art_version(row["art_marks"], viewer.cache_stamp) if keepable else None,
            concealed=bool(row["concealed"]),
        )
