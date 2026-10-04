# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tools the Windows release ships: pinned, verified before they are unpacked, and all there.

`scripts/fetch_vendor.py` is the only way a third-party program gets into the installer, and it runs
only when a release is built, so nothing else would notice a manifest entry that stopped being
pinned, an unpacker that started following a path out of its folder, or a release step that reuses
a vendor/bin from before a tool was added. These are the checks that notice, run without a network:
the downloads are stood in for by bytes this file makes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sift.slices.download.sources.tuning import JS_RUNTIME_NG_MIN_VERSION

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "scripts" / "vendor_manifest.json"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_{name}_under_test", REPO / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fetcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """fetch_vendor.py, writing into a folder of this test's rather than the repository's."""
    module = _load("fetch_vendor")
    monkeypatch.setattr(module, "VENDOR", tmp_path / "vendor")
    monkeypatch.setattr(module, "CACHE", tmp_path / "vendor" / "_cache")
    return module


def _tools() -> dict[str, dict[str, object]]:
    return {tool["name"]: tool for tool in json.loads(MANIFEST.read_text("utf-8"))["tools"]}


# --- the pin table ------------------------------------------------------------------------------


def test_every_file_the_release_fetches_is_pinned_by_a_digest_over_https() -> None:
    """One table, and nothing in it floats. A URL with no digest, or a digest that is not one, would
    let the fetch ship whatever the address happens to serve that day."""
    declared = json.loads(MANIFEST.read_text("utf-8"))
    others = [*declared.get("libraries", []), *declared.get("built", [])]
    for name, tool in [*_tools().items(), *((one["name"], one) for one in others)]:
        extras = tool.get("extra_files", [])
        assert isinstance(extras, list)
        # A library entry fetches only its source: the library itself arrives inside a wheel. A
        # program built here fetches only its licence texts.
        for entry in [tool, *extras] if "url" in tool else extras:
            assert isinstance(entry, dict)
            assert str(entry["url"]).startswith("https://"), f"{name}: {entry['url']}"
            assert _SHA256.match(str(entry["sha256"])), f"{name}: {entry['url']} has no digest"


def test_both_downloaders_and_their_engine_are_in_the_release() -> None:
    """A release that ships ffmpeg and nothing that downloads is refused."""
    tools = _tools()

    assert tools["yt-dlp"]["extract"] == {"bin/yt-dlp.exe": "yt-dlp.exe"}
    assert "bin/gallery-dl.exe" in tools["gallery-dl"]["extract"]  # type: ignore[operator]
    # Under exactly this name, in exactly this folder: that is how yt-dlp finds it on Windows.
    assert "bin/qjs.exe" in tools["quickjs-ng"]["extract"]  # type: ignore[operator]


def test_the_engine_is_new_enough_to_solve_a_challenge_in_seconds() -> None:
    pinned = tuple(int(part) for part in str(_tools()["quickjs-ng"]["version"]).split("."))
    floor = tuple(int(part) for part in JS_RUNTIME_NG_MIN_VERSION.split("."))

    assert pinned >= floor


def test_a_gpl_tool_ships_its_source_beside_it() -> None:
    """GPL-2.0 asks a binary to travel with its source or a written offer of it. The source is a
    fraction of a megabyte, so it travels, and a pin bump that drops it is refused here rather
    than shipped."""
    for name in ("gallery-dl", "yt-dlp"):
        listed = _tools()[name]["extra_files"]
        assert isinstance(listed, list)
        extras = [str(extra["dest"]) for extra in listed]
        assert any(dest.startswith("bin/sources/") for dest in extras), name
        assert any("LICENSE" in dest for dest in extras), name


def test_ffmpeg_ships_the_source_of_the_exact_commit_it_was_built_from() -> None:
    """The vendored ffmpeg is GPL-3.0 as built, and it is not the n7.1.5 release: it is sixteen
    commits past it, and says so in its version. So the source that ships is named for that version
    and fetched by that commit: the release tarball would be the source of a different program.
    The GPL encoders compiled into it and the recipe that names the rest ship beside it."""
    ffmpeg = _tools()["ffmpeg"]
    version = str(ffmpeg["version"])
    commit = version.rsplit("-g", 1)[1]
    listed = ffmpeg["extra_files"]
    assert isinstance(listed, list)
    by_dest = {str(extra["dest"]): str(extra["url"]) for extra in listed}

    source = f"bin/sources/ffmpeg-{version}.tar.gz"
    assert source in by_dest
    assert f"/{commit}" in by_dest[source]
    for other in ("x264-", "x265-", "FFmpeg-Builds-"):
        assert any(dest.startswith(f"bin/sources/{other}") for dest in by_dest), other


def test_the_release_refuses_a_gpl_tool_with_no_source_of_its_version() -> None:
    """A GPL tool declared with no source archive, or with the source of the version before a pin
    bump, is refused, read from the licence each entry states, so no list of names can miss one."""
    release = _load("release")
    tool: dict[str, object] = {"name": "anything", "version": "2.0", "licence": "GPL-2.0-only"}

    for shipped in ([], ["anything-LICENSE"], ["sources/anything-1.0.tar.gz"]):
        with pytest.raises(release.ReleaseFailed, match="no source archive"):
            release._check_a_gpl_tool_declares_its_source(tool, shipped)

    release._check_a_gpl_tool_declares_its_source(tool, ["sources/anything-2.0.tar.gz"])
    for licence in ("LGPL-2.1-or-later", "AGPL-3.0"):
        with pytest.raises(release.ReleaseFailed):
            release._check_a_gpl_tool_declares_its_source({**tool, "licence": licence}, [])
    release._check_a_gpl_tool_declares_its_source({**tool, "licence": "MIT"}, [])


# --- fetching -----------------------------------------------------------------------------------


def _serve(module: ModuleType, monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(payload))


def test_a_pruned_archive_is_taken_from_the_mirror_under_the_same_digest(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pinned address that answers 404 is asked for at the mirror by its cached name, and the
    digest decides: the pinned bytes land, other bytes do not, and a mirror without it stops."""
    held: dict[str, bytes] = {}
    asked: list[str] = []

    def answer(url: str, **_k: object) -> io.BytesIO:
        asked.append(url)
        if url not in held:
            raise fetcher.urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        return io.BytesIO(held[url])

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", answer)
    monkeypatch.delenv("SIFT_VENDOR_SEED", raising=False)
    pinned = "https://example.invalid/builds/tool.exe"
    dest = tmp_path / "vendor" / "_cache" / "tool-1.0.exe"
    wanted = fetcher.hashlib.sha256(b"the release").hexdigest()

    with pytest.raises(SystemExit, match=r"the mirror holds no tool-1\.0\.exe"):
        fetcher.fetch(pinned, dest, wanted)
    assert asked == [pinned, fetcher.MIRROR + "tool-1.0.exe"]
    assert not dest.exists()

    held[fetcher.MIRROR + "tool-1.0.exe"] = b"not the release"
    with pytest.raises(SystemExit, match="SHA-256 MISMATCH"):
        fetcher.fetch(pinned, dest, wanted)
    assert not dest.exists()

    held[fetcher.MIRROR + "tool-1.0.exe"] = b"the release"
    fetcher.fetch(pinned, dest, wanted)
    assert dest.read_bytes() == b"the release"
    assert fetcher.MIRROR.startswith("https://")


