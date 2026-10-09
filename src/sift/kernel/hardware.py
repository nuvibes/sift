# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift is running on, and what to do about it, asked once of the machine.

A GPU is never required and never pretended; one expected and missing is said in plain words."""

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

# A flag, as mypy calls a literal platform test's other branch unreachable.
_WINDOWS = sys.platform == "win32"

log = get_logger(__name__)

# More workers stop helping: jobs are mostly ffmpeg and disk.
MAX_WORKERS = 8

# Only a guard against a wedged binary; listing encoders does no work.
_FFMPEG_TIMEOUT_SECONDS = 10.0

# Hardware encoders and the device each needs: usable only when compiled in and the device exists.
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
    """How many jobs to run together, leaving one core for the server and the database."""
    if settings.worker_concurrency is not None:
        return settings.worker_concurrency

    cores = os.cpu_count() or 1
    return max(1, min(MAX_WORKERS, cores - 1))


@dataclass(frozen=True)
class HardwareReport:
    """What the machine turned out to be, built once at startup so every part reads one answer."""

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

    # The make-and-model fields, read by an admin and decided from by nothing.
    cpu_model: str | None = None
    """What the processor calls itself, or None where the platform will not say."""

    @property
    def profile(self) -> str:
        """A stable name for this shape of machine, from only what decides speed."""
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
        """The same machine for somebody to read; never used to tell two profiles apart."""
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
        """The card Sift would put a model on, which is not always the first listed."""
        return next((one for one in self.gpu_cards if one.can_compute), None)

    @property
    def gpu_name(self) -> str | None:
        """What that card is called; None where nothing here can do the work."""
        card = self._working_card
        return card.name if card else None

    @property
    def gpu_vram_bytes(self) -> int | None:
        """Its memory, which decides what fits: a model too big fails as it loads."""
        card = self._working_card
        return card.vram_bytes if card else None

    @property
    def gpu_transcode(self) -> bool:
        """Whether any hardware video encoder is actually usable on this machine."""
        return bool(self.transcode_encoders)

    def as_dict(self) -> dict[str, Any]:
        """The shape served at /health, with nothing that identifies the machine."""
        return {
            "cpu_count": self.cpu_count,
            "total_ram_bytes": self.total_ram_bytes,
            "worker_concurrency": self.worker_concurrency,
            "gpu": {"cuda": self.cuda, "rocm": self.rocm},
            "transcode_encoders": list(self.transcode_encoders),
            "warnings": list(self.warnings),
        }


def parse_encoders(text: str) -> frozenset[str]:
    """The encoder names in `ffmpeg -encoders` output, only after the dashes; tested on captures."""
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
    """Total physical memory, or None; the host's on a container, as it is only diagnostic."""
    if _WINDOWS:
        return _windows_total_memory()
    try:
        # Both codes, so neither the Linux nor the Windows check complains.
        page = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined, unused-ignore]
        pages = os.sysconf("SC_PHYS_PAGES")  # type: ignore[attr-defined, unused-ignore]
        return int(page) * int(pages)
    except (AttributeError, ValueError, OSError):
        return None


def _windows_total_memory() -> int | None:
    """How much memory the machine has, asked the way Windows answers it."""
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
    """What the memory sticks add up to, unlike the addressable total; None where unknown."""
    if not _WINDOWS:
        # Linux has no equivalent that is not a guess.
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
    """Whether an NVIDIA GPU is here for the transcode ladder; Windows has no /dev to look in."""
    if _WINDOWS:
        return _windows_nvidia_present()
    return any(os.path.exists(node) for node in ("/dev/nvidia0", "/dev/nvidiactl"))


def _windows_nvidia_present() -> bool:
    """Ask NVML how many NVIDIA devices work; every failure means no card."""
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
        # Refcounted, so this releases only what the init above took.
        nvml.nvmlShutdown()


#: Read rather than shelled out to, as a file read cannot hang at startup.
_CPU_INFO = Path("/proc/cpuinfo")
_CPU_MODEL_FIELDS = ("model name", "Model", "Hardware", "cpu model")

#: The registry, as `platform.processor()` gives only family and stepping, and WMI is a subprocess.
_CPU_REGISTRY_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
_CPU_REGISTRY_VALUE = "ProcessorNameString"


def _windows_cpu_model() -> str | None:  # pragma: no cover (the other platform's branch)
    """What the processor calls itself on Windows, or None; a registry read cannot hang."""
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
    """What the processor calls itself, or None; the first field that answers wins."""
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


