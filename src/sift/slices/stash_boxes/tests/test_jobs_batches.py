# SPDX-License-Identifier: AGPL-3.0-or-later
"""The batch of names, the linking pass over what a box invented, and the pictures it owes.

The second half of the jobs the pass is made of: what a batch came to, in the sentence the toast
sends people to, and the follow-ups that link and picture the rows a box named.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's table registers itself)
from sift.kernel.db import Database
from sift.kernel.jobs import (
    JobBlocked,
    JobContext,
    JobQueue,
    register_handler,
)
from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.adapter import Box, StashBoxUnreachable, as_json
from sift.slices.stash_boxes.entities import EntityEnricher
from sift.slices.stash_boxes.jobs import (
    KEPT_LOCAL_NOTE,
    STASH_CREATOR_PICTURE,
    STASH_ENRICH,
    STASH_LINK_INVENTED,
    STASH_SCAN,
    STASH_SWEEP,
    Outcome,
    _enriched,
    creator_pictures_owed,
    enrich_entities,
    invented_pass,
    invented_sites_pass,
    inventions_owed,
    keep_creator_picture,
    link_what_boxes_invented,
    scan,
    sweep,
)
from sift.slices.stash_boxes.service import EXACT, StashBoxService
from sift.slices.stash_boxes.settings import (
    AUTO_APPLY_KEY,
    DURATION_KEY,
    SCAN_KEY,
    invent_key,
)
from sift.slices.stash_boxes.tests.jobs_support import (
    A_KEY,
    _a_box,
    _a_creators_scene,
    _a_file,
    _Access,
    _Adapter,
    _Asset,
    _children,
    _Covers,
    _deps,
    _Enricher,
    _Entities,
    _found,
    _Inventing,
    _KnowsHer,
    _Naming,
    _never_called,
    _people_a_box_invented,
    _Pictures,
    _queued,
    _Settings,
    _Studios,
)

pytestmark = pytest.mark.anyio


class TestWhatABatchCameTo:
    """The sentence the job list shows after Auto-enrich has run.

    Without one, pressing Auto-enrich on a Site with twenty possible matches would queue a job,
    run it, write nothing (correctly) and leave a finished green job with no explanation on it,
    which reads as a button that does not work.
    """

    def test_it_says_what_was_filled_in(self) -> None:
        assert "2 filled in" in _enriched(Counter({Outcome.WROTE: 2}))

    def test_more_than_one_match_says_so_and_says_what_to_do(self) -> None:
        """The clause that closes the report. Declining IS the design (choosing between twenty
        candidates is a judgement), but the person who pressed the button is owed the reason and
        the next move, and `Enrich` is the next move."""
        said = _enriched(Counter({Outcome.AMBIGUOUS: 20}))
        assert "20 had more than one match" in said
        assert "Enrich" in said

    def test_a_name_nobody_has_is_a_different_sentence(self) -> None:
        """Not the same event as too many matches, and it does not lead to the same place: nothing
        to choose between means the name here is not the name the boxes file them under."""
        said = _enriched(Counter({Outcome.UNKNOWN: 3}))
        assert "not found" in said
        assert "more than one" not in said

    def test_every_outcome_together_reads_as_one_sentence(self) -> None:
        said = _enriched(
            Counter({Outcome.WROTE: 1, Outcome.LINKED: 2, Outcome.AMBIGUOUS: 3, Outcome.UNKNOWN: 4})
        )
        assert said.count(",") == 3
        assert said.startswith("1 filled in")

    def test_an_empty_batch_still_says_something(self) -> None:
        """A note that is the empty string is a job with no note, which is the state this exists
        to end."""
        assert _enriched(Counter()) == "Nothing to ask about."

    def test_only_the_two_that_need_nobody_count_as_settled(self) -> None:
        """What the job reports as done, and what the queue is allowed to stop asking about."""
        assert Outcome.WROTE.settled and Outcome.LINKED.settled
        assert not Outcome.AMBIGUOUS.settled and not Outcome.UNKNOWN.settled


async def test_the_batch_asks_about_every_subject_it_was_given(
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    entities = _Entities([Outcome.WROTE, Outcome.LINKED])
    context = await context_for(
        STASH_ENRICH,
        {
            "subjects": [
                {"subject": "person", "id": "p1", "name": "Jane"},
                {"subject": "site", "id": "s1", "name": "Northlight"},
            ],
            "key": A_KEY.hex(),
        },
    )

    await enrich_entities(context, entities=entities)  # type: ignore[arg-type]

    assert entities.asked == [
        (Subject.PERSON, "p1", "Jane", A_KEY),
        (Subject.SITE, "s1", "Northlight", A_KEY),
    ]


async def test_the_batch_says_what_it_came_to_where_the_toast_sends_people(
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """A finished job saying nothing is worse than not looking: the toast sends people to the job
    list, and a green job over an unchanged record is indistinguishable from a broken button."""
    entities = _Entities([Outcome.WROTE, Outcome.AMBIGUOUS])
    context = await context_for(
        STASH_ENRICH,
        {
            "subjects": [
                {"subject": "person", "id": "p1", "name": "Jane"},
                {"subject": "person", "id": "p2", "name": "Doe"},
            ],
            "key": None,
        },
    )

    await enrich_entities(context, entities=entities)  # type: ignore[arg-type]

    note = str((await context.queue.get(context.job.id)).note)  # type: ignore[union-attr]
    assert "1 filled in" in note
    assert "1 had more than one match" in note


async def test_a_subject_that_has_gone_is_skipped_rather_than_failing_the_batch(
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """This runs behind somebody's back and the batch is worth finishing. A row with no id, no
    name, a kind Sift has no word for, or a payload entry that is not a row at all, is dropped."""
    entities = _Entities()
    context = await context_for(
        STASH_ENRICH,
        {
            "subjects": [
                "not a row at all",
                {"subject": "widget", "id": "x1", "name": "Whatever"},
                {"subject": "person", "id": "", "name": "Jane"},
                {"subject": "person", "id": "p1", "name": ""},
                {"subject": "person", "id": "p2", "name": "Doe"},
            ],
            "key": None,
        },
    )

    await enrich_entities(context, entities=entities)  # type: ignore[arg-type]

    assert entities.asked == [(Subject.PERSON, "p2", "Doe", None)]


async def test_one_subject_going_wrong_does_not_stop_the_ones_after_it(
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """A batch is a list of separate questions, and one of them raising is a fact about that
    subject alone.

    Here a stash-box answers with an alias the person already holds in a different case and the
    insert refuses it; the rest of the batch must still be asked, and the note must say which one
    failed, since it is written after the loop.
    """
    entities = _Entities(
        [Outcome.WROTE, RuntimeError("that alias is already there"), Outcome.LINKED]
    )
    context = await context_for(
        STASH_ENRICH,
        {
            "subjects": [
                {"subject": "person", "id": "p1", "name": "Jane"},
                {"subject": "person", "id": "p2", "name": "Doe"},
                {"subject": "person", "id": "p3", "name": "Marla"},
            ],
            "key": None,
        },
    )

    await enrich_entities(context, entities=entities)  # type: ignore[arg-type]

    assert [one[1] for one in entities.asked] == ["p1", "p2", "p3"]
    note = str((await context.queue.get(context.job.id)).note)  # type: ignore[union-attr]
    # The partial result is reported in full: what landed, and that one of them did not.
    assert "1 filled in" in note
    assert "1 already agreed" in note
    assert "1 went wrong" in note


async def test_sealed_keys_still_park_the_whole_batch(
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The one exception that must NOT be swallowed. `JobBlocked` says the keys are sealed, which
    is true of every subject in the batch and of none of them in particular: the kernel parks the
    job until somebody signs in, at no cost to its attempts. Counted as a subject that went wrong,
    it would instead be a batch that quietly asked nobody anything."""
    entities = _Entities([JobBlocked("the stash-box keys are sealed")])
    context = await context_for(
        STASH_ENRICH,
        {
            "subjects": [
                {"subject": "person", "id": "p1", "name": "Jane"},
                {"subject": "person", "id": "p2", "name": "Doe"},
            ],
            "key": None,
        },
    )

    with pytest.raises(JobBlocked):
        await enrich_entities(context, entities=entities)  # type: ignore[arg-type]

    assert [one[1] for one in entities.asked] == ["p1"]


