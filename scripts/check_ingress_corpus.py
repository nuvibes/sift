# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the ingress gate over the whole fixture corpus and report any disagreement.

The unit tests run against whatever ffprobe resolves where they run. This puts the whole corpus
through the gate with the one the installer ships, the build in vendor/bin, which Sift's own
settings find in a checkout as they do in an installed copy. A decoder check verified against a
decoder nobody runs proves nothing about the decoder everybody runs.

The verdict comes from the filename: anything whose name begins with `accepted` must get in, and
everything else must not. A prefix rather than `accepted.<extension>` because one extension can hold
more than one format: a `.webp` is a still or an animation and they take different paths.

    python scripts/check_ingress_corpus.py <corpus-directory>
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.ingress import IngressRejected, Origin, verify_decodable, verify_ingress


def stage(source: Path, work: Path) -> Path:
    """A copy to work on. The gate moves what it rejects, and the corpus is the checkout's own."""
    scratch = work / source.name
    shutil.copy(source, scratch)
    return scratch


async def verdict(source: Path, settings: Settings, work: Path) -> tuple[bool, str]:
    """Whether the gate lets this file in, and what it said about it."""
    candidate = stage(source, work)
    try:
        result = verify_ingress(candidate, origin=Origin.DOWNLOAD, settings=settings)
        await verify_decodable(result, settings=settings)
    except IngressRejected as rejected:
        return False, str(rejected.reason)
    return True, result.media.name


async def check(corpus: list[Path], settings: Settings, work: Path) -> list[str]:
    failures = []
    for source in corpus:
        expected = source.name.startswith("accepted")
        accepted, detail = await verdict(source, settings, work)

        mark = "ok  " if accepted == expected else "FAIL"
        state = "accepted" if accepted else "rejected"
        print(f"{mark} {source.name:32} {state:9} {detail}")

        if accepted != expected:
            failures.append(source.name)
    return failures


def main(corpus_directory: Path) -> int:
    corpus = sorted(corpus_directory.iterdir())
    if not corpus:
        print(f"no fixtures in {corpus_directory}")
        return 1

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        settings = Settings(data_dir=root / "data", cache_dir=root / "cache")
        settings.quarantine_dir.mkdir(parents=True)
        work = root / "work"
        work.mkdir()

        failures = asyncio.run(check(corpus, settings, work))

    if failures:
        print(f"\n{len(failures)} fixture(s) disagree with the shipped ffmpeg: {failures}")
        return 1

    print(f"\nthe ingress gate agrees with the shipped ffmpeg on all {len(corpus)} fixtures")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: check_ingress_corpus.py <corpus-directory>")
    raise SystemExit(main(Path(sys.argv[1])))