#: A driver in a bad state can hang this, and startup must not wait for an optional name.
_NVIDIA_SMI_TIMEOUT_SECONDS = 5


@dataclass(frozen=True, slots=True)
class Card:
    """One graphics adapter; every field may be absent, as older drivers report less."""

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
    """Every card the driver reports with its name and memory; memory arrives in mebibytes."""
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

    # One row per card, so a two-card machine is described whole.
    return [
        _card_from(line)
        for line in result.stdout.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]


def _card_from(line: str) -> Card:
    """One nvidia-smi CSV row, field by field, as old drivers answer `[N/A]`."""
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
    # nvidia-smi lists only cards CUDA can use.
    return Card(name=name, driver=driver, vram_bytes=vram, can_compute=True)


#: Adapters Windows lists that are not hardware: remote display, and the basic stand-in.
_NOT_REALLY_A_CARD = ("microsoft ",)

#: `AdapterRAM` is 32 bits, so at this cap it only says "at least four gigabytes".
_ADAPTER_RAM_CAP = 4293918720


#: The registry knows real memory but keeps stale drivers; Windows' list knows what is present.
#: Both are asked in one invocation and joined on the name.
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
    """Every graphics adapter Windows knows of, so an AMD or Intel one is described too."""
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
    """The adapter rows, as far as each can be read."""
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
    """The card's memory: the 64-bit registry figure, else `AdapterRAM` below its cap."""
    recorded = _as_bytes(parts[2] if len(parts) > 2 else "")
    if recorded is not None:
        return recorded
    capped = _as_bytes(parts[3] if len(parts) > 3 else "")
    return capped if capped is not None and capped < _ADAPTER_RAM_CAP else None


def _as_bytes(field: str) -> int | None:
    """A size out of one field; None for anything that is not a positive number."""
    try:
        value = int(field.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _amd_compute_present() -> bool:
    # The AMD compute device, apart from the video-only render node.
    return os.path.exists("/dev/kfd")


def _render_node_present() -> bool:
    try:
        return any(entry.name.startswith("renderD") for entry in Path("/dev/dri").iterdir())
    except OSError:
        return False


async def _ffmpeg_encoders(settings: Settings) -> frozenset[str]:
    """The encoders ffmpeg was built with, or an empty set; a broken ffmpeg is not fatal here."""
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
    """Notes for a GPU asked for and missing; "auto" never warns, as the CPU is then expected."""
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


#: Bumped when the kept answers' shape changes, so an older file is ignored.
_KEPT_FORMAT = 1


def _kept_key(settings: Settings, *, cuda: bool) -> str | None:
    """What the kept answers depend on; None when ffmpeg cannot be found, never hidden."""
    found = shutil.which(settings.ffmpeg_path)
    if found is None:
        return None
    try:
        stat = os.stat(found)
    except OSError:
        return None
    return f"{_KEPT_FORMAT}|{found}|{stat.st_size}|{stat.st_mtime_ns}|{cuda}|{settings.gpu}"


async def _ask_the_programs(settings: Settings, *, cuda: bool) -> tuple[frozenset[str], list[Card]]:
    """The slow half of the probe: three programs, asked together."""

    async def nvidia() -> list[Card]:
        return await _nvidia_identity() if cuda else []

    encoders, named, adapters = await asyncio.gather(
        _ffmpeg_encoders(settings), nvidia(), _windows_display_adapters()
    )
    # NVIDIA's exact list first, so the card Sift uses is named first; no NVIDIA card twice.
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
    """Look at the machine once at startup, reusing last start's program answers when unchanged."""
    cpu_count = os.cpu_count() or 1
    cpu_model = await asyncio.to_thread(_cpu_model)
    # Each asks the filesystem whether a device is there.
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
        # One driver serves every NVIDIA card, so the first to report it serves.
        gpu_driver=next((one.driver for one in cards if one.driver), None),
        transcode_encoders=transcode_encoders,
        warnings=warnings,
        answers_kept=held is not None,
    )

    # Loud, not fatal: logged and carried to /health while the app runs on the CPU.
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
    """Ask the programs again after a start that used kept answers; True when they changed."""
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
    """What this computer is called, for an admin's screen; None for no usable name.

    A narrow exception to redacting hostnames: only the admin-only roots route returns it."""
    try:
        name = socket.gethostname()
    except OSError:
        return None
    base = name.split(".")[0].strip()
    if not base or base.lower() == "localhost":
        return None
    # A container id: twelve hex characters, no use to anybody.
    if len(base) == 12 and all(c in "0123456789abcdef" for c in base.lower()):
        return None
    return base
