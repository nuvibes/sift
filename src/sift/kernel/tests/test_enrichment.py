# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rules that decide what a stash-box's answer is allowed to write.

Every one of these is a rule somebody could be shown on a screen, which is why the thing being
tested is a pure function of its arguments. There is no database here and no application: a
decision that needed either to be checked would be a decision nobody could reason about.

The cases that matter are the ones where the obvious implementation is wrong:

  - **an empty offer is not a correction**, so a thin entry on one stash-box cannot blank a record
    somebody typed by hand;
  - **zero is a value**, so a truthiness test would drop a career that started in year zero along
    with every rating of nought;
  - **a list never conflicts**, because two spellings of a name are two spellings and not a
    disagreement;
  - **Merge leaves a filled field alone**, which is the whole of "nothing is silently overwritten".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from sift.kernel.enrichment import (
    DEFAULT_STRATEGY,
    Creating,
    Decision,
    Enricher,
    Missing,
    Outcome,
    Plan,
    Strategy,
    decide,
    declared_field_kind,
    enrichable,
    may_create,
    plan,
    strategy_key,
)
from sift.kernel.ledger import Actor
from sift.kernel.records import Kind, Subject

pytestmark = pytest.mark.regression


def _decide(mine: object, theirs: object, strategy: Strategy, *, is_list: bool = False) -> Decision:
    return decide(key="birth_date", mine=mine, theirs=theirs, strategy=strategy, is_list=is_list)


class TestOneField:
    def test_ignore_never_writes_anything(self) -> None:
        """The strategy that means what it says. Even a blank field is left blank."""
        assert _decide(None, "1990-01-01", Strategy.IGNORE).outcome is Outcome.KEEP

    def test_an_empty_offer_is_not_a_correction(self) -> None:
        """A stash-box that does not know somebody's birthday sends nothing, and nothing is not a
        value. Without this, one thin entry would empty a record somebody typed by hand."""
        nothings: tuple[object, ...] = (None, "", "   ", [], ())
        for nothing in nothings:
            assert _decide("1990-01-01", nothing, Strategy.OVERWRITE).outcome is Outcome.KEEP

    def test_a_blank_field_takes_what_arrives(self) -> None:
        made = _decide(None, "1990-01-01", Strategy.MERGE)
        assert made.outcome is Outcome.WRITE
        assert made.value == "1990-01-01"

    def test_the_same_value_is_not_a_write(self) -> None:
        """Compared without case or padding, so a stash-box sending ` Blonde ` for `blonde` is not
        a change, and a page of matches does not report fields it would not touch."""
        assert _decide("blonde", " Blonde ", Strategy.OVERWRITE).outcome is Outcome.KEEP

    def test_merge_leaves_a_filled_field_alone_and_says_why(self) -> None:
        """The rule the whole design rests on: nothing is silently overwritten. A disagreement is a
        question, and the question is what the reconcile screen is a list of."""
        made = _decide("1990-01-01", "1991-02-02", Strategy.MERGE)
        assert made.outcome is Outcome.CONFLICT
        assert (made.mine, made.theirs) == ("1990-01-01", "1991-02-02")
        assert made.value is None

    def test_overwrite_is_the_one_that_replaces(self) -> None:
        made = _decide("1990-01-01", "1991-02-02", Strategy.OVERWRITE)
        assert made.outcome is Outcome.WRITE
        assert made.value == "1991-02-02"

    def test_zero_is_a_value_and_not_a_blank(self) -> None:
        """A truthiness test would drop both halves of this: an offered nought would read as
        nothing to write, and a held nought as a field waiting to be filled."""
        assert _decide(None, 0, Strategy.MERGE).outcome is Outcome.WRITE
        assert _decide(0, 1995, Strategy.MERGE).outcome is Outcome.CONFLICT


