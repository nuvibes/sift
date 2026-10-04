# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Build can make for a file, declared once so the sheet and the pass agree.

A product is one thing a file can lack (its pictures, its fingerprints, its faces, its meaning)
and four questions about it: is it switched on, what does lacking it look like, which of THESE
files lack it, and how to make it for one file. The sheet asks the first two to draw a row with a
count: the second as a condition the content store counts the whole library against, every
product's at once, in one statement; the pass asks the third a page at a time to decide each
file's list; the task asks the fourth, once per product, on the one read of the file.

Declared here as a shape and filled in by the composition root, because the answers live in three
different features (pictures in `media_jobs`, faces in `faces`, meaning in `semantic`) and a
feature never imports another. What this module knows is the shape of the question.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Protocol

from sift.kernel.content import ContentStore, Lack, Within
from sift.kernel.jobs import JobContext
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.quiet_hours import clock
from sift.kernel.media import FileFacts, FrameRequest
from sift.kernel.when import moment_of, wall
from sift.kernel.wiring import Part

#: How many files a pass over the library takes at a time when deciding what each lacks. One short
#: query per product per page; a thousand keeps a 50,000-file library at fifty pages.
PAGE = 1000


@dataclass(frozen=True, slots=True)
class Product:
    """One thing a Build can make for a file, and the four questions about it."""

    key: str
    label: str
    help: str
    switched_on: Callable[[], Awaitable[bool]]
    """Whether the library's own switches say this is wanted for files as they arrive. The sheet
    ticks a row to match; the person can untick it for one run."""
    lack: Callable[[], Awaitable[Lack | None]]
    """What lacking it looks like, as a condition on the assets row, or None while nothing is
    going to be made, because the feature is off or not ready, so nothing is lacking. What the
    sheet's count and the run's weight are read from; see `count_lacking`."""
    lacking_among: Callable[[Sequence[str]], Awaitable[set[str]]]
    """Which of these files lack it. What the pass asks a page at a time."""
    build: Callable[[JobContext], Awaitable[None]]
    #: Which of the two passes makes it: `GENERATE` (pictures, fingerprints) or `IDENTIFY`
    #: (faces, meaning). The run that hands this product out belongs to that family, so the
    #: Activity screen counts and times the two separately. See `jobs.RUNS`.
    family: Family
    """Make it for the file the context's payload names. The task calls this once per product."""
    frames: Callable[[FileFacts], Awaitable[Sequence[FrameRequest]]] | None = None
    """Which moments of a video it would read, so the task can read the file once for every
    product. None for a product that reads no moments, or none the kernel can prepare."""
    before_run: Callable[[], Awaitable[object]] | None = None
    #: How many files want this product AT ALL, done or not: the denominator of its bar on
    #: Activity, narrowed to the files the folders' answers leave wanting it (`Within`, None when
    #: no folder refuses). None means every file in the library, which is wrong for a picture (a photo
    #: never wants a hover preview) and for a fingerprint of the sound (a silent file never wants
    #: one), so each of those says its own.
    wants: Callable[[Within | None], Awaitable[int]] | None = None
    """Housekeeping once per run, before the first task: dropping what belongs to files that
    have left the library, say. None for a product with none."""
    #: How many files not yet READ will want this product once they are, narrowed the same way.
    #: They lack it, and the count of what is lacking cannot see them (it reads only files that
    #: have been read), so without this the bar on Activity would count a first import's every
    #: unread file as done, and price the time left over the few files already read. None for a product
    #: nothing counts that way.
    coming: Callable[[Within | None], Awaitable[int]] | None = None
    governed_by: str | None = None
    """This product's own job type: the one an arriving file's work for it is queued as. Two
    readers ask it. The import policy gates the product under it, so a folder that refuses that
    work takes its files out of the count. See `ProductRegistry.within`; and the work ledger
    records a run of that type as a run FOR this product (`Ledger.started`), which is what a task's
    "last ran" is read by. None for a product with no job of its own."""
    doing: str | None = None
    """What a "Run task" press on some files is doing, as the head of the sentence it answers with:
    "Looking for faces in" becomes "Looking for faces in 12 files." Declared beside the label so the
    words a press says are the feature's own, like the label the Importing sheet draws. None falls
    back to the label, which is honest and plainer."""
    again: bool = False
    """Whether a press on a file that ALREADY HAS this makes it again (`AGAIN` in the task's
    payload). True where the maker re-makes: a picture honours the key, a face scan and a
    watermark read always look again. False where the maker skips a file that has it, and then the
    press is offered only for the files that lack it and says how many already had it: a press
    that reported success over a file it then skipped would be a button that does nothing."""
    settles_into: tuple[str, ...] = ()
    """The whole-library passes that read what this product makes, by job type: asked for once a
    run's files stop landing, after any file had it made. The fingerprints settle into the near
    duplicate sweep and the stash-box ask, which nothing else asks for after a pressed run."""
    cannot_run: Callable[[], Awaitable[str | None]] | None = None
    """Why this cannot be made on this machine at all just now, in a sentence, or None when it can:
    the device recognition is set to is missing, the models were never fetched. Asked by a press
    before anything is queued, because a maker that is not ready returns without a word and a
    queued task would finish having done nothing. None for a product that is always able."""


