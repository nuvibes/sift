# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one viewer may be shown of a face: the file it is in, the person on it, and its picture.

Every screen that draws a face goes through `_sighting`, so the rule that withholds a hidden
person's name from a face lives in one place.
"""

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
    """What a route serving a face picture needs beyond "yes".

    `concealed` is whether the file this face came from is in the asking user's vault. It is
    reachable at all only because the vault is open, and it decides one thing: a concealed picture
    is never given an address a browser may keep, so shutting the vault takes effect on the very
    next use rather than whenever a stored copy happens to expire.
    """

    concealed: bool


@dataclass(frozen=True, slots=True)
class Sighting:
    """One appearance, as a screen reads it, already settled against who is asking.

    `person_id` and `person_name` are absent together, and that is the shape rather than an
    accident: an appearance attributed to somebody this viewer has hidden comes back with the face
    and without the name. Handing over the id and withholding the name would be handing over the
    fact that a hidden person is in this file, which is what the name was being withheld for.
    """

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
        """The user a queued job is acting for, or None if it has gone.

        A job outlives the request that started it, so the user it names may have been deleted
        or turned off in between. Resolved through the access layer, which is what decides what
        that user can see: the job holds an id and never an answer.

        **Hidden files are included, and that is the one deliberate difference from a request.**
        Whether the vault is open is a fact about a browsing session, and a job has no session,
        so a sweep rebuilt from a user id alone would always run with it shut, and a hidden file
        would never be scanned no matter what the person who pressed the button was looking at.
        Unlocking could not help: by the time the job runs, the session it belonged to is gone.

        Including them changes what gets *recognized*, not what gets *shown*. Every screen settles
        its faces against whoever is asking, one appearance at a time, so a face found in a hidden
        file is absent from every pile, count and cover while the vault is shut, exactly as the
        file itself is. What it stops is a file being permanently invisible to the machinery.
        """
        return await self._repository.load_viewer(user_id, show_hidden=True)

    async def name_of(self, viewer: Viewer, person_id: str) -> str | None:
        """What this viewer may call one person, or None where they may not be told.

        Not taken off the page's first face, so a narrowing with no faces in it still has a name to
        give. The same permission question `_names_of` asks for a page of tracks, asked for one
        person the page is about.
        """
        found = await self._repository.visible_person(viewer, person_id)
        return found.name if found is not None else None

    def _reveals_existence(self, viewer: Viewer) -> bool:
        """Whether concealed files come back for this viewer at all: an open vault, or locked
        placeholders asked for. The rule `standing_of` applies to each face's file, which is what
        makes a stored count agree with the faces the same screen draws.

        The kernel's answer (`viewer.reveals_existence`), so this is not a second copy of the
        repository's rule."""
        return reveals_existence(viewer)

    async def may_see_person(self, viewer: Viewer, person_id: str) -> bool:
        """Whether this user may act on this person at all. A vaulted one answers no."""
        return await self._repository.visible_person(viewer, person_id) is not None

    async def touchable_faces(self, viewer: Viewer, track_ids: Sequence[str]) -> Actionable:
        """Which of these faces this user may act on, and which two reasons the rest fell to.

        Not a plain yes-or-no about ALL of them: a No that refused the whole call would make naming
        forty faces of which one sat in the vault name none of them. A partial success is fine when
        it says which part succeeded; see `sift.kernel.reach` for that answer, which every bulk
        write in the app gives.

        Over TRACK ids, decided on the file each face is a face IN, because that is where the
        permission lives. A track whose row has gone is refused: there is nothing to write against.

        Strict: `actionable_of` mirrors `open_asset`, so a locked placeholder is
        not enough to act on. The picture behind the decision is the thing being withheld.
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
        """Which of these files this viewer may be shown something about, and whether that
        something is a LOCKED PLACEHOLDER rather than the picture.

        The distinction the pile screens need, and it is the same one the grid already makes.
        Placeholder concealment mode keeps a tile for a file in the vault (there is something
        here and it does not say what), while the crop route refuses the picture, exactly as it
        refuses the file. Each is right alone; together, a screen that asked only "may I show this"
        would draw broken pictures where the padlocks should be.

        So the answer is two-valued. True means draw the lock this app draws everywhere else, and
        do not ask for the crop. Absent means the file is not there for them at all, which is the
        default mode's answer and stays an absence.

        Genuinely unlocking the vault clears every flag: then the pictures are theirs to see, and
        this says so.

        ## `standing_of` and not a page of them

        Asking for a page of `len(wanted)` rows looks like asking about all of them and is not: a
        page is capped, so everything past the cap comes back as "you may not see this", the same
        answer a real refusal gives, and therefore invisible, and the faces on those files would
        leave every screen this scopes. A membership question has no cap, because the caller's own
        list is what bounds it.

        Empty in, empty out is the repository's, not restated here.
        """
        found = await self._repository.standing_of(viewer, asset_ids)
        if self._repository.reveals_named_rows(viewer):
            return dict.fromkeys(found, False)
        return found

    async def _names_of(
        self, viewer: Viewer, tracks: Sequence[StoredTrack]
    ) -> dict[str, str | None]:
        """What each person on these tracks is called, as far as THIS viewer is concerned.

        The cache `_sighting` reads, filled in one question rather than one per person. Asked per
        person and cached, a few hundred faces come down to a handful of reads, and the handful is
        still the expensive part, because a name costs a whole resolve of the People wall however
        few are asked for: on the Identified screen, one resolve per name would be most of the time
        the screen takes.

        A person this viewer may not be told about is present with no name, which is what makes
        this a cache and not a lookup: `_sighting` reads a MISSING key as "not asked yet" and would
        go and ask, one at a time, for exactly the people this is keeping back.

        Scoped to one call and one viewer, as it has to be: the answer is a permission decision, so
        a cache that outlived the request would be one user's view of the library answering
        another user's question.
        """
        return await self._names_for(
            viewer, [track.person_id for track in tracks if track.person_id is not None]
        )

    async def _names_for(self, viewer: Viewer, person_ids: Sequence[str]) -> dict[str, str | None]:
        """`_names_of` asked of the people themselves, for a caller that has their ids already.

        The same cache in the same shape (every id present, None for a person this viewer may not
        be told about), so `_sighting` never falls back to asking one person at a time. See
        `_known_cards`, which is why this exists: it knows who it is about to read before it
        has read a single face.
        """
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

        **A locked face is not named.** With "Show a locked tile" on,
        a face on a file in the shut vault is drawn as a padlock, and a padlock beside "Alice" on
        a "who is this" screen tells anybody that a hidden file exists and Alice is in it. So the
        person is withheld from a locked face exactly as from a face whose person is hidden: no
        id, no name, nothing about them. This is the ONE place that rule lives, so every screen that
        draws a face gets it without asking; the two that keep the name (the person's own page,
        and a file's own face list, which never passes `locked` because the file is open there)
        say so with `named_when_locked`.

        `names` is a cache of "what is this person called, as far as THIS viewer is concerned",
        held for the length of one gather. Without it the screens that read a few hundred faces
        would ask that question once per face, of the same handful of people, hundreds of times.

        It is scoped to one call and one viewer deliberately, and it must stay that way: the answer
        is a permission decision, so a cache that outlived the request would be one user's view
        of the library answering another user's question.

        `moments` is when each appearance's picture was taken (`Store.picture_moments`), read once
        for the whole gather. REQUIRED rather than defaulted: a screen that forgot it would play a
        pressed face from the wrong moment, silently, and a missing argument is an error immediately.
        An appearance absent from it has no face left to picture and plays from where it began.
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
            # Only for a face with nothing on it. A face whose person was withheld above has every
            # other field about that person withheld too, and the pile is a resemblance fact about
            # the same person, so it is withheld on the raw column rather than on `named`, which
            # reads None for both an unnamed face and a concealed one.
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

        One call for a route to make rather than two, so that no surface serving a face picture can
        get the permission right and the caching rule from somewhere else. It is not one read: the
        answer is the same `can_view` `may_see_crop` asks, and the concealment flag comes from the
        scoped row beside it. The strictness is `can_view`'s alone: `get_asset` is the looser of
        the two, and is here for the flag rather than for the decision.

        None means no: an unknown track, or one from a file this viewer may not open. The caller
        cannot tell those apart and must not, for the reason `may_see_crop` gives.
        """
        track = await self._store.track(track_id)
        if track is None:
            return None
        view = await self._repository.get_asset(viewer, track.asset_id)
        if view is None or not await self._repository.can_view(viewer, track.asset_id):
            return None
        return FaceVisibility(concealed=view.concealed)

    async def may_see_crop(self, viewer: Viewer, track_id: str) -> bool:
        """Whether this viewer may be shown a face taken from a file.

        A crop is a fragment of the file it came from, so it asks the same question the file does,
        of the same rule. Asked here, never re-implemented: a second copy of the concealment rule
        would be a way around the first one the day they drifted, and this surface would be the way
        around it.
        """
        track = await self._store.track(track_id)
        if track is None:
            return False
        return await self._repository.can_view(viewer, track.asset_id)
