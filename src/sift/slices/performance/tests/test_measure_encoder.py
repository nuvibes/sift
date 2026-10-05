# SPDX-License-Identifier: AGPL-3.0-or-later
"""The GPU's previews, measured with the preview's own command: where it is not measured, the
ladder and its stops, a width whose encodes fail, and the previews it advises."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import media, sampling
from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.media import Encoder
from sift.slices.performance import measure_encoder as me
from sift.slices.performance.measure_models import NO_SOURCE
from sift.slices.performance.models import card_view


def a_machine(*, card: bool = True) -> HardwareReport:
    return HardwareReport(
        cpu_count=8,
        total_ram_bytes=None,
        worker_concurrency=4,
        cuda=card,
        rocm=False,
        transcode_encoders=("h264_nvenc",) if card else (),
        warnings=(),
    )


def a_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


def a_command(
    source: Path,
    destination: Path,
    *,
    pieces: Sequence[sampling.Piece],
    encoder: Encoder,
    device: str | None = None,
    decode: Sequence[str] = (),
    settings: Settings,
) -> list[str]:
    return [
        settings.ffmpeg_path,
        *decode,
        "-i",
        str(source),
        "-c:v",
        encoder.value,
        str(destination),
    ]


class Quiet:
    def start(self) -> None:
        pass

    def stop(self) -> tuple[int | None, int | None]:
        return None, 7_000_000


async def _measure(tmp_path: Path, *, cap: int = 4, **given: Any) -> me.CardCurve:
    """A ladder whose previews a second grow with the width up to `cap`, then stay there."""
    clock = [0.0]

    async def encode(_preview: Any, _source: Path, _into: Path, index: int, **_kwargs: Any) -> bool:
        # Width n takes n / min(n, cap) seconds in all: one second, then 1 / cap per preview.
        clock[0] += 1.0 if index == 0 else (0.0 if index < cap else 1 / cap)
        return index not in given.get("failing", ())

    return await me.measure_card(
        source=tmp_path / "clip.mp4",
        workspace=tmp_path,
        settings=a_settings(tmp_path),
        hardware=given.get("hardware", a_machine()),
        preview=a_command,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        repeats=1,
        busy=given.get("busy", lambda: False),
        watch=Quiet,
        encode=encode,
        clock=lambda: clock[0],
        fell_behind=given.get("fell_behind", lambda: 0),
        deadline=given.get("deadline"),
    )


async def test_no_gpu_encoder_is_not_measured_and_says_why(tmp_path: Path) -> None:
    curve = await me.measure_card(
        source=tmp_path,
        workspace=tmp_path,
        settings=a_settings(tmp_path),
        hardware=a_machine(card=False),
        preview=a_command,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
    )
    assert curve.failed == me.NO_CARD and curve.encoder == "libx264"
    assert curve.said() == [f"Previews on the GPU weren't measured: {me.NO_CARD}."]
    assert me.recommend_previews(curve, tasks=8, current={}, key="k", label="l") is None


async def test_no_clip_is_not_measured(tmp_path: Path) -> None:
    curve = await me.measure_card(
        source=None,
        workspace=tmp_path,
        settings=a_settings(tmp_path),
        hardware=a_machine(),
        preview=a_command,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
    )
    assert curve.failed == NO_SOURCE and curve.decodes_on_card


async def test_the_ladder_stops_where_it_stops_gaining_then_tries_the_midpoint(
    tmp_path: Path,
) -> None:
    curve = await _measure(tmp_path, cap=4)
    assert [level.at_once for level in curve.levels] == [1, 2, 4, 6, 8]
    assert [round(level.throughput, 2) for level in curve.levels] == [1, 2, 4, 4, 4]
    assert curve.best is not None and curve.best.at_once == 4
    assert curve.levels[0].card_memory_bytes == 7_000_000
    assert curve.said() == []


async def test_a_width_whose_encodes_fail_is_not_counted_and_nothing_wider_is_tried(
    tmp_path: Path,
) -> None:
    curve = await _measure(tmp_path, cap=16, failing=frozenset({5}))
    assert [level.at_once for level in curve.levels] == [1, 2, 3, 4, 8]
    eight = curve.levels[-1]
    assert eight.finished == 7 and not eight.counted
    assert curve.best is not None and curve.best.at_once == 4
    assert curve.said() == [
        "On the GPU, 1 of 8 previews built at the same time failed, so 8 wasn't counted and "
        "nothing wider was tried."
    ]
    found = me.recommend_previews(curve, tasks=12, current={"k": 8}, key="k", label="Previews")
    assert found is not None and (found.current, found.suggested) == (8, 4)
    assert "wider failed, or Sift stopped keeping up" in found.reason


async def test_a_busy_width_is_taken_once_and_marked(tmp_path: Path) -> None:
    answers = iter([True] + [False] * 20)
    curve = await _measure(tmp_path, cap=1, busy=lambda: next(answers))
    assert [level.busy for level in curve.levels] == [True, False]
    assert curve.said() == [
        "The GPU at 1 at the same time was measured while other programs were busy."
    ]


async def test_previews_are_advised_from_the_gpu_and_never_above_the_tasks(tmp_path: Path) -> None:
    curve = await _measure(tmp_path, cap=16)
    found = me.recommend_previews(curve, tasks=5, current={}, key="k", label="Previews")
    assert found is not None and (found.current, found.suggested) == (0, 5)
    assert "(h264_nvenc, decoding on the GPU): 16 at the same time" in found.reason
    assert "nothing wider was tried" in found.reason and "The task count, 5" in found.reason
    flat = await _measure(tmp_path, cap=2)
    advised = me.recommend_previews(flat, tasks=8, current={}, key="k", label="Previews")
    assert advised is not None and advised.suggested == 2
    assert "added under 5%" in advised.reason
    assert me.recommend_previews(None, tasks=8, current={}, key="k", label="l") is None


async def test_the_processor_compared_on_the_same_command_drops_the_gpus_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []

    async def run(argv: list[str], **_kwargs: Any) -> bytes:
        ran.append(argv)
        if "-hwaccel" in argv and len(ran) > 1:
            raise media.FFmpegError("no session")
        return b""

    monkeypatch.setattr(media, "run", run)
    settings = a_settings(tmp_path)
    source = await me.build_source(tmp_path, settings)
    assert source.name == "self-test-1080p.mp4" and "testsrc2=size=1920x1080" in " ".join(ran[0])
    done = await me._encode(
        a_command, source, tmp_path, 3, encoder=Encoder.CPU, decode=(), settings=settings
    )
    assert done and "-hwaccel" not in ran[-1] and ran[-1][-1].endswith("card-3.mp4")
    failed = await me._encode(
        a_command,
        source,
        tmp_path,
        0,
        encoder=Encoder.NVENC,
        decode=("-hwaccel", "cuda"),
        settings=settings,
    )
    assert not failed
    curve = await me.measure_card(
        source=source,
        workspace=tmp_path,
        settings=settings,
        hardware=a_machine(),
        preview=a_command,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        encoder=Encoder.CPU,
        levels=(1,),
        repeats=1,
        busy=lambda: False,
        watch=Quiet,
    )
    assert curve.encoder == "libx264" and not curve.decodes_on_card and curve.levels[0].counted


def test_the_wire_carries_the_gpus_ladder() -> None:
    curve = me.CardCurve(
        encoder="h264_nvenc",
        decodes_on_card=True,
        levels=(
            me.CardLevel(at_once=1, seconds=2.0, finished=1, card_memory_bytes=1_128_000_000),
            me.CardLevel(at_once=2, seconds=0.0, finished=1, worst_lag_seconds=1.0),
        ),
    )
    view = card_view(curve)
    assert view is not None and view.best_at_once == 1
    assert view.levels[0].card_megabytes == 1128 and view.levels[0].per_second == 0.5
    assert view.levels[1].per_second == 0.0 and not view.levels[1].responsive
    assert card_view(None) is None


async def test_a_width_where_the_server_fell_behind_is_not_counted(tmp_path: Path) -> None:
    stalls = iter([0, 0, 0, 2])
    curve = await _measure(tmp_path, fell_behind=lambda: next(stalls))
    assert [(one.at_once, one.fell_behind, one.counted) for one in curve.levels] == [
        (1, 0, True),
        (2, 2, False),
    ]