def test_a_seeded_archive_is_copied_and_verified_before_the_network_is_asked(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A machine with no cache of its own takes a verified copy from the seed folder, so a build
    upstream has pruned is still there for it; a seed with the wrong bytes is not trusted either."""

    def no_network(*_a: object, **_k: object) -> None:
        raise AssertionError("the network was asked")

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", no_network)
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "tool-1.0.exe").write_bytes(b"the release")
    monkeypatch.setenv("SIFT_VENDOR_SEED", str(seed))
    dest = tmp_path / "vendor" / "_cache" / "tool-1.0.exe"

    fetcher.fetch(
        "https://example.invalid/tool.exe", dest, fetcher.hashlib.sha256(b"the release").hexdigest()
    )
    assert dest.read_bytes() == b"the release"

    (seed / "other-1.0.exe").write_bytes(b"not the release")
    with pytest.raises((SystemExit, AssertionError)):
        fetcher.fetch(
            "https://example.invalid/other.exe",
            tmp_path / "vendor" / "_cache" / "other-1.0.exe",
            "0" * 64,
        )


def test_a_download_with_the_wrong_digest_is_refused_and_nothing_is_kept(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(fetcher, monkeypatch, b"not the release")
    dest = tmp_path / "vendor" / "_cache" / "tool-1.0.exe"

    with pytest.raises(SystemExit, match="MISMATCH"):
        fetcher.fetch("https://example.invalid/tool.exe", dest, "0" * 64)

    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []


def test_a_download_with_the_right_digest_is_kept(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"the release"
    _serve(fetcher, monkeypatch, payload)
    dest = tmp_path / "vendor" / "_cache" / "tool-1.0.exe"

    fetcher.fetch(
        "https://example.invalid/tool.exe", dest, fetcher.hashlib.sha256(payload).hexdigest()
    )

    assert dest.read_bytes() == payload


def test_a_bare_executable_is_laid_out_under_the_name_the_manifest_gives(
    fetcher: ModuleType, tmp_path: Path
) -> None:
    """gallery-dl and the engine are published as the executable alone, with no archive round it."""
    cached = tmp_path / "vendor" / "_cache" / "quickjs-ng-0.17.0-windows-x86_64.exe"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"MZ engine")

    fetcher.unpack(cached, {"bin/qjs.exe": "quickjs-ng-0.17.0-windows-x86_64.exe"})

    assert (tmp_path / "vendor" / "bin" / "qjs.exe").read_bytes() == b"MZ engine"


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return path


def test_a_program_folder_keeps_its_layout(fetcher: ModuleType, tmp_path: Path) -> None:
    """yt-dlp's one-folder build loads `_internal/...` by relative path, so the layout IS the program."""
    archive = _zip(
        tmp_path / "vendor" / "_cache" / "yt.zip",
        {
            "yt-dlp.exe": b"MZ",
            "_internal/python310.dll": b"dll",
            "_internal/yt_dlp_ejs/yt/solver/core.min.js": b"js",
        },
    )
    stale = tmp_path / "vendor" / "bin" / "_internal" / "from-the-last-pin.pyd"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")

    fetcher.unpack_tree(archive, {"bin/_internal/": "_internal/"})

    root = tmp_path / "vendor" / "bin" / "_internal"
    assert (root / "python310.dll").read_bytes() == b"dll"
    assert (root / "yt_dlp_ejs" / "yt" / "solver" / "core.min.js").read_bytes() == b"js"
    # A folder from the previous pin is replaced, not merged: a program loading half of each
    # release is a program nobody tested.
    assert not stale.exists()


@pytest.mark.parametrize(
    "member",
    ["_internal/../escaped.txt", "_internal/sub/../../escaped.txt", "_internal/C:evil.dll"],
)
def test_a_program_folder_member_that_leaves_the_folder_is_refused(
    fetcher: ModuleType, tmp_path: Path, member: str
) -> None:
    archive = _zip(tmp_path / "vendor" / "_cache" / "yt.zip", {member: b"x", "_internal/ok": b"ok"})
    kept = tmp_path / "vendor" / "bin" / "_internal" / "from-the-last-good-fetch.pyd"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"good")

    with pytest.raises(SystemExit, match="not a plain path"):
        fetcher.unpack_tree(archive, {"bin/_internal/": "_internal/"})

    # Refused before anything was touched: the last good fetch is still whole.
    assert kept.read_bytes() == b"good"
    assert not (kept.parent / "ok").exists()
    assert not (tmp_path / "vendor" / "escaped.txt").exists()
    assert not (tmp_path / "vendor" / "bin" / "escaped.txt").exists()


