# SPDX-License-Identifier: AGPL-3.0-or-later
"""The models a feature can use, where to get them, and proving you got the right thing.

**No model file ships with Sift**, in the source or in the image, and that is a licence
requirement rather than a size decision: the most accurate models available are published for
non-commercial research only, and putting one inside a public image would be redistributing it
under terms nobody granted. So Sift describes them, and the person running it obtains their own
copy when they switch a feature on. `docs/model-licences.md` records the terms of each, read from
the publisher's own files.

Everything about the fetch is deliberate:

- **It never happens on its own.** Nothing here runs until somebody enables a feature and asks
  for a model.
- **It resumes.** These are hundreds of megabytes; a connection dropping at 90 % must not mean
  starting again.
- **It can be stopped**, and stopping leaves a partial file that a later attempt continues from.
- **It is verified**, by digest, before anything uses it. A model file that is not exactly the one
  described is refused rather than loaded: a truncated download does not fail loudly on its own,
  it produces a model that silently gives worse answers.
- **A local file is always an alternative.** A machine with no route to the internet must still be
  able to use the feature, and "download it on another machine and put it here" is the answer.

Files land in the device's model store rather than the cache: they are expensive to obtain, they
may have been placed there by hand, and a cache is something Sift is allowed to delete. One store
per device, beside the libraries, because a model is the same for every library that uses it.

**This is the kernel's copy, and it knows about no particular feature.** Which models exist, what
they are for and where they sit on disk are the asking feature's business: it names a place to
keep them and hands over a description of each file. What is here is the machinery every feature
that loads a model needs and none of them should write twice.
"""

from __future__ import annotations

import asyncio
import functools
import shutil
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from blake3 import blake3

from sift.kernel.config import Settings
from sift.kernel.fetch import (
    CHUNK,
    FetchFailed,
    Progress,
    SessionFactory,
    Untrusted,
    fetch_resumable,
)
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: How much of a file to read at a time when hashing it. The same size the transfer uses, and read
#: from there rather than declared again.
_CHUNK = CHUNK


class WeightError(Exception):
    """A model could not be obtained, or is not the one it claims to be. The message is for a
    person to read."""


@functools.cache
def refused_for_good() -> type[WeightError]:
    """A certificate refusal, which asking again within the second never changes: a job failure no
    retry is spent on. Made on first use because the inference process imports this module and
    never the job queue."""
    from sift.kernel.jobs.queue_rows import JobFailedPermanently

    return type("WeightRefused", (WeightError, JobFailedPermanently), {"__module__": __name__})


@dataclass(frozen=True, slots=True)
class Weight:
    """One model file: what it is, where it comes from, and how to know it arrived intact."""

    id: str
    role: str
    """What this file does within its set. The words belong to the feature (a face pass has a
    detector and a recognizer, a search index has one model that reads pictures and one that reads
    words), and this module only ever repeats the word back in a message."""
    family: str
    """Which set this belongs to. Files in one set have to be used together: they were trained
    against each other, and mixing two sets measurably degrades the answer even though nothing
    errors."""
    revision: str
    url: str
    digest: str
    size_bytes: int
    archive_member: str | None
    """Set when the publisher distributes this file only inside an archive. The archive is fetched,
    the one file is taken out of it, and the rest is discarded."""
    licence: str
    archive_bytes: int = 0
    """How much comes DOWN when this is fetched, where that is not the file itself: the size of
    the archive it travels in. Zero where the file is fetched as it is. A bar drawn against
    `size_bytes` for an archived file would sit at 100% for the last part of the download, because
    the archive is bigger than the one member taken out of it, and the switch that asks for
    consent would say the members' size, not the download's."""
    dimension: int = 0
    """How many numbers this model produces, where it produces numbers. Zero where the question
    does not apply: a detector that returns boxes has no width."""
    suffix: str = ".onnx"
    """What the file is called on disk, after its id. Defaulted because nearly everything fetched
    here is a model in one format. But not everything is a model: reading a typed sentence needs
    the vocabulary the text model was trained against, which is a data file that travels with it
    and is obtained, verified and stored exactly the same way."""


#: Re-exported rather than redeclared: a caller of this module should not have to know that the
#: transfer underneath it is the kernel's.
#:
#: Named in `__all__` rather than rebound to themselves. `Progress = Progress` reads like a
#: re-export and is not one to a type checker: it is an assignment from a name to itself, which
#: says nothing about what this module offers, and the modules that import these through here
#: would be told the attribute does not exist.
__all__ = [
    "CHUNK",
    "FetchFailed",
    "Progress",
    "SessionFactory",
    "Weight",
    "WeightError",
    "WeightStore",
    "digest_of",
]


