# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asserts each CI gate rejects a planted violation.

A check that cannot fail is worse than no check, because the green result is what stops
anyone from looking. These tests exist so that loosening a rule breaks the build visibly.

Violations are planted in a throwaway repo under tmp_path, never in this tree: a fake key
committed here to prove the scanner works would be a fake key in the history forever, and
the scanner would then fail every subsequent build.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tests.gates import posix_bash, the_client_tree

pytestmark = pytest.mark.gate

REPO = Path(__file__).resolve().parents[2]
# Both layouts. A virtual environment puts its executables in `bin/` on POSIX and in `Scripts/` on
# Windows, with an `.exe` on the end, and naming only the POSIX one would make this gate SKIP on the
# platform Sift ships on, with semgrep installed. A skipped gate reads exactly like a passing one in
# the summary, which is what `_require` says in its own docstring.
VENV_BIN = REPO / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
TOOL_SUFFIX = ".exe" if sys.platform == "win32" else ""


# UTF-8 AND `errors="replace"` ON EVERY ONE OF THESE, AND IT IS NOT TIDINESS.
#
# `text=True` alone decodes a subprocess's output with the LOCALE codec, which on Windows is cp1252.
# semgrep's findings contain bytes cp1252 cannot decode, and when the decode fails, subprocess
# swallows the error inside its reader thread and hands back `stdout=None`. The assertion then reads
# None and dies with "argument of type 'NoneType' is not iterable", which says nothing at all about
# the real cause, with semgrep installed and the rules working.


def _require(tool: str) -> str:
    """Locate a tool. Skips locally if absent; in CI a missing tool is a failure, since a
    skipped gate is indistinguishable from a passing one in the summary."""
    path = shutil.which(tool) or str(VENV_BIN / f"{tool}{TOOL_SUFFIX}")
    if not Path(path).exists():
        if os.environ.get("CI"):
            pytest.fail(f"{tool} is not installed in CI; the gate would not run")
        pytest.skip(f"{tool} not installed")
    return path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.fixture
def throwaway_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "victim"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@users.noreply.github.com")
    return repo


# --- semgrep -----------------------------------------------------------------------


def _semgrep() -> list[str]:
    """semgrep by the route every runner of it takes: the pinned tool outside the lockfile, which
    `scripts/venv_tool.py` runs through uv. Without uv this skips locally and fails in CI."""
    script = REPO / "scripts" / "venv_tool.py"
    spec = importlib.util.spec_from_file_location("venv_tool", script)
    assert spec is not None and spec.loader is not None
    venv_tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(venv_tool)
    try:
        venv_tool.find_uv()
    except SystemExit:
        if os.environ.get("CI"):
            pytest.fail("uv is not installed in CI; the semgrep gates would not run")
        pytest.skip("uv not installed")
    return [sys.executable, str(script), "semgrep"]


