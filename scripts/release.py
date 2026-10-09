#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build one Sift installer, from a clean tree to a signed hash. The only way a release is made.

    python scripts/release.py                    # build it
    python scripts/release.py --skip-vendor      # reuse the verified vendor/bin already here
    python scripts/release.py --no-sign          # build without minisign, to install on this device
    python scripts/release.py --publish          # build, sign, and publish it on GitHub
    python scripts/release.py --publish --dry-run   # say what publishing would do; send nothing
    python scripts/release.py --tools-dir D:/tools  # where a release tool not on PATH is found

One script, because a release has to contain things that are easy to forget: the drag addon is
bound to one Electron version, so its recompile is a step here.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_bytecode
import release_gates
import release_signing
from release_artefacts import ARTEFACT, INTERMEDIATE, KEEP_RELEASES, OURS, put_on_the_desktop
from release_changelog import UNRELEASED, changelog_covers, notes_for, read_changelog, release_notes
from release_common import (
    DESKTOP,
    MANIFEST,
    PYTHON_VERSION,
    ROOT,
    RUNTIME,
    ReleaseFailed,
    _sha256,
    _version_key,
)
from release_cpp import (
    CPP_COMPANIONS,
    CPP_RUNTIME,
    _parts,
    _visual_studio_redist,
    carry_the_cpp_runtime,
    cpp_runtime_source,
    file_version,
    linker_version,
    newest_linked,
    whole_cpp_runtime,
)
from release_fixture import (
    _MAKE_A_LIBRARY,
    FIXTURE,
    FIXTURES,
    keep_one_fixture,
    without_sql_comments,
)
from release_pack import (
    FUSE_INHERIT,
    FUSE_OFF,
    FUSE_ON,
    FUSE_REMOVED,
    FUSE_SENTINEL,
    Launcher,
    ShellRun,
    _check_a_gpl_tool_declares_its_source,
    _vendored_files,
    read_fuse_wire,
    run_the_shell,
)
from release_publish import Gh, GhRun, _github, gh_program, missing_from, signature_is_good
from release_runtime import (
    _interpreter_behind,
    _pinned_wheels,
    prove_runtime_is_self_contained,
    prove_the_built_programs_are_pinned,
    prove_the_runtime_carries_the_pinned_wheels,
    prove_the_runtime_ignores_other_pythons,
    prove_the_runtime_ships_no_tests,
    prove_the_runtime_ships_the_site_icons,
    wheel_dlls_refused,
)

#: Read from this module by the tests, though only a sibling uses them.
__all__ = [
    "FIXTURE",
    "FUSE_SENTINEL",
    "OURS",
    "ShellRun",
    "_check_a_gpl_tool_declares_its_source",
    "_visual_studio_redist",
    "cpp_runtime_source",
    "linker_version",
    "release_notes",
    "wheel_dlls_refused",
    "whole_cpp_runtime",
]

FRONTEND = ROOT / "frontend"
CLIENT = ROOT / "src" / "sift" / "web"
VENDOR = ROOT / "vendor" / "bin"
#: Not `dist`: electron-builder packs that, so each build would pack the last one's output.
ARTIFACTS = DESKTOP / "release"


def _declared_version() -> str:
    """Sift's version, read from pyproject.toml; a second copy anywhere would drift."""
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(declared["project"]["version"]).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"pyproject.toml declares version {version!r}, which is not a version")
    # CI installs with `--locked`, so a bump that missed the lockfile could never pass.
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


VERSION = _declared_version()

#: Never in the repository: losing it fails every future release against the shipped public key.
MINISIGN_KEY = Path(os.environ.get("RELEASE_SIGNING_KEY", Path.home() / ".minisign" / "sift.key"))


def run(command: list[str], *, where: Path, quiet: bool = False) -> str:
    """Run one command, stopping the release if it fails; a shell on Windows runs `.cmd` tools."""
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


#: Not a `SIFT_` name: the application refuses to start beside a `SIFT_` variable it does not know.
TOOLS_DIR_VARIABLE = "RELEASE_TOOLS_DIR"


def toolchain() -> Path | None:
    """The tools folder this run was given, or None where nobody named one."""
    named = os.getenv(TOOLS_DIR_VARIABLE)
    return Path(named) if named else None


def tool(name: str) -> str:
    """A tool's path from PATH, then the tools folder, refusing early rather than four steps in."""
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


#: The lockfile's run-time packages at their locked hashes; `--frozen` never resolves it again.
_EXPORT_THE_LOCK = ("export", "--frozen", "--no-emit-project", "--format", "requirements-txt")