@dataclass(frozen=True, slots=True)
class Reading:
    """Reading ONE file again: what the Scan stage's "Scan now" means for a file rather than a folder.

    Not a `Product`, because nothing lacks it (every file in the library has been read, which is
    how it is in the library), so it has no count, no sheet row and no place in the Build. It is
    here because the press that offers it is the same press that offers the products, and the words
    have to live beside theirs. The job it queues is the arriving file's own read, handed the file.
    """

    key: str
    label: str
    help: str
    doing: str
    job_type: str
    """The queue type that reads one file, whose payload is `{"asset_id"}`."""


class Machine(Protocol):
    """The self-test, as the Build needs it: whether this machine has been measured, and measure it.

    Every read a Build makes is shaped by two rates only the self-test measures (how fast this
    machine decodes and what a seek costs it), so a Build on a machine that was never measured
    measures it first, as its first step, rather than guessing from the core count.
    """

    async def measured(self) -> bool: ...

    async def measure(self) -> None: ...


class OnePass(Protocol):
    """Reads one file once for several products: the kernel's prepared frames, set around the
    task's products by the composition root's reader. See `media_jobs.OnePassReader`."""

    def prepared(
        self, asset_id: str, products: Sequence[Product]
    ) -> AbstractAsyncContextManager[None]: ...


class EachOnItsOwn:
    """No shared read: every product reads the file as it always did. What a registry has until
    the composition root hands it a reader, and what a test that is not about reading gets."""

    @asynccontextmanager
    async def prepared(self, asset_id: str, products: Sequence[Product]) -> AsyncIterator[None]:
        yield


class AlreadyMeasured:
    """A machine that needs no measuring. What a registry has until the composition root hands it
    the real self-test, and what a test that is not about measuring gets."""

    async def measured(self) -> bool:
        return True

    async def measure(self) -> None:
        return None


class FolderPolicy(Protocol):
    """The import policy, as the count needs it: which files a piece of work is still wanted for
    once the folders refusing it are counted. See `ImportPolicy.folder_term`."""

    async def folder_term(self, job_type: str) -> Within | None: ...


class ProductRegistry:
    """The products, in the order the sheet draws them, and the two things the sheet and the pass
    need from other features: when tonight starts, and whether this machine has been measured."""

    def __init__(
        self,
        *,
        night_start: Callable[[], Awaitable[str]],
        quiet_opens: Callable[[], Awaitable[int]] | None = None,
        machine: Machine | None = None,
        one_pass: OnePass | None = None,
        policy: FolderPolicy | None = None,
    ) -> None:
        self._products: dict[str, Product] = {}
        self._readings: list[Reading] = []
        self.night_start = night_start
        """When quiet hours start, as "HH:MM", for the sheet to say. Read from the Tasks settings
        by the composition root."""
        self.quiet_opens = quiet_opens or self._next_start
        """When quiet hours next open, in seconds since the epoch, now while they are open. What
        a Build asked for "Run during quiet hours" says it will start at. The composition root hands in the
        answer from the whole range; left out (a registry built by a test), it is the next time the
        start hour comes round, which is the same answer whenever the range is shut."""
        self.machine: Machine = machine or AlreadyMeasured()
        """The self-test, for the pass's first step. See `Machine`."""
        self.one_pass: OnePass = one_pass or EachOnItsOwn()
        """The reader that reads a file once for every product of a task. See `OnePass`."""
        self._policy = policy

    async def _next_start(self) -> int:
        start = clock(await self.night_start())
        moment = wall(time.time())
        begins = datetime.combine(moment.date(), start)
        if begins <= moment:
            begins += timedelta(days=1)
        return moment_of(begins)

    async def within(self, product: Product) -> Within | None:
        """The files this product is still wanted for, where a folder refuses its work. One answer
        for the count of what is lacking and the count of what is wanted, so the two ends of its
        bar describe one set of files."""
        if self._policy is None or product.governed_by is None:
            return None
        return await self._policy.folder_term(product.governed_by)

    def register(self, product: Product) -> None:
        if product.key in self._products or any(one.key == product.key for one in self._readings):
            raise ValueError(f"a product named {product.key!r} is already registered")
        self._products[product.key] = product

    def get(self, key: str) -> Product | None:
        return self._products.get(key)

    def __iter__(self) -> Iterator[Product]:
        return iter(self._products.values())

    def keys(self) -> list[str]:
        return list(self._products)

    def register_reading(self, reading: Reading) -> None:
        """Offer reading one file again beside the products. See `Reading`."""
        if reading.key in self._products or any(one.key == reading.key for one in self._readings):
            raise ValueError(f"a pass named {reading.key!r} is already registered")
        self._readings.append(reading)

    def readings(self) -> list[Reading]:
        return list(self._readings)


