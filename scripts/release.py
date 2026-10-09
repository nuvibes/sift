#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build one Sift installer, from a clean tree to a signed hash. The only way a release is made.

    python scripts/release.py                    # build it
    python scripts/release.py --skip-vendor      # reuse the verified vendor/bin already here
    python scripts/release.py --no-sign          # build without minisign, to install on this device
    python scripts/release.py --publish          # build, sign, and publish it on GitHub
    python scripts/release.py --publish --dry-run   # say what publishing would do; send nothing
    python scripts/release.py --tools-dir D:/tools  # where a release tool not on PATH is found

ONE SCRIPT, BECAUSE A RELEASE HAS TO CONTAIN THINGS THAT ARE EASY TO FORGET.

The native drag addon is bound to ONE Electron version. Moving Electron without recompiling it
produces an installer that builds, installs, opens, and crashes the first time somebody drags a clip
out, on their machine, not here. So the recompile is a step in this script, and an Electron upgrade
is a release event. The same holds for the rest: the client has to be built or the backend serves
nothing; the backend has to be installed NON-EDITABLE or the built client is read out of a checkout
not on the user's disk; the vendored tools are verified before they are packaged, not after.

## The steps, in the order they have to happen

    1  the client            npm run build in frontend/ -> src/sift/web
    2  the vendored tools    scripts/fetch_vendor.py, which verifies every SHA-256 before unpacking
    3  the runtime           a fresh venv on the pinned CPython with the backend installed into it
    3b the upgrade fixture   a new library made by that runtime, dumped for the next upgrade test
    4  the addon             electron-rebuild against the Electron version in desktop/package.json
    5  the shell             tsc
    6  the installer         electron-builder, per-user NSIS, one artefact
    7  the hash              SHA-256 of the installer, written beside it
    8  the manifest          the version, the installer's name and its SHA-256, in one small file
    9  the signature         minisign, over the hash file and the manifest, in one call

The packer is told `--publish never`: publishing is this script's own last step, taken only with
`--publish`, for a signed release CHANGELOG.md describes, on a commit the hosted suite passed.
`--no-sign` builds all but the manifest and the two signatures, for an install on this device.

A signed build and a publish refuse a working tree with changes in it and a HEAD that is not on
`origin/main` (see `check_the_tree`): what anybody else installs is exactly a commit the project
holds. A `--no-sign` build, for this device only, says so and goes on.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import gzip
import hashlib
import itertools
import json
import mmap
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_bytecode
import release_gates
import release_signing

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "vendor_manifest.json"
FRONTEND = ROOT / "frontend"
DESKTOP = ROOT / "desktop"
RUNTIME = ROOT / "build" / "runtime"
CLIENT = ROOT / "src" / "sift" / "web"
VENDOR = ROOT / "vendor" / "bin"
#: Where the installer is written. NOT `dist`, which is where tsc puts the compiled main process
#: and is what electron-builder PACKS. Left at the default, every build would pack the previous
#: build's output into the next one's app.asar and the installer would double with each release. See
#: the `directories` block in electron-builder.yml, and `_check_the_bundle_is_sane` below.
ARTIFACTS = DESKTOP / "release"


def _declared_python() -> str:
    """The interpreter version this repository declares, from the one file that declares it.

    READ, not written. `.python-version` is what uv picks up by itself in a checkout, in a worktree
    and in the Windows continuous-integration jobs, so a literal here would be a second declaration
    of one fact, and the first time either moved, the installer would bundle an interpreter nothing
    had been tested on while every check stayed green. There is no default: with no file there is no
    declaration, and a guess is how the two come apart quietly.
    """
    declared = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    if not declared:
        raise SystemExit(".python-version is empty, and it declares the interpreter to bundle")
    return declared


#: The interpreter the application ships and develops on.
PYTHON_VERSION = _declared_python()


def _declared_version() -> str:
    """Sift's version, from the one file that declares it.

    READ, not written, for the same reason the interpreter is. The backend reports the version out
    of its own installed metadata, which comes from this field, so a second copy anywhere else
    eventually ships an installer whose name disagrees with the application inside it.

    desktop/package.json therefore declares no version at all, and this is handed to the packer
    instead. A build made any other way has none to use and stops rather than guessing.
    """
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(declared["project"]["version"]).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"pyproject.toml declares version {version!r}, which is not a version")
    # The lockfile records the project's own version again, and a bump that misses it ships a
    # lockfile claiming the release before. CI installs with `--locked` and refuses that tree, so a
    # release cut from it is one no push after it can pass. Refused here, before any building.
    locked = re.search(
        r'\[\[package\]\]\nname = "sift"\nversion = "([^"]+)"',
        (ROOT / "uv.lock").read_text(encoding="utf-8"),
    )
    if locked is None or locked.group(1) != version:
        raise SystemExit(
            f"uv.lock records sift {locked.group(1) if locked else 'nothing'} and pyproject.toml "
            f"declares {version}. Run `uv lock` and commit the result with the bump."
        )
    return version


#: What this release is called, everywhere: the installer's name, the application's own version
#: information, and what the update check compares a published release against.
VERSION = _declared_version()

#: Where the signing key lives. Never in the repository: it is the one secret whose loss cannot be
#: undone: every future release would fail verification against the public key already shipped.
MINISIGN_KEY = Path(os.environ.get("RELEASE_SIGNING_KEY", Path.home() / ".minisign" / "sift.key"))


class ReleaseFailed(RuntimeError):
    """A step did not succeed, said in one sentence. Nothing later is attempted."""


