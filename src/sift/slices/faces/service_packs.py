# SPDX-License-Identifier: AGPL-3.0-or-later
"""Face packs: making one from People already in this library, and taking one in as held entries
that the library's faces place later (`service_fingerprints`).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sift.kernel.log import get_logger
from sift.slices.faces import crop as cropping
from sift.slices.faces import packs, recognize, weights
from sift.slices.faces.models import Origin
from sift.slices.faces.recognize import Recognizer
from sift.slices.faces.service_weights import WeightsMixin

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AliasClash:
    """One name a pack wanted to give somebody, which somebody else already answers to.

    Both names are carried, because the report is what whoever imported the pack has to decide
    from, and "the alias 'Ada' is taken" without saying by whom is not something anybody can act
    on.
    """

    alias: str
    wanted_by: str
    held_by: str


@dataclass(frozen=True, slots=True)
class PackOutcome:
    """What taking in a pack did.

    `added` is how many faces it brought that were not held already; `held` the names they are
    held under, waiting for faces in the library to match them. `alias_clashes` is filled by a
    swap's door alone, the one that adds to somebody here: a fingerprints file adds nobody's names.
    """

    added: int
    held: list[str]
    alias_clashes: list[AliasClash]


@dataclass(frozen=True, slots=True)
class HeldEntry:
    """Somebody waiting for a matching face whom a facial fingerprints file can carry."""

    entry_id: str
    name: str


class PacksMixin(WeightsMixin):
    """Making and taking in face packs."""

    async def _catalog_model(self) -> weights.Weight:
        """The face model in use, read from the catalog: the stored numbers need no model loaded."""
        configured = await self.configuration()
        return weights.pairing(configured.family)[1]

    async def shareable_people(self) -> list[str]:
        """Everybody Sift can recognize by a face somebody chose: who "Share everyone" means.

        Whoever is asking: which of them a viewer may be shown is the route's question.
        """
        await self._require_enabled()
        model = await self._catalog_model()
        return await self._store.people_with_chosen_references(model.revision)

    async def held_for_export(self) -> list[HeldEntry]:
        """Everybody waiting for a matching face who has a face the model in use made, by name."""
        model = await self._catalog_model()
        return [
            HeldEntry(entry_id=str(row["entry_id"]), name=str(row["name"]))
            for row in await self._store.held_for_export(model.revision)
        ]

    async def people_named(self, names: Sequence[str]) -> dict[str, list[str]]:
        """The People called each of these names, ignoring case, keyed by the folded name."""
        return await self._store.people_named(names)

    async def export_pack(
        self,
        *,
        name: str,
        version: str,
        person_ids: Sequence[str],
        include_pictures: bool,
        entry_ids: Sequence[str] = (),
    ) -> bytes:
        """Make a pack from People here and from the people waiting for a matching face.

        Neither list named means everybody Sift can recognize and everybody waiting. A person and
        an entry of one name are one person in the file.
        """
        await self._require_enabled()
        model = await self._catalog_model()
        chosen = list(person_ids)
        wanted = list(entry_ids)
        if not chosen and not wanted:
            chosen = await self._store.people_with_chosen_references(model.revision)
            wanted = [one.entry_id for one in await self.held_for_export()]

        people: list[packs.PackedPerson] = []
        for person_id in chosen:
            # Never a STARTER. A pack is read back as references somebody chose (`Origin.PACK`), so
            # a stash-box's photo carried out in one would come home trusted to name her.
            references = [
                one
                for one in await self._store.references(person_id)
                if one.origin is not Origin.SEED
            ]
            if not references:
                continue
            faces = []
            for reference in references:
                picture = (
                    await self._store.reference_picture(reference.id) if include_pictures else None
                )
                faces.append(
                    packs.PackedFace(
                        digest=reference.crop_digest,
                        quality=reference.quality,
                        vector=reference.vector,
                        picture=picture,
                    )
                )
            people.append(
                packs.PackedPerson(
                    name=await self._store.person_name(person_id) or person_id,
                    # The other names they answer to travel with them. Without this a pack made
                    # here and read back somewhere else would quietly lose them, and the import
                    # side has to merge aliases anyway, so leaving them out would mean the only
                    # packs that ever carried any were the ones written by hand.
                    aliases=tuple(await self._store.aliases_of(person_id)),
                    links=(),
                    faces=tuple(faces),
                    # Her confirmed count here, the number her page bands, for the taking side.
                    confirmed=len(references),
                )
            )
        people = await self._with_entries(people, wanted, model.revision, include_pictures)
        return packs.build(
            name=name,
            version=version,
            recognizer=model.revision,
            dimension=model.dimension,
            people=people,
            include_pictures=include_pictures,
            library=await self._store.own_library(),
        )

    async def _with_entries(
        self,
        people: list[packs.PackedPerson],
        entry_ids: Sequence[str],
        recognizer: str,
        include_pictures: bool,
    ) -> list[packs.PackedPerson]:
        """`people` with each of these waiting entries added, joined to anybody of the same name."""
        held = {str(row["entry_id"]): row for row in await self._store.held_for_export(recognizer)}
        rows = list(people)
        at = {person.name.casefold(): index for index, person in reversed(list(enumerate(rows)))}
        for entry_id in entry_ids:
            entry = held.get(entry_id)
            if entry is None:
                continue
            faces = [
                packs.PackedFace(
                    digest=str(face["crop_digest"]),
                    quality=float(face["quality"]),
                    vector=recognize.unpack(bytes(face["embedding"])),
                    picture=(
                        await self._store.picture_bytes(face["crop_path"])
                        if include_pictures
                        else None
                    ),
                )
                for face in await self._store.entry_faces(entry_id)
                if face["recognizer"] == recognizer
            ]
            waiting = packs.PackedPerson(
                name=str(entry["name"]),
                aliases=tuple(json.loads(entry["aliases"] or "[]")),
                links=tuple(json.loads(entry["links"] or "[]")),
                faces=tuple(faces),
                confirmed=entry["confirmed"],
            )
            index = at.setdefault(waiting.name.casefold(), len(rows))
            if index == len(rows):
                rows.append(waiting)
            else:
                rows[index] = _joined(rows[index], waiting)
        return rows

    async def descriptions_for_swap(
        self, person_ids: Sequence[str]
    ) -> tuple[str, int, dict[str, list[tuple[str, float, Sequence[float]]]]] | None:
        """The descriptions a pack would carry for each of these People, without the pictures and
        without loading a model: the recognizer's revision and dimension from the catalog, and per
        person the references somebody chose (never a starter, as `export_pack` never carries one).
        None when faces are off. What a swap's offer is made from; the offer decides how many."""
        if not await self.enabled():
            return None
        configured = await self.configuration()
        _, recognizer = weights.pairing(configured.family)
        found: dict[str, list[tuple[str, float, Sequence[float]]]] = {}
        for person_id in person_ids:
            references = [
                one
                for one in await self._store.references(person_id)
                if one.origin is not Origin.SEED
            ]
            if references:
                found[person_id] = [
                    (one.crop_digest, one.quality, one.vector) for one in references
                ]
        return recognizer.revision, recognizer.dimension, found

    async def pictures_for_swap(self, person_ids: Sequence[str], *, best: int) -> dict[str, bytes]:
        """The face pictures behind the fingerprints `descriptions_for_swap` reads, keyed by the
        picture's digest: the `best` by quality for each person, the ones a swap sends. Asked for
        only when the other side uses the other face model, so it can describe them itself. A
        reference whose picture has gone is simply absent."""
        if not await self.enabled():
            return {}
        found: dict[str, bytes] = {}
        for person_id in person_ids:
            references = sorted(
                (
                    one
                    for one in await self._store.references(person_id)
                    if one.origin is not Origin.SEED
                ),
                key=lambda one: (-one.quality, one.crop_digest),
            )[:best]
            for reference in references:
                picture = await self._store.reference_picture(reference.id)
                if picture:
                    found[reference.crop_digest] = picture
        return found

    async def import_pack(
        self, raw: bytes, *, other_model: bool = False, while_off: bool = False
    ) -> PackOutcome:
        """Take in a facial fingerprints file: every person it names is held as an entry with
        their faces and the other names they answer to, and nothing else.

        **No person is made, given anything or asked about here**, whatever People holds by the
        same name. The pass that follows (`FingerprintsMixin.recognize_from_fingerprints`, asked
        for by the route) places each entry by face: with the person whose own pictures match the
        faces it matches, or as a new person. So an import of six hundred people asks nothing.

        `while_off` takes it in with recognition switched off: the entries wait, and nothing is
        recognized until the switch is on. It is the file's own door, so a new library can be given
        its people before anybody has agreed to faces being measured. Only a pack made with the
        other model is refused then, because describing its pictures again needs the model itself.

        `other_model` takes a pack made with the OTHER face model when its faces came with their
        pictures (`packs.read`): each picture is described again with this machine's model and the
        numbers that came with it are thrown away; a face with no picture is left out.

        Importing the same pack twice holds nothing the second time: an entry is keyed by the pack
        and the name, and each face by its picture. A later edition replaces the earlier one.
        """
        pack, pack_id = await self._take_in(raw, other_model=other_model, while_off=while_off)
        # A library's pack is keyed by its id, so each entry says the file's name.
        source = pack.name if pack.library else None
        added = 0
        for person in pack.people:
            added += await self._keep_unplaced(pack_id, person, source=source)
        log.info("faces.pack.imported", added=added, held=len(pack.people))
        return PackOutcome(
            added=added, held=[person.name for person in pack.people], alias_clashes=[]
        )

    async def take_from_swap(
        self, raw: bytes, *, suggest_only: bool, other_model: bool = False
    ) -> PackOutcome:
        """Take in the faces a swap brought for one person.

        The swap has already put the file on a person here, by name: one it made in this library
        moments ago (`suggest_only` false) has nothing of her own yet, so the faces are her
        references at once. One this library already had gets them held, never as references, until somebody
        gives them to her from her own page (`claim_for`): they are another install's say-so
        about somebody we know. Neither is ever placed by the pass over facial fingerprints
        (`Store.SWAPPED_PACKS`).
        """
        pack, pack_id = await self._take_in(raw, other_model=other_model, while_off=False)
        known = await self._store.existing_people(person.name for person in pack.people)
        added = 0
        held: list[str] = []
        clashes: list[AliasClash] = []
        for person in pack.people:
            person_id = known.get(person.name.casefold())
            if person_id is None or suggest_only:
                held.append(person.name)
                await self._keep_unplaced(pack_id, person)
                continue
            clashes.extend(await self._merge_aliases(person_id, person))
            added += await self._store.add_references(
                person_id,
                [
                    (face.vector, face.quality, face.picture, face.digest, None)
                    for face in person.faces
                ],
                origin=Origin.PACK,
                recognizer=pack.recognizer,
                pack_id=pack_id,
            )
        log.info("faces.pack.swapped", added=added, held=len(held), clashes=len(clashes))
        return PackOutcome(added=added, held=held, alias_clashes=clashes)

    async def _take_in(
        self, raw: bytes, *, other_model: bool, while_off: bool
    ) -> tuple[packs.Pack, str]:
        """Read a pack, describe its pictures again where it was made with the other model, and
        record it. Returns the pack as this machine reads it and its id."""
        if not while_off:
            await self._require_enabled()
        configured = await self.configuration()
        # The catalog's revision rather than the loaded model's: reading a pack's numbers and
        # storing them loads nothing, so it works switched off and before the models are fetched.
        _, expected = weights.pairing(configured.family)
        pack = packs.read(raw, expect_recognizer=expected.revision, other_model=other_model)
        if pack.recognizer != expected.revision:
            await self._require_enabled()
            _, recognizer = await self._models(configured)
            pack = await self._described_here(pack, recognizer)
        pack_id = await self._store.record_pack(
            name=pack.name,
            version=pack.version,
            recognizer=pack.recognizer,
            dimension=pack.dimension,
            digest=pack.digest,
            library=pack.library,
        )
        return pack, pack_id

    async def _described_here(self, pack: packs.Pack, recognizer: Recognizer) -> packs.Pack:
        """The pack with every pictured face described by this machine's model and every face
        without a picture left out. One person at a time, so a picture that cannot be read costs
        that person's faces (said in the log) and not everybody's: the decoder refuses a partial
        answer, so which face lost its numbers would otherwise be unknown."""
        people: list[packs.PackedPerson] = []
        for person in pack.people:
            pictured = [face for face in person.faces if face.picture]
            described: list[packs.PackedFace] = []
            if pictured:
                try:
                    chips = await cropping.decode(
                        [face.picture or b"" for face in pictured], self._settings
                    )
                    answers = await asyncio.to_thread(recognizer.embed_many, chips)
                except Exception as error:
                    log.warning("faces.pack.pictures_unreadable", reason=type(error).__name__)
                    answers = []
                described = [
                    replace(face, vector=answer.vector)
                    for face, answer in zip(pictured, answers, strict=False)
                ]
            people.append(replace(person, faces=tuple(described)))
        log.info(
            "faces.pack.described_here",
            theirs=pack.recognizer,
            ours=recognizer.revision,
            faces=sum(len(person.faces) for person in people),
        )
        return replace(
            pack,
            recognizer=recognizer.revision,
            dimension=recognizer.dimension,
            people=tuple(people),
        )

    async def _keep_unplaced(
        self, pack_id: str, person: packs.PackedPerson, *, source: str | None = None
    ) -> int:
        """Hold somebody a pack named as an entry, with every face and every other name they
        answer to; re-importing the same pack writes nothing. Returns how many faces were new."""
        entry_id = await self._store.keep_pack_entry(
            pack_id=pack_id,
            name=person.name,
            aliases=person.aliases,
            links=person.links,
            source=source,
            confirmed=person.confirmed,
        )
        recognizer = await self._recognizer_of(pack_id)
        held = 0
        for face in person.faces:
            face_id = await self._store.keep_entry_face(
                entry_id,
                vector=face.vector,
                quality=face.quality,
                crop=face.picture,
                digest=face.digest,
                recognizer=recognizer,
            )
            held += face_id is not None
        return held

    async def _recognizer_of(self, pack_id: str) -> str:
        row = await self._store.pack_recognizer(pack_id)
        return row or ""

    async def _merge_aliases(self, person_id: str, person: packs.PackedPerson) -> list[AliasClash]:
        """Give somebody the other names the pack says they answer to. Reports what could not be.

        Checked against who holds the name before writing, rather than relying on the table to
        refuse it. The table is unique per person and alias, so the same word on a SECOND person is
        a perfectly legal row, which is exactly the state that has to be prevented, and the only
        way to prevent it is to look.

        A person's own name is not added as an alias of themselves: it is already how they are
        found, and a row saying somebody is also called what they are called is noise in a table
        whose whole purpose is to be read.
        """
        clashes: list[AliasClash] = []
        for alias in person.aliases:
            if alias.casefold() == person.name.casefold():
                continue
            owner = await self._store.alias_owner(alias)
            if owner is not None and owner != person_id:
                clashes.append(
                    AliasClash(
                        alias=alias,
                        wanted_by=person.name,
                        held_by=await self._store.person_name(owner) or owner,
                    )
                )
                continue
            await self._store.add_alias(person_id, alias)
        return clashes


def _joined(person: packs.PackedPerson, more: packs.PackedPerson) -> packs.PackedPerson:
    """One person from two of the same name: faces by picture, other names and links merged, and
    the first one's confirmed count."""
    digests = {face.digest for face in person.faces}
    own = person.name.casefold()
    return replace(
        person,
        aliases=tuple(
            dict.fromkeys(
                alias for alias in (*person.aliases, *more.aliases) if alias.casefold() != own
            )
        ),
        links=tuple(dict.fromkeys((*person.links, *more.links))),
        faces=(*person.faces, *(face for face in more.faces if face.digest not in digests)),
    )