def build_runtime() -> None:
    """A real interpreter copied whole with the backend installed into it, non-editable."""
    uv = tool("uv")
    staging = ROOT / "build" / "runtime-staging"
    for directory in (RUNTIME, staging):
        if directory.exists():
            shutil.rmtree(directory)
    RUNTIME.parent.mkdir(parents=True, exist_ok=True)

    # Its `pyvenv.cfg` then names the interpreter uv chose; asking uv again could disagree.
    run([uv, "venv", "--python", PYTHON_VERSION, str(staging)], where=ROOT)
    base = _interpreter_behind(staging)
    _install_the_locked_packages(uv, staging)
    packages = _copy_into_the_runtime(base, staging)
    _refuse_an_editable_install(packages)

    run(release_bytecode.compile_command(RUNTIME), where=ROOT, quiet=True)
    prove_runtime_is_self_contained()
    prove_the_runtime_ignores_other_pythons()
    prove_the_runtime_ships_no_tests()
    prove_the_runtime_ships_bytecode()
    prove_the_runtime_ships_the_site_icons()
    prove_the_runtime_carries_the_pinned_wheels(packages)
    prove_the_runtime_carries_the_cpp_runtime()


def _install_the_locked_packages(uv: str, staging: Path) -> None:
    """The lockfile's packages, hash-checked, then Sift and the wheels built here."""
    python = str(staging / "Scripts" / "python.exe")
    locked = staging.parent / "runtime-requirements.txt"
    run([uv, *_EXPORT_THE_LOCK, "-o", str(locked)], where=ROOT)
    run(
        [uv, "pip", "install", "--python", python, "--require-hashes", "-r", str(locked)],
        where=ROOT,
    )
    run([uv, "pip", "install", "--python", python, "--no-deps", "."], where=ROOT)
    # Checked against its pin here: `--skip-vendor` skips the fetch that would have.
    for wheel in _pinned_wheels():
        built = ROOT / "vendor" / str(wheel["file"])
        if not built.is_file() or _sha256(built) != wheel["sha256"]:
            raise ReleaseFailed(
                f"{built} is missing or is not the file scripts/vendor_manifest.json pins. Build it "
                f"with {wheel['recipe']}, or run scripts/fetch_vendor.py to see what differs."
            )
        reinstall = ["--no-deps", "--reinstall-package", str(wheel["package"]), str(built)]
        run([uv, "pip", "install", "--python", python, *reinstall], where=ROOT)


def _copy_into_the_runtime(base: Path, staging: Path) -> Path:
    """The interpreter, the C++ runtime and the staged packages, copied into RUNTIME."""
    # Headers and link libraries are for compiling only; nothing at run time imports them.
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
    return packages


def _refuse_an_editable_install(packages: Path) -> None:
    """Refuse a backend without its client, or with a `.pth` naming a folder outside the runtime."""
    if not (packages / "sift" / "web" / "index.html").is_file():
        raise ReleaseFailed(
            "the installed backend has no client inside it. Either the client was not built before "
            "this ran, or the install was editable, in which case the packaged application would "
            "look for its files in this checkout."
        )
    # Read, not counted: uv's own `_virtualenv.pth` points nowhere.
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


def prove_the_runtime_ships_bytecode(runtime: Path = RUNTIME) -> None:
    """Refuse a runtime where any module lacks its checked-hash `.pyc`."""
    if found := release_bytecode.uncompiled(runtime):
        raise ReleaseFailed(found)


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
    """The drag addon, against the Electron this release ships; built for another, it crashes."""
    run(["npm", "run", "rebuild:native"], where=DESKTOP)
    built = DESKTOP / "native" / "drag" / "build" / "Release" / "drag.node"
    if not built.is_file():
        raise ReleaseFailed(f"the addon was not produced at {built}.")


def build_shell() -> None:
    run(["npm", "run", "build"], where=DESKTOP)


def pack() -> Path:
    """The installer, stamped with the version this repository declares."""
    run(["npm", "run", "pack", "--", f"-c.extraMetadata.version={VERSION}"], where=DESKTOP)
    _check_the_bundle_is_sane()
    _check_the_vendored_tools_are_in_it()
    _check_the_fuses_are_flipped()
    _check_the_shell_boots()
    # Named, never sorted for: "Sift-1.0.9" sorts after "Sift-1.0.10".
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


def write_manifest(installer: Path, sums: Path) -> Path:
    """Version, installer and SHA-256 under the signature: an old installer cannot pass as new."""
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
    """`--no-build`: sign a built release an earlier run left unsigned; a dry run signs nothing."""
    installer = folder / f"Sift-{VERSION}-x64-setup.exe"
    sums = installer.with_name(f"{installer.name}.sha256")
    if signing and sums.is_file() and not sums.with_name(f"{sums.name}.minisig").is_file():
        manifest = write_manifest(installer, sums)
        put_on_the_desktop([installer, sums, manifest, *sign(sums, manifest)])


