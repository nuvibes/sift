# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workflows run each check where it means something, say each version once, and agree with the
local runner.

  1. Every job runs on a runner GitHub hosts: a self-hosted runner serving a public repository runs
     the workflow of whoever opens a pull request on the machine that hosts it.
  2. A matrix never cancels its siblings.
  3. The interpreter the installer bundles is written down once, in `.python-version`.
  4. Node's version is written down once, in `.nvmrc`.
  5. Nothing in a step that runs on Windows names a path only POSIX has.
  6. Every composite action is well formed. actionlint cannot read an `action.yml`, so this is the
     only schema check those files get.
  7. Every check in this repository is run by something.
  8. The suite's shards hold every test file exactly once, and the suite and code scanning spend no
     minutes in a private copy unless dispatched.
  9. The local runner's default is what `quick.yml` runs, gate tests included.
 10. Every gate file is placed: in the tier `quick` runs on every push, or named with what makes it
     wait for the whole run.
 11. The workflows hold up in a public repository: no trigger that runs a fork's code with this
     repository's secrets, no event text spliced into a script, the least permissions on every
     job, a time limit and a concurrency group everywhere, and the local whole run reached.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
ACTIONS = REPO / ".github" / "actions"
SHARDS = REPO / ".github" / "scripts" / "shard_tests.py"
LOCAL = REPO / "scripts" / "ci-local.sh"

LINUX = "ubuntu-latest"
WINDOWS = "windows-latest"
#: The runners GitHub hosts that these workflows use.
HOSTED = frozenset({LINUX, WINDOWS})

#: The jobs that spend minutes only when the repository is public or somebody dispatched them.
PUBLIC_OR_DISPATCHED = (
    "github.event_name == 'workflow_dispatch' || !github.event.repository.private"
)


def workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def workflow(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def triggers(body: dict[str, Any]) -> dict[str, Any]:
    """The `on:` block. YAML 1.1 reads a bare `on` key as the boolean True."""
    found: dict[str, Any] = body.get("on", body.get(True, {}))
    return found


def all_jobs() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path.name, name, job)
        for path in workflow_files()
        for name, job in workflow(path)["jobs"].items()
    ]


def runners(job: dict[str, Any], name: str) -> set[str]:
    """Which runners a job actually uses, resolved through its matrix.

    An unrecognised shape RAISES rather than returning nothing: a rule that reads a form it does not
    understand as "no runners" passes hardest on exactly the job it cannot read.
    """
    declared = job.get("runs-on")
    if isinstance(declared, str) and not declared.startswith("${{"):
        return {declared}
    if declared == "${{ matrix.os }}":
        listed = job.get("strategy", {}).get("matrix", {}).get("os")
        assert listed, f"{name} runs on ${{{{ matrix.os }}}} but declares no matrix.os"
        return set(listed)
    raise AssertionError(f"{name}: cannot tell what {declared!r} runs on. Teach this gate the form")


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job.get("steps", []) if isinstance(step, dict)]


def composite_actions() -> list[Path]:
    return sorted(ACTIONS.glob("*/action.yml"))


