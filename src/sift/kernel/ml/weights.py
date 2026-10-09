# SPDX-License-Identifier: AGPL-3.0-or-later
"""The models a feature can use, where to get them, and proving the right file arrived.

None ships with Sift (a licence requirement); a fetch is asked for, resumable and verified."""

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

#: The transfer's own read size.
_CHUNK = CHUNK


class WeightError(Exception):
    """A model could not be obtained, or is not the one it claims; the message is for a person."""


@functools.cache
def refused_for_good() -> type[WeightError]:
    """A certificate refusal, failing for good; built late, as the inference child imports this."""
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


#: Re-exported through `__all__`, as `Progress = Progress` is no re-export to a type checker.
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
    """The digest of a file on disk, read in pieces so a large model need not fit in memory."""
    hasher = blake3()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class WeightStore:
    """One feature's own corner of the model store, so removing one never takes another's."""

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
        """This feature's corner of the device's one model store, never under the cache."""
        return self._settings.models_dir / self._namespace

    def path_of(self, weight: Weight) -> Path:
        return self.directory() / f"{weight.id}{weight.suffix}"

    def installed(self, weight: Weight) -> bool:
        """Whether the file is there; `verify` reads the whole file to say if it is right."""
        return self.path_of(weight).is_file()

    def verify(self, weight: Weight) -> None:
        """Prove an installed file is the expected model before every load; a truncated one lies."""
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
        """Take a model from a file already on the machine, checked against the same digest."""
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
        """Download a model, resuming unless `fresh`; nothing is placed until the digest matches."""
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
            # The client's own words follow the sentence, for whoever chases the fault.
            said = f"{exc.sentence} Or copy the file to this device yourself."
            kind = refused_for_good() if isinstance(exc, Untrusted) else WeightError
            raise kind(f"{said} {exc.words}" if exc.words else said) from exc
        if not finished:
            # Stopped on purpose; what arrived stays for the next attempt to continue.
            return

        try:
            await asyncio.to_thread(self._install_local, weight, partial)
        except WeightError as exc:
            # A wrong partial would fail the same check for ever if resumed.
            await asyncio.to_thread(partial.unlink, True)
            raise WeightError(
                f"The {weight.role} model came in damaged, so it was removed. Starting again "
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
    """Pull one named member out of an archive to a path chosen here, never the archive's own."""
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
