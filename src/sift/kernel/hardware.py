# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift is running on, and what to do about it.

The machines Sift runs on have almost nothing in common: a mini PC, an old laptop, a NAS, a
workstation with a GPU in it. So it asks the machine what it is rather than assuming, and the
answers live here so that no feature has to work it out again.

The probe is honest in both directions. It never requires a GPU (everything works on the CPU,
just more slowly) and it never pretends a GPU is present when it is not. If Sift is told to
expect a GPU but none is visible on the machine, the report says so in plain words rather than
silently falling back to the CPU and leaving someone to wonder why it is slow.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from sift.kernel.config import Settings
from sift.kernel.log import get_logger
from sift.kernel.subprocess import SubprocessError
from sift.kernel.subprocess import run as run_tool

# Read through a flag rather than tested directly: mypy narrows a literal
# `sys.platform == "win32"` and then calls the other platform's branch unreachable, which
# would leave whichever half is not being checked unchecked. Same reason as kernel/paths.py.
_WINDOWS = sys.platform == "win32"

log = get_logger(__name__)

# Above this, more workers stop helping. Jobs are mostly ffmpeg and disk, so a dozen together on a
# machine with a single spinning disk finishes slower than four, and a big server is far more
# likely to be running Sift alongside other things than to want every core given to it.
MAX_WORKERS = 8

# How long to wait for ffmpeg to list its encoders. It reads no files and does no work, so this is
# only a guard against a wedged binary, not a real time budget.
_FFMPEG_TIMEOUT_SECONDS = 10.0

# The hardware video encoders worth using, and the device each one needs in order to actually run.
# ffmpeg can be built with an encoder the machine has no hardware for; asking such an encoder to run
# fails, or worse, quietly falls back to something slower than plain CPU. So an encoder counts as
# usable only when it is compiled in AND its device is present: both halves are checked below.
_NVIDIA = "nvidia"
_RENDER = "render"  # a /dev/dri render node: shared by VAAPI (most GPUs) and Intel Quick Sync
_TRANSCODE_ENCODERS: dict[str, str] = {
    "h264_nvenc": _NVIDIA,
    "hevc_nvenc": _NVIDIA,
    "av1_nvenc": _NVIDIA,
    "h264_vaapi": _RENDER,
    "hevc_vaapi": _RENDER,
    "av1_vaapi": _RENDER,
    "h264_qsv": _RENDER,
    "hevc_qsv": _RENDER,
    "av1_qsv": _RENDER,
}


def worker_concurrency(settings: Settings) -> int:
    """How many jobs to run together.

    One core is left for everything that is not a job: the web server, the database, the browser
    on the other end waiting for a thumbnail. A box with two cores gets one worker, which is
    slower than two and much better than a machine that stops answering while it thumbnails.
    """
    if settings.worker_concurrency is not None:
        return settings.worker_concurrency

    cores = os.cpu_count() or 1
    return max(1, min(MAX_WORKERS, cores - 1))