# --- the release reuses what is there only if it is all there -----------------------------------


def test_the_release_knows_every_vendored_program_from_the_manifest() -> None:
    executables, folders, notices = _load("release")._vendored_files()

    assert {"yt-dlp.exe", "gallery-dl.exe", "qjs.exe", "ffmpeg.exe", "wireproxy.exe"} <= executables
    assert folders == {"_internal"}
    # The texts and the source a licence asks to travel with the program are checked for too.
    assert {"gallery-dl-LICENSE", "qjs-LICENSE", "yt-dlp-LICENSE"} <= notices
    assert {
        "wireproxy-LICENSE",
        "wireproxy-gvisor-LICENSE",
        "wireproxy-wireguard-go-LICENSE",
    } <= notices
    assert any(rel.startswith("sources/gallery_dl-") for rel in notices)
    assert f"sources/ffmpeg-{_tools()['ffmpeg']['version']}.tar.gz" in notices


def test_skip_vendor_refuses_a_vendor_folder_from_before_the_downloaders(tmp_path: Path) -> None:
    """`--skip-vendor` looks for every tool, not only ffmpeg.exe, so a vendor/bin fetched before
    the downloaders were added cannot build an installer without them."""
    release = _load("release")
    for name in ("ffmpeg.exe", "ffprobe.exe", "webpinfo.exe", "anim_dump.exe", "wireproxy.exe"):
        (tmp_path / name).write_bytes(b"MZ")

    with pytest.raises(release.ReleaseFailed) as refused:
        release._check_the_vendored_tools_are_in(tmp_path, "--skip-vendor")

    said = str(refused.value)
    for missing in ("yt-dlp.exe", "gallery-dl.exe", "qjs.exe", "_internal/", "sources/"):
        assert missing in said, missing


# --- the fetched yt-dlp can see its partners ----------------------------------------------------


def _ytdlp_says(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, header: str
) -> None:
    """A vendored yt-dlp that prints `header` under -v, on a machine that is Windows."""
    exe = tmp_path / "vendor" / "bin" / "yt-dlp.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(fetcher, "sys", type("Windows", (), {"platform": "win32"}))
    monkeypatch.setattr(
        fetcher.subprocess,
        "run",
        lambda argv, **_k: fetcher.subprocess.CompletedProcess(argv, 2, "", header),
    )


_FFMPEG = "n7.1.5-16-g9a4bb2c579"
_TOOLS = f"[debug] exe versions: ffmpeg {_FFMPEG}-20260816 (setts), ffprobe {_FFMPEG}-20260816\n"


