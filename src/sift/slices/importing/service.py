# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a piece of work runs on a file that has just arrived.

One question, asked per file, answered from three places in order: the group's master switch, the
switch for that particular piece of work, and whatever the folder the file landed in says instead.

**Why a tuple of keys rather than one.** A piece of work can depend on more than one switch:
`semantic_describe` needs Smart Search on as well as its own switch, and `face_scan` needs
recognition. Gated on one key, such a job is still written, claimed, run and recorded once per
file, for ever, while its handler returns having done nothing. Asking for every key a piece of work
depends on is the same mechanism doing the whole job instead of most of it.

**Why the folder can only be asked about a key the library already has.** An override answers one of
the switches on the Importing screen; it is not a place to invent a new one. So the keys come from
the map below and a folder either agrees with the library or gives a different answer to the same
question.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.content import ContentStore, Within, wanted_outside
from sift.kernel.log import get_logger
from sift.kernel.seams import SettingsSeam
from sift.kernel.settings_registry import get_retired
from sift.kernel.wiring import Part
from sift.slices.importing.store import RootPreferences

log = get_logger(__name__)

SERVICE: Part[ImportPolicy] = Part("importing")


class ImportPolicy:
    """What runs when a file arrives, for the whole library and for one folder.

    `gates` maps a job type to every setting key that has to be on for it. It is built by the
    composition root, which is the only place that knows both the job types and the settings that
    govern them: this feature must not learn that face recognition exists.
    """

    def __init__(
        self,
        *,
        settings: SettingsSeam,
        content: ContentStore,
        roots: RootPreferences,
        gates: Mapping[str, Sequence[str]],
        overridable: Sequence[str],
    ) -> None:
        self._settings = settings
        self._content = content
        self._roots = roots
        self._gates = {job: tuple(keys) for job, keys in gates.items()}
        self._overridable = tuple(overridable)

    @property
    def gates(self) -> Mapping[str, Sequence[str]]:
        """Which keys govern which job. Read by the screen, so it can draw the same groups."""
        return self._gates

    def keys(self) -> tuple[str, ...]:
        """Every key any piece of import work depends on, in no particular order and once each."""
        seen: dict[str, None] = {}
        for keys in self._gates.values():
            for key in keys:
                seen.setdefault(key, None)
        return tuple(seen)

    def overridable(self) -> tuple[str, ...]:
        """Which of those a single folder may answer differently.

        NOT all of them, and the difference is the kind of question each one asks. "Build hover
        previews" is about work done to files, and doing it for one folder and not another is an
        ordinary thing to want. "Recognize faces in your library" is about whether the feature
        exists at all: it decides whether models are downloaded and whether the Identify screens
        are there, and a folder answering that would be a folder turning a feature on for
        everybody, or off for itself while every screen still offered it.

        Named from outside rather than worked out here: which keys are a feature's own consent is
        something the composition root knows and this does not.
        """
        gated = set(self.keys())
        return tuple(key for key in self._overridable if key in gated)

    async def allows(
        self, job_type: str, asset_id: str | None = None, *, pressed: bool = False
    ) -> bool:
        """Whether this work should be started for this file.

        A job type nothing governs is always allowed. That is what keeps this from becoming a list
        every new job has to be added to before it will run at all. `pressed` is a person asking
        for it. See `refused_by`.
        """
        return await self.refused_by(job_type, asset_id, pressed=pressed) is None

    async def refused_by(
        self, job_type: str, asset_id: str | None = None, *, pressed: bool = False
    ) -> str | None:
        """The first switch that says no to this work for this file, or None where every one says
        yes. The same answer `allows` gives, with the reason kept, so a press that is refused can
        name the switch: one rule read by both, never a second copy of it.

        A PRESS IS NOT ASKED WHETHER THE WORK STARTS ON ITS OWN. Some of these keys answer "does
        this happen as a file arrives" (Generate's and Identify's masters, recognition's, Smart
        Search's, the watermarks' and the music fingerprints' own) and each of those is a task's
        When now (a retired key, read through it). Somebody pressing Run now, the Build or a file's
        Run task has answered that question for themselves, so for a press those keys are passed
        over and only the ones that say WHAT is made are asked: whether a feature is on at all,
        and which pictures. Refusing a press with "switched off for this file" would be wrong.
        """
        keys = self._asked(job_type, pressed=pressed)
        if not keys:
            return None
        overrides = await self._overrides_for(asset_id)
        return await self._first_refusal(job_type, keys, overrides, asset_id=asset_id)

    async def allows_for_root(self, job_type: str, root_id: str) -> bool:
        """Whether this work starts for a file ARRIVING in this library root, asked before the file
        is in it.

        `allows` reads a folder's answer through the places a FILE sits, and at staging (the
        moment a download, a drop, a paste or an upload is still on this device's own disk, which
        is the one cheap moment to read it end to end), the file sits nowhere yet. What is known
        is where it is going: the destination the request named. So the destination is asked
        directly, with the same rule `allows` applies once the file has arrived: the folder's own
        answer where it gave one, the library's where it follows the library. One folder, so the
        EITHER rule of `_on` is simply that folder's word.

        Never a press: this is the arrival's question, so every key is asked, the Whens included.
        """
        keys = self._asked(job_type, pressed=False)
        if not keys:
            return True
        found = await self._roots.for_roots([root_id])
        refused = await self._first_refusal(job_type, keys, list(found.values()), root_id=root_id)
        return refused is None

    def _asked(self, job_type: str, *, pressed: bool) -> tuple[str, ...]:
        """The keys a piece of work is asked about: all of them for an arrival, and for a press only
        the ones that say WHAT is made. See `refused_by`."""
        keys = self._gates.get(job_type, ())
        if pressed:
            return tuple(key for key in keys if get_retired(key) is None)
        return tuple(keys)

    async def _first_refusal(
        self,
        job_type: str,
        keys: Sequence[str],
        overrides: Sequence[Mapping[str, object]],
        **about: str | None,
    ) -> str | None:
        """The first of these keys the folders' answers (or the library's) say no to, or None."""
        for key in keys:
            if not await self._on(key, overrides):
                log.info("importing.skipped", job=job_type, because=key, **about)
                return key
        return None

    async def folder_term(self, job_type: str) -> Within | None:
        """The files this work is still wanted for once folders that refuse it are counted. None
        when no folder refuses it (nearly always), so the count it narrows pays nothing.

        The same rule `allows` applies to one file, as a condition over them all: a count of what
        is lacking that ignored it would say the Music card had work to do in a folder that had said
        no to music, and draw a bar that could never reach its end. It assumes the library's own answer is
        yes, which is the only case asked: a product whose library switch is off has no `Lack` to
        narrow and counts nothing.

        As a PRESS is asked, not as an arrival: the count narrows what the Build and the cards say
        is left, and a press reads those files, so a folder's answer to a When (the music
        fingerprints' retired key, the others) is passed over here exactly as `refused_by` passes
        it over for a press. Counting a folder out that the press would still read is a bar that
        ends before the work does, which is the same fault as the one this docstring names, the
        other way round.
        """
        keys = self._asked(job_type, pressed=True)
        if not keys:
            return None
        return wanted_outside([await self._roots.refusing(key) for key in keys])

    async def _on(self, key: str, overrides: Sequence[Mapping[str, object]]) -> bool:
        """One key's answer, taking a folder's word over the library's.

        **A file that sits in two folders is generated for if EITHER of them allows it.** The
        alternative refuses work that was asked for in one of the two places the file was put, and
        there is only one derivative between them, so a refusal in one folder would be felt in the
        other, which is not what turning a switch off in one folder can reasonably mean.

        **EITHER is over every folder the file sits in, and a folder with no answer of its own
        answers with the library's.** Following the library is an answer, not an abstention.
        Collecting the stored overrides alone would be the refusal the paragraph above rejects: with
        one folder overridden to no and the other following a library yes, the only answer collected
        would be the no. The library's answer is read only when some folder is following it, so a
        file whose folders all answer costs no settings read.
        """
        answered = [bool(one[key]) for one in overrides if key in one]
        if any(answered):
            return True
        if answered and len(answered) == len(overrides):
            return False
        return bool(await self._settings.get_app(key))

    async def _overrides_for(self, asset_id: str | None) -> list[Mapping[str, object]]:
        """What every folder holding this file says, one entry per folder and `{}` where the folder
        follows the library. Empty for work that is not about one file.

        The empty ones matter, and they are the store's to supply: `for_roots` answers for every
        folder it is asked about, so the length of this list is the number of folders the file sits
        in. `_on` counts folders rather than stored answers, and a folder missing from here is a
        folder whose say in the EITHER rule is silently lost. Filling the gaps here as well would
        be the same rule written in two places, either of which could then stop working alone.
        """
        if asset_id is None:
            return []
        places = await self._content.locations(asset_id)
        if not places:
            return []
        found = await self._roots.for_roots(place.root_id for place in places)
        return list(found.values())
