# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a graphics card usable, on a machine where Sift was installed rather than built.

**THE PROBLEM THIS SOLVES.** Sift ships the processor-only build of its model runtime. That is not
an oversight: the graphics-card build is a quarter of a gigabyte and needs a further gigabyte of
NVIDIA's own libraries beside it, and the overwhelming majority of installs will never switch on a
feature that uses either. Putting all of it in the installer would charge every download for a
capability almost nobody turns on.

Without it a menu offers a GPU option that can never work, refused with a sentence that has no
answer in it. A setting somebody cannot act on is worse than no setting: it reads as a fault in the
machine.

**WHAT THIS DOES.** The graphics-card runtime is fetched on request, the same way a model is, and
put somewhere it can survive:

- **In the DATA directory, not beside the application.** Installing an update replaces everything
  under the application's own folder, so a runtime put there would be silently deleted by the next
  update and the feature would turn itself off with no explanation. The data directory is the same
  reasoning the model files are kept there under: expensive to obtain, and not Sift's to delete.
- **Under a folder named for the exact set**, so a Sift whose bundled runtime has moved on does not
  load a graphics-card runtime built against the old one. Mixing the two is not a version warning;
  it is a process that dies inside a C library.
- **Verified wheel by wheel BEFORE anything is unpacked.** A truncated download of a native library
  does not fail loudly: it fails when a card is asked to do something, hours later, inside code
  that cannot say what happened.

**AND IT IS PROVED BEFORE IT IS BELIEVED.** `works` below does not ask which providers are
available. It starts a separate process, loads a real model onto the card, and runs it. The list of
available providers is not evidence: a runtime lists every provider it was COMPILED with, the way
a video encoder can appear in a list, accept a job, and fail on every frame because the hardware
behind it is not reachable.

