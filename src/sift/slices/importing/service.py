# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a piece of work runs on a file that has just arrived.

Asked of every key the work depends on: the group's switch, the work's own, then the folder's word.
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
    """What runs when a file arrives, for the library and per folder; `gates` is built outside."""

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
        """Which of those a single folder may answer differently: never a feature's own consent."""
        gated = set(self.keys())
        return tuple(key for key in self._overridable if key in gated)

    async def allows(
        self, job_type: str, asset_id: str | None = None, *, pressed: bool = False
    ) -> bool:
        """Whether this work should be started for this file; work nothing governs always is."""
        return await self.refused_by(job_type, asset_id, pressed=pressed) is None

    async def refused_by(
        self, job_type: str, asset_id: str | None = None, *, pressed: bool = False
    ) -> str | None:
        """The first switch that says no to this work for this file, or None.

        A press skips the keys that only say whether work starts on its own (the Whens).
        """
        keys = self._asked(job_type, pressed=pressed)
        if not keys:
            return None
        overrides = await self._overrides_for(asset_id)
        return await self._first_refusal(job_type, keys, overrides, asset_id=asset_id)

    async def allows_for_root(self, job_type: str, root_id: str) -> bool:
        """Whether this work starts for a file arriving in this library root, before it is there."""
        keys = self._asked(job_type, pressed=False)
        if not keys:
            return True
        found = await self._roots.for_roots([root_id])
        refused = await self._first_refusal(job_type, keys, list(found.values()), root_id=root_id)
        return refused is None

    def _asked(self, job_type: str, *, pressed: bool) -> tuple[str, ...]:
        """The keys a work is asked about: all for an arrival, the WHAT keys for a press."""
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
        """The files this work is still wanted for once refusing folders are counted, as a press.

        None when no folder refuses it, so the count it narrows pays nothing.
        """
        keys = self._asked(job_type, pressed=True)
        if not keys:
            return None
        return wanted_outside([await self._roots.refusing(key) for key in keys])

    async def _on(self, key: str, overrides: Sequence[Mapping[str, object]]) -> bool:
        """One key's answer: allowed if EITHER folder holding the file allows it, a silent folder
        answering with the library's word."""
        answered = [bool(one[key]) for one in overrides if key in one]
        if any(answered):
            return True
        if answered and len(answered) == len(overrides):
            return False
        return bool(await self._settings.get_app(key))

    async def _overrides_for(self, asset_id: str | None) -> list[Mapping[str, object]]:
        """What every folder holding this file says, one per folder; `{}` follows the library."""
        if asset_id is None:
            return []
        places = await self._content.locations(asset_id)
        if not places:
            return []
        found = await self._roots.for_roots(place.root_id for place in places)
        return list(found.values())
