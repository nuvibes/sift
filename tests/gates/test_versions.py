# SPDX-License-Identifier: AGPL-3.0-or-later
"""A version is pinned in exactly one place, and the lockfile is honest.

A tool named in several files gets bumped in one, and CI and a contributor's machine stop agreeing
without anything failing. So everything installable from Python is pinned once, in uv.lock, and
installed from it. gitleaks, a Go binary, is named in two files that are held equal here; semgrep
is pinned once in a locked script of its own and reached through `scripts/venv_tool.py`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from sift.kernel.db import probe_sqlite
from sift.kernel.version import app_version

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]


def test_a_lockfile_exists() -> None:
    """A lockfile exists, so a vulnerability report has a shipped version to point at."""
    assert (REPO / "uv.lock").is_file(), "no uv.lock: run uv lock"


def test_the_lockfile_matches_pyproject() -> None:
    """A stale lockfile is worse than none: it looks authoritative and is not."""
    lock = (REPO / "uv.lock").read_text()
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())

    declared = pyproject["project"]["dependencies"]
    declared += pyproject["project"]["optional-dependencies"]["dev"]

    for spec in declared:
        name = re.split(r"[<>=~\[!]", spec, maxsplit=1)[0].strip()
        assert f'name = "{name}"' in lock, f"{name} is in pyproject but not in uv.lock: re-lock"


def test_the_lockfile_records_the_version_this_repository_declares() -> None:
    """The lockfile carries the version `pyproject.toml` declares, read from HEAD: any `uv` command
    re-locks the working tree, so only the committed file shows a bump that missed it."""
    committed = subprocess.run(
        ["git", "show", "HEAD:uv.lock"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    project = re.search(r'\[\[package\]\]\nname = "sift"\nversion = "([^"]+)"', committed)
    assert project, "uv.lock has no entry for sift itself: re-lock"

    declared = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert project.group(1) == declared, (
        f"uv.lock records sift {project.group(1)} and pyproject.toml declares {declared}. "
        "Run `uv lock` and commit the result with the bump"
    )


SEMGREP_SCRIPT = REPO / "scripts" / "tools" / "semgrep_pinned.py"


def _script_metadata(path: Path) -> dict[str, object]:
    """The inline `# /// script` block of a Python file, read as uv reads it."""
    block = re.search(
        r"^# /// script$\s(?P<content>(^#(| .*)$\s)+)^# ///$",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert block, f"{path.relative_to(REPO)} has no `# /// script` block"
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in block.group("content").splitlines(keepends=True)
    )
    return tomllib.loads(content)


def _semgrep_version() -> str:
    """The one version of semgrep this repository scans with."""
    dependencies = _script_metadata(SEMGREP_SCRIPT)["dependencies"]
    assert isinstance(dependencies, list)
    pins = [one for one in dependencies if re.match(r"semgrep\s*==", one)]
    assert len(pins) == 1, f"the semgrep script must pin exactly one semgrep: {dependencies}"
    return str(pins[0]).split("==", 1)[1].strip()


def test_semgrep_stays_out_of_the_project_lockfile() -> None:
    """Semgrep is in neither pyproject nor uv.lock: its dependencies (an old PyJWT among them) do
    not belong on the surface Sift ships and the audit reads."""
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["dependencies"]
    for extra in pyproject["project"].get("optional-dependencies", {}).values():
        declared = declared + extra
    names = {re.split(r"[<>=~\[!; ]", spec, maxsplit=1)[0].strip().lower() for spec in declared}
    assert "semgrep" not in names, (
        "semgrep is declared in pyproject.toml again. It is a pinned tool outside the lockfile: "
        "change its version in scripts/tools/semgrep_pinned.py"
    )
    lock = (REPO / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "semgrep"' not in lock, "uv.lock records semgrep: re-lock after removing it"


def test_semgrep_is_pinned_once_and_its_lock_agrees() -> None:
    """Semgrep's version is named once, in the script's metadata, and the script's lock agrees."""
    version = _semgrep_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"semgrep is pinned to {version!r}, not a release"
    )

    lock = (REPO / "scripts" / "tools" / "semgrep_pinned.py.lock").read_text(encoding="utf-8")
    locked = re.search(r'\[\[package\]\]\nname = "semgrep"\nversion = "([^"]+)"', lock)
    assert locked, "semgrep_pinned.py.lock records no semgrep: run uv lock --script on it"
    assert locked.group(1) == version, (
        f"the script pins semgrep {version} and its lock records {locked.group(1)}. "
        "Run `uv lock --script scripts/tools/semgrep_pinned.py`"
    )

    consumers = [
        REPO / ".pre-commit-config.yaml",
        REPO / "pyproject.toml",
        REPO / "scripts" / "ci-local.sh",
        REPO / "scripts" / "venv_tool.py",
        *sorted((REPO / ".github").rglob("*.yml")),
    ]
    for path in consumers:
        stray = re.findall(r"semgrep\s*(?:==|~=|>=|<=|@)\s*v?\d", path.read_text(encoding="utf-8"))
        assert not stray, f"{path.relative_to(REPO)} names a semgrep version of its own: {stray}"