class TestAList:
    def test_merge_adds_what_is_new_and_keeps_the_order(self) -> None:
        """What is there, then what arrived. A record is read in its order."""
        made = decide(
            key="aliases",
            mine=["Jane"],
            theirs=["JD", "Jane Doe"],
            strategy=Strategy.MERGE,
            is_list=True,
        )
        assert made.value == ["Jane", "JD", "Jane Doe"]

    def test_a_repeat_is_not_a_new_entry_whatever_its_case(self) -> None:
        """`Jane` and `jane` are one alias, and keeping both is how a chooser fills up with the
        same name twice."""
        made = decide(
            key="aliases", mine=["Jane"], theirs=[" jane "], strategy=Strategy.MERGE, is_list=True
        )
        assert made.outcome is Outcome.KEEP

    def test_a_list_never_conflicts(self) -> None:
        """Two stash-boxes each knowing a different spelling is two spellings, not a disagreement."""
        made = decide(
            key="aliases", mine=["Jane"], theirs=["Other"], strategy=Strategy.MERGE, is_list=True
        )
        assert made.outcome is Outcome.WRITE

    def test_overwrite_on_a_list_replaces_it(self) -> None:
        made = decide(
            key="aliases",
            mine=["Jane"],
            theirs=["Only"],
            strategy=Strategy.OVERWRITE,
            is_list=True,
        )
        assert made.value == ["Only"]

    def test_a_bare_value_is_one_entry_and_not_a_pile_of_letters(self) -> None:
        made = decide(
            key="aliases", mine=None, theirs="Solo", strategy=Strategy.MERGE, is_list=True
        )
        assert made.value == ["Solo"]

    def test_an_entry_that_is_neither_text_nor_a_mapping_still_compares(self) -> None:
        """A list of numbers is not a shape any adapter sends today, and the comparison must not
        fall over on one anyway: a value it cannot fold is compared as it is."""
        made = decide(
            key="aliases", mine=[1, 2], theirs=[2, 3], strategy=Strategy.MERGE, is_list=True
        )
        assert made.value == [1, 2, 3]

    def test_entries_that_are_mappings_compare_by_their_contents(self) -> None:
        """A username entry is a mapping, and two are the same entry when they say the same thing.
        Compared this way because a mapping cannot be put in a set."""
        held = [{"site": "X", "handle": "neve", "url": ""}]
        made = decide(
            key="accounts",
            mine=held,
            theirs=list(held),
            strategy=Strategy.MERGE,
            is_list=True,
        )
        assert made.outcome is Outcome.KEEP


class TestAWholePlan:
    def test_it_walks_the_registry_rather_than_what_arrived(self) -> None:
        """A key nothing declared is never written. An adapter is supposed to answer in Sift's own
        field keys, and a key nobody declared is a translation that did not happen."""
        made = plan(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            mine={},
            theirs={"aliases": ["Jane"], "invented_by_nobody": "x"},
            strategies={},
        )
        assert "invented_by_nobody" not in {one.key for one in made.decisions}
        assert made.writes == {"aliases": ["Jane"]}

    def test_a_field_no_stash_box_may_fill_is_never_written(self) -> None:
        """A file's size is not an opinion. Declared unimportable, so no strategy can reach it."""
        made = plan(
            subject=Subject.ASSET,
            local_id="a",
            source_id="b",
            mine={},
            theirs={"size_bytes": 1, "title": "A Title"},
            strategies={"size_bytes": Strategy.OVERWRITE},
        )
        assert made.writes == {"title": "A Title"}

    def test_the_conflicts_are_separable_from_the_writes(self) -> None:
        made = plan(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            mine={"name": "Jane", "aliases": []},
            theirs={"name": "Jane Doe", "aliases": ["JD"]},
            strategies={},
        )
        assert [one.key for one in made.conflicts] == ["name"]
        assert made.writes == {"aliases": ["JD"]}

    def test_the_default_is_merge_when_nobody_has_said(self) -> None:
        assert DEFAULT_STRATEGY is Strategy.MERGE
        made = plan(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            mine={"name": "Jane"},
            theirs={"name": "Other"},
            strategies={},
        )
        assert made.decisions[0].outcome is Outcome.CONFLICT


