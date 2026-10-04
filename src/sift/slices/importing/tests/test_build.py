# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Build: what it hands out, in what order, and what it says it did.

The products are stand-ins that answer from sets, so every assertion here is about what the pass
ASKED for and what the task CALLED; the real pictures, faces and meaning are each tested where they
are made.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Lack
from sift.kernel.db import Database, in_clause
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.jobs import (
    JobBlocked,
    JobContext,
    JobHeld,
    JobQueue,
    JobState,
    register_handler,
)
from sift.kernel.jobs.families import Family
from sift.kernel.log import set_stage_sink
from sift.slices.importing import jobs as jobs_module
from sift.slices.importing import products as products_module
from sift.slices.importing.jobs import (
    GENERATE,
    GENERATE_FILE,
    IDENTIFY,
    IDENTIFY_FILE,
    _handed_out,
    _page_key,
    build,
    build_file,
)
from sift.slices.importing.products import (
    Product,
    ProductRegistry,
    files_lacking,
    lacking_by_kind,
    lacking_on_page,
)
from sift.slices.importing.router import start_runs
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.anyio

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


async def a_probed_file(
    root: LibraryRoot, content: ContentStore, settings: Settings, name: str
) -> str:
    """A file the library has read. Only a read file is offered anything.

    Its bytes carry its name on the end, so two files made here are two assets: identical bytes
    would be one asset with two locations, and every count in these tests would be one.
    """
    landed = root.path / name
    landed.write_bytes((CORPUS / "accepted.mp4").read_bytes() + name.encode("ascii"))
    checked = verify_ingress(landed, origin=Origin.SCAN, settings=settings)
    ingested = await content.ingest(checked, root_id=root.id, rel_path=name)
    await content.record_probe(ingested.asset.id, width=640, height=480, duration_ms=1000)
    return ingested.asset.id


class Made:
    """A product that lacks for a fixed set of files, and records what it was asked to make."""

    def __init__(
        self,
        key: str,
        lacking: set[str],
        *,
        on: bool = True,
        family: Family = Family.GENERATE,
    ) -> None:
        self.key = key
        self.lacking = set(lacking)
        self.on = on
        #: Which run makes this. A request can tick products of both families and the route starts
        #: one run per family, so a test about that has to be able to say which.
        self.family = family
        self.built: list[str] = []

    async def night(self) -> str:
        return "23:00"

    def product(self) -> Product:
        async def switched_on() -> bool:
            return self.on

        async def lack() -> Lack | None:
            if not self.lacking:
                return Lack("0")
            condition, params = in_clause("a.id IN (?*)", sorted(self.lacking))
            return Lack(condition, tuple(params))

        async def lacking_among(asset_ids: Any) -> set[str]:
            return {one for one in asset_ids if one in self.lacking}

        async def make(context: JobContext) -> None:
            self.built.append(str(context.payload["asset_id"]))
            self.lacking.discard(str(context.payload["asset_id"]))

        return Product(
            family=self.family,
            key=self.key,
            label=self.key.title(),
            help="",
            switched_on=switched_on,
            lack=lack,
            lacking_among=lacking_among,
            build=make,
        )


class Unmeasured:
    """A machine that has not been measured, recording when it is."""

    def __init__(self) -> None:
        self.measured_times = 0
        self.is_measured = False

    async def measured(self) -> bool:
        return self.is_measured

    async def measure(self) -> None:
        self.measured_times += 1
        self.is_measured = True


def registry_of(*made: Made, machine: Unmeasured | None = None) -> ProductRegistry:
    async def night() -> str:
        return "23:00"

    registry = ProductRegistry(night_start=night, machine=machine)
    for one in made:
        registry.register(one.product())
    return registry


