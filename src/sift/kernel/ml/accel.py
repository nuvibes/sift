# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fetching the graphics-card runtime on request into the data directory, and proving it works.

Verified wheel by wheel before unpacking; proved by running a model in a separate process."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

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
from sift.kernel.ml.child import devices_here, forget_devices
from sift.kernel.ml.runtime import DeviceUnavailable, why_unusable
from sift.kernel.paths import PathEscape, confine
from sift.kernel.subprocess import SubprocessError
from sift.kernel.subprocess import run as run_once

log = get_logger(__name__)


class AccelError(Exception):
    """The graphics-card runtime could not be installed. The message is for a person to read."""


def refused_for_good(exc: FetchFailed) -> type[AccelError]:
    """A refused certificate fails for good; built late, as the inference child imports this."""
    if not isinstance(exc, Untrusted):
        return AccelError
    from sift.kernel.jobs.queue_rows import JobFailedPermanently

    return type("AccelRefused", (AccelError, JobFailedPermanently), {"__module__": __name__})


@dataclass(frozen=True, slots=True)
class Wheel:
    """One package, pinned to the byte."""

    name: str
    version: str
    url: str
    digest: str
    """SHA-256, as the index publishes it. Not the BLAKE3 the model files use: this one is quoted
    from the publisher rather than measured here, and a digest anybody can check against the
    published one is worth more than a faster one only Sift knows."""
    size_bytes: int


#: Every part is load-bearing: the runtime Sift carries, the interpreter, and the CUDA generation.
PIN = "1.28.0-cp313-cu13"

#: Regenerate with `uv pip compile ... 'onnxruntime-gpu[cuda,cudnn]==<version>'` and keep only the
#: GPU runtime and `nvidia-*` lines; the rest is already inside Sift, and a second numpy breaks it.
WHEELS: tuple[Wheel, ...] = (
    Wheel(
        "onnxruntime-gpu",
        "1.28.0",
        "https://files.pythonhosted.org/packages/47/e8/aab01c0b41cfc2cdcb24de9dbf546c7470e4a43d854c617220e425f2c6f5/onnxruntime_gpu-1.28.0-cp313-cp313-win_amd64.whl",
        "4c89b8c192864582e04eb4b43574648f185bfd00fc754c3f085fd04d0cd81bc2",
        241464688,
    ),
    Wheel(
        "nvidia-cublas",
        "13.6.0.2",
        "https://files.pythonhosted.org/packages/08/8f/890a96ea1ff615100296977cce23296052dcb8c114d4e451201ec39df9bf/nvidia_cublas-13.6.0.2-py3-none-win_amd64.whl",
        "3b5bcd6bfb6f65010ebf195851bcb9b2aa34b9fe08479432002991c1fe84b67d",
        394568225,
    ),
    Wheel(
        "nvidia-cuda-nvrtc",
        "13.3.33",
        "https://files.pythonhosted.org/packages/a1/42/edce72f2c5a0f587168109c867f25f4a9a6cd7289ecf0d68ed2b1070f273/nvidia_cuda_nvrtc-13.3.33-py3-none-win_amd64.whl",
        "7d2af818851c0c224d5f92221e9226e51ee23c236df4b51f9194563979c888be",
        45319163,
    ),
    Wheel(
        "nvidia-cuda-runtime",
        "13.3.29",
        "https://files.pythonhosted.org/packages/d2/27/b53a5e0397842a5c11f0e1a39d4e5b2f22638a4126e83b3c4e196f62c969/nvidia_cuda_runtime-13.3.29-py3-none-win_amd64.whl",
        "0667ec61c3d897388efa305ed4f7609ace88849a753ba9c6311d06dca55fff4f",
        2630354,
    ),
    Wheel(
        "nvidia-cufft",
        "12.3.0.29",
        "https://files.pythonhosted.org/packages/94/64/8e9d808720559d3cbfcd1d1bc8a2e6f55deb29d692513d5a93c8d417b7e5/nvidia_cufft-12.3.0.29-py3-none-win_amd64.whl",
        "510036a2bbab5c83ae93dc5c907c3a49d3518e3066ac3a2052ff0f7f9b27dfc4",
        183939745,
    ),
    Wheel(
        "nvidia-curand",
        "10.4.3.29",
        "https://files.pythonhosted.org/packages/2a/eb/63f7710fc84837e0118002bc29671542807921aef3a0c710da83a5e7e711/nvidia_curand-10.4.3.29-py3-none-win_amd64.whl",
        "34b18d5a2a8e5db4c3846475ae4eef0cacdf3ac5e9c501f3a4efb422f137a74e",
        55429221,
    ),
    Wheel(
        "nvidia-cudnn-cu13",
        "9.24.0.43",
        "https://files.pythonhosted.org/packages/31/23/1dd3aa15cc4ab62c8fc88f8049ef137bc44c17892f5577bc80d994941f77/nvidia_cudnn_cu13-9.24.0.43-py3-none-win_amd64.whl",
        "67a7273b5cf062f9446fd76cf464351a1c0f66501e6cd78f6675c0d604d8ac87",
        412314771,
    ),
)

