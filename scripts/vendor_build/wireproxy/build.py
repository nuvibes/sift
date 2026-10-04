#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build Sift's tunnel client from its pinned source and install it as vendor/bin/wireproxy.exe.

    python scripts/vendor_build/wireproxy/build.py [--go PATH_TO_GO_EXE] [--work EMPTY_FOLDER]

The client is wireproxy, the userspace WireGuard program every tunnel runs as its own process.
Sift ships its own build of it rather than the upstream release, for two reasons:

  - The release pins a gVisor (the TCP/IP stack wireproxy carries) whose loss detection misreads
    Windows' coarse clock and treats every acknowledgement as a loss, which holds one upload through
    a tunnel to a few megabytes a second. `01-gomod.patch` moves gVisor to a revision that carries
    the upstream repair, with the two golang.org/x modules that revision needs, and moves
    golang.org/x/crypto and golang.org/x/net to releases that fix their published advisories.
  - `02-stack.patch` gives the stack CUBIC, a fine-grained clock on Windows and larger buffer
    limits, and repairs four faults in gVisor's TCP sender that cut the window under reordering.

Everything the build depends on is pinned in scripts/vendor_manifest.json under `built`: the source
repository, tag and commit, the digests of the two patches beside this file, the Go release, and
the digest of the program it makes. The same Go release and the same inputs make the same file byte
for byte, so the digest of the output is the proof that it is the reviewed build. Nothing is written
to vendor/bin unless it matches.

The steps:

  1. clone the tag with its line endings untouched, and check the commit;
  2. check both patches against their pinned digests, and apply the first (go.mod and go.sum);
  3. `go mod vendor` and `go mod verify`: every module copied into vendor/ at the version go.sum
     pins, checked by the go command against the Go checksum database;
  4. apply the second patch to that vendor tree;
  5. build from the vendor tree alone: no network, no version-control stamp, trimmed paths;
  6. refuse an output whose SHA-256 is not the pinned one, else install it.

Needs git and exactly the pinned Go release (`--go`, else the SIFT_GO variable, else `go` on
PATH). Steps 1 and 3 reach github.com and proxy.golang.org, or a module cache that already holds
the modules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MANIFEST = ROOT / "scripts" / "vendor_manifest.json"
VENDOR = ROOT / "vendor"

#: The entry under `built` in the manifest this script builds.
NAME = "wireproxy"

#: The variable that names the Go executable when `--go` does not.
GO_VARIABLE = "SIFT_GO"

#: Where a Go release is installed from.
GO_DOWNLOADS = "https://go.dev/dl/"

#: The Go settings a build may take from the machine: where the caches are and where modules are
#: fetched from. Every other GO* or CGO_* variable is dropped, because each can change the program
#: the compiler writes (GOAMD64, GOEXPERIMENT, GOFLAGS, GOFIPS140 among them), and a machine's own
#: `go env -w` file is switched off for the same reason.
KEPT_GO_SETTINGS = frozenset({"GOMODCACHE", "GOCACHE", "GOPATH", "GOPROXY", "GOSUMDB"})

#: Run one command in a folder with an environment, and answer what it printed. The tests stand a
#: function of their own in for it, so they run no git, no Go and no network.
Runner = Callable[[Sequence[str], Path, Mapping[str, str]], str]

#: How long one step may take. Generous: with an empty module cache, `go mod vendor` downloads
#: every module the program is built from.
STEP_TIMEOUT_S = 1800


class BuildRefused(Exception):
    """A step did not answer what the manifest pins. Nothing has been written to vendor/bin."""


@dataclass(frozen=True, slots=True)
class Pin:
    """What the manifest pins for the build, read once."""

    repository: str
    tag: str
    commit: str
    go: str
    version: str
    sha256: str
    file: str
    patches: tuple[tuple[Path, str], ...]


def read_pin(manifest: Path = MANIFEST) -> Pin:
    """The `built` entry named `NAME`, with its patches as paths in this checkout."""
    declared = json.loads(manifest.read_text(encoding="utf-8"))
    entries = [one for one in declared.get("built", []) if one.get("name") == NAME]
    if len(entries) != 1:
        raise BuildRefused(f"{manifest} has no single `built` entry named {NAME!r}.")
    (entry,) = entries
    if len(entry["patches"]) != 2:
        raise BuildRefused(
            f"the {NAME!r} entry names {len(entry['patches'])} patches; the recipe applies two."
        )
    source = entry["source"]
    return Pin(
        repository=str(source["repository"]),
        tag=str(source["tag"]),
        commit=str(source["commit"]),
        go=str(entry["go"]),
        version=str(entry["version"]),
        sha256=str(entry["sha256"]),
        file=str(entry["file"]),
        patches=tuple((ROOT / str(one["file"]), str(one["sha256"])) for one in entry["patches"]),
    )


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> str:
    """One step, for real. A step that fails stops the build with what it printed."""
    done = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=STEP_TIMEOUT_S,
    )
    if done.returncode != 0:
        said = (done.stdout + done.stderr).strip()
        raise BuildRefused(f"`{' '.join(argv)}` failed with exit {done.returncode}:\n{said}")
    return done.stdout


def go_environment(machine: Mapping[str, str]) -> dict[str, str]:
    """The machine's environment with only the Go settings in `KEPT_GO_SETTINGS` carried over."""
    kept = {
        key: value
        for key, value in machine.items()
        if not key.upper().startswith(("GO", "CGO_")) or key.upper() in KEPT_GO_SETTINGS
    }
    return {**kept, "GOENV": "off", "GOTOOLCHAIN": "local"}


