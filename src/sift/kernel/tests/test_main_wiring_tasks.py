# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run now and what a start queues, and the swap's seams, as the composition root wires them."""

from __future__ import annotations

import ast
import inspect
import re

# The NAME only, never a connection, for the same reason `wiring/lifespan.py` carries this. `sqlite3.Error`
# is what the settings converger catches, and the test below proves that arm is load-bearing by
# raising one; nothing here opens a database.
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from sift.composition import SwapFaceDescriptions
from sift.kernel import wiring
from sift.kernel.access import Role, Viewer
from sift.kernel.jobs.families import Family
from sift.kernel.tests.test_main_wiring import (
    _Hub,
)
from sift.slices import (
    faces,
    importing,
    search,
    stash_boxes,
    swap,
    tasks,
    update_notify,
)
from sift.slices.dedup import service as dedup_service
from sift.slices.importing import JOBS_AT_ONCE
from sift.slices.swap import diff as swap_diff
from sift.slices.swap import offer as swap_offer
from sift.wiring import (
    products,
    swapping,
)
from sift.wiring import tasks as task_wiring

# --- Run now, and what a start queues ------------------------------------------------------------


class _Product:
    def __init__(
        self, key: str, family: Any, refuses: str | None = None, *, asks: bool = True
    ) -> None:
        self.key = key
        self.label = key
        self.family = family
        self._refuses = refuses
        self.cannot_run = self._cannot_run if asks else None

    async def _cannot_run(self) -> str | None:
        return self._refuses


class _Products:
    def __init__(self, *products: _Product) -> None:
        self._by_key = {one.key: one for one in products}

    def get(self, key: str) -> _Product | None:
        return self._by_key.get(key)

    def __iter__(self) -> Iterator[_Product]:
        return iter(self._by_key.values())


class _TasksQueue:
    def __init__(self) -> None:
        self.switchboard = SimpleNamespace(
            declare=lambda *_a: None, declare_quiet_hours=lambda _hold: None
        )
        self.enqueued: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def enqueue(self, job_type: str, payload: dict[str, Any], **kwargs: Any) -> str:
        self.enqueued.append((job_type, payload, kwargs))
        return f"job-{len(self.enqueued)}"

    def record_runs_of(self, job_type: str, *, task_id: str, title: str) -> None:
        """The tasks whose runs are written down as their own lines; nothing here reads them."""


class _Book:
    def __init__(self) -> None:
        self.learned: dict[str, tuple[str, ...]] = {}

    def learn_tasks(self, products: dict[str, tuple[str, ...]]) -> None:
        self.learned = dict(products)


class _Boxes:
    def __init__(self, nobody: str | None = None) -> None:
        self.nobody = nobody
        self.asked: list[str] = []

    async def cannot_ask(self, box: str) -> str | None:
        self.asked.append(box)
        return self.nobody


async def _built_tasks(
    monkeypatch: pytest.MonkeyPatch,
    hub: _Hub,
    *,
    products: _Products,
    book: _Book | None = None,
    boxes: _Boxes | None = None,
    parts: dict[str, Any] | None = None,
    store: Any = None,
) -> tuple[dict[str, Any], _TasksQueue]:
    """Run the tasks' assembly with every part stood in, and keep what it handed the service."""
    handed: dict[str, Any] = {}

    class _Service:
        quiet_hold = None

        def __init__(self, **kwargs: Any) -> None:
            handed.update(kwargs)

        async def rehearse(self, *_a: Any, **_kw: Any) -> str:
            return ""

    class _Clock:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            self.ensured: list[tuple[str, int | None, bool]] = []
            handed["clock"] = self

        async def ensure_all(self) -> None:
            return None

        async def ensure(
            self, task_id: str, *, since: int | None = None, move: bool = False
        ) -> None:
            self.ensured.append((task_id, since, move))

    monkeypatch.setattr(update_notify, "register_handlers", lambda **_kw: None)
    # The dry run's handler goes on the process-wide registry, which refuses a second claim: one
    # assembly per test, so each one stands the registration in.
    monkeypatch.setattr(tasks, "register_handlers", lambda *_a: None)
    monkeypatch.setattr(task_wiring, "TaskClock", _Clock)
    monkeypatch.setattr(task_wiring, "install_task_clock", lambda _clock: None)
    monkeypatch.setattr(tasks, "TasksService", _Service)
    monkeypatch.setattr(task_wiring, "registered_schedules", dict)
    monkeypatch.setattr(task_wiring, "provide", lambda *_a: None)
    state: dict[str, Any] = {
        importing.PRODUCTS.name: products,
        update_notify.SERVICE.name: None,
        stash_boxes.SERVICE.name: boxes or _Boxes(),
    }
    if book is not None:
        state[wiring.LEDGER.name] = book
    state.update(parts or {})
    queue = _TasksQueue()
    store = store or SimpleNamespace(content="content", database="database")
    await task_wiring.build_tasks(
        cast(Any, SimpleNamespace(state=SimpleNamespace(**state))),
        cast(Any, store),
        cast(Any, queue),
        cast(Any, hub),
    )
    return handed, queue


