# SPDX-License-Identifier: AGPL-3.0-or-later
"""A run as the plain text a person copies: every line a fact the ledger's table holds."""

from __future__ import annotations

from collections.abc import Mapping

from sift.kernel.jobs.failure_words import KINDS, OTHERWISE
from sift.kernel.jobs.families import FAMILY_LABELS, Family
from sift.kernel.jobs.run_records import RunRecord
from sift.kernel.when import stamp as machine_stamp


def _took(seconds: int | None) -> str:
    if seconds is None:
        return "still going"
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} hr"


def _size(count: int) -> str:
    if count >= 1_000_000_000_000:
        return f"{count / 1_000_000_000_000:.2f} TB"
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f} GB"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.0f} MB"
    return f"{count} bytes"


_FAILED = {one.words for one in KINDS} | {OTHERWISE}


def _label(family: str) -> str:
    """A family retired from the enum still reports, under the name its row has."""
    try:
        return FAMILY_LABELS[Family(family)]
    except ValueError:
        return family


def _time_left_lines(scored: Mapping[str, object]) -> list[str]:
    """How often a run's time left held its real finish, and how long it waited for other work."""
    lines = []
    if scored.get("right") is not None:
        lines.append(f"Its time left was right in {scored['right']}% of minutes.")
    if stalled := int(str(scored.get("stalled") or 0)):
        lines.append(
            f"It waited for other work for {stalled:,} minute{'s' if stalled > 1 else ''}."
        )
    return lines


def report_text(run: RunRecord) -> str:
    """A run as plain text to paste: every line a fact the table holds, in ASCII."""
    when = machine_stamp(run.started_at, "%Y-%m-%d %H:%M")
    lines = [
        f"Sift {run.version}, {_label(run.family)} run, {when}, took {_took(run.seconds)}"
        + (" (stopped by hand)" if run.stopped else ""),
        f"Machine: {run.machine or 'unknown'}",
    ]
    if run.settings:
        said = ", ".join(f"{key} {value}" for key, value in sorted(run.settings.items()))
        lines.append(f"Settings: {said}")
    # A kind nobody named with no bytes is a job about no file (a walk): nothing to say of it.
    known = {k: one for k, one in run.files.items() if k != "unknown" or one.get("bytes")}
    kinds = ", ".join(
        f"{int(one.get('n', 0)):,} {kind} ({_size(int(one.get('bytes', 0)))})"
        for kind, one in sorted(known.items())
    )
    if kinds or not run.files:
        lines.append(f"Files: {kinds or 'none recorded'}")
    lines.append(f"Jobs: {run.jobs_done:,} done, {run.jobs_failed:,} failed")
    lines += _time_left_lines(run.time_left or {})
    ended = sorted(run.ended_with.items(), key=lambda one: (-one[1], one[0]))
    lines += [
        f"Why {n:,} failed: {words}"
        if words in _FAILED
        else f"{'Ended' if n == 1 else f'{n:,} ended'} with: {words}"
        for words, n in ended
    ]
    took = run.seconds
    if took and took > 0 and run.jobs_done:
        moved = f", {_size(int(run.bytes_total * 60 / took))}/min" if run.bytes_total else ""
        lines.append(f"Pace: {run.jobs_done * 60 / took:.1f} jobs/min{moved}")
    for kind, one in sorted(known.items()):
        n = int(one.get("n", 0))
        if n:
            lines.append(
                f"Per {kind}: {int(one.get('ms', 0)) / n / 1000:.1f} s of worker time each"
            )
    if run.products:
        lines.append("Built (worker time):")
        for product, one in sorted(
            run.products.items(), key=lambda item: -int(item[1].get("ms", 0))
        ):
            n = int(one.get("n", 0))
            ms = int(one.get("ms", 0))
            each = f", {ms / n / 1000:.1f} s each" if n else ""
            lines.append(f"  {product}: {n:,} files, {ms / 1000:.0f} s{each}")
    if run.stages:
        lines.append("Stages (worker time):")
        for stage, one in sorted(run.stages.items(), key=lambda item: -int(item[1].get("ms", 0))):
            n = int(one.get("n", 0))
            ms = int(one.get("ms", 0))
            each = f", {ms / n / 1000:.2f} s each" if n else ""
            lines.append(f"  {stage}: {ms / 1000:.0f} s over {n:,}{each}")
    return "\n".join(lines)