@dataclass(frozen=True)
class HardwareReport:
    """What the machine turned out to be. Built once at startup and read from thereafter.

    Other parts of the app consume this instead of probing for themselves, so the answer is
    decided in one place and cannot disagree with itself: the worker pool sizes from it, the
    player picks a transcode path from it, and the optional similarity features decide from it
    whether they can run at all.
    """

    cpu_count: int
    total_ram_bytes: int | None
    worker_concurrency: int
    cuda: bool
    """An NVIDIA compute device is present. The optional similarity features can use it."""
    rocm: bool
    """An AMD compute device is present."""
    transcode_encoders: tuple[str, ...]
    """Hardware video encoders that are both compiled into ffmpeg and backed by a present device."""
    warnings: tuple[str, ...]
    """Plain-language notes for the person running Sift. Also written to the log at startup."""

    # The make-and-model fields. Last, and optional, because they are the only ones nothing in Sift
    # decides anything from: they are read by an admin looking at a screen. A probe that could not
    # name a part leaves it None and the screen simply says less.
    cpu_model: str | None = None
    """What the processor calls itself, or None where the platform will not say."""

    @property
    def profile(self) -> str:
        """A short name for THIS shape of machine, stable across restarts.

        Two runs are comparable when they happened on the same hardware, and "the same hardware" has
        to mean something narrower than "the same computer": a machine with a graphics card removed
        is a different machine for anything that measures how fast it works.

        So the digest covers only what DECIDES speed: how many cores, how many workers, whether
        there is a compute device, and which hardware encoders are actually usable. It deliberately
        leaves out everything a driver update moves: card names, driver versions, warnings, and the
        make and model strings, which are read by people and by nothing else. Included, they would
        make every past run belong to a machine that no longer exists after a routine update.
        """
        parts = (
            str(self.cpu_count),
            str(self.worker_concurrency),
            "cuda" if self.cuda else "",
            "rocm" if self.rocm else "",
            ",".join(sorted(self.transcode_encoders)),
        )
        return sha256("|".join(parts).encode()).hexdigest()[:12]

    @property
    def profile_label(self) -> str:
        """The same machine, for somebody to read. Never used to tell two profiles apart.

        A label and a digest rather than one string doing both: this is written for a person, so it
        changes when a driver starts reporting a card differently, and a history keyed on it would
        split in two on a day nothing about the machine changed.
        """
        cores = f"{self.cpu_count} threads"
        gpu = self.gpu_cards[0].name if self.gpu_cards and self.gpu_cards[0].name else None
        return " - ".join(part for part in (self.cpu_model, cores, gpu) if part)

    gpu_cards: tuple[Card, ...] = ()
    """EVERY graphics card the driver reports, in the order it reports them.

    A list rather than one card, because a machine can have more than one and reporting only the
    first is a description of somebody's computer that is quietly wrong. The FIRST is the one that
    matters for work: it is the device CUDA uses unless it is told otherwise.

    An integrated graphics chip is not in here and cannot be. This comes from NVIDIA's own tool,
    which knows about NVIDIA cards and nothing else, and an integrated chip is not something Sift
    could put a model on anyway. What it can do for video is answered by `transcode_encoders`,
    which is measured rather than named.
    """
    gpu_driver: str | None = None
    """The driver version behind that card. Distinct from the card: one Sift can see with a driver
    too old to drive it is the case that produces the least useful silence."""
    installed_ram_bytes: int | None = None
    """What the memory sticks add up to, which is NOT `total_ram_bytes`. That one is what the
    operating system can address (the installed total less what the firmware reserves) and it is
    the figure any sizing has to be done against. This one is the figure a person recognises as
    their machine, and is what the description of the machine shows. None where nothing will say."""
    answers_kept: bool = False
    """The encoders and cards came from the last start's probe; `reprobe` asks again after this one."""

    @property
    def _working_card(self) -> Card | None:
        """The card Sift would put a model on, which is not always the first one listed.

        A machine with a chip built into its processor and a card in a slot has two adapters and
        one answer. Picking the first would name whichever Windows happened to enumerate first.
        """
        return next((one for one in self.gpu_cards if one.can_compute), None)

    @property
    def gpu_name(self) -> str | None:
        """What that card is called. None where nothing here can do the work."""
        card = self._working_card
        return card.name if card else None

    @property
    def gpu_vram_bytes(self) -> int | None:
        """Its memory. The figure that decides what will FIT: a model too big for the card does not
        run slowly, it fails at the point of loading."""
        card = self._working_card
        return card.vram_bytes if card else None

    @property
    def gpu_transcode(self) -> bool:
        """Whether any hardware video encoder is actually usable on this machine."""
        return bool(self.transcode_encoders)

    def as_dict(self) -> dict[str, Any]:
        """The shape served at /health.

        Deliberately carries nothing that identifies the machine: no paths, no hostname, no
        usernames, and none of the make-and-model fields below: /health can be reachable, and
        that detail is exactly what a stranger would use to fingerprint a box. The settings screen
        shows more and reads it through an admin-only route of its own.
        """
        return {
            "cpu_count": self.cpu_count,
            "total_ram_bytes": self.total_ram_bytes,
            "worker_concurrency": self.worker_concurrency,
            "gpu": {"cuda": self.cuda, "rocm": self.rocm},
            "transcode_encoders": list(self.transcode_encoders),
            "warnings": list(self.warnings),
        }