The separate process is not tidiness either. A CUDA context that fails once is dead for the whole
process, forever and silently: every later call fails while the program goes on running. Proving it
in a process that then exits is the only way to ask the question without risking the answer.
"""

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
from sift.kernel.fetch import CHUNK, FetchFailed, Progress, SessionFactory, fetch_resumable
from sift.kernel.log import get_logger
from sift.kernel.ml.child import devices_here, forget_devices
from sift.kernel.ml.runtime import DeviceUnavailable, why_unusable
from sift.kernel.paths import PathEscape, confine
from sift.kernel.subprocess import SubprocessError
from sift.kernel.subprocess import run as run_once

log = get_logger(__name__)


class AccelError(Exception):
    """The graphics-card runtime could not be installed. The message is for a person to read."""


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


#: What this set IS, and the name of the folder it goes in.
#:
#: Every part of it is load-bearing. `1.28.0` must match the runtime Sift itself carries, because
#: the two are the same library and only one of them can be imported. `cp313` is the interpreter
#: the packages are compiled for. `cu13` is the CUDA generation the whole set is built against:
#: mixing a CUDA 12 library into a CUDA 13 set produces a process that exits without a message.
PIN = "1.28.0-cp313-cu13"

#: The exact packages, resolved once and written down.
#:
#: REGENERATE, do not edit by hand:
#:
#:     uv pip compile --python-site x86_64-pc-windows-msvc --python-version 3.13 \
#:         --generate-hashes -  <<< 'onnxruntime-gpu[cuda,cudnn]==<version>'
#:
#: and take the graphics-card runtime and the `nvidia-*` lines from it. The rest of what that
#: resolves (numpy, protobuf, flatbuffers, packaging) is already inside Sift, because the
#: processor build of the same runtime depends on all four. Fetching second copies of them would
#: put two numpys on one path, which is a failure with no useful message at either end.
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

#: How much has to come down the wire, so a screen can say so BEFORE anybody starts.
TOTAL_BYTES = sum(wheel.size_bytes for wheel in WHEELS)

#: What the set takes on disk once unpacked: 1.8 GB of libraries beside the 1.34 GB of wheels they
#: came out of. Both exist at the same time while the last wheel is being unpacked, which is what
#: `PEAK_BYTES` is: the room the install needs, and the figure the screen says and the download is
#: refused without.
UNPACKED_BYTES = 1_800_000_000
PEAK_BYTES = TOTAL_BYTES + UNPACKED_BYTES

#: Written last, and its presence is what "installed" means.
#:
#: LAST, so that a download interrupted three quarters of the way through leaves a folder that is
#: obviously incomplete rather than one that looks finished and fails at the first use. Nothing
#: reads a half-unpacked folder, because nothing looks at the folder at all until this file is in
#: it.
_MARKER = "ready.json"

#: Where the partly-downloaded packages wait between attempts. Inside the same folder, so stopping
#: and resuming later costs the remainder rather than the whole gigabyte, and so that throwing the
#: whole installation away is one directory removal.
_PARTS = ".parts"


#: The runtime's corner of the device's model store. Older versions kept it in a library's data
#: folder, under the same name (`kernel.ml.store`).
FOLDER = "accel"


def directory(settings: Settings) -> Path:
    """Where this exact set of packages lives: in the device's store, beside the models.

    Once per device rather than once per library, for the reason the models are: it is a build of
    a program for this device's card, and a second library would otherwise download 1.3 GB again.
    """
    return settings.models_dir / FOLDER / PIN


def installed(settings: Settings) -> bool:
    """Whether a COMPLETE installation is there. Says nothing about whether the card works."""
    return (directory(settings) / _MARKER).is_file()


def already_capable() -> bool:
    """Whether the runtime this Sift can already import drives a card without any of this.

    THE ANSWER TO "WHAT IF I ALREADY HAVE THESE FILES". Somebody running Sift from its source may
    have installed the graphics-card runtime themselves, deliberately, at a version they chose,
    and a machine with a full CUDA toolkit on it is the commonest kind of machine that has a card
    worth using. Downloading 1.3 GB over the top of that would be spending somebody's bandwidth to
    replace something already working, and putting a folder in front of theirs on the import path
    would silently take the version decision away from them.

    So it is asked BEFORE anything is offered: where this is true, there is nothing to install and
    the screen says so instead of offering a download. A runtime that can't start can't drive one.
    """
    from sift.kernel.config import get_settings

    try:
        available = devices_here(get_settings())
    except DeviceUnavailable:
        return False
    return why_unusable("nvidia", available) is None


# The directories Windows has been told it may load libraries from. Held at module scope because
# `add_dll_directory` UNDOES ITSELF when the object it returns is collected, so a handle nobody
# keeps is a directory that stops working a moment later, at whatever point the collector runs.
_dll_directories: list[object] = []

#: Which directories have already been handed to `add_dll_directory`, so none is handed over twice.
#:
#: **The guard that keeps `enable` idempotent.** That function is called on EVERY import of the
#: runtime (and the import site is deliberately not cached, because a screen asks it the moment
#: somebody changes a device), so it runs again for every model load and every provider query.
#: `sys.path` and `PATH` are checked before they are written; without this, each call would append
#: two more registrations, and the list above holds every handle for ever precisely so that none of
#: them is collected.
#:
#: Windows' own list of added directories is bounded. Once it overflows, `AddDllDirectory` answers
#: **ERROR_FILENAME_EXCED_RANGE (206)**, which Python maps to `ENOENT` and reports as
#: `FileNotFoundError: [WinError 206] The filename or extension is too long: '<the directory>'`.
#: The directory exists, its name is 88 characters, and nothing about it is too long: it is the
#: LIST that is too long, and the message names the wrong thing entirely. It would show as
#: describe jobs that fail with that sentence and nothing else.
_dll_added: set[str] = set()

#: Whether this site lets a process widen its own library search path.
#:
#: `os.add_dll_directory` is Windows-only, and the packages this installs are Windows builds, so
#: on any other site there is nothing here to do and the path alone decides what gets imported.
#: Named rather than asked inline so both answers can be driven: one of them is unreachable on
#: whichever machine the suite happens to be running on.
_CAN_ADD_DLL_DIRECTORY = hasattr(os, "add_dll_directory")


def enable(settings: Settings) -> bool:
    """Make the installed runtime the one that gets imported. Idempotent; False when there is none.

    THIS HAS TO HAPPEN BEFORE THE RUNTIME IS IMPORTED, and once it is imported it cannot be undone:
    two builds of the same library cannot both be loaded, and the first one in wins for the life of
    the process. That is why the call sits at the top of the one function that does the importing,
    rather than anywhere that looks tidier.
    """
    if not installed(settings):
        return False
    root = directory(settings)
    # Written down where it is easy to check: EVERYTHING this touches is inside Sift's own folder.
    # Nothing is installed into the machine's Python, nothing is put on the system PATH, and no
    # library anywhere else is replaced. `add_dll_directory` is process-local, so even the search
    # path this widens is widened only for this one running copy of Sift.
    if str(root) not in sys.path:
        # FRONT of the path. Sift's own processor build is installed beside the application and
        # would otherwise be found first, and "found first" is the whole question here.
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
    r"""Also on this PROCESS'S own PATH, and both are needed rather than either.

    `add_dll_directory` governs what PYTHON's loader is asked to open. It does not reach what those
    libraries then ask for themselves: `onnxruntime_providers_cuda.dll` names `cublasLt64_13.dll` in
    its import table, and Windows resolves that with the ordinary search order, which does not
    consult the directories added above: with the folders added and not on the path, the provider
    fails to load with "cublasLt64_13.dll ... is missing" while the file sits in a folder that was
    just added. With them on the path it loads and the card runs a model.

    FIRST, not last, and that is deliberate on a machine that has CUDA installed for something else.
    Sift's own copy wins for Sift, so nothing here can pick up a different version that happens to
    be on the machine, which is the same isolation, read from the other side, as nothing here
    being able to disturb that other installation.

    THIS PROCESS ONLY. `os.environ` is a copy the process holds; nothing is written to the user's
    or the machine's environment, and it is gone when Sift stops. Its one reach beyond this process
    is that anything Sift starts inherits it, which is what makes the proof subprocess work.
    """
    current = os.environ.get("PATH", "")
    already = {part.casefold() for part in current.split(os.pathsep) if part}
    adding = [str(one) for one in directories if str(one).casefold() not in already]
    if not adding:
        return
    os.environ["PATH"] = os.pathsep.join([*adding, current]) if current else os.pathsep.join(adding)


def library_directories(root: Path) -> list[Path]:
    r"""Every folder under `nvidia/` that actually holds a library.

    FOUND BY LOOKING, NOT BY KNOWING THE LAYOUT. A fixed pattern like `nvidia/*/bin` stops being
    true without anything failing loudly: the CUDA 13 wheels put their libraries one level deeper,
    in `nvidia/cu13/bin/x86_64`, and a runtime installed, verified and reported ready would then
    fail to load `cublas64_13.dll`, which on screen reads only as "the card was refused and the
    processor was used instead". Asking which folders contain a `.dll` cannot go out of date.
    """
    return sorted({found.parent for found in root.glob("nvidia/**/*.dll")})


async def install(
    settings: Settings,
    *,
    progress: Progress | None = None,
    session_factory: SessionFactory | None = None,
) -> bool:
    """Fetch and unpack the whole set. True when it finished, False when it was stopped.

    Every package is downloaded and checked before ANY of them is unpacked. A set where six of seven
    libraries are the right ones is not six-sevenths working: it is a process that dies on a call
    into the seventh, with a message about an address rather than about a download.
    """
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
                raise AccelError(str(exc)) from exc
            if not finished:
                return False
            if not await asyncio.to_thread(_matches, target, wheel.digest):
                await asyncio.to_thread(target.unlink, True)
                raise AccelError(
                    f"The {wheel.name} package didn't arrive intact. Nothing was installed. "
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
    # The kept answer to "which devices can the runtime drive" has just stopped being true.
    forget_devices()
    log.info("ml.accel.installed", pin=PIN, bytes=TOTAL_BYTES)
    return True


def remove(settings: Settings) -> None:
    """Throw the whole thing away. A gigabyte somebody may want back is a gigabyte they can see.

    The rule this steps around exists to stop Sift deleting files somebody else put there: media
    it indexes in place. This folder is not that: Sift created it, Sift is the only thing that reads
    it, and every byte in it can be fetched again from the addresses pinned in this file.
    """
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
    """Unzip one package into the folder, and refuse anything this is not equipped to install.

    A wheel is a zip, and for THIS pinned set installing one really is unzipping it: every package
    in it is a directory of files plus its own metadata, with no scripts, no headers and no data
    section. That is checked rather than assumed: a `.data` directory means a package whose files
    have to be sorted into several destinations, and unzipping one of those puts libraries where
    nothing will look for them and reports success. If a future pin brings one, this stops.
    """
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            first = member.split("/", 1)[0]
            if first.endswith(".data"):
                raise AccelError(
                    f"the {wheel.name} package has to be installed by a package installer, and "
                    "Sift only knows how to unpack the simple kind. Nothing was installed."
                )
            # Every member's place is proved inside the folder before anything is written: a name
            # with a drive, a separator of the other system, a `..`, or one that resolves out of
            # the folder is refused rather than repaired, since this set is pinned by digest and a
            # member like that means the file is not what was pinned.
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


#: The model the proof runs. It comes WITH the runtime (every build ships these three tiny files
#: for its own tests), so there is nothing to fetch, nothing to add to the installer, and nothing
#: that can go stale against the runtime it is proving.
_PROOF_MODEL = "mul_1.onnx"

#: Long enough for a first CUDA context on a cold driver, which is seconds rather than milliseconds.
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
    """Prove the card really runs a model, in a process of its own (see the module's note).
    None when it does, a reason when it does not."""
    if not installed(settings):
        return "The graphics-card runtime is not installed."

    argv = [sys.executable, "-c", _PROOF, str(directory(settings)), _PROOF_MODEL]
    try:
        # The one limit covers starting the proof too, which waits on the pool an import fills.
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
    # The last line, because a failing runtime writes pages of its own diagnostics first and the
    # sentence a person needs is at the end of them.
    said = _readable(finished.stderr or finished.stdout)
    log.warning("ml.accel.proof_failed", detail="\n".join(said[-8:])[:800])
    return (
        "The graphics-card runtime is installed but the card would not run a model. "
        f"The reason it gave was: {_last_sentence(said)}"
    )


#: Anything that is not ordinary printable text, in a sentence that came from another program.
#:
#: A byte that is not valid UTF-8 becomes U+FFFD, which reaches the screen as a box in front of an
#: otherwise perfectly good sentence and reads as Sift having garbled its own message. Control
#: characters do the same more quietly. Both are taken out HERE rather than in the interface,
#: because this is where a person's sentence is being built out of another program's bytes.
_UNREADABLE = re.compile(r"[^\x20-\x7e]+")

#: The colour codes a terminal would have eaten. The escape itself is a control character and is
#: gone by the time this runs; what is left is the `[1;31m` that followed it, in the middle of a
#: sentence somebody is trying to read.
_COLOUR = re.compile(r"\[[0-9;]*m")


def _readable(raw: bytes) -> list[str]:
    r"""The child's output as lines, in the two encodings it arrives in.

    ONE STREAM, TWO ENCODINGS, and the second one is why this is not a plain `decode`. The runtime
    is a C++ library and writes its diagnostics as WIDE characters, so each letter arrives followed
    by a zero byte; Python's own last words on the same stream are ordinary UTF-8. Decoded as one or
    the other, half the output is unreadable, and the unreadable half can be the half that says
    what is actually wrong.

    Dropping the zero bytes makes both halves readable at the same time, because everything either
    of them says is ASCII. It is the smallest thing that is true of both rather than a guess about
    which one a given run produced.
    """
    return raw.replace(b"\x00", b"").decode("utf-8", "replace").strip().splitlines()


#: What a line looks like when it names the REAL failure rather than the consequence.
#:
#: The proof script's own last words are "the card was refused and the processor was used instead",
#: which is true and is a summary of what happened, not a reason, and read on a settings screen it
#: is a sentence about a graphics card. The reason comes earlier, a line such as
#: `cublasLt64_13.dll ... is missing`, which is a sentence somebody can act on and is not about a
#: card at all. So a line that names a load failure wins over the last line.
_A_REASON = re.compile(
    r"error loading|onnxruntimeerror|is missing|cannot find|failed to create", re.I
)


def _last_sentence(lines: list[str]) -> str:
    """The line that best says WHY, made safe to put on a screen.

    The last non-empty line is the summary and is the fallback. A line naming a load failure is
    preferred over it, because that is the one with the answer in it.
    """
    cleaned = [_COLOUR.sub("", _UNREADABLE.sub(" ", line)).strip() for line in lines]
    kept = [line for line in cleaned if line]
    for line in reversed(kept):
        if _A_REASON.search(line):
            return line
    return kept[-1] if kept else "nothing at all"