def test_a_ytdlp_that_sees_both_partners_passes(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ytdlp_says(fetcher, monkeypatch, tmp_path, _TOOLS + "[debug] JS runtimes: quickjs-ng-0.17.0\n")

    fetcher.check_ytdlp_finds_its_partners(_FFMPEG)


def test_a_ytdlp_that_sees_no_engine_fails_the_fetch(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """yt-dlp carries on without one and says so only in a debug line, so the fetch reads it."""
    _ytdlp_says(fetcher, monkeypatch, tmp_path, _TOOLS + "[debug] JS runtimes: none\n")

    with pytest.raises(SystemExit, match="cannot see the vendored JavaScript engine"):
        fetcher.check_ytdlp_finds_its_partners(_FFMPEG)


def test_a_ytdlp_joining_with_some_other_ffmpeg_fails_the_fetch(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ffmpeg on PATH is not Sift's, and with none at all yt-dlp quietly picks a lower quality."""
    other = "[debug] exe versions: ffmpeg 7.1.1-essentials_build-www.gyan.dev (setts)\n"
    _ytdlp_says(fetcher, monkeypatch, tmp_path, other + "[debug] JS runtimes: quickjs-ng-0.17.0\n")

    with pytest.raises(SystemExit, match="not using the vendored ffmpeg"):
        fetcher.check_ytdlp_finds_its_partners(_FFMPEG)


# --- the HEIF reader's libraries and the wheel built without an encoder ----------------------------

_PUBLISHED_DLLS = [
    "libde265-0-863aced21c291386b51bbbd6c57331e8.dll",
    "libgcc_s_seh-1-7579db3ccd9543f896752ed45d8cb013.dll",
    "libheif-30fc1b3ceee9088934615cbf4948eba3.dll",
    "libstdc++-6-a168feb806be6a6b9920422b91e14e6f.dll",
    "libwinpthread-1-cd4f4bcf906810bdc5341685d1696fa6.dll",
    "libx265-217-8a7f7f4ebe0ffaa73ce4bb306c2d18d6.dll",
]
_REBUILT_DLLS = [
    "heif-94f624a98fefd06213541a34f6101a77.dll",
    "libde265-50110abdd9e009973f0058ff36f1ccd2.dll",
    "msvcp140-20076bc0e3fcb1842cab77dfe8ef816b.dll",
]


def _manifest() -> dict[str, object]:
    loaded = json.loads(MANIFEST.read_text("utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _heif_wheel() -> dict[str, object]:
    wheels = _manifest()["wheels"]
    assert isinstance(wheels, list)
    (wheel,) = [one for one in wheels if one["package"] == "pillow-heif"]
    assert isinstance(wheel, dict)
    return wheel


def test_the_heif_libraries_ship_the_source_of_the_versions_the_wheel_carries() -> None:
    """libheif and libde265 are LGPL-3.0 and travel inside the pillow-heif wheel, so the release
    archive of exactly the version each DLL reports ships under bin/sources, pinned like a tool."""
    libraries = _manifest()["libraries"]
    assert isinstance(libraries, list)
    by_name = {str(one["name"]): one for one in libraries}
    assert {name: str(one["version"]) for name, one in by_name.items()} == {
        "libheif": "1.23.4",
        "libde265": "1.1.3",
    }
    for name, library in by_name.items():
        assert str(library["licence"]).startswith("LGPL-3.0"), name
        (source,) = library["extra_files"]
        assert source["dest"] == f"bin/sources/{name}-{library['version']}.tar.gz"
        assert str(source["url"]).startswith("https://github.com/strukturag/"), name
        assert f"/v{library['version']}/" in str(source["url"]), name
        assert _SHA256.match(str(source["sha256"])), name


def test_the_heif_wheel_is_pinned_with_its_recipe_and_refuses_the_encoder() -> None:
    wheel = _heif_wheel()

    assert _SHA256.match(str(wheel["sha256"]))
    assert str(wheel["file"]).startswith("wheels/pillow_heif-1.8.0-")
    assert (REPO / str(wheel["recipe"])).is_file()
    assert wheel["refuses"] == ["libx265"]
    assert wheel["carries"] == ["heif", "libde265"]
    # The recipe builds exactly what the manifest ships the source of, with the encoder off.
    recipe = (REPO / str(wheel["recipe"])).read_text("utf-8")
    for archive in ("libde265-1.1.3.tar.gz", "libheif-1.23.4.tar.gz"):
        assert archive in recipe
    assert "-DWITH_X265=OFF" in recipe and "-DWITH_X265=ON" not in recipe


def test_the_release_refuses_a_pack_without_the_heif_sources_or_the_recipe(tmp_path: Path) -> None:
    release = _load("release")
    _executables, _folders, notices = release._vendored_files()

    wanted = {"sources/libheif-1.23.4.tar.gz", "sources/libde265-1.1.3.tar.gz"}
    assert wanted | {"sources/build_pillow_heif.bat"} <= notices
    # A vendor folder holding every program but not these is refused, naming them.
    with pytest.raises(release.ReleaseFailed) as refused:
        release._check_the_vendored_tools_are_in(tmp_path, "the packed application")
    for missing in (*wanted, "sources/build_pillow_heif.bat"):
        assert missing in str(refused.value), missing
    # And an LGPL library declared with no source of its version is refused outright.
    libheif = {"name": "libheif", "version": "1.23.4", "licence": "LGPL-3.0-or-later"}
    with pytest.raises(release.ReleaseFailed, match="no source archive"):
        release._check_a_gpl_tool_declares_its_source(libheif, ["sources/libheif-1.23.3.tar.gz"])


def _installed(packages: Path, dlls: list[str]) -> Path:
    info = packages / "pillow_heif-1.8.0.dist-info"
    info.mkdir(parents=True)
    lines = ["pillow_heif/__init__.py,sha256=x,1", *(f"{dll},sha256=x,1" for dll in dlls)]
    (info / "RECORD").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return packages


def test_the_release_refuses_a_runtime_carrying_the_published_heif_wheel(tmp_path: Path) -> None:
    """The published wheel reads a photograph exactly as the rebuilt one does, so only its list of
    DLLs can tell which shipped: one carrying libx265 is refused."""
    release = _load("release")
    packages = _installed(tmp_path / "site-packages", _PUBLISHED_DLLS)

    with pytest.raises(release.ReleaseFailed, match="builds: libx265-217"):
        release.prove_the_runtime_carries_the_pinned_wheels(packages, [_heif_wheel()])
    # Refused for the encoder itself, not only for the published wheel's other names.
    assert release.wheel_dlls_refused(_PUBLISHED_DLLS, {"refuses": ["libx265"]}) == [
        "libx265-217-8a7f7f4ebe0ffaa73ce4bb306c2d18d6.dll"
    ]


def test_the_release_takes_a_runtime_carrying_the_rebuilt_heif_wheel(tmp_path: Path) -> None:
    release = _load("release")
    packages = _installed(tmp_path / "site-packages", _REBUILT_DLLS)

    release.prove_the_runtime_carries_the_pinned_wheels(packages, [_heif_wheel()])
    # One that lost its decoder, or was never installed, is refused as well.
    broken = _installed(tmp_path / "other", _REBUILT_DLLS[:1])
    with pytest.raises(release.ReleaseFailed, match="no libde265 library"):
        release.prove_the_runtime_carries_the_pinned_wheels(broken, [_heif_wheel()])
    with pytest.raises(release.ReleaseFailed, match="no pillow-heif installed"):
        release.prove_the_runtime_carries_the_pinned_wheels(tmp_path / "none", [_heif_wheel()])


def test_the_fetch_refuses_a_heif_wheel_that_carries_the_encoder(
    fetcher: ModuleType, tmp_path: Path
) -> None:
    """The fetch reads the wheel's DLLs as well as its digest, so a rebuild with the encoder left
    in cannot be pinned by mistake."""
    members = {f"pillow_heif-1.8.0.data/platlib/{dll}": b"MZ" for dll in _PUBLISHED_DLLS}
    built = _zip(tmp_path / "vendor" / "wheels" / "pillow_heif.whl", members)
    wheel = {**_heif_wheel(), "file": "wheels/pillow_heif.whl", "sha256": fetcher.digest(built)}

    with pytest.raises(SystemExit, match="libx265-217"):
        fetcher.check_wheel(wheel, verify_only=True)

    members = {f"pillow_heif-1.8.0.data/platlib/{dll}": b"MZ" for dll in _REBUILT_DLLS}
    built = _zip(tmp_path / "vendor" / "wheels" / "pillow_heif.whl", members)
    fetcher.check_wheel({**wheel, "sha256": fetcher.digest(built)}, verify_only=False)
    assert (tmp_path / "vendor" / "bin" / "sources" / "build_pillow_heif.bat").is_file()
    with pytest.raises(SystemExit, match="SHA-256 MISMATCH"):
        fetcher.check_wheel({**wheel, "sha256": "0" * 64}, verify_only=True)


class _Recipe:
    """A recipe stood in for: writes the file it is told to and records what it was given."""

    def __init__(self, dest: Path, members: dict[str, bytes] | None, program: bytes = b"") -> None:
        self.dest = dest
        self.members = members
        self.program = program
        self.ran: list[tuple[Path, dict[str, str]]] = []

    def __call__(self, recipe: Path, env: dict[str, str]) -> None:
        self.ran.append((recipe, env))
        if self.members is None:
            self.dest.parent.mkdir(parents=True, exist_ok=True)
            self.dest.write_bytes(self.program)
        else:
            _zip(self.dest, self.members)


def test_a_seeded_wheel_is_read_as_any_other_and_the_recipe_is_not_run(
    fetcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wheel built before, taken from the seed folder, is held to its libraries or its digest
    exactly as one the recipe just made; a seed carrying the encoder is refused the same way."""
    wheel = {**_heif_wheel(), "file": "wheels/pillow_heif.whl"}
    dest = tmp_path / "vendor" / "wheels" / "pillow_heif.whl"
    seed = tmp_path / "seed"
    seed.mkdir()
    _zip(
        seed / "pillow_heif.whl",
        {f"pillow_heif-1.8.0.data/platlib/{dll}": b"MZ" for dll in _REBUILT_DLLS},
    )
    monkeypatch.setenv("SIFT_VENDOR_SEED", str(seed))
    recipe = _Recipe(dest, None)
    monkeypatch.setattr(fetcher, "run_recipe", recipe)

    fetcher.check_wheel(wheel, verify_only=False, build_missing=True, by_contents=True)
    assert dest.is_file() and recipe.ran == [], "the recipe ran although the seed had the wheel"

    dest.unlink()
    _zip(
        seed / "pillow_heif.whl",
        {f"pillow_heif-1.8.0.data/platlib/{dll}": b"MZ" for dll in _PUBLISHED_DLLS},
    )
    with pytest.raises(SystemExit, match="libx265-217"):
        fetcher.check_wheel(wheel, verify_only=False, build_missing=True, by_contents=True)


def test_the_fetch_builds_a_missing_wheel_and_reads_it_as_any_other(
    fetcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wheel the cache lacks is made by its recipe and then read like one that was there: by
    its digest for the release, by its libraries alone where the compiler is not the release
    machine's, and refused for the encoder either way."""
    wheel = {**_heif_wheel(), "file": "wheels/pillow_heif.whl"}
    dest = tmp_path / "vendor" / "wheels" / "pillow_heif.whl"
    clean = {f"pillow_heif-1.8.0.data/platlib/{dll}": b"MZ" for dll in _REBUILT_DLLS}
    recipe = _Recipe(dest, clean)
    monkeypatch.setattr(fetcher, "run_recipe", recipe)

    with pytest.raises(SystemExit, match="SHA-256 MISMATCH"):
        fetcher.check_wheel(wheel, verify_only=False, build_missing=True)
    ((ran, env),) = recipe.ran
    assert ran == REPO / str(wheel["recipe"])
    assert env == {
        "OUT": str(dest.parent),
        "SOURCES": str(tmp_path / "vendor" / "bin" / "sources"),
    }

    fetcher.check_wheel(wheel, verify_only=False, build_missing=True, by_contents=True)
    assert len(recipe.ran) == 1, "a wheel already here was built again"

    dest.unlink()
    monkeypatch.setattr(
        fetcher,
        "run_recipe",
        _Recipe(dest, {f"pillow_heif-1.8.0.data/platlib/{dll}": b"MZ" for dll in _PUBLISHED_DLLS}),
    )
    with pytest.raises(SystemExit, match="libx265-217"):
        fetcher.check_wheel(wheel, verify_only=False, build_missing=True, by_contents=True)


def test_the_fetch_builds_a_missing_tunnel_client_and_takes_only_the_pinned_one(
    fetcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = {**_tunnel_entry(), "extra_files": []}
    program = tmp_path / "vendor" / "bin" / "wireproxy.exe"
    recipe = _Recipe(program, None, b"MZ some other build")
    monkeypatch.setattr(fetcher, "run_recipe", recipe)

    with pytest.raises(SystemExit, match="SHA-256 MISMATCH"):
        fetcher.check_built(entry, verify_only=False, build_missing=True)
    assert [ran for ran, _env in recipe.ran] == [REPO / str(entry["recipe"])]

    program.unlink()
    recipe.program = b"MZ the pinned build"
    pinned = {**entry, "sha256": _sha256_of(recipe.program)}
    fetcher.check_built(pinned, verify_only=False, build_missing=True)
    assert len(recipe.ran) == 2
    fetcher.check_built(pinned, verify_only=False, build_missing=True)
    assert len(recipe.ran) == 2, "a program already here was built again"


def test_checking_what_is_here_builds_nothing(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = _Recipe(Path("unused"), None)
    monkeypatch.setattr(fetcher, "run_recipe", recipe)

    with pytest.raises(SystemExit, match="is missing"):
        fetcher.check_built(_tunnel_entry(), verify_only=True, build_missing=True)
    with pytest.raises(SystemExit, match="is missing"):
        fetcher.check_wheel(_heif_wheel(), verify_only=True, build_missing=True)
    with pytest.raises(SystemExit, match="is missing"):
        fetcher.check_wheel(_heif_wheel(), verify_only=False)
    assert recipe.ran == []


def test_a_recipe_runs_by_its_kind_and_a_failed_one_stops_the_fetch(
    fetcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []

    def answer(argv: list[str], **_kwargs: object) -> Any:
        ran.append(argv)
        return dataclasses.make_dataclass("Done", [("returncode", int)])(3 if ran[1:] else 0)

    monkeypatch.setattr(fetcher.subprocess, "run", answer)
    monkeypatch.setattr(fetcher.sys, "platform", "win32")
    fetcher.run_recipe(REPO / "scripts" / "vendor_build" / "wireproxy" / "build.py", {})
    with pytest.raises(SystemExit, match=r"build_pillow_heif\.bat failed with exit 3"):
        fetcher.run_recipe(REPO / "scripts" / "build_pillow_heif.bat", {})
    assert ran[0][1:] == [str(REPO / "scripts" / "vendor_build" / "wireproxy" / "build.py")]
    assert ran[1][:3] == ["cmd.exe", "/d", "/c"]

    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit, match="builds on Windows only"):
        fetcher.run_recipe(REPO / "scripts" / "build_pillow_heif.bat", {})


def test_the_heif_recipe_makes_the_same_wheel_twice() -> None:
    """The pinned digest can be made again only if nothing of the moment of the build reaches the
    file: the time the compiler and linker stamp, the date on each member of the wheel, the paths
    the repair step records, and build requirements resolved on the day."""
    recipe = (REPO / "scripts" / "build_pillow_heif.bat").read_text("utf-8")
    assert re.search(r'^set "_CL_=/Brepro"\r?$', recipe, re.M)
    assert re.search(r'^set "_LINK_=/Brepro"\r?$', recipe, re.M)
    assert re.search(r'^set "SOURCE_DATE_EPOCH=\d+"\r?$', recipe, re.M)
    resolved = re.search(r'^set "RESOLVED_AS_OF=(\S+)"\r?$', recipe, re.M)
    assert resolved is not None
    for tool in ("uv build", "uvx"):
        (line,) = [one for one in recipe.splitlines() if one.lstrip().startswith(tool)]
        assert "--exclude-newer %RESOLVED_AS_OF%" in line, line


def test_the_suite_builds_what_its_caches_lack() -> None:
    """The suite's preparation fetches with the recipes on hand, keeps the wheel it builds in a
    cache keyed on the wheel's recipe, and keys the rest on the manifest and the client's recipe."""
    action = (REPO / ".github" / "actions" / "prepare-suite" / "action.yml").read_text("utf-8")
    fetches = [line.strip() for line in action.splitlines() if "fetch_vendor.py" in line]
    assert fetches == ["uv run python scripts/fetch_vendor.py --build-missing --wheel-by-contents"]
    assert "hashFiles('scripts/build_pillow_heif.bat')" in action
    assert "hashFiles('scripts/vendor_manifest.json', 'scripts/vendor_build/**')" in action
    assert "!vendor/wheels" in action


# --- the tunnel client, built here from its pinned source --------------------------------------

_BUILD = REPO / "scripts" / "vendor_build" / "wireproxy" / "build.py"


def _tunnel_entry() -> dict[str, Any]:
    built = _manifest()["built"]
    assert isinstance(built, list)
    (entry,) = [one for one in built if one["name"] == "wireproxy"]
    assert isinstance(entry, dict)
    return entry


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_wireproxy_build_under_test", _BUILD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up while it is made.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_the_tunnel_client_is_built_from_the_pinned_patches_in_this_tree() -> None:
    """The patches are pinned byte for byte: a patch that differs from its digest is a program
    nobody reviewed. The module versions the manifest names are the ones the first patch moves to,
    and the licence of every project whose files the second patch changes ships beside it."""
    entry = _tunnel_entry()
    assert REPO / str(entry["recipe"]) == _BUILD
    assert _BUILD.is_file()
    assert _SHA256.match(str(entry["sha256"]))
    assert entry["file"] == "bin/wireproxy.exe"
    patches = entry["patches"]
    assert len(patches) == 2
    for patch in patches:
        path = REPO / str(patch["file"])
        assert path.parent == _BUILD.parent
        assert _sha256_of(path.read_bytes()) == patch["sha256"], path.name
    gomod = (REPO / str(patches[0]["file"])).read_text("utf-8")
    moved = {name: version for name, version in entry["modules"].items() if "wireguard" not in name}
    assert set(moved) == {
        "gvisor.dev/gvisor",
        "golang.org/x/time",
        "golang.org/x/exp",
        "golang.org/x/crypto",
        "golang.org/x/net",
        "golang.org/x/sys",
    }
    for module, version in moved.items():
        assert re.search(
            rf"^\+\t{re.escape(module)} {re.escape(version)}( // indirect)?$", gomod, re.M
        ), module
    assert {str(extra["dest"]) for extra in entry["extra_files"]} == {
        "bin/wireproxy-LICENSE",
        "bin/wireproxy-gvisor-LICENSE",
        "bin/wireproxy-wireguard-go-LICENSE",
    }
    assert str(entry["licence"]).startswith("ISC")
    # CI builds it with the same release, named a second time in the action that installs Go.
    action = (REPO / ".github" / "actions" / "prepare-suite" / "action.yml").read_text("utf-8")
    assert f'go-version: "{str(entry["go"]).removeprefix("go")}"' in action


class _Steps:
    """The build's commands, answered without git, Go or a network, and recorded in order."""

    def __init__(self, *, go: str, commit: str, program: bytes) -> None:
        self.go = go
        self.commit = commit
        self.program = program
        self.ran: list[list[str]] = []

    def __call__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> str:
        self.ran.append(list(argv))
        if argv[1:] == ["version"]:
            return f"go version {self.go} windows/amd64\n"
        if "rev-parse" in argv:
            return self.commit + "\n"
        if "clone" in argv:
            Path(argv[-1]).mkdir(parents=True)
        if argv[1:2] == ["build"]:
            Path(argv[argv.index("-o") + 1]).write_bytes(self.program)
        return ""

    def said(self, *words: str) -> bool:
        return any(all(word in argv for word in words) for argv in self.ran)

    def steps(self) -> list[str]:
        """Each command by the word that names it."""
        names = ("clone", "rev-parse", "apply", "mod", "build", "version")
        return [next(word for word in names if word in argv) for argv in self.ran]


def _pin(builder: ModuleType, program: bytes) -> Any:
    """The real pin, with the digest of bytes a test's build makes."""
    return dataclasses.replace(builder.read_pin(), sha256=_sha256_of(program))


def test_the_tunnel_build_refuses_a_wrong_go_release(tmp_path: Path) -> None:
    """The digest holds for one Go release: another one is refused before anything is cloned."""
    builder = _builder()
    pin = _pin(builder, b"MZ built")
    steps = _Steps(go="go1.26.5", commit=pin.commit, program=b"MZ built")

    with pytest.raises(builder.BuildRefused, match=r"go1\.26\.5.*go1\.27\.0"):
        builder.build(pin, "go.exe", tmp_path, steps, {})

    assert steps.ran == [["go.exe", "version"]]


def test_the_tunnel_build_refuses_a_tag_at_another_commit(tmp_path: Path) -> None:
    builder = _builder()
    pin = _pin(builder, b"MZ built")
    steps = _Steps(go=pin.go, commit="0" * 40, program=b"MZ built")

    with pytest.raises(builder.BuildRefused, match="is commit 0000"):
        builder.build(pin, "go.exe", tmp_path, steps, {})

    assert steps.steps() == ["version", "clone", "rev-parse"]


def test_the_tunnel_build_installs_nothing_whose_digest_is_not_the_pinned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole run, as `main` takes it, against the real pin: a build that makes any other
    file is refused and vendor/bin is left as it was."""
    builder = _builder()
    real = builder.read_pin()
    vendor = tmp_path / "vendor"
    monkeypatch.setattr(builder, "VENDOR", vendor)
    kept = vendor / "bin" / "wireproxy.exe"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"the program installed before")
    go = tmp_path / "go.exe"
    go.write_bytes(b"")
    steps = _Steps(go=real.go, commit=real.commit, program=b"MZ some other build")

    said = builder.main(
        ["--go", str(go), "--work", str(tmp_path / "work")], runner=steps, machine={}
    )

    assert said == 1
    assert "the build made a file with sha256" in capsys.readouterr().err
    assert steps.steps()[-1] == "build"
    assert kept.read_bytes() == b"the program installed before"
    assert [path.name for path in kept.parent.iterdir()] == ["wireproxy.exe"]


def test_the_tunnel_build_installs_the_pinned_program(tmp_path: Path) -> None:
    builder = _builder()
    pin = _pin(builder, b"MZ built")
    steps = _Steps(go=pin.go, commit=pin.commit, program=b"MZ built")

    dest = builder.install(builder.build(pin, "go.exe", tmp_path, steps, {}), pin, tmp_path / "v")

    assert dest == tmp_path / "v" / "bin" / "wireproxy.exe"
    assert dest.read_bytes() == b"MZ built"
    # The go.mod patch before the vendor tree is made, the stack patch on the vendor tree after.
    assert steps.steps() == [
        "version",
        "clone",
        "rev-parse",
        "apply",
        "mod",
        "mod",
        "apply",
        "build",
    ]
    applied = [argv for argv in steps.ran if "apply" in argv]
    assert applied[0][-1].endswith("01-gomod.patch")
    assert applied[1][-2:] == ["-p1", str(pin.patches[1][0])]


def test_the_tunnel_build_says_what_to_install_without_go(monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _builder()
    monkeypatch.setattr(builder.shutil, "which", lambda _name: None)

    with pytest.raises(builder.BuildRefused) as refused:
        builder.find_go(None, {}, builder.read_pin())

    assert "Go 1.27.0 is needed" in str(refused.value)
    assert "https://go.dev/dl/" in str(refused.value)


def test_the_tunnel_build_takes_no_go_setting_that_changes_the_program() -> None:
    builder = _builder()
    machine = {
        "GOAMD64": "v3",
        "GOFLAGS": "-race",
        "CGO_CFLAGS": "-O3",
        "GOMODCACHE": "m",
        "PATH": "p",
    }

    env = builder.go_environment(machine)

    assert env == {"GOMODCACHE": "m", "PATH": "p", "GOENV": "off", "GOTOOLCHAIN": "local"}


def test_the_fetch_refuses_a_tunnel_client_that_is_not_the_built_one(
    fetcher: ModuleType, tmp_path: Path
) -> None:
    """Nothing to download: the fetch checks the built file and says how to make it."""
    entry = {**_tunnel_entry(), "extra_files": []}

    with pytest.raises(SystemExit, match="Build it with: python scripts/vendor_build/wireproxy"):
        fetcher.check_built(entry, verify_only=True)

    program = tmp_path / "vendor" / "bin" / "wireproxy.exe"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"MZ the upstream release")
    with pytest.raises(SystemExit, match="SHA-256 MISMATCH"):
        fetcher.check_built(entry, verify_only=True)

    fetcher.check_built({**entry, "sha256": fetcher.digest(program)}, verify_only=True)


def test_the_release_refuses_a_pack_whose_tunnel_client_is_not_the_built_one(
    tmp_path: Path,
) -> None:
    """Every file the manifest declares is there, and the tunnel client is some other build: every
    install would call its own program altered, so the pack is refused."""
    release = _load("release")
    executables, folders, notices = release._vendored_files()
    for name in (*executables, *notices, *(f"{folder}/x" for folder in folders)):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"MZ")
    (tmp_path / "wireproxy.exe").write_bytes(b"MZ the upstream release")

    with pytest.raises(release.ReleaseFailed, match=r"wireproxy\.exe that is not the one"):
        release._check_the_vendored_tools_are_in(tmp_path, "the packed application")

    built = {**_tunnel_entry(), "sha256": release._sha256(tmp_path / "wireproxy.exe")}
    release.prove_the_built_programs_are_pinned(tmp_path, "the packed application", [built])