def test_venv_tool_runs_semgrep_from_the_locked_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """`venv_tool` runs semgrep through uv from its locked script, `--locked`, never from the
    project's environment."""
    spec = importlib.util.spec_from_file_location("venv_tool", REPO / "scripts" / "venv_tool.py")
    assert spec is not None and spec.loader is not None
    venv_tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(venv_tool)
    ran: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(venv_tool, "find_uv", lambda: "uv")
    monkeypatch.setattr(venv_tool.subprocess, "run", run)
    assert venv_tool.main(["semgrep", "--version"]) == 0

    assert ran == [
        ["uv", "run", "--quiet", "--locked", "--script", str(SEMGREP_SCRIPT), "--version"]
    ]


#: How each runner reaches semgrep: the hook's entry, a script line, a workflow step.
SEMGREP_RUNNERS = (
    REPO / ".pre-commit-config.yaml",
    REPO / "scripts" / "ci-local.sh",
    *sorted((REPO / ".github").rglob("*.yml")),
)


def _semgrep_calls(text: str) -> list[tuple[str, str]]:
    """Every command word that runs semgrep in a runner's text, with the word before it."""
    return re.findall(r"(\S+)\s+(\S*semgrep(?:\.exe)?)\s+(?:scan|--test)\b", text)


def test_the_semgrep_reader_finds_a_bypass() -> None:
    """A runner calling semgrep from the project environment is named; the one route is not."""
    assert _semgrep_calls("run: uv run semgrep scan --config semgrep/") == [("run", "semgrep")]
    assert _semgrep_calls("entry: .venv/bin/semgrep scan") == [("entry:", ".venv/bin/semgrep")]
    assert _semgrep_calls("python scripts/venv_tool.py semgrep --test x") == [
        ("scripts/venv_tool.py", "semgrep")
    ]


def test_every_runner_of_semgrep_goes_through_the_one_route() -> None:
    """Every runner reaches semgrep through the one route, or the pin describes nothing that
    runs."""
    seen = 0
    for path in SEMGREP_RUNNERS:
        for before, word in _semgrep_calls(path.read_text(encoding="utf-8")):
            seen += 1
            assert word == "semgrep" and before.endswith("venv_tool.py"), (
                f"{path.relative_to(REPO)} runs semgrep as {before} {word}. Run it as "
                "`python scripts/venv_tool.py semgrep ...`, the pinned tool"
            )
    # The hook, two ci-local steps and two workflow steps.
    assert seen >= 5, f"only {seen} semgrep runs found: the reader has stopped reading"


GITLEAKS_ACTION = REPO / ".github" / "actions" / "gitleaks" / "action.yml"


def _workflow_files() -> list[Path]:
    return sorted((REPO / ".github" / "workflows").glob("*.yml"))


def _workflows() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _workflow_files())


def test_gitleaks_is_the_same_version_in_ci_and_pre_commit() -> None:
    """gitleaks is the same version in CI and pre-commit, or a secret the local hook misses lands in
    the history before CI catches it. CI's pin is the installer action's default."""
    inputs = yaml.safe_load(GITLEAKS_ACTION.read_text(encoding="utf-8"))["inputs"]
    pre_commit = (REPO / ".pre-commit-config.yaml").read_text()

    hook_match = re.search(
        r"repo:\s*https://github\.com/gitleaks/gitleaks\s*\n\s*rev:\s*v([\d.]+)", pre_commit
    )
    assert hook_match, "gitleaks rev not found in .pre-commit-config.yaml"

    assert inputs["version"]["default"] == hook_match.group(1), (
        f"gitleaks is v{hook_match.group(1)} in pre-commit but "
        f"{inputs['version']['default']} in CI, and they must scan with the same rules"
    )


