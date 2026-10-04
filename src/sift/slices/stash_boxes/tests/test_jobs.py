# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two jobs the pass is made of: one that queues the questions and one that asks them.

They are separate for the reason the sweep's docstring gives: a page of the library is worked out
under one user's permissions, and each question is a request to somebody else's service, paced.
What is worth holding here is the arithmetic and the three refusals:

* the sweep steps by what it WALKED and not by what it asked for, or a capped page silently skips
  files on every step and the library never finishes;
* a file this user may not be shown never has its fingerprints sent anywhere;
* only an exact-file hash is ever applied without being asked, whatever the switch says.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's table registers itself)
from sift.kernel.db import Database
from sift.kernel.enrichment import Missing
from sift.kernel.jobs import (
    JobBlocked,
    JobContext,
    JobQueue,
)
from sift.kernel.records import Subject
from sift.slices.stash_boxes.jobs import (
    STASH_LINK_INVENTED,
    STASH_SCAN,
    STASH_SWEEP,
    _apply,
    scan,
    strategies_for,
    sweep,
    sweep_page,
)
from sift.slices.stash_boxes.service import EXACT, StashBoxService
from sift.slices.stash_boxes.settings import (
    ASK_NEW_FILES_KEY,
    AUTO_APPLY_KEY,
    DURATION_KEY,
    SCAN_KEY,
    invent_key,
)
from sift.slices.stash_boxes.tests.jobs_support import (
    A_KEY,
    ADMIN,
    _a_box,
    _a_file,
    _Access,
    _Adapter,
    _admin_exists,
    _asked_events,
    _Asset,
    _children,
    _deps,
    _Enricher,
    _found,
    _people_a_box_invented,
    _Settings,
)

pytestmark = pytest.mark.anyio


# --- the switch -------------------------------------------------------------------------------