async def test_a_batch_with_nothing_in_it_asks_nobody_and_still_says_so(
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    entities = _Entities()
    context = await context_for(STASH_ENRICH, {"subjects": [], "key": None})

    await enrich_entities(context, entities=entities)  # type: ignore[arg-type]

    assert entities.asked == []
    note = str((await context.queue.get(context.job.id)).note)  # type: ignore[union-attr]
    assert note == "Nothing to ask about."


async def test_the_pile_narrowed_to_one_file_holds_that_file_and_counts_only_it(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """What the chooser on a file's own menu asks for.

    Not a second method and not a filter applied afterwards: the pile is PAGED, so a file whose
    match sits on page four would come back empty from a client-side filter and read as "nothing
    found". Same order, same rules, narrowed in the query.
    """
    adapter = _Adapter([_found(0.9, duration_ms=61_000)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    await _a_file(temp_db, "a2")
    deps = _deps(
        _Access([_Asset("a1"), _Asset("a2")]),
        _Settings({SCAN_KEY: True, DURATION_KEY: 10}),
        _Enricher(),
    )
    for asset_id in ("a1", "a2"):
        await scan(
            await context_for(STASH_SCAN, {"asset_id": asset_id, "viewer": "admin"}),
            service=service,
            deps=deps,
        )

    everything, all_of_them = await service.waiting(limit=10)
    just_one, only_it = await service.waiting(limit=10, asset_id="a1")

    assert {one.asset_id for one in everything} == {"a1", "a2"}
    assert all_of_them == 2
    assert [one.asset_id for one in just_one] == ["a1"]
    assert only_it == 1


async def test_a_file_nothing_was_found_for_has_an_empty_pile_of_its_own(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    # A count of nought rather than the whole pile's count, which would tell the sheet there was
    # something waiting and then draw nothing.
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

    rows, total = await service.waiting(limit=10, asset_id="a2")

    assert rows == []
    assert total == 0


async def test_every_person_a_box_invented_and_never_linked_is_asked_of_that_box_once(
    service: StashBoxService,
    temp_db: Database,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The people a box made before 0.1.190: asked by name, of the box that made them and no
    other, and the pass remembered, so a second run asks nobody."""
    box = await _people_a_box_invented(service, temp_db)
    entities = _Entities([Outcome.LINKED])

    await link_what_boxes_invented(
        await context_for(STASH_LINK_INVENTED, {}),
        service=service,
        entities=entities,  # type: ignore[arg-type]
    )

    assert [one[1] for one in entities.asked] == ["p-bare"]
    assert entities.boxes == ["stashdb"]
    assert entities.asked[0][3] == A_KEY
    assert await service.catch_up_ran(invented_pass(box))
    assert not await inventions_owed(service), "a sweep would queue the pass again"

    again = _Entities()
    await link_what_boxes_invented(
        await context_for(STASH_LINK_INVENTED, {}),
        service=service,
        entities=again,  # type: ignore[arg-type]
    )
    assert again.asked == []


async def test_the_people_a_box_invented_are_only_those_still_unlinked(
    service: StashBoxService, temp_db: Database
) -> None:
    """Idempotent before the pass is remembered: a person already linked is never asked again."""
    box = await _people_a_box_invented(service, temp_db)

    assert await service.any_invented_unlinked()
    assert await service.invented_unlinked() == [("p-bare", "Neve Arbor", box, "stashdb")]


async def test_sealed_keys_park_the_linking_pass_without_remembering_it(
    service: StashBoxService,
    temp_db: Database,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    box = await _people_a_box_invented(service, temp_db)
    entities = _Entities()

    with pytest.raises(JobBlocked):
        await link_what_boxes_invented(
            await context_for(STASH_LINK_INVENTED, {}, key=None),
            service=service,
            entities=entities,  # type: ignore[arg-type]
        )

    assert entities.asked == []
    assert not await service.catch_up_ran(invented_pass(box))


async def test_a_box_switched_off_leaves_its_inventions_owed_rather_than_done(
    service: StashBoxService,
    temp_db: Database,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """A box that is off asks nobody, so its pass is NOT remembered: its people wait for a sweep
    with it switched on, while the box beside it, switched on, is asked and remembered."""
    box = await _people_a_box_invented(service, temp_db)
    other = await service.add(
        name="FansDB",
        endpoint="https://fansdb.cc/graphql",
        api_key="a-real-key",
        master_key=A_KEY,
    )
    # Invented for this file; see `tests/gates/data/names_cast.txt`.
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at, created_by_kind, created_by_box_id)"
        " VALUES ('p-other', 'Ines Dray', 0, 'box', ?)",
        (other,),
    )
    await service.set_enabled(other, False)

    first = _Entities([Outcome.LINKED])
    await link_what_boxes_invented(
        await context_for(STASH_LINK_INVENTED, {}),
        service=service,
        entities=first,  # type: ignore[arg-type]
    )
    assert [one[1] for one in first.asked] == ["p-bare"]
    assert await service.catch_up_ran(invented_pass(box))
    assert not await service.catch_up_ran(invented_pass(other)), "an unasked box was remembered"
    assert not await inventions_owed(service), "a sweep would queue a pass that can ask nobody"

    await service.set_enabled(other, True)
    assert await inventions_owed(service)
    then = _Entities([Outcome.LINKED])
    await link_what_boxes_invented(
        await context_for(STASH_LINK_INVENTED, {}),
        service=service,
        entities=then,  # type: ignore[arg-type]
    )
    assert [one[1] for one in then.asked] == ["p-other"], "p-bare was asked twice"
    assert then.boxes == ["fansdb"]
    assert await service.catch_up_ran(invented_pass(other))


async def test_every_site_a_box_made_and_never_linked_is_asked_of_that_box_once(
    service: StashBoxService,
    temp_db: Database,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """A studio or network a box named before its id was kept wears a letter until that box is
    asked. The Site half of the pass asks it, is remembered apart from the people, and a box that
    failed on one is not remembered, so the next pass asks it again."""
    box = await _people_a_box_invented(service, temp_db)
    await service.record_catch_up(invented_pass(box))
    # Invented for this file; see `tests/gates/data/names_cast.txt`.
    for site_id, name, made_by in (
        ("s-bare", "Harbour Network", box),
        ("s-yours", "Quillhouse", None),
    ):
        await temp_db.execute(
            "INSERT INTO sites (id, name, created_at, created_by_kind, created_by_box_id)"
            " VALUES (?, ?, 0, ?, ?)",
            (site_id, name, "box" if made_by else "user", made_by),
        )
    assert await service.invented_sites_unlinked() == [
        ("s-bare", "Harbour Network", box, "stashdb")
    ]
    assert await inventions_owed(service), "the people were asked and the Sites were not"

    failed = _Entities([Outcome.FAILED])
    await link_what_boxes_invented(
        await context_for(STASH_LINK_INVENTED, {}),
        service=service,
        entities=failed,  # type: ignore[arg-type]
    )
    assert failed.asked == [(Subject.SITE, "s-bare", "Harbour Network", A_KEY)]
    assert not await service.catch_up_ran(invented_sites_pass(box)), "a refusal was filed as done"

    linked = _Entities([Outcome.LINKED])
    await link_what_boxes_invented(
        await context_for(STASH_LINK_INVENTED, {}),
        service=service,
        entities=linked,  # type: ignore[arg-type]
    )
    assert [one[1] for one in linked.asked] == ["s-bare"]
    assert await service.catch_up_ran(invented_sites_pass(box))
    assert not await inventions_owed(service)


async def test_a_person_an_exact_match_invents_ends_linked_with_a_cover(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """The whole of the row, end to end through the real service and the real kept match.

    A person a confirmed match creates gets the same photograph a linked lookup brings: the box's
    id is kept on the answer, survives being kept in Sift's own table, and the row the answer made
    is linked by it the moment it is made, and the cover comes with the link.
    """
    adapter = _KnowsHer()
    box = await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    naming = _Naming()
    writers = _Inventing(temp_db, naming)
    settings = _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True, invent_key(Subject.PERSON): True})
    access = _Access([_Asset("a1")])
    covers = _Covers()
    entities = EntityEnricher(
        service,
        access,  # type: ignore[arg-type]
        writers,  # type: ignore[arg-type]
        settings,  # type: ignore[arg-type]
        covers,  # type: ignore[arg-type]
        naming=naming,
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=_deps(access, settings, writers, naming=naming),  # type: ignore[arg-type]
        entities=entities,
    )

    assert naming.made_by == [("person", "p-new", box)]
    assert adapter.people_asked == ["pf-1"], "linked by the box's own id, never by her name"
    (link,) = await service.links_of(Subject.PERSON, "p-new")
    assert (link.source_id, link.remote_id) == (box, "pf-1")
    assert covers.filled == {(Subject.PERSON, "p-new"): b"\x89PNG\r\n\x1a\n"}


async def test_a_batch_entry_carrying_the_boxs_id_is_linked_by_it_rather_than_searched(
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """What a confirmed page queues for the rows it invented: the box and that box's id, so the
    job links them by it. An entry without one is searched by name as it always was, and an id
    from a box this batch was told not to ask is not used: one press, one box."""
    entities = _Entities([Outcome.UNKNOWN, Outcome.UNKNOWN])
    context = await context_for(
        STASH_ENRICH,
        {
            "subjects": [
                {"subject": "person", "id": "p1", "name": "Jane", "box": "b1", "remote_id": "r1"},
                {"subject": "person", "id": "p2", "name": "Jane Roe"},
                {"subject": "site", "id": "s1", "name": "There", "box": "b2", "remote_id": "r2"},
            ],
            "key": A_KEY.hex(),
            "box": "b1",
        },
    )

    await enrich_entities(context, entities=entities)  # type: ignore[arg-type]

    assert entities.known == [(Subject.PERSON, "p1", "b1", "r1", A_KEY)]
    assert [one[1] for one in entities.asked] == ["p2", "s1"]


async def test_an_old_answer_read_as_a_creators_carries_the_studios_id_to_the_creator(
    service: StashBoxService, temp_db: Database
) -> None:
    """A payload kept while a creators box's studios were read as sites carries the studio's id under
    `site`. Read today the studio is the creator, so its id is the creator's. Left under `site`
    it would name a row nothing makes, and the creator a confirmation invents would go unlinked."""
    service._adapter = _Adapter()  # type: ignore[assignment]
    box_id = await service.add(
        name="A creators box",
        endpoint="https://pmvstash.org/graphql",
        api_key="a-real-key",
        master_key=A_KEY,
    )
    await _a_file(temp_db, "a1")
    record = FoundRecord(
        source_id=box_id,
        remote_id="r1",
        subject=Subject.ASSET,
        name="A Clip",
        fields={"site": "Northlight Media"},
        confidence=1.0,
        refs={"site": {"Northlight Media": "st-1"}},
    )
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
        " VALUES (?, ?, 'r1', ?, 'certain', 'applied', 1)",
        ("a1", box_id, as_json([record])),
    )

    held = await service.match("a1", box_id)

    assert held is not None
    assert held.record.refs == {"person": {"Northlight Media": "st-1"}}


async def test_the_registered_file_question_can_link_what_it_invents(
    clean_handlers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan is handed the enricher by the ONE place that registers it. Without it an exact match
    applied by the unattended pass invents a person and never links her, and no test that calls
    `scan` directly could tell: they hand it whatever they like."""
    import sift.slices.stash_boxes.jobs as jobs_module
    from sift.kernel.jobs import registered_handlers

    seen: dict[str, object] = {}

    async def recording_scan(context: object, **kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(jobs_module, "scan", recording_scan)
    entities = object()
    jobs_module.register_handlers(
        service=object(),  # type: ignore[arg-type]
        deps=object(),  # type: ignore[arg-type]
        entities=entities,  # type: ignore[arg-type]
    )

    await registered_handlers()[STASH_SCAN](object())  # type: ignore[arg-type]

    assert seen.get("entities") is entities


# --- kept local, and the apply word carried down a sweep --------------------------------------------


async def test_a_file_kept_local_before_its_question_went_out_is_finished_and_says_why(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """Marked kept local after the job was queued: the job is not a failure and asks nobody: the
    decision is the answer, and the note says it rather than "no box had heard of it"."""
    from sift.kernel.access.catalog import set_kept_local_on

    adapter = _Adapter([_found(EXACT)])
    await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    async with temp_db.write() as connection:
        await set_kept_local_on(connection, "asset", "a1", True)
    deps = _deps(_Access([_Asset("a1")]), _Settings({SCAN_KEY: True}), _Enricher())
    context = await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"})

    await scan(context, service=service, deps=deps)

    job = await context.queue.get(context.job.id)
    assert job is not None and job.note == KEPT_LOCAL_NOTE
    assert (await service.waiting(limit=0))[1] == 0, "nothing came back, because nothing went"


async def test_a_sweep_pressed_with_its_apply_word_hands_it_to_every_child_and_the_next_page(
    service: StashBoxService,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The press decides whether answers are accepted without asking, and it decides for the whole
    run: each question queued and the page that carries on all say the same thing."""
    await _a_box(service, _Adapter())
    access = _Access([_Asset(f"a{n}") for n in range(10)])
    access.cap = 4
    deps = _deps(access, _Settings({SCAN_KEY: True}), _Enricher())

    context = await context_for(STASH_SWEEP, {"viewer": "admin", "offset": 0, "apply": True})
    await sweep(context, service=service, deps=deps)

    assert {one["apply"] for one in await _children(context, STASH_SCAN)} == {True}
    (following,) = await _children(context, STASH_SWEEP)
    assert following["apply"] is True


def test_a_batch_that_met_subjects_kept_local_says_nothing_was_sent_for_them() -> None:
    said = _enriched(Counter({Outcome.WROTE: 1, Outcome.KEPT_LOCAL: 2}))

    assert "2 kept local" in said and "nothing was sent" in said


async def test_a_creator_username_that_lands_queues_the_boxs_picture_of_them(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    register_handler(STASH_CREATOR_PICTURE, _never_called, name="Keeping a picture")
    box_id = await _a_box(service, _Adapter([_a_creators_scene()]))
    await _a_file(temp_db, "a1")
    deps = _deps(
        _Access([_Asset("a1")]),
        _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True}),
        _Enricher(writes=("accounts",)),
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert await _queued(temp_db, STASH_CREATOR_PICTURE) == [
        {
            "box": box_id,
            "studio": "studio-9",
            "asset_id": "a1",
            "site": "OnlyFans",
            "username": "quillmoss",
            "address": "https://onlyfans.com/quillmoss",
        }
    ]


async def test_a_username_the_rules_did_not_write_queues_no_picture(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    """A rule that ignores usernames ignores their pictures too."""
    register_handler(STASH_CREATOR_PICTURE, _never_called, name="Keeping a picture")
    await _a_box(service, _Adapter([_a_creators_scene()]))
    await _a_file(temp_db, "a1")
    deps = _deps(
        _Access([_Asset("a1")]),
        _Settings({SCAN_KEY: True, AUTO_APPLY_KEY: True}),
        _Enricher(writes=("title",)),
    )

    await scan(
        await context_for(STASH_SCAN, {"asset_id": "a1", "viewer": "admin"}),
        service=service,
        deps=deps,
    )

    assert await _queued(temp_db, STASH_CREATOR_PICTURE) == []


def test_a_username_without_the_boxs_id_is_owed_no_picture() -> None:
    """With no id there is nothing to fetch the picture by, and a name search is not an answer."""
    assert creator_pictures_owed(_a_creators_scene(studio_id=None), box_id="b", asset_id="a") == []


async def test_the_picture_job_hands_the_boxs_studio_picture_to_the_seam(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    register_handler(STASH_CREATOR_PICTURE, _never_called, name="Keeping a picture")
    adapter = _Studios()
    box_id = await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    pictures = _Pictures()
    [payload] = creator_pictures_owed(_a_creators_scene(), box_id=box_id, asset_id="a1")

    await keep_creator_picture(
        await context_for(STASH_CREATOR_PICTURE, payload), service=service, pictures=pictures
    )

    assert adapter.studios == ["studio-9"]
    assert pictures.kept == [
        (
            "OnlyFans",
            "quillmoss",
            "https://onlyfans.com/quillmoss",
            b"\x89PNG\r\n\x1a\nstudio",
        )
    ]


async def test_a_creator_who_has_a_picture_costs_the_box_no_request(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    register_handler(STASH_CREATOR_PICTURE, _never_called, name="Keeping a picture")
    adapter = _Studios()
    box_id = await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    [payload] = creator_pictures_owed(_a_creators_scene(), box_id=box_id, asset_id="a1")

    await keep_creator_picture(
        await context_for(STASH_CREATOR_PICTURE, payload),
        service=service,
        pictures=_Pictures(held=True),
    )

    assert adapter.studios == []


async def test_the_picture_job_waits_for_the_keys_rather_than_asking_without_them(
    service: StashBoxService, temp_db: Database, context_for: Callable[..., Awaitable[JobContext]]
) -> None:
    register_handler(STASH_CREATOR_PICTURE, _never_called, name="Keeping a picture")
    box_id = await _a_box(service, _Studios())
    [payload] = creator_pictures_owed(_a_creators_scene(), box_id=box_id, asset_id="a1")

    with pytest.raises(JobBlocked):
        await keep_creator_picture(
            await context_for(STASH_CREATOR_PICTURE, payload, key=None),
            service=service,
            pictures=_Pictures(),
        )


class _NoStudioPicture(_Studios):
    """A box that knows the studio and keeps no picture for it."""

    async def site(self, box: Box, remote_id: str) -> FoundRecord | None:
        self.studios.append(remote_id)
        return FoundRecord(
            source_id=box.id, remote_id=remote_id, subject=Subject.SITE, name="quillmoss"
        )


class _StudiosDown(_Studios):
    async def site(self, box: Box, remote_id: str) -> FoundRecord | None:
        raise StashBoxUnreachable("the box did not answer")


@pytest.mark.parametrize("adapter", [_NoStudioPicture(), _StudiosDown()], ids=["none", "down"])
async def test_a_studio_picture_that_will_not_come_keeps_nothing(
    service: StashBoxService,
    temp_db: Database,
    context_for: Callable[..., Awaitable[JobContext]],
    adapter: _Studios,
) -> None:
    """A studio with no picture, or a box that does not answer, leaves the creator as they were:
    nothing is kept and the job ends rather than failing."""
    register_handler(STASH_CREATOR_PICTURE, _never_called, name="Keeping a picture")
    box_id = await _a_box(service, adapter)
    await _a_file(temp_db, "a1")
    pictures = _Pictures()
    [payload] = creator_pictures_owed(_a_creators_scene(), box_id=box_id, asset_id="a1")

    await keep_creator_picture(
        await context_for(STASH_CREATOR_PICTURE, payload), service=service, pictures=pictures
    )

    assert pictures.kept == []


async def test_a_picture_owed_by_a_box_since_removed_asks_nobody(
    service: StashBoxService,
    temp_db: Database,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    register_handler(STASH_CREATOR_PICTURE, _never_called, name="Keeping a picture")
    adapter = _Studios()
    await _a_box(service, adapter)
    pictures = _Pictures()
    [payload] = creator_pictures_owed(
        _a_creators_scene(), box_id="a-box-since-removed", asset_id="a1"
    )

    await keep_creator_picture(
        await context_for(STASH_CREATOR_PICTURE, payload), service=service, pictures=pictures
    )

    assert (adapter.studios, pictures.kept) == ([], [])


def test_an_account_that_does_not_read_as_one_is_owed_no_picture() -> None:
    record = _a_creators_scene()
    offered = record.fields["accounts"]
    assert isinstance(offered, list)
    odd = FoundRecord(
        source_id=record.source_id,
        remote_id=record.remote_id,
        subject=record.subject,
        name=record.name,
        fields={"accounts": ["quillmoss", *offered]},
        confidence=record.confidence,
        refs=record.refs,
    )
    assert [one["username"] for one in creator_pictures_owed(odd, box_id="b", asset_id="a")] == [
        "quillmoss"
    ]


class _QueueRefuses:
    def __init__(self) -> None:
        self.asked = 0

    async def enqueue(self, job_type: str, payload: object, **options: object) -> str:
        self.asked += 1
        raise RuntimeError("the queue is closed")


async def test_a_picture_the_queue_will_not_take_is_let_go_and_one_owed_nothing_asks_nothing() -> (
    None
):
    """The picture is extra: an answer that landed is never made to fail by it."""
    from sift.slices.stash_boxes.jobs import ask_for_creator_pictures

    refusing = _QueueRefuses()
    written = {"accounts": 1}
    await ask_for_creator_pictures(
        refusing,  # type: ignore[arg-type]
        _a_creators_scene(),
        box_id="b",
        asset_id="a1",
        written=written,
    )
    assert refusing.asked == 1
    await ask_for_creator_pictures(
        refusing,  # type: ignore[arg-type]
        _a_creators_scene(studio_id=None),
        box_id="b",
        asset_id="a1",
        written=written,
    )
    assert refusing.asked == 1
