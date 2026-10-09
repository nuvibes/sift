# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one viewer may be shown of a face: its file, the person on it, and its picture; every
screen goes through `_sighting`, so the rule lives in one place."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access import Actionable, Viewer
from sift.kernel.access.viewer import reveals_existence
from sift.slices.faces import tuning
from sift.slices.faces.models import Attribution, PileStatus
from sift.slices.faces.service_base import FaceServiceBase
from sift.slices.faces.store import StoredTrack


@dataclass(frozen=True, slots=True)
class FaceVisibility:
    """What a route serving a face picture needs beyond "yes": whether its file is in the vault,
    so a concealed picture never gets an address a browser may keep."""

    concealed: bool


@dataclass(frozen=True, slots=True)
class Sighting:
    """One appearance as a screen reads it, settled against who is asking; a hidden person's id
    and name are withheld together."""

    track_id: str
    asset_id: str
    started_ms: int
    ended_ms: int
    picture_ms: int
    """When this appearance's picture was taken: the moment of its clearest face, which is the face
    its picture is cut from. Where a pressed face plays from: the frame the person pressed, not
    the first frame the face was seen in. See `Store.picture_moments`."""
    person_id: str | None
    person_name: str | None
    confidence: float | None
    attribution: Attribution | None
    teachable: bool = True
    """Whether agreeing to this face would also teach Sift what the person looks like.

    False for a crop too poor to learn from: a face behind a phone, half a face, something small
    and blurred. Every reference is blended into one averaged description of a person, so a bad one
    pulls that average away from them; those are named and not filed. A screen offering the decision
    should say which is which, or somebody is asked to agree to a picture whose only effect is on
    the file it is on.
    """

    locked: bool = False
    """Whether the file this face is on is in the vault, and the vault is shut.

    Only ever True for a viewer whose concealment mode keeps a placeholder: in the default mode the
    file is simply absent and so is this. A screen with one of these draws the padlock it draws
    everywhere else and does not ask for the crop, which would be refused.
    """

    is_reference: bool = False
    """Whether this appearance actually became one of the pictures Sift matches against.

    `teachable` is a forecast and this is the outcome, and they disagree often enough to be worth
    both. Agreeing to a face OFFERS it: it is declined if the crop is below the quality floor, if
    the person already holds a near-copy of it, or once an appearance has contributed its share:
    every reference is blended into one averaged description, so a second picture of the same moment
    moves it nowhere and a video's worth of them moves it to that video.

    Without this all three are silent. Somebody could name four faces, watch the count go up by one
    and have nowhere at all to find out which one it was, which reads as the number being broken
    rather than as three pictures having been declined.
    """

    pile_id: str | None = None
    """Which group of look-alike faces this one is waiting in, where it is waiting in one.

    Set only for a face with NO person on it, and that is a rule rather than a convenience. A face
    attributed to somebody this viewer may not be told about already arrives here with its name,
    its id, its confidence and its attribution all withheld together; the pile it sits in is a
    resemblance fact about that same person, so it goes with them. A face nobody has named yet is
    the case this exists for, and it has no person to conceal.
    """

    pile_status: PileStatus | None = None
    """Whether that group is waiting to be named or was deliberately set aside.

    Carried because the two live at different addresses and a screen offering to open the group has
    to know which one. Absent, with the id present, means the pile has since been rebuilt away:
    the group is gone rather than ignored, and nothing should offer to open it.
    """

    turned: bool = False
    """Whether the face is turned past the quality bar's angle: kept and matched like any other,
    never grouped and never filed as a reference (`quality.asked_only`). Said only on a file's own
    faces."""


