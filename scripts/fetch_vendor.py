#!/usr/bin/env python3
"""Fetch and verify the external binaries the Windows desktop application ships.

Reads scripts/vendor_manifest.json, downloads each archive into
vendor/_cache, checks its SHA-256 BEFORE unpacking anything, and lays the executables out in
vendor/bin. Re-running is cheap: a cached archive whose hash still matches is not re-downloaded.
The source archives of the libraries inside a wheel the release installs go to vendor/bin/sources
the same way, and what this repository builds itself (a wheel in vendor/wheels, a program in
vendor/bin) is checked against its pinned digest.

WHY THIS IS NOT A "DOWNLOAD LATEST" SCRIPT
------------------------------------------
Falling back to a rolling `latest` tag when a pin has been pruned suits a container that is rebuilt
constantly. An installer that quietly picks up a different, unverified ffmpeg ships that binary to
whoever runs it: a supply-chain event, not an inconvenience. So there is NO fallback to another
build: a pinned URL that is gone is asked for at the one other place the SAME archive is kept,
under the same digest, and otherwise the script fails and says what to do.

WHEN THE URL 404s
-----------------
BtbN keeps roughly the last three dozen autobuild releases and prunes the rest, so a dated pin
eventually stops resolving. Three things protect a build:

  1. `vendor/_cache/`: once an archive is here and verified, a prune upstream cannot stop a build.
     Back it up with the signing key, for the same reason.
  2. The mirror (`MIRROR`), a pre-release of this repository whose assets are the archives under
     their cached names. The digest check is the same, so what unpacks is the pinned file or nothing.
  3. With the cache empty AND both gone, the fix is deliberate: find the current build of the SAME
     ffmpeg release line, verify what it is, and update the manifest's url + sha256 in one commit.
     Never point this at `latest`.

Usage:  python scripts/fetch_vendor.py [--verify-only | --build-missing [--wheel-by-contents]]
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from sift.slices.download.sources.tuning import JS_RUNTIME

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "scripts" / "vendor_manifest.json"
VENDOR = ROOT / "vendor"
CACHE = VENDOR / "_cache"

#: Where a pruned archive is still held: one pre-release's assets, each under its cached name.
MIRROR = "https://github.com/nuvibes/sift/releases/download/vendor-archives/"

#: How long one recipe may take: the tunnel client with an empty module cache downloads every
#: module it is built from, and the wheel compiles two libraries.
RECIPE_TIMEOUT_S = 3600


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path, expected: str) -> None:
    """Download unless a cached copy already matches. Verify before returning, always.

    A folder of archives fetched before (`VENDOR_SEED`) is copied from first, for a machine
    with no cache of its own once upstream has pruned a pinned build; the digest check is the same.
    """
    seed = os.environ.get("VENDOR_SEED")
    if not dest.exists() and seed and (Path(seed) / dest.name).is_file():
        print(f"    seeded  {dest.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(seed) / dest.name, dest)
    if dest.exists():
        got = digest(dest)
        if got == expected:
            print(f"    cached and verified  {dest.name}")
            return
        print(f"    cached copy has the WRONG hash, re-downloading  {dest.name}")
        dest.unlink()

    # `urlopen` opens `file:` too, so a manifest edited to a local path would copy, hash and ship
    # that file with every message still saying "downloading"; the scheme is checked first.
    if not url.startswith("https://"):
        raise SystemExit(f"\n  {url}\n  is not an https address. Refusing to fetch it.\n")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        try:
            _download(url, tmp)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            _download(MIRROR + dest.name, tmp)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        if exc.code == 404:
            raise SystemExit(
                f"\n{url}\n  is gone (404), the mirror holds no {dest.name},\n"
                f"  and there is no verified copy in {CACHE}.\n\n"
                "  This is the expected end of a pinned autobuild: upstream prunes them.\n"
                "  Do NOT point this script at a rolling 'latest' tag: it ships to users.\n"
                "  Find the current build of the SAME release line, check its configuration,\n"
                "  and update url + sha256 in scripts/vendor_manifest.json in one commit.\n"
            ) from None
        raise

    got = digest(tmp)
    if got != expected:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"\n  SHA-256 MISMATCH for {dest.name}\n"
            f"    expected  {expected}\n    got       {got}\n\n"
            "  Nothing was unpacked. Either the manifest is stale or the file is not what it claims.\n"
        )
    tmp.replace(dest)
    print(f"    verified  {dest.name}  ({dest.stat().st_size // 1048576} MB)")


def _download(url: str, tmp: Path) -> None:
    print(f"    downloading  {url}")
    with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as fh:  # noqa: S310 (https only)
        shutil.copyfileobj(r, fh, length=1 << 20)


def _members(archive: Path):
    """The archive, whichever kind it is, as (names, open one by name).

    THREE KINDS BECAUSE PUBLISHERS DIFFER, not because a choice was wanted: ffmpeg and libwebp ship
    zips, a Go program's release is usually a gzipped tar, and gallery-dl and the JavaScript engine ship the bare
    executable with no archive round it at all. Behind one shape here so `unpack` below (which is
    where the path-safety rules live) has one copy rather than one per format.

    A bare executable is an archive of ONE member named after the cached file, so the manifest names
    it the same way it names any other member and the one-file rule in `unpack` still holds.
    """
    if archive.suffix.lower() == ".exe":
        return contextlib.nullcontext(), [archive.name], lambda _name: archive.open("rb")
    if archive.name.endswith((".tar.gz", ".tgz")):
        # The caller closes it: `unpack` below opens this in a `with`. It cannot be one here
        # because what is returned is the archive AND two ways of reading it.
        bundle = tarfile.open(archive, "r:gz")  # noqa: SIM115

        def open_tar(name: str):
            member = bundle.getmember(name)
            # A tar can carry symlinks, hard links and devices. Only a real file is ever copied out:
            # `extractfile` answers None for the rest, and a link pointing outside the archive is
            # precisely the thing this refuses to follow.
            if not member.isfile():
                raise SystemExit(f"\n  {archive.name} member {name!r} is not a regular file\n")
            handle = bundle.extractfile(member)
            if handle is None:  # pragma: no cover - isfile() already ruled this out
                raise SystemExit(f"\n  {archive.name} member {name!r} could not be read\n")
            return handle

        return bundle, [m.name for m in bundle.getmembers() if m.isfile()], open_tar

    zipped = zipfile.ZipFile(archive)
    return zipped, zipped.namelist(), zipped.open


def refuse_while_in_use(*, checking_only: bool) -> None:
    """Stop before anything is replaced while a program in vendor/ is running: Windows will not
    open one for writing, and a fetch stopped part way leaves a mixture of two releases."""
    held: list[str] = []
    programs = [] if checking_only else sorted(VENDOR.rglob("*"))
    for path in programs:
        if path.suffix.lower() not in (".exe", ".dll", ".pyd") or not path.is_file():
            continue
        try:
            with path.open("r+b"):
                pass
        except OSError:
            held.append(path.relative_to(VENDOR).as_posix())
    if held:
        raise SystemExit(
            f"\n  in use, so nothing was changed: {', '.join(held[:5])}\n"
            "  Stop every Sift running from this folder, then fetch again.\n"
        )


def unpack(archive: Path, mapping: dict[str, str]) -> None:
    """Pull named members out of the archive. Patterns are matched, never trusted paths.

    A destination ending in `/` takes EVERY match into that directory, under each member's own
    filename: the shared ffmpeg build is one executable and eight libraries whose names carry
    their soversion (`avcodec-61.dll`), which would otherwise have to be in the manifest.

    Every other destination is exactly one file and stays ambiguous-is-an-error, which is what keeps
    a single-file pattern from quietly picking the first of several.
    """
    bundle, names, open_member = _members(archive)
    with bundle:
        for dest_rel, pattern in mapping.items():
            hits = [n for n in names if fnmatch.fnmatch(n, pattern)]
            if not hits:
                raise SystemExit(f"\n  {archive.name} contains nothing matching {pattern!r}\n")
            many = dest_rel.endswith("/")
            if len(hits) > 1 and not many:
                raise SystemExit(f"\n  {pattern!r} is ambiguous in {archive.name}: {hits[:5]}\n")
            for hit in sorted(hits):
                # `PurePosixPath(...).name` and never the member's path: an archive can name a member
                # `../../anything`, and joining that onto the destination writes outside it.
                dest = VENDOR / dest_rel / PurePosixPath(hit).name if many else VENDOR / dest_rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open_member(hit) as src, dest.open("wb") as fh:
                    shutil.copyfileobj(src, fh)
                print(f"    -> {dest.relative_to(VENDOR).as_posix()}")


def unpack_tree(archive: Path, mapping: dict[str, str]) -> None:
    """Pull a whole FOLDER out of the archive, keeping the layout under it.

    For a program that is a folder rather than a file: yt-dlp's one-folder build, whose
    `yt-dlp.exe` loads everything it needs from an `_internal` folder beside it, by relative path.
    `unpack` above flattens every member to its own filename on purpose; here the layout IS the
    program, so it is kept, and so every part of every member's path is checked before anything
    is written. An archive can name a member `_internal/../../anything`, and a joined path that
    leaves the destination is exactly the write this refuses.

    The destination is emptied first. A folder left from the previous pin would otherwise keep
    every file the new release dropped, and the program would load a mixture of two releases.
    """
    bundle, names, open_member = _members(archive)
    with bundle:
        for dest_rel, prefix in mapping.items():
            if not dest_rel.endswith("/") or not prefix.endswith("/"):
                raise SystemExit(
                    f"\n  extract_tree entries are folders: {dest_rel!r} <- {prefix!r}\n"
                )
            hits = [n for n in names if n.startswith(prefix) and not n.endswith("/")]
            if not hits:
                raise SystemExit(f"\n  {archive.name} contains no folder {prefix!r}\n")
            # Every path is checked BEFORE anything is written or removed, so a refused archive
            # leaves the folder exactly as the last good fetch left it.
            laid_out: list[tuple[str, tuple[str, ...]]] = []
            for hit in sorted(hits):
                parts = PurePosixPath(hit[len(prefix) :]).parts
                if not parts or any(p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
                    raise SystemExit(f"\n  {archive.name} member {hit!r} is not a plain path\n")
                laid_out.append((hit, parts))
            root = VENDOR / dest_rel
            if root.exists():
                shutil.rmtree(root)
            for hit, parts in laid_out:
                dest = root.joinpath(*parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open_member(hit) as src, dest.open("wb") as fh:
                    shutil.copyfileobj(src, fh)
            print(f"    -> {root.relative_to(VENDOR).as_posix()}/  ({len(hits)} files)")


def check_ytdlp_finds_its_partners(ffmpeg_version: str | None) -> None:
    """Ask the vendored yt-dlp which JavaScript engine and which ffmpeg it can see.

    yt-dlp on Windows finds `qjs.exe` by it sitting in the same folder, a rule of yt-dlp's, not of
    Sift's, and the download command's `--js-runtimes quickjs` depends on it. A rename, a layout
    change or a new yt-dlp that looks somewhere else would not fail anything: yt-dlp carries on
    without an engine and says only "JS runtimes: none" in a debug line nobody reads. So the fetch
    reads it. With no address given yt-dlp prints its debug header and stops with a usage error,
    which is why the exit code is not the answer here and the header is.

    The ffmpeg it finds matters the same way and fails more quietly still. yt-dlp joins a site's
    separate picture and sound with ffmpeg; with none, it picks the best format that needs no
    joining, which on YouTube is 360p: a download that works and is the wrong file. Nothing tells
    yt-dlp where Sift's ffmpeg is: it finds it by the same same-folder rule, ahead of anything on
    PATH, even on a machine with a different ffmpeg on PATH. So the version it reports has to be the
    one the manifest pins.
    """
    exe = VENDOR / "bin" / "yt-dlp.exe"
    if sys.platform != "win32" or not exe.exists():
        return
    said = subprocess.run(
        [str(exe), "-v", "--js-runtimes", JS_RUNTIME], capture_output=True, text=True, timeout=120
    )
    header = said.stdout + said.stderr
    engine = next(
        (
            line.split("JS runtimes:", 1)[1].strip()
            for line in header.splitlines()
            if "JS runtimes:" in line
        ),
        "",
    )
    if not engine or engine == "none":
        raise SystemExit(
            f"\n  The vendored yt-dlp cannot see the vendored JavaScript engine (it said {engine or 'nothing'!r}).\n"
            "  qjs.exe has to sit beside yt-dlp.exe under exactly that name. Do not ship it like this.\n"
        )
    tools = next((line.strip() for line in header.splitlines() if "exe versions:" in line), "")
    if ffmpeg_version and f"ffmpeg {ffmpeg_version}" not in tools:
        raise SystemExit(
            f"\n  The vendored yt-dlp is not using the vendored ffmpeg {ffmpeg_version}"
            f" (it said {tools or 'nothing'!r}).\n"
            "  It joins picture and sound with whatever it finds, and with none it quietly picks a\n"
            "  lower quality. ffmpeg.exe has to sit beside yt-dlp.exe. Do not ship it like this.\n"
        )
    print(f"    yt-dlp sees  {engine}" + (f"  ({tools.split('] ', 1)[-1]})" if tools else ""))


def check_ffmpeg_configuration(required: list[str]) -> None:
    """A version string is not a capability. Ask the binary what it was built with.

    An ffmpeg without libx264 is not a smaller ffmpeg, it is one that cannot produce Sift's
    transcode ladder at all, and the failure would land on a user's machine, per clip, not here.
    """
    exe = VENDOR / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if not exe.exists():
        raise SystemExit(f"\n  {exe} is missing.\n")
    out = subprocess.run(
        [str(exe), "-hide_banner", "-version"], capture_output=True, text=True, timeout=60
    ).stdout
    missing = [flag for flag in required if flag not in out]
    if missing:
        raise SystemExit(
            f"\n  The vendored ffmpeg was NOT built with: {', '.join(missing)}\n"
            "  This build cannot do what Sift needs. Replace it; do not work around it.\n"
        )
    first = out.splitlines()[0] if out else "?"
    print(f"    configuration OK  ({first})")


def library_dlls(names: list[str]) -> list[str]:
    """The DLLs a wheel carries, by file name, from the list of its members."""
    return sorted(PurePosixPath(name).name for name in names if name.lower().endswith(".dll"))


def _listed(entry: dict[str, object], key: str) -> list[str]:
    """A list of names from a manifest entry, lower-cased, and empty for anything that is not one."""
    listed = entry.get(key)
    return [str(one).lower() for one in listed] if isinstance(listed, list) else []


def refused_in(dlls: list[str], wheel: dict[str, object]) -> list[str]:
    """What a wheel carries that its manifest entry refuses, and what it should carry and does not.

    Matched on the start of the DLL's name, because the packer that puts a library into a wheel
    adds a hash to the name (`libx265-217-8a7f....dll`), and that part changes with every build.
    """
    refuses = _listed(wheel, "refuses")
    carries = _listed(wheel, "carries")
    wrong = [dll for dll in dlls if any(dll.lower().startswith(one) for one in refuses)]
    wrong += [
        f"no {one} library"
        for one in carries
        if not any(dll.lower().startswith(f"{one}-") or dll.lower() == f"{one}.dll" for dll in dlls)
    ]
    return wrong


def run_recipe(recipe: Path, env: dict[str, str]) -> None:
    """Build something this repository builds, by its recipe, and stop the fetch if it fails.

    What the recipe makes is not trusted for having been made here: the caller reads it exactly as
    it reads a file that was already in vendor/, so a recipe that makes something else is refused.
    """
    shown = recipe.relative_to(ROOT).as_posix()
    if recipe.suffix.lower() == ".bat":
        if sys.platform != "win32":
            raise SystemExit(f"\n  {shown} builds on Windows only.\n")
        argv = ["cmd.exe", "/d", "/c", str(recipe)]
    else:
        argv = [sys.executable, str(recipe)]
    print(f"    building with {shown}")
    done = subprocess.run(
        argv, cwd=ROOT, env={**os.environ, **env}, timeout=RECIPE_TIMEOUT_S, check=False
    )
    if done.returncode != 0:
        raise SystemExit(f"\n  {shown} failed with exit {done.returncode}, so nothing was built.\n")


def check_wheel(
    wheel: dict[str, object],
    *,
    verify_only: bool,
    build_missing: bool = False,
    by_contents: bool = False,
) -> None:
    """A wheel the release installs in place of the published one: there, pinned, and whole.

    Built here by its recipe rather than downloaded, so there is nothing to fetch: a missing or
    different file stops the release and says how to make it, or with `build_missing` the recipe
    makes it. The DLLs in it are read as well as its digest, so a wheel rebuilt with the encoder
    left in cannot be pinned by mistake.

    `by_contents` holds the wheel to those DLLs alone. The digest holds only for the compiler the
    release machine has, and a CI runner's is another release of it: there the recipe is proved to
    build a wheel without the encoder, while the release goes on refusing any digest but the pinned
    one, here and again when it installs the wheel.
    """
    path = VENDOR / str(wheel["file"])
    recipe = ROOT / str(wheel["recipe"])
    seed = os.environ.get("VENDOR_SEED")
    seeded = Path(seed) / path.name if seed else None
    if not path.is_file() and seeded and seeded.is_file() and not verify_only:
        # A seed is a copy of the pinned wheel, so any other is passed over and the recipe builds.
        if digest(seeded) == wheel["sha256"]:
            print(f"    seeded  {path.name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(seeded, path)
        else:
            print(f"    seeded copy has the WRONG hash, not used  {path.name}")
    if not path.is_file() and build_missing and not verify_only:
        run_recipe(recipe, {"OUT": str(path.parent), "SOURCES": str(VENDOR / "bin" / "sources")})
    if not path.is_file():
        raise SystemExit(
            f"\n  {path} is missing.\n"
            f"  Build it with {recipe.relative_to(ROOT)} (it writes vendor/wheels), and if the\n"
            "  digest it prints differs from the manifest's, pin the new one in the same commit.\n"
        )
    got = digest(path)
    if got != wheel["sha256"] and by_contents:
        print(f"    built by this machine's compiler (sha256 {got}): held to what it carries")
    elif got != wheel["sha256"]:
        raise SystemExit(
            f"\n  SHA-256 MISMATCH for {path.name}\n"
            f"    expected  {wheel['sha256']}\n    got       {got}\n\n"
            "  A rebuild on another machine makes a different file. Check what it carries and pin\n"
            "  its digest in scripts/vendor_manifest.json, or restore the pinned file from backup.\n"
        )
    with zipfile.ZipFile(path) as bundle:
        wrong = refused_in(library_dlls(bundle.namelist()), wheel)
    if wrong:
        raise SystemExit(
            f"\n  {path.name} is not the wheel its entry describes: {', '.join(wrong)}\n"
        )
    print(f"    verified  {path.name}")
    if not verify_only:
        # The recipe and the files it reads ship beside the sources: the scripts that control
        # compilation are part of the source a library under the LGPL travels with.
        dest = VENDOR / "bin" / "sources" / recipe.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(recipe, dest)
        inputs = wheel.get("recipe_inputs")
        for one in inputs if isinstance(inputs, list) else []:
            shutil.copyfile(ROOT / str(one), dest.parent / Path(str(one)).name)
        print(f"    -> {dest.relative_to(VENDOR).as_posix()}")


def fetch_extras(extras: list[dict[str, str]], *, verify_only: bool) -> None:
    """The licence texts and source archives an entry ships beside what it describes."""
    for extra in extras:
        dest = VENDOR / extra["dest"]
        if not verify_only:
            fetch(extra["url"], dest, extra["sha256"])
        elif not dest.exists():
            print(f"    MISSING: {extra['dest']}")
        else:
            good = digest(dest) == extra["sha256"]
            print("    " + ("hash OK" if good else f"HASH MISMATCH ({dest.name})"))


def check_built(
    program: dict[str, object], *, verify_only: bool, build_missing: bool = False
) -> None:
    """A program built here from its pinned source: there, and the file its recipe makes.

    Built by the recipe rather than downloaded, so there is nothing to fetch for the program itself:
    a missing or different file stops the fetch and says how to make it, or with `build_missing`
    the recipe makes it. The recipe installs only a file with the pinned digest, so a different one
    here was put here some other way.
    """
    path = VENDOR / str(program["file"])
    build = f"python {program['recipe']}"
    if not path.is_file() and build_missing and not verify_only:
        run_recipe(ROOT / str(program["recipe"]), {})
    if not path.is_file():
        raise SystemExit(
            f"\n  {path} is missing.\n"
            f"  Build it with: {build}\n"
            f"  It needs git and Go {str(program['go']).removeprefix('go')}, and installs the file only "
            "when its digest is the one scripts/vendor_manifest.json pins.\n"
        )
    got = digest(path)
    if got != program["sha256"]:
        raise SystemExit(
            f"\n  SHA-256 MISMATCH for {path.name}\n"
            f"    expected  {program['sha256']}\n    got       {got}\n\n"
            "  It is not the program this repository builds. Do not ship it: build it again with\n"
            f"  {build}\n"
        )
    print(f"    verified  {path.name}")
    extras = program.get("extra_files", [])
    fetch_extras(extras if isinstance(extras, list) else [], verify_only=verify_only)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--verify-only",
        action="store_true",
        help="check what is already in vendor/ without downloading anything",
    )
    ap.add_argument(
        "--build-missing",
        action="store_true",
        help="build what this repository builds, by its recipe, when vendor/ does not hold it",
    )
    ap.add_argument(
        "--wheel-by-contents",
        action="store_true",
        help=(
            "hold a wheel to the libraries it carries and refuses, not to its digest: for a "
            "machine whose compiler is not the release machine's, such as CI"
        ),
    )
    args = ap.parse_args()
    if args.verify_only and args.build_missing:
        ap.error("--verify-only checks what is here and builds nothing")

    refuse_while_in_use(checking_only=args.verify_only)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for tool in manifest["tools"]:
        print(f"\n{tool['name']} {tool['version']}")
        archive = CACHE / tool["archive"]
        if args.verify_only:
            if not archive.exists():
                print(f"    MISSING from the cache: {archive.name}")
                continue
            got = digest(archive)
            print("    " + ("hash OK" if got == tool["sha256"] else f"HASH MISMATCH ({got})"))
        else:
            fetch(tool["url"], archive, tool["sha256"])
            unpack(archive, tool["extract"])
            if "extract_tree" in tool:
                unpack_tree(archive, tool["extract_tree"])
            for extra in tool.get("extra_files", []):
                dest = VENDOR / extra["dest"]
                fetch(extra["url"], dest, extra["sha256"])
        if tool["name"] == "ffmpeg" and not args.verify_only:
            check_ffmpeg_configuration(tool["must_have_config"])

    # The programs built here: checked, never fetched, and their licence texts beside them.
    for program in manifest.get("built", []):
        print(f"\n{program['name']} {program['version']} (built here)")
        check_built(program, verify_only=args.verify_only, build_missing=args.build_missing)

    # The libraries inside a wheel the release installs: nothing to unpack, only the source that
    # travels with them.
    for library in manifest.get("libraries", []):
        print(f"\n{library['name']} {library['version']} (source)")
        fetch_extras(library.get("extra_files", []), verify_only=args.verify_only)
    for wheel in manifest.get("wheels", []):
        print(f"\n{wheel['package']} {wheel['version']} (wheel)")
        check_wheel(
            wheel,
            verify_only=args.verify_only,
            build_missing=args.build_missing,
            by_contents=args.wheel_by_contents,
        )

    if not args.verify_only:
        pinned = {tool["name"]: tool["version"] for tool in manifest["tools"]}
        check_ytdlp_finds_its_partners(pinned.get("ffmpeg"))

    print(f"\nvendor/bin is ready at {VENDOR / 'bin'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