def parse_encoders(text: str) -> frozenset[str]:
    """Pull the encoder names out of `ffmpeg -encoders` output.

    The format is a legend, then a line of dashes, then one encoder per line: six flag characters,
    the name, and a description. Only the lines after the dashes are encoders; the legend above
    them uses the same flag column and would otherwise be misread as encoders called "=".

    Kept as its own function, and tested against captured output, because this is where a future
    ffmpeg quietly changing its listing format would silently switch hardware acceleration off.
    """
    names: set[str] = set()
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not in_table:
            # The row of dashes separates the legend from the encoders.
            if stripped and set(stripped) == {"-"}:
                in_table = True
            continue
        parts = line.split()
        if len(parts) >= 2 and _is_flag_column(parts[0]):
            names.add(parts[1])
    return frozenset(names)


def _is_flag_column(token: str) -> bool:
    """A six-character flag column: a type letter, then five capability flags or dots."""
    return len(token) == 6 and token[0] in "VAS" and set(token[1:]) <= set(".FSXBDL")


def _total_ram_bytes() -> int | None:
    """Total physical memory, or None where the platform will not say.

    Reports what the machine has, which on a container host is the host's memory rather than any
    cgroup limit. It is a diagnostic figure, not a budget anything is spent against, so the simple
    honest number is the right one.
    """
    if _WINDOWS:
        return _windows_total_memory()
    try:
        # `sysconf` is absent from `os` on Windows, so the ignore is needed when this is checked
        # there and unnecessary in CI. Both codes, so neither host complains about the other.
        page = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined, unused-ignore]
        pages = os.sysconf("SC_PHYS_PAGES")  # type: ignore[attr-defined, unused-ignore]
        return int(page) * int(pages)
    except (AttributeError, ValueError, OSError):
        return None


def _windows_total_memory() -> int | None:
    """How much memory the machine has, asked the way Windows answers it.

    Without this the answer is simply `None` on the platform Sift ships on: `os.sysconf` does not
    exist there, the guard above swallows the AttributeError, and the hardware report says the
    machine's memory is unknown. Nothing crashes, which is why it would go unnoticed, and
    "unknown" is what the worker sizing and the diagnostics would have to work from.

    `GlobalMemoryStatusEx` is the same physical total `sysconf` reports, from the same kind of
    place: the operating system, not a guess.
    """
    import ctypes
    from ctypes import wintypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = (
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullTotalPhys)
    except (OSError, AttributeError, ValueError):
        return None


def _installed_ram_bytes() -> int | None:
    """What the memory sticks in the machine add up to, or None where nothing will say.

    NOT THE SAME NUMBER AS `_total_ram_bytes`, AND THE DIFFERENCE IS WHY THIS EXISTS. What the
    operating system reports as physical memory is what it can ADDRESS, which is the installed
    total minus whatever the firmware and the hardware have reserved. On a machine with 32 GB in it
    that is a little under 31 GB, and beside the system's own dialog saying 32 GB the honest number
    reads as Sift being unable to count.

    Both are kept. The addressable figure is the one any sizing decision has to be made against,
    because it is the memory that actually exists to be spent; this one is what a person recognises
    as their machine, and it is the one the description of the machine should show.

    `GetPhysicallyInstalledSystemMemory` reads it out of the firmware's own table, in kilobytes. It
    is one call to a library already loaded, rather than a WMI query, which would be a subprocess
    and a second of startup.
    """
    if not _WINDOWS:
        # Linux has no equivalent that is not a guess: /proc/meminfo reports the same addressable
        # total, and reading DMI needs privileges a container does not have. Answering nothing is
        # better than answering the number that is already in the field beside it.
        return None
    import ctypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
        kilobytes = ctypes.c_ulonglong(0)
        if not kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kilobytes)):
            return None
        total = int(kilobytes.value) * 1024
        return total if total > 0 else None
    except (OSError, AttributeError, ValueError):
        return None


