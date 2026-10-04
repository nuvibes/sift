# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every name a filter has is a name the parser takes, and every copy of them agrees.

The query language's vocabulary is written in four places that may not import each other: `Field`
and `FILTERS` in the search slice, `FACETS` and `FACETS_RENAMED` in the kernel, and the browser's
`FIELDS` and facet panel. A facet row is clicked to write the filter that finds its own files, so
a dimension that lost its token would count one set and select another. This holds every pair of
copies to agreement; it reads across the kernel, the slice and the browser, so it is a gate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sift.kernel.access import FACETS, FACETS_RENAMED
from sift.kernel.access.constraints import ENTITY_FACETS
from sift.kernel.ingress import Reason
from sift.kernel.jobs.failure_words import VERDICT_WORDS
from sift.slices.download.router import DOWNLOAD_FACETS
from sift.slices.search.filters import ALIASES, FILTERS, LEFT_OUT_WAYS, Field

pytestmark = pytest.mark.unit

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _spelling(label: str) -> str:
    """The token a label is written as. The same rule the language enforces at import."""
    return label.strip().casefold().replace(" ", "_")


def test_every_label_is_a_spelling_the_parser_takes() -> None:
    """A filter's label is a spelling the parser takes, as its token is: a refused label offered
    `duration:` for "Length", and `length:2m+` then searched for those characters."""
    for entry in FILTERS:
        spelling = _spelling(entry.label)
        assert spelling == entry.field.value or ALIASES.get(spelling) is entry.field, (
            f"{entry.label!r} labels {entry.field.value!r} and is not a spelling of it"
        )
        # The label is the READABLE form: a capital and never an underscore.
        assert "_" not in entry.label, entry.label
        assert entry.label[0].isupper(), entry.label


def test_a_label_is_drawn_on_the_row_it_put_there() -> None:
    """The dropdown draws the label it matched on, or a filter appears for an invisible reason."""
    source = (FRONTEND / "lib" / "components" / "shell" / "SearchSuggestions.svelte").read_text()
    assert "marked(row.filter.label)" in source, "the filter row stopped drawing its label"
    assert "marked(row.filter.example)" in source, "the filter row stopped drawing its example"


def test_every_field_is_offered_exactly_once() -> None:
    """A token with no row is a filter the parser takes and nothing on screen mentions."""
    offered = [entry.field for entry in FILTERS]
    assert sorted(offered, key=lambda f: f.value) == sorted(Field, key=lambda f: f.value)
    assert len(offered) == len(set(offered))


def test_a_facets_other_spelling_agrees_with_the_language() -> None:
    """The kernel's spelling table resolves every name where the language does: a facet panel and a
    search box must not disagree about a word."""
    for other, canonical in FACETS_RENAMED.items():
        assert other in ALIASES, (
            f"the kernel takes {other!r} and the language has never heard of it"
        )
        assert ALIASES[other].value == canonical, other
        assert canonical in FACETS, canonical


def test_every_facet_dimension_is_a_filter() -> None:
    """Every facet dimension is a filter: clicking a row writes it, and an unaccepted one falls
    through to free text."""
    tokens = {field.value for field in Field}
    for dimension in FACETS:
        assert dimension in tokens, dimension


def _without_comments(source: str) -> str:
    """TypeScript with its comments removed: an apostrophe in a comment opens a quoted string."""
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", without_blocks)


def _ts_string_list(source: str, name: str) -> list[str]:
    """The string literals of an exported array, read out of TypeScript without running it."""
    source = _without_comments(source)
    start = source.index(f"export const {name} = [")
    depth = 0
    for at in range(start, len(source)):
        if source[at] == "[":
            depth += 1
        elif source[at] == "]":
            depth -= 1
            if depth == 0:
                return re.findall(r"'([^']+)'", source[start : at + 1])
    raise AssertionError(f"{name} is not closed")


def test_the_browser_knows_every_field_and_every_old_spelling() -> None:
    """The browser's `FIELDS` knows every field and old spelling, or a saved search widens."""
    listed = _ts_string_list(
        (FRONTEND / "lib" / "search" / "search.svelte.ts").read_text(), "FIELDS"
    )

    wanted = {field.value for field in Field} | set(ALIASES)
    assert set(listed) == wanted, sorted(wanted.symmetric_difference(listed))
    assert len(listed) == len(set(listed)), "a field is listed twice"

    # Current spellings first, so a stored query comes back in today's name.
    current = [name for name in listed if name not in ALIASES]
    assert listed[: len(current)] == current, "an old spelling is listed before a current one"


#: One entry in the panel's chooser: the key, the label, and the tail, where an entry says it is a
#: SPAN; an entry with a third field must not fall out of the list.
_PANEL_ENTRY = re.compile(r"\{ key: '([a-z_]+)', label: '([^']+)'([^}]*)\}")


#: A column that reads another filter's word for the same files. `songs` narrows to what `music:`
#: does, since the server keeps a file's Music field equal to its song's name; the typed word stays
#: `songs:`, since `music:` is the field's own filter.
READS_AS: dict[str, Field] = {"songs": Field.MUSIC}