def test_the_gitleaks_pin_is_the_one_ci_actually_runs() -> None:
    """The workflows install gitleaks through the action, pin left alone, which verifies the
    download against its declared checksums."""
    ci = _workflows()
    installer = GITLEAKS_ACTION.read_text(encoding="utf-8")

    assert "uses: ./.github/actions/gitleaks" in ci, (
        "nothing in the workflows installs the pinned gitleaks, so its pin describes nothing "
        "that runs"
    )
    for path in _workflow_files():
        for job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].values():
            for step in job.get("steps", []):
                if step.get("uses") == "./.github/actions/gitleaks":
                    assert not step.get("with"), f"{path.name} overrides the gitleaks pin"
    for site in ("linux", "windows"):
        assert f"inputs.sha256-{site}" in installer, (
            f"the installer declares a {site} checksum and does not check against it"
        )


def test_ci_installs_from_the_lockfile() -> None:
    """CI installs from the lockfile, never `pip install tool==x`."""
    # Every workflow and composite action: the sync lives in .github/actions/python.
    sources = sorted((REPO / ".github").rglob("*.yml"))
    assert sources, "no workflow or action files found"
    ci = "\n".join(path.read_text() for path in sources)

    assert "uv sync --locked" in ci, "CI must install from the lockfile"

    stray = re.findall(r"pip install\s+([a-zA-Z0-9_\-]+==[\d.]+)", ci)
    assert not stray, f"CI pins a Python tool outside the lockfile: {stray}"


def test_the_installer_installs_what_the_lockfile_records() -> None:
    """The installer installs what the lockfile records, not what the index resolves that day."""
    source = (REPO / "scripts" / "release.py").read_text(encoding="utf-8")
    assert '"export", "--frozen"' in source, "the release must export the lockfile"
    assert '"--require-hashes"' in source, "the release must install the export hash-checked"
    assert '"--no-deps", "."' in source, "Sift itself must go in without resolving again"


def test_every_tool_ci_runs_is_declared() -> None:
    """Every tool CI runs is declared, or a CI job installing from the lock fails on its first
    run."""
    ci = _workflows()
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())

    declared = {
        re.split(r"[<>=~\[!]", spec, maxsplit=1)[0].strip().lower()
        for spec in pyproject["project"]["dependencies"]
        + pyproject["project"]["optional-dependencies"]["dev"]
    }
    aliases = {"cyclonedx-py": "cyclonedx-bom"}

    for tool in re.findall(r"uv run ([a-z0-9][a-z0-9\-]*)", ci):
        if tool in {"bash", "python", "pytest"}:
            continue
        name = aliases.get(tool, tool)
        assert name in declared, (
            f"CI runs `uv run {tool}` but {name} is not in pyproject. "
            "CI installs only from the lockfile, so the job would fail"
        )


def test_python_floor_is_consistent() -> None:
    """Python's floor is 3.13 in all three places: a device swap's pre-shared-key TLS callbacks
    arrived in 3.13 (`slices/swap/lock.py`), and higher would narrow who can run from source."""
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert pyproject["project"]["requires-python"] == ">=3.13"
    assert pyproject["tool"]["ruff"]["target-version"] == "py313"
    assert pyproject["tool"]["mypy"]["python_version"] == "3.13"


def test_the_sqlite_here_can_do_what_sift_needs() -> None:
    """The SQLite here has FTS5, a build option no version reports, and extension loading."""
    capabilities = probe_sqlite()

    assert capabilities.fts5, (
        f"SQLite {capabilities.version} here has no FTS5, and search is built on it"
    )
    assert capabilities.load_extension, f"SQLite {capabilities.version} here cannot load extensions"


def test_nothing_builds_sqlite_any_more() -> None:
    """Nothing builds SQLite: Sift runs on the system's."""
    searched = ("scripts", ".github", "frontend/e2e")
    # By suffix: a browser run leaves media under frontend/e2e.
    text = {"", ".sh", ".yml", ".yaml", ".py", ".ts", ".json", ".toml", ".txt", ".md"}
    offenders = [
        str(path.relative_to(REPO))
        for directory in searched
        for path in (REPO / directory).rglob("*")
        if path.is_file()
        and path.suffix in text
        and "build_sqlite" in path.read_text(errors="ignore")
    ]

    assert offenders == [], f"these still reference a SQLite build: {offenders}"


def _action_refs(document: object) -> Iterator[str]:
    """Every `uses:` VALUE in a parsed workflow, wherever its key sits.

    The document, never the text: a regex over the file reads a comment ending "uses:" as a step,
    and a gate that constrains comments is one people work around.
    """
    if isinstance(document, dict):
        for key, value in document.items():
            if key == "uses" and isinstance(value, str):
                yield value
            else:
                yield from _action_refs(value)
    elif isinstance(document, list):
        for one in document:
            yield from _action_refs(one)