def run(command: list[str], *, where: Path, quiet: bool = False) -> str:
    """Run one command and stop the release if it fails.

    Through a shell on Windows because npm and uv ship as `.cmd` files, and since the fix for
    CVE-2024-27980 Node refuses to spawn one without a shell, answering EINVAL rather than running
    it. Every argv here is written in this file; nothing comes from a person or a file.
    """
    shown = " ".join(command)
    print(f"\n$ {shown}")
    done = subprocess.run(
        shown if sys.platform == "win32" else command,
        cwd=where,
        shell=sys.platform == "win32",
        capture_output=quiet,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[-2000:]
        raise ReleaseFailed(f"{shown}\n  failed with {done.returncode}\n{detail}")
    return done.stdout or ""


#: The variable naming the folder searched for a release tool that is not on PATH; `--tools-dir`
#: sets it for the run. No default: a folder written here would be one machine's layout in
#: everybody's script, so whoever builds says where theirs is. A release tool belongs to the
#: project rather than to the machine, and putting it on PATH is a change to somebody's
#: environment made on their behalf. Not a `SIFT_` name: the application refuses to start beside a
#: `SIFT_` variable it does not know.
TOOLS_DIR_VARIABLE = "RELEASE_TOOLS_DIR"


def toolchain() -> Path | None:
    """The tools folder this run was given, or None where nobody named one."""
    named = os.getenv(TOOLS_DIR_VARIABLE)
    return Path(named) if named else None


def tool(name: str) -> str:
    """A tool's path, refusing early rather than failing four steps in.

    PATH first, then the project's own toolchain folder. That order means a tool somebody installed
    deliberately wins over the copy this project keeps, which is what somebody who installed one
    would expect.
    """
    found = shutil.which(name)
    if found is not None:
        return found
    folder = toolchain()
    if folder is not None and (folder / f"{name}.exe").is_file():
        return str(folder / f"{name}.exe")
    where = (
        f"not in {folder}"
        if folder is not None
        else f"no tools folder is named (--tools-dir or {TOOLS_DIR_VARIABLE})"
    )
    raise ReleaseFailed(f"{name} is not on PATH and {where}. It is needed to build a release.")


#: An optional design gallery may sit here; it is not part of this tree.
GALLERY = FRONTEND / "src" / "routes" / "design"
GALLERY_ASIDE = FRONTEND / ".design-aside"


def build_client(*, with_gallery: bool) -> None:
    """The client, into the Python package. A signed release is built without the gallery."""
    if GALLERY_ASIDE.is_dir() and not GALLERY.exists():
        GALLERY_ASIDE.rename(GALLERY)  # a build that stopped half-way left it aside
    aside = GALLERY.is_dir() and not with_gallery
    if aside:
        try:
            GALLERY.rename(GALLERY_ASIDE)
        except OSError as exc:
            held = f"{GALLERY} is open in another program ({exc.strerror})"
            raise ReleaseFailed(f"{held}: stop whatever holds it and build again.") from exc
    try:
        run(["npm", "run", "build"], where=FRONTEND)
    finally:
        if aside:
            GALLERY_ASIDE.rename(GALLERY)
    if not (CLIENT / "index.html").is_file():
        raise ReleaseFailed(
            f"the client build wrote nothing to {CLIENT}. The backend would serve an empty page."
        )


def fetch_vendor() -> None:
    run([sys.executable, str(ROOT / "scripts" / "fetch_vendor.py")], where=ROOT)


#: The lockfile as the list of packages the runtime installs: every package the project needs at
#: run time, each at its locked version with its hashes, the project itself and the `dev` extra
#: left out. `--frozen` reads the lock as it is and never resolves it again.
_EXPORT_THE_LOCK = ("export", "--frozen", "--no-emit-project", "--format", "requirements-txt")


def build_runtime() -> None:
    """A real interpreter copied whole with the backend installed into it, non-editable.

    A virtual environment names an interpreter in the build machine's profile, and an editable
    install names this checkout: neither exists on anybody else's computer.
    """
    uv = tool("uv")
    staging = ROOT / "build" / "runtime-staging"
    for directory in (RUNTIME, staging):
        if directory.exists():
            shutil.rmtree(directory)
    RUNTIME.parent.mkdir(parents=True, exist_ok=True)

    # The environment exists to make uv resolve, download and install; what is SHIPPED is built from
    # it below. Created first because its `pyvenv.cfg` is then the authority on which interpreter uv
    # actually chose: asking uv a second time, separately, is two answers that can disagree.
    run([uv, "venv", "--python", PYTHON_VERSION, str(staging)], where=ROOT)
    base = _interpreter_behind(staging)
    # The packages are the lockfile's, hash-checked, and Sift itself goes in without resolving
    # again. The lock is what CI tests and what the audit and the bill of materials read; a fresh
    # resolve here ships whatever the index holds on the day, which nothing has checked.
    python = str(staging / "Scripts" / "python.exe")
    locked = staging.parent / "runtime-requirements.txt"
    run([uv, *_EXPORT_THE_LOCK, "-o", str(locked)], where=ROOT)
    run(
        [uv, "pip", "install", "--python", python, "--require-hashes", "-r", str(locked)],
        where=ROOT,
    )
    run([uv, "pip", "install", "--python", python, "--no-deps", "."], where=ROOT)
    # A wheel built here replaces the published one (the HEIF reader without its encoder). Checked
    # against its pinned digest first: `--skip-vendor` does not run the fetch that would have.
    for wheel in _pinned_wheels():
        built = ROOT / "vendor" / str(wheel["file"])
        if not built.is_file() or _sha256(built) != wheel["sha256"]:
            raise ReleaseFailed(
                f"{built} is missing or is not the file scripts/vendor_manifest.json pins. Build it "
                f"with {wheel['recipe']}, or run scripts/fetch_vendor.py to see what differs."
            )
        reinstall = ["--no-deps", "--reinstall-package", str(wheel["package"]), str(built)]
        run([uv, "pip", "install", "--python", python, *reinstall], where=ROOT)

    # Headers and link libraries are for COMPILING against this interpreter, and nothing at runtime
    # can import them. They are the only things dropped: 4 MB is not worth a class of fault where
    # some import fails on somebody's machine and works on every one of ours.
    shutil.copytree(base, RUNTIME, ignore=shutil.ignore_patterns("include", "libs", "BUILD"))
    carry_the_cpp_runtime(RUNTIME)
    packages = RUNTIME / "Lib" / "site-packages"
    packages.mkdir(parents=True, exist_ok=True)
    for entry in (staging / "Lib" / "site-packages").iterdir():
        target = packages / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(entry, target)
    shutil.rmtree(staging)

    # Proved rather than assumed: the wheel has to carry the client, and an editable install would
    # pass every check above and fail this one.
    if not (packages / "sift" / "web" / "index.html").is_file():
        raise ReleaseFailed(
            "the installed backend has no client inside it. Either the client was not built before "
            "this ran, or the install was editable, in which case the packaged application would "
            "look for its files in this checkout."
        )
    # An editable install leaves a `.pth` naming a folder OUTSIDE the environment, and that folder
    # does not exist on anybody else's machine. Read rather than counted: `_virtualenv.pth` is
    # written by uv itself and points nowhere, so a check on the mere presence of a `.pth` refuses
    # every environment it is given.
    for pth in packages.glob("*.pth"):
        for line in pth.read_text(encoding="utf-8", errors="replace").splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("import "):
                continue
            where = Path(candidate)
            if where.is_absolute() and RUNTIME not in where.parents:
                raise ReleaseFailed(
                    f"{pth.name} points at {where}, outside the runtime, which means an editable "
                    "install. The packaged application would read its own code from there, and it "
                    "is not on the user's machine."
                )

    run(release_bytecode.compile_command(RUNTIME), where=ROOT, quiet=True)
    prove_runtime_is_self_contained()
    prove_the_runtime_ignores_other_pythons()
    prove_the_runtime_ships_no_tests()
    prove_the_runtime_ships_bytecode()
    prove_the_runtime_ships_the_site_icons()
    prove_the_runtime_carries_the_pinned_wheels(packages)
    prove_the_runtime_carries_the_cpp_runtime()


def _interpreter_behind(environment: Path) -> Path:
    """Which interpreter a virtual environment was built on, read from its own `pyvenv.cfg`."""
    config = environment / "pyvenv.cfg"
    for line in config.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "home":
            base = Path(value.strip())
            if not (base / "python.exe").is_file():
                raise ReleaseFailed(f"{config} names {base}, which has no python.exe in it.")
            return base
    raise ReleaseFailed(f"{config} does not say which interpreter it was built on.")


def prove_runtime_is_self_contained() -> None:
    """Run the bundled interpreter and make it say where its own standard library came from."""
    python = RUNTIME / "python.exe"
    if not python.is_file():
        raise ReleaseFailed(f"the runtime has no interpreter at {python}.")
    script = (
        "import json, os, sys, sift;"
        "print(json.dumps({'base': sys.base_prefix, 'prefix': sys.prefix,"
        " 'stdlib': os.__file__, 'sift': sift.__file__}))"
    )
    result = subprocess.run(
        [str(python), "-c", script], capture_output=True, text=True, cwd=str(ROOT)
    )
    if result.returncode != 0:
        raise ReleaseFailed(
            f"the bundled interpreter could not start or could not import sift:\n{result.stderr}"
        )
    answers = json.loads(result.stdout.strip().splitlines()[-1])
    for what, where in answers.items():
        if RUNTIME not in Path(where).resolve().parents and Path(where).resolve() != RUNTIME:
            raise ReleaseFailed(
                f"the bundled interpreter reads its {what} from {where}, which is OUTSIDE the "
                f"runtime at {RUNTIME}. That folder is on this machine and on no other, so the "
                "backend would not start once installed. The runtime must be a real interpreter, "
                "not a virtual environment pointing at one."
            )


def shell_interpreter_args() -> list[str]:
    """The flags the desktop shell starts the backend with, read from desktop/src/backend.ts."""
    source = DESKTOP / "src" / "backend.ts"
    text = source.read_text(encoding="utf-8")
    found = re.search(r"export const INTERPRETER_ARGS = \[([^\]]*)\]", text)
    if found is None:
        raise ReleaseFailed(
            f"{source} no longer declares INTERPRETER_ARGS, so there is nothing saying how the "
            "shell starts the backend. Either it was renamed (in which case this function must "
            "follow it) or the isolation flags were dropped, which is the fault this checks for."
        )
    args = re.findall(r"'([^']+)'", found.group(1))
    if not args:
        raise ReleaseFailed(f"INTERPRETER_ARGS in {source} is empty.")
    return args


def prove_the_runtime_ignores_other_pythons() -> None:
    """Start the interpreter with the shell's flags beside a staged per-user package folder and
    PYTHONPATH, and require the import to come out of the runtime anyway.

    A normal interpreter reads both before its own packages, so a stray package on somebody's
    machine would win over the one Sift ships.
    """
    python = RUNTIME / "python.exe"
    staged = ROOT / "build" / "foreign-python"
    if staged.exists():
        shutil.rmtree(staged)
    # The name and the layout are both the real ones: `<userbase>\PythonXY\site-packages` is where
    # a per-user install puts things, and typing_extensions is the module that actually broke.
    version = "".join(PYTHON_VERSION.split(".")[:2])
    packages = staged / f"Python{version}" / "site-packages"
    packages.mkdir(parents=True)
    (packages / "typing_extensions.py").write_text(
        "# Stands in for an older copy in somebody's own profile. Deliberately empty: every real\n"
        "# version has the names Sift's dependencies ask for, and this one has none of them.\n",
        encoding="utf-8",
    )

    hostile = {
        # Windows cannot start a process without this one; it is where the system DLLs are. Read
        # by its canonical upper-case name because that is how Python stores it on Windows, and
        # handed on in the spelling everything else uses: the site ignores the difference.
        "SystemRoot": os.getenv("SYSTEMROOT", r"C:\Windows"),
        "PATH": os.getenv("PATH", ""),
        "PYTHONUSERBASE": str(staged),
        "PYTHONPATH": str(packages),
    }
    script = (
        "import json, sift, typing_extensions;"
        "print(json.dumps({'sift': sift.__file__, 'typing_extensions': typing_extensions.__file__}))"
    )
    result = subprocess.run(
        [str(python), *shell_interpreter_args(), "-c", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=hostile,
    )
    shutil.rmtree(staged)
    if result.returncode != 0:
        raise ReleaseFailed(
            "the bundled interpreter read another Python's packages instead of its own, and could "
            f"not start:\n{result.stderr}\n"
            "This is what happens on any computer carrying packages installed for a user account. "
            "fix is the isolation flags in desktop/src/backend.ts (see INTERPRETER_ARGS there)."
        )
    answers = json.loads(result.stdout.strip().splitlines()[-1])
    for what, where in answers.items():
        if RUNTIME not in Path(where).resolve().parents:
            raise ReleaseFailed(
                f"with another Python's packages on the machine, the interpreter loaded {what} from "
                f"{where}, outside the runtime at {RUNTIME}. Sift must read its own code."
            )


def prove_the_runtime_ships_no_tests() -> None:
    """Refuse a packed runtime carrying the tests kept beside the code (the wheel's `exclude`)."""
    packages = RUNTIME / "Lib" / "site-packages" / "sift"
    shipped = [
        one.relative_to(packages).as_posix()
        for one in packages.rglob("*")
        if one.is_file()
        and ("tests" in one.relative_to(packages).parts or one.name == "conftest.py")
    ]
    if shipped:
        raise ReleaseFailed(
            f"the packed runtime carries {len(shipped)} test files, starting with "
            f"{', '.join(sorted(shipped)[:3])}. They cannot run where they are going and they are a "
            "third of what is installed. See the wheel's `exclude` in pyproject.toml."
        )


def prove_the_runtime_ships_bytecode(runtime: Path = RUNTIME) -> None:
    """Refuse a runtime where any module lacks its checked-hash `.pyc`."""
    if found := release_bytecode.uncompiled(runtime):
        raise ReleaseFailed(found)


def prove_the_runtime_ships_the_site_icons() -> None:
    """Refuse a packed runtime without the site logos: nothing in the packaging names them."""
    pack = RUNTIME / "Lib" / "site-packages" / "sift" / "kernel" / "site_icons"
    icons = sorted(pack.glob("icons/*.png"))
    if not (pack / "manifest.json").is_file() or not icons:
        raise ReleaseFailed(
            f"the packed runtime carries {len(icons)} site icons and "
            f"{'a' if (pack / 'manifest.json').is_file() else 'no'} manifest, at {pack}. Every "
            "Site without a chosen picture would be drawn as a letter. The pack is committed "
            "under src/sift/kernel/site_icons; check it was not excluded from the wheel."
        )


def _pinned_wheels() -> list[dict[str, object]]:
    """The wheels scripts/vendor_manifest.json says the runtime gets in place of the published ones."""
    declared = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wheels = declared.get("wheels", []) if isinstance(declared, dict) else []
    return [one for one in wheels if isinstance(one, dict)] if isinstance(wheels, list) else []


def _built_programs() -> list[dict[str, object]]:
    """The programs scripts/vendor_manifest.json says this repository builds, each pinned by the
    digest of the file its recipe makes."""
    declared = json.loads(MANIFEST.read_text(encoding="utf-8"))
    built = declared.get("built", []) if isinstance(declared, dict) else []
    return [one for one in built if isinstance(one, dict)] if isinstance(built, list) else []


def prove_the_built_programs_are_pinned(
    folder: Path, what: str, programs: list[dict[str, object]] | None = None
) -> None:
    """Refuse a vendor folder whose copy of a program built here is not the file its recipe makes.

    **THE FAULT THIS CATCHES INSTALLS AND WORKS ON THIS MACHINE.** The upstream tunnel client in
    place of the one built here starts, connects and carries every download; only its uploads are
    slower. But every installed copy compares the program with the digest it was built with
    (`kernel/tunnels/client.py`), so each install would call its own tunnel program altered and
    refuse to start a tunnel. A program that is missing is named by the check that calls this.
    """
    for program in _built_programs() if programs is None else programs:
        path = folder / Path(str(program.get("file", ""))).name
        if not path.is_file():
            continue
        got = _sha256(path)
        if got != program.get("sha256"):
            raise ReleaseFailed(
                f"{what} has a {path.name} that is not the one {program.get('recipe')} builds "
                f"(sha256 {got}, pinned {program.get('sha256')}). Do NOT ship it: every installed "
                f"copy would refuse to run it. Build it with python {program.get('recipe')}, then "
                "run scripts/fetch_vendor.py."
            )


def _listed(entry: dict[str, object], key: str) -> list[str]:
    listed = entry.get(key)
    return [str(one).lower() for one in listed] if isinstance(listed, list) else []


def wheel_dlls_refused(dlls: list[str], wheel: dict[str, object]) -> list[str]:
    """What an installed wheel carries that its manifest entry refuses, and what it lacks.

    By the start of each DLL's name: the packer that puts a library into a wheel adds a digest to
    it (`libx265-217-8a7f....dll`), and that part changes with every build.
    """
    wrong = [
        dll for dll in dlls if any(dll.lower().startswith(no) for no in _listed(wheel, "refuses"))
    ]
    for one in _listed(wheel, "carries"):
        if not any(
            dll.lower().startswith(f"{one}-") or dll.lower() == f"{one}.dll" for dll in dlls
        ):
            wrong.append(f"no {one} library")
    return wrong


def prove_the_runtime_carries_the_pinned_wheels(
    packages: Path, wheels: list[dict[str, object]] | None = None
) -> None:
    """Refuse a runtime whose package is the published wheel rather than the one built here.

    **THE FAULT THIS CATCHES INSTALLS AND WORKS.** The published pillow-heif reads a photograph
    exactly as the rebuilt one does; the only difference is a 22.6 MB encoder under the GPL that
    nothing calls and whose source the release does not carry. So nothing on anybody's machine would
    ever say which one shipped. Read from what the installed package recorded (its RECORD), which
    names every file the install wrote, rather than from a folder listing that can hold files a
    previous install left.
    """
    for wheel in _pinned_wheels() if wheels is None else wheels:
        name = str(wheel.get("package", "")).replace("-", "_")
        records = sorted(packages.glob(f"{name}-*.dist-info/RECORD"))
        if not records:
            raise ReleaseFailed(f"the packed runtime has no {wheel.get('package')} installed.")
        written = [line.split(",", 1)[0] for line in records[0].read_text("utf-8").splitlines()]
        dlls = sorted(Path(one).name for one in written if one.lower().endswith(".dll"))
        wrong = wheel_dlls_refused(dlls, wheel)
        if wrong:
            raise ReleaseFailed(
                f"the packed runtime's {wheel.get('package')} is not the wheel "
                f"{wheel.get('recipe')} builds: {', '.join(wrong)} (it carries "
                f"{', '.join(dlls) or 'no DLLs'}). The published wheel links an encoder under the "
                "GPL whose source this release does not ship."
            )


#: The Visual C++ runtime carried beside the interpreter, where Windows looks before System32.
CPP_RUNTIME = (
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)
#: Carried too where the source has them, at its version: a library may import any of them.
CPP_COMPANIONS = (
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140_threads.dll",
    "vccorlib140.dll",
)


def file_version(path: Path) -> str | None:
    """A Windows file's version from its fixed version block, or None where it has none."""
    data = path.read_bytes()
    at = data.find(b"\xbd\x04\xef\xfe")
    if at < 0 or len(data) < at + 16:
        return None
    high, low = struct.unpack_from("<II", data, at + 8)
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def _visual_studio_redist() -> Path:
    """The newest installed C++ toolchain's redistributable folder, as its own tools name it."""
    named = os.getenv("VCTOOLSREDISTDIR")
    if named:
        return Path(named)
    installer = Path(os.getenv("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
    vswhere = installer / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise ReleaseFailed(f"there is no {vswhere}, so no C++ runtime to carry.")
    asked = [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"]
    home = Path(subprocess.run(asked, capture_output=True, text=True, check=True).stdout.strip())
    default = home / "VC" / "Auxiliary" / "Build" / "Microsoft.VCRedistVersion.default.txt"
    if not default.is_file():
        raise ReleaseFailed(f"{home} has no C++ build tools, so no C++ runtime to carry.")
    return home / "VC" / "Redist" / "MSVC" / default.read_text(encoding="utf-8").strip()


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def whole_cpp_runtime(folder: Path) -> str | None:
    """The one version all of CPP_RUNTIME, and every companion present, carries in `folder`."""
    versions = {
        file_version(folder / name) if (folder / name).is_file() else None for name in CPP_RUNTIME
    }
    present = {file_version(folder / name) for name in CPP_COMPANIONS if (folder / name).is_file()}
    whole = len(versions) == 1 and None not in versions and present <= versions
    return versions.pop() if whole else None


def cpp_runtime_source(redist: Path | None = None) -> tuple[Path, str]:
    """The newer whole x64 C++ runtime of the toolchain's redistributable and Windows' installed one."""
    candidates = [Path(os.getenv("SYSTEMROOT", "C:\\Windows")) / "System32"]
    try:
        folder = _visual_studio_redist() if redist is None else redist
        candidates += sorted(folder.glob("x64/Microsoft.VC*.CRT"))[-1:]
    except (ReleaseFailed, OSError, subprocess.CalledProcessError):
        pass
    whole = [(whole_cpp_runtime(one), at, one) for at, one in enumerate(candidates)]
    found = [(_parts(version), at, one, version) for version, at, one in whole if version]
    if not found:
        raise ReleaseFailed(
            "no whole x64 C++ runtime is on this machine. Install the C++ build tools."
        )
    _, _, folder, version = max(found)
    print(f"  C++ runtime from {folder}  {version}")
    return folder, version


def carry_the_cpp_runtime(into: Path) -> None:
    """The newest C++ runtime this machine has, beside the interpreter, over what it came with."""
    source, _ = cpp_runtime_source()
    names = [name for name in CPP_RUNTIME + CPP_COMPANIONS if (source / name).is_file()]
    for name in names:
        shutil.copyfile(source / name, into / name)
    print(f"  C++ runtime  {len(names)} files carried")


def linker_version(path: Path) -> tuple[int, int] | None:
    """The toolset a Windows binary was linked with, from its PE optional header; None if not one."""
    with path.open("rb") as handle:
        head = handle.read(0x40)
        if len(head) < 0x40 or head[:2] != b"MZ":
            return None
        handle.seek(struct.unpack_from("<I", head, 0x3C)[0])
        pe = handle.read(28)
    return (pe[26], pe[27]) if len(pe) == 28 and pe[:4] == b"PE\0\0" else None


def newest_linked(folder: Path) -> tuple[tuple[int, int], Path] | None:
    """The newest toolset any bundled binary was linked with; an older linker cannot raise it."""
    linked = [
        (linker_version(one), one)
        for one in folder.rglob("*")
        if one.suffix.lower() in (".pyd", ".dll")
    ]
    found = [(version, one) for version, one in linked if version is not None]
    return max(found) if found else None


def prove_the_runtime_carries_the_cpp_runtime(folder: Path | None = None) -> str:
    """Refuse a C++ runtime that is missing, mixed, or older than a bundled binary's linker.

    New code on an older C++ runtime can die as it loads, so it must be at least the newest toolset.
    """
    where = RUNTIME if folder is None else folder
    missing = [name for name in CPP_RUNTIME if not (where / name).is_file()]
    if missing:
        raise ReleaseFailed(f"the runtime has no {', '.join(missing)} beside its interpreter.")
    carried_names = CPP_RUNTIME + tuple(one for one in CPP_COMPANIONS if (where / one).is_file())
    versions = {name: file_version(where / name) for name in carried_names}
    if None in versions.values() or len(set(versions.values())) != 1:
        found = ", ".join(f"{name} {version}" for name, version in versions.items())
        raise ReleaseFailed(f"the runtime's C++ runtime is not one release: {found}.")
    carried = str(versions["msvcp140.dll"])
    newest = newest_linked(where)
    if newest is not None and _parts(carried)[:2] < newest[0]:
        (major, minor), library = newest
        raise ReleaseFailed(
            f"the runtime carries C++ runtime {carried}, older than {library.relative_to(where)} "
            f"was linked with ({major}.{minor}). It must be at least as new as every component."
        )
    print(f"  C++ runtime  {carried}")
    return carried


def rebuild_addon() -> None:
    """The drag addon, against the Electron version this release ships.

    It is ABI-bound. An addon compiled against a different Electron loads and then crashes the
    renderer the first time it is called, which is a fault a person meets while dragging a file and
    nobody meets while building one.
    """
    run(["npm", "run", "rebuild:native"], where=DESKTOP)
    built = DESKTOP / "native" / "drag" / "build" / "Release" / "drag.node"
    if not built.is_file():
        raise ReleaseFailed(f"the addon was not produced at {built}.")


def build_shell() -> None:
    run(["npm", "run", "build"], where=DESKTOP)


def pack() -> Path:
    """The installer, stamped with the version this repository declares.

    The version is passed in rather than read from desktop/package.json, which no longer carries
    one. electron-builder merges this over the package metadata before it validates it, so it names
    the installer, the executable's version information and the entry in Add/Remove Programs.
    """
    run(["npm", "run", "pack", "--", f"-c.extraMetadata.version={VERSION}"], where=DESKTOP)
    _check_the_bundle_is_sane()
    _check_the_vendored_tools_are_in_it()
    _check_the_fuses_are_flipped()
    _check_the_shell_boots()
    # NAMED, never picked out of a sort. The folder keeps more than one installer, and a sort of
    # names puts "Sift-1.0.9" after "Sift-1.0.10", so the last name is the wrong file to hash, sign
    # and hand over. A valid signature over the wrong file is worse than no signature, because it
    # is the thing somebody checks and is reassured by.
    installer = ARTIFACTS / f"Sift-{VERSION}-x64-setup.exe"
    if not installer.is_file():
        raise ReleaseFailed(f"the packer produced no {installer.name} in {ARTIFACTS}.")
    return installer


def write_hash(installer: Path) -> Path:
    """SHA-256 beside the installer, in the shape `sha256sum -c` reads."""
    digest = _sha256(installer)
    sums = installer.with_suffix(".exe.sha256")
    sums.write_text(f"{digest}  {installer.name}\n", encoding="utf-8", newline="\n")
    print(f"\n  SHA-256  {digest}")
    return sums


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(installer: Path, sums: Path) -> Path:
    """The release, described in one small file the signature covers: version, installer, SHA-256.

    What the desktop application checks before it installs anything. The version is in here, under
    the signature, because a signature over the bytes alone proves they are Sift's and says nothing
    about WHICH release they are: an old installer carries a perfectly good one.
    """
    digest = sums.read_text(encoding="utf-8").split()[0]
    manifest = installer.with_name(f"{installer.name}.manifest.json")
    body = {"version": VERSION, "installer": installer.name, "sha256": digest}
    manifest.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def sign(sums: Path, manifest: Path) -> list[Path]:
    """Minisign over the hash file and the manifest, three tries at the password."""
    minisign = tool("minisign")
    return release_signing.sign(
        sums, manifest, minisign=minisign, key=MINISIGN_KEY, run=run, failed=ReleaseFailed
    )


def sign_what_is_built(folder: Path = ARTIFACTS, *, signing: bool = True) -> None:
    """`--no-build`: sign the release built for this version where an earlier run stopped short of
    its signatures, so a wrong password never costs a second build. A dry run signs nothing."""
    installer = folder / f"Sift-{VERSION}-x64-setup.exe"
    sums = installer.with_name(f"{installer.name}.sha256")
    if signing and sums.is_file() and not sums.with_name(f"{sums.name}.minisig").is_file():
        manifest = write_manifest(installer, sums)
        put_on_the_desktop([installer, sums, manifest, *sign(sums, manifest)])


#: What this script is allowed to replace on the desktop: its own artefacts and nothing else.
#: Anchored and version-shaped: a looser pattern on a folder full of somebody's own files is a
#: delete waiting to hit the wrong one. NARROWER than `ARTEFACT` below, and not derived from it:
#: the desktop gets the files a person is handed (installer, hash, manifest and their signatures)
#: and never a blockmap, so what may be removed from a desktop must not learn to match one.
OURS = re.compile(
    r"^Sift-\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?-x64-setup\.exe"
    r"(?:\.sha256(?:\.minisig)?|\.manifest\.json(?:\.minisig)?)?$"
)

#: One release's files in the artefacts folder: the installer, the hash and the manifest beside it,
#: the signatures over those two, and the packer's own blockmap. The version is CAPTURED because that is what the
#: pruning orders on, never the name. Anything this does not match was not written by this script
#: and is left alone.
ARTEFACT = re.compile(
    r"^Sift-(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)-x64-setup\.exe"
    r"(?:\.blockmap|(?:\.sha256|\.manifest\.json)(?:\.minisig)?)?$"
)

#: The packer's own intermediate: the compressed payload NSIS wraps into the installer. NOT an
#: artefact: it is named for the package rather than the product (`sift-desktop-1.2.3-x64.nsis.7z`),
#: it is nobody's download, and electron-builder rebuilds it from `win-unpacked` on the next pack.
#: The packer usually cleans it up, so one is left only when a pack went wrong, and then it stays
#: through every later release: the folder's prune keeps the set a release ships, and this is not
#: one of those. The prune removes it by rule.
INTERMEDIATE = re.compile(
    r"^sift-desktop-(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)-x64\.nsis\.7z$"
)

#: How many releases the artefacts folder keeps. Three so that the release before last is still
#: there to roll back to with one to spare; older ones are rebuilt from the tree rather than kept.
KEEP_RELEASES = 3


def _version_key(version: str) -> tuple[int, int, int, int, str]:
    """Order two versions the way they are meant, not the way their names sort.

    `1.0.9` sorts AFTER `1.0.10` as text. Picked by name, the wrong installer would be hashed and
    signed (`pack()` names its own installer for that reason), and a prune by name would take the
    newest release out of the folder while leaving the old ones.

    The three numbers compare as numbers. A version carrying a pre-release suffix sorts below the
    bare one it qualifies, which is semver's rule: 1.0.10-rc1 is older than 1.0.10.
    """
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?", version)
    if match is None:  # Unreachable from ARTEFACT, which has already matched the same shape.
        raise ReleaseFailed(f"{version!r} is not a version this script knows how to order")
    major, minor, patch, extra = match.groups()
    return (int(major), int(minor), int(patch), 0 if extra else 1, extra or "")


def prune_old_releases(
    folder: Path = ARTIFACTS, *, keep: int = KEEP_RELEASES, never: str = VERSION
) -> int:
    """Keep the newest few releases in the artefacts folder, delete the rest. Returns bytes freed.

    Whole releases go (installer, hash and signatures together), nothing a person put there, and
    never the version this run built. Every packer intermediate but this run's goes, whatever its
    version: it is a working file, not something anybody rolls back to.
    """
    if not folder.is_dir():
        return 0

    releases: dict[str, list[Path]] = {}
    leftovers: list[Path] = []
    for one in folder.iterdir():
        if not one.is_file():
            continue
        found = ARTEFACT.match(one.name)
        if found is not None:
            releases.setdefault(found["version"], []).append(one)
            continue
        packed = INTERMEDIATE.match(one.name)
        if packed is not None and packed["version"] != never:
            leftovers.append(one)

    ranked = sorted(releases, key=_version_key)
    # `max(0, ...)` is load-bearing and is not defensive tidiness. With fewer releases in the folder
    # than the limit, `len(ranked) - keep` is NEGATIVE, and a negative end is Python's "all but the
    # last n", so a folder holding two releases and a limit of three would lose the older of the
    # two. It reads as an off-by-one and is a delete.
    over = max(0, len(ranked) - keep)
    doomed = [version for version in ranked[:over] if version != never]

    freed = 0
    for version in doomed:
        for one in sorted(releases[version]):
            freed += one.stat().st_size
            one.unlink()
    for one in sorted(leftovers):
        freed += one.stat().st_size
        one.unlink()
    if not doomed and not leftovers:
        return 0
    print(
        f"\n  pruned   {len(doomed)} older release(s) and {len(leftovers)} packer leftover(s) "
        f"from {folder}  ({freed / 1e9:.1f} GB)"
    )
    return freed


#: WHERE THE UPGRADE TEST FINDS THE LAST RELEASE'S LIBRARY: one gzipped dump of a new library made by
#: the released code (`write_upgrade_fixture`), read by
#: `tests/integration/test_a_library_from_the_last_release_comes_forward.py`, newest by version.
FIXTURES = ROOT / "tests" / "integration" / "data"
FIXTURE = re.compile(r"^library-(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\.sql\.gz$")

#: Run by the released interpreter: a new library, every component at its released version, made
#: through the same door a first boot makes one (`initialize_schema`), dumped whole to the path
#: it is handed. To a file and not to standard output, which the database's own log lines share.
#: Run from a file of its own (`write_upgrade_fixture`).
_MAKE_A_LIBRARY = """
import asyncio, sqlite3, sys, tempfile
from pathlib import Path

import sift.main  # every component registers its schema at import
from sift.kernel.db import Database


async def make(path):
    database = Database(path)
    await database.connect()
    try:
        await database.initialize_schema()
    finally:
        await database.close()


with tempfile.TemporaryDirectory() as folder:
    library = Path(folder) / "library.sqlite3"
    asyncio.run(make(library))
    connection = sqlite3.connect(library)
    try:
        dumped = "\\n".join(connection.iterdump()) + "\\n"
    finally:
        connection.close()
Path(sys.argv[1]).write_text(dumped, encoding="utf-8", newline="\\n")
"""


def without_sql_comments(sql: str) -> str:
    """SQL with its comments taken out and nothing else changed.

    A table or a trigger keeps the text it was created with, comments and all, and the tree holds
    no double hyphen, so a dump goes in without them. A comment's own line is kept as a line (its
    words and the spaces before them go), and nothing inside a quoted string or a quoted name is
    touched: a setting's value may hold either mark, and it is data.
    """
    out: list[str] = []
    at, end = 0, len(sql)
    quote: str | None = None
    while at < end:
        one = sql[at]
        if quote is not None:
            out.append(one)
            if one == quote:
                quote = None
            at += 1
            continue
        if one in "'\"`[":
            quote = "]" if one == "[" else one
            out.append(one)
            at += 1
            continue
        if sql.startswith("--", at):
            stop = sql.find("\n", at)
            at = end if stop == -1 else stop
            while out and out[-1] in " \t":
                out.pop()
            continue
        if sql.startswith("/*", at):
            stop = sql.find("*/", at + 2)
            at = end if stop == -1 else stop + 2
            continue
        out.append(one)
        at += 1
    return "".join(out)


def keep_one_fixture(folder: Path, version: str) -> list[Path]:
    """Delete every upgrade fixture in `folder` but this version's. Returns what went.

    The test reads the newest, and an older one is a library no update starts from any more.
    """
    gone: list[Path] = []
    for one in sorted(folder.iterdir()) if folder.is_dir() else []:
        found = FIXTURE.match(one.name)
        if one.is_file() and found is not None and found["version"] != version:
            one.unlink()
            gone.append(one)
    return gone


def write_upgrade_fixture(
    python: Path | None = None, folder: Path = FIXTURES, version: str = VERSION
) -> Path:
    """The library this release makes, dumped for the next release's upgrade test.

    Made by the RELEASED interpreter and code (the runtime just built and proved), so the dump is
    every table, index and trigger exactly as an installed copy creates them. Its SQL comments go
    (`without_sql_comments`), and it is gzipped: the dump is megabytes of trigger text that packs
    to a fraction of one, under the size a commit may add. The fixture before it is deleted, so
    the folder holds one. Written into the tree, which is clean before a release and holds this
    one file new after it: it goes in the next commit.
    """
    interpreter = python or RUNTIME / "python.exe"
    with tempfile.TemporaryDirectory() as scratch:
        # A file, not `-c`: a launcher between here and the interpreter passes a command line
        # on, and a line break inside one argument does not survive it.
        maker = Path(scratch) / "make_a_library.py"
        maker.write_text(_MAKE_A_LIBRARY, encoding="utf-8")
        dumped = Path(scratch) / "library.sql"
        run([str(interpreter), str(maker), str(dumped)], where=ROOT, quiet=True)
        sql = without_sql_comments(dumped.read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    fixture = folder / f"library-{version}.sql.gz"
    # `mtime=0`, so the same library makes the same bytes: a release built twice changes nothing.
    fixture.write_bytes(gzip.compress(sql.encode("utf-8"), mtime=0))
    keep_one_fixture(folder, version)
    print(f"\n  wrote    {fixture.relative_to(ROOT) if ROOT in fixture.parents else fixture}")
    return fixture


def _desktop() -> Path | None:
    """Where this account's desktop is, read from the registry as Explorer reads it (it may be
    redirected); None where there is none, an ordinary state on a build machine."""
    import winreg

    shell_folders = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, shell_folders) as key:
            recorded, _ = winreg.QueryValueEx(key, "Desktop")
    except OSError:
        return None
    place = Path(os.path.expandvars(str(recorded)))
    return place if place.is_dir() else None


def put_on_the_desktop(artefacts: list[Path]) -> None:
    """Copy the finished release to this account's desktop, replacing the copies this script wrote
    before: one copy in a known place cannot be an old build that looks current."""
    where = _desktop()
    if where is None:
        print("\n  No desktop to copy to; the artefacts are in the release folder only.")
        return

    for old in sorted(where.glob("Sift-*-x64-setup.exe*")):
        if OURS.match(old.name) and old.name not in {one.name for one in artefacts}:
            old.unlink()
            print(f"  removed  {old.name}")
    for one in artefacts:
        shutil.copy2(one, where / one.name)
        print(f"  copied   {one.name}  ->  {where}")


#: What each release carries, newest first. A version's section is its release notes, on the release
#: page and on the Updates screen alike.
CHANGELOG = ROOT / "CHANGELOG.md"

#: The section that collects changes before a version carries them.
UNRELEASED = "Unreleased"

_SECTION = re.compile(r"^##(?!#)\s*(?P<title>.*?)\s*$")
_VERSION_TITLE = re.compile(
    r"^(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)(?:\s+-\s+(?P<date>\d{4}-\d{2}-\d{2}))?$"
)
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_LINK_DEFINITION = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*\S")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CODE_SPAN = re.compile(r"(`+).+?\1")

#: What the Updates screen does not draw: a note written with one of these reads differently there
#: than on the release page. Looked for outside code, where the characters are meant literally.
_UNDRAWN = (
    (re.compile(r"<[A-Za-z/!?][^>\n]*>"), "an HTML tag, which the Updates screen shows as text"),
    (re.compile(r"^\s{0,3}\|"), "a table, which the Updates screen shows as rows of pipes"),
    (re.compile(r"^\s{0,3}>"), "a quote, which the Updates screen shows with its marker"),
)


class ChangelogSection(NamedTuple):
    """One `## ` section: Unreleased or a version, its date if it has one, and its text."""

    title: str
    date: str | None
    body: str


def read_changelog(text: str) -> list[ChangelogSection]:
    """Every section of the changelog in order, or ReleaseFailed naming what is out of shape.

    A heading is `## Unreleased`, `## <version>` or `## <version> - <YYYY-MM-DD>`. Unreleased comes
    first and the versions run newest first, each once, so the section a release reads is the only
    one with its name.
    """
    sections: list[ChangelogSection] = []
    title: tuple[str, str | None] | None = None
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        opened = _FENCE.match(line)
        if fence is not None:
            if opened is not None and opened.group(1) == fence:
                fence = None
        elif opened is not None:
            fence = opened.group(1)
        elif (heading := _SECTION.match(line)) is not None:
            if title is not None:
                sections.append(ChangelogSection(*title, "\n".join(lines)))
            title, lines = _section_title(heading["title"]), []
            continue
        lines.append(line)
    if title is not None:
        sections.append(ChangelogSection(*title, "\n".join(lines)))

    titles = [one.title for one in sections]
    if UNRELEASED in titles[1:]:
        raise ReleaseFailed(f"CHANGELOG.md has `## {UNRELEASED}` below a version; it goes first.")
    versions = [one for one in titles if one != UNRELEASED]
    for newer, older in itertools.pairwise(versions):
        if _version_key(newer) <= _version_key(older):
            raise ReleaseFailed(
                f"CHANGELOG.md lists {older} below {newer}. Versions run newest first, each once."
            )
    return sections


def _section_title(title: str) -> tuple[str, str | None]:
    if title == UNRELEASED:
        return UNRELEASED, None
    found = _VERSION_TITLE.match(title)
    if found is None:
        raise ReleaseFailed(
            f"CHANGELOG.md has a section headed {title!r}. A section is `## {UNRELEASED}`, "
            "`## <version>` or `## <version> - <YYYY-MM-DD>`."
        )
    if found["date"] is not None:
        try:
            datetime.date.fromisoformat(found["date"])
        except ValueError:
            raise ReleaseFailed(
                f"CHANGELOG.md dates {found['version']} {found['date']}, which is not a date."
            ) from None
    return found["version"], found["date"]


def release_notes(body: str) -> str:
    """A section's text as it is published.

    What a rendered page never shows (link definitions, HTML comments) is left out, and anything the
    Updates screen would draw differently from the release page is refused, so the two read alike.
    """
    kept: list[str] = []
    problems: list[str] = []
    fence: str | None = None
    for line in _HTML_COMMENT.sub("", body).splitlines():
        opened = _FENCE.match(line)
        if fence is not None:
            if opened is not None and opened.group(1) == fence:
                fence = None
        elif opened is not None:
            fence = opened.group(1)
        elif _LINK_DEFINITION.match(line):
            continue
        else:
            prose = _CODE_SPAN.sub("", line)
            problems += [f"{why}: {line.strip()}" for rule, why in _UNDRAWN if rule.search(prose)]
        kept.append(line.rstrip())
    if problems:
        raise ReleaseFailed("CHANGELOG.md uses " + "\n  ".join(problems))
    notes = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    limit = notes_limit()
    if len(notes) > limit:
        raise ReleaseFailed(
            f"the notes are {len(notes)} characters and the Updates screen keeps {limit}. "
            "Say less, or link to the rest from the release page."
        )
    return notes


def notes_limit() -> int:
    """The most release-notes text the backend keeps, read from the backend itself."""
    source = ROOT / "src" / "sift" / "slices" / "update_notify" / "service.py"
    found = re.search(r"^MAX_NOTES_CHARS = ([\d_]+)$", source.read_text(encoding="utf-8"), re.M)
    if found is None:
        raise ReleaseFailed(f"{source} no longer declares MAX_NOTES_CHARS.")
    return int(found.group(1))


def notes_for(version: str, sections: list[ChangelogSection], *, dated: bool) -> str:
    """The published notes of `version`, refusing a section that is missing, undated or empty."""
    section = next((one for one in sections if one.title == version), None)
    if section is None:
        raise ReleaseFailed(
            f"CHANGELOG.md has no section for {version}. Write `## {version} - <YYYY-MM-DD>` with "
            "what it changes for the people who install it."
        )
    if dated and section.date is None:
        raise ReleaseFailed(
            f"CHANGELOG.md's section for {version} has no date. A release is dated the day it is "
            f"published: `## {version} - YYYY-MM-DD`."
        )
    notes = release_notes(section.body)
    if not notes.strip():
        raise ReleaseFailed(f"CHANGELOG.md's section for {version} is empty.")
    return notes


def changelog_covers(version: str, sections: list[ChangelogSection]) -> bool:
    """Whether the changelog says what `version` carries: in a section of its own, or under
    Unreleased, where changes wait for the release that will publish them."""
    return any(
        one.title in (version, UNRELEASED) and release_notes(one.body).strip() for one in sections
    )


def check_the_changelog(*, signed: bool, path: Path | None = None) -> str:
    """Refuse, before anything is built, a release the changelog does not describe.

    A signed release is one that can be published, so it needs a dated section of its own, and its
    notes are returned. An unsigned build installs on this device only, and changes waiting under
    Unreleased describe it; the empty string is returned.
    """
    path = CHANGELOG if path is None else path
    if not path.is_file():
        raise ReleaseFailed(f"there is no {path.name} in {path.parent}.")
    sections = read_changelog(path.read_text(encoding="utf-8"))
    if signed:
        return notes_for(VERSION, sections, dated=True)
    if not changelog_covers(VERSION, sections):
        raise ReleaseFailed(
            f"CHANGELOG.md says nothing about {VERSION}: no section for it and nothing under "
            f"`## {UNRELEASED}`. Write what changed before building."
        )
    return ""


def _declared_in_the_shell(name: str) -> str:
    """One string constant of desktop/src/update.ts, which is what the installed shell reads."""
    source = DESKTOP / "src" / "update.ts"
    found = re.search(rf"export const {name} = '([^']+)'", source.read_text(encoding="utf-8"))
    if found is None:
        raise ReleaseFailed(f"{source} no longer declares {name}.")
    return found.group(1)


def release_repository() -> str:
    """`owner/name` of the repository whose latest release the shell's update check reads."""
    feed = _declared_in_the_shell("DEFAULT_FEED_URL")
    found = re.fullmatch(r"https://api\.github\.com/repos/([\w.-]+/[\w.-]+)/releases/latest", feed)
    if found is None:
        raise ReleaseFailed(f"the shell's feed {feed} is not a GitHub repository's latest release.")
    return found.group(1)


def signature_is_good(content: bytes, signature: str, key_base64: str) -> bool:
    """The shell's own test of a minisign signature: the plain form, by this key, over `content`.

    A prehashed signature (algorithm `ED`) is refused, as the shell refuses it.
    """
    lines = [line for line in signature.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    try:
        raw = base64.b64decode(lines[1].strip(), validate=True)
        key = base64.b64decode(key_base64, validate=True)
    except ValueError:
        return False
    if len(raw) != 74 or raw[:2] != b"Ed" or len(key) != 42 or raw[2:10] != key[2:10]:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(key[10:]).verify(raw[10:], content)
    except InvalidSignature:
        return False
    return True


def signed_release(version: str = VERSION, folder: Path = ARTIFACTS) -> list[Path]:
    """The files a published release carries, checked the way an installed shell will check them.

    The installer, its hash, the manifest, the signatures over those two, and the packer's blockmap
    when there is one. Refused unless the manifest names this version and installer, both hashes
    agree with the installer's bytes, and both signatures are by the key the shell carries.
    """
    installer = folder / f"Sift-{version}-x64-setup.exe"
    sums = installer.with_name(f"{installer.name}.sha256")
    manifest = installer.with_name(f"{installer.name}.manifest.json")
    signatures = [one.with_name(f"{one.name}.minisig") for one in (sums, manifest)]
    wanted = [installer, sums, manifest, *signatures]
    missing = [one.name for one in wanted if not one.is_file()]
    if missing:
        raise ReleaseFailed(
            f"{folder} holds no signed release of {version}: {', '.join(missing)} missing. A "
            "release is published signed or not at all."
        )

    digest = _sha256(installer)
    recorded = sums.read_text(encoding="utf-8").split()
    if not recorded or recorded[0] != digest:
        raise ReleaseFailed(f"{sums.name} does not hold the SHA-256 of {installer.name}.")
    expected = {"version": version, "installer": installer.name, "sha256": digest}
    try:
        described = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        described = None
    if described != expected:
        raise ReleaseFailed(f"{manifest.name} does not describe {installer.name} as {version}.")

    key = _declared_in_the_shell("PUBLIC_KEY_BASE64")
    for signed, signature in zip((sums, manifest), signatures, strict=True):
        if not signature_is_good(signed.read_bytes(), signature.read_text(encoding="utf-8"), key):
            raise ReleaseFailed(
                f"{signature.name} is not a signature by the key desktop/src/update.ts carries, so "
                "every installed copy would refuse this release."
            )
    blockmap = installer.with_name(f"{installer.name}.blockmap")
    return [*wanted, *([blockmap] if blockmap.is_file() else [])]


class GhRun(NamedTuple):
    """What one run of the GitHub CLI answered."""

    exit_code: int
    output: str
    error: str


#: How the publish step runs the GitHub CLI: its arguments, and what goes to its standard input. A
#: seam, so the publish step is tested with a stand-in that records every call.
Gh = Callable[[list[str], str | None], GhRun]


def gh_program() -> str:
    """The GitHub CLI that publishes: `RELEASE_GH`, else `gh` on PATH.

    One program with no arguments. A login kept apart from this machine's default one is a small
    script that sets it up and runs `gh`, and `RELEASE_GH` names the script.
    """
    named = os.environ.get("RELEASE_GH", "").strip() or "gh"
    found = shutil.which(named)
    if found is None:
        raise ReleaseFailed(f"{named} cannot be found. Install the GitHub CLI or set RELEASE_GH.")
    return found


def run_gh(args: list[str], stdin: str | None, *, where: Path = ARTIFACTS) -> GhRun:
    """Run the GitHub CLI from the artefacts folder, files by bare name; a `.bat` needs a shell."""
    argv = [gh_program(), *args]
    script = sys.platform == "win32" and argv[0].lower().endswith((".cmd", ".bat"))
    done = subprocess.run(
        subprocess.list2cmdline(argv) if script else argv,
        cwd=where,
        shell=script,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return GhRun(done.returncode, done.stdout or "", done.stderr or "")


def _github(gh: Gh, path: str) -> dict[str, object] | None:
    """One read of GitHub's API: the document, or None when GitHub answers 404."""
    # A query goes as fields: a `&` on the line would end the command in a Windows shell.
    route, _, query = path.partition("?")
    fields = [part for pair in query.split("&") if pair for part in ("-f", pair)]
    done = gh(["api", *(["--method", "GET"] if fields else []), route, *fields], None)
    if done.exit_code != 0:
        if "HTTP 404" in done.error or "Not Found" in done.error:
            return None
        raise ReleaseFailed(f"gh api {path} failed ({done.exit_code}):\n{done.error.strip()}")
    try:
        answer = json.loads(done.output)
    except json.JSONDecodeError:
        answer = None
    if not isinstance(answer, dict):
        raise ReleaseFailed(f"gh api {path} answered something that is not a JSON object.")
    return answer


def publish_steps(
    existing: dict[str, object] | None,
    files: list[Path],
    *,
    repository: str,
    pre_release: bool,
    notes: str,
) -> list[tuple[list[str], str | None]]:
    """The GitHub CLI calls that publish `files`, each with what goes to its standard input.

    A release that is not there is created as a draft against the tag already on GitHub
    (`--verify-tag`), then published with every file on it. One that is there keeps every file whose
    bytes match, gains the ones it lacks and takes the changelog's notes; a file there with other
    bytes stops everything, because somebody may already have checked the one published.
    """
    tag, title, target = f"v{VERSION}", f"Sift {VERSION}", ["--repo", repository]
    edit = ["release", "edit", tag, *target, "--title", title, "--notes-file", "-"]
    edit.append(f"--prerelease={'true' if pre_release else 'false'}")
    if existing is None:
        # A draft first: an immutable release takes no file once it is published.
        create = ["release", "create", tag, *target, "--verify-tag", "--draft", "--title", title]
        create += ["--notes-file", "-", *(["--prerelease"] if pre_release else [])]
        return [([*create, *(one.name for one in files)], notes), ([*edit, "--draft=false"], notes)]

    held: dict[str, dict[str, object]] = {}
    assets = existing.get("assets")
    for asset in assets if isinstance(assets, list) else []:
        if isinstance(asset, dict) and isinstance(asset.get("name"), str):
            held[str(asset["name"])] = asset
    missing: list[str] = []
    for one in files:
        asset = held.get(one.name)
        if asset is None:
            missing.append(one.name)
            continue
        recorded = asset.get("digest")
        if not isinstance(recorded, str) or not recorded.startswith("sha256:"):
            raise ReleaseFailed(
                f"{tag} already carries {one.name} and GitHub records no SHA-256 for it, so it "
                "cannot be compared with this build. Compare it by hand or remove it."
            )
        if recorded.removeprefix("sha256:").lower() != _sha256(one):
            raise ReleaseFailed(
                f"{tag} already carries {one.name} with different bytes. A published file is "
                "never replaced; a different build is a different version."
            )
    if missing and existing.get("immutable") is True:
        raise ReleaseFailed(
            f"{tag} is immutable and lacks {', '.join(missing)}, and GitHub adds nothing to an "
            "immutable release."
        )
    steps: list[tuple[list[str], str | None]] = []
    if missing:
        steps.append((["release", "upload", tag, *target, *missing], None))
    if existing.get("draft") is True:
        edit.append("--draft=false")
    steps.append((edit, notes))
    return steps


def publish(
    *,
    pre_release: bool,
    dry_run: bool,
    gh: Gh | None = None,
    folder: Path | None = None,
    changelog: Path | None = None,
) -> list[tuple[list[str], str | None]]:
    """Publish this version's signed release on GitHub, or with `dry_run` say what that would do.

    Only a signed release that CHANGELOG.md describes, with its tag already pushed. A pre-release is
    one only when asked: the update check reads `/releases/latest`, which never names a pre-release.
    Returns the steps, run or not.
    """
    where = ARTIFACTS if folder is None else folder
    call: Gh = gh if gh is not None else (lambda args, stdin: run_gh(args, stdin, where=where))
    notes = check_the_changelog(signed=True, path=changelog)
    files = signed_release(VERSION, where)
    repository = release_repository()
    tag = f"v{VERSION}"
    kind = "a pre-release" if pre_release else "a full release"
    print(f"\n  Publishing Sift {VERSION} to {repository} as {tag}, {kind}")

    if _github(call, f"repos/{repository}/git/ref/tags/{tag}") is None:
        raise ReleaseFailed(
            f"GitHub shows no tag {tag} in {repository}. Tag the commit this release was built "
            "from and push the tag, then publish; if the tag is there, this login cannot see the "
            "repository."
        )
    existing = _github(call, f"repos/{repository}/releases/tags/{tag}")
    print(f"  {tag} {'has no release yet' if existing is None else 'already has a release'}")
    steps = publish_steps(
        existing, files, repository=repository, pre_release=pre_release, notes=notes
    )
    for args, stdin in steps:
        shown = subprocess.list2cmdline(args)
        print(f"\n  $ gh {shown}" + ("  < the notes" if stdin is not None else ""))
    print("\n  The notes:\n\n" + "".join(f"    {line}\n" for line in notes.splitlines()))
    if dry_run:
        print("  Dry run: nothing was sent.\n")
        return steps

    for args, stdin in steps:
        done = call(args, stdin)
        if done.exit_code != 0:
            raise ReleaseFailed(f"gh {args[1]} failed ({done.exit_code}):\n{done.error.strip()}")
    print(f"  Published: https://github.com/{repository}/releases/tag/{tag}\n")
    return steps


def check_the_suite(gh: Gh | None = None, commit: str | None = None) -> None:
    """Refuse to publish a commit the hosted suite has not passed."""
    call: Gh = gh if gh is not None else (lambda args, stdin: run_gh(args, stdin, where=ROOT))
    if commit is None:
        head = _git(ROOT, "rev-parse", "HEAD")
        if head.returncode != 0:
            raise ReleaseFailed(f"git could not name HEAD: {head.stderr.strip()}")
        commit = head.stdout.strip()
    runs = _github(call, release_gates.runs_path(release_repository(), commit))
    found = release_gates.suite_verdict(runs, commit)
    if found is not None:
        raise ReleaseFailed(f"A release is published only from a commit the suite passed: {found}")


def check_the_notices(manifest: Path = MANIFEST) -> None:
    """Refuse a signed build while a library inside a shipped program has no licence or source."""
    missing = release_gates.unrecorded(release_gates.read_manifest(manifest))
    if missing:
        more = f" and {len(missing) - 8} more" if len(missing) > 8 else ""
        raise ReleaseFailed(
            f"{manifest.name} records no licence or source for {', '.join(missing[:8])}{more}. "
            "Read each from the library's own files and record it under `inside`."
        )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """One read-only git question about `repo`. Never a fetch, never a write."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def check_the_tree(*, signed: bool, repo: Path = ROOT) -> None:
    """Refuse a signed or published release built from anything but a clean commit on `origin/main`.

    Read-only: nothing is fetched. An unsigned build for this device only prints a warning.
    """
    problems: list[str] = []
    status = _git(repo, "status", "--porcelain")
    if status.returncode != 0:
        raise ReleaseFailed(f"git could not read the working tree: {status.stderr.strip()}")
    lines = [one for one in status.stdout.splitlines() if one.strip()]
    changed = [one[3:] for one in lines if not release_signing.written_by_the_build(one, VERSION)]
    if changed:
        more = f" and {len(changed) - 5} more" if len(changed) > 5 else ""
        problems.append(
            f"the working tree has changes nobody committed ({', '.join(changed[:5])}{more})"
        )
    if _git(repo, "rev-parse", "--verify", "--quiet", "origin/main").returncode != 0:
        problems.append("this clone has no origin/main to compare HEAD with")
    elif _git(repo, "merge-base", "--is-ancestor", "HEAD", "origin/main").returncode != 0:
        problems.append("HEAD is not on origin/main: push it, then build")
    if not problems:
        return
    if signed:
        raise ReleaseFailed(
            "A signed or published release is built from a pushed commit and nothing else: "
            + "; ".join(problems)
            + "."
        )
    print("\n  NOT A RELEASE ANYBODY ELSE SHOULD INSTALL: " + "; ".join(problems) + ".\n")


def build(*, skip_vendor: bool, no_sign: bool) -> None:
    """The installer, its hash and, unless `no_sign`, the manifest and the two signatures."""
    print(f"\n  Building Sift {VERSION}")
    build_client(with_gallery=no_sign)
    if not skip_vendor:
        fetch_vendor()
    release_gates.write_notice(MANIFEST, VENDOR)
    if skip_vendor:
        _check_the_vendored_tools_are_in(VENDOR, "--skip-vendor reuses vendor/bin, and it")
    build_runtime()
    write_upgrade_fixture()
    rebuild_addon()
    build_shell()
    installer = pack()
    sums = write_hash(installer)
    # No manifest without a signature: an unsigned one would describe a release nothing can
    # verify, and the desktop application refuses a release that carries none.
    manifest = None if no_sign else write_manifest(installer, sums)
    signatures = [] if manifest is None else sign(sums, manifest)
    put_on_the_desktop([installer, sums, *([] if manifest is None else [manifest]), *signatures])
    # After the copy, never before it: everything up to here can still fail, and a folder
    # tidied by a run that then produced nothing would have thrown away the release somebody
    # was about to roll back to.
    prune_old_releases()

    size = installer.stat().st_size / 1e6
    signed = [one.name for one in ([] if manifest is None else [manifest, *signatures])]
    print(
        f"\n  {installer.name}  ({size:.0f} MB)\n"
        f"  {sums.name}\n" + "".join(f"  {name}\n" for name in signed)
    )
    print(f"  All of them are in {ARTIFACTS}.\n")
    if not signatures:
        print("  NOT SIGNED. No installed copy of Sift will offer this release as an update.\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build one Sift installer.")
    parser.add_argument(
        "--skip-vendor",
        action="store_true",
        help="reuse the vendor/bin already here instead of re-verifying it",
    )
    parser.add_argument(
        "--no-sign", action="store_true", help="stop after the hash, without signing"
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="after the build and the signature, publish the release on GitHub",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --publish: build nothing, send nothing, and say what publishing would do",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="with --publish: sign if need be and publish the release built for this version",
    )
    parser.add_argument(
        "--pre-release",
        action="store_true",
        help="with --publish: mark it a pre-release, which the update check never offers",
    )
    parser.add_argument(
        "--tools-dir",
        type=Path,
        help=f"the folder a release tool not on PATH is found in (else {TOOLS_DIR_VARIABLE})",
    )
    args = parser.parse_args(argv)
    if args.tools_dir is not None:
        os.environ[TOOLS_DIR_VARIABLE] = str(args.tools_dir)
    if not args.publish and (args.dry_run or args.no_build or args.pre_release):
        parser.error("--dry-run, --no-build and --pre-release go with --publish")
    if args.publish and args.no_sign:
        parser.error("a release is published signed or not at all; drop --no-sign")
    building = not (args.dry_run or args.no_build)

    if building and sys.platform != "win32":
        print(
            "\nA release can only be built on Windows: the addon is compiled with MSVC and the "
            "installer is an NSIS executable.\n",
            file=sys.stderr,
        )
        return 1

    stage = "published" if args.publish else "built"
    try:
        # First, so a release the changelog does not describe, or one built from a tree the
        # project does not hold, stops in a second, not after a build.
        check_the_changelog(signed=not args.no_sign)
        check_the_tree(signed=args.publish or not args.no_sign)
        if not args.no_sign:
            check_the_notices()
        if args.publish:
            check_the_suite()
        if building:
            build(skip_vendor=args.skip_vendor, no_sign=args.no_sign)
        else:
            sign_what_is_built(signing=not args.dry_run)
        if args.publish:
            publish(pre_release=args.pre_release, dry_run=args.dry_run)
        return 0
    except ReleaseFailed as failure:
        print(f"\nThe release was not {stage}.\n\n  {failure}\n", file=sys.stderr)
        return 1


#: The biggest the application's own code may be: a few megabytes of compiled TypeScript, so twenty
#: still catches a `files` glob that has started sweeping something enormous.
MAX_APP_BUNDLE_MB = 20


def _check_the_vendored_tools_are_in_it() -> None:
    """Refuse a pack missing a vendored tool: an antivirus quarantining one mid-copy leaves the
    packer exiting 0 with an installer that installs and then fails when the tool is reached for."""
    _check_the_vendored_tools_are_in(
        ARTIFACTS / "win-unpacked" / "resources" / "vendor" / "bin", "the packed application"
    )


def _check_the_vendored_tools_are_in(folder: Path, what: str) -> None:
    """Refuse unless every tool the manifest declares is in `folder`, and each program built here
    is the file its recipe makes. `--skip-vendor` is checked before the build, the pack after it."""
    executables, folders, notices = _vendored_files()
    missing = sorted(name for name in executables if not (folder / name).is_file())
    # A GPL program shipped without its source breaks the terms it is distributed under.
    missing += sorted(rel for rel in notices if not (folder / rel).is_file())
    # Only non-empty: the publisher's file list changes every release.
    missing += sorted(f"{name}/" for name in folders if not any((folder / name).glob("*")))
    if missing:
        raise ReleaseFailed(
            f"{what} is missing {', '.join(missing)} from {folder}.\n"
            "  The tools are copied in as extraResources, so this means the copy did not happen or "
            "something removed them afterwards. An antivirus quarantining one mid-build is how "
            "this was first seen. Do NOT ship it: the installer would install, and the feature that "
            "needs the tool would fail on somebody's machine with nothing to say why. Run "
            "scripts/fetch_vendor.py."
        )
    prove_the_built_programs_are_pinned(folder, what)


def _vendored_files() -> tuple[set[str], set[str], set[str]]:
    """What the manifest says vendor/bin holds: the executables and the program folders by name,
    and the licence texts and source archives by their path under bin/."""
    declared = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # Executables only: a `bin/` destination takes DLLs by pattern and has no one name to look for.
    wanted: set[str] = set()
    folders: set[str] = set()
    notices: set[str] = set()
    for tool_entry in _every_tool(declared):
        # Each shape is checked before it is walked: a hand-edited manifest can hold anything.
        extract = tool_entry.get("extract")
        if isinstance(extract, dict):
            for where in extract:
                if str(where).lower().endswith(".exe"):
                    wanted.add(Path(str(where)).name)
        # A program built here names its one file rather than an archive member.
        built = tool_entry.get("file")
        if isinstance(built, str) and built.lower().endswith(".exe"):
            wanted.add(Path(built).name)
        tree = tool_entry.get("extract_tree")
        if isinstance(tree, dict):
            for where in tree:
                folders.add(Path(str(where).rstrip("/")).name)
        extra_files = tool_entry.get("extra_files")
        declared_here: list[str] = []
        if isinstance(extra_files, list):
            for extra in extra_files:
                dest = extra.get("dest", "") if isinstance(extra, dict) else ""
                if str(dest).lower().endswith(".exe"):
                    wanted.add(Path(str(dest)).name)
                elif str(dest).startswith("bin/"):
                    notices.add(str(dest)[len("bin/") :])
                    declared_here.append(str(dest)[len("bin/") :])
        _check_a_gpl_tool_declares_its_source(tool_entry, declared_here)
    # A wheel's recipe and the files it reads are part of the source the LGPL asks for.
    notices.update(release_gates.recipe_files(_pinned_wheels()))
    notices.add(release_gates.NOTICE)
    if not wanted:
        raise ReleaseFailed(
            "no vendored tools were found in scripts/vendor_manifest.json, so this check cannot "
            "tell whether the build packed them. Read the manifest before releasing."
        )
    return wanted, folders, notices


#: A licence that asks a binary to travel with its source: GPL, LGPL or AGPL, any version. Read from
#: the start of the manifest's `licence` text, which is where each entry names its own.
_SOURCE_LICENCE = re.compile(r"^[A-Z]?GPL-")


def _check_a_gpl_tool_declares_its_source(tool: dict[str, object], shipped: list[str]) -> None:
    """Refuse a GPL tool whose manifest entry ships no source archive named for THIS version, so a
    bumped pin cannot ship beside the old version's source."""
    licence = str(tool.get("licence", ""))
    if not _SOURCE_LICENCE.match(licence):
        return
    version = str(tool.get("version", ""))
    if version and any(rel.startswith("sources/") and version in Path(rel).name for rel in shipped):
        return
    raise ReleaseFailed(
        f"{tool.get('name', '?')} {version} is {licence.split(' ', 1)[0]} and "
        "scripts/vendor_manifest.json ships no source archive for this version under "
        f"bin/sources/ (it declares {', '.join(shipped) or 'nothing'}).\n"
        "  A GPL program is redistributed WITH its source. Add the source archive of exactly the "
        "pinned build to its extra_files, named with its version, before releasing."
    )


def _every_tool(node: object) -> list[dict[str, object]]:
    """Every entry in the manifest that names a tool, wherever the file happens to nest them."""
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        if "name" in node and ("extract" in node or "extra_files" in node):
            found.append(node)
        for value in node.values():
            found.extend(_every_tool(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_every_tool(value))
    return found


#: The marker @electron/fuses writes into an Electron binary, with the fuse wire immediately after
#: it: one version byte, one length byte, then one byte per fuse. Copied from the package rather
#: than computed, because it IS the constant: there is nothing to derive it from.
FUSE_SENTINEL = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"

#: What one byte of the wire means. @electron/fuses' own numbers: the first two are the characters
#: `0` and `1`, which is why a wire reads as a run of digits in a hex editor.
FUSE_OFF = 0x30
FUSE_ON = 0x31
FUSE_REMOVED = 0x72
FUSE_INHERIT = 0x90

#: EVERY FUSE THIS APPLICATION HAS AN OPINION ABOUT, by its position in the wire, with the state it
#: must be in. Five, and the numbers are @electron/fuses' `FuseV1Options`.
#: DECLARED HERE AND NOT READ OUT OF electron-builder.yml: a check that read its expectation from
#: the configuration would pass in silence the day a line was deleted from it. A gate holds the two
#: in step (tests/gates/test_the_shell_ships_with_its_fuses_flipped.py); this is checked against
#: the bytes that shipped.
INTENDED_FUSES: dict[int, tuple[str, bool]] = {
    0: ("RunAsNode", False),
    2: ("EnableNodeOptionsEnvironmentVariable", False),
    3: ("EnableNodeCliInspectArguments", False),
    4: ("EnableEmbeddedAsarIntegrityValidation", True),
    5: ("OnlyLoadAppFromAsar", True),
}

#: How long the packed shell is given to say it started before it is taken down.
#:
#: A stop, not a measurement: this number cannot tell a slow machine from a hung one and does not
#: try, and the message it produces says so rather than naming a cause. Generous on purpose: the
#: cost of being wrong in one direction is a release that fails for no reason.
SMOKE_TIMEOUT_S = 120.0


def read_fuse_wire(binary: Path) -> dict[int, int]:
    """The fuse states baked into an Electron binary, found by their sentinel as @electron/fuses
    finds them: wire version, fuse count, then one byte per fuse."""
    with binary.open("rb") as handle:
        size = binary.stat().st_size
        if size == 0:
            raise ReleaseFailed(f"{binary} is empty, so it carries no fuses.")
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
            at = view.find(FUSE_SENTINEL)
            if at == -1:
                raise ReleaseFailed(
                    f"{binary} carries no fuse wire. Either it is not an Electron binary, or the "
                    "packer wrote something this script cannot read. Do not ship it until the "
                    "fuses can be read back."
                )
            wire = at + len(FUSE_SENTINEL)
            version = view[wire]
            if version != 1:
                raise ReleaseFailed(
                    f"{binary} carries fuse wire version {version}, and this script reads version "
                    "1. Electron has changed the format; update @electron/fuses and this reader "
                    "together before shipping."
                )
            length = view[wire + 1]
            return {index: view[wire + 2 + index] for index in range(length)}


def _check_the_fuses_are_flipped() -> None:
    """Refuse a build whose packed executable does not carry the fuse states it was configured with.

    Read from the packed file, since electron-builder flips them after the bundle is assembled and a
    fuse left unflipped leaves an application that works with a door open in it.
    """
    shell = ARTIFACTS / "win-unpacked" / "Sift.exe"
    if not shell.is_file():
        raise ReleaseFailed(f"the pack produced no {shell.name} at {shell}.")
    wire = read_fuse_wire(shell)
    wrong: list[str] = []
    for index, (name, wanted) in sorted(INTENDED_FUSES.items()):
        state = wire.get(index)
        if state is None:
            wrong.append(f"{name}: this Electron's fuse wire is too short to carry it")
        elif state == FUSE_REMOVED:
            wrong.append(
                f"{name}: removed from this version of Electron, so setting it did nothing"
            )
        elif state == FUSE_INHERIT:
            wrong.append(f"{name}: never set, left at whatever Electron was built with")
        elif state != (FUSE_ON if wanted else FUSE_OFF):
            reads = "on" if state == FUSE_ON else "off"
            wrong.append(f"{name}: {reads}, and it must be {'on' if wanted else 'off'}")
    if wrong:
        raise ReleaseFailed(
            "the packed application's fuses are not what this release intends:\n  "
            + "\n  ".join(wrong)
            + f"\n  They are set by `electronFuses` in {DESKTOP / 'electron-builder.yml'}, at pack "
            "time. Do NOT ship it: an installer with a fuse the wrong way round installs and runs, "
            "and the only symptom is a door nobody can see."
        )


def smoke_marker() -> str:
    """The line the shell prints for a smoke run, READ FROM THE SHELL ITSELF.

    The same argument `shell_interpreter_args` is written under: copied here it would be a second
    declaration of one string, and the two would part company the first time either was edited,
    leaving a boot check that could never pass, or worse, one looking for a line nothing writes any
    more and reporting a working build as broken.
    """
    source = DESKTOP / "src" / "smoke.ts"
    text = source.read_text(encoding="utf-8")
    found = re.search(r"export const SMOKE_MARKER = '([^']+)'", text)
    if found is None:
        raise ReleaseFailed(
            f"{source} no longer declares SMOKE_MARKER, so there is nothing saying what a smoke "
            "run prints. Either it moved (in which case this must follow it) or the smoke run "
            "was removed, and this check has to go with it rather than be left unable to pass."
        )
    return found.group(1)


class ShellRun(NamedTuple):
    """What one run of the packed shell did. `exit_code` is None when it had to be taken down."""

    exit_code: int | None
    output: str


#: How the boot check starts a process. A seam so the check can be tested without a build: every
#: way the launch can go is a return value here, and the fake in the test returns each of them.
Launcher = Callable[[list[str], float], ShellRun]


def run_the_shell(command: list[str], timeout_s: float) -> ShellRun:
    """Start the packed shell, read what it says, and take it down by PID if it does not stop.

    BY PID, AND WITH `/T`. `taskkill /IM Sift.exe` does not take the tree down: the children
    answer "can only be terminated forcefully", the main process survives and respawns them, and
    port 5171 keeps coming back under a new number. `Popen.kill` has the other half of the same
    problem: it terminates the one process it started and leaves whatever it spawned running.
    """
    started = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        output, _ = started.communicate(timeout=timeout_s)
        return ShellRun(started.returncode, output)
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/PID", str(started.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        output, _ = started.communicate()
        return ShellRun(None, output or "")


def _check_the_shell_boots(launch: Launcher = run_the_shell) -> None:
    """Start the packed application with `--smoke` and require its marker.

    The fuses decide whether Electron runs Sift's code at all. It stops short of the backend: the
    port and the data folder are whoever's machine this is. The marker, not the exit code, because
    0 is also what a second copy exits with.
    """
    shell = ARTIFACTS / "win-unpacked" / "Sift.exe"
    if not shell.is_file():
        raise ReleaseFailed(f"the pack produced no {shell.name} at {shell}.")
    marker = smoke_marker()
    run_result = launch([str(shell), "--smoke"], SMOKE_TIMEOUT_S)
    tail = run_result.output.strip()[-2000:]
    if run_result.exit_code is None:
        raise ReleaseFailed(
            f"{shell.name} did not stop within {SMOKE_TIMEOUT_S:.0f} seconds and was taken down.\n"
            "  It was asked only to say it had started. Something held it open: a dialog waiting "
            "to be answered is the usual one, and a machine merely being slow is the other. Look "
            f"at it by hand before shipping.\n{tail}"
        )
    if run_result.exit_code != 0:
        raise ReleaseFailed(
            f"{shell.name} would not start: it exited with {run_result.exit_code}.\n"
            "  An `ASAR Integrity Violation` here means the bundle and the hash compiled into the "
            "executable disagree, which is what the two archive fuses are for. Do NOT ship it: "
            f"this is the application refusing to open on every machine it is installed on.\n{tail}"
        )
    if marker not in run_result.output:
        raise ReleaseFailed(
            f"{shell.name} exited cleanly and never said it had started.\n"
            f"  It was looked for by the line desktop/src/smoke.ts writes ({marker!r}). Exiting 0 "
            "without it is what this shell does when another copy holds the single-instance lock, "
            "so a clean exit on its own proves nothing.\n" + tail
        )


def _check_the_bundle_is_sane() -> None:
    """Refuse a build whose app.asar has swallowed something it should not have.

    THE FAULT THIS CATCHES DOES NOT FAIL. If `dist` is both tsc's output directory and
    electron-builder's, each build packs the previous build's installer and unpacked tree into the
    next one's bundle, doubling it with identical code inside. Nothing errors, nothing looks wrong,
    and the only symptom is a number in a log nobody is comparing against the last one.
    """
    bundle = ARTIFACTS / "win-unpacked" / "resources" / "app.asar"
    if not bundle.exists():
        # Refused rather than skipped. A check that passes when it cannot find what it measures is
        # the shape of a gate that stops meaning anything the day a path moves.
        raise ReleaseFailed(f"the pack produced no bundle at {bundle}.")
    size_mb = bundle.stat().st_size / 1_000_000
    if size_mb > MAX_APP_BUNDLE_MB:
        raise ReleaseFailed(
            f"app.asar is {size_mb:.0f} MB, and it should be a few.\n"
            "  Something in electron-builder's `files` is sweeping more than the main process's\n"
            "  own code. The usual cause is a build directory that is also an output directory."
        )


if __name__ == "__main__":
    raise SystemExit(main())
