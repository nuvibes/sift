#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Say when the tunnel client Sift builds has something to move to.

The tunnel client is built here from a pinned source with two patches (scripts/vendor_build/wireproxy),
so nothing upstream publishes reaches it by itself: a release of the source, or an advisory against
one of the modules compiled into it, changes nothing until somebody moves the pin. This asks the two
questions that say a move is due, and exits 1 when either answers yes:

  * ADVISORIES. `govulncheck -mode=binary` reads the built file and reports every published
    advisory against the Go release and the modules inside it. The manifest's entry lists the ones
    already known and judged (`known_findings`); an id that is not in that list is new. An id in the
    list that is no longer reported has been closed, and the list is out of date.
  * THE SOURCE. The newest release of the repository the recipe clones, against the tag the
    manifest pins.

Both need the network (the vulnerability database, the repository's release list), which is why
this is a check run by hand and not for a commit. Without a way to ask, it exits 2 and
says which question went unanswered: an unanswered question is not a clean answer.

    python scripts/check_tunnel_client.py --govulncheck C:/path/to/govulncheck.exe
    python scripts/check_tunnel_client.py --skip-advisories      (the release question alone)

govulncheck is found from --govulncheck, then SIFT_GOVULNCHECK, then PATH. Install it with the Go
release the manifest pins: `go install golang.org/x/vuln/cmd/govulncheck@latest`.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "scripts" / "vendor_manifest.json"
VENDOR = ROOT / "vendor"

#: How long either question may take before it counts as unanswered.
ASK_SECONDS = 120


class Unanswered(Exception):
    """A question could not be asked. The message says which, and why."""


def built_entry(manifest: dict[str, Any], name: str = "wireproxy") -> dict[str, Any]:
    """The manifest's entry for a program built here."""
    for entry in manifest.get("built", []):
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    raise Unanswered(f"the manifest has no built entry named {name}")


def documents(text: str) -> Iterator[dict[str, Any]]:
    """Every JSON object in govulncheck's output, which is objects one after another, not a list."""
    decoder = json.JSONDecoder()
    at = 0
    while at < len(text):
        while at < len(text) and text[at].isspace():
            at += 1
        if at >= len(text):
            return
        found, at = decoder.raw_decode(text, at)
        if isinstance(found, dict):
            yield found


def findings_in(text: str) -> set[str]:
    """The advisory ids govulncheck reports as findings against the file."""
    ids: set[str] = set()
    for one in documents(text):
        finding = one.get("finding")
        if isinstance(finding, dict) and isinstance(finding.get("osv"), str):
            ids.add(finding["osv"])
    return ids


def judged(reported: set[str], known: set[str]) -> tuple[list[str], list[str]]:
    """The advisories that are new, and the known ones no longer reported."""
    return sorted(reported - known), sorted(known - reported)


def release_tag(document: dict[str, Any]) -> str:
    """The tag of a release as the repository's release list gives it."""
    tag = document.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise Unanswered("the release list answered without a tag")
    return tag


def releases_address(repository: str) -> str:
    """Where the newest release of a GitHub repository is read."""
    prefix = "https://github.com/"
    if not repository.startswith(prefix):
        raise Unanswered(f"the source is not a GitHub repository: {repository}")
    return f"https://api.github.com/repos/{repository[len(prefix) :].strip('/')}/releases/latest"


def _fetch(address: str) -> dict[str, Any]:
    if not address.startswith("https://api.github.com/"):
        raise Unanswered(f"not an address this asks: {address}")
    request = urllib.request.Request(address, headers={"Accept": "application/vnd.github+json"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=ASK_SECONDS) as answer:  # noqa: S310
            found = json.loads(answer.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise Unanswered(f"the release list could not be read: {error}") from error
    if not isinstance(found, dict):
        raise Unanswered("the release list answered with something that is not a release")
    return found


def _scan(tool: str, program: Path) -> str:
    try:
        done = subprocess.run(
            [tool, "-mode=binary", "-json", str(program)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=ASK_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unanswered(f"govulncheck could not be run: {error}") from error
    # It exits 3 when it has findings, which is an answer; anything else with no output is not.
    said = done.stdout or ""
    if done.returncode not in (0, 3) or not said.strip():
        raise Unanswered(
            f"govulncheck exited {done.returncode}: {(done.stderr or '').strip()[:300]}"
        )
    return said


def check(
    manifest: dict[str, Any],
    *,
    scan: Callable[[Path], str] | None,
    fetch: Callable[[str], dict[str, Any]] | None,
    vendor: Path = VENDOR,
) -> list[str]:
    """Every reason a move is due, as sentences. Empty when there is none.

    `scan` reads the built file and hands back govulncheck's output; `fetch` reads one address.
    Either left out skips its question.
    """
    entry = built_entry(manifest)
    due: list[str] = []
    if scan is not None:
        program = vendor / str(entry["file"])
        if not program.is_file():
            raise Unanswered(f"{program} is not there; build it with {entry['recipe']}")
        known = {str(one) for one in entry.get("known_findings", [])}
        new, closed = judged(findings_in(scan(program)), known)
        due += [f"a new advisory against the built tunnel client: {one}" for one in new]
        due += [
            f"{one} is listed as known and is no longer reported: take it out of known_findings"
            for one in closed
        ]
    if fetch is not None:
        source = entry["source"]
        newest = release_tag(fetch(releases_address(str(source["repository"]))))
        if newest != source["tag"]:
            due.append(
                f"the source has a release the pin is not at: {newest} (pinned {source['tag']})"
            )
    return due


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--govulncheck", help="the govulncheck executable")
    parser.add_argument("--skip-advisories", action="store_true")
    parser.add_argument("--skip-upstream", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scan: Callable[[Path], str] | None = None
    if not args.skip_advisories:
        tool = args.govulncheck or os.environ.get("SIFT_GOVULNCHECK") or shutil.which("govulncheck")
        if not tool:
            print("unanswered: govulncheck is not installed (see this script's head)")
            return 2
        scan = functools.partial(_scan, str(tool))

    try:
        due = check(manifest, scan=scan, fetch=None if args.skip_upstream else _fetch)
    except Unanswered as unanswered:
        print(f"unanswered: {unanswered}")
        return 2
    for one in due:
        print(one)
    if not due:
        print("the tunnel client has nothing to move to")
    return 1 if due else 0


if __name__ == "__main__":
    sys.exit(main())