PRODUCTS: Part[ProductRegistry] = Part("importing_products")


@dataclass(slots=True)
class Lacking:
    """What one page of the library lacks, per file."""

    by_file: dict[str, list[str]] = field(default_factory=dict)

    def add(self, asset_id: str, product: str) -> None:
        self.by_file.setdefault(asset_id, []).append(product)


async def lacking_on_page(
    registry: ProductRegistry, keys: Sequence[str], asset_ids: Sequence[str], content: ContentStore
) -> Lacking:
    """Which of these files lack which of the named products. Products in the order named."""
    found = Lacking()
    for key in keys:
        product = registry.get(key)
        if product is None:
            continue
        # A file the product has already said it cannot make this for is not lacking it: the
        # same exclusion the count makes, taken out of the page here.
        given_up = await content.verdicted_among(key, asset_ids)
        for asset_id in sorted(await product.lacking_among(asset_ids)):
            if asset_id not in given_up:
                found.add(asset_id, key)
    return found


@dataclass(frozen=True, slots=True)
class Counted:
    """How many files lack each product, by key, and how many lack any of the ticked ones."""

    each: dict[str, int]
    files: int


async def count_lacking(
    registry: ProductRegistry,
    content: ContentStore,
    keys: Sequence[str],
    ticked: Sequence[str],
    *,
    roots: Sequence[str] | None = None,
) -> Counted:
    """A count per named product and the union over the ticked ones, from one statement.

    The union is what the sheet states and the pass is weighed by: a file lacking two things is
    one file. A product whose `lack` answers None counts nothing and adds nothing to the union.
    A key nothing registers is left out, the way the pass leaves it out. `roots` counts only the
    files in those library folders, as a run over them walks (None is the whole library).
    """
    lacks, present = await _terms(registry, keys)
    counted = await content.count_lacking(
        [lack for _key, lack in present],
        ticked=[key in ticked for key, _lack in present],
        roots=roots,
    )
    each = dict.fromkeys((product.key for product, _lack in lacks), 0)
    each.update((key, n) for (key, _lack), n in zip(present, counted.each, strict=True))
    return Counted(each=each, files=counted.files)


async def _terms(
    registry: ProductRegistry, keys: Sequence[str]
) -> tuple[list[tuple[Product, Lack | None]], list[tuple[str, Lack]]]:
    """Each named product with its term, and the terms that count, filed under their products."""
    products = [product for key in keys if (product := registry.get(key)) is not None]
    # Each term is filed under its product, so the count leaves out the files the product has
    # already given up on: the same files `lacking_on_page` takes out of a page.
    lacks = [(product, await product.lack()) for product in products]
    # And narrowed to the files the folders' answers still want it for: a file only in a folder
    # that refused this work is not lacking it, because nothing is going to make it there.
    present = [
        (product.key, replace(lack, product=product.key, within=await registry.within(product)))
        for product, lack in lacks
        if lack is not None
    ]
    return lacks, present


async def lacking_by_kind(
    registry: ProductRegistry,
    keys: Sequence[str],
    content: ContentStore,
    *,
    each: bool = False,
    roots: Sequence[str] | None = None,
) -> dict[str, int]:
    """What `files_lacking` counts, by media kind: the mix an estimate of time left prices.

    `each` counts a file once per product it lacks, which is how work queued one product at a time
    is weighed; otherwise the union, which is how a Build's tasks are.
    """
    _lacks, present = await _terms(registry, keys)
    split = await content.count_lacking_by_kind([lack for _key, lack in present], roots=roots)
    return {kind: sum(one.each) if each else one.files for kind, one in split.items()}


async def files_lacking(
    registry: ProductRegistry,
    keys: Sequence[str],
    content: ContentStore,
    *,
    roots: Sequence[str] | None = None,
) -> int:
    """How many files lack at least one of the named products. The count the sheet states and the
    pass is weighed by: the union, so a file lacking two is one file. `roots` as `count_lacking`."""
    return (await count_lacking(registry, content, keys, keys, roots=roots)).files