class _Writer:
    """A writer that remembers what it was handed, which is the whole of what a writer is for."""

    subject = Subject.PERSON

    def __init__(self, held: Mapping[str, object] | None = None) -> None:
        self.held = dict(held or {})
        self.written: dict[str, object] = {}
        self.creating: Creating | None = None

    async def current(self, local_id: str) -> Mapping[str, object]:
        _ = local_id
        return self.held

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        named = values.get("aliases")
        if not isinstance(named, (list, tuple)):
            return ()
        return tuple(Missing(name=str(one), kind=Subject.PERSON.value) for one in named)

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Takes everything and says so. A writer answers with the keys it actually wrote, and a
        double that took a field and reported nothing would be a double that cannot be planned
        against. See `_DropsOne` for the other half of that.

        One row per field, which is what a scalar means. A list field's real count is the writer's
        own business and is proved where the writers are."""
        _ = local_id, actor
        self.written.update(values)
        self.creating = creating
        return dict.fromkeys(values, 1)


class _DropsOne:
    """A writer with somewhere to put a name and nowhere to put the details.

    The shape a writer can have: a field declared importable, offered by every plan, handed
    over, and silently dropped. It is a double rather than a story because the honest count can only
    be checked against a writer that genuinely writes less than it is given.
    """

    subject = Subject.PERSON

    def __init__(self) -> None:
        self.written: dict[str, object] = {}

    async def current(self, local_id: str) -> Mapping[str, object]:
        _ = local_id
        return {}

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        _ = values
        return ()

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        _ = local_id, creating, actor
        if "name" not in values:
            return {}
        self.written["name"] = values["name"]
        return {"name": 1}


class _ReadsMany(_Writer):
    """A writer that reads a whole list in one go and has no row for some of what it is asked."""

    def __init__(self, rows: Mapping[str, Mapping[str, object]]) -> None:
        super().__init__({"name": "read alone"})
        self.rows = rows
        self.read_alone: list[str] = []

    async def current_many(self, local_ids: Sequence[str]) -> Mapping[str, Mapping[str, object]]:
        return {one: self.rows[one] for one in local_ids if one in self.rows}

    async def current(self, local_id: str) -> Mapping[str, object]:
        self.read_alone.append(local_id)
        return self.held


class TestTheRegistry:
    def test_a_subject_can_only_be_claimed_once(self) -> None:
        """Two features each believing they own a person is the drift a registry exists to stop."""
        enricher = Enricher()
        enricher.register(_Writer())
        with pytest.raises(ValueError, match="already registered"):
            enricher.register(_Writer())

    async def test_it_plans_through_the_writer_that_registered(self) -> None:
        enricher = Enricher()
        enricher.register(_Writer({"name": "Jane"}))
        assert enricher.can_write(Subject.PERSON)
        made = await enricher.plan_for(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            offered={"aliases": ["JD"]},
            strategies={},
        )
        assert made is not None
        assert made.writes == {"aliases": ["JD"]}

    async def test_a_subject_nothing_can_write_answers_with_nothing(self) -> None:
        """An older build meeting a subject it has no writer for draws no plan rather than failing.
        The alternative is a screen that cannot open because one feature is absent."""
        enricher = Enricher()
        assert not enricher.can_write(Subject.TAG)
        assert (
            await enricher.plan_for(
                subject=Subject.TAG, local_id="t", source_id="b", offered={}, strategies={}
            )
            is None
        )
        empty = Plan(subject=Subject.TAG, local_id="t", source_id="b")
        assert await enricher.apply(empty) == {}
        assert await enricher.missing_for(empty) == ()

    async def test_what_is_held_is_read_in_one_go_where_the_writer_can_and_every_id_answered(
        self,
    ) -> None:
        """A survey plans against every linked subject of a kind: one read for the list where the
        writer can, one read each for the rest, and an id the list had no row for read alone, so
        no plan is ever made against nothing."""
        many = _ReadsMany({"p1": {"name": "Jane"}, "p2": {"name": "June"}})
        enricher = Enricher()
        enricher.register(many)

        held = await enricher.currents(Subject.PERSON, ["p1", "p2", "p3"])

        assert held == {
            "p1": {"name": "Jane"},
            "p2": {"name": "June"},
            "p3": {"name": "read alone"},
        }
        assert many.read_alone == ["p3"]

        one_by_one = Enricher()
        one_by_one.register(_Writer({"name": "Jane"}))
        assert await one_by_one.currents(Subject.PERSON, ["p1", "p2"]) == {
            "p1": {"name": "Jane"},
            "p2": {"name": "Jane"},
        }
        assert await one_by_one.currents(Subject.PERSON, []) == {}
        assert await one_by_one.currents(Subject.TAG, ["t"]) == {}

    def test_a_plan_against_what_was_read_already_is_the_plan_of_a_fresh_read(self) -> None:
        enricher = Enricher()
        enricher.register(_Writer())
        made = enricher.plan_against(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            mine={"name": "Jane"},
            offered={"aliases": ["JD"], "name": "Someone Else"},
            strategies={},
        )
        assert made is not None
        assert made.writes == {"aliases": ["JD"]}
        assert [one.outcome for one in made.decisions if one.key == "name"] == [Outcome.CONFLICT]
        assert (
            enricher.plan_against(
                subject=Subject.TAG, local_id="t", source_id="b", mine={}, offered={}, strategies={}
            )
            is None
        )

    async def test_applying_writes_only_what_the_plan_decided(self) -> None:
        writer = _Writer({"name": "Jane"})
        enricher = Enricher()
        enricher.register(writer)
        made = await enricher.plan_for(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            offered={"aliases": ["JD"], "name": "Someone Else"},
            strategies={},
        )
        assert made is not None
        assert await enricher.apply(made, creating=True) == {"aliases": 1}
        # The name disagreed, so it is a conflict and is not among the writes.
        assert writer.written == {"aliases": ["JD"]}
        assert writer.creating is True

    async def test_what_comes_back_is_what_the_writer_wrote_and_not_what_it_was_asked(self) -> None:
        """The count on a confirm toast is the writer's, not the ASK's.

        A count taken from the keys handed in can only ever agree with the ask, so fields a file
        writer has no setter for would be reported written while nothing wrote them.
        """
        writer = _DropsOne()
        enricher = Enricher()
        enricher.register(writer)
        made = await enricher.plan_for(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            offered={"name": "Jane", "details": "Some words."},
            strategies={},
        )
        assert made is not None
        assert sorted(made.writes) == ["details", "name"]

        assert await enricher.apply(made) == {"name": 1}

    async def test_a_field_a_writer_drops_is_not_reported_as_settled_by_hand_either(self) -> None:
        """The same honesty on the reconcile path. A press that wrote nothing has settled nothing,
        and a screen told otherwise goes on showing the disagreement it just said was answered."""
        enricher = Enricher()
        enricher.register(_DropsOne())

        assert (
            await enricher.write_one(
                Subject.PERSON, "p1", "details", "Some words.", actor=Actor.sift("stash")
            )
            is False
        )
        assert (
            await enricher.write_one(
                Subject.PERSON, "p1", "name", "Jane", actor=Actor.sift("stash")
            )
            is True
        )

    async def test_a_tick_narrows_a_plan_and_cannot_widen_it(self) -> None:
        """The ticks say which of the plan's own writes to take. Naming a field the plan did not
        decide on adds nothing: a conflict stays a conflict."""
        writer = _Writer({"name": "Jane"})
        enricher = Enricher()
        enricher.register(writer)
        made = await enricher.plan_for(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            offered={"aliases": ["JD"], "name": "Someone Else"},
            strategies={},
        )
        assert made is not None
        assert await enricher.apply(made, only=["name"]) == {}
        assert writer.written == {}

    async def test_it_counts_what_would_be_created_without_creating_it(self) -> None:
        enricher = Enricher()
        enricher.register(_Writer())
        made = await enricher.plan_for(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            offered={"aliases": ["JD"]},
            strategies={},
        )
        assert made is not None
        assert await enricher.missing_for(made) == (Missing(name="JD", kind="person"),)

    async def test_a_plan_with_nothing_to_write_touches_nothing(self) -> None:
        writer = _Writer({"name": "Jane"})
        enricher = Enricher()
        enricher.register(writer)
        made = await enricher.plan_for(
            subject=Subject.PERSON,
            local_id="p",
            source_id="b",
            offered={"name": "Jane"},
            strategies={},
        )
        assert made is not None
        assert await enricher.apply(made) == {}
        assert writer.written == {}


class TestOneFieldOnItsOwn:
    """The seam a reconcile press writes through, which is one field and never a plan.

    It exists so that a value taken from a stash-box lands exactly where a typed one would: the
    subject's own writer, which is the one place that knows what a person's birthdate is stored in.
    A second path would be a second answer to that question.
    """

    async def test_it_writes_through_the_subjects_own_writer(self) -> None:
        enricher = Enricher()
        writer = _Writer()
        enricher.register(writer)

        wrote = await enricher.write_one(
            Subject.PERSON, "p1", "birth_date", "1991-02-02", actor=Actor.sift("stash")
        )

        assert wrote is True
        assert writer.written == {"birth_date": "1991-02-02"}

    async def test_creating_is_never_asked_for_by_this_one(self) -> None:
        """One field, agreed to on a screen showing both answers. Inventing a person while writing
        it would be a decision nobody took."""
        enricher = Enricher()
        writer = _Writer()
        enricher.register(writer)

        await enricher.write_one(
            Subject.PERSON, "p1", "aliases", ["Another"], actor=Actor.sift("stash")
        )

        assert writer.creating is False

    async def test_a_subject_nothing_can_write_says_so_rather_than_raising(self) -> None:
        """An older build meeting a newer one. The screen reports that it did not land; it does not
        fail the request."""
        assert (
            await Enricher().write_one(
                Subject.TAG, "t1", "category", "Location", actor=Actor.sift("stash")
            )
            is False
        )


class TestTheNames:
    def test_a_settings_key_is_built_from_the_registry_and_not_written_out(self) -> None:
        assert strategy_key(Subject.PERSON, "birth_date") == "enrich.person.birth_date"

    def test_the_enrichable_fields_are_the_importable_ones(self) -> None:
        """Not the editable ones. A person's usernames are written by an import and are never typed
        into the record form, which is why the registry asks those two questions separately."""
        keys = enrichable(Subject.PERSON)
        assert "accounts" in keys
        assert "sources" not in keys

    def test_a_kind_can_be_looked_up_by_key(self) -> None:
        assert declared_field_kind(Subject.PERSON, "accounts") is Kind.ACCOUNTS
        assert declared_field_kind(Subject.PERSON, "no_such_field") is None


class TestWhichRowsMayBeInvented:
    """The permission a writer asks per NAME.

    A bool OR a set, because half of what a stash-box names is worth having and half is somebody
    else's filing, and one answer for both loses whichever half you wanted. One shape rather than
    a flag beside a list, because two parameters can disagree and every writer would then have to
    decide which of them wins.

    Asked through this one function by every writer, so that `True` and an empty set cannot come to
    mean different things in two places.
    """

    def test_yes_means_every_name(self) -> None:
        assert may_create(True, "person", "Jane") is True

    def test_no_means_none_of_them(self) -> None:
        assert may_create(False, "person", "Jane") is False

    def test_a_set_admits_the_names_in_it(self) -> None:
        allowed: Creating = frozenset({("person", "Jane")})

        assert may_create(allowed, "person", "Jane") is True

    def test_a_set_refuses_a_name_that_is_not_in_it(self) -> None:
        allowed: Creating = frozenset({("person", "Jane")})

        assert may_create(allowed, "person", "Doe") is False

    def test_the_kind_is_part_of_the_answer(self) -> None:
        # A person and a tag can be spelled the same, and ticking one must not invent the other.
        allowed: Creating = frozenset({("person", "Jane")})

        assert may_create(allowed, "tag", "Jane") is False

    def test_an_empty_set_is_a_real_answer_and_means_invent_nothing(self) -> None:
        # Not the absence of an answer. A page where nothing was ticked sends this, and it has to
        # mean "no" rather than falling back to anything.
        assert may_create(frozenset(), "person", "Jane") is False