async def test_the_pass_hands_out_one_task_per_file_naming_what_it_lacks(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    a, b, c = [
        await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "abc"
    ]
    pictures = Made("pictures", {a, b})
    faces = Made("faces", {b, c})
    registry = registry_of(pictures, faces)

    context = await context_for(GENERATE, {"products": ["pictures", "faces"], "files": 3})
    await build(
        context,
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )

    children = await job_queue.children(context.job.id)
    tasks = {
        str(child.payload["asset_id"]): list(child.payload["products"])
        for child in children
        if child.type == GENERATE_FILE
    }
    assert tasks == {a: ["pictures"], b: ["pictures", "faces"], c: ["faces"]}, (
        "one task per file, naming every ticked product the file lacks, in the sheet's order"
    )
    assert not [child for child in children if child.type == GENERATE], "one page was the library"
    job = await job_queue.get(context.job.id)
    assert job is not None and job.note == "Handed out 3 files to build."


async def test_a_machine_never_measured_is_measured_before_the_first_file(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    """The reads every task makes are shaped by rates only the self-test knows, so the first page
    of a run measures the machine first: once, and only the first page."""
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    machine = Unmeasured()
    registry = registry_of(Made("pictures", {a}), machine=machine)

    first = await context_for(GENERATE, {"products": ["pictures"], "files": 1})
    await build(
        first, content=content_store, products=registry, run_type=GENERATE, file_type=GENERATE_FILE
    )
    assert machine.measured_times == 1
    assert len(await job_queue.children(first.job.id)) == 1, "the page went on to its files"

    later = await context_for(GENERATE, {"products": ["pictures"], "files": 1, "offset": 1000})
    await build(
        later, content=content_store, products=registry, run_type=GENERATE, file_type=GENERATE_FILE
    )
    assert machine.measured_times == 1, "a later page never measures"


async def test_a_measured_machine_is_not_measured_again(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    machine = Unmeasured()
    machine.is_measured = True
    registry = registry_of(Made("pictures", {a}), machine=machine)

    await build(
        await context_for(GENERATE, {"products": ["pictures"], "files": 1}),
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )

    assert machine.measured_times == 0


async def test_only_the_ticked_products_are_handed_out(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(Made("pictures", {a}), Made("faces", {a}))

    context = await context_for(GENERATE, {"products": ["faces"], "files": 1})
    await build(
        context,
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )

    children = await job_queue.children(context.job.id)
    assert [list(child.payload["products"]) for child in children] == [["faces"]]


async def test_a_ticked_product_nothing_on_the_page_lacks_is_handed_out_for_nobody(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    """Ticked, and already made for every file on this page: no task names it, and the product
    that is lacking still goes out."""
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(Made("pictures", {a}), Made("faces", set()))

    context = await context_for(GENERATE, {"products": ["pictures", "faces"], "files": 1})
    await build(
        context,
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )

    children = await job_queue.children(context.job.id)
    assert [list(child.payload["products"]) for child in children] == [["pictures"]]


@pytest.mark.parametrize(
    ("carried", "key"),
    [
        ([1700, "01HX"], (1700, "01HX")),
        (None, None),
        ([1700], None),
        (["1700", "01HX"], None),
        ([1700, 42], None),
    ],
)
def test_where_a_page_starts_is_read_only_from_the_shape_the_walk_writes(
    carried: object, key: tuple[int, str] | None
) -> None:
    """The payload is stored and read back, so anything but `[added_at, id]` starts at the top of
    the library rather than at a key the walk would misread."""
    assert _page_key(carried) == key


async def test_a_library_wider_than_a_page_asks_for_the_next_page(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One page per job, the job asking for itself again with where it got to. The page weighs
    nothing of its own: the family's counter carries the files. See
    `sift.wiring.work_ahead._pass_ahead`."""
    # The pass reads the page size by the name it imported, so both bindings are narrowed.
    monkeypatch.setattr(products_module, "PAGE", 2)
    monkeypatch.setattr(jobs_module, "PAGE", 2)
    ids = [await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "abc"]
    registry = registry_of(Made("pictures", set(ids)))

    context = await context_for(GENERATE, {"products": ["pictures"], "files": 3})
    await build(
        context,
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )

    children = await job_queue.children(context.job.id)
    tasks = [child for child in children if child.type == GENERATE_FILE]
    pages = [child for child in children if child.type == GENERATE]
    assert len(tasks) == 2 and len(pages) == 1
    handed = {str(task.payload["asset_id"]) for task in tasks}
    after = pages[0].payload.pop("after")
    assert pages[0].payload == {"products": ["pictures"], "queued": 2, "files": 3}
    assert isinstance(after, list) and after[1] in handed, "the next page starts after the last"
    job = await job_queue.get(context.job.id)
    assert job is not None
    assert job.units == 1, "the page weighs nothing beyond itself; the counter carries the files"
    assert job.note == "2 of 3 files handed out so far, still going through the library\u2026"

    # The next page is the rest of the library, by key: exactly the file not yet handed out.
    following = await context_for(GENERATE, {**pages[0].payload, "after": after})
    await build(
        following,
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )
    rest = [
        str(child.payload["asset_id"])
        for child in await job_queue.children(following.job.id)
        if child.type == GENERATE_FILE
    ]
    assert rest == sorted(set(ids) - handed)


async def test_nothing_ticked_is_said_rather_than_walked(
    content_store: ContentStore, job_queue: JobQueue, context_for: Any
) -> None:
    context = await context_for(GENERATE, {"products": ["nothing-of-the-sort"], "files": 0})
    await build(
        context,
        content=content_store,
        products=registry_of(),
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )
    job = await job_queue.get(context.job.id)
    assert job is not None and job.note == "Nothing was ticked, so there's nothing to build."
    assert await job_queue.children(context.job.id) == []


async def test_the_task_makes_each_product_in_order_and_times_each_under_its_name(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    """The timings are what the sheet prices the next Build from, so their names are the product
    keys under the Build prefix: a stage the ledger files as a product."""
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    pictures = Made("pictures", {a})
    faces = Made("faces", {a})
    registry = registry_of(pictures, faces)
    stages: list[str] = []
    set_stage_sink(lambda stage, _ms: stages.append(stage) if stage.startswith("build.") else None)
    try:
        context = await context_for(
            GENERATE_FILE, {"asset_id": a, "products": ["pictures", "faces"]}
        )
        await build_file(context, products=registry)
    finally:
        set_stage_sink(None)

    assert pictures.built == [a] and faces.built == [a]
    assert stages == ["build.pictures", "build.faces"]
    job = await job_queue.get(context.job.id)
    assert job is not None and job.progress == 1.0


async def test_a_product_nothing_registers_any_more_is_skipped_not_refused(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    context_for: Any,
) -> None:
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    pictures = Made("pictures", {a})
    context = await context_for(GENERATE_FILE, {"asset_id": a, "products": ["gone", "pictures"]})
    await build_file(context, products=registry_of(pictures))
    assert pictures.built == [a]


async def test_a_file_a_product_gave_up_on_is_left_out_of_the_page_and_the_count(
    content_store: ContentStore, library_root: LibraryRoot, settings: Settings
) -> None:
    """The verdict, honoured in both places the Build decides its files: the page the pass
    walks and the count the sheet states. A transient verdict (a share away) excludes
    nothing, because the next scan clears it and the file is still wanted."""
    a, b, c = [
        await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "abc"
    ]
    registry = registry_of(Made("pictures", {a, b, c}))
    await content_store.record_verdict(a, "pictures", code="no_frame", reason="No frame.")
    await content_store.record_verdict(
        c, "pictures", code="no_copy", reason="Away.", transient=True
    )

    page = await lacking_on_page(registry, ["pictures"], [a, b, c], content_store)
    assert page.by_file == {b: ["pictures"], c: ["pictures"]}
    assert await files_lacking(registry, ["pictures"], content_store) == 2


async def test_files_lacking_counts_a_file_once_however_many_products_it_lacks(
    content_store: ContentStore, library_root: LibraryRoot, settings: Settings
) -> None:
    a, b, c = [
        await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "abc"
    ]
    registry = registry_of(Made("pictures", {a, b}), Made("faces", {b, c}))
    assert await files_lacking(registry, ["pictures", "faces"], content_store) == 3
    assert await files_lacking(registry, ["pictures"], content_store) == 2
    assert await files_lacking(registry, [], content_store) == 0


async def test_what_is_lacking_is_split_by_kind_once_per_file_or_once_per_product(
    content_store: ContentStore, library_root: LibraryRoot, settings: Settings
) -> None:
    """The mix an estimate of time left prices. A Build's tasks carry every product of a file, so
    a file counts once; work queued one product at a time counts a file once for each."""
    a, b, c = [
        await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "abc"
    ]
    registry = registry_of(Made("pictures", {a, b}), Made("faces", {b, c}))

    keys = ["pictures", "faces"]
    assert await lacking_by_kind(registry, keys, content_store) == {"video": 3}
    assert await lacking_by_kind(registry, keys, content_store, each=True) == {"video": 4}


def test_what_a_page_says_it_did() -> None:
    assert _handed_out(queued=0, files=0, done=True) == "Nothing was missing."
    assert _handed_out(queued=1200, files=1200, done=True) == "Handed out 1,200 files to build."
    assert (
        _handed_out(queued=2, files=3, done=False)
        == "2 of 3 files handed out so far, still going through the library\u2026"
    )


async def test_the_task_reads_the_file_once_for_every_product(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    context_for: Any,
) -> None:
    """The products run inside the one-pass reader's context, and are handed to it in order."""
    from collections.abc import AsyncIterator, Sequence
    from contextlib import asynccontextmanager

    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    pictures = Made("pictures", {a})
    faces = Made("faces", {a})
    registry = registry_of(pictures, faces)
    opened: list[tuple[str, list[str]]] = []
    inside: list[str] = []

    class Recording:
        @asynccontextmanager
        async def prepared(self, asset_id: str, products: Sequence[Product]) -> AsyncIterator[None]:
            opened.append((asset_id, [one.key for one in products]))
            yield
            inside.extend(pictures.built + faces.built)

    registry.one_pass = Recording()
    context = await context_for(GENERATE_FILE, {"asset_id": a, "products": ["pictures", "faces"]})
    await build_file(context, products=registry)

    assert opened == [(a, ["pictures", "faces"])]
    assert inside == [a, a], "both products were made inside the read"


async def test_a_products_housekeeping_runs_once_before_the_first_page(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    """Meaning drops the descriptions of files that have left the library before a run; a product
    says so with `before_run`, and it runs on the first page only, and only when ticked."""
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    runs: list[str] = []

    async def prune() -> int:
        runs.append("pruned")
        return 0

    made = Made("meaning", {a})
    registry = registry_of(made, Made("pictures", {a}))
    meaning = registry.get("meaning")
    assert meaning is not None
    registry._products["meaning"] = dataclasses.replace(meaning, before_run=prune)

    await build(
        await context_for(GENERATE, {"products": ["pictures"], "files": 1}),
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )
    assert runs == [], "not ticked, not run"

    await build(
        await context_for(GENERATE, {"products": ["meaning"], "files": 1}),
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )
    await build(
        await context_for(GENERATE, {"products": ["meaning"], "files": 1, "offset": 1000}),
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )
    assert runs == ["pruned"], "once, on the first page"


# --- the registry itself --------------------------------------------------------------------------


def test_a_product_registered_twice_is_a_bug_rather_than_an_override() -> None:
    registry = registry_of(Made("pictures", set()))
    with pytest.raises(ValueError, match="pictures"):
        registry.register(Made("pictures", set()).product())


async def test_a_registry_handed_no_self_test_treats_the_machine_as_measured() -> None:
    """What a test that is not about measuring gets, and what the registry holds until the
    composition root hands it the real self-test: measured already, and measuring does nothing."""
    registry = registry_of()
    await registry.machine.measure()
    assert await registry.machine.measured() is True


async def test_a_page_asked_about_a_product_nothing_registers_skips_it(
    content_store: ContentStore, library_root: LibraryRoot, settings: Settings
) -> None:
    """The same forgiveness the task shows: a key from an older client names nothing here, and the
    page is about the products that are."""
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(Made("pictures", {a}))

    page = await lacking_on_page(registry, ["gone", "pictures"], [a], content_store)

    assert page.by_file == {a: ["pictures"]}


async def test_each_familys_page_job_hands_out_its_OWN_file_type(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    """THE REASON THE HANDLER IS A CLOSURE PER FAMILY rather than a lambda in the loop.

    A lambda written inside `for family, (run_type, file_type) in RUNS.items()` binds those names
    rather than their values, so every handler registered would read whichever pair the loop
    finished on, and BOTH families' page jobs would hand out Identify tasks and ask for Identify
    pages. The Generate run would then queue work under the wrong family, be counted on the wrong
    bar, and its tasks would be claimed by handlers expecting a different payload.
    """
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(
        Made("pictures", {a}),
        Made("faces", {a}, family=Family.IDENTIFY),
    )

    for run_type, file_type, ticked in (
        (GENERATE, GENERATE_FILE, "pictures"),
        (IDENTIFY, IDENTIFY_FILE, "faces"),
    ):
        # The closure as `register_handlers` builds it, one family at a time. Built here rather
        # than registered, because the fixtures have already claimed both types, and what is
        # being asked is which pair this handler CLOSED OVER, which is a fact about the closure.
        handler = jobs_module._page_handler(content_store, registry, run_type, file_type)
        context = await context_for(run_type, {"products": [ticked], "files": 1})
        await handler(context)

        children = await job_queue.children(context.job.id)
        assert [child.type for child in children] == [file_type], (
            f"the {run_type} page handed out {[c.type for c in children]}"
        )


async def test_a_product_that_has_to_wait_does_not_park_the_others(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    context_for: Any,
) -> None:
    """One product waiting does not stop the others.

    A product raises `JobBlocked` when it needs something no retry can supply (the face models
    not fetched, say). Run in a line, the first such raise would end the task where it stood and
    everything after it in the sheet would never be attempted, while the row said "waiting", which
    is true of one product and would be said about all of them.
    """
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    faces = Made("faces", {a})
    meaning = Made("meaning", {a})
    parked = faces.product()

    async def waits(_context: JobContext) -> None:
        raise JobBlocked("The face models are not installed.")

    registry = ProductRegistry(night_start=_eleven)
    registry.register(replace(parked, build=waits))
    registry.register(meaning.product())
    context = await context_for(IDENTIFY_FILE, {"asset_id": a, "products": ["faces", "meaning"]})

    with pytest.raises(JobBlocked, match="face models"):
        await build_file(context, products=registry)

    assert meaning.built == [a], "a product that could be made was skipped over a different wait"


async def _eleven() -> str:
    return "23:00"


async def test_a_page_takes_over_a_files_waiting_work_rather_than_handing_it_out_again(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    """A file's own pictures held for quiet hours, and a Build pressed now: the held row becomes
    the run's (now, named for the presser, and no second task), so the pictures are not made
    twice when the range opens."""
    a, b = [await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "ab"]
    pictures = Made("pictures", {a, b})
    registry = ProductRegistry(night_start=pictures.night)
    registry.register(replace(pictures.product(), governed_by="thumbnail"))

    context = await context_for(GENERATE, {"products": ["pictures"], "files": 2})
    context.job = replace(context.job, timing="now", requested_by="acct-presser")
    held = await job_queue.enqueue("thumbnail", {"asset_id": a})

    await build(
        context,
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )

    children = await job_queue.children(context.job.id)
    assert [str(one.payload["asset_id"]) for one in children if one.type == GENERATE_FILE] == [b]
    row = await job_queue.get(held)
    assert row is not None and row.timing == "now" and row.requested_by == "acct-presser"
    job = await job_queue.get(context.job.id)
    assert job is not None and job.note == "Handed out 2 files to build."


async def test_work_that_finished_while_the_page_was_read_is_not_handed_out_again(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    """A file's own picture job writes its picture and finishes after the page read what is
    lacking and before it looked at what is coming: the file is given no task, since what it lacked
    is made."""
    a, b = [await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "ab"]
    pictures = Made("pictures", {a, b})
    context = await context_for(GENERATE, {"products": ["pictures"], "files": 2})
    running = await job_queue.enqueue("thumbnail", {"asset_id": a})
    worker = "worker-of-the-arrival"
    while (claimed := await job_queue.claim(worker)) is not None and claimed.id != running:
        pass
    product = pictures.product()
    asked = product.lacking_among

    async def lacking_among(asset_ids: Any) -> set[str]:
        found = await asked(asset_ids)
        if a in found and await job_queue.is_live("thumbnail", {"asset_id": a}):
            pictures.lacking.discard(a)
            assert await job_queue.complete(running, worker)
        return found

    registry = ProductRegistry(night_start=pictures.night)
    registry.register(replace(product, governed_by="thumbnail", lacking_among=lacking_among))

    await build(
        context,
        content=content_store,
        products=registry,
        run_type=GENERATE,
        file_type=GENERATE_FILE,
    )

    children = await job_queue.children(context.job.id)
    assert [str(one.payload["asset_id"]) for one in children if one.type == GENERATE_FILE] == [b]


async def test_a_product_says_which_passes_read_what_it_made_and_the_task_asks_for_them(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
) -> None:
    """A Generate that made fingerprints asks for the duplicate sweep and the stash-box ask once
    its files stop landing: nothing else asks for them after a pressed run."""

    async def nothing(_context: JobContext) -> None:
        return None

    register_handler("dedup_scan_test", nothing, name="Test sweep")
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    made = Made("fingerprints", {a})
    registry = ProductRegistry(night_start=_eleven)
    registry.register(replace(made.product(), settles_into=("dedup_scan_test",)))

    await build_file(
        await context_for(GENERATE_FILE, {"asset_id": a, "products": ["fingerprints"]}),
        products=registry,
    )

    waiting = await job_queue.list(job_type="dedup_scan_test", state=JobState.QUEUED)
    assert waiting.total == 1
    assert waiting.jobs[0].run_after is not None, "asked once the batch settles, not at once"


async def test_a_product_that_fails_outright_fails_the_task_and_nothing_after_it_is_made(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    context_for: Any,
) -> None:
    """Only a hold or a wait lets the others go on. A plain failure is the task's own, so it
    reaches the queue as one, and a failure swallowed here would read as a file made."""
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    faces = Made("faces", {a})
    meaning = Made("meaning", {a})

    async def broken(_context: JobContext) -> None:
        raise RuntimeError("the model file is damaged")

    registry = ProductRegistry(night_start=_eleven)
    registry.register(replace(faces.product(), build=broken))
    registry.register(meaning.product())
    context = await context_for(IDENTIFY_FILE, {"asset_id": a, "products": ["faces", "meaning"]})

    with pytest.raises(RuntimeError, match="damaged"):
        await build_file(context, products=registry)

    assert meaning.built == []


async def test_a_product_the_machine_cannot_make_just_now_is_held_and_the_others_are_made(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    context_for: Any,
) -> None:
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    faces = Made("faces", {a})
    meaning = Made("meaning", {a})

    async def card_gone(_context: JobContext) -> None:
        raise JobHeld("The graphics card stopped answering.", retry_in=60)

    registry = ProductRegistry(night_start=_eleven)
    registry.register(replace(faces.product(), build=card_gone))
    registry.register(meaning.product())
    context = await context_for(IDENTIFY_FILE, {"asset_id": a, "products": ["faces", "meaning"]})

    with pytest.raises(JobHeld, match="graphics card"):
        await build_file(context, products=registry)

    assert meaning.built == [a]


async def _beside(database: Database, root: LibraryRoot, name: str) -> LibraryRoot:
    """A second library folder next to the first."""
    directory = root.path.parent / name
    directory.mkdir(exist_ok=True)
    root_id = new_id()
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, name, str(directory), 1_700_000_000),
    )
    return LibraryRoot(id=root_id, path=directory)


async def test_a_run_over_some_folders_counts_and_walks_only_them_on_every_page(
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    job_queue: JobQueue,
    context_for: Any,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The folders ride in the run's payload from the press to its last page, so every page hands
    out only their files, and the count it is weighed by is theirs. A whole run is unchanged."""
    monkeypatch.setattr(products_module, "PAGE", 2)
    monkeypatch.setattr(jobs_module, "PAGE", 2)
    garden = await _beside(temp_db, library_root, "garden")
    inside = []
    for n in "abc":
        inside.append(await a_probed_file(library_root, content_store, settings, f"{n}.mp4"))
        await a_probed_file(garden, content_store, settings, f"garden-{n}.mp4")
    registry = registry_of(Made("pictures", set(await _every_file(content_store))))

    files, [run_id] = await start_runs(
        ["pictures"],
        content=content_store,
        products=registry,
        queue=job_queue,
        requested_by=None,
        roots=[library_root.id],
    )
    run = await job_queue.get(run_id)
    assert run is not None
    assert files == 3
    assert run.payload == {
        "products": ["pictures"],
        "files": 3,
        "roots": [library_root.id],
        "each": {"pictures": 3},
    }, "and what it is over per product, which Activity draws its lines from"

    handed: list[str] = []
    payload: dict[str, Any] = dict(run.payload)
    for _page in range(5):
        context = await context_for(GENERATE, payload)
        await build(
            context,
            content=content_store,
            products=registry,
            run_type=GENERATE,
            file_type=GENERATE_FILE,
        )
        children = await job_queue.children(context.job.id)
        handed += [str(one.payload["asset_id"]) for one in children if one.type == GENERATE_FILE]
        following = [one for one in children if one.type == GENERATE]
        if not following:
            break
        assert following[0].payload["roots"] == [library_root.id]
        payload = following[0].payload
    assert sorted(handed) == sorted(inside)

    # The whole press: every file counted, and a payload with no folders in it at all.
    whole, [whole_id] = await start_runs(
        ["pictures"], content=content_store, products=registry, queue=job_queue, requested_by=None
    )
    whole_run = await job_queue.get(whole_id)
    assert whole == 6
    assert whole_run is not None and whole_run.payload == {"products": ["pictures"], "files": 6}


async def _every_file(content: ContentStore) -> list[str]:
    return (await content.asset_ids_page(limit=50)).ids


async def test_the_next_night_start_is_tonight_before_it_and_tomorrow_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both sides of the clock, fixed in the test: a run at ten in the morning waits for tonight's
    start, a run half an hour after it waits for tomorrow's. Read from the wall clock, the branch
    the test takes would depend on the hour it was run at (a runner in the small hours never saw
    the first)."""
    import time
    from datetime import datetime

    from sift.kernel.when import moment_of

    registry = ProductRegistry(night_start=_eleven)
    morning = datetime(2026, 10, 2, 10, 0).timestamp()
    late = datetime(2026, 10, 2, 23, 30).timestamp()

    monkeypatch.setattr(time, "time", lambda: morning)
    tonight = await registry._next_start()
    monkeypatch.setattr(time, "time", lambda: late)
    tomorrow = await registry._next_start()

    assert tonight == moment_of(datetime(2026, 10, 2, 23, 0))
    assert tomorrow == moment_of(datetime(2026, 10, 3, 23, 0))