def prune_old_releases(
    folder: Path = ARTIFACTS, *, keep: int = KEEP_RELEASES, never: str = VERSION
) -> int:
    """Delete whole releases past the newest few (never `never`) and packer leftovers; bytes freed."""
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
    # A negative end would be Python's "all but the last n": a delete, not an off-by-one.
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


def write_upgrade_fixture(
    python: Path | None = None, folder: Path = FIXTURES, version: str = VERSION
) -> Path:
    """The library the released runtime makes, dumped and gzipped for the next upgrade test."""
    interpreter = python or RUNTIME / "python.exe"
    with tempfile.TemporaryDirectory() as scratch:
        # A file, not `-c`: a line break inside one argument does not survive a launcher.
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


CHANGELOG = ROOT / "CHANGELOG.md"


def check_the_changelog(*, signed: bool, path: Path | None = None) -> str:
    """Refuse a release the changelog does not describe; a signed one's dated notes are returned."""
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


def signed_release(version: str = VERSION, folder: Path = ARTIFACTS) -> list[Path]:
    """The files a published release carries, checked the way an installed shell will check them."""
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


def publish_steps(
    existing: dict[str, object] | None,
    files: list[Path],
    *,
    repository: str,
    pre_release: bool,
    notes: str,
) -> list[tuple[list[str], str | None]]:
    """The GitHub CLI calls that publish `files`, each with what goes to its standard input."""
    tag, title, target = f"v{VERSION}", f"Sift {VERSION}", ["--repo", repository]
    edit = ["release", "edit", tag, *target, "--title", title, "--notes-file", "-"]
    edit.append(f"--prerelease={'true' if pre_release else 'false'}")
    if existing is None:
        # A draft first: an immutable release takes no file once it is published.
        create = ["release", "create", tag, *target, "--verify-tag", "--draft", "--title", title]
        create += ["--notes-file", "-", *(["--prerelease"] if pre_release else [])]
        return [([*create, *(one.name for one in files)], notes), ([*edit, "--draft=false"], notes)]

    missing = missing_from(existing, files, tag)
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
    """Publish this version's signed release on GitHub, or with `dry_run` say what that would do."""
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
    """Refuse a signed release but from a clean commit on `origin/main`; nothing is fetched."""
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
    # No manifest without a signature: the desktop application refuses one that carries none.
    manifest = None if no_sign else write_manifest(installer, sums)
    signatures = [] if manifest is None else sign(sums, manifest)
    put_on_the_desktop([installer, sums, *([] if manifest is None else [manifest]), *signatures])
    # Last: a run that failed before here must not have pruned the release to roll back to.
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
        # First, so a release that cannot ship stops in a second, not after a build.
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


MAX_APP_BUNDLE_MB = 20


def _check_the_vendored_tools_are_in_it() -> None:
    """Refuse a pack missing a vendored tool, which an antivirus can quarantine mid-copy."""
    _check_the_vendored_tools_are_in(
        ARTIFACTS / "win-unpacked" / "resources" / "vendor" / "bin", "the packed application"
    )


def _check_the_vendored_tools_are_in(folder: Path, what: str) -> None:
    """Refuse unless every declared tool is in `folder`, each built program its recipe's file."""
    executables, folders, notices = _vendored_files()
    missing = sorted(name for name in executables if not (folder / name).is_file())
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


#: Every fuse decided, by its position in the wire. Declared here, not read from the packer's
#: configuration, which a deleted line would quietly agree with.
INTENDED_FUSES: dict[int, tuple[str, bool]] = {
    0: ("RunAsNode", False),
    2: ("EnableNodeOptionsEnvironmentVariable", False),
    3: ("EnableNodeCliInspectArguments", False),
    4: ("EnableEmbeddedAsarIntegrityValidation", True),
    5: ("OnlyLoadAppFromAsar", True),
}

#: A stop, not a measurement: generous, since too short fails a good release.
SMOKE_TIMEOUT_S = 120.0


def _check_the_fuses_are_flipped() -> None:
    """Refuse a packed executable whose fuses are not the ones intended, read from its bytes."""
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
    """The line the shell prints for a smoke run, read from the shell so the two cannot drift."""
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


def _check_the_shell_boots(launch: Launcher = run_the_shell) -> None:
    """Start the packed application with `--smoke` and require its marker; a second copy exits 0."""
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
    """Refuse an app.asar that has swallowed an earlier build, a fault that fails nothing."""
    bundle = ARTIFACTS / "win-unpacked" / "resources" / "app.asar"
    if not bundle.exists():
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
