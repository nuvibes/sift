# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sharing a curated set of reference faces, and taking one in.

**Nobody writes a manifest.** Everything Sift can work out (the model, the counts, the dates, the
digests, the quality of each picture) it works out. What is left is what no program can derive:
the other names a person is known by, and links to where their content is. Those come from an
optional spreadsheet dropped beside the folders, in the same four columns a list of people already
has, and a set with no spreadsheet is perfectly valid.

**Scope: people, their other names, their links, and reference faces. Nothing else.** Tags and
collections were considered for the same container and deliberately left out. Do not generalise it.

Two properties, and they are mechanics rather than intentions:

**Importing the same pack twice writes nothing the second time.** Every reference is keyed by the
identity of its picture, so one that is already held is skipped whatever it arrived in. Importing a
*later edition* updates what the earlier one brought, because a pack carries a name and the name is
what an edition replaces.

**Descriptions by default, pictures only if the maintainer opts in.** A description cannot be turned
back into a photograph of somebody, so a pack of numbers hands on far less about real people than a
pack of pictures does. The cost is that a pack is bound to the model that made it, so the manifest
says which, and **a pack made by a different model is refused rather than silently compared**.
Numbers from two different models are not merely less accurate together; they are meaningless
together, and the failure would look like poor matching rather than like a mistake.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from blake3 import blake3

from sift.slices.faces import recognize
from sift.slices.faces.models import Vector

#: The format's own version, so a pack made by a much older or newer Sift can be recognized as such
#: rather than half-read. Version 2 gives each person how many confirmed faces they had where the
#: file was made (`PackedPerson.confirmed`); a version 1 file is still read, without it.
FORMAT = 2

#: Every version this Sift reads.
READABLE = frozenset({1, 2})

MANIFEST = "manifest.json"
VECTORS = "vectors.bin"
PICTURES = "pictures"


class PackError(Exception):
    """A pack cannot be read, or does not belong here. The message is written to be shown, in the
    words the screen uses: a pack is "a file of facial fingerprints" to anybody reading it."""


#: What every file that is not one of these is told.
NOT_ONE = "That file isn't a file of facial fingerprints."


@dataclass(frozen=True, slots=True)
class PackedFace:
    """One reference face inside a pack."""

    digest: str
    quality: float
    vector: Vector
    picture: bytes | None


@dataclass(frozen=True, slots=True)
class PackedPerson:
    """One person inside a pack."""

    name: str
    aliases: tuple[str, ...]
    links: tuple[str, ...]
    faces: tuple[PackedFace, ...]
    #: How many confirmed faces they had in the library the file was made from, so the side that
    #: takes it in can say how surely they will be recognized. None from a file that never said.
    confirmed: int | None = None


@dataclass(frozen=True, slots=True)
class Pack:
    """A whole pack, read into memory. They are small: a few thousand descriptions is megabytes."""

    name: str
    version: str
    recognizer: str
    dimension: int
    people: tuple[PackedPerson, ...]
    digest: str

    @property
    def face_count(self) -> int:
        return sum(len(person.faces) for person in self.people)


def build(
    *,
    name: str,
    version: str,
    recognizer: str,
    dimension: int,
    people: list[PackedPerson],
    include_pictures: bool,
) -> bytes:
    """Write a pack.

    The descriptions go in one block in the order the manifest lists them rather than one entry per
    face: a few thousand of them as text would be several times the size and would have to be parsed
    number by number.
    """
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "name": name,
        "version": version,
        "recognizer": recognizer,
        "dimension": dimension,
        "pictures": include_pictures,
        "people": [],
    }
    block = bytearray()
    for person in people:
        entry: dict[str, Any] = {
            "name": person.name,
            "aliases": list(person.aliases),
            "links": list(person.links),
            "faces": [],
        }
        if person.confirmed is not None:
            entry["confirmed"] = person.confirmed
        for face in person.faces:
            if len(face.vector) != dimension:
                raise PackError(
                    "a face in this set has a different number of values from the rest. More "
                    "than one model made the set, so it can't be shared"
                )
            entry["faces"].append({"digest": face.digest, "quality": round(face.quality, 4)})
            block.extend(recognize.pack(face.vector))
        manifest["people"].append(entry)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
        bundle.writestr(VECTORS, bytes(block))
        if include_pictures:
            for person in people:
                for face in person.faces:
                    if face.picture is not None:
                        bundle.writestr(f"{PICTURES}/{face.digest}.jpg", face.picture)
    return buffer.getvalue()


