# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work that puts itself back in the queue with a time on it is declared as a scheduled task.

Recurring work is a `jobs` row with `run_after` set, so a machine switched off overnight runs it on
waking; in the source it is an `enqueue` with a keyword, invisible. So a delayed, self-queued job
type must be declared in `sift.kernel.jobs.schedules`, which lists what Sift does on a clock. The
keyword is read, since a job cannot wait without it. `enqueue_when_settled` (a `delay`, waiting
for a batch to stop) does not re-arm and is not a cadence; a job type computed at run time is
listed and turns this red until somebody says which it is.
"""

from __future__ import annotations

import ast
import importlib
import textwrap
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every slice declares its schedules)
from sift.kernel.jobs import schedules
from sift.kernel.jobs.schedules import scheduled_job_types

pytestmark = [pytest.mark.gate, pytest.mark.unit]

#: Calls that put work in the queue, as `test_no_dead_job_types` names them.
_ENQUEUERS = frozenset({"enqueue", "enqueue_child", "enqueue_when_settled"})

#: Delayed job types that are NOT a scheduled task, each with why: waiting is not recurring.
NOT_A_SCHEDULE: dict[str, str] = {}

#: Call sites whose job type is worked out at run time, by file, each with why it is not a schedule.
COMPUTED_AND_NOT_A_SCHEDULE: dict[str, str] = {
    "kernel/jobs/clock.py": (
        "the one scheduler of every timed task. The type it queues is read off a DECLARED task "
        "(`registered_schedules`, filtered to those with an interval), so everything it puts on a "
        "clock is a scheduled task by construction: there is nothing undeclared for it to queue"
    ),
}


#: The queue's own module, which implements waiting with `run_after` and is the MECHANISM.
_THE_MECHANISM = "kernel/jobs/queue_enqueue.py"


def _source_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "src" / "sift"
        if candidate.is_dir():
            return candidate
    raise AssertionError("could not find src/sift from the test file")


def delayed_enqueues(source: str) -> tuple[set[str], int]:
    """The names enqueued WITH a `run_after`, and how many such calls could not be read."""
    names: set[str] = set()
    unreadable = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        called = (
            function.attr
            if isinstance(function, ast.Attribute)
            else function.id
            if isinstance(function, ast.Name)
            else None
        )
        if called not in _ENQUEUERS:
            continue
        if not any(keyword.arg == "run_after" for keyword in node.keywords):
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            names.add(first.id)
        elif isinstance(first, ast.Attribute):
            names.add(first.attr)
        else:
            unreadable += 1
    return names, unreadable


def _module_name(path: Path) -> str:
    """`src/sift/slices/backup/jobs.py` -> `sift.slices.backup.jobs`."""
    return ".".join(path.relative_to(_source_root().parent).with_suffix("").parts)


def _delayed_across_the_tree() -> tuple[dict[str, str], list[str]]:
    """Every delayed job type, where it was queued, and the files with a type chosen at run time."""
    root = _source_root()
    found: dict[str, str] = {}
    computed: list[str] = []
    for path in sorted(root.rglob("*.py")):
        where = str(path.relative_to(root)).replace("\\", "/")
        if "tests" in path.parts or where == _THE_MECHANISM:
            continue
        names, unreadable = delayed_enqueues(path.read_text(encoding="utf-8"))
        if not names and not unreadable:
            continue
        module = importlib.import_module(_module_name(path))
        for name in sorted(names):
            value = getattr(module, name, None)
            if isinstance(value, str):
                found[value] = where
            else:
                computed.append(where)
        if unreadable:
            computed.append(where)
    return found, sorted(set(computed))


@pytest.mark.regression
def test_every_delayed_job_type_is_a_declared_schedule() -> None:
    delayed, _computed = _delayed_across_the_tree()
    declared = scheduled_job_types()
    undeclared = sorted(
        f"{job_type}  (queued from {where})"
        for job_type, where in delayed.items()
        if job_type not in declared and job_type not in NOT_A_SCHEDULE
    )

    assert not undeclared, (
        "\nThese job types are queued with a time on them and no scheduled task declares them.\n\n"
        "Work that runs on a clock without anybody pressing it has to be visible on the Scheduled\n"
        "tasks screen, or it is a thing Sift does that nobody can see, switch off or find out the\n"
        "next run of. Declare it with `register_schedule`, or name it in NOT_A_SCHEDULE with the\n"
        "reason waiting is not the same as recurring.\n\n  " + "\n  ".join(undeclared) + "\n"
    )


@pytest.mark.regression
def test_nothing_delayed_is_queued_by_a_name_this_cannot_read() -> None:
    """Nothing delayed is queued by a name this cannot read."""
    _delayed, computed = _delayed_across_the_tree()
    unread = [where for where in computed if where not in COMPUTED_AND_NOT_A_SCHEDULE]

    assert not unread, (
        "\nThese files queue a delayed job whose type this check cannot resolve, so the check\n"
        "above passed over them without looking. Either enqueue a named constant, or say in\n"
        "COMPUTED_AND_NOT_A_SCHEDULE why what is queued there is not a recurring task.\n\n  "
        + "\n  ".join(unread)
        + "\n"
    )


def test_the_excuses_are_all_still_delayed_job_types() -> None:
    """No excuse outlives what it excused."""
    delayed, computed = _delayed_across_the_tree()
    stale = sorted(set(NOT_A_SCHEDULE) - set(delayed))
    assert not stale, f"nothing queues these with a delay any more: {stale}"
    forgotten = sorted(set(COMPUTED_AND_NOT_A_SCHEDULE) - set(computed))
    assert not forgotten, f"these files no longer queue a computed delayed job: {forgotten}"


# --- the other way work runs on a clock: a loop of the process
#
# A timer the composition root starts is not a job. Each loop the lifespan starts is named by the
# words the `schedules` note ("What is NOT declared here") lists it under, so an unlisted one is
# red.

#: The loops the lifespan starts, by task name, and the words the note lists each by.
PROCESS_LOOPS: dict[str, str] = {
    "db.log_keeper": "The write-ahead checkpoint",
    "db.statistics_keeper": "the statistics refresh",
    "insights.rollup": "slices/insights/rollup.py",
    "tasks.keep_awake": "the keep-awake request",
    "performance.when_quiet": "the look for a quiet moment",
    "settings.applier": "the settings pushed onto the running process",
}

#: The lifespan, where the process's own loops are started.
_COMPOSITION_ROOT = "wiring/lifespan.py"


def loops_started(source: str) -> dict[str, int]:
    """Every `asyncio.create_task(..., name="...")` with a literal name, and its line; a worker's
    own
    task has none."""
    found: dict[str, int] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "create_task":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                found[keyword.value.value] = node.lineno
    return found


def not_declared_note() -> str:
    """The note's "What is NOT declared here" section, its whitespace run together."""
    text = schedules.__doc__ or ""
    start = text.index("## What is NOT declared here")
    end = text.index("\n## ", start + 1)
    return " ".join(text[start:end].split())