def test_the_facet_panel_offers_dimensions_that_exist() -> None:
    """The panel's keys are dimensions that exist, and its labels are the language's labels.

    A key must be a dimension SOMETHING groups by: the Files wall's `FACETS`, an entity's
    `ENTITY_FACETS` (`hair_color` on People) or the Downloads wall's. A SPAN (`added:`) takes a
    range, so it has no dimension but must still be a name the language takes. A key the language
    knows, by token or any spelling, is held to the language's label; one it does not know
    (`linked`, `cover`, `mine`) has nothing to agree with.
    """
    # `facet-labels.ts`, which the filter chip reads too. `assert listed` catches the list moving.
    source = (FRONTEND / "lib" / "components" / "shell" / "facet-labels.ts").read_text()
    listed = _PANEL_ENTRY.findall(source)
    assert listed, "the facet list could not be read"

    assert len(listed) == source.count("{ key: '"), "an entry in the panel's list was not read"

    labels = {entry.field.value: entry.label for entry in FILTERS}
    tokens = {field.value for field in Field}
    # The Downloads wall's panel groups by its own route's dimensions (`/downloads/facets`).
    entity = {key for subject in ENTITY_FACETS.values() for key in subject} | set(DOWNLOAD_FACETS)
    for key, label, rest in listed:
        if "span: true" in rest:
            assert key in tokens, f"the panel's span {key!r} is not a filter the parser takes"
            assert key not in FACETS, (
                f"{key!r} IS a dimension the server groups by, so it is a column and not a span"
            )
        else:
            assert key in FACETS or key in entity, (
                f"the panel offers {key!r}, which nothing on the server groups by"
            )
        named = key if key in tokens else ALIASES.get(key, None) and ALIASES[key].value
        if key in READS_AS:
            named = READS_AS[key].value
        if named is None:
            assert key in entity, f"{key!r} is neither a filter nor a facet of anything"
            continue
        assert label == labels[named], (
            f"the panel calls {key!r} {label!r}, the language {labels[named]!r}"
        )


#: The dimensions whose values are BANDS the server cuts in SQL, and the browser function that
#: words each: two copies of one set of cuts. An exact duration or size would be a column of ones.
BANDED = {
    "duration": "DURATION_BANDS",
    "size": "SIZE_BANDS",
}


def _server_bands(dimension: str) -> list[str]:
    """The band names one facet's CASE can emit, read out of the expression itself."""
    _, expression = FACETS[dimension]
    return re.findall(r"THEN '([^']+)'", expression) + re.findall(r"ELSE '([^']+)'", expression)


def _browser_bands(source: str, table: str) -> list[str]:
    """The band names one browser table has a word for, in WRITTEN order, which is the order the
    panel draws them: a band inserted out of place fails here."""
    without = _without_comments(source)
    start = without.index(f"const {table}: Record<string, string> = {{")
    end = without.index("};", start)
    return re.findall(r"'([^']*)'\s*:", without[start:end])


def test_every_band_the_server_cuts_has_a_word_on_it() -> None:
    """Every band the server cuts has a word, and every word names a band it cuts: a band with no
    word draws `60s..<3m` among English."""
    source = (FRONTEND / "lib" / "components" / "shell" / "facet-labels.ts").read_text()
    for dimension, table in BANDED.items():
        cut = _server_bands(dimension)
        said = _browser_bands(source, table)
        assert cut, f"the {dimension} column's bands could not be read"
        assert said, f"{table} could not be read"
        assert cut == said, (
            f"the {dimension} column cuts {cut} and the browser has words for {said}"
        )


def test_json_shape_of_the_offer_is_unchanged() -> None:
    """The dropdown's rows carry the token and the label, the one place both names show."""
    folder = next(entry for entry in FILTERS if entry.field is Field.IN)
    assert json.loads(json.dumps({"field": folder.field.value, "label": folder.label})) == {
        "field": "in",
        "label": "Folder",
    }


def _ts_const_list(source: str, name: str) -> list[str]:
    """The string literals of an exported `as const` array, read without running it."""
    without = _without_comments(source)
    start = without.index(f"export const {name} = [")
    end = without.index("]", start)
    return re.findall(r"'([^']+)'", without[start:end])


def test_every_product_the_importing_pane_says_left_out_about_is_a_left_out_value() -> None:
    """Every product key the Importing pane links as `left_out=<key>` is a value the language takes,
    or the link opens a wall filtered to nothing. Read from `importing.ts`, where its rows live."""
    source = (FRONTEND / "lib" / "library" / "importing.ts").read_text()
    drawn = _ts_const_list(source, "GENERATE_PRODUCTS") + _ts_const_list(
        source, "IDENTIFY_PRODUCTS"
    )
    assert len(drawn) >= 7, f"the pane's products could not be read: {drawn}"
    for key in drawn:
        assert key in LEFT_OUT_WAYS, f"left_out: does not take {key!r}"
        assert LEFT_OUT_WAYS[key].value == key, f"left_out:{key} names {LEFT_OUT_WAYS[key]!r}"


def test_a_left_out_file_and_a_refused_file_say_one_reason_in_the_same_words() -> None:
    """A file left out and a file refused say one reason in the same words, capitalised with a full
    stop on the tile: one decision by one gate."""
    source = (FRONTEND / "lib" / "settings-ui" / "refused.svelte.ts").read_text()
    without = _without_comments(source)
    start = without.index("const WHY: Record<string, string> = {")
    end = without.index("};", start)
    said = {
        key: single or double
        for key, single, double in re.findall(
            r"(\w+): (?:'([^']*)'|\"([^\"]*)\")", without[start:end]
        )
    }
    shared = {reason.value for reason in Reason} & set(said)
    assert len(shared) >= 8, f"the refusal list could not be read: {sorted(said)}"
    for code in shared:
        words = said[code]
        assert VERDICT_WORDS[code] == f"{words[0].upper()}{words[1:]}.", code