#: How much comes down the wire, so a screen can say so before anybody starts.
TOTAL_BYTES = sum(wheel.size_bytes for wheel in WHEELS)

#: Unpacked size; wheels and libraries coexist while the last unpacks, so `PEAK_BYTES` is the need.
UNPACKED_BYTES = 1_800_000_000
PEAK_BYTES = TOTAL_BYTES + UNPACKED_BYTES

#: Written last, so an interrupted install never looks finished.
_MARKER = "ready.json"

#: Partly downloaded packages, inside the folder, so a resume costs only the remainder.
_PARTS = ".parts"


#: The runtime's corner of the device's model store.
FOLDER = "accel"


def directory(settings: Settings) -> Path:
    """Where this exact set lives: once per device, beside the models."""
    return settings.models_dir / FOLDER / PIN


def installed(settings: Settings) -> bool:
    """Whether a complete installation is there; says nothing about whether the card works."""
    return (directory(settings) / _MARKER).is_file()


def already_capable() -> bool:
    """Whether the runtime Sift already imports drives a card, so nothing need be downloaded."""
    from sift.kernel.config import get_settings

    try:
        available = devices_here(get_settings())
    except DeviceUnavailable:
        return False
    return why_unusable("nvidia", available) is None


# Held for ever, as `add_dll_directory` undoes itself when its handle is collected.
_dll_directories: list[object] = []

#: Directories already added, so `enable` stays idempotent: Windows' list overflows with a
#: misleading "filename too long" (206) error.
_dll_added: set[str] = set()

#: Named so both answers can be tested on either system.
_CAN_ADD_DLL_DIRECTORY = hasattr(os, "add_dll_directory")


def enable(settings: Settings) -> bool:
    """Make the installed runtime the one imported, before the import; False when there is none."""
    if not installed(settings):
        return False
    root = directory(settings)
    # Everything touched is inside Sift's folder and this process; nothing system-wide changes.
    if str(root) not in sys.path:
        # The front, so Sift's own processor build is not found first.
        sys.path.insert(0, str(root))
    if _CAN_ADD_DLL_DIRECTORY:
        found = library_directories(root)
        for where in found:
            # Once each, for the life of the process. See `_dll_added`.
            if str(where) not in _dll_added:
                _dll_directories.append(os.add_dll_directory(str(where)))
                _dll_added.add(str(where))
        _put_first_on_the_path(found)
    return True


def _put_first_on_the_path(directories: list[Path]) -> None:
    r"""Also first on this process's PATH: the libraries' own imports use the ordinary search."""
    current = os.environ.get("PATH", "")
    already = {part.casefold() for part in current.split(os.pathsep) if part}
    adding = [str(one) for one in directories if str(one).casefold() not in already]
    if not adding:
        return
    os.environ["PATH"] = os.pathsep.join([*adding, current]) if current else os.pathsep.join(adding)


def library_directories(root: Path) -> list[Path]:
    r"""Every folder under `nvidia/` that holds a library, found by looking, not by a layout."""
    return sorted({found.parent for found in root.glob("nvidia/**/*.dll")})


async def install(
    settings: Settings,
    *,
    progress: Progress | None = None,
    session_factory: SessionFactory | None = None,
) -> bool:
    """Fetch and verify every package, then unpack; True when done, False when stopped."""
    root = directory(settings)
    parts = root / _PARTS
    await asyncio.to_thread(parts.mkdir, parents=True, exist_ok=True)

    done = 0
    files: list[tuple[Wheel, Path]] = []
    for wheel in WHEELS:
        target = parts / f"{wheel.name}-{wheel.version}.whl"
        already = done

        def onward(received: int, _total: int, *, base: int = already) -> bool:
            return True if progress is None else progress(base + received, TOTAL_BYTES)

        if not await asyncio.to_thread(_matches, target, wheel.digest):
            try:
                finished = await fetch_resumable(
                    wheel.url,
                    target,
                    what=f"{wheel.name} package",
                    progress=onward,
                    session_factory=session_factory,
                )
            except FetchFailed as exc:
                raise refused_for_good(exc)(str(exc)) from exc
            if not finished:
                return False
            if not await asyncio.to_thread(_matches, target, wheel.digest):
                await asyncio.to_thread(target.unlink, True)
                raise AccelError(
                    f"The {wheel.name} package came in damaged. Nothing was installed. "
                    "Starting again downloads it afresh."
                )
        done += wheel.size_bytes
        files.append((wheel, target))
        if progress is not None and not progress(done, TOTAL_BYTES):
            return False

    await asyncio.to_thread(_unpack_all, root, files)
    await asyncio.to_thread(shutil.rmtree, parts, True)
    await asyncio.to_thread(
        (root / _MARKER).write_text,
        json.dumps({"pin": PIN, "packages": {w.name: w.version for w in WHEELS}}, indent=2),
        "utf-8",
    )
    # The kept answer about which devices work is now stale.
    forget_devices()
    log.info("ml.accel.installed", pin=PIN, bytes=TOTAL_BYTES)
    return True