def _nvidia_present() -> bool:
    """Whether an NVIDIA GPU is here for the transcode ladder to use.

    THE POSIX BRANCH IS ALWAYS FALSE ON WINDOWS, which is the platform Sift ships on. There is no
    `/dev` there at all, so on its own it would answer "no NVIDIA card" on a machine with one, and
    since it gates which of ffmpeg's `*_nvenc` encoders count as usable, the ladder could never
    pick one.
    """
    if _WINDOWS:
        return _windows_nvidia_present()
    return any(os.path.exists(node) for node in ("/dev/nvidia0", "/dev/nvidiactl"))


def _windows_nvidia_present() -> bool:
    """Ask the driver's own management library how many NVIDIA devices there are.

    NOT a file-existence check, deliberately. The POSIX branch can look for a device node because
    one exists precisely when the driver is loaded; Windows has no equivalent path to test, and
    guessing at a DLL's presence would say yes for a driver that is installed and broken.
    `nvmlDeviceGetCount_v2` answers the question actually being asked (is there a working NVIDIA
    device) and it is the same library `nvidia-smi` is a front end for.

    Every failure is the same answer: no card. A missing library, a driver mid-upgrade, an
    initialisation that refuses: none of them are worth a crash at start-up over an optional
    accelerator, and the ladder's software path is always there behind it.
    """
    import ctypes

    try:
        nvml = ctypes.CDLL("nvml.dll")
    except OSError:
        return False
    if nvml.nvmlInit_v2() != 0:
        return False
    try:
        count = ctypes.c_uint(0)
        if nvml.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            return False
        return count.value > 0
    finally:
        # Refcounted inside NVML, so this releases exactly what the init above took and leaves any
        # other user of the library alone.
        nvml.nvmlShutdown()


#: Where Linux writes the processor's own name for itself. Read rather than shelled out to: this
#: runs at startup on every install, and a file read cannot hang the way a subprocess can.
_CPU_INFO = Path("/proc/cpuinfo")
_CPU_MODEL_FIELDS = ("model name", "Model", "Hardware", "cpu model")

#: Where Windows writes the same thing. The registry rather than `platform.processor()`, which
#: answers with the family and stepping ("AMD64 Family <n> Model <n> Stepping <n>") and not with
#: the name anybody would recognise; and rather than WMI, which is a subprocess.
_CPU_REGISTRY_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
_CPU_REGISTRY_VALUE = "ProcessorNameString"


def _windows_cpu_model() -> str | None:  # pragma: no cover (the other platform's branch)
    """What the processor calls itself on Windows, or None.

    `/proc/cpuinfo` does not exist on Windows, so the Linux probe alone would leave the
    Performance screen naming the machine's processor as unknown on every Windows install while
    reporting its core count correctly beside it.

    A registry read, for the reason the file read was chosen: it cannot hang the way a subprocess
    can, and this runs while the application is starting.
    """
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CPU_REGISTRY_KEY) as key:
            value, kind = winreg.QueryValueEx(key, _CPU_REGISTRY_VALUE)
    except OSError:
        return None
    if kind != winreg.REG_SZ or not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _cpu_model() -> str | None:
    """What the processor calls itself, or None.

    The field differs by architecture: x86 writes "model name", an ARM board often writes only
    "Model" or "Hardware", so several are tried and the first that answers wins. None is an
    ordinary answer: the screen shows the core count either way and simply says nothing about the
    make.
    """
    if _WINDOWS:
        return _windows_cpu_model()
    try:
        text = _CPU_INFO.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip() in _CPU_MODEL_FIELDS and value.strip():
            return value.strip()
    return None


#: How long to wait for the card to introduce itself. Startup work, and a driver in a bad state can
#: leave this hanging: a machine that boots slowly because of an optional line on a settings
#: screen is a worse outcome than a screen that says the card is present and unnamed.
_NVIDIA_SMI_TIMEOUT_SECONDS = 5


@dataclass(frozen=True, slots=True)
class Card:
    """One graphics adapter. Every field independently absent: an older driver reports less."""

    name: str | None = None
    driver: str | None = None
    vram_bytes: int | None = None
    can_compute: bool = False
    """Whether Sift could put a model on it, which today means an NVIDIA card with CUDA.

    A machine can have several adapters and only one of them do work: an AMD chip built into the
    processor and an NVIDIA card in a slot is the ordinary shape. Both belong in a description of
    the machine; only one of them is the answer to "which card will Smart Search run on", and a
    list that did not say which would be a list somebody has to guess at."""