def unlisted_loops(started: dict[str, int], note: str) -> list[str]:
    """The loops started that the note does not list, each with why."""
    unlisted = []
    for name, line in sorted(started.items()):
        words = PROCESS_LOOPS.get(name)
        if words is None:
            unlisted.append(f"{name} ({_COMPOSITION_ROOT}:{line}) is not in PROCESS_LOOPS")
        elif words not in note:
            unlisted.append(f"{name}: the schedules note does not list {words!r}")
    return unlisted


@pytest.mark.regression
def test_every_loop_the_process_starts_is_listed_in_the_schedules_note() -> None:
    source = (_source_root() / _COMPOSITION_ROOT).read_text(encoding="utf-8")
    unlisted = unlisted_loops(loops_started(source), not_declared_note())
    assert not unlisted, (
        "\nThese run on a clock inside the process, and nothing says why they are not scheduled\n"
        "tasks. Work on a clock that is not a job is invisible to the Scheduled tasks screen and to\n"
        "the check above, so either make it a task, or name it in the `schedules` note's 'What is\n"
        "NOT declared here' and here, with the words the note uses.\n\n  "
        + "\n  ".join(unlisted)
        + "\n"
    )


def test_every_listed_loop_is_still_started() -> None:
    """No listed loop has stopped being started."""
    source = (_source_root() / _COMPOSITION_ROOT).read_text(encoding="utf-8")
    stale = sorted(set(PROCESS_LOOPS) - set(loops_started(source)))
    assert not stale, f"the process no longer starts these: {stale}"


def test_the_loop_check_refuses_an_unlisted_loop_and_accepts_the_adder_up() -> None:
    """An unlisted clocked loop is refused and Insights' adder-up, listed by its module,
    accepted."""
    source = textwrap.dedent("""
        folding = asyncio.create_task(keep_the_log_folded(db, stop), name="db.log_keeper")
        nightly = asyncio.create_task(back_up_every_night(db, stop), name="backup.nightly")
        adder = asyncio.create_task(insights.keep_the_days_added_up(db, stop), name="insights.rollup")
        worker = asyncio.create_task(self._work(n, stop), name=f"jobs.worker.{n}")
    """)
    started = loops_started(source)
    assert set(started) == {"db.log_keeper", "backup.nightly", "insights.rollup"}
    note = not_declared_note()
    assert unlisted_loops(started, note) == [
        f"backup.nightly ({_COMPOSITION_ROOT}:3) is not in PROCESS_LOOPS"
    ]
    assert "slices/insights/rollup.py" in note
    assert unlisted_loops({"insights.rollup": 1}, "a note that names nothing") == [
        "insights.rollup: the schedules note does not list 'slices/insights/rollup.py'"
    ]


def test_the_check_sees_a_delayed_enqueue_and_ignores_an_ordinary_one() -> None:
    """A delayed enqueue is seen and an ordinary one ignored."""
    source = textwrap.dedent("""
        await queue.enqueue(BACKUP_RUN, run_after=later)
        await queue.enqueue(THUMBNAIL, {"asset_id": one})
        await context.enqueue_child(jobs.SCAN_SETTLE, run_after=soon)
        await queue.enqueue(f"build_{suffix}", run_after=tonight)
    """)
    names, unreadable = delayed_enqueues(source)
    assert names == {"BACKUP_RUN", "SCAN_SETTLE"}
    assert unreadable == 1