def remove(settings: Settings) -> None:
    """Throw the whole set away; Sift made it and can fetch it all again."""
    root = directory(settings)
    shutil.rmtree(root, ignore_errors=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
    forget_devices()
    log.info("ml.accel.removed", pin=PIN)


def _matches(path: Path, digest: str) -> bool:
    """Whether a file on disk is exactly the one that was promised."""
    if not path.is_file():
        return False
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest() == digest


def _unpack_all(root: Path, files: list[tuple[Wheel, Path]]) -> None:
    for wheel, archive in files:
        _unpack(root, wheel, archive)


def _unpack(root: Path, wheel: Wheel, archive: Path) -> None:
    """Unzip one package, refusing a `.data` section this cannot install correctly."""
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            first = member.split("/", 1)[0]
            if first.endswith(".data"):
                raise AccelError(
                    f"the {wheel.name} package has to be installed by a package installer, and "
                    "Sift only knows how to unpack the simple kind. Nothing was installed."
                )
            # Each member's place is proved inside the folder before anything is written.
            parts = member.rstrip("/").split("/")
            if any(part in ("", ".", "..") or ":" in part or "\\" in part for part in parts):
                raise AccelError(f"the {wheel.name} package names a file outside the folder.")
            try:
                confine(root, root.joinpath(*parts))
            except PathEscape as escape:
                raise AccelError(
                    f"the {wheel.name} package names a file outside the folder."
                ) from escape
        bundle.extractall(root)


#: A model every runtime build ships for its own tests, so nothing is fetched.
_PROOF_MODEL = "mul_1.onnx"

#: Long enough for a first CUDA context on a cold driver.
_PROOF_TIMEOUT = 120.0

_PROOF = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
# Every folder holding a library, found by walking rather than by naming a layout. See
# `library_directories`: assuming `nvidia/*/bin` misses the CUDA 13 layout.
found = []
for here, _dirs, files in os.walk(os.path.join(sys.argv[1], "nvidia")):
    if any(name.endswith(".dll") for name in files):
        found.append(here)
        os.add_dll_directory(here)
# And on the PATH, which is what a library's own dependencies are resolved with. See
# `_put_first_on_the_path`: without this the provider cannot find cublasLt and says the card
# refused it, which is a sentence about a card and has nothing to do with one.
os.environ["PATH"] = os.pathsep.join(found + [os.environ.get("PATH", "")])
import numpy, onnxruntime
from onnxruntime.datasets import get_example
session = onnxruntime.InferenceSession(get_example(sys.argv[2]), providers=["CUDAExecutionProvider"])
if "CUDAExecutionProvider" not in session.get_providers():
    raise SystemExit("the card was refused and the processor was used instead")
name = session.get_inputs()[0].name
session.run(None, {name: numpy.ones((3, 2), dtype=numpy.float32)})
print(json.dumps({"ok": True, "version": onnxruntime.__version__}))
"""


async def works(settings: Settings) -> str | None:
    """Prove the card runs a model, in a process of its own; None when it does, else a reason."""
    if not installed(settings):
        return "The graphics-card runtime is not installed."

    argv = [sys.executable, "-c", _PROOF, str(directory(settings)), _PROOF_MODEL]
    try:
        # The limit covers starting the proof too.
        finished = await asyncio.wait_for(
            run_once(argv, time_limit=_PROOF_TIMEOUT), timeout=_PROOF_TIMEOUT
        )
    except (SubprocessError, TimeoutError) as exc:
        why = str(exc) or "it did not answer in time"
        return (
            "The card could not be tested: Sift could not run the check, or it did not answer "
            f"within two minutes. That usually means the graphics driver is busy or has stopped "
            f"responding, and restarting the computer is what clears it. ({why})"
        )
    if finished.returncode == 0:
        log.info("ml.accel.proved", pin=PIN)
        return None
    # A failing runtime writes pages first; the sentence a person needs is at the end.
    said = _readable(finished.stderr or finished.stdout)
    log.warning("ml.accel.proof_failed", detail="\n".join(said[-8:])[:800])
    return (
        "The graphics-card runtime is installed but the card would not run a model. "
        f"The reason it gave was: {_last_sentence(said)}"
    )


#: Non-printable text from another program, removed here where a person's sentence is built.
_UNREADABLE = re.compile(r"[^\x20-\x7e]+")

#: The colour codes left once the escape character is gone.
_COLOUR = re.compile(r"\[[0-9;]*m")


def _readable(raw: bytes) -> list[str]:
    r"""The child's output as lines; dropping zero bytes reads both its wide and UTF-8 halves."""
    return raw.replace(b"\x00", b"").decode("utf-8", "replace").strip().splitlines()


#: A line naming the real load failure, preferred over the summarising last line.
_A_REASON = re.compile(
    r"error loading|onnxruntimeerror|is missing|cannot find|failed to create", re.I
)


def _last_sentence(lines: list[str]) -> str:
    """The line that best says why, made safe to put on a screen."""
    cleaned = [_COLOUR.sub("", _UNREADABLE.sub(" ", line)).strip() for line in lines]
    kept = [line for line in cleaned if line]
    for line in reversed(kept):
        if _A_REASON.search(line):
            return line
    return kept[-1] if kept else "nothing at all"