def test_semgrep_rules_fire_and_do_not_overfire() -> None:
    """Every rule must catch its violation and stay quiet on the correct equivalent."""
    result = subprocess.run(
        [*_semgrep(), "--test", "--config", "semgrep/", "semgrep/", "--disable-version-check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"rule self-tests failed:\n{result.stdout}\n{result.stderr}"
    assert "All tests passed" in result.stdout


@pytest.mark.parametrize(
    ("rule", "filename", "source"),
    [
        (
            "sift-no-string-built-sql",
            "sql.py",
            "async def f(conn, aid):\n"
            '    await conn.execute(f"SELECT * FROM assets WHERE id = {aid}")\n',
        ),
        (
            "sift-no-shell-true",
            "sh.py",
            'import subprocess\ndef f(url):\n    subprocess.run(f"yt-dlp {url}", shell=True)\n',
        ),
        (
            "sift-no-unsafe-deserialization",
            "deser.py",
            "import pickle\ndef f(b):\n    return pickle.loads(b)\n",
        ),
        (
            "sift-no-downloader-import",
            "imp.py",
            "import yt_dlp\ndef f():\n    return yt_dlp\n",
        ),
        (
            "sift-no-print-or-raw-logger",
            "log.py",
            "def f(cookies):\n    print(cookies)\n",
        ),
        (
            "sift-no-asset-sql-outside-kernel",
            "browse.py",
            "async def f(conn, asset_id):\n"
            '    await conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))\n',
        ),
        (
            "sift-no-content-store-outside-kernel",
            "serve.py",
            "async def f(request, viewer, asset_id):\n"
            "    return request.app.state.content.get(asset_id)\n",
        ),
        (
            "sift-no-viewer-forgery-outside-kernel",
            "forge.py",
            "def f():\n    return Viewer(id='x', role=Role.ADMIN)\n",
        ),
        (
            "sift-no-svelte-html-directive",
            "Bad.svelte",
            "<h1>{@html asset.title}</h1>\n",
        ),
    ],
)
def test_planted_violation_fails_the_scan(
    rule: str, filename: str, source: str, tmp_path: Path
) -> None:
    """Separate from the rule self-tests above: this runs the same invocation CI runs, so it
    also proves the rules are actually wired into the scan."""
    target = tmp_path / filename
    target.write_text(source, encoding="utf-8", newline="\n")

    result = subprocess.run(
        [
            *_semgrep(),
            "scan",
            "--config",
            str(REPO / "semgrep"),
            "--error",
            "--quiet",
            "--disable-version-check",
            str(target),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, f"{rule} did not fail the scan"
    assert rule in result.stdout, f"expected {rule} in findings:\n{result.stdout}"


def test_semgrep_is_clean_on_the_tree() -> None:
    result = subprocess.run(
        [
            *_semgrep(),
            "scan",
            "--config",
            "semgrep/",
            "--error",
            "--quiet",
            "--disable-version-check",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"semgrep is not clean:\n{result.stdout}"


# --- gitleaks ----------------------------------------------------------------------


def test_gitleaks_catches_a_planted_key(throwaway_repo: Path) -> None:
    # Assembled at runtime: a literal here would be a detectable secret in this repo's own
    # history, and the scanner would then flag this file forever.
    #
    # AND IT MUST NOT END IN "EXAMPLE". The obvious choice is AWS's own documentation key, but
    # gitleaks' aws-access-token rule carries an allowlist of `.+EXAMPLE$`, so from 8.21.2 onwards
    # the scanner ignores it on purpose. Planting it would assert the gate catches the one key the
    # gate is built to skip, and pass only on an older gitleaks (8.16.0 does not skip it). The rule
    # also requires an entropy of 3, so the body has to look random rather than spell a word.
    fake_key = "AKIA" + "QYLPMN5HG7RT" + "K2WX"
    fake_secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"
    gitleaks = _require("gitleaks")

    (throwaway_repo / "config.py").write_text(
        f'AWS_ACCESS_KEY_ID = "{fake_key}"\nAWS_SECRET_ACCESS_KEY = "{fake_secret}"\n',
        encoding="utf-8",
        newline="\n",
    )
    _git(throwaway_repo, "add", "-A")
    _git(throwaway_repo, "commit", "-m", "x")

    result = subprocess.run(
        [gitleaks, "detect", "--source", str(throwaway_repo), "--no-banner", "--redact"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a committed access key was not detected"


def test_gitleaks_catches_a_planted_cookie_jar(throwaway_repo: Path) -> None:
    gitleaks = _require("gitleaks")
    shutil.copy(REPO / ".gitleaks.toml", throwaway_repo / ".gitleaks.toml")

    (throwaway_repo / "leaked.txt").write_text(
        "# Netscape HTTP Cookie File\n"
        ".example.com\tTRUE\t/\tTRUE\t1799999999\tsessionid\tabc123def456ghi789jkl\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(throwaway_repo, "add", "-A")
    _git(throwaway_repo, "commit", "-m", "x")

    result = subprocess.run(
        [
            gitleaks,
            "detect",
            "--source",
            str(throwaway_repo),
            "--config",
            str(throwaway_repo / ".gitleaks.toml"),
            "--no-banner",
            "--redact",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a committed cookie jar was not detected"


def test_gitleaks_reads_an_address_in_text_and_not_in_a_lockfile_or_a_picture(
    throwaway_repo: Path, tmp_path: Path
) -> None:
    """What the history scan reports, read the way every scan here reads: through git.

    An address in an ordinary text file is reported; that is the known positive, without which an
    empty report would pass. gitleaks' own defaults leave every `package-lock.json` unread (npm
    writes a maintainer's address into a deprecation notice word for word), and git hands the scan
    no binary file, so a Site icon's pixels are never read either. Asked through git, the answer is
    the same on every system; a scan of a bare folder names paths with the system's separator, and
    the defaults' lockfile rule matches only a forward slash. Assembled at runtime, so this file
    holds no address to be flagged.
    """
    import json

    gitleaks = _require("gitleaks")
    address = "fixture" + "@" + "tarball.dev"
    lockfile = throwaway_repo / "desktop" / "package-lock.json"
    lockfile.parent.mkdir()
    lockfile.write_text(
        "{\n"
        f'      "deprecated": "Old versions are not supported. Write to {address} with questions.",\n'
        f'      "author": "{address}"\n'
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    icon = throwaway_repo / "src" / "sift" / "kernel" / "site_icons" / "icons" / "a-site.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"\x89PNG\r\n\x1a\n" + address.encode() + b"\x00\xff")
    (throwaway_repo / "desktop" / "notes.txt").write_text(
        f"Write to {address} with questions.\n", encoding="utf-8", newline="\n"
    )
    _git(throwaway_repo, "add", "-A")
    _git(throwaway_repo, "commit", "-m", "x")
    report = tmp_path / "report.json"

    subprocess.run(
        [
            gitleaks,
            "detect",
            "--source",
            str(throwaway_repo),
            "--config",
            str(REPO / ".gitleaks.toml"),
            "--no-banner",
            "--redact",
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    found = [(one["File"], one["StartLine"]) for one in json.loads(report.read_text())]
    assert found == [("desktop/notes.txt", 1)], found


def test_gitleaks_is_clean_on_the_history() -> None:
    gitleaks = _require("gitleaks")
    result = subprocess.run(
        [
            gitleaks,
            "detect",
            "--source",
            str(REPO),
            "--config",
            str(REPO / ".gitleaks.toml"),
            "--no-banner",
            "--redact",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"gitleaks found a secret in the history:\n{result.stdout}"


# --- repo hygiene ------------------------------------------------------------------


def _install(repo: Path, script: str) -> None:
    (repo / "scripts").mkdir(exist_ok=True)
    shutil.copy(REPO / "scripts" / script, repo / "scripts" / script)
    (repo / "scripts" / script).chmod(0o755)


def _install_hygiene(repo: Path) -> None:
    _install(repo, "check_repo_hygiene.sh")
    # `newline` throughout this file, and it is not decoration: text mode on Windows turns
    # every \n into \r\n, so a fixture standing in for repository content came out with a
    # line ending the repository does not use. The rule that reads the working tree then
    # fires on the fixture rather than on what the test planted, and the test passes for the
    # wrong reason, or, for one asserting a clean run, cannot pass at all.
    (repo / ".gitignore").write_text(".env\ncookies/\n", encoding="utf-8", newline="\n")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Creating a symbolic link needs a privilege an ordinary Windows account does not hold "
        "(WinError 1314). There is no junction equivalent for this one: the rule reads git's index "
        "for mode 120000, and a junction is not a git symlink, so what is being planted cannot be "
        "planted here at all. The rule itself is platform-independent; only this planting is not"
    ),
)
def test_hygiene_catches_escaping_symlink(throwaway_repo: Path) -> None:
    # A symlink commits as a pointer, so content scanners see nothing, but it resolves for
    # anyone who clones it.
    _install_hygiene(throwaway_repo)
    (throwaway_repo / "private").symlink_to("../../elsewhere")
    _git(throwaway_repo, "add", "-A")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "an escaping symlink was not rejected"
    assert "outside the repo" in result.stdout


def test_hygiene_catches_forced_env(throwaway_repo: Path) -> None:
    _install_hygiene(throwaway_repo)
    (throwaway_repo / ".env").write_text("SIFT_ADMIN_PASSWORD=x\n", encoding="utf-8", newline="\n")
    _git(throwaway_repo, "add", "-f", ".env")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a force-added .env was not rejected"
    assert "secret file" in result.stdout


@pytest.mark.parametrize(
    "planted",
    [
        "deploy/server.pem",
        "tls.key",
        "capture.har",
        "keystore.jar",
        "library.sqlite",
        "data/sift.sqlite3",
        "tunnel.conf",
    ],
)
def test_hygiene_catches_a_forced_secret_shaped_file(throwaway_repo: Path, planted: str) -> None:
    _install_hygiene(throwaway_repo)
    secret = throwaway_repo / planted
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("x\n", encoding="utf-8", newline="\n")
    _git(throwaway_repo, "add", "-f", planted)

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, f"a force-added {planted} was not rejected"
    assert "secret file" in result.stdout


def test_gitleaks_skips_the_same_folders_whichever_separator_a_path_has() -> None:
    """A scan on Windows reports paths with backslashes, and an ignore path written for one
    separator skips nothing there."""
    import re
    import tomllib

    config = tomllib.loads((REPO / ".gitleaks.toml").read_text(encoding="utf-8"))
    skipped = [re.compile(one) for one in config["allowlist"]["paths"]]
    for folder in (".venv", "node_modules", ".git"):
        for path in (f"{folder}/lib/x.py", f"D:\\checkout\\{folder}\\lib\\x.py"):
            assert any(one.search(path) for one in skipped), path
    assert not any(one.search("D:\\checkout\\src\\venv_tool.py") for one in skipped)


def test_hygiene_passes_on_the_tree() -> None:
    """Run with the client's source tree held, because the rule against a file written and never
    added reads that tree too: a fixture another gate test has planted there for a moment is a
    written, unadded file, and would be reported as one."""
    with the_client_tree():
        result = subprocess.run(
            [posix_bash(), "scripts/check_repo_hygiene.sh"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    assert result.returncode == 0, f"repo hygiene is failing:\n{result.stdout}"


def _plant(repo: Path, line: str) -> None:
    """One tracked source file carrying `line`, and otherwise beyond reproach.

    The licence header matters: without it the script fails on the missing header instead, and a
    test asserting only "it failed" then passes without the rule under test ever firing.
    """
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "thing.py").write_text(
        f"# SPDX-License-Identifier: AGPL-3.0-or-later\n{line}\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "-A")


def test_hygiene_catches_a_source_file_that_reads_as_binary(throwaway_repo: Path) -> None:
    """A NUL byte anywhere in a text source file makes it binary to every check that skips
    binaries, as the ASCII rule does so that fonts and media fixtures are skipped.

    A component using one as a stand-in value would otherwise stop being read by that rule and
    read as passing it, so "a source file has no NUL bytes" is a check rather than an assumption.
    """
    _install_hygiene(throwaway_repo)
    (throwaway_repo / "src").mkdir(exist_ok=True)
    (throwaway_repo / "src" / "thing.py").write_bytes(
        b"# SPDX-License-Identifier: AGPL-3.0-or-later\nSENTINEL = '\x00none'\n"
    )
    _git(throwaway_repo, "add", "-A")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a source file holding a NUL byte was not rejected"
    assert "holds a NUL byte" in result.stdout


def test_hygiene_catches_a_tracked_file_that_carries_crlf_in_the_working_tree(
    throwaway_repo: Path,
) -> None:
    """`.gitattributes` promises LF and delivers it on the way IN, which is the half that hides
    this: a file written with CRLF is normalised as it is committed, so the committed content is
    right and git reports no diff while the copy on disk is wrong.

    A build step that regenerates a file with CRLF does it on every single run. What it costs: a
    shell script with CRLF is a "bad
    interpreter" error naming the wrong problem, and every affected file reads as modified for
    ever, which is how a real change goes unnoticed in a status listing.
    """
    _install_hygiene(throwaway_repo)
    (throwaway_repo / "notes.md").write_bytes(b"one\r\ntwo\r\n")
    _git(throwaway_repo, "add", "-A")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a file carrying CRLF in the working tree was not rejected"
    assert "carries CRLF in the working tree" in result.stdout
    assert "notes.md" in result.stdout


def test_hygiene_accepts_a_working_tree_that_is_lf(throwaway_repo: Path) -> None:
    """The known positive for the one above. Without it the rule could be matching everything, or
    nothing, and the test beside it would pass either way."""
    _install_hygiene(throwaway_repo)
    (throwaway_repo / "notes.md").write_bytes(b"one\ntwo\n")
    _git(throwaway_repo, "add", "-A")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert "every tracked file is LF in the working tree" in result.stdout


def test_hygiene_reads_a_file_at_the_root_and_not_only_a_list_of_directories(
    throwaway_repo: Path,
) -> None:
    """The rule reads the repository root as well as the source directories.

    Everything the other tests here plant goes in `src/`, so all of them would pass while a line in
    `.gitattributes` named a file that is not here. This plants at the root instead: what it proves
    is not the rule, which is proved next door, but the sweep: a check that names the places it
    looks is a check somebody has to remember to extend.
    """
    _install_hygiene(throwaway_repo)
    (throwaway_repo / ".gitattributes").write_text(
        "# See missing-guide.md for the reasoning.\n* text=auto eol=lf\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(throwaway_repo, "add", "-A")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a dead reference in a file at the root was not rejected"
    assert ".gitattributes: missing-guide.md" in result.stdout


def test_hygiene_catches_a_source_file_that_was_never_added(throwaway_repo: Path) -> None:
    """A file nothing tracks is a file no check in the repository can read.

    Every other rule in that script reads the index, and so does nearly every gate here. A source
    file that was written and never added therefore passes all of them by being absent, and the
    summary cannot tell that apart from passing: a count of files read never mentions the ones that
    were not there, a gate's own test module included.
    """
    _install_hygiene(throwaway_repo)
    (throwaway_repo / "src").mkdir()
    (throwaway_repo / "src" / "forgotten.py").write_text(
        "# SPDX-License-Identifier: AGPL-3.0-or-later\n", encoding="utf-8", newline="\n"
    )
    _git(throwaway_repo, "add", "scripts", ".gitignore")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a source file that was never added was not rejected"
    assert "written but never added" in result.stdout
    assert "forgotten.py" in result.stdout


def test_hygiene_accepts_a_tree_where_everything_written_is_also_added(
    throwaway_repo: Path,
) -> None:
    """The known positive for the one above, and it is the half that can rot quietly.

    A rule that reads an empty listing as a clean tree passes for two different reasons, and only
    one of them is the tree being clean. This plants the same file and adds it: the rule has to say
    so in as many words, which it cannot do if the listing it reads never had anything in it.
    """
    _install_hygiene(throwaway_repo)
    (throwaway_repo / "src").mkdir()
    (throwaway_repo / "src" / "remembered.py").write_text(
        "# SPDX-License-Identifier: AGPL-3.0-or-later\n", encoding="utf-8", newline="\n"
    )
    _git(throwaway_repo, "add", "-A")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert "nothing is written and left out of the index" in result.stdout
    assert "written but never added" not in result.stdout


def test_hygiene_accepts_a_file_the_ignore_rules_cover(throwaway_repo: Path) -> None:
    """The rule asks the ignore rules rather than carrying a list of what is generated.

    Without this, the honest reading of the rule above is "every file must be added", which would
    make it unusable the first time a build wrote anything. What is refused is a file that is
    neither tracked nor ignored. That is the whole of the claim, and this is the half of it the
    test above cannot show.
    """
    _install_hygiene(throwaway_repo)
    ignores = throwaway_repo / ".gitignore"
    ignores.write_text(
        ignores.read_text(encoding="utf-8") + "build/\n", encoding="utf-8", newline="\n"
    )
    (throwaway_repo / "build").mkdir()
    (throwaway_repo / "build" / "bundle.js").write_text("0;\n", encoding="utf-8", newline="\n")
    _git(throwaway_repo, "add", "-A")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert "nothing is written and left out of the index" in result.stdout


def test_hygiene_catches_a_planted_emoji(throwaway_repo: Path) -> None:
    """The tree is ASCII: an emoji in a source file is refused."""
    _install_hygiene(throwaway_repo)
    _plant(throwaway_repo, "# A rocket: \U0001f680")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a planted emoji was not rejected"
    assert "not ASCII" in result.stdout


def test_hygiene_catches_a_md_file_the_repository_does_not_hold(throwaway_repo: Path) -> None:
    """A comment pointing at a .md file that is not in the tree is a dead reference.

    The rule asks the index whether the file is there.
    """
    _install_hygiene(throwaway_repo)
    _plant(throwaway_repo, "# The reasoning is in missing-guide.md, see there.")

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, "a .md file the repository does not hold was not rejected"
    assert "names a .md file that is not in the tree" in result.stdout
    assert "src/thing.py: missing-guide.md" in result.stdout


def test_hygiene_accepts_a_md_file_the_repository_holds(throwaway_repo: Path) -> None:
    """The known negative, and it carries every shape the pattern has to leave alone.

    A bare name and a path both resolve against the index, with or without a leading `./`. A URL's
    last segment is another project's file and a `.chip.md` in a stylesheet is a class, so neither
    is a token at all: a rule that fired on them would be answered by rewording correct text.
    """
    _install_hygiene(throwaway_repo)
    (throwaway_repo / "docs").mkdir()
    (throwaway_repo / "docs" / "guide.md").write_text("A guide.\n", encoding="utf-8", newline="\n")
    _plant(
        throwaway_repo,
        "# See guide.md, docs/guide.md and ./docs/guide.md.\n"
        "# Upstream: https://example.com/project/blob/main/CHANGELOG.md\n"
        "STYLE = '.chip.md { gap: 0 }'",
    )

    result = subprocess.run(
        [posix_bash(), "scripts/check_repo_hygiene.sh"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert "every .md file named is in the tree" in result.stdout, result.stdout


# --- the dash -----------------------------------------------------------------------------------

#: The dash the gate refuses. Every specimen below is built from it, so this file carries none.
DASH = " -- "


def _dash_gate() -> types.ModuleType:
    """`scripts/check_display_dashes.py`, loaded by path: it is a hook script, not a package."""
    path = REPO / "scripts" / "check_display_dashes.py"
    spec = importlib.util.spec_from_file_location("_dash_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: One line per shape the gate refuses, each in a comment or a string where it reads as prose.
REFUSED_SHAPES = {
    "between two words": f"# the row{DASH}once written{DASH}is read by nobody",
    "at the end of a line": "# a sentence carried onto the next line" + DASH.rstrip(),
    "at the start of a wrapped line": "#" + DASH.rstrip() + " and the rest of the sentence",
    "before an escaped newline": 'MESSAGE = "a message wrapped here' + DASH.rstrip() + '\\n"',
}

#: Every named exemption, with a file that holds it: `(path, text)`. A name added to the gate
#: without a specimen here fails `test_every_named_exemption_is_proved_by_a_specimen`.
EXEMPT_SPECIMENS: dict[str, list[tuple[str, str]]] = {
    "sql-comment": [
        (
            "src/fine_sql.py",
            'QUERY = """\nSELECT a.id\n  FROM assets a'
            + DASH
            + "the files a viewer may see\n"
            + DASH.lstrip()
            + 'a comment line of its own\n WHERE a.id = ?\n"""\n',
        )
    ],
    "seam-marker": [
        ("src/fine_seam.py", 'MARKER = "\\n' + DASH + 'A collection is a sequence arranged"\n')
    ],
    "the-dash-itself": [("src/fine_itself.py", 'FOUND = "' + DASH + '" in TEXT\n')],
    "end-of-options": [
        ("scripts/fine.sh", "git ls-files -z" + DASH + "src\n"),
        (".github/workflows/fine.yml", "steps:\n  - run: npm run test" + DASH + "--bail\n"),
        ("docs/fine.md", "Run it:\n\n```sh\ngit grep -n" + DASH + "foo\n```\n"),
        (
            "src/fine_command.py",
            'COMMAND = "yt-dlp --retries 0' + DASH + 'https://example.test/a"\n',
        ),
    ],
    "section-banner": [("src/fine_banner.py", "#" + DASH + "the caller's side" + DASH + "\n")],
    "stored-sentence": [
        (
            "src/fine_stored.py",
            'STALE = "This is often temporary' + DASH + 'try it again in a little while."\n',
        )
    ],
    "lockfile": [("uv.lock", "a" + DASH + "b\n")],
}


@pytest.mark.parametrize("shape", sorted(REFUSED_SHAPES))
def test_every_shape_of_the_dash_is_refused(shape: str) -> None:
    line = REFUSED_SHAPES[shape]
    assert _dash_gate().file_dashes("src/thing.py", line + "\n") == [(1, line)]


def test_a_docstring_is_never_read_as_sql() -> None:
    """A line opening with two hyphens is a SQL comment inside a statement and a wrapped dash
    inside a docstring. Only the string it sits in can tell them apart, and a docstring naming
    PRAGMA and JOIN is still prose."""
    text = (
        "def f() -> None:\n"
        '    """A docstring that names PRAGMA and JOIN and WHERE, and wraps its dash\n'
        "   " + DASH.rstrip() + ' onto the next line."""\n'
    )
    assert [line for line, _ in _dash_gate().file_dashes("src/thing.py", text)] == [3]


def test_a_sql_string_does_not_excuse_a_comment_beside_it() -> None:
    """The exemption covers the statement's own text and stops at the string's end."""
    text = f'QUERY = "SELECT 1"  # a note{DASH}beside it\n'
    assert [line for line, _ in _dash_gate().file_dashes("src/thing.py", text)] == [1]


def test_a_quoted_string_in_shell_is_prose_and_is_refused() -> None:
    """End of options is only ever unquoted; a message in quotes is a sentence."""
    text = f'echo "uv not found{DASH}run uv sync"\n'
    assert [line for line, _ in _dash_gate().file_dashes("scripts/thing.sh", text)] == [1]


@pytest.mark.parametrize(
    ("name", "path", "text"),
    [(name, path, text) for name, pairs in EXEMPT_SPECIMENS.items() for path, text in pairs],
)
def test_every_exemption_lets_its_specimen_through(name: str, path: str, text: str) -> None:
    gate = _dash_gate()
    assert name in gate.EXEMPTIONS
    assert gate.file_dashes(path, text) == [], name


def test_every_named_exemption_is_proved_by_a_specimen() -> None:
    """A reason with nothing proving it lets through what nobody looked at."""
    gate = _dash_gate()
    assert set(EXEMPT_SPECIMENS) == set(gate.EXEMPTIONS)
    assert all(reason.strip() for reason in gate.EXEMPTIONS.values())


def test_the_dash_gate_refuses_a_planted_dash_anywhere_in_the_tree(throwaway_repo: Path) -> None:
    """The whole path: git's listing, every file in it, and a file written and not yet added.

    The file that was never added is the known positive for the listing. A walk of the index alone
    passes a new file until its first commit, and a new file is where a new dash is written."""
    _install(throwaway_repo, "check_display_dashes.py")
    (throwaway_repo / "tests" / "gates").mkdir(parents=True)
    shutil.copy(REPO / "tests" / "gates" / "server_copy.py", throwaway_repo / "tests" / "gates")
    for pairs in EXEMPT_SPECIMENS.values():
        for path, text in pairs:
            (throwaway_repo / path).parent.mkdir(parents=True, exist_ok=True)
            (throwaway_repo / path).write_text(text, encoding="utf-8", newline="\n")
    (throwaway_repo / "src" / "thing.py").write_text(
        "".join(line + "\n" for line in REFUSED_SHAPES.values()), encoding="utf-8", newline="\n"
    )
    _git(throwaway_repo, "add", "-A")
    (throwaway_repo / "src" / "new.py").write_text(
        f"# written and never added{DASH}still read\n", encoding="utf-8", newline="\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/check_display_dashes.py"],
        cwd=throwaway_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0, result.stdout
    assert "A dash made of two hyphens" in result.stdout, result.stdout
    found = {line.strip() for line in result.stdout.splitlines() if line.startswith("  ")}
    expected = {f"src/thing.py:{number}" for number in range(1, len(REFUSED_SHAPES) + 1)}
    assert expected | {"src/new.py:1"} <= found, result.stdout
    exempt = {path for pairs in EXEMPT_SPECIMENS.values() for path, _ in pairs}
    assert not [one for one in found if one.split(":")[0] in exempt], result.stdout
