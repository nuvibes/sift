# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing a built release: minisign asked again after a wrong password, and the one change a
build itself writes into the tree told apart from a change nobody committed."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

#: How many times minisign asks for the key's password before the release stops.
TRIES = 3

#: The upgrade fixture's folder in the tree, as `git status` names it.
FIXTURES = "tests/integration/data/"
_FIXTURE = re.compile(r"^library-[0-9A-Za-z.+-]+\.sql\.gz$")


def sign(
    sums: Path,
    manifest: Path,
    *,
    minisign: str,
    key: Path,
    run: Callable[..., str],
    failed: type[Exception],
    tries: int = TRIES,
) -> list[Path]:
    """Minisign over the hash file and the manifest, never over the installer, asked again on a
    refusal. `-l`, the legacy form, because the desktop application refuses a prehashed one."""
    if not key.is_file():
        raise failed(
            f"there is no signing key at {key}.\n"
            "  Make one once, back it up, and never lose it: every release is verified against "
            "the public key already shipped, so a lost key means no further update can be "
            "verified:\n"
            f"    minisign -G -s {key} -p {key.with_suffix('.pub')}\n"
            "  Or pass --no-sign to build without one."
        )
    command = [minisign, "-S", "-l", "-s", str(key), "-m", str(sums), str(manifest)]
    for attempt in range(1, tries + 1):
        try:
            run(command, where=sums.parent)
            break
        except failed:
            if attempt == tries:
                raise
            print(f"\n  minisign did not sign ({attempt} of {tries} tries). The password again:")
    signatures = [one.with_name(f"{one.name}.minisig") for one in (sums, manifest)]
    for signature in signatures:
        if not signature.is_file():
            raise failed(f"minisign reported success and wrote no {signature.name}.")
    return signatures


def written_by_the_build(line: str, version: str) -> bool:
    """Whether one `git status --porcelain` line is the build's own upgrade fixture: this version's,
    added or replaced, or an older one it deleted."""
    state, path = line[:2], line[3:]
    if not path.startswith(FIXTURES) or not _FIXTURE.match(path[len(FIXTURES) :]):
        return False
    return path == f"{FIXTURES}library-{version}.sql.gz" or state.strip() == "D"
