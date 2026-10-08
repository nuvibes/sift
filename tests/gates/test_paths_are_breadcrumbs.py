# SPDX-License-Identifier: AGPL-3.0-or-later
"""A place in Sift is named by its breadcrumb, in the documents as on the screen.

The screen's name, then each step, joined by `" > "`: `Settings > Tasks and Activity > Import tasks`. It is what
the copy button beside a settings name puts on the clipboard and what the settings search takes
pasted, so a reader can follow a path from the documents straight to the place. "Under Importing's
Folders row", "in Settings, under Tasks" and `Settings -> Folders` each name the same place in a
way nothing can follow.

On screen the rule is the `paths` list in `data/vocabulary.json`, held at zero by both copy readers
(`vocabulary.py` over the server's copy, `frontend/scripts/lib/vocabulary.js` over the client's).
This gate reads the same list over the public documents and the changelog, and adds the one rule a
document has that a screen does not: the breadcrumb stands in code ticks, so it reads as a name and
not as a sentence with arrows in it. It also reads the client's markup JOINED ACROSS INLINE TAGS,
the one shape the copy readers cannot see: a sentence whose place is split by a link tag.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.gates import vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]

#: The documents a reader of the public repository reads.
DOCUMENTS = ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "ARCHITECTURE.md")

#: The first step of a path: a screen somebody opens from the rail or the menu.
SCREENS = (
    "Settings",
    "Organize",
    "Browse",
    "People",
    "Sites",
    "Collections",
    "Photo Sets",
    "Tags",
    "Loops",
    "Theater",
    "Downloads",
    "Activity",
    "Insights",
    "Favorites",
    "Hidden",
)

#: A breadcrumb: a screen, then at least one more step after `" > "`.
_BREADCRUMB = re.compile(r"\b(?:" + "|".join(SCREENS) + r") > \S")

#: A span of code ticks, where a breadcrumb belongs in a document.
_TICKED = re.compile(r"`[^`]*`")


def documents() -> list[Path]:
    """The public documents: the ones at the top of the repository, and everything under docs."""
    found = [REPO / name for name in DOCUMENTS if (REPO / name).is_file()]
    return found + sorted((REPO / "docs").rglob("*.md"))


def offences_in(text: str) -> list[tuple[int, str, str]]:
    """`(line, what was found, what to write)` for every place a document names any other way."""
    found: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match, instead in vocabulary.offences("paths", line):
            found.append((number, match, instead))
        for crumb in _BREADCRUMB.finditer(_TICKED.sub("", line)):
            found.append((number, crumb.group(0), "the breadcrumb in code ticks"))
    return found


#: What the client's markup is read without: its code, its styles and its comments.
_NOT_TEXT = re.compile(r"<(script|style)\b[^>]*>[\s\S]*?</\1>|<!--[\s\S]*?-->")
#: A tag that ENDS a run of text: a paragraph, a list item, a heading, a line break, a block.
_BLOCK_TAG = re.compile(
    r"</?(?:p|div|li|ul|ol|h[1-6]|br|section|header|footer|table|tr|td|th|dd|dt|dl)\b[^>]*>",
    re.IGNORECASE,
)
#: Any other tag, or a Svelte block marker (`{#if}`, `{:else}`, `{/if}`): a link, an emphasis, a
#: component such as `SettingLink`, drawn INSIDE the sentence around it.
_INLINE = re.compile(r"<[^>]*>|\{[#:/@][^}]*\}")


def sentences_in_markup(source: str) -> list[str]:
    """The runs of text a component's markup draws, JOINED ACROSS INLINE TAGS.

    The client's copy reader reads the text between two tags as one string, so "in Settings under
    <SettingLink>Faces</SettingLink>" reached it as "in Settings under" and "Faces", neither of
    which names a place. Joined, it is the sentence a person reads, and the `paths` rule sees it.
    """
    # A line break in the source is only layout: a run ends at a block tag and nowhere else.
    text = _BLOCK_TAG.sub("\0", _NOT_TEXT.sub("", source))
    runs = (" ".join(_INLINE.sub("", run).split()) for run in text.split("\0"))
    return [run for run in runs if run]


def markup_offences(source: str) -> list[tuple[str, str]]:
    """`(sentence, what to write)` for every run of a component's text naming a place another way."""
    joined = "\n".join(sentences_in_markup(source))
    return [(found, instead) for found, instead in vocabulary.offences("paths", joined)]


def test_the_client_names_every_place_by_its_breadcrumb_across_its_tags() -> None:
    """The half of the screen's copy a link tag split: every component's markup, read joined."""
    client = REPO / "frontend" / "src"
    read = [path for path in client.rglob("*.svelte") if "routes/design/" not in path.as_posix()]
    assert len(read) > 300, f"read only {len(read)} components, which cannot be right"
    wrong = [
        f"    {path.relative_to(REPO).as_posix()}: {found!r} -> {instead}"
        for path in sorted(client.rglob("*.svelte"))
        if "routes/design/" not in path.as_posix()
        for found, instead in markup_offences(path.read_text(encoding="utf-8"))
    ]
    assert not wrong, (
        f"\n{len(wrong)} places on screen named another way than their breadcrumb, read across"
        " the tags inside the sentence, for example Settings > Faces:\n\n" + "\n".join(wrong) + "\n"
    )


def test_a_place_split_by_a_link_tag_is_found() -> None:
    """The known positive: the sentence that shipped, a link tag splitting the place's name."""
    shipped = (
        "<p>turn on Recognize faces in your library, in Settings under\n"
        '<SettingLink section="faces" setting={FACES_KEY}>Faces</SettingLink>.</p>'
    )
    assert markup_offences(shipped)
    assert not markup_offences(
        '<p>turn it on in <SettingLink section="faces">Settings > Faces</SettingLink>.</p>'
    )


def test_the_documents_name_every_place_by_its_breadcrumb() -> None:
    wrong = [
        f"    {path.relative_to(REPO).as_posix()}:{line}  {found!r} -> {instead}"
        for path in documents()
        for line, found, instead in offences_in(path.read_text(encoding="utf-8"))
    ]
    assert not wrong, (
        f"\n{len(wrong)} places named another way than their breadcrumb in code ticks, for example"
        " `Settings > Tasks and Activity > Import tasks`:\n\n" + "\n".join(wrong) + "\n"
    )


def test_the_documents_are_read() -> None:
    """A gate over no files passes on anything."""
    names = {path.name for path in documents()}
    assert {"README.md", "CHANGELOG.md"} <= names


@pytest.mark.parametrize(
    "line",
    [
        "Open **Settings -> Folders** and add a folder.",
        "Open Settings > Folders and add a folder.",
        "- **Settings > Privacy** can hide your profile folder.",
        "It is in Settings, under Tasks.",
        "Turn it on under Playback's Repeat row.",
        "Add the folder under Folders in Settings first.",
    ],
)
def test_a_place_named_another_way_is_found(line: str) -> None:
    """The known positives: every shape the gate exists to refuse."""
    assert offences_in(line), line


@pytest.mark.parametrize(
    "line",
    [
        "Open `Settings > Folders` and add a folder.",
        "- `Settings > Privacy` can hide your profile folder.",
        "Faces to confirm are in `Organize > Faces > Faces to confirm`.",
        "Sift reads the folders you add, and nothing else.",
        "A score > 0.5 is kept.",
    ],
)
def test_a_breadcrumb_in_ticks_and_ordinary_prose_pass(line: str) -> None:
    assert not offences_in(line), line