class VisibilityMixin(FaceServiceBase):
    """Settling faces against who is asking."""

    async def viewer_for(self, user_id: str) -> Viewer | None:
        """The user a queued job acts for, or None if gone, with hidden files included.

        A job has no session to open the vault in, so it sees everything; what is shown is still
        settled per viewer.
        """
        return await self._repository.load_viewer(user_id, show_hidden=True)

    async def name_of(self, viewer: Viewer, person_id: str) -> str | None:
        """What this viewer may call one person, or None where they may not be told."""
        found = await self._repository.visible_person(viewer, person_id)
        return found.name if found is not None else None

    def _reveals_existence(self, viewer: Viewer) -> bool:
        """Whether concealed files come back for this viewer at all (`viewer.reveals_existence`)."""
        return reveals_existence(viewer)

    async def may_see_person(self, viewer: Viewer, person_id: str) -> bool:
        """Whether this user may act on this person at all. A vaulted one answers no."""
        return await self._repository.visible_person(viewer, person_id) is not None

    async def touchable_faces(self, viewer: Viewer, track_ids: Sequence[str]) -> Actionable:
        """Which of these faces this user may act on, and which reason each of the rest fell to.

        Decided on each face's file, strictly: a locked placeholder is not enough to act on.
        """
        wanted = list(dict.fromkeys(track_ids))
        found = await self._store.tracks(wanted)
        tracks = {track_id: found.get(track_id) for track_id in wanted}
        by_asset = await self._repository.actionable_of(
            viewer, [track.asset_id for track in tracks.values() if track is not None]
        )
        allowed: list[str] = []
        concealed: list[str] = []
        refused: list[str] = []
        for track_id in wanted:
            track = tracks[track_id]
            if track is None:
                refused.append(track_id)
            elif track.asset_id in by_asset.allowed:
                allowed.append(track_id)
            elif track.asset_id in by_asset.concealed:
                concealed.append(track_id)
            else:
                refused.append(track_id)
        return Actionable(
            allowed=tuple(allowed), concealed=tuple(concealed), refused=tuple(refused)
        )

    async def _shown_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> dict[str, bool]:
        """Which of these files this viewer may be shown something about, and whether it is a
        locked placeholder (True: draw the lock, ask for no crop).

        A membership question (`standing_of`), since a capped page would hide the rest.
        """
        found = await self._repository.standing_of(viewer, asset_ids)
        if self._repository.reveals_named_rows(viewer):
            return dict.fromkeys(found, False)
        return found

    async def _names_of(
        self, viewer: Viewer, tracks: Sequence[StoredTrack]
    ) -> dict[str, str | None]:
        """What each person on these tracks is called for this viewer, None where withheld.

        The cache `_sighting` reads, filled in one question and scoped to one call.
        """
        return await self._names_for(
            viewer, [track.person_id for track in tracks if track.person_id is not None]
        )

    async def _names_for(self, viewer: Viewer, person_ids: Sequence[str]) -> dict[str, str | None]:
        """`_names_of` asked of the people themselves, for a caller that has their ids."""
        wanted = set(person_ids)
        if not wanted:
            return {}
        found = await self._repository.visible_people(viewer, sorted(wanted))
        return {one: found[one].name if one in found else None for one in wanted}

    async def _sighting(
        self,
        viewer: Viewer,
        track: StoredTrack,
        *,
        moments: Mapping[str, int],
        names: dict[str, str | None] | None = None,
        references: set[str] | None = None,
        locked: bool = False,
        named_when_locked: bool = False,
        piles: dict[str, PileStatus] | None = None,
        turned: bool = False,
    ) -> Sighting:
        """One appearance as a screen reads it, with the name settled against this viewer.

        A locked face is not named unless `named_when_locked`; `names` caches names for one call
        and one viewer; `moments` is required, or a pressed face plays from the wrong moment.
        """
        named: str | None = None
        if track.person_id is not None and (named_when_locked or not locked):
            if names is not None and track.person_id in names:
                named = names[track.person_id]
            else:
                found = await self._repository.visible_person(viewer, track.person_id)
                named = found.name if found is not None else None
                if names is not None:
                    names[track.person_id] = named
        return Sighting(
            track_id=track.id,
            asset_id=track.asset_id,
            started_ms=track.started_ms,
            ended_ms=track.ended_ms,
            picture_ms=moments.get(track.id, track.started_ms),
            person_id=track.person_id if named is not None else None,
            person_name=named,
            confidence=track.confidence if named is not None else None,
            attribution=track.attribution if named is not None else None,
            teachable=track.quality >= tuning.REFERENCE_QUALITY,
            is_reference=references is not None and track.id in references,
            locked=locked,
            # Only for a face with nothing on it: the pile is a fact about a withheld person too.
            pile_id=track.pile_id if track.person_id is None else None,
            pile_status=(
                piles.get(track.pile_id)
                if piles is not None and track.pile_id is not None and track.person_id is None
                else None
            ),
            turned=turned,
        )

    async def see_face(self, viewer: Viewer, track_id: str) -> FaceVisibility | None:
        """Whether this viewer may be shown a face, and whether its file is in their vault.

        None for an unknown track or a file this viewer may not open, alike.
        """
        track = await self._store.track(track_id)
        if track is None:
            return None
        view = await self._repository.get_asset(viewer, track.asset_id)
        if view is None or not await self._repository.can_view(viewer, track.asset_id):
            return None
        return FaceVisibility(concealed=view.concealed)

    async def may_see_crop(self, viewer: Viewer, track_id: str) -> bool:
        """Whether this viewer may be shown a face from a file: the file's own question, asked."""
        track = await self._store.track(track_id)
        if track is None:
            return False
        return await self._repository.can_view(viewer, track.asset_id)
