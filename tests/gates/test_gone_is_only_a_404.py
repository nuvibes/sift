# SPDX-License-Identifier: AGPL-3.0-or-later
"""A screen may say a thing has gone only when the server said it had.

A 404 is the one status that is a fact about the library ("no such id" and "not yours" alike);
anything else is a fault, and `isMissing` is the one place that decides. A screen that printed
"not there any more" for every failure would say it of a malformed request or a restarting server.
So a file that tells somebody something is not there must call it. Read from the copy, for screens
not yet written.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

CLIENT = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: The one place that decides. The CALL, not the name: an unused import or a comment satisfies a
#: name.
DECIDER = "isMissing("

#: Sentences telling somebody a thing they asked for is not there, matched by wording and in the
#: contracted voice the copy uses ("isn't").
_NOT = r"(?:is|are) not|(?:is|are)n['\u2019]t"
_GONE = re.compile(
    rf"(?:{_NOT}) here(?:\.|\")|(?:{_NOT}) there any ?more|(?:is|are) no longer here"
    r"|there['\u2019]s no [\w ]+ here\.",
    re.IGNORECASE,
)


def _copy_finder() -> object:
    """The client's own copy finder (`check_display_dashes.py`), borrowed whole, so there is one
    answer to where copy is written."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_display_dashes.py"
    spec = importlib.util.spec_from_file_location("_display_dashes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_COPY = _copy_finder()


def _client_files() -> list[Path]:
    found = [*CLIENT.rglob("*.svelte"), *CLIENT.rglob("*.ts")]
    return sorted(
        path
        for path in found
        # Generated declarations and test doubles are not screens.
        if not path.name.endswith(".d.ts") and ".test." not in path.name
    )


def says_gone(source: str) -> bool:
    """Whether this source tells somebody something is not there, in its strings or between tags,
    never in a comment."""
    without_comments = _COPY._without_comments(source)
    quoted = " ".join(match.group(2) for match in _COPY.STRING.finditer(without_comments))
    markup = _COPY._markup_only(without_comments)
    return bool(_GONE.search(quoted) or _GONE.search(markup))


#: Modules that decide on a screen's behalf, checked in their own right: the five entity pages
#: share `EntitySubject`, so the call need not sit in the same file as the sentence.
DELEGATES = ("$lib/entity/subject.svelte",)


def _delegates(source: str) -> bool:
    """Whether this screen gets its answer from something that asks the question."""
    return any(f"from '{module}'" in source for module in DELEGATES)


def test_a_screen_that_says_a_thing_has_gone_asks_whether_it_has() -> None:
    silent: list[str] = []
    for path in _client_files():
        source = path.read_text(encoding="utf-8")
        if not says_gone(source):
            continue
        if DECIDER not in source and not _delegates(source):
            silent.append(path.relative_to(CLIENT).as_posix())

    assert not silent, (
        "\nThese tell somebody something in their library is not there, without asking whether it\n"
        "is. A refused request, a restarting server and a dropped connection all reach the same\n"
        "catch, and all of them print the same sentence. Call isMissing with the error, and say\n"
        "something else when the answer is no.\n\n  " + "\n  ".join(silent) + "\n"
    )


@pytest.mark.parametrize("module", DELEGATES)
def test_a_delegate_actually_asks_the_question(module: str) -> None:
    """A delegate itself asks `isMissing`, or importing it would excuse every screen."""
    path = CLIENT / (module.replace("$lib/", "lib/") + ".ts")
    assert path.exists(), f"{module} is listed as a delegate and is not there"
    assert DECIDER in path.read_text(encoding="utf-8"), (
        f"{module} is what several screens lean on to tell 'it is gone' from 'it could not be "
        f"read', and it no longer asks. Every one of those screens is now saying the wrong thing."
    )


def test_the_check_notices_a_screen_that_does_not_ask() -> None:
    """A screen whose empty state is reached by every failure is caught."""
    planted = """
	try {
		thing = await api.get('/things/1');
	} catch {
		thing = null;
	}
</script>
{#if !thing}<p>That thing is not here.</p>{/if}
"""

    assert says_gone(planted)
    assert DECIDER not in planted


def test_the_check_is_satisfied_by_asking() -> None:
    """A screen that asks passes."""
    written = """
	import { isMissing } from '$lib/api/client';
	try {
		thing = await api.get('/things/1');
	} catch (error) {
		thing = null;
		unreadable = !isMissing(error);
	}
</script>
{#if unreadable}<p>That could not be loaded.</p>{:else if !thing}<p>That thing is not here.</p>{/if}
"""

    assert says_gone(written)
    assert DECIDER in written


def test_a_comment_about_the_rule_is_not_a_screen_breaking_it() -> None:
    """A comment quoting the sentence is not a screen saying it."""
    assert not says_gone("/* never print 'that group is not there any more' on a 500 */")
    assert not says_gone("\t// the ids in it name rows that are not there any more.")
    assert not says_gone("<!-- the tile is gone: that user is not here. -->")


def test_the_sentences_the_screens_say_are_found() -> None:
    """The screens' real sentences are found, and near misses are not."""
    for said in (
        "<Empty>That person isn't here.</Empty>",
        "<Empty>That Photo Set isn\u2019t here.</Empty>",
        "<Empty quiet>\n\tThat group isn't there any more. It may have been named.\n</Empty>",
        '<Empty icon="content_copy" title="That chain is no longer here">',
        "<Empty>There's no recap here.</Empty>",
        '<Empty scope="page" icon="person" title="That person isn\'t here"',
    ):
        assert says_gone(said), said

    assert not says_gone("<p>there's no folder here to look in yet.</p>")


def test_there_are_screens_to_check() -> None:
    """A walk that found nothing would pass for ever."""
    saying = sum(1 for path in _client_files() if says_gone(path.read_text(encoding="utf-8")))

    assert saying >= 4, f"only {saying} screens say a thing has gone; the walk is broken"


def test_a_mention_of_the_decider_is_not_a_check() -> None:
    """Deleting the check and keeping a mention of `isMissing` fails."""
    planted = """
	/* Only a 404 says the thing has gone (see isMissing). */
	try {
		thing = await api.get('/things/1');
	} catch {
		thing = null;
	}
</script>
{#if !thing}<p>That thing is not here.</p>{/if}
"""

    assert says_gone(planted)
    assert DECIDER not in planted