def shard_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("shard_tests", SHARDS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------- 1, 2. where each job runs


def test_there_are_workflows_to_read() -> None:
    """Every rule below iterates. Empty, they would all pass."""
    names = {path.name for path in workflow_files()}
    assert {"quick.yml", "suite.yml", "release-verify.yml"} <= names, names


def test_every_job_runs_on_a_hosted_runner() -> None:
    """A label list, a group or an expression could name a machine of somebody's own; a job names
    one of the hosted runners outright."""
    for file, name, job in all_jobs():
        declared = job.get("runs-on")
        assert isinstance(declared, str), f"{file}:{name} runs on {declared!r}, not a hosted label"
        assert runners(job, name) <= HOSTED, f"{file}:{name} runs on {runners(job, name)}"


#: The jobs that run Sift, or what it ships, where it ships: the suite's system branches and the
#: coverage gates read differently on Linux, and the shell branches on `process.platform`.
WINDOWS_JOBS = {
    ("quick.yml", "desktop"),
    *(("suite.yml", name) for name in ("tests", "authz", "coverage", "e2e", "shipped")),
    ("release-verify.yml", "verify"),
    ("release-verify.yml", "sbom"),
}


def test_what_ships_on_windows_is_checked_on_windows() -> None:
    found = {(file, name): runners(job, name) for file, name, job in all_jobs()}
    moved = sorted(key for key in WINDOWS_JOBS if found.get(key) != {WINDOWS})
    assert not moved, f"jobs that check what ships on Windows and run elsewhere: {moved}"


def test_a_matrix_never_cancels_its_siblings() -> None:
    """One leg's failure must not hide whether the others had the same one."""
    for file, name, job in all_jobs():
        strategy = job.get("strategy")
        if not strategy or "matrix" not in strategy:
            continue
        assert strategy.get("fail-fast") is False, f"{file}:{name} has no `fail-fast: false`"


# ---------------------------------------------------------------------------- 2, 3, 4. versions


def declared_python() -> str:
    return (REPO / ".python-version").read_text(encoding="utf-8").strip()


def declared_node() -> str:
    return (REPO / ".nvmrc").read_text(encoding="utf-8").strip()


def test_the_shipping_interpreter_is_declared() -> None:
    assert re.fullmatch(r"\d+\.\d+", declared_python()), (
        f".python-version reads {declared_python()!r}; it must be major.minor, because uv, the "
        "release script and the Windows build all read this one file"
    )


@pytest.mark.parametrize(
    "rel",
    [
        "scripts/release.py",
        "scripts/check_windows_wheels.py",
        *(path.relative_to(REPO).as_posix() for path in workflow_files()),
        *(path.relative_to(REPO).as_posix() for path in composite_actions()),
    ],
)
def test_the_shipping_interpreter_is_declared_only_once(rel: str) -> None:
    """A second copy of the version is a build testing an interpreter nobody installs, the day the
    two disagree."""
    version = declared_python()
    body = (REPO / rel).read_text(encoding="utf-8")
    quoted = [f'"{version}"', f"'{version}'"]
    found = [form for form in quoted if form in body]
    assert not found, (
        f"{rel} writes the shipping interpreter out as {found[0]}. It is declared in "
        ".python-version, which uv reads by itself; a second copy drifts the first time one moves."
    )


def test_node_is_declared_once() -> None:
    version = declared_node()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f".nvmrc reads {version!r}, not a full version"

    for path in [*workflow_files(), *composite_actions()]:
        assert "node-version:" not in path.read_text(encoding="utf-8"), (
            f"{path.name} pins a Node version of its own; `.nvmrc` is the declaration and "
            "`node-version-file` reads it"
        )


# ------------------------------------------------------------------------- 5. POSIX-only paths

#: Paths that exist on Linux and not on Windows, and what goes wrong when a step names one.
POSIX_ONLY = {
    "/dev/stdin": "a Windows program reads it as a relative path off the current drive",
    "/dev/null": "no /dev at all",
    ".venv/bin/": "a virtualenv puts its programs in Scripts/ with an .exe there",
    "/tmp/": "RUNNER_TEMP is the portable name for it",  # noqa: S108 (named to be refused)
}


def test_no_step_that_runs_on_windows_names_a_posix_only_path() -> None:
    offences = []
    for _, name, job in all_jobs():
        if WINDOWS not in runners(job, name):
            continue
        for step in steps(job):
            body = str(step.get("run", "")) + str(step.get("env", ""))
            if step.get("if", "") and "Linux" in str(step["if"]):
                continue
            for path, why in POSIX_ONLY.items():
                if path in body:
                    offences.append(f"{name}: {path} ({why})")
    assert not offences, "steps that run on Windows name paths only Linux has: " + "; ".join(
        offences
    )


# ------------------------------------------------------- 6. the composite actions are well formed
#
# Their `uses:` lines are held to a commit SHA by test_versions.py, which reads all of `.github`
# rather than only the workflows: one rule, in the file that already owned it.


def test_there_is_at_least_one_composite_action_to_check() -> None:
    """The three rules below iterate. Empty, they would all pass on a repository with none."""
    assert composite_actions(), "no composite actions found, so the rules below would prove nothing"


@pytest.mark.parametrize("action", composite_actions(), ids=lambda p: p.parent.name)
def test_every_composite_action_is_well_formed(action: Path) -> None:
    """actionlint reads a workflow and cannot read one of these: handed an action.yml it reports a
    missing `jobs` section. This stands in its place."""
    body = yaml.safe_load(action.read_text(encoding="utf-8"))
    where = f"{action.parent.name}/action.yml"

    assert body.get("name"), f"{where} has no name"
    assert body.get("description"), f"{where} has no description"
    assert body["runs"]["using"] == "composite", f"{where} is not a composite action"

    listed = body["runs"].get("steps")
    assert listed, f"{where} declares no steps"
    for index, step in enumerate(listed):
        assert "shell" in step or "uses" in step, (
            f"{where} step {index} runs a script with no `shell:`. A composite action does not "
            "inherit one, and the failure is at the moment somebody depends on it."
        )


@pytest.mark.parametrize("action", composite_actions(), ids=lambda p: p.parent.name)
def test_every_declared_input_is_used(action: Path) -> None:
    """An input nobody reads is a caller passing something into nothing, and it looks exactly like
    configuration that works."""
    text = action.read_text(encoding="utf-8")
    body = yaml.safe_load(text)
    for name in body.get("inputs", {}):
        assert f"inputs.{name}" in text, (
            f"{action.parent.name}/action.yml declares the input {name!r} and never reads it"
        )


# --------------------------------------------------------------- 7. every check is run by something

#: Checks with no place in a build, and the place they do have. Named rather than detected: a check
#: nothing runs is indistinguishable from a check that passes.
RUN_OUT_OF_BAND = {
    "check_loop_under_load.py": "a timing measurement under real load; a shared runner cannot host it",
    "check_tunnel_egress.py": "needs a real tunnel and the downloader tools; run out of band",
    # It cannot fail on Windows (no fork handlers), so a runner here would be a green light over
    # nothing; it runs out of band on a system that forks instead.
    "check_launch_survives_load.py": "a fork-family launch under a loaded pool; only fork can fail it",
    # It asks the vulnerability database and the source's release list about the tunnel client
    # built here, so its answer changes with the day and not with a commit; a scheduled run asks.
    "check_tunnel_client.py": "needs the network and the built tunnel client; a scheduled run asks",
}

#: What is allowed to run a check.
RUNNERS = (
    Path("scripts") / "ci-local.sh",
    Path(".pre-commit-config.yaml"),
)


def what_runs_checks() -> str:
    sources = [REPO / rel for rel in RUNNERS] + workflow_files()
    return "\n".join(path.read_text(encoding="utf-8") for path in sources)


#: npm gates a build is not expected to name, and why. One entry, and it is not an exemption from
#: being run: both halves of it ARE run, as separate steps, so that a failure says which of the two
#: it was. The script exists as the entry point to run by hand: it is how a new coverage baseline
#: is recorded, and its own error message says so.
NPM_GATES_RUN_IN_PARTS = {
    "gate:client-coverage": "the suite and the ratchet are separate steps in the build",
}


def test_every_npm_gate_is_run_by_something() -> None:
    """A gate script sitting in package.json and named by nothing is a gate that does not exist."""
    import json

    wired = what_runs_checks()
    orphans = []
    for workspace in ("frontend", "desktop"):
        scripts = json.loads((REPO / workspace / "package.json").read_text(encoding="utf-8"))
        for name in scripts.get("scripts", {}):
            if not name.startswith("gate:"):
                continue
            if name in wired or name in NPM_GATES_RUN_IN_PARTS:
                continue
            orphans.append(f"{workspace}: npm run {name}")
    assert not orphans, "these gates are declared and run by nothing: " + "; ".join(orphans)


def test_a_gate_run_in_parts_really_is_run_in_parts() -> None:
    """The excuse above is that the halves are run separately. If they stop being, it is just an
    orphan with a note attached."""
    import json

    wired = what_runs_checks()
    scripts = json.loads((REPO / "frontend" / "package.json").read_text(encoding="utf-8"))[
        "scripts"
    ]
    for name in NPM_GATES_RUN_IN_PARTS:
        assert name in scripts, f"NPM_GATES_RUN_IN_PARTS names {name}, which is gone"
        for half in scripts[name].split("&&"):
            command = half.strip().removeprefix("npm run ").strip()
            assert command in wired, (
                f"{name} is excused because its halves run separately, and {command!r} does not"
            )


def test_every_check_script_is_run_by_something() -> None:
    wired = what_runs_checks()
    orphans = [
        found.name
        for found in sorted((REPO / "scripts").glob("check_*"))
        if found.name not in wired and found.name not in RUN_OUT_OF_BAND
    ]
    assert not orphans, (
        "these checks are run by nothing: "
        + ", ".join(orphans)
        + ". Wire one in, or name it in RUN_OUT_OF_BAND with where it does belong."
    )


def test_the_out_of_band_list_has_not_gone_stale() -> None:
    """An excuse for a file that no longer exists reads as coverage nobody has."""
    for name in RUN_OUT_OF_BAND:
        assert (REPO / "scripts" / name).is_file(), (
            f"RUN_OUT_OF_BAND excuses {name}, which is not there any more"
        )


# ------------------------------------------------------------ 8. the suite's shards and its minutes


def test_every_test_file_is_in_exactly_one_shard() -> None:
    """A file no shard runs is a test that passes by not existing."""
    shards = shard_module()
    files = shards.collected_files()
    assert len(files) > 300, f"only {len(files)} test files found: the reader has stopped reading"
    for count in (1, 7, 12):
        parts = [shards.shard(files, index, count) for index in range(count)]
        placed = [name for part in parts for name in part]
        assert sorted(placed) == sorted(set(files) - set(shards.ALONE)), count
        assert all(parts), f"an empty shard of {count}"
    for name in shards.ALONE:
        assert name in files, f"ALONE names {name}, which is not a test file here"


def test_the_suite_hands_each_shard_its_place_and_runs_the_rest_alone() -> None:
    """The count is the matrix's length, read by the job itself, so there is no second copy of it."""
    jobs = workflow(WORKFLOWS / "suite.yml")["jobs"]
    shard_step = " ".join(str(step.get("run", "")) for step in steps(jobs["tests"]))
    assert "shard_tests.py" in shard_step, "the shards do not ask shard_tests.py for their files"
    assert "strategy.job-index" in shard_step and "strategy.job-total" in shard_step
    alone_step = " ".join(str(step.get("run", "")) for step in steps(jobs["authz"]))
    assert "shard_tests.py --alone" in alone_step, "nothing runs the files kept out of the shards"


@pytest.mark.parametrize("file", ["suite.yml", "codeql.yml"])
def test_the_long_workflows_spend_nothing_while_private(file: str) -> None:
    """A private copy (a fork made private, say) has a monthly allowance one suite run would take a
    large share of, and code scanning cannot store results there at all. A job skipped by its `if`
    starts no runner, so these cost nothing there unless somebody dispatches them."""

    for name, job in workflow(WORKFLOWS / file)["jobs"].items():
        condition = str(job.get("if", ""))
        gated = PUBLIC_OR_DISPATCHED in condition or "!github.event.repository.private" in condition
        by_hand = condition == "github.event_name == 'workflow_dispatch'"
        assert gated or by_hand, f"{file}:{name} runs on a private repository without being asked"


def test_quick_runs_on_every_pull_request_and_is_never_gated() -> None:
    body = workflow(WORKFLOWS / "quick.yml")
    assert "pull_request" in triggers(body) and "push" in triggers(body)
    for name, job in body["jobs"].items():
        assert "if" not in job, f"quick.yml:{name} can be skipped, and a skipped check reads green"


# ------------------------------------------------------- 9. the local runner's default is quick


def _gate_pytest(text: str) -> list[str]:
    return re.findall(r"pytest tests/gates[^\n]*", text)


def test_the_local_default_is_what_quick_runs() -> None:
    local = LOCAL.read_text(encoding="utf-8")
    assert re.search(r'TARGET="\$\{1:-quick\}"', local), "ci-local.sh no longer defaults to quick"
    for name in ("--all", "run_quick", "job_gates"):
        assert name in local, f"ci-local.sh has no {name}"


def test_the_gate_tests_leave_out_the_same_files_in_both_runners() -> None:
    """The local quick run and quick.yml run tests/gates with the same exclusions."""
    local = _gate_pytest(LOCAL.read_text(encoding="utf-8"))
    remote = _gate_pytest((WORKFLOWS / "quick.yml").read_text(encoding="utf-8"))
    assert local and remote, "one of the runners no longer runs tests/gates"

    def ignored(lines: list[str]) -> set[str]:
        return {found for line in lines for found in re.findall(r"--ignore=([^\s;]+)", line)}

    assert ignored(local) == ignored(remote), (ignored(local), ignored(remote))


def test_both_runners_run_the_same_tier_of_the_gates() -> None:
    """Quick runs the gates marked `unit`; the rest wait for the whole run (see 10)."""
    local = _gate_pytest(LOCAL.read_text(encoding="utf-8"))
    remote = _gate_pytest((WORKFLOWS / "quick.yml").read_text(encoding="utf-8"))

    def selected(lines: list[str]) -> set[str]:
        return {found for line in lines for found in re.findall(r"-m (\w+)", line)}

    assert selected(local) == selected(remote) == {"unit"}, (selected(local), selected(remote))


def test_quick_reads_the_windows_branches() -> None:
    """The one Windows-specific check that runs on Linux, and only because mypy can be told."""
    for path in (WORKFLOWS / "quick.yml", LOCAL):
        assert "mypy --platform win32" in path.read_text(encoding="utf-8"), path.name


# -------------------------------------------------------- 10. the gates quick runs, and the rest

GATES = REPO / "tests" / "gates"

#: Gate files that wait for the whole run (the full suite: `scripts/ci-local.sh --all` and
#: `suite.yml`) rather than running on every push, each with what makes it slow. Listed rather than
#: detected, so a new gate is placed on purpose: marked `unit`, or named here.
WHOLE_RUN_ONLY: dict[str, str] = {
    "test_a_cover_write_answers_with_the_row.py": "builds the application",
    "test_a_kept_filter_names_things_by_id.py": "builds a library and runs its schema steps",
    "test_a_point_read_is_not_a_library_read.py": "opens a database",
    "test_a_refusal_takes_back_what_it_filed.py": "builds a library from every component",
    "test_a_site_reaches_the_same_files_everywhere.py": "opens a database",
    "test_authz_matrix.py": "every route put to every role; most of the gates' wall clock",
    "test_ci_budget_env.py": "starts a shell for every question",
    "test_commit_message_and_root_entries.py": "runs git over a planted repository",
    "test_diagnostics_conformance.py": "boots the application",
    "test_every_count_hides_what_the_vault_hides.py": "boots the application over a library",
    "test_every_route_is_reachable.py": "builds the application",
    "test_every_store_lets_go.py": "opens databases",
    "test_every_write_records_an_event.py": "four walks of every write in the tree, seconds each",
    "test_gates.py": "runs git, the secret scanner and the code scanner",
    "test_history_names_what_it_knows.py": "opens a database",
    "test_insights_says_what_it_can_prove.py": "boots the application over a library",
    "test_licenses.py": "starts the license checker as a process",
    "test_native_chrome.py": "starts node",
    "test_no_dead_css.py": "starts node",
    "test_no_dead_job_types.py": "boots the application",
    "test_no_setting_that_does_nothing.py": "builds the application",
    "test_no_unreachable_service_work.py": "finds every service method's callers: most of a minute",
    "test_numeric_threads_are_pinned.py": "starts an interpreter for every proof",
    "test_one_page_ceiling.py": "builds the application",
    "test_queue_plans.py": "opens a database",
    "test_schema_shape_is_pinned.py": "opens a database",
    "test_settings_headings_reject.py": "starts node",
    "test_web_gates_reject.py": "starts node for every planted violation",
}


def gate_marks(path: Path) -> set[str]:
    """The marks a gate file puts on every test in it: the names in its `pytestmark`."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            return {
                one.attr
                for one in ast.walk(node.value)
                if isinstance(one, ast.Attribute)
                and isinstance(one.value, ast.Attribute)
                and one.value.attr == "mark"
            }
    return set()


def test_every_gate_is_in_the_quick_tier_or_says_why_not() -> None:
    """A gate nobody placed runs only in the whole run, which is not every push."""
    files = sorted(path.name for path in GATES.glob("test_*.py"))
    assert len(files) > 50, f"only {len(files)} gate files found; the reader has stopped reading"
    tier = {name for name in files if "unit" in gate_marks(GATES / name)}
    assert len(tier) > 50, (
        f"only {len(tier)} gates in the tier; the mark reader has stopped reading"
    )

    unplaced = [name for name in files if name not in tier and name not in WHOLE_RUN_ONLY]
    assert not unplaced, (
        "gate files neither marked `unit` (quick runs them on every push) nor named in "
        f"WHOLE_RUN_ONLY with what makes them wait for the whole run: {unplaced}"
    )
    both = sorted(tier & set(WHOLE_RUN_ONLY))
    assert not both, f"marked `unit` and named in WHOLE_RUN_ONLY; which is it: {both}"
    stale = sorted(set(WHOLE_RUN_ONLY) - set(files))
    assert not stale, f"WHOLE_RUN_ONLY names gate files that are not there: {stale}"


# ---------------------------------------------------------------- 11. a public repository's rules

#: Triggers that run with this repository's secrets and a write token on behalf of a fork.
FORK_WITH_SECRETS = ("pull_request_target", "workflow_run")

#: Event text a stranger writes (a branch name, a title, an input), which a `run:` would execute if
#: it were spliced in; it reaches a script only through `env:`.
SPLICED = re.compile(r"\$\{\{[^}]*\b(?:github\.event\.|github\.head_ref|inputs\.)")

#: The only write permissions any job holds, and why.
WRITES = {
    ("codeql.yml", "analyze"): {"security-events"},  # code scanning stores its results
    ("docs.yml", "deploy"): {"pages", "id-token"},  # a Pages deployment, signed by the run
}


def test_no_trigger_runs_a_fork_with_this_repositorys_secrets() -> None:
    for path in workflow_files():
        found = sorted(set(triggers(workflow(path))) & set(FORK_WITH_SECRETS))
        assert not found, f"{path.name} runs on {found}"


def test_a_workflow_a_pull_request_starts_names_no_secret() -> None:
    for path in workflow_files():
        if "pull_request" in triggers(workflow(path)):
            assert "secrets." not in path.read_text(encoding="utf-8"), path.name


def run_blocks() -> list[tuple[str, str]]:
    """`(where, script)` for every `run:` in a workflow or a composite action."""
    found = [
        (f"{file}:{name}", str(step["run"]))
        for file, name, job in all_jobs()
        for step in steps(job)
        if "run" in step
    ]
    for action in composite_actions():
        for step in yaml.safe_load(action.read_text(encoding="utf-8"))["runs"]["steps"]:
            if "run" in step:
                found.append((action.parent.name, str(step["run"])))
    return found


def test_no_script_splices_in_text_a_stranger_wrote() -> None:
    assert len(run_blocks()) > 30, "the reader has stopped finding the scripts"
    spliced = [where for where, script in run_blocks() if SPLICED.search(script)]
    assert not spliced, f"scripts that splice event text in rather than reading env: {spliced}"


def test_every_job_states_the_least_permissions_it_needs() -> None:
    for path in workflow_files():
        assert workflow(path).get("permissions") == {}, f"{path.name} grants a default"
    for file, name, job in all_jobs():
        granted = job.get("permissions")
        assert isinstance(granted, dict) and granted, f"{file}:{name} states no permissions"
        writes = {scope for scope, level in granted.items() if level == "write"}
        assert writes == WRITES.get((file, name), set()), f"{file}:{name} writes {writes}"


def test_every_job_has_a_time_limit_and_every_workflow_a_concurrency_group() -> None:
    for file, name, job in all_jobs():
        assert isinstance(job.get("timeout-minutes"), int), f"{file}:{name} has no time limit"
    for path in workflow_files():
        assert "group" in workflow(path).get("concurrency", {}), f"{path.name}: no concurrency"


def test_no_checkout_leaves_the_token_on_the_runner() -> None:
    """The token stays in the checkout's git config otherwise, for every later step to read."""
    checkouts = [
        (file, name, step)
        for file, name, job in all_jobs()
        for step in steps(job)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(checkouts) > 5, "the reader has stopped finding the checkouts"
    for file, name, step in checkouts:
        assert (step.get("with") or {}).get("persist-credentials") is False, f"{file}:{name}"


def test_no_composite_action_reads_a_context_it_cannot_see() -> None:
    """A composite action has no `vars` or `secrets` context: a template naming one fails at load,
    before any step runs. A value from either comes in as an input."""
    for path in composite_actions():
        text = path.read_text(encoding="utf-8")
        named = re.findall(r"\$\{\{\s*(vars|secrets)\.", text)
        assert not named, f"{path.parent.name} reads {sorted(set(named))}, which a composite cannot"


def test_the_docs_deploy_only_when_pages_takes_the_build() -> None:
    """A private repository or one with no Pages site published from a workflow builds the site
    and skips the deploy; a deploy that fails there fails every push."""
    jobs = workflow(WORKFLOWS / "docs.yml")["jobs"]
    asked = next(step for step in steps(jobs["build"]) if step.get("id") == "pages")
    assert "::notice::" in asked["run"] and '"workflow"' in asked["run"]
    assert "refs/heads/main" in asked["if"] and "push" in asked["if"]
    assert jobs["deploy"]["if"] == "${{ needs.build.outputs.deploy == 'true' }}"


def test_dependabot_reads_every_lockfile() -> None:
    body = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    npm = {one["directory"] for one in body["updates"] if one["package-ecosystem"] == "npm"}
    locked = {f"/{path.parent.name}" for path in REPO.glob("*/package-lock.json")}
    assert locked and npm == locked, (npm, locked)
    assert any(one["package-ecosystem"] == "uv" for one in body["updates"])


#: What each job of the local whole run (`ci-local.sh --all`) is in the hosted workflows, by a
#: command the job's steps run. A job added to the whole run is placed here on purpose.
LOCAL_JOBS_HOSTED = {
    "job_secrets": ("gitleaks_full_history.sh",),
    "job_hygiene": ("check_repo_hygiene.sh", "check_display_dashes.py", "actionlint"),
    "job_python_static": ("ruff check", "mypy --platform win32", "semgrep scan", "pip-audit"),
    "job_web": ("test:unit:coverage", "gate:structure", "npm audit --audit-level=high"),
    "job_desktop": ("npm run typecheck", "npm run gate:coverage", "npm run gate:audit"),
    "job_schema": ("dump_openapi.py",),
    "job_test": ("shard_tests.py --alone", "test_properties.py", "coverage_gates.sh --shard"),
    "job_e2e": ("npm run test:e2e",),
    "job_wheels": ("check_windows_wheels.py",),
    "job_sbom": ("check_licenses.py",),
    "job_shipped": ("check_ingress_corpus.py", "check_selftest.py"),
}


def test_the_hosted_workflows_reach_every_job_of_the_local_whole_run() -> None:
    local = LOCAL.read_text(encoding="utf-8")
    found = re.search(r"^run_default\(\) \{\n(.*?)^\}", local, re.S | re.M)
    assert found, "ci-local.sh has no run_default"
    jobs = set(re.findall(r"^\s*(job_\w+)", found.group(1), re.M))
    assert jobs == set(LOCAL_JOBS_HOSTED), (jobs, set(LOCAL_JOBS_HOSTED))
    hosted = "\n".join(
        (WORKFLOWS / name).read_text(encoding="utf-8") for name in ("quick.yml", "suite.yml")
    )
    missing = [
        f"{job}: {command}"
        for job, commands in LOCAL_JOBS_HOSTED.items()
        for command in commands
        if command not in hosted
    ]
    assert not missing, f"the whole local run does what no hosted workflow does: {missing}"


def test_the_local_runner_starts_no_bare_python3() -> None:
    """`ci-local.sh` runs on Windows too, where no `python3` is on the PATH; `uv run` starts the
    declared interpreter everywhere."""
    for path in (LOCAL, REPO / "scripts" / "coverage_gates.sh"):
        lines = path.read_text(encoding="utf-8").splitlines()
        bare = [
            n
            for n, line in enumerate(lines, 1)
            if not line.lstrip().startswith("#") and re.search(r"(^|\s)python3\s", line)
        ]
        assert not bare, f"{path.name} starts python3 on lines {bare}"
