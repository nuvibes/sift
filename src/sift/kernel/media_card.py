# SPDX-License-Identifier: AGPL-3.0-or-later
"""The graphics card: which encoder and decoder to ask it for, and when to stop asking."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path

from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger

log = get_logger("sift.kernel.media")


class FFmpegError(Exception):
    """ffmpeg or ffprobe failed, or could not be run at all; carries the tool's own stderr."""


class Encoder(StrEnum):
    """The video encoders Sift will ask for: H.264 only, the one codec every browser decodes."""

    CPU = "libx264"
    NVENC = "h264_nvenc"
    QSV = "h264_qsv"
    VAAPI = "h264_vaapi"


#: Which hardware encoder to prefer when a machine has several.
ENCODER_PREFERENCE: tuple[Encoder, ...] = (Encoder.NVENC, Encoder.QSV, Encoder.VAAPI)


def choose_encoder(report: HardwareReport) -> Encoder:
    """The best encoder this machine can run: built into ffmpeg and with its device present."""
    usable = set(report.transcode_encoders)
    for encoder in ENCODER_PREFERENCE:
        if encoder.value in usable:
            return encoder
    return Encoder.CPU


def decode_flags(report: HardwareReport) -> tuple[str, ...]:
    """Input flags that move decoding onto an NVIDIA card, for a process decoding a run of video.

    `cuda` by name: `auto` silently does nothing for AV1. A single frame does not pay back the
    card's setup, and a codec the card cannot decode falls back to software by itself.
    """
    return ("-hwaccel", "cuda") if report.cuda else ()


#: How many refusals in a row before the card stops being asked; any success resets it.
GIVE_UP_AFTER = 3


#: A frame with a side shorter than this goes to the processor: a card refuses tiny frames, and
#: that refusal must not count against the card.
CARD_SMALLEST_SIDE = 160


class Accelerator:
    """What this machine's graphics card is worth asking for, and whether it still is.

    Three refusals in a row that the processor then succeeded at, and the card stops being asked for
    the session: a failure on both paths is about the file, not the card. One per process, not
    locked: every worker is a task on one loop.
    """

    def __init__(self, report: HardwareReport, *, patience: int = GIVE_UP_AFTER) -> None:
        self._encoder = choose_encoder(report)
        self._decode = decode_flags(report)
        self._patience = max(1, patience)
        self._failures = 0
        self._given_up = False

    @property
    def offers_hardware(self) -> bool:
        """Whether anything here is still being asked of the card."""
        return not self._given_up and (self._encoder is not Encoder.CPU or bool(self._decode))

    @property
    def state(self) -> str:
        """What the card is doing: `on`, `off`, or `latched_off` once it was given up on."""
        if self._given_up:
            return "latched_off"
        if self._encoder is Encoder.CPU and not self._decode:
            return "off"
        return "on"

    @property
    def encoder(self) -> Encoder:
        """The encoder to use now. The processor, once the card has been given up on."""
        return Encoder.CPU if self._given_up else self._encoder

    @property
    def decode(self) -> tuple[str, ...]:
        """The decode flags to use now. Nothing, once the card has been given up on."""
        return () if self._given_up else self._decode

    async def run[T](
        self,
        attempt: Callable[[Encoder, tuple[str, ...]], Awaitable[T]],
        *,
        frame: tuple[int | None, int | None] | None = None,
    ) -> T:
        """Do the work on the card, and again on the processor if the card would not, keeping count.

        `ValueError` counts as a refusal. A frame under `CARD_SMALLEST_SIDE` goes to the processor.
        """
        refusal: Exception | None = None
        if self.offers_hardware and not _too_small_for_the_card(frame):
            try:
                result = await attempt(self.encoder, self.decode)
            except (FFmpegError, ValueError) as exc:
                refusal = exc
            else:
                self._failures = 0
                return result

        result = await attempt(Encoder.CPU, ())
        # Reached only when the processor succeeded, so a refusal above was the card's.
        if refusal is not None:
            self._blame(refusal)
        return result

    def _blame(self, refusal: Exception) -> None:
        """Record a failure the processor went on to prove was the card's."""
        self._failures += 1
        log.warning(
            "media.accelerator_fell_back",
            encoder=self._encoder.value,
            hardware_decode=bool(self._decode),
            failures=self._failures,
            reason=str(refusal),
        )
        if self._failures < self._patience:
            return

        self._given_up = True
        # Once: the sentence that explains every slow thing afterwards.
        log.warning(
            "media.accelerator_given_up",
            encoder=self._encoder.value,
            hardware_decode=bool(self._decode),
            failures=self._failures,
            detail=(
                "The graphics card refused this work several times in a row and the processor "
                "did it instead. Sift will stop asking the card until it is restarted."
            ),
        )


def _too_small_for_the_card(frame: tuple[int | None, int | None] | None) -> bool:
    if frame is None:
        return False
    return any(side is not None and 0 < side < CARD_SMALLEST_SIDE for side in frame)


def render_node() -> str | None:
    """The first DRI render node VAAPI and Quick Sync encode through, if there is one."""
    try:
        nodes = sorted(
            entry for entry in Path("/dev/dri").iterdir() if entry.name.startswith("renderD")
        )
    except OSError:
        return None
    return str(nodes[0]) if nodes else None
