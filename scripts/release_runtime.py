# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release's runtime stage: what the bundled interpreter must prove before it is packed."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from release_common import DESKTOP, MANIFEST, PYTHON_VERSION, ROOT, RUNTIME, ReleaseFailed, _sha256


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
    """Require the runtime's own packages to win over a per-user folder and PYTHONPATH."""
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
    """The programs the manifest says this repository builds, each pinned by its file's digest."""
    declared = json.loads(MANIFEST.read_text(encoding="utf-8"))
    built = declared.get("built", []) if isinstance(declared, dict) else []
    return [one for one in built if isinstance(one, dict)] if isinstance(built, list) else []


def prove_the_built_programs_are_pinned(
    folder: Path, what: str, programs: list[dict[str, object]] | None = None
) -> None:
    """Refuse a built program that is not its recipe's file: every install would refuse it."""
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
    """What a wheel carries that its entry refuses, and what it lacks, by DLL name prefix."""
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
    """Refuse a runtime carrying the published wheel, read from its RECORD, not a listing."""
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
