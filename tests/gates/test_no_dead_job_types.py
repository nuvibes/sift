# SPDX-License-Identifier: AGPL-3.0-or-later
"""A slice declares no job type nothing runs.

Three questions. A public job-type constant must be used somewhere at all, or it documents a
feature never built (asked loosely, since a job type cannot be told from a payload key by its
spelling). Every type handed to `register_handler` (a job type by definition) must be enqueued by
something, or a written, tested, registered handler never runs on any install. And every
registration must actually run when the application boots.
"""

from __future__ import annotations

import ast
import importlib
import re
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.jobs.worker_pool import registered_job_names
from sift.main import create_app

pytestmark = pytest.mark.gate


def _source_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "src" / "sift"
        if candidate.is_dir():
            return candidate
    raise AssertionError("could not find src/sift from the test file")


def declared_constants(source: str) -> list[str]:
    """The public module-level string constants in a jobs module, factored out for the self-test."""
    tree = ast.parse(source)
    names: list[str] = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not isinstance(statement.value, ast.Constant) or not isinstance(
            statement.value.value, str
        ):
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name) and target.id.isupper() and target.id[0] != "_":
                names.append(target.id)
    return names


def uses(name: str, source: str) -> int:
    """How many times `name` is READ in this source: not its own declaration, not an attribute or a
    longer name (`Origin.WATCH`), and not on a line that assigns it."""
    read = re.compile(rf"(?<![.\w]){re.escape(name)}\b")
    defines = re.compile(rf"^\s*{re.escape(name)}\s*(?::[^=]+)?=(?!=)")
    return sum(len(read.findall(line)) for line in source.splitlines() if not defines.match(line))


def _uses_in_tree(name: str, tree: Path, exclude: Path) -> int:
    """How many times `name` is read across the tree, ignoring the module that declares it."""
    return sum(
        uses(name, path.read_text(encoding="utf-8"))
        for path in tree.rglob("*.py")
        if path != exclude
    )


@pytest.mark.regression
def test_every_declared_job_type_is_used_somewhere() -> None:
    root = _source_root()
    dead: list[str] = []
    for module in sorted(root.glob("slices/*/jobs.py")):
        source = module.read_text(encoding="utf-8")
        for name in declared_constants(source):
            # One use anywhere is enough.
            if uses(name, source) == 0 and _uses_in_tree(name, root, exclude=module) == 0:
                dead.append(f"{module.relative_to(root)}: {name}")

    assert not dead, (
        "these job types are declared and then never registered, enqueued or referred to. They "
        "describe work Sift does not do: " + ", ".join(dead)
    )


def test_the_check_sees_a_public_constant_and_skips_a_private_one() -> None:
    source = textwrap.dedent("""
        SCAN = "scan"
        WATCH = "watch"
        _COOKIE_FILENAME = "cookies.txt"
        RETRIES = 3
    """)
    assert declared_constants(source) == ["SCAN", "WATCH"]


def test_a_declaration_is_never_counted_as_a_use() -> None:
    """Neither its own declaration nor an unrelated name's elsewhere counts as a use."""
    declaring = textwrap.dedent("""
        WATCH = "watch"
    """)
    assert uses("WATCH", declaring) == 0

    an_enum_member_elsewhere = textwrap.dedent("""
        class Origin(StrEnum):
            SCAN = "scan"
            WATCH = "watch"
    """)
    assert uses("WATCH", an_enum_member_elsewhere) == 0


# --- registered, and nothing ever queues one

#: Calls that put work in the queue: from outside, from a running job, deferred until a batch
#: settles, and `_enqueue`, the queue's `enqueue` held by a feature (the music lookup's only way
#: in).
_ENQUEUERS = frozenset(
    {"enqueue", "enqueue_child", "enqueue_many", "enqueue_when_settled", "_enqueue"}
)

#: Keyword arguments that hand one feature's job types to another to enqueue, since a feature never
#: imports a feature. Named one by one so a new seam fails CLOSED until somebody reads it; any
#: keyword would fail open, reading a type as wired that is not.
_WIRING_KEYWORDS = frozenset({"follow_on", "settles_into", "also_if_asked"})