def no_go(go_release: str) -> str:
    """The one sentence for a machine without the Go it needs."""
    return (
        f"Go {go_release.removeprefix('go')} is needed to build the tunnel client: install it "
        f"from {GO_DOWNLOADS} and name its go.exe with --go or the {GO_VARIABLE} variable."
    )


def find_go(named: str | None, machine: Mapping[str, str], pin: Pin) -> str:
    """The Go executable: `--go`, else SIFT_GO, else `go` on PATH."""
    chosen = named or machine.get(GO_VARIABLE) or shutil.which("go")
    if not chosen or not (Path(chosen).is_file() or shutil.which(chosen)):
        raise BuildRefused(no_go(pin.go))
    return chosen


def check_go(go: str, pin: Pin, runner: Runner, where: Path, env: Mapping[str, str]) -> None:
    """Refuse every Go release but the pinned one: the digest holds for that release alone."""
    said = runner([go, "version"], where, env).split()
    found = said[2] if len(said) > 2 and said[:2] == ["go", "version"] else " ".join(said)
    if found != pin.go:
        raise BuildRefused(
            f"{go} is {found or 'not a Go executable'}, and the pinned build is made with "
            f"{pin.go}: another release makes another file. {no_go(pin.go)}"
        )


def check_patches(pin: Pin) -> None:
    """Refuse a patch that is not the reviewed one, before anything is cloned."""
    for patch, pinned in pin.patches:
        if not patch.is_file():
            raise BuildRefused(f"{patch} is missing.")
        got = digest(patch)
        if got != pinned:
            raise BuildRefused(
                f"{patch.name} is not the pinned patch (sha256 {got}, pinned {pinned}). A changed "
                "patch is a changed program: review it, build it, and pin both digests in "
                "scripts/vendor_manifest.json in one commit."
            )


def build(pin: Pin, go: str, work: Path, runner: Runner, machine: Mapping[str, str]) -> Path:
    """Steps 1 to 6 in `work`, answering the program, which has the pinned digest."""
    env = go_environment(machine)
    check_go(go, pin, runner, work, env)
    check_patches(pin)
    (gomod, _), (stack, _) = pin.patches

    source = work / NAME
    git = ["git", "-c", "core.autocrlf=false"]
    runner(
        [
            *git,
            "clone",
            "--quiet",
            "--config",
            "core.autocrlf=false",
            "--branch",
            pin.tag,
            "--depth",
            "1",
            pin.repository,
            str(source),
        ],
        work,
        env,
    )
    head = runner([*git, "rev-parse", "HEAD"], source, env).strip()
    if head != pin.commit:
        raise BuildRefused(
            f"{pin.repository} tag {pin.tag} is commit {head}, and the pinned build is from "
            f"{pin.commit}. A tag that moves is a different program: read what changed before "
            "moving the pin."
        )
    runner([*git, "apply", str(gomod)], source, env)

    modules = {**env, "GOFLAGS": "-mod=mod"}
    runner([go, "mod", "vendor"], source, modules)
    runner([go, "mod", "verify"], source, modules)
    runner([*git, "apply", "-p1", str(stack)], source, env)

    program = work / Path(pin.file).name
    compile_env = {
        **env,
        "CGO_ENABLED": "0",
        "GOOS": "windows",
        "GOARCH": "amd64",
        "GOFLAGS": "-mod=vendor",
        "GOPROXY": "off",
    }
    runner(
        [
            go,
            "build",
            "-trimpath",
            "-buildvcs=false",
            "-ldflags",
            f"-s -w -X main.version={pin.version} -X main.arch=amd64",
            "-o",
            str(program),
            "./cmd/wireproxy",
        ],
        source,
        compile_env,
    )
    if not program.is_file():
        raise BuildRefused(f"the build wrote nothing to {program}.")
    got = digest(program)
    if got != pin.sha256:
        raise BuildRefused(
            f"the build made a file with sha256 {got}, and the pinned one is {pin.sha256}. "
            "Nothing was installed. With the pinned Go release and these inputs the file is the "
            "same everywhere, so a different one means a different input: find it before pinning."
        )
    return program


def install(program: Path, pin: Pin, vendor: Path = VENDOR) -> Path:
    """Copy the program into vendor/, checked again after the copy and moved into place whole."""
    dest = vendor / pin.file
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    shutil.copyfile(program, part)
    if digest(part) != pin.sha256:
        part.unlink()
        raise BuildRefused(f"the copy into {dest.parent} is not the file that was built.")
    part.replace(dest)
    return dest


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = run,
    machine: Mapping[str, str] = os.environ,
) -> int:
    parser = argparse.ArgumentParser(
        description="Build Sift's tunnel client from its pinned source."
    )
    parser.add_argument("--go", help=f"the Go executable (else {GO_VARIABLE}, else go on PATH)")
    parser.add_argument(
        "--work",
        type=Path,
        help="an empty folder to build in (else a temporary one, removed after)",
    )
    args = parser.parse_args(argv)
    try:
        pin = read_pin()
        go = find_go(args.go, machine, pin)
        if args.work is not None:
            if args.work.exists() and any(args.work.iterdir()):
                raise BuildRefused(f"{args.work} is not empty.")
            args.work.mkdir(parents=True, exist_ok=True)
            dest = install(build(pin, go, args.work, runner, machine), pin)
        else:
            with tempfile.TemporaryDirectory(
                prefix="sift-wireproxy-", ignore_cleanup_errors=True
            ) as scratch:
                dest = install(build(pin, go, Path(scratch), runner, machine), pin)
    except BuildRefused as refused:
        print(f"\nThe tunnel client was not built.\n\n  {refused}\n", file=sys.stderr)
        return 1
    print(f"  {dest}\n  {pin.version}  sha256 {pin.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