async def test_neither_job_does_anything_while_matching_is_switched_off(
    service: StashBoxService, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The setting is the feature. A job that ran anyway would send requests to three third-party
    services on behalf of somebody who has said not to."""
    access = _Access([_Asset("a1")])
    deps = _deps(access, _Settings({SCAN_KEY: False}), _Enricher())

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )
    await sweep(
        await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0}), service=service, deps=deps
    )

    assert access.pages == []


async def test_neither_job_runs_for_an_account_that_has_gone(
    service: StashBoxService, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """It carries the user who asked for it, resolved when it RUNS. A sweep whose asker has
    been removed stops rather than quietly running as nobody."""
    access = _Access([_Asset("a1")])
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher(), viewer=None)

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "gone"}),
        service=service,
        deps=deps,
    )
    await sweep(
        await context_for(STASH_SWEEP, {"viewer": "gone", "offset": 0}), service=service, deps=deps
    )

    assert access.pages == []


# --- asking about one file --------------------------------------------------------------------


async def test_a_file_this_account_may_not_be_shown_is_never_sent_anywhere(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The scoped read is doing real work rather than being polite: it is what stops a concealed
    file having its fingerprints sent to three third-party services."""
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    access = _Access([_Asset("a1")], hidden={"a1"})
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert adapter.asked == []


async def test_a_file_with_no_fingerprint_is_finished_rather_than_failed(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """A photograph has no video fingerprint, and a clip that has not been through the catch-up
    pass has none yet. Neither is an error and neither is worth a line per file."""
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    access = _Access([_Asset("a1", oshash=None, video_phash=None)])
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert adapter.asked == []


async def test_a_scan_with_the_keys_still_sealed_waits_rather_than_failing(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """After a restart nobody's key is open yet. The kernel parks the job until somebody logs in,
    where failing would mark a whole sweep as broken because it ran at four in the morning."""
    await _a_box(service, _Adapter([_found(EXACT)]))
    await _a_file(temp_db, "a1")
    deps = _deps(_Access([_Asset("a1")]), _Settings({SCAN_KEY: True}), _Enricher())

    with pytest.raises(JobBlocked):
        await scan(
            await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}, key=None),
            service=service,
            deps=deps,
        )


async def test_a_scan_keeps_what_came_back_and_applies_none_of_it_by_default(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    enricher = _Enricher()
    deps = _deps(_Access([_Asset("a1")]), _Settings({SCAN_KEY: True}), enricher)

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert (await service.waiting(limit=0))[1] == 1
    assert enricher.applied == []


async def test_the_length_window_comes_from_the_setting_and_grades_the_answer(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The cheapest disambiguator there is, and the one thing a stash-box cannot be asked to do."""
    adapter = _Adapter([_found(0.9, duration_ms=61_000)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    deps = _deps(
        _Access([_Asset("a1")]), _Settings({SCAN_KEY: True, DURATION_KEY: 10}), _Enricher()
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    waiting, _ = await service.waiting(limit=10)
    assert [one.grade.value for one in waiting] == ["likely"]


async def test_a_tolerance_that_cannot_be_read_grades_everything_unsure(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The cautious reading, and the one that loses nothing: an unsure answer is still a row
    somebody can agree with, where a wrong `likely` is one they will not look twice at."""
    adapter = _Adapter([_found(0.9, duration_ms=61_000)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    deps = _deps(
        _Access([_Asset("a1")]),
        _Settings({SCAN_KEY: True, DURATION_KEY: "not a number"}),
        _Enricher(),
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    waiting, _ = await service.waiting(limit=10)
    assert [one.grade.value for one in waiting] == ["unsure"]


# --- applying an exact match without being asked ------------------------------------------------


async def test_an_exact_match_is_applied_when_the_switch_says_so_and_settles_the_row(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    enricher = _Enricher()
    deps = _deps(
        _Access([_Asset("a1")]),
        _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True}),
        enricher,
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert enricher.applied == [("apply", frozenset())], (
        "nothing is invented unless a switch says so, and every switch is off by default"
    )


async def test_an_exact_match_invents_only_the_kinds_whose_switch_is_on(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """One kind on and one off, which is the case the per-kind shape exists for.

    A single kind would pass with the permission read as all-or-nothing; a run that files every
    person a box names and none of its tag vocabulary is the thing somebody actually wants, and it
    is the only arrangement that proves the switches are read one at a time.
    """
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    enricher = _Enricher(
        missing=(
            Missing(name="Jane Doe", kind=Subject.PERSON.value),
            Missing(name="beach", kind=Subject.TAG.value),
        )
    )
    deps = _deps(
        _Access([_Asset("a1")]),
        _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True, invent_key(Subject.PERSON): True}),
        enricher,
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert enricher.applied == [("apply", frozenset({(Subject.PERSON.value, "Jane Doe")}))]
    assert (await service.waiting(limit=0))[1] == 0


async def test_an_exact_match_invents_a_tag_only_when_the_tag_switch_is_on(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The mirror of the case above: the tag switch alone, so the person named beside it is left."""
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    enricher = _Enricher(
        missing=(
            Missing(name="Jane Doe", kind=Subject.PERSON.value),
            Missing(name="beach", kind=Subject.TAG.value),
        )
    )
    deps = _deps(
        _Access([_Asset("a1")]),
        _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True, invent_key(Subject.TAG): True}),
        enricher,
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert enricher.applied == [("apply", frozenset({(Subject.TAG.value, "beach")}))]


async def test_a_resemblance_is_never_applied_however_the_switch_is_set(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """A perceptual match is fuzzy and capped by nature, and no setting should be able to turn that
    into a decision taken while nobody is looking."""
    adapter = _Adapter([_found(0.99, duration_ms=60_000)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    enricher = _Enricher()
    deps = _deps(
        _Access([_Asset("a1")]),
        _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True, DURATION_KEY: 10}),
        enricher,
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert enricher.applied == []
    assert (await service.waiting(limit=0))[1] == 1


async def test_an_apply_with_nothing_to_plan_settles_nothing(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """An older build meeting a newer one: nothing can write a file, so there is nothing to apply
    and the row stays a question."""
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    deps = _deps(
        _Access([_Asset("a1")]),
        _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True}),
        _Enricher(plans=False),
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert (await service.waiting(limit=0))[1] == 1


async def test_a_pressed_auto_enrich_applies_an_exact_match_with_the_switch_off_press_decides(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The press is the consent: `apply_certain` governs only the runs nobody pressed,
    so a pressed Auto-enrich accepts what is certain whatever the switch says."""
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    enricher = _Enricher()
    deps = _deps(_Access([_Asset("a1")]), _Settings({SCAN_KEY: True}), enricher)

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin", "apply": True}),
        service=service,
        deps=deps,
    )

    assert enricher.applied == [("apply", frozenset())]


async def test_a_pressed_enrich_leaves_even_an_exact_match_waiting_press_decides(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The other press: Enrich asks and lets somebody choose, so the switch being on is not asked."""
    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    enricher = _Enricher()
    deps = _deps(
        _Access([_Asset("a1")]), _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True}), enricher
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin", "apply": False}),
        service=service,
        deps=deps,
    )

    assert enricher.applied == []
    assert (await service.waiting(limit=0))[1] == 1


async def test_a_file_kept_local_while_its_question_was_out_is_not_applied_kept_local(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The unattended apply is a writer of a stored answer like any other, so it asks
    `nothing_applied`: the door let the question out, and the file was kept local since."""
    from sift.kernel.access.catalog import set_kept_local_on

    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    enricher = _Enricher()
    deps = _deps(_Access([_Asset("a1")]), _Settings({SCAN_KEY: True}), enricher)
    found = await service.scan_one("a1", {"oshash": "x"}, A_KEY, length_ms=None, tolerance_ms=0)
    async with temp_db.write() as connection:
        await set_kept_local_on(connection, "asset", "a1", True)

    await _apply(
        "a1", found[0].box_id, service=service, deps=deps, rules={}, inventable=frozenset()
    )

    assert enricher.applied == []


# --- the sweep --------------------------------------------------------------------------------


async def test_the_sweep_queues_one_question_per_file_nobody_has_asked_about(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    await _a_box(service, _Adapter())
    await _admin_exists(temp_db)
    access = _Access([_Asset("a1"), _Asset("a2")])
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0})
    await sweep(context, service=service, deps=deps)

    assert sorted(one["asset_id"] for one in await _children(context, STASH_SCAN)) == ["a1", "a2"]


async def test_a_page_decided_is_what_the_sweep_queues_and_deciding_asks_nobody(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """A dry run counts `sweep_page`; the sweep queues exactly its files. Deciding queues nothing."""
    await _a_box(service, _Adapter())
    await _admin_exists(temp_db)
    access = _Access([_Asset("a1"), _Asset("a2")])
    page = await sweep_page(
        access,  # type: ignore[arg-type]
        service,
        ADMIN,
        offset=0,
        folder=None,
        only=None,
    )
    assert (sorted(page.asking), page.walked, page.total) == (["a1", "a2"], 2, 2)

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0})
    assert await _children(context, STASH_SCAN) == []
    await sweep(
        context, service=service, deps=_deps(access, _Settings({SCAN_KEY: True}), _Enricher())
    )
    queued = sorted(one["asset_id"] for one in await _children(context, STASH_SCAN))
    assert queued == sorted(page.asking)


async def test_the_sweep_steps_by_what_it_walked_and_not_by_what_it_asked_for(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The fault this exists to prevent, and it is silent: the access layer caps a page, so a sweep
    asking for a hundred, given forty, and stepping on by a hundred steps over sixty files every
    time, with nothing to see but a library that never finishes."""
    await _a_box(service, _Adapter())
    access = _Access([_Asset(f"a{n}") for n in range(10)])
    access.cap = 4
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0})
    await sweep(context, service=service, deps=deps)

    following = await _children(context, STASH_SWEEP)
    assert [one["offset"] for one in following] == [4]
    assert following[0]["queued"] == 4


async def test_a_sweep_that_has_reached_the_end_does_not_queue_itself_again(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    await _a_box(service, _Adapter())
    await _admin_exists(temp_db)
    deps = _deps(_Access([_Asset("a1")]), _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0})
    await sweep(context, service=service, deps=deps)

    assert await _children(context, STASH_SWEEP) == []
    assert await _children(context, STASH_LINK_INVENTED) == [], "nobody was owed a link"


async def test_a_finished_sweep_asks_for_the_people_a_switched_on_box_invented(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """People a box invented before they were linked to it are asked about once a sweep with that
    box on has walked the whole library, as the sweep's own work rather than a start's."""
    await _people_a_box_invented(service, temp_db)
    await _admin_exists(temp_db)
    deps = _deps(_Access([_Asset("a1")]), _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0})
    await sweep(context, service=service, deps=deps)

    assert await _children(context, STASH_LINK_INVENTED) == [{}]


async def test_a_page_that_walked_nothing_ends_the_sweep_rather_than_repeating_it(
    service: StashBoxService,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The guard rather than a tidy-up: a page that walked nothing advances nothing, so re-queueing
    on one would be this job asking for itself again at the same offset, for ever."""
    await _a_box(service, _Adapter())
    deps = _deps(_Access([_Asset("a1")]), _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 50})
    await sweep(context, service=service, deps=deps)

    assert await _children(context, STASH_SWEEP) == []


async def test_a_library_with_nothing_in_it_finishes_rather_than_dividing_by_its_size(
    service: StashBoxService,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    await _a_box(service, _Adapter())
    deps = _deps(_Access([]), _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0})
    await sweep(context, service=service, deps=deps)

    assert await _children(context, STASH_SCAN) == []


# --- the rules a field is written under ---------------------------------------------------------


async def test_a_rule_nobody_has_set_falls_back_to_the_one_that_cannot_lose_anything() -> None:
    """Read by the key the registry builds, so a field added later gets its rule without this
    function knowing it exists."""
    rules = await strategies_for(_Settings(), Subject.ASSET)  # type: ignore[arg-type]

    assert rules
    assert set(rules.values()) == {"merge"}


async def test_a_stored_rule_from_a_newer_version_falls_back_rather_than_raising() -> None:
    """A database edited by hand, or a setting from a build this one has never seen. An unreadable
    preference must not stop an import."""
    key = next(iter(await strategies_for(_Settings(), Subject.ASSET)))  # type: ignore[arg-type]
    from sift.kernel.enrichment import strategy_key

    rules = await strategies_for(
        _Settings({strategy_key(Subject.ASSET, key): "something-else"}),  # type: ignore[arg-type]
        Subject.ASSET,
    )

    assert rules[key] == "merge"


def _every_field_rule() -> list[tuple[Subject, str]]:
    """Every rule Settings > Stash-boxes draws, as the registry holds them."""
    from sift.kernel.settings_registry import registered_settings

    return [
        (Subject(key.split(".")[1]), key.split(".", 2)[2])
        for key in sorted(registered_settings())
        if key.startswith("enrich.")
    ]


@pytest.mark.parametrize("subject_and_field", _every_field_rule(), ids=lambda one: ".".join(one))
@pytest.mark.parametrize("chosen", ["ignore", "overwrite"])
async def test_each_field_rule_on_the_screen_is_the_rule_a_lookup_writes_under(
    subject_and_field: tuple[Subject, str], chosen: str
) -> None:
    """A rule changed on Settings > Stash-boxes is the rule that field is written under, and only
    that field's: every other field of the subject keeps the default."""
    from sift.kernel.enrichment import strategy_key

    subject, key = subject_and_field
    rules = await strategies_for(
        _Settings({strategy_key(subject, key): chosen}),  # type: ignore[arg-type]
        subject,
    )

    assert rules[key] == chosen
    assert {one for name, one in rules.items() if name != key} <= {"merge"}


def test_the_screen_draws_a_rule_for_every_field_a_lookup_writes_and_no_other() -> None:
    from sift.kernel.enrichment import enrichable
    from sift.slices.stash_boxes.settings import ENRICHED

    drawn = set(_every_field_rule())
    written = {(subject, key) for subject in ENRICHED for key in enrichable(subject)}
    assert drawn and drawn == written


async def test_a_sweep_of_one_folder_narrows_to_it(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """Asking the stash-boxes about one folder rather than the whole library.

    The counterpart to "Scan this folder" in the same menu. What is asserted is that the narrowing
    reaches the READ: the work list is what the access layer returns, so a filter that never
    arrives there is a folder-scoped sweep that quietly asks about everything.
    """
    await _a_box(service, _Adapter())
    await _admin_exists(temp_db)
    access = _Access([_Asset("a1")])
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0, "folder": "f1"})
    await sweep(context, service=service, deps=deps)

    asked = access.filters[0]
    assert asked is not None
    assert getattr(asked, "folder_scope", None) == (("f1",),)


async def test_a_sweep_of_the_whole_library_narrows_to_nothing(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """No folder named is the whole library. The pair with the test above: a scope that is always
    applied is not a scope."""
    await _a_box(service, _Adapter())
    await _admin_exists(temp_db)
    access = _Access([_Asset("a1")])
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0})
    await sweep(context, service=service, deps=deps)

    assert getattr(access.filters[0], "folder_scope", None) == ()


async def test_a_scoped_sweep_carries_its_folder_into_the_next_page(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The failure this exists to catch is silent and only appears on a library big enough to page.

    A sweep re-queues itself rather than looping. Drop the folder from what it hands forward and
    the FIRST page is scoped exactly as asked while every page after it sweeps the whole library,
    so a person who asked about one folder gets the entire stash-box queued, and the only symptom
    is a job that takes far longer than it should.
    """
    await _a_box(service, _Adapter())
    access = _Access([_Asset(f"a{n}") for n in range(10)])
    access.cap = 4
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0, "folder": "f1"})
    await sweep(context, service=service, deps=deps)

    following = await _children(context, STASH_SWEEP)
    assert [one["folder"] for one in following] == ["f1"]


# --- the sweep nobody pressed --------------------------------------------------------------------


async def test_a_sweep_nobody_pressed_reads_the_library_as_an_admin(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The fingerprint chain asks for a sweep with no user on it. It still has to see the
    library as somebody, and an admin's view is the whole library."""
    await _a_box(service, _Adapter())
    access = _Access([_Asset("a1"), _Asset("a2")])
    deps = _deps(access, _Settings({SCAN_KEY: True, ASK_NEW_FILES_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {})
    await sweep(context, service=service, deps=deps)

    children = await _children(context, STASH_SCAN)
    assert sorted(one["asset_id"] for one in children) == ["a1", "a2"]
    assert {one["viewer"] for one in children} == {"admin"}


async def test_a_sweep_nobody_pressed_waits_for_a_press_while_its_own_switch_is_off(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The sub-switch gates the run nobody pressed and only that: enriching stays on, a press
    still sweeps, and the fingerprint chain's request does nothing."""
    await _a_box(service, _Adapter())
    await _admin_exists(temp_db)
    access = _Access([_Asset("a1")])
    deps = _deps(access, _Settings({SCAN_KEY: True, ASK_NEW_FILES_KEY: False}), _Enricher())

    quiet = await context_for(STASH_SWEEP, {})
    await sweep(quiet, service=service, deps=deps)
    assert await _children(quiet, STASH_SCAN) == []

    pressed = await context_for(STASH_SWEEP, {"viewer": ADMIN.id, "offset": 0})
    await sweep(pressed, service=service, deps=deps)
    assert [one["asset_id"] for one in await _children(pressed, STASH_SCAN)] == ["a1"]


async def test_a_sweep_nobody_pressed_stops_where_the_instance_has_no_admin(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    await _a_box(service, _Adapter())
    access = _Access([_Asset("a1")])
    access.admin = None
    deps = _deps(access, _Settings({SCAN_KEY: True, ASK_NEW_FILES_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {})
    await sweep(context, service=service, deps=deps)

    assert await _children(context, STASH_SCAN) == []
    assert await _asked_events(temp_db) == []


async def test_a_sweep_nobody_pressed_stays_sifts_own_on_every_page(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The page that carries on must not pick a user up on the way, or a run that began as
    Sift's would finish as an admin's and be written down against their name. And nothing is
    written down until the last page, because the count is not known before it."""
    await _a_box(service, _Adapter())
    access = _Access([_Asset("a1"), _Asset("a2"), _Asset("a3")])
    access.cap = 2
    deps = _deps(access, _Settings({SCAN_KEY: True, ASK_NEW_FILES_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {})
    await sweep(context, service=service, deps=deps)

    following = await _children(context, STASH_SWEEP)
    assert len(following) == 1
    assert "viewer" not in following[0]
    assert await _asked_events(temp_db) == []


async def test_a_finished_sweep_nobody_pressed_is_written_down_as_sifts_own_act(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """One event with a count, the box being both what was asked and where the event lives, so
    the feed reads "Sift asked StashDB about 2 files" and the box's own thread can find it."""
    box_id = await _a_box(service, _Adapter())
    access = _Access([_Asset("a1"), _Asset("a2")])
    deps = _deps(access, _Settings({SCAN_KEY: True, ASK_NEW_FILES_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {})
    await sweep(context, service=service, deps=deps)

    assert await _asked_events(temp_db) == [("asked", "sift", "stash", "box", box_id, "StashDB", 2)]


async def test_a_pressed_sweep_is_written_down_against_the_account_that_pressed(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    box_id = await _a_box(service, _Adapter())
    await _admin_exists(temp_db)
    access = _Access([_Asset("a1")])
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": ADMIN.id, "offset": 0})
    await sweep(context, service=service, deps=deps)

    assert await _asked_events(temp_db) == [
        ("asked", "user", ADMIN.id, "box", box_id, "StashDB", 1)
    ]


async def test_a_sweep_that_queued_nothing_writes_nothing_down(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """A pass that found nothing to ask about is not an act anybody needs telling of, and the
    ledger refuses a count of nothing in as many words."""
    await _a_box(service, _Adapter())
    deps = _deps(_Access([]), _Settings({SCAN_KEY: True, ASK_NEW_FILES_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {})
    await sweep(context, service=service, deps=deps)

    assert await _asked_events(temp_db) == []