#: A `ScheduledTask` hands its job type to the clock, which queues it on its timer and on Run now.
#: The constructor and the keyword together, since `job_type=` on a read is not an enqueue.
_SCHEDULERS = frozenset({"ScheduledTask"})
_SCHEDULE_KEYWORD = "job_type"

#: The one call that claims a job type. Anything handed to it is a job type by definition.
_REGISTRARS = frozenset({"register_handler"})

#: Job types with a handler that nothing enqueues, each with why it is asked for from outside.
NOT_ENQUEUED_BY_SIFT: dict[str, str] = {}

#: Job types deliberately not claimed at boot, and it should stay EMPTY: a registration that runs
#: only sometimes raises the first time somebody uses it where the condition was false.
NOT_CLAIMED_AT_BOOT: dict[str, str] = {}


def _first_argument_names(source: str, called: frozenset[str] | set[str]) -> set[str]:
    """The first positional argument of every call to one of these functions, as a bare name.

    `queue.enqueue(FACE_SWEEP, ...)` and `enqueue(faces.FACE_SCAN)` both yield the last part. A
    computed first argument yields nothing, a limit a test below plants.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        name = (
            function.attr
            if isinstance(function, ast.Attribute)
            else function.id
            if isinstance(function, ast.Name)
            else None
        )
        if name not in called:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            found.add(first.id)
        elif isinstance(first, ast.Attribute):
            found.add(first.attr)
    return found


def _called_name(call: ast.Call) -> str | None:
    """The bare name a call is made through, plain or through a module."""
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    if isinstance(function, ast.Name):
        return function.id
    return None


def _wired_through_keywords(source: str) -> set[str]:
    """Job types handed to another feature to enqueue, as `follow_on=(...)` and its like."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == _SCHEDULE_KEYWORD and _called_name(node) in _SCHEDULERS:
                carried = keyword.value
                if isinstance(carried, ast.Name):
                    found.add(carried.id)
                elif isinstance(carried, ast.Attribute):
                    found.add(carried.attr)
                continue
            if keyword.arg not in _WIRING_KEYWORDS:
                continue
            listed = keyword.value
            if not isinstance(listed, ast.Tuple | ast.List):
                continue
            for item in listed.elts:
                if isinstance(item, ast.Name):
                    found.add(item.id)
                elif isinstance(item, ast.Attribute):
                    found.add(item.attr)
    return found


def _across_the_tree(root: Path, called: frozenset[str] | set[str]) -> set[str]:
    found: set[str] = set()
    for path in root.rglob("*.py"):
        # A slice's own tests enqueue its jobs to exercise a handler; that is not the application.
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        found |= _first_argument_names(source, called)
        if called is not _REGISTRARS:
            found |= _wired_through_keywords(source)
    return found


@pytest.mark.regression
def test_every_registered_job_type_is_enqueued_by_something() -> None:
    """Every registered job type is enqueued by something outside the tests: a handler only its own
    test runs has never run for a user."""
    root = _source_root()
    registered = _across_the_tree(root, _REGISTRARS)
    enqueued = _across_the_tree(root, _ENQUEUERS)

    dead = sorted(name for name in registered - enqueued if name not in NOT_ENQUEUED_BY_SIFT)

    assert not dead, (
        "\nThese job types have a handler and nothing in Sift ever puts one in the queue.\n\n"
        "The work is written, registered and tested, and has never run on any install, which\n"
        "looks exactly like a working feature from every angle except using it. Either enqueue it\n"
        "from wherever it belongs, or add it to NOT_ENQUEUED_BY_SIFT with the reason.\n\n  "
        + "\n  ".join(dead)
        + "\n"
    )


def test_the_excuses_are_all_still_registered_job_types() -> None:
    """No excuse outlives the job type it excused."""
    registered = _across_the_tree(_source_root(), _REGISTRARS)
    stale = sorted(set(NOT_ENQUEUED_BY_SIFT) - registered)
    assert not stale, f"no handler is registered for these any more: {stale}"