async def _nvidia_identity() -> list[Card]:
    """Every card the driver reports: what each is called and how much memory it has.

    Only asked when a device node is already there, so this is not how Sift decides whether a card
    exists: it is how it says which one. Every failure is the same answer: a card with nothing
    filled in, and a screen that reports a card it cannot name.

    THE MEMORY IS THE FIGURE THAT DECIDES WHAT WILL FIT. A model that does not fit in the card's
    memory does not run slowly; it fails, at the point of loading, in a message from a C library,
    so "how much has it got" is the first question anybody asks of a machine before turning a
    feature on.

    `nounits` is asked for so the memory comes back as a bare number. It is reported in MEBIBYTES,
    which is the one thing about this output easy to get wrong by a factor of a million.
    """
    argv = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = await run_tool(argv, time_limit=_NVIDIA_SMI_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        log.info("hardware.gpu_unnamed", detail=str(exc))
        return []
    if result.returncode != 0:
        return []

    # ONE ROW PER CARD. Taking only the first would describe a two-card machine by half of it,
    # with nothing anywhere saying so.
    return [
        _card_from(line)
        for line in result.stdout.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]


def _card_from(line: str) -> Card:
    """One CSV row from nvidia-smi, as far as it can be read.

    Field by field rather than all-or-nothing: a driver that names the card but will not report its
    memory should still put the card's name on the screen. Older drivers answer `[N/A]` for a
    figure they do not have, which is why the memory is parsed rather than trusted.
    """
    fields = [part.strip() for part in line.split(",")]
    name = fields[0] if fields and fields[0] else None
    driver = fields[1] if len(fields) > 1 and fields[1] else None
    vram: int | None = None
    if len(fields) > 2:
        try:
            mebibytes = int(fields[2])
        except ValueError:
            mebibytes = 0
        if mebibytes > 0:
            vram = mebibytes * 1024 * 1024
    # Everything nvidia-smi answers is a card CUDA can be put on: it is the driver's own tool and
    # it lists nothing else.
    return Card(name=name, driver=driver, vram_bytes=vram, can_compute=True)


#: Adapters Windows lists that are not hardware anybody has.
#:
#: `Microsoft Remote Display Adapter` appears on every machine that has ever had a remote session,
#: and `Microsoft Basic Display Adapter` is what stands in before a real driver is installed. Both
#: are real entries and neither is a card; listing them makes a description of somebody's computer
#: read as though it has hardware it does not.
_NOT_REALLY_A_CARD = ("microsoft ",)

#: Where `AdapterRAM` stops being an answer.
#:
#: The field is 32 bits, so every card with 4 GB or more reports 4293918720 and no more. Above the
#: cap it says nothing except "at least four", which is not a
#: figure to put on a screen, and for the cards this matters most for, NVIDIA's own tool has the
#: real number anyway.
_ADAPTER_RAM_CAP = 4293918720


#: The display-adapter class in the registry, where the driver records what the card really has.
#:
#: `AdapterRAM` is 32 bits, so every card with 4 GB or more reports the same 4293918720, which is
#: why a 16 GB card would come out with no memory beside it at all. `HardwareInformation.qwMemorySize`
#: is 64 bits and is the real figure, the same one nvidia-smi reports for the card.
#:
#: BOTH ARE ASKED, and neither on its own is the answer. The registry keeps entries for drivers that
#: are no longer installed (stale ones for a remote-display adapter are common), so it cannot say
#: what is PRESENT. `Win32_VideoController` says exactly what is present and is the vague
#: one about memory. One invocation, joined on the name.
_ADAPTER_QUERY = (
    "$mem = @{};"
    " Get-ItemProperty"
    " 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*'"
    " -ErrorAction SilentlyContinue | ForEach-Object {"
    " if ($_.DriverDesc -and $_.'HardwareInformation.qwMemorySize')"
    " { $mem[$_.DriverDesc] = $_.'HardwareInformation.qwMemorySize' } };"
    " Get-CimInstance Win32_VideoController | ForEach-Object {"
    " '{0}|{1}|{2}|{3}' -f $_.Name, $_.DriverVersion, $mem[$_.Name], $_.AdapterRAM }"
)


async def _windows_display_adapters() -> list[Card]:
    """Every graphics adapter Windows knows about, whoever made it.

    THIS IS WHERE AN AMD CARD COMES FROM, and an Intel one, and the chip built into a processor.
    NVIDIA's own tool knows about NVIDIA cards and nothing else, so without this a machine with a
    Radeon in it would have a hardware report that did not mention it, which is a description of
    somebody's computer with a piece missing and nothing saying so.

    None of these can do work for Sift today: putting a model on a card means CUDA. They are here
    because a description of a machine should describe the machine, and because "which of these is
    the one Sift uses" is a question that cannot be asked of a list with one entry.
    """
    if not _WINDOWS:
        return []
    argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _ADAPTER_QUERY]
    try:
        result = await run_tool(argv, time_limit=_NVIDIA_SMI_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        log.info("hardware.adapters_unlisted", detail=str(exc))
        return []
    if result.returncode != 0:
        return []
    return _adapters_from(result.stdout.decode("utf-8", "replace").splitlines())


def _adapters_from(lines: Sequence[str]) -> list[Card]:
    """The adapter rows, as far as each can be read. See `_windows_display_adapters`."""
    found: list[Card] = []
    for line in lines:
        parts = [part.strip() for part in line.split("|")]
        name = parts[0] if parts and parts[0] else None
        if name is None or name.casefold().startswith(_NOT_REALLY_A_CARD):
            continue
        driver = parts[1] if len(parts) > 1 and parts[1] else None
        found.append(Card(name=name, driver=driver, vram_bytes=_memory_of(parts)))
    return found


def _memory_of(parts: Sequence[str]) -> int | None:
    """The card's memory: what the driver recorded, or the capped field, or nothing.

    The registry figure first, because it is 64 bits and is what the card actually has. The
    `AdapterRAM` field is the fallback for a driver that recorded nothing, and it is only believed
    BELOW the cap, because at the cap it means "four gigabytes or more" and nothing else.
    """
    recorded = _as_bytes(parts[2] if len(parts) > 2 else "")
    if recorded is not None:
        return recorded
    capped = _as_bytes(parts[3] if len(parts) > 3 else "")
    return capped if capped is not None and capped < _ADAPTER_RAM_CAP else None


def _as_bytes(field: str) -> int | None:
    """A size out of one field. None for anything that is not a positive number."""
    try:
        value = int(field.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _amd_compute_present() -> bool:
    # The AMD compute (ROCm) device. Distinct from the render node below, which is video only.
    return os.path.exists("/dev/kfd")


def _render_node_present() -> bool:
    try:
        return any(entry.name.startswith("renderD") for entry in Path("/dev/dri").iterdir())
    except OSError:
        return False


async def _ffmpeg_encoders(settings: Settings) -> frozenset[str]:
    """Ask ffmpeg which encoders it was built with. Empty set if it cannot be run.

    A missing or broken ffmpeg is not made fatal here: the rest of the app will notice when it
    tries to use it, with a better message than a probe could give. The probe's job is only to
    report what it found, so a failure to run means "no hardware encoders known", not a crash.
    """
    argv = [settings.ffmpeg_path, "-hide_banner", "-encoders"]
    try:
        result = await run_tool(argv, time_limit=_FFMPEG_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        log.warning("hardware.ffmpeg_unavailable", detail=str(exc))
        return frozenset()

    if result.returncode != 0:
        log.warning("hardware.ffmpeg_failed", returncode=result.returncode)
        return frozenset()

    return parse_encoders(result.stdout.decode("utf-8", "replace"))


def _acceleration_warnings(preference: str, *, cuda: bool, rocm: bool) -> tuple[str, ...]:
    """The plain-language notes for a GPU that was expected but is not there.

    Only fires when a specific accelerator was asked for (SIFT_GPU set to cuda or rocm), so "auto"
    never warns: on a machine with no GPU, the CPU is the right and expected answer, not a fault.
    """
    warnings: list[str] = []
    if preference == "cuda" and not cuda:
        warnings.append(
            "GPU acceleration was requested (cuda) but no NVIDIA GPU is visible to Sift. "
            "Running on the CPU, which is slower. If this machine has an NVIDIA GPU, check that "
            "the container was started with access to it."
        )
    if preference == "rocm" and not rocm:
        warnings.append(
            "GPU acceleration was requested (rocm) but no AMD GPU is visible to Sift. "
            "Running on the CPU, which is slower. If this machine has an AMD GPU, check that "
            "the container was started with access to it."
        )
    return tuple(warnings)


#: Bumped when what the three programs' answers are read into changes, so an older file is ignored.
_KEPT_FORMAT = 1


def _kept_key(settings: Settings, *, cuda: bool) -> str | None:
    """What the kept answers depend on: ffmpeg's file, and whether a card is there. None when
    ffmpeg cannot be found, which a kept answer must never hide."""
    found = shutil.which(settings.ffmpeg_path)
    if found is None:
        return None
    try:
        stat = os.stat(found)
    except OSError:
        return None
    return f"{_KEPT_FORMAT}|{found}|{stat.st_size}|{stat.st_mtime_ns}|{cuda}|{settings.gpu}"


async def _ask_the_programs(settings: Settings, *, cuda: bool) -> tuple[frozenset[str], list[Card]]:
    """The slow half of the probe: three programs, asked together rather than in turn."""

    async def nvidia() -> list[Card]:
        return await _nvidia_identity() if cuda else []

    encoders, named, adapters = await asyncio.gather(
        _ffmpeg_encoders(settings), nvidia(), _windows_display_adapters()
    )
    # THE TWO SOURCES, and the order is the answer to "which one does the work". NVIDIA's own tool
    # is exact about its own cards and knows about nothing else; Windows lists every adapter and is
    # vague about memory. The computable ones come first so that the card Sift uses is the card the
    # screen names first, and anything NVIDIA is dropped from the second list rather than appearing
    # twice under a slightly different name.
    cards = named + [
        one for one in adapters if not (one.name or "").casefold().startswith("nvidia")
    ]
    return encoders, cards


def _usable(encoders: frozenset[str], *, cuda: bool, render_node: bool) -> tuple[str, ...]:
    """The encoders ffmpeg was built with that a present device can run."""
    present = {_NVIDIA: cuda, _RENDER: render_node}
    return tuple(
        sorted(
            name
            for name, device in _TRANSCODE_ENCODERS.items()
            if name in encoders and present[device]
        )
    )


def _read_kept(kept: Path, key: str) -> tuple[frozenset[str], list[Card]] | None:
    try:
        held = json.loads(kept.read_text(encoding="utf-8"))
        if held["key"] != key:
            return None
        return frozenset(held["encoders"]), [Card(**one) for one in held["cards"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_kept(kept: Path, key: str, encoders: frozenset[str], cards: Sequence[Card]) -> None:
    body = {"key": key, "encoders": sorted(encoders), "cards": [asdict(one) for one in cards]}
    try:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps(body), encoding="utf-8")
    except OSError:
        log.info("hardware.kept_unwritten", exc_info=True)


async def probe(settings: Settings, *, kept: Path | None = None) -> HardwareReport:
    """Look at the machine once, and hand back what to do about it.

    Run at startup; the result is kept and read from. With `kept`, the three programs' answers of
    the last start are used when ffmpeg and the card are unchanged, and `reprobe` asks again after
    the start: nothing a request waits for depends on them.
    """
    cpu_count = os.cpu_count() or 1
    cpu_model = await asyncio.to_thread(_cpu_model)
    # Each of these asks the filesystem whether a device is there. Startup work rather than
    # serving work, so the cost is not the point: what matters is that the rule holds without
    # exception, because an exception is what the next one hides behind.
    cuda = await asyncio.to_thread(_nvidia_present)
    rocm = await asyncio.to_thread(_amd_compute_present)
    render_node = await asyncio.to_thread(_render_node_present)

    key = None if kept is None else await asyncio.to_thread(_kept_key, settings, cuda=cuda)
    held = None if kept is None or key is None else await asyncio.to_thread(_read_kept, kept, key)
    if held is None:
        encoders, cards = await _ask_the_programs(settings, cuda=cuda)
        if kept is not None and key is not None:
            await asyncio.to_thread(_write_kept, kept, key, encoders, cards)
    else:
        encoders, cards = held

    transcode_encoders = _usable(encoders, cuda=cuda, render_node=render_node)
    warnings = _acceleration_warnings(settings.gpu, cuda=cuda, rocm=rocm)

    report = HardwareReport(
        cpu_count=cpu_count,
        cpu_model=cpu_model,
        total_ram_bytes=_total_ram_bytes(),
        installed_ram_bytes=_installed_ram_bytes(),
        worker_concurrency=worker_concurrency(settings),
        cuda=cuda,
        rocm=rocm,
        gpu_cards=tuple(cards),
        # ONE driver serves every NVIDIA card in a machine, so it is a fact about the machine
        # rather than about a card, and the first one to report it is as good as any.
        gpu_driver=next((one.driver for one in cards if one.driver), None),
        transcode_encoders=transcode_encoders,
        warnings=warnings,
        answers_kept=held is not None,
    )

    # Loud, but not fatal: the warning is logged and also carried in the report to /health, and the
    # app keeps running on the CPU regardless.
    for note in warnings:
        log.warning("hardware.acceleration_unavailable", detail=note)
    log.info(
        "hardware.probe",
        cpu_count=report.cpu_count,
        total_ram_bytes=report.total_ram_bytes,
        worker_concurrency=report.worker_concurrency,
        cuda=report.cuda,
        rocm=report.rocm,
        transcode_encoders=list(report.transcode_encoders),
        kept=report.answers_kept,
    )
    return report


async def reprobe(settings: Settings, report: HardwareReport, *, kept: Path) -> bool:
    """Ask the three programs again after a start that used kept answers, and keep the new ones.

    True when they differ from what this run is using: the next start takes them, and the log says
    so now.
    """
    key = await asyncio.to_thread(_kept_key, settings, cuda=report.cuda)
    if key is None:
        return False
    encoders, cards = await _ask_the_programs(settings, cuda=report.cuda)
    await asyncio.to_thread(_write_kept, kept, key, encoders, cards)
    render_node = await asyncio.to_thread(_render_node_present)
    usable = _usable(encoders, cuda=report.cuda, render_node=render_node)
    changed = usable != report.transcode_encoders or tuple(cards) != report.gpu_cards
    if changed:
        log.info("hardware.changed", transcode_encoders=list(usable), cards=len(cards))
    return changed


def machine_name() -> str | None:
    """What this computer is CALLED, for a screen that has to say which computer it means.

    ## Why a screen needs it at all

    Sift's client is the same page whether it is opened in a browser on the machine holding the
    library or in the desktop application on another one. So "on a disk in this computer" is a
    sentence whose meaning depends on where the reader is sitting, and for the person sitting
    somewhere else it is simply wrong: the disk is in a computer, and it is not theirs. The name is
    the only thing that makes the sentence true in both places.

    ## The tension with the redaction policy, said out loud

    A hostname is treated as identifying everywhere else in this application: `kernel/log.py`
    substitutes it out of every log line, and `/health` is asserted not to contain it, because logs
    get pasted into issues and a hardware summary is a fingerprinting surface. This is a deliberate,
    narrow exception to that, and the difference is who is being shown it. Those two surfaces
    travel: off the machine, to strangers. This one goes to an ADMIN and to nobody else.

    A GUEST IS NEVER SHOWN IT. The one route that returns this is `GET /api/library/roots`, which
    takes `Depends(require_admin)` (a guest is refused before any of it is assembled) and the
    authorization matrix declares and probes it as admin-only. Nothing here should be copied into a
    log or a report.

    None when the machine has no name worth saying: `localhost`, a container's random hex id, or an
    empty answer. The screen then falls back to a sentence that names no machine.
    """
    try:
        name = socket.gethostname()
    except OSError:
        return None
    base = name.split(".")[0].strip()
    if not base or base.lower() == "localhost":
        return None
    # A container id: twelve hex characters, different on every run, and no use to anybody reading
    # it. The same test `kernel/log.py` applies for the opposite reason.
    if len(base) == 12 and all(c in "0123456789abcdef" for c in base.lower()):
        return None
    return base