def read(raw: bytes, *, expect_recognizer: str, other_model: bool = False) -> Pack:
    """Read a pack, refusing one that does not belong on this machine.

    The model check happens before anything is unpacked and is not negotiable. Descriptions made by
    two different models occupy different spaces: comparing one against the other does not produce a
    worse answer, it produces a meaningless one, and it would show up as matching that had quietly
    stopped working rather than as an error anybody could act on.

    `other_model` is the one exception, and it is not a comparison: a swap with somebody on the
    other model sends the face pictures beside their numbers, and the caller describes those
    pictures again with this machine's model (`PacksMixin.import_pack`), throwing the numbers
    away. A file chosen on the Faces pane never asks for it.
    """
    try:
        bundle = zipfile.ZipFile(BytesIO(raw))
    except zipfile.BadZipFile as error:
        raise PackError(NOT_ONE) from error

    with bundle:
        manifest = _manifest(bundle)
        recognizer_name = str(manifest.get("recognizer") or "")
        if recognizer_name != expect_recognizer and not other_model:
            raise PackError(
                f"This file's facial fingerprints were made with the "
                f"{recognizer_name or 'unknown'} face model, and Sift is using {expect_recognizer}. "
                "Fingerprints from two models can't be compared, so nothing was added. Switch "
                "models, or ask for a file made with the one in use."
            )

        dimension = int(manifest.get("dimension") or 0)
        if dimension <= 0:
            raise PackError(
                "This file doesn't say how its faces were measured, so it can't be read."
            )

        block = bundle.read(VECTORS)
        stride = dimension * 4
        expected = sum(len(person.get("faces") or []) for person in manifest.get("people") or [])
        if len(block) != expected * stride:
            raise PackError("This file lists more or fewer faces than it holds, so it's damaged.")

        people: list[PackedPerson] = []
        offset = 0
        held = set(bundle.namelist())
        for entry in manifest.get("people") or []:
            faces: list[PackedFace] = []
            for face in entry.get("faces") or []:
                digest = str(face.get("digest") or "")
                member = f"{PICTURES}/{digest}.jpg"
                faces.append(
                    PackedFace(
                        digest=digest,
                        quality=float(face.get("quality") or 0.0),
                        vector=recognize.unpack(block[offset : offset + stride]),
                        picture=bundle.read(member) if member in held else None,
                    )
                )
                offset += stride
            people.append(
                PackedPerson(
                    name=str(entry.get("name") or "").strip(),
                    aliases=tuple(str(item) for item in entry.get("aliases") or []),
                    links=tuple(str(item) for item in entry.get("links") or []),
                    faces=tuple(faces),
                    confirmed=_count(entry.get("confirmed")),
                )
            )

    return Pack(
        name=str(manifest.get("name") or "").strip() or "unnamed",
        version=str(manifest.get("version") or "1"),
        recognizer=recognizer_name,
        dimension=dimension,
        people=tuple(people),
        digest=blake3(raw).hexdigest(),
    )


def _manifest(bundle: zipfile.ZipFile) -> dict[str, Any]:
    try:
        payload = json.loads(bundle.read(MANIFEST))
    except (KeyError, json.JSONDecodeError) as error:
        raise PackError(NOT_ONE) from error
    if not isinstance(payload, dict):
        raise PackError(NOT_ONE)
    if int(payload.get("format") or 0) not in READABLE:
        raise PackError("This file was made by a different version of Sift and can't be read.")
    return payload


def _count(value: object) -> int | None:
    """A count a file gave, or None where it gave none or something that is not one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def read_file(path: Path, *, expect_recognizer: str) -> Pack:
    return read(path.read_bytes(), expect_recognizer=expect_recognizer)
