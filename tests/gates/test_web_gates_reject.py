# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser-client gates, each watched rejecting the thing it exists to catch.

A check that has never failed is indistinguishable from one that cannot. Two of the browser gates
have a test of this shape (the dead-CSS one and the native-chrome one), and without one here the
other four's only evidence would be that they print a number. The one-server-type gate has its
three.

Every plant here is written into the real source tree rather than into a copy, because each gate
reads the project's own layout and a copy of it would be a different project. Two habits follow
from that and both are deliberate:

  * Each fixture violates ONE gate. The suite runs in parallel, so a fixture that is live while a
    different gate reads the tree must not make that gate say something untrue.
  * Nothing here asserts the clean tree passes. `ci-local.sh` runs every one of them against the clean
    tree as their own steps, and an assertion of that here could be reddened by another test's
    fixture rather than by anything being wrong.

The plants that need a stylesheet live under `routes/`, where the shared-component gate does not
look. A bare `.svelte` file there is not a route (only `+page.svelte` is), so nothing else finds
them either.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.gates import PLANTED_PREFIX, the_client_tree

pytestmark = [pytest.mark.gate]

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
SOURCE = FRONTEND / "src"


def _run(gate: str) -> subprocess.CompletedProcess[str]:
    script = FRONTEND / "scripts" / gate
    assert script.is_file(), f"{gate} is not where this test expects it"
    return subprocess.run(
        ["node", str(script)],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


@contextmanager
def _planted(where: Path, body: str) -> Iterator[None]:
    """The fixture in the real tree, and nothing else reading that tree while it is there.

    The lock is what makes the plant safe rather than the naming convention, which does not hold
    on its own (see `the_client_tree`).
    """
    with the_client_tree(), _written(where, body):
        yield


@contextmanager
def _written(where: Path, body: str) -> Iterator[None]:
    """The fixture in the real tree, for a test that already holds the tree."""
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(body, encoding="utf-8")
    try:
        yield
    finally:
        where.unlink(missing_ok=True)


def test_every_plant_is_named_so_the_other_gates_skip_it() -> None:
    """The prefix is load-bearing and nothing else says so.

    These files exist for a few milliseconds each, in the real tree, while the rest of the suite is
    reading it. Every gate that walks the client's source filters them out BY NAME (see
    `tests/gates/__init__.py`), which is what stops one of them listing a plant and then failing on
    a file that has since been removed. A plant named anything else reopens that window, and the
    failure it produces belongs to whichever gate happened to be reading at the time.
    """
    written = [
        piece
        for line in Path(__file__).read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("plant = SOURCE /")
        for piece in line.split('"')
        if piece.endswith(".svelte")
    ]

    assert written, "no plants found: this check has stopped reading the file it is about"
    unprefixed = [name for name in written if not name.startswith(PLANTED_PREFIX)]
    assert not unprefixed, (
        f"these fixtures are planted into the real tree without the {PLANTED_PREFIX} prefix, "
        f"so every gate walking that tree can read one mid-run: {unprefixed}"
    )


def test_a_class_handed_to_a_component_with_a_scoped_rule_is_caught() -> None:
    """The third case: a rule and an element that cannot see each other.

    The class lands on an element compiled in the component's file and carries that file's scope
    hash, so a scoped selector here matches it never. Neither of the two gates either side can see
    it: the dead-CSS one finds the class in the markup and believes the rule is live, and the
    styled one finds a rule and believes the class is dressed.
    """
    plant = SOURCE / "routes" / "GateFixtureHandedClass.svelte"
    body = (
        "<script lang='ts'>\n"
        "\timport Badge from '$lib/components/common/Badge.svelte';\n"
        "</script>\n\n"
        '<Badge class="handed-to-a-component" state="done" />\n\n'
        "<style>\n"
        "\t.handed-to-a-component {\n"
        "\t\tcolor: var(--sift-ink);\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_handed_class.js")

    assert answer.returncode == 1, "the gate passed a scoped rule that reaches nothing"
    assert "handed-to-a-component" in answer.stderr


def test_the_same_class_on_a_plain_element_too_is_not_reported() -> None:
    """The one thing that gate must not do.

    A class can legitimately be on BOTH a component and a plain element in one file (the search
    box hands `.token` to a chip and also writes it on a suggestion row), and there the scoped
    rule is correct, because it reaches the plain element. One implementation of the rule holds
    the exemption; a second copy elsewhere would be two implementations of one rule.

    Asserted on the CLASS NAME rather than on the exit code: another test's fixture can be live in
    the tree at this moment, and this is a statement about this fixture only.
    """
    plant = SOURCE / "routes" / "GateFixtureBothForms.svelte"
    body = (
        "<script lang='ts'>\n"
        "\timport Badge from '$lib/components/common/Badge.svelte';\n"
        "</script>\n\n"
        '<Badge class="on-both-forms" state="done" />\n'
        '<span class="on-both-forms">and on a plain element</span>\n\n'
        "<style>\n"
        "\t.on-both-forms {\n"
        "\t\tcolor: var(--sift-ink);\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_handed_class.js")

    assert "on-both-forms" not in answer.stderr, (
        "a class that is also on a plain element was reported; the scoped rule reaches that element"
    )


def test_a_class_in_the_markup_with_no_rule_is_caught() -> None:
    """The other direction from the dead-CSS gate: an element that reaches no rule.

    It ships as a bare box with no gap and no alignment, and nothing about either half of the file
    looks wrong: the markup names a class and the stylesheet is full of other rules.
    """
    plant = SOURCE / "routes" / "GateFixtureUnstyled.svelte"
    body = (
        '<div class="dressed"><span class="never-dressed-anywhere">x</span></div>\n\n'
        "<style>\n"
        "\t.dressed {\n"
        "\t\tcolor: var(--sift-ink);\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_styled.js")

    assert answer.returncode == 1, "the gate passed a class with no rule anywhere in the file"
    assert "never-dressed-anywhere" in answer.stderr


def test_a_control_labelled_with_a_word_from_inside_the_machinery_is_caught() -> None:
    """The vocabulary gate: a button whose label opens with a verb the table does not allow, and a
    word from inside the machinery, are both refused."""
    plant = SOURCE / "routes" / "GateFixtureVocab.svelte"
    body = (
        '<script lang="ts">\n'
        "\timport Button from '$lib/components/common/Button.svelte';\n"
        "</script>\n\n"
        "<Button>Settle it</Button>\n"
    )

    with _planted(plant, body):
        answer = _run("check_vocabulary.js")

    assert answer.returncode == 1, "the gate passed a label the vocabulary refuses"
    assert "GateFixtureVocab" in answer.stderr


def test_a_sentence_a_person_would_contract_is_caught() -> None:
    """The vocabulary gate's contractions ratchet: a file with no entry is held at zero, so one
    planted "cannot" in a sentence on screen is refused and the contraction is named."""
    plant = SOURCE / "routes" / "GateFixtureContraction.svelte"
    body = "<p>Sift cannot reach that folder.</p>\n"

    with _planted(plant, body):
        answer = _run("check_vocabulary.js")

    assert answer.returncode == 1, "the gate passed a sentence that says cannot"
    assert "GateFixtureContraction" in answer.stderr
    assert "can't" in answer.stderr


def test_a_date_written_by_hand_is_caught() -> None:
    """One time format: a date built anywhere but `lib/shell/when.ts` is refused, whether by a locale
    method or by a second ladder of relative words."""
    plant = SOURCE / "routes" / "GateFixtureOwnDate.svelte"
    body = (
        '<script lang="ts">\n'
        "\tconst when = new Date(0).toLocaleDateString();\n"
        "\tconst ago = `${3}m ago`;\n"
        "</script>\n\n"
        "<p>{when} {ago}</p>\n"
    )

    with _planted(plant, body):
        answer = _run("check_one_time_format.js")

    assert answer.returncode == 1, "the gate passed a date written by hand"
    assert "GateFixtureOwnDate" in answer.stderr


def test_a_screen_that_scrolls_itself_is_caught() -> None:
    """A declaration, not a mention, and not anchored to the start of a line.

    A pattern anchored as `^\\s*overflow` misses a declaration written inline. The plant here is
    written the way prettier writes one, and a second one inline, so the ratchet moves by two
    however the pattern is later tightened.
    """
    plant = SOURCE / "routes" / "GateFixtureOwnScroll.svelte"
    body = (
        '<div class="own-scroll">x</div>\n\n'
        "<style>\n"
        "\t.own-scroll {\n"
        "\t\toverflow-y: auto;\n"
        "\t}\n"
        "\t.own-scroll:hover { overflow-x: scroll; }\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_one_scrollbar.js")

    assert answer.returncode == 1, "the gate passed a screen painting its own scrollbar"
    assert "GateFixtureOwnScroll" in answer.stderr


def test_a_shared_component_with_no_reason_and_no_gallery_entry_is_caught() -> None:
    """The half that is not a ratchet: one line, in the file, or the library.

    Planted in the primitives folder, because that is the population the written-reason half asks
    about. It draws no button and declares no styles, so it is invisible to every other gate while
    it is there.
    """
    plant = SOURCE / "lib" / "components" / "common" / "GateFixtureBitsFirst.svelte"
    body = "<div>a primitive that says nothing about why it is not from the library</div>\n"

    with _planted(plant, body):
        answer = _run("check_bits_first.js")

    assert answer.returncode == 1, "the gate passed a primitive with no written judgement"
    assert "GateFixtureBitsFirst" in answer.stderr
    assert "neither use bits-ui nor say why not" in answer.stderr


def test_a_global_rule_with_nothing_of_its_own_in_front_is_caught() -> None:
    """The fourth case, and the one that reaches furthest.

    A rule that STARTS with `:global(...)` is not a rule about the component that wrote it. It is a
    rule about every screen in the application, in force from the moment that stylesheet loads,
    and SvelteKit loads a route's stylesheet when the pointer touches a link to it, not when
    somebody opens it. A panel capping `:global(.scroll-root)` at 60vh for its own list takes the
    height off the bottom of the library, of Settings and of every other screen, for the rest of
    the browser session, after a hover.

    None of the three gates beside this one can see it: the selector matches plenty, the class has a
    rule, and the rule is already global.
    """
    plant = SOURCE / "routes" / "GateFixtureLooseGlobal.svelte"
    body = (
        "<div class='mine'>a panel of my own</div>\n\n"
        "<style>\n"
        "\t:global(.scroll-root) {\n"
        "\t\tmax-block-size: 60vh;\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_anchored_globals.js")

    assert answer.returncode == 1, "the gate passed a global rule that reaches every screen"
    assert "scroll-root" in answer.stderr


def test_a_global_rule_anchored_by_this_file_is_not_reported() -> None:
    """The thing that gate must not do.

    Reaching into an element somebody else rendered is ordinary and correct: every class this app
    hands to bits-ui is dressed that way, and the fix for the fault above is not to stop doing it
    but to put something of your own in front. A rule bounded by a class this file writes, or one
    whose tail is scoped here, is a sentence about this component and must pass.
    """
    plant = SOURCE / "routes" / "GateFixtureAnchoredGlobal.svelte"
    body = (
        # The scroll area inside is rendered by the shared scroller, never written here: a file
        # writing a class the scroller owns would be a second writer of it, a fault of its own.
        "<div class='capped'>bounded</div>\n"
        "<button class='gate-fixture-own'>and a button of my own</button>\n\n"
        "<style>\n"
        "\t/* Bounded by a class this file writes. */\n"
        "\t.capped :global(.scroll-root) {\n"
        "\t\tmax-block-size: 60vh;\n"
        "\t}\n"
        "\n"
        "\t/* Bounded by the second half of its own first compound, a class no other file writes. */\n"
        "\t:global(.btn.gate-fixture-own) {\n"
        "\t\tcolor: var(--sift-ink);\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_anchored_globals.js")

    assert "GateFixtureAnchoredGlobal" not in answer.stderr, (
        "a global rule bounded by this file's own class was reported"
    )


def test_a_second_copy_of_a_file_s_facts_is_caught() -> None:
    """The fifth case: a second copy of a file's facts.

    Several surfaces draw a file's size, length, rate, shape, encoder and bit depth. Each with its
    own copy of the arithmetic, one divides by 1024 and another by 1000, or one spells the encoder
    `H.264` where the record beside it spells it `h264`, and one file reads `5.6 GB` in one panel
    and `6.0 GB` on its own record two inches below.

    Nothing else catches it. Every copy is right about itself, every copy has a passing test, and no
    check in the repository compares two surfaces to each other. The gate refuses the two things a
    second copy cannot be written without, anywhere but the one file that holds them.
    """
    plant = SOURCE / "routes" / "GateFixtureSecondFacts.svelte"
    body = (
        "<script lang='ts'>\n"
        "\tconst units = ['B', 'KB', 'MB', 'GB', 'TB'];\n"
        "\tconst named: Record<string, string> = { h264: 'H.264' };\n"
        "</script>\n\n"
        "<p class='mine'>{units[0]}{named.h264}</p>\n\n"
        "<style>\n"
        "\t.mine {\n"
        "\t\tcolor: var(--sift-ink);\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_one_facts_definition.js")

    assert answer.returncode == 1, "the gate passed a second copy of a file's facts"
    assert "GateFixtureSecondFacts" in answer.stderr
    assert "a ladder of byte units" in answer.stderr
    assert "a table of encoder names" in answer.stderr


def test_a_test_naming_what_a_panel_should_read_is_not_reported() -> None:
    """The thing that gate must not do.

    A test asserting a panel reads `H.264` is the evidence the one definition works. Refusing it
    would leave the gate satisfiable only by having no test of the thing it protects, which is the
    shape of a check that makes a codebase worse.
    """
    plant = SOURCE / "lib" / "GateFixtureFactsExpectation.test.ts"
    body = (
        "import { expect, it } from 'vitest';\n"
        "import { codec, size } from '$lib/library/facts';\n\n"
        "it('says what a panel should read', () => {\n"
        "\texpect(codec('h264')).toBe('H.264');\n"
        "\texpect(size(6_000_000_000)).toBe('6.0 GB');\n"
        "});\n"
    )

    with _planted(plant, body):
        answer = _run("check_one_facts_definition.js")

    assert answer.returncode == 0, "a test naming the expected output was reported as a second copy"


def test_a_second_copy_of_a_schema_the_server_already_has_is_caught() -> None:
    """A shape written out beside the generated one.

    `CollectionSummary` is real and the client reads it from the schema. A file that describes it
    again by hand is outside every piece of machinery that keeps the two in step (the regenerate-
    and-diff step in CI has nothing to say about a type it did not generate), so the day the
    server changes, this copy keeps compiling and starts being wrong.
    """
    plant = SOURCE / "routes" / "GateFixtureSecondSchema.svelte"
    body = (
        "<script lang='ts'>\n"
        "\tinterface Shelf {\n"
        "\t\tid: string;\n"
        "\t\tname: string;\n"
        "\t\titem_count: number;\n"
        "\t\tcover_asset_id: string | null;\n"
        "\t}\n"
        "\tlet shelf: Shelf | null = null;\n"
        "</script>\n\n"
        "<p>{shelf?.name}</p>\n"
    )

    with _planted(plant, body):
        answer = _run("check_one_server_type.js")

    assert answer.returncode == 1, "the gate passed a hand-written copy of a schema"
    assert "GateFixtureSecondSchema" in answer.stderr
    assert "CollectionSummary" in answer.stderr


def test_a_reply_described_where_it_is_asked_for_is_caught() -> None:
    """The second rule, which does not care how many fields there are.

    A two-field reply is too small for the subset rule above (`{ id, name }` is half the document
    and every dropdown row anybody has written), so the small ones are caught by WHERE they are
    written instead. An inline shape at the call is a description of the server in the one place
    nothing checks it.
    """
    plant = SOURCE / "routes" / "GateFixtureInlineReply.svelte"
    body = (
        "<script lang='ts'>\n"
        "\timport { api } from '$lib/api/client';\n"
        "\tasync function ask() {\n"
        "\t\treturn await api.get<{ counted: number }>('/tags');\n"
        "\t}\n"
        "</script>\n\n"
        "<button onclick={() => void ask()}>ask</button>\n"
    )

    with _planted(plant, body):
        answer = _run("check_one_server_type.js")

    assert answer.returncode == 1, "the gate passed a reply described inline at the call"
    assert "GateFixtureInlineReply" in answer.stderr
    assert "counted" in answer.stderr


def test_a_narrower_slice_taken_from_the_schema_is_not_reported() -> None:
    """The thing that gate must not do, and the reason the rule is not "no local types".

    A screen that wants four fields of a forty-field record should say so: drawing a whole
    `AssetDetail` where a tile needs an id and a size is not an improvement. `Pick<>` is how that is
    written, and it stays correct by construction: the day the server drops one of the four, this
    fails to compile instead of quietly filling in nothing.
    """
    plant = SOURCE / "routes" / "GateFixtureNarrowSlice.svelte"
    body = (
        "<script lang='ts'>\n"
        "\timport type { components } from '$lib/api/schema';\n"
        "\ttype Shelf = Pick<\n"
        "\t\tcomponents['schemas']['CollectionSummary'],\n"
        "\t\t'id' | 'name' | 'item_count' | 'cover_asset_id'\n"
        "\t>;\n"
        "\tlet shelf: Shelf | null = null;\n"
        "</script>\n\n"
        "<p>{shelf?.name}</p>\n"
    )

    with _planted(plant, body):
        answer = _run("check_one_server_type.js")

    assert "GateFixtureNarrowSlice" not in answer.stderr, (
        "a `Pick<>` of the generated schema was reported as a second copy of it"
    )


def test_a_hover_state_with_nothing_animating_it_is_caught() -> None:
    """The SNAP half of the hover ratchet, watched refusing.

    The fault it is for: a shared button whose every tone has a hover and none a transition, so
    every button in the application changes between one frame and the next. No test, no
    screenshot and no amount of reading says so, and without this the gate's only evidence would
    be that it prints a number.
    """
    plant = SOURCE / "routes" / "GateFixtureHoverSnap.svelte"
    body = (
        '<button class="gate-fixture-snap">Press</button>\n\n'
        "<style>\n"
        "\t.gate-fixture-snap:hover {\n"
        "\t\tbackground: var(--sift-surface-3);\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_hover_answers.js")

    assert answer.returncode == 1, "the gate passed a hover state with nothing animating it"
    assert "GateFixtureHoverSnap" in answer.stderr


def test_a_hover_that_changes_the_ink_alone_is_caught() -> None:
    """The INK half, and the plant carries a transition so it cannot pass by tripping SNAP instead.

    A colour change on text alone is invisible to anybody not looking straight at it and fails
    altogether at the low-contrast end, which is why it is counted separately from a file that was
    never animated at all.
    """
    plant = SOURCE / "routes" / "GateFixtureHoverInk.svelte"
    body = (
        '<button class="gate-fixture-ink">Press</button>\n\n'
        "<style>\n"
        "\t.gate-fixture-ink {\n"
        "\t\ttransition: color var(--dur-instant) var(--ease);\n"
        "\t}\n"
        "\t.gate-fixture-ink:hover {\n"
        "\t\tcolor: var(--sift-ink);\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_hover_answers.js")

    assert answer.returncode == 1, "the gate passed a hover that changes the ink and nothing else"
    assert "GateFixtureHoverInk" in answer.stderr


def test_a_screen_dressing_a_text_field_of_its_own_is_caught() -> None:
    """The one-field ratchet, watched refusing.

    Three of the six properties `app.css` already gives every field is what that gate calls a copy
    rather than an adjustment, and three is what this restates. The fault it is for: sibling
    walls each drawing a search box of their own.
    """
    plant = SOURCE / "routes" / "GateFixtureOwnField.svelte"
    body = (
        '<input class="gate-fixture-field" />\n\n'
        "<style>\n"
        "\tinput.gate-fixture-field {\n"
        "\t\tblock-size: 36px;\n"
        "\t\tborder: 1px solid var(--sift-line);\n"
        "\t\tborder-radius: var(--radius-md);\n"
        "\t}\n"
        "</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_one_field.js")

    assert answer.returncode == 1, "the gate passed a screen dressing a field of its own"
    assert "GateFixtureOwnField" in answer.stderr


def test_a_control_inside_a_tooltip_detail_is_caught() -> None:
    """The tooltip gate's second rule, watched refusing.

    `Tooltip` has a `detail` snippet so a kept filter can show what it holds as chips before it is
    pressed. The prop's documentation says description only, and a tooltip which takes markup is
    a tooltip somebody eventually puts a control in; a comment saying so cannot go red.

    The reason it must: a tooltip is portalled, is not focusable, and vanishes the moment the
    pointer leaves the control it belongs to. A button inside one cannot be reached by keyboard at
    all, and would look entirely correct in the markup, in review and in a screenshot.
    """
    plant = SOURCE / "routes" / "GateFixtureTooltipDetail.svelte"
    body = (
        "<script lang='ts'>\n"
        "\timport Tooltip from '$lib/components/common/Tooltip.svelte';\n"
        "</script>\n\n"
        '<Tooltip label="What this keeps">\n'
        "\t{#snippet detail()}\n"
        '\t\t<button type="button">Clear it</button>\n'
        "\t{/snippet}\n"
        '\t<span aria-label="What this keeps">x</span>\n'
        "</Tooltip>\n"
    )

    with _planted(plant, body):
        answer = _run("check_tooltip_is_the_name.js")

    assert answer.returncode == 1, "the gate passed a control drawn inside a tooltip"
    assert "GateFixtureTooltipDetail" in answer.stderr


def test_a_tooltip_detail_that_only_describes_is_not_reported() -> None:
    """The other half, and the half a one-sided test cannot supply.

    A rule that refused every `detail` snippet would pass the test above and take the feature away:
    the one real detail in the tree draws chips, which is exactly what it is for. So a plant that
    describes and does not operate has to come back clean.
    """
    plant = SOURCE / "routes" / "GateFixtureTooltipPlainDetail.svelte"
    body = (
        "<script lang='ts'>\n"
        "\timport Tooltip from '$lib/components/common/Tooltip.svelte';\n"
        "</script>\n\n"
        '<Tooltip label="What this keeps">\n'
        "\t{#snippet detail()}\n"
        '\t\t<span class="gate-fixture-note">tags: beach</span>\n'
        "\t{/snippet}\n"
        '\t<span aria-label="What this keeps">x</span>\n'
        "</Tooltip>\n"
    )

    with _planted(plant, body):
        answer = _run("check_tooltip_is_the_name.js")

    assert answer.returncode == 0, (
        "the gate reported a detail snippet that only describes:\n" + answer.stderr
    )


def test_a_primitive_that_does_not_declare_itself_is_caught() -> None:
    """Every primitive says what it is, in its own module script, or the gate refuses it.

    Planted in the primitives folder, which is the population the declaration rule covers. It
    carries a `WHY NOT BITS-UI:` line so the OTHER gate over that folder is content, and the only
    thing wrong with it is the missing declaration.
    """
    plant = SOURCE / "lib" / "components" / "common" / "GateFixtureUndeclared.svelte"
    body = (
        "<script lang='ts'>\n"
        "\t/* WHY NOT BITS-UI: a fixture. */\n"
        "</script>\n\n"
        "<span>a primitive that says nothing about itself</span>\n"
    )

    with _planted(plant, body):
        answer = _run("check_design_entries.js")

    assert answer.returncode == 1, "the gate passed a primitive with no design declaration"
    assert "GateFixtureUndeclared" in answer.stderr
    assert "no design declaration" in answer.stderr


def test_two_primitives_claiming_one_role_are_caught() -> None:
    """The duplicate detector: a second component with the first one's purpose is the first one
    written twice, and this is the only gate that can say so before the copy has callers.

    The plant declares itself perfectly (name, category, basis, states) and claims Chip's role
    word for word, differently capitalised, so the comparison is proved to be on meaning rather
    than on bytes."""
    plant = SOURCE / "lib" / "components" / "common" / "GateFixtureSecondChip.svelte"
    body = (
        "<script lang='ts' module>\n"
        "\timport type { DesignEntry } from '$lib/design/entry';\n\n"
        "\texport const design = {\n"
        "\t\tname: 'GateFixtureSecondChip',\n"
        "\t\tcategory: 'primitive',\n"
        "\t\trole: 'A small labeled pill or square: a filter token, a tag, a count, a mark',\n"
        "\t\tbasis: 'own',\n"
        "\t\tstates: ['default']\n"
        "\t} satisfies DesignEntry;\n"
        "</script>\n\n"
        "<script lang='ts'>\n"
        "\t/* WHY NOT BITS-UI: a fixture. */\n"
        "</script>\n\n"
        "<span>a second chip</span>\n"
    )

    with _planted(plant, body):
        answer = _run("check_design_entries.js")

    assert answer.returncode == 1, "the gate passed a second primitive with the first one's role"
    assert "GateFixtureSecondChip" in answer.stderr
    assert "both claim the role" in answer.stderr


def test_a_basis_naming_a_library_part_the_file_does_not_import_is_caught() -> None:
    """The declaration has to describe the file as it is, not as it was: `bits-ui:Select` on a
    file that imports nothing from the library is refused."""
    plant = SOURCE / "lib" / "components" / "common" / "GateFixtureFalseBasis.svelte"
    body = (
        "<script lang='ts' module>\n"
        "\timport type { DesignEntry } from '$lib/design/entry';\n\n"
        "\texport const design = {\n"
        "\t\tname: 'GateFixtureFalseBasis',\n"
        "\t\tcategory: 'control',\n"
        "\t\trole: 'a fixture that claims a library it does not use',\n"
        "\t\tbasis: 'bits-ui:Select'\n"
        "\t} satisfies DesignEntry;\n"
        "</script>\n\n"
        "<span>not a select</span>\n"
    )

    with _planted(plant, body):
        answer = _run("check_design_entries.js")

    assert answer.returncode == 1, "the gate passed a basis the file's imports contradict"
    assert "does not import Select" in answer.stderr


def test_a_second_mechanism_beside_a_primitive_is_caught() -> None:
    """The fence's first ban: a `<dialog>` where Modal is every dialog."""
    plant = SOURCE / "routes" / "GateFixtureDialog.svelte"
    body = "<dialog open><p>a dialog that is not Modal</p></dialog>\n"

    with _planted(plant, body):
        answer = _run("check_design_fence.js")

    assert answer.returncode == 1, "the fence passed a raw <dialog>"
    assert "GateFixtureDialog" in answer.stderr
    assert "site mechanism a primitive already wraps" in answer.stderr


def test_the_library_imported_outside_the_primitives_is_caught() -> None:
    """The fence's second ban: bits-ui reached for by a screen rather than by a primitive."""
    plant = SOURCE / "routes" / "GateFixtureLibrary.svelte"
    body = "<script lang='ts'>\n\timport { Popover } from 'bits-ui';\n\tvoid Popover;\n</script>\n<span>a screen with its own popover</span>\n"

    with _planted(plant, body):
        answer = _run("check_design_fence.js")

    assert answer.returncode == 1, "the fence passed bits-ui imported by a screen"
    assert "GateFixtureLibrary" in answer.stderr
    assert "outside lib/components/common" in answer.stderr


def test_a_box_drawn_by_hand_is_counted() -> None:
    """A ratchet of the fence: one rule with a radius, a ground and an inset is a panel written
    again, and the count may only fall."""
    plant = SOURCE / "routes" / "GateFixtureBox.svelte"
    body = (
        "<div class='gate-fixture-box'>a box</div>\n"
        "<style>\n\t.gate-fixture-box {\n\t\tpadding: var(--space-3);\n"
        "\t\tborder-radius: var(--radius-lg);\n\t\tbackground: var(--sift-surface-2);\n\t}\n</style>\n"
    )

    import re

    def boxes(answer: subprocess.CompletedProcess[str]) -> int:
        """The box count the gate read, from whichever line it printed it on: the summary when it
        is at the recorded number, the rise or the fall line when it is not."""
        found = re.search(
            r"design-fence\.box: (\d+) of|down to (\d+) from|\bbox (\d+)",
            answer.stderr + answer.stdout,
        )
        assert found, f"the gate printed no box count:\n{answer.stdout}\n{answer.stderr}"
        return int(next(one for one in found.groups() if one))

    # Both counts under one hold of the tree: the gate reads every file, plants included, so a
    # count taken while another test's fixture is there is not the count this one rises from.
    with the_client_tree():
        before = boxes(_run("check_design_fence.js"))
        with _written(plant, body):
            answer = _run("check_design_fence.js")

    # The count rose by exactly the plant. The offender list is the ten heaviest files, which a
    # one-rule plant is not on, so it is the NUMBER that proves the plant was read, against the
    # count the same gate gave a moment earlier, so a tree mid-conversion cannot make this flaky.
    assert boxes(answer) == before + 1, "the fence did not count the box drawn by hand"


def test_a_route_that_never_reaches_the_frame_is_counted() -> None:
    """Every route wears PageFrame or DoorCard, or says why not.

    The plant is named `GateFixtureUnframed+page.svelte`: it ends the way a route file does, which
    is what the fence keys on, and it starts with the prefix every plant must carry so the other
    gates walking the tree skip it."""
    plant = SOURCE / "routes" / "GateFixtureUnframed+page.svelte"
    body = "<h1>a page with no frame</h1>\n"

    with _planted(plant, body):
        answer = _run("check_design_fence.js")

    assert answer.returncode == 1, "the fence passed an unframed route"
    assert "unframed-routes" in answer.stderr
    assert "GateFixtureUnframed" in answer.stderr


def test_a_dependency_with_no_reason_is_caught() -> None:
    """A runtime dependency package.json names and the allowlist does not."""

    package = FRONTEND / "package.json"
    # Bytes in, bytes out. `write_text` on Windows turns every "\n" into "\r\n", so a restore
    # through it would hand the tracked manifest back with CRLF endings and redden the hygiene
    # rule that keeps the tree LF.
    original = package.read_bytes()
    manifest = json.loads(original)
    manifest["dependencies"]["gate-fixture-second-library"] = "0.0.0"
    with the_client_tree():
        package.write_bytes((json.dumps(manifest, indent="\t") + "\n").encode("utf-8"))
        try:
            answer = _run("check_dependencies.js")
        finally:
            package.write_bytes(original)

    assert answer.returncode == 1, "the dependency gate passed a package with no reason"
    assert "gate-fixture-second-library" in answer.stderr


def test_a_hand_rolled_button_is_counted() -> None:
    """The oldest ratchet: a bare <button> outside the primitives."""
    plant = SOURCE / "routes" / "GateFixtureButton.svelte"
    body = "<button type='button'>a button drawn by hand</button>\n"

    with _planted(plant, body):
        answer = _run("check_handrolled.js")

    assert answer.returncode == 1, "the hand-rolled gate passed a bare <button>"
    assert "handrolled.button" in answer.stderr
    assert "GateFixtureButton" in answer.stderr


def test_a_scroller_under_a_cap_with_no_height_is_counted() -> None:
    plant = SOURCE / "routes" / "GateFixtureCapped.svelte"
    body = (
        "<script lang='ts'>\n\timport Scroller from '$lib/components/common/Scroller.svelte';\n</script>\n"
        "<div class='gate-fixture-capped'><Scroller><p>rows</p></Scroller></div>\n"
        "<style>\n\t.gate-fixture-capped {\n\t\tmax-block-size: 20rem;\n\t}\n</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_capped_scroller.js")

    assert answer.returncode == 1, "the capped-scroller gate passed a cap with no height"
    assert "GateFixtureCapped" in answer.stderr


def test_a_padding_shorthand_after_the_chrome_clearing_is_caught() -> None:
    plant = SOURCE / "routes" / "GateFixtureChrome.svelte"
    body = (
        "<div class='gate-fixture-chrome'>under the strip</div>\n"
        "<style>\n\t.gate-fixture-chrome {\n\t\tpadding-block-start: var(--window-chrome);\n"
        "\t\tpadding: var(--space-4);\n\t}\n</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_window_chrome_is_padding.js")

    assert answer.returncode == 1, (
        "the window-chrome gate passed a shorthand that overwrites the clearing"
    )
    assert "GateFixtureChrome" in answer.stderr


def test_a_stylesheet_comment_that_never_closes_is_caught() -> None:
    plant = SOURCE / "routes" / "GateFixtureComment.svelte"
    body = "<div class='gate-fixture-comment'>x</div>\n<style>\n\t.gate-fixture-comment {\n\t\tcolor: var(--sift-ink);\n\t}\n\t/* a comment that never closes\n</style>\n"

    with _planted(plant, body):
        answer = _run("check_css_comments.js")

    assert answer.returncode == 1, "the css-comments gate passed an unterminated comment"
    assert "GateFixtureComment" in answer.stderr


def test_a_token_that_does_not_exist_is_caught() -> None:
    plant = SOURCE / "routes" / "GateFixtureToken.svelte"
    body = "<div class='gate-fixture-token'>x</div>\n<style>\n\t.gate-fixture-token {\n\t\tcolor: var(--gate-fixture-does-not-exist);\n\t}\n</style>\n"

    with _planted(plant, body):
        answer = _run("check_tokens_exist.js")

    assert answer.returncode == 1, "the tokens gate passed a token nothing defines"
    assert "gate-fixture-does-not-exist" in answer.stderr


def test_a_hex_colour_in_a_component_is_caught() -> None:
    plant = SOURCE / "routes" / "GateFixtureHex.svelte"
    body = "<div class='gate-fixture-hex'>x</div>\n<style>\n\t.gate-fixture-hex {\n\t\tcolor: #123456;\n\t}\n</style>\n"

    with _planted(plant, body):
        answer = _run("check_no_hex_literals.js")

    assert answer.returncode == 1, "the hex gate passed a colour written in a component"
    assert "GateFixtureHex" in answer.stderr


def test_a_light_theme_branch_is_caught() -> None:
    plant = SOURCE / "routes" / "GateFixtureLight.svelte"
    body = (
        "<div class='gate-fixture-light'>x</div>\n<style>\n\t@media (prefers-color-scheme: light) {\n"
        "\t\t.gate-fixture-light {\n\t\t\tcolor: var(--sift-ink);\n\t\t}\n\t}\n</style>\n"
    )

    with _planted(plant, body):
        answer = _run("check_dark_only.js")

    assert answer.returncode == 1, "the dark-only gate passed a light-theme branch"
    assert "GateFixtureLight" in answer.stderr


def test_an_app_wide_key_nobody_declared_is_counted() -> None:
    plant = SOURCE / "routes" / "GateFixtureKey.svelte"
    body = (
        "<script lang='ts'>\n\tfunction pressed(event: KeyboardEvent) {\n"
        "\t\tif (event.key === 'q') event.preventDefault();\n\t}\n</script>\n"
        "<svelte:window onkeydown={pressed} />\n"
    )

    with _planted(plant, body):
        answer = _run("check_shortcuts.js")

    assert answer.returncode == 1, "the shortcuts gate passed an app-wide key nobody declared"
    assert "shortcuts.undeclared" in answer.stderr or "GateFixtureKey" in answer.stderr


def test_an_effect_that_starts_the_load_it_waits_on_is_caught() -> None:
    plant = SOURCE / "routes" / "GateFixtureLoop.svelte"
    body = (
        "<script lang='ts'>\n\tconst store = $state({ loading: false, load() { this.loading = true; } });\n"
        "\t$effect(() => {\n\t\tif (!store.loading) store.load();\n\t});\n</script>\n<p>x</p>\n"
    )

    with _planted(plant, body):
        answer = _run("check_effect_load_loop.js")

    assert answer.returncode == 1, "the effect-load-loop gate passed an effect that feeds itself"
    assert "GateFixtureLoop" in answer.stderr


def test_a_private_copy_of_the_step_is_caught() -> None:
    plant = SOURCE / "routes" / "GateFixtureStep.svelte"
    body = "<script lang='ts'>\n\tconst STEP_SECONDS = 7;\n\tvoid STEP_SECONDS;\n</script>\n<p>Back 7 seconds</p>\n"

    with _planted(plant, body):
        answer = _run("check_one_step.js")

    assert answer.returncode == 1, "the one-step gate passed a second step size"
    assert "GateFixtureStep" in answer.stderr


def test_a_bare_field_on_a_settings_pane_is_caught() -> None:
    """A settings pane is a column of ROWS, and a stacked form field dropped into it is not one.

    The plant goes under `settings-ui/` because that is the only place this gate looks: every
    other fixture here lives under `routes/`, where it is invisible to it.
    """
    plant = SOURCE / "lib" / "settings-ui" / "GateFixtureBareField.svelte"
    body = (
        "<script lang='ts'>\n"
        "\timport Field from '$lib/components/common/Field.svelte';\n"
        "</script>\n\n"
        '<Field label="Something">\n'
        "\t{#snippet control({ id })}\n"
        "\t\t<span {id}>a control</span>\n"
        "\t{/snippet}\n"
        "</Field>\n"
    )

    with _planted(plant, body):
        answer = _run("check_settings_row.js")

    assert answer.returncode == 1, "the settings-row gate passed a stacked field on a pane"
    assert "GateFixtureBareField" in answer.stderr


def test_a_field_inside_a_form_card_is_not_counted() -> None:
    """The distinction the gate was taught, and the one thing it must not get wrong.

    A short form on a pane genuinely is a stack of labelled boxes, and `FormCard` is how a file
    says so. Counting those anyway would force the baseline UP whenever a correct form was added,
    which is a ratchet that does not ratchet.

    Asserted on the fixture's own name rather than on the exit code, for the reason the
    both-forms test gives: another plant can be live in the tree at this moment, and this is a
    statement about this fixture only.
    """
    plant = SOURCE / "lib" / "settings-ui" / "GateFixtureFormField.svelte"
    body = (
        "<script lang='ts'>\n"
        "\timport Field from '$lib/components/common/Field.svelte';\n"
        "\timport FormCard from '$lib/components/common/FormCard.svelte';\n"
        "</script>\n\n"
        '<FormCard title="Add a thing">\n'
        '\t<Field label="Something">\n'
        "\t\t{#snippet control({ id })}\n"
        "\t\t\t<span {id}>a control</span>\n"
        "\t\t{/snippet}\n"
        "\t</Field>\n"
        "</FormCard>\n"
    )

    with _planted(plant, body):
        answer = _run("check_settings_row.js")

    assert "GateFixtureFormField" not in answer.stderr, (
        "a field inside a FormCard was counted; a form on a pane is not a row"
    )


def test_an_exemption_for_one_rule_does_not_excuse_another() -> None:
    """A `WHY NOT SHARED:` line naming `button` excuses the raw button beside it and nothing else: a
    raw input in the same file is still counted, which a whole-file exemption would not do."""
    plant = SOURCE / "routes" / "GateFixtureScoped.svelte"
    body = (
        "<script lang='ts'>\n\t/* WHY NOT SHARED: button: a fixture whose surface is the control. */\n</script>\n"
        "<button type='button'>the excused button</button>\n"
        "<input value='a box the exemption does not cover' />\n"
    )

    with _planted(plant, body):
        answer = _run("check_handrolled.js")

    assert answer.returncode == 1, "a button exemption excused a raw input"
    assert "handrolled.input" in answer.stderr
    assert "handrolled.button" not in answer.stderr, answer.stderr


def test_an_exemption_that_names_no_rule_is_refused() -> None:
    plant = SOURCE / "routes" / "GateFixtureUnnamed.svelte"
    body = "<script lang='ts'>\n\t/* WHY NOT SHARED: the old whole-file spelling. */\n</script>\n<p>x</p>\n"

    with _planted(plant, body):
        answer = _run("check_handrolled.js")

    assert answer.returncode == 1, "the gate accepted an exemption that names no rule"
    assert "GateFixtureUnnamed" in answer.stderr
    assert "names no rule" in answer.stderr