def digest_of(path: Path) -> str:
    """The digest of a file on disk, read in pieces so a large model does not have to fit in
    memory."""
    hasher = blake3()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class WeightStore:
    """Where one feature's model files live, and everything done to them.

    A feature makes one of these with the name of its own corner of the data directory. Two
    features never share a corner, so switching one off and deleting its models cannot take
    another's with them.
    """

    def __init__(self, settings: Settings, namespace: str) -> None:
        self._settings = settings
        self._namespace = namespace

    @property
    def settings(self) -> Settings:
        """What this store was made from, for a process that has to be told where to look."""
        return self._settings

    @property
    def namespace(self) -> str:
        return self._namespace

    def directory(self) -> Path:
        """This feature's corner of the device's model store (`Settings.models_dir`).

        ONE STORE PER DEVICE, not one per library: a model gives the same answer whichever library
        asks, so every library on this device reads the same files and a new or duplicated library
        has them from its first start. Never under the cache, so a cache clean cannot take them.
        Older versions used `<data folder>/<namespace>/models`, and a one-time move (now retired)
        carried them here (`kernel.ml.store`).
        """
        return self._settings.models_dir / self._namespace

    def path_of(self, weight: Weight) -> Path:
        return self.directory() / f"{weight.id}{weight.suffix}"

    def installed(self, weight: Weight) -> bool:
        """Whether the file is there. Says nothing about whether it is the right one: `verify`
        does that, and it reads the whole file, so the two questions are kept apart."""
        return self.path_of(weight).is_file()

    def verify(self, weight: Weight) -> None:
        """Prove an installed file is the model it claims to be. Raises with a readable reason.

        Checked before a model is loaded, every time, rather than only when it was installed. A
        file on disk can be truncated by a full disk, replaced by a restore from an older backup,
        or edited by somebody who thought they were being helpful, and none of those announce
        themselves: an ONNX file with its tail missing often loads and then produces subtly wrong
        numbers.
        """
        path = self.path_of(weight)
        if not path.is_file():
            raise WeightError(f"the {weight.role} model has not been installed yet")
        actual = digest_of(path)
        if actual != weight.digest:
            raise WeightError(
                f"the {weight.role} model on disk is not the one Sift expects. Delete "
                f"{path.name} and fetch it again."
            )

    async def install_from_file(self, weight: Weight, source: Path) -> None:
        """Take a model from a file the operator already has.

        The offline answer, and the only one on a machine with no route out. The file is checked
        against the same digest a download would be, so a wrong or corrupted copy is refused here
        rather than producing quietly worse answers later.
        """
        if not await asyncio.to_thread(source.is_file):
            raise WeightError(f"there is no file at {source}")
        await asyncio.to_thread(self._install_local, weight, source)
        log.info("ml.weight.installed", namespace=self._namespace, weight=weight.id, source="file")

    async def fetch(
        self,
        weight: Weight,
        *,
        session_factory: SessionFactory | None = None,
        progress: Progress | None = None,
        fresh: bool = False,
    ) -> None:
        """Download a model, resuming a previous attempt unless `fresh` says start again.

        The partial file is kept beside the destination and asked for by byte range on a second
        attempt. Nothing is put in place until the digest matches; a partial that fails it goes.
        """
        destination = self.path_of(weight)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        partial = destination.with_suffix(".part")
        if fresh:
            await asyncio.to_thread(partial.unlink, True)

        try:
            finished = await fetch_resumable(
                weight.url,
                partial,
                what=f"{weight.role} model",
                progress=progress,
                session_factory=session_factory,
            )
        except FetchFailed as exc:
            # The client's own words after the sentence, where whoever chases the fault reads them.
            said = f"{exc.sentence} Or copy the file to this device yourself."
            kind = refused_for_good() if isinstance(exc, Untrusted) else WeightError
            raise kind(f"{said} {exc.words}" if exc.words else said) from exc
        if not finished:
            # Stopped on purpose. What has arrived stays where it is, and the next attempt asks for
            # the remainder rather than starting again.
            return

        try:
            await asyncio.to_thread(self._install_local, weight, partial)
        except WeightError as exc:
            # Resuming a wrong partial asks for nothing more and fails the same check for ever.
            await asyncio.to_thread(partial.unlink, True)
            raise WeightError(
                f"The {weight.role} model didn't arrive intact, so it was removed. Starting again "
                "downloads it afresh."
            ) from exc
        await asyncio.to_thread(partial.unlink, True)
        log.info(
            "ml.weight.installed", namespace=self._namespace, weight=weight.id, source="download"
        )

    def _install_local(self, weight: Weight, source: Path) -> None:
        destination = self.path_of(weight)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = destination.with_suffix(".staged")
        if weight.archive_member and zipfile.is_zipfile(source):
            try:
                _extract(source, weight, staged)
            except (zipfile.BadZipFile, zlib.error):
                staged.unlink(missing_ok=True)
                raise WeightError(
                    f"that archive is damaged, so it isn't the {weight.role} model Sift expects."
                ) from None
        else:
            shutil.copyfile(source, staged)

        actual = digest_of(staged)
        if actual != weight.digest:
            staged.unlink(missing_ok=True)
            raise WeightError(
                f"that file is not the {weight.role} model Sift expects. It may be the wrong "
                "model, or it may have been damaged in transit."
            )
        staged.replace(destination)


def _extract(archive: Path, weight: Weight, destination: Path) -> None:
    """Pull one named file out of an archive, and nothing else.

    The member is named exactly rather than searched for, and written to a path this module chose.
    An archive that names its own destination is how an extraction writes outside the directory it
    was supposed to stay in, and this one comes off the internet.
    """
    member_name = weight.archive_member or ""
    with zipfile.ZipFile(archive) as bundle:
        try:
            info = bundle.getinfo(member_name)
        except KeyError:
            raise WeightError(
                f"that archive does not contain {weight.archive_member}, so it is not the "
                f"{weight.role} model Sift expects."
            ) from None
        with bundle.open(info) as member, destination.open("wb") as out:
            shutil.copyfileobj(member, out, _CHUNK)