def _runs_started(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    started: list[list[str]] = []

    async def start_runs(keys: list[str], **_kw: Any) -> tuple[int, list[str]]:
        started.append(list(keys))
        return 0, [f"run-{key}" for key in keys]

    monkeypatch.setattr(importing, "start_runs", start_runs)
    return started


_PRESSER = Viewer(id="u1", role=Role.ADMIN)


async def test_run_now_builds_only_the_products_that_can_run_and_says_why_when_none_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A product that cannot run is left out of the Build and its sentence is kept for the case
    where nothing could; a product the registry does not hold is passed over."""
    from sift.kernel.jobs.quiet_hours import AT_NOW

    started = _runs_started(monkeypatch)
    generate = Family.GENERATE
    handed, _queue = await _built_tasks(
        monkeypatch,
        _Hub(),
        products=_Products(
            _Product("thumbnails", generate, asks=False),
            _Product("previews", generate, "Previews cannot be made on this machine."),
            _Product(faces.PRODUCT, Family.IDENTIFY, "Recognition needs its models."),
            _Product("meaning", Family.IDENTIFY),
        ),
    )
    starters = handed["starters"]

    assert await starters["generate"](AT_NOW, _PRESSER) == ["run-thumbnails"]
    assert await starters["smart-search"](AT_NOW, _PRESSER) == ["run-meaning"]
    with pytest.raises(tasks.TaskRefused, match=re.escape("Recognition needs its models.")):
        await starters["faces"](AT_NOW, _PRESSER)
    # Nothing registered for watermarks: no refusal to give, and nothing to build.
    assert await starters["watermarks"](AT_NOW, _PRESSER) == []
    assert started == [["thumbnails"], ["meaning"], []]


async def test_run_now_for_enrichment_is_refused_while_off_or_with_nobody_to_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Started, it would answer "Started" and walk the library asking nobody."""
    from sift.kernel.jobs.quiet_hours import AT_NOW

    handed, queue = await _built_tasks(
        monkeypatch, _Hub(), products=_Products(), boxes=_Boxes(nobody="Every box is off.")
    )
    with pytest.raises(tasks.TaskRefused, match=re.escape(stash_boxes.ENRICHING_OFF)):
        await handed["starters"]["enrichment"](AT_NOW, _PRESSER)

    async def every_box(_hub: object, _named: str | None) -> str:
        return ""

    monkeypatch.setattr(stash_boxes, "box_for", every_box)
    on = _Hub(**{stash_boxes.SCAN_KEY: True})
    handed, queue = await _built_tasks(
        monkeypatch, on, products=_Products(), boxes=_Boxes(nobody="Every box is off.")
    )
    with pytest.raises(tasks.TaskRefused, match=re.escape("Every box is off.")):
        await handed["starters"]["enrichment"](AT_NOW, _PRESSER)
    assert [job for job, _payload, _kw in queue.enqueued if job == stash_boxes.STASH_SWEEP] == []


async def test_run_now_for_enrichment_queues_a_pressed_sweep_of_the_box_it_resolves_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A press carries the box and applies certain matches (without them a Run now would run as an
    unattended sweep), and waits ahead of the queue only when it is for now."""
    from sift.kernel.jobs import DEFAULT_PRIORITY, WAITED_ON_PRIORITY
    from sift.kernel.jobs.quiet_hours import AT_NOW, AT_QUIET

    async def the_auto_box(_hub: object, _named: str | None) -> str:
        return "stashdb"

    monkeypatch.setattr(stash_boxes, "box_for", the_auto_box)
    boxes = _Boxes()
    handed, queue = await _built_tasks(
        monkeypatch, _Hub(**{stash_boxes.SCAN_KEY: True}), products=_Products(), boxes=boxes
    )
    queue.enqueued.clear()

    await handed["starters"]["enrichment"](AT_NOW, _PRESSER)
    await handed["starters"]["enrichment"](AT_QUIET, _PRESSER)

    assert boxes.asked == ["stashdb", "stashdb"]
    payload = {"viewer": "u1", "offset": 0, "queued": 0, "box": "stashdb", "apply": True}
    assert queue.enqueued == [
        (
            stash_boxes.STASH_SWEEP,
            payload,
            {"priority": WAITED_ON_PRIORITY, "requested_by": "u1", "at": AT_NOW},
        ),
        (
            stash_boxes.STASH_SWEEP,
            payload,
            {"priority": DEFAULT_PRIORITY, "requested_by": "u1", "at": AT_QUIET},
        ),
    ]


async def test_run_now_for_scan_walks_each_folder_asked_for_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.kernel.jobs import DEFAULT_PRIORITY, WAITED_ON_PRIORITY
    from sift.kernel.jobs.quiet_hours import AT_NOW, AT_QUIET

    handed, queue = await _built_tasks(monkeypatch, _Hub(), products=_Products())
    queue.enqueued.clear()
    scan = handed["starters"]["scan"]

    assert await scan(AT_NOW, _PRESSER, tasks.Selection(locations=("r1", "r2"))) == [
        "job-1",
        "job-2",
    ]
    assert await scan(AT_QUIET, _PRESSER, tasks.Selection(locations=("r3",))) == ["job-3"]
    assert await scan(AT_NOW, _PRESSER) == [], "a whole Scan is its own job, not this"
    assert [(payload["root_id"], kw["priority"]) for _job, payload, kw in queue.enqueued] == [
        ("r1", WAITED_ON_PRIORITY),
        ("r2", WAITED_ON_PRIORITY),
        ("r3", DEFAULT_PRIORITY),
    ]
    assert all(payload["scan_only"] for _job, payload, _kw in queue.enqueued)


class _Lookups:
    def __init__(self, answer: object) -> None:
        self._answer = answer
        self.asked: list[dict[str, Any]] = []

    async def start_catch_up(self, **kwargs: Any) -> str | None:
        self.asked.append(kwargs)
        if isinstance(self._answer, BaseException):
            raise self._answer
        return cast("str | None", self._answer)


async def test_run_now_for_song_names_starts_one_walk_says_nothing_to_run_or_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.kernel.jobs.quiet_hours import AT_NOW
    from sift.slices import music

    async def press(answer: object) -> tuple[list[str], _Lookups]:
        lookups = _Lookups(answer)
        handed, _queue = await _built_tasks(
            monkeypatch,
            _Hub(),
            products=_Products(),
            parts={music.LOOKUP_STARTER.name: lookups},
        )
        return await handed["starters"][music.LOOKUP_TASK](AT_NOW, _PRESSER), lookups

    started, lookups = await press("walk-1")
    assert started == ["walk-1"] and lookups.asked[0]["requested_by"] == "u1"
    assert (await press(None))[0] == []
    with pytest.raises(tasks.TaskRefused, match="No key"):
        await press(music.LookupNotReady("No key is saved for AcoustID."))


async def test_the_tasks_read_their_folders_and_load_a_dry_runs_viewer_through_the_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def roots() -> list[Any]:
        return [SimpleNamespace(id="r1", name="Home", abs_path="C:/Home")]

    async def load_viewer(user_id: str) -> Viewer | None:
        return _PRESSER if user_id == "u1" else None

    store = SimpleNamespace(
        content="content",
        database="database",
        library=SimpleNamespace(roots=roots),
        access=SimpleNamespace(load_viewer=load_viewer),
    )
    handed, _queue = await _built_tasks(monkeypatch, _Hub(), products=_Products(), store=store)

    assert await handed["folders"]() == [tasks.TaskPart(key="r1", label="Home", path="C:/Home")]
    assert await handed["viewer_for"]("u1") is _PRESSER
    assert await handed["viewer_for"]("gone") is None


async def test_a_builds_dry_run_counts_what_can_run_and_names_the_first_files_it_meets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named in the order the pass's pages walk, a file the viewer cannot see or with no place
    passed over, and no page read past the last name needed."""
    monkeypatch.setattr(task_wiring, "PAGE", 4)
    monkeypatch.setattr(task_wiring, "NAMED", 3)
    pages = {
        None: SimpleNamespace(ids=["a0", "a1", "a2", "a3"], last="a3"),
        "a3": SimpleNamespace(ids=["b0", "b1", "b2", "b3"], last="b3"),
    }
    walked: list[object] = []

    async def asset_ids_page(*, after: str | None, limit: int, roots: object) -> Any:
        walked.append(after)
        return pages[after]

    async def locations_of(ids: list[str]) -> dict[str, list[Any]]:
        return {one: [SimpleNamespace(filename=f"{one}.mp4")] for one in ids if one != "a2"}

    async def visible_of(_viewer: object, ids: list[str]) -> set[str]:
        return {one for one in ids if one != "a1"}

    async def lacking_on_page(
        _products: object, keys: list[str], ids: list[str], _c: object
    ) -> Any:
        return SimpleNamespace(by_file={one: keys for one in ids if one != "a3"})

    async def count_lacking(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(files=7, each={"thumbs": 7})

    monkeypatch.setattr(task_wiring, "lacking_on_page", lacking_on_page)
    monkeypatch.setattr(task_wiring, "count_lacking", count_lacking)
    store = SimpleNamespace(
        content=SimpleNamespace(asset_ids_page=asset_ids_page, locations_of=locations_of),
        access=SimpleNamespace(visible_of=visible_of),
    )
    products = _Products(
        _Product("thumbs", Family.OTHER), _Product("previews", Family.OTHER, "No encoder here.")
    )
    builds = task_wiring._Builds(cast(Any, products), cast(Any, store), cast(Any, _TasksQueue()))

    planned = await builds.plan_of("thumbs", "previews", "retired")(tasks.EVERYTHING, _PRESSER)

    assert planned.files == 7
    assert planned.lines == (tasks.PlanLine(label="thumbs", count=7),)
    assert planned.refusals == ("No encoder here.",)
    assert planned.names == ("a0.mp4", "b0.mp4", "b1.mp4")
    assert walked == [None, "a3"]

    # A library smaller than a page: its one page is the end of the walk, named or not.
    pages[None] = SimpleNamespace(ids=["a0", "a1"], last="a1")
    walked.clear()
    planned = await builds.plan_of("thumbs")(tasks.EVERYTHING, _PRESSER)
    assert planned.names == ("a0.mp4",)
    assert walked == [None]


async def test_a_builds_dry_run_with_nothing_lacking_names_nothing_and_walks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def count_lacking(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(files=0, each={})

    monkeypatch.setattr(task_wiring, "count_lacking", count_lacking)
    store = SimpleNamespace(content=None)
    builds = task_wiring._Builds(
        cast(Any, _Products(_Product("thumbs", Family.OTHER))),
        cast(Any, store),
        cast(Any, _TasksQueue()),
    )

    planned = await builds.plan_of("thumbs")(tasks.Selection(parts=("thumbs",)), _PRESSER)

    assert (planned.files, planned.names) == (0, ())


async def test_a_start_checks_for_an_update_only_when_the_check_starts_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a check at start, the banner says nothing of a release for hours after every
    restart; press-only, a start asks for none. The check is the schedule's own run moved to now,
    never a second row beside it. And the history learns each task's products from the same
    declaration its Run now builds from, when there is a history to learn them."""
    from sift.kernel.jobs.quiet_hours import WHEN_PRESS
    from sift.kernel.jobs.schedules import when_key

    book = _Book()
    handed, automatic = await _built_tasks(monkeypatch, _Hub(), products=_Products(), book=book)
    assert automatic.enqueued == []
    assert handed["clock"].ensured == [("update-check", 0, True)]
    assert book.learned["faces"] == (faces.PRODUCT,)

    handed, pressed = await _built_tasks(
        monkeypatch, _Hub(**{when_key("update-check"): WHEN_PRESS}), products=_Products()
    )
    assert pressed.enqueued == []
    assert handed["clock"].ensured == []
    assert handed["ledger"] is None


# --- the swap's seams ----------------------------------------------------------------------------


class _SwapAccess:
    def __init__(
        self, admin: str | None = "admin-1", viewers: dict[str, Viewer] | None = None
    ) -> None:
        self.admin = admin
        self.viewers = viewers or {}
        self.located: list[tuple[Viewer, str]] = []

    async def an_admin(self) -> str | None:
        return self.admin

    async def load_viewer(self, user_id: str) -> Viewer | None:
        return self.viewers.get(user_id)

    async def locate(self, viewer: Viewer, key: str) -> Path:
        self.located.append((viewer, key))
        return Path("/library") / key


async def _swap_seams(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, access: _SwapAccess
) -> dict[str, Any]:
    """Assemble the swap with its parts stood in, and keep the three seams it hands the session."""
    seams: dict[str, Any] = {}

    class _Sessions:
        def __init__(self, *_a: Any, **kwargs: Any) -> None:
            seams.update(kwargs)

        async def settle_after_restart(self) -> None:
            return None

        async def on_settled(self, *_a: Any) -> None:
            return None

    monkeypatch.setattr(swap, "SwapSessions", _Sessions)
    monkeypatch.setattr(swap, "SessionStore", lambda *_a: None)
    monkeypatch.setattr(swap, "register_handlers", lambda *_a: None)
    monkeypatch.setattr(swapping, "provide", lambda *_a: None)
    app = SimpleNamespace(
        state=SimpleNamespace(**{wiring.REINDEXER.name: None, search.SERVICE.name: "search"})
    )
    queue = SimpleNamespace(listen_for_settled=lambda *_a: None)
    downloads = SimpleNamespace(
        secrets=None,
        tunnels=SimpleNamespace(default_route=None, exit_address=None, server_address=None),
        egress=None,
    )
    await swapping.build_swap(
        cast(Any, app),
        cast(Any, SimpleNamespace(data_dir=tmp_path)),
        cast(Any, SimpleNamespace(access=access, database="database")),
        cast(Any, queue),
        cast(Any, _Hub()),
        cast(Any, downloads),
        cast(Any, SimpleNamespace(faces=None)),
    )
    return seams


async def test_the_swap_offer_is_read_through_the_access_layer_and_the_search_compiler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    access = _SwapAccess()
    seams = await _swap_seams(monkeypatch, tmp_path, access)
    asked: list[tuple[Any, ...]] = []

    async def offer_for(*args: Any, **kwargs: Any) -> str:
        asked.append((*args, kwargs["share_boxes"], type(kwargs["faces"]), kwargs["peer_model"]))
        return "the offer"

    monkeypatch.setattr(swap_offer, "offer_for", offer_for)

    assert await seams["make_offer"](_PRESSER, ["chosen"], True, "theirs") == "the offer"
    assert asked == [
        (access, "database", "search", _PRESSER, ["chosen"], True, SwapFaceDescriptions, "theirs")
    ]


async def test_a_swap_is_assessed_as_an_admin_or_refused_when_there_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guest's reading of what this library already holds is a fact about the library, read as
    an admin with the vault shut, and a device with no admin to read it as cannot receive one."""
    for access in (_SwapAccess(admin=None), _SwapAccess(admin="admin-1")):
        seams = await _swap_seams(monkeypatch, tmp_path, access)
        with pytest.raises(swap.SwapRefused, match="no admin to receive a swap"):
            await seams["assess"]("an offer")

    admin = Viewer(id="admin-1", role=Role.ADMIN)
    access = _SwapAccess(viewers={"admin-1": admin})
    seams = await _swap_seams(monkeypatch, tmp_path, access)

    class _Reads:
        def __init__(self, database: str) -> None:
            assert database == "database"

        async def fingerprints(self) -> list[str]:
            return ["print"]

    async def bound_rule(_hub: object) -> str:
        return "bound"

    assessed: list[tuple[Any, ...]] = []

    async def assess(*args: Any, held: Any, rule: Any) -> str:
        assessed.append((*args, held, rule))
        return "the reading"

    monkeypatch.setattr(swapping, "DuplicateReads", _Reads)
    monkeypatch.setattr(dedup_service, "bound_rule", bound_rule)
    monkeypatch.setattr(swap_diff, "held_from", lambda prints: ("held", tuple(prints)))
    monkeypatch.setattr(swap_diff.NearRule, "from_bound", staticmethod(lambda b: ("rule", b)))
    monkeypatch.setattr(swap_diff, "assess", assess)

    assert await seams["assess"]("an offer") == "the reading"
    assert assessed == [
        ("database", access, admin, "an offer", ("held", ("print",)), ("rule", "bound"))
    ]


async def test_a_swapped_file_is_located_for_the_viewer_as_loaded_now_or_not_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Loaded afresh, so the vault is shut whatever the session held; a viewer who is gone finds
    nothing."""
    fresh = Viewer(id="u1", role=Role.ADMIN)
    access = _SwapAccess(viewers={"u1": fresh})
    seams = await _swap_seams(monkeypatch, tmp_path, access)

    assert await seams["path_of"](_PRESSER, "clip.mp4") == Path("/library") / "clip.mp4"
    assert access.located == [(fresh, "clip.mp4")]
    assert await seams["path_of"](Viewer(id="gone", role=Role.ADMIN), "clip.mp4") is None


def test_the_meaning_product_asks_for_the_shoots_pass_after_a_press() -> None:
    """The shoots pass reads descriptions, and a press of Identify that wrote some is the moment
    to ask for it, the way the fingerprints product asks for the duplicate sweep. Read off the
    declaration itself: the products are registered by name in one module."""
    source = inspect.getsource(products)
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Product"):
            continue
        keywords = {one.arg: one.value for one in node.keywords}
        key = keywords.get("key")
        if not (isinstance(key, ast.Constant) and key.value == "meaning"):
            continue
        settles = keywords.get("settles_into")
        assert settles is not None, "the meaning product settles into nothing"
        assert "shoots.SHOOTS_LOOK" in ast.unparse(settles)
        return
    raise AssertionError("no meaning product is declared")


def test_the_pool_settings_key_is_the_one_the_importing_screen_reads() -> None:
    """`sift/wiring/workers.py` writes the pool's concurrency into every run's settings under the
    key the Importing screen reads back; both spell it through the one name the importing slice
    exports, so a boot cannot fail on a name the package does not carry."""
    from sift.slices import importing
    from sift.wiring import workers

    assert importing.JOBS_AT_ONCE == JOBS_AT_ONCE
    assert "importing.JOBS_AT_ONCE" in inspect.getsource(workers)


async def test_a_guest_dials_through_the_tunnel_chosen_for_joining_and_never_another() -> None:
    """The downloads' route is not a guest's: with no tunnel chosen on the swap screens, the
    dial goes out directly."""
    chosen = swapping._guest_tunnel(cast(Any, _Hub(**{swap.GUEST_TUNNEL_KEY: "t1"})))
    unchosen = swapping._guest_tunnel(cast(Any, _Hub(**{swap.GUEST_TUNNEL_KEY: ""})))

    assert await chosen() == "t1"
    assert await unchosen() is None
