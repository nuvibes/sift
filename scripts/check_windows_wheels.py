#!/usr/bin/env python3
"""Prove every dependency has a Windows x64 wheel at the pinned interpreter.

The desktop application bundles its own CPython, so the whole dependency
set has to be installable from wheels on Windows: there is no compiler on a user's machine and
there never will be.

WHY A SUCCESSFUL RESOLVE IS NOT ENOUGH
--------------------------------------
`uv pip compile` proves the version constraints are mutually satisfiable. It says nothing about
whether a built artefact exists: a source-only package resolves perfectly and then demands a C
toolchain at install time. Half this dependency set is compiled (onnxruntime, sqlite-vec,
sentencepiece, numpy, blake3, cryptography, curl-cffi), and compiled packages lag a new Python
release by months. So this asks PyPI for the actual files.

Run it whenever the dependency set moves, or when considering a newer interpreter.

Usage:  python scripts/check_windows_wheels.py [--extra dev]

The interpreter comes from `.python-version` unless --python-version overrides it, which is what
you want when the question is whether a NEWER one is usable yet.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLATFORM = "x86_64-pc-windows-msvc"


def declared_python() -> str:
    """The interpreter this repository ships, from the one file that declares it.

    Not a literal written here as well as in scripts/release.py and in the workflow, where copies
    drift apart. This gate exists to answer whether a given interpreter is installable, so a stale
    copy of that answer living inside it is the worst place of all for one to sit.
    """
    return (ROOT / ".python-version").read_text(encoding="utf-8").strip()


def resolve(py: str, extras: list[str]) -> list[tuple[str, str]]:
    """Ask uv for the full pinned set for Windows at this interpreter."""
    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as tmp:
        out = Path(tmp.name)
    cmd = [
        "uv",
        "pip",
        "compile",
        str(ROOT / "pyproject.toml"),
        "--python-platform",
        PLATFORM,
        "--python-version",
        py,
        "--no-header",
        "-q",
        "-o",
        str(out),
    ]
    for e in extras:
        cmd += ["--extra", e]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"\n  resolution failed for {py} / {PLATFORM}:\n{proc.stderr}\n")
    pins = []
    for line in out.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9._-]+)==([^\s;]+)", line.split("#")[0].strip())
        if m:
            pins.append((m.group(1), m.group(2)))
    out.unlink(missing_ok=True)
    return pins


def usable_wheel(files: list[str], cp: str) -> str | None:
    """Is one of these wheels installable on Windows x64 at cp<version>?

    A wheel's filename ends in <python tags>-<abi tags>-<platform>. It is usable when the platform
    is win_amd64 (or 'any', for a pure-Python package) AND a python tag admits this interpreter,
    either exactly, or via the stable ABI, where a wheel built for an OLDER cp works on every
    later one.
    """
    for fn in files:
        parts = fn[:-4].split("-")
        if len(parts) < 3:
            continue
        pytags, abitags, plat = parts[-3], parts[-2], parts[-1]
        if plat != "win_amd64" and plat != "any":
            continue
        for tag in pytags.split("."):
            if tag in (f"cp{cp}", "py3", "cp3"):
                return fn
            if "abi3" in abitags and tag.startswith("cp3"):
                try:
                    if int(tag[2:]) <= int(cp):
                        return fn
                except ValueError:
                    pass
    return None


#: How many times the index is asked about one package before the lookup counts as failed.
LOOKUP_ATTEMPTS = 3


def check(item: tuple[str, str], cp: str) -> tuple[str, str, str, str]:
    name, version = item
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    data = None
    for attempt in range(LOOKUP_ATTEMPTS):
        try:
            # The address is built from the https literal above and two names out of a resolution
            # this script just ran; there is no scheme here for anything else to come in through.
            with urllib.request.urlopen(url, timeout=60) as r:
                data = json.load(r)
            break
        except Exception as exc:
            # A dropped connection is the index's or the runner's, not the package's: asked again,
            # a little later, before it is reported as anything.
            if attempt + 1 == LOOKUP_ATTEMPTS:
                return (name, version, "LOOKUP-FAILED", str(exc))
            time.sleep(2**attempt)
    if data is None:
        return (name, version, "LOOKUP-FAILED", "no answer")
    wheels = [f["filename"] for f in data["urls"] if f["filename"].endswith(".whl")]
    if not wheels:
        return (name, version, "SOURCE-ONLY", "no wheel published at all")
    hit = usable_wheel(wheels, cp)
    if hit:
        return (name, version, "OK", hit)
    return (name, version, "NO-WINDOWS-WHEEL", "; ".join(wheels[:3]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python-version", default=declared_python())
    ap.add_argument("--extra", action="append", default=[])
    args = ap.parse_args()
    major, minor = args.python_version.split(".")[:2]
    cp = major + minor

    pins = resolve(args.python_version, args.extra)
    label = f"{args.python_version} / {PLATFORM}" + (
        f" +{','.join(args.extra)}" if args.extra else ""
    )
    print(f"{len(pins)} packages resolved for {label}")

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(lambda p: check(p, cp), pins))

    bad = [r for r in results if r[2] != "OK"]
    for name, version, status, detail in bad:
        print(f"  {status:18} {name}=={version}   {detail[:100]}")
    unverified = [r for r in bad if r[2] == "LOOKUP-FAILED"]
    unusable = [r for r in bad if r[2] != "LOOKUP-FAILED"]
    if unusable:
        print(
            f"\n{len(unusable)} package(s) cannot be installed on Windows at {args.python_version}."
        )
    if unverified:
        # Not the same finding: the index could not be asked, so nothing is known either way.
        print(f"\n{len(unverified)} package(s) could not be looked up; run this again.")
    if bad:
        return 1
    print("every resolved package has a usable Windows x64 wheel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