def test_the_check_reads_both_spellings_of_a_job_type_and_ignores_a_computed_one() -> None:
    """Both spellings of a job type are read, and a computed one is not."""
    source = textwrap.dedent("""
        queue.enqueue(FACE_SWEEP, {"offset": 0})
        await context.enqueue_child(faces.FACE_SCAN, {"asset_id": one})
        await queue.enqueue(f"face_{suffix}")
        await self._enqueue(MUSIC_LOOKUP, {"asset_id": one}, dedupe=True)
        await queue.enqueue_many(CREATOR_PICTURE, payloads)
        await self._enqueued(NOT_A_JOB)
        register_handler(FACE_REGROUP, handler)
    """)
    assert _first_argument_names(source, _ENQUEUERS) == {
        "FACE_SWEEP",
        "FACE_SCAN",
        "MUSIC_LOOKUP",
        "CREATOR_PICTURE",
    }
    assert _first_argument_names(source, _REGISTRARS) == {"FACE_REGROUP"}


def test_a_scheduled_task_enqueues_its_job_type_and_a_read_naming_one_does_not() -> None:
    """A `ScheduledTask(job_type=X)` is the enqueue of X; a `job_type=` on any other call is a
    read."""
    wired = _wired_through_keywords(
        textwrap.dedent("""
            register_schedule(ScheduledTask(id="scan", job_type=LIBRARY_SCAN, every=None))
            register_schedule(jobs.ScheduledTask(id="prune", job_type=search.SEARCH_EVENTS_PRUNE))
            await queue.waiting(job_type=FACE_SWEEP)
        """)
    )
    assert wired == {"LIBRARY_SCAN", "SEARCH_EVENTS_PRUNE"}


def test_a_real_use_is_counted() -> None:
    enqueued = textwrap.dedent("""
        SCAN = "scan"
        register_handler(SCAN, handle)
    """)
    assert uses("SCAN", enqueued) == 1
    assert uses("SCAN", "verify(origin=Origin.SCAN)") == 0


# --- and the third question: does the registration ever RUN


def _module_name(path: Path) -> str:
    """`src/sift/slices/player/jobs.py` -> `sift.slices.player.jobs`."""
    return ".".join(path.relative_to(_source_root().parent).with_suffix("").parts)


def _declared_job_types() -> dict[str, str]:
    """Every job type a slice registers a handler for, `{type: where declared}`, resolved through
    its module, since the queue holds the VALUE."""
    found: dict[str, str] = {}
    for path in sorted(_source_root().rglob("*.py")):
        if "tests" in path.parts:
            continue
        names = _first_argument_names(path.read_text(encoding="utf-8"), _REGISTRARS)
        if not names:
            continue
        module = importlib.import_module(_module_name(path))
        for name in sorted(names):
            value = getattr(module, name, None)
            if isinstance(value, str):
                found[value] = f"{_module_name(path)}.{name}"
    return found


@pytest.mark.regression
def test_every_declared_handler_is_actually_claimed_when_the_app_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The started application claims every declared job type.

    A registration can be present in the source and never run: a `return` above it, an `if` never
    true, a removed build step. Only building the application and reading what it claimed shows it.
    """
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    try:
        # STARTED, not merely built: the build steps run inside the lifespan.
        with TestClient(create_app()):
            claimed = set(registered_job_names())
    finally:
        get_settings.cache_clear()

    declared = _declared_job_types()
    unclaimed = sorted(
        f"{job_type}  (declared at {where})"
        for job_type, where in declared.items()
        if job_type not in claimed and job_type not in NOT_CLAIMED_AT_BOOT
    )

    assert not unclaimed, (
        "\nThese job types have a handler in the source and nothing claimed one when the\n"
        "application was built. Anything that enqueues one gets `UnknownJobType`, which is a\n"
        "feature that raises the first time somebody uses it, and looks perfect from every angle\n"
        "except that one.\n\n  " + "\n  ".join(unclaimed) + "\n"
    )


def test_the_boot_excuses_are_all_still_declared_job_types() -> None:
    """No boot excuse outlives the job type it excused."""
    declared = set(_declared_job_types())
    stale = sorted(set(NOT_CLAIMED_AT_BOOT) - declared)

    assert not stale, (
        "\nNOT_CLAIMED_AT_BOOT names job types that no longer have a handler at all:\n\n  "
        + "\n  ".join(stale)
        + "\n"
    )