def test_the_pinning_rule_reads_keys_and_not_prose() -> None:
    """A comment about a step is not read as one, and the real step beside it is found."""
    pinned = f"actions/checkout@{'a' * 40}"
    planted = yaml.safe_load(
        "jobs:\n"
        "  one:\n"
        "    steps:\n"
        "      # The same threshold, and NOT the bare one-liner it uses: npm audit\n"
        f"      - uses: {pinned}\n"
    )

    assert list(_action_refs(planted)) == [pinned]


def test_every_github_action_is_pinned_to_a_sha() -> None:
    """Every action under `.github`, composite actions included, is pinned to a SHA: a tag is
    mutable and the action runs with the workflow's token. A `./` reference is this commit."""
    seen = 0
    for source in sorted((REPO / ".github").rglob("*.yml")):
        for ref in _action_refs(yaml.safe_load(source.read_text(encoding="utf-8"))):
            seen += 1
            if ref.startswith("./"):
                continue
            _, _, version = ref.partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", version), (
                f"{ref} in {source.relative_to(REPO)} is pinned to a tag, not a commit SHA"
            )

    assert seen > 5, f"only {seen} actions found under .github: the reader has stopped reading"


def test_sift_declares_its_own_version_exactly_once() -> None:
    """Sift's version is one field in pyproject.toml, and nothing else declares one: two copies
    drift, and the release script hands this one to the packer that names the installer."""
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["version"]

    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", declared), (
        f"pyproject.toml declares version {declared!r}, which is not a version anything can compare"
    )

    desktop = json.loads((REPO / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert "version" not in desktop, (
        "desktop/package.json declares a version of its own. That is a second declaration of one "
        "fact, and two copies drift apart. The release script supplies it instead"
    )

    # The desktop lockfile records the package's version too, so the field is absent from both.
    lock = json.loads((REPO / "desktop" / "package-lock.json").read_text(encoding="utf-8"))
    assert "version" not in lock, "desktop/package-lock.json still records a version of its own"
    assert "version" not in lock["packages"][""], (
        "the lockfile's own entry for the desktop package records a version. Re-run "
        "`npm install --package-lock-only` in desktop/ after removing it from package.json"
    )

    # uv.lock's copy is generated, so it must agree: `uv sync --locked` refuses a disagreeing one.
    locked = re.search(
        r'name = "sift"\nversion = "([^"]+)"\nsource = \{ editable',
        (REPO / "uv.lock").read_text(encoding="utf-8"),
    )
    assert locked is not None, "uv.lock no longer records this project"
    assert locked.group(1) == declared, (
        f"uv.lock says {locked.group(1)} and pyproject says {declared}. Re-run `uv lock`, or "
        "every `uv sync --locked` refuses the lockfile"
    )


def test_the_installer_is_stamped_from_that_one_declaration() -> None:
    """The release script reads pyproject's version and passes it to the packer, writing none."""
    release = (REPO / "scripts" / "release.py").read_text(encoding="utf-8")

    assert 'declared["project"]["version"]' in release, (
        "the release script no longer reads the version out of pyproject.toml"
    )
    assert "-c.extraMetadata.version={VERSION}" in release, (
        "the packer is not handed the declared version, so the installer would be named after "
        "whatever the desktop package happens to say, which is nothing"
    )


def test_a_release_is_published_where_the_update_check_reads() -> None:
    """A release goes to the repository the shell's update check reads, tagged with the version
    read from pyproject."""
    release = (REPO / "scripts" / "release.py").read_text(encoding="utf-8")
    feed = re.search(
        r"export const DEFAULT_FEED_URL = 'https://api\.github\.com/repos/([^/]+/[^/]+)/",
        (REPO / "desktop" / "src" / "update.ts").read_text(encoding="utf-8"),
    )
    assert feed is not None, "desktop/src/update.ts no longer declares DEFAULT_FEED_URL"

    assert '_declared_in_the_shell("DEFAULT_FEED_URL")' in release, (
        "the release script no longer reads where to publish from the shell's feed address"
    )
    assert feed.group(1) not in release, (
        f"the release script names {feed.group(1)} itself, a second copy of where releases go"
    )
    assert 'f"v{VERSION}"' in release, "the release is no longer tagged with the declared version"


def test_the_running_package_reports_the_declared_version() -> None:
    """The installed package reports the declared version: the number the application shows.
    On a development machine a mismatch means re-install (`uv sync`).
    """
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))

    assert app_version() == pyproject["project"]["version"], (
        f"the installed package reports {app_version()!r} and pyproject declares "
        f"{pyproject['project']['version']!r}. Re-install the package so they agree"
    )
