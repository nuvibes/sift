# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every screen that lists scoped things re-reads when what may be seen changes: a share produces no
import job, so a list not told keeps showing what was taken away. The signal is `libraryChanges`,
through `reloadOnLibraryChange`."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROUTES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "routes"

#: The screens that list things whose visibility depends on a grant, written out: no pattern can
#: tell a scoped list (People) from an unscoped one (Downloads).
SCOPED_WALLS = (
    "collections",
    "people",
    "sites",
    "tags",
    # The review queues: a share moves every pile's size, and the folders read for people.
    "organize",
    "organize/[queue]",
)

#: The grid watches the signal directly, since it re-reads a page rather than a whole list.
WATCHERS = (Path("lib/components/AssetGrid.svelte"),)

#: Scoped lists that are not routes: the library tree in Settings badges every folder's sharing.
SCOPED_COMPONENTS = (
    Path("lib/library/LibraryScreen.svelte"),
    # The piles: the two routes are thin wrappers around this component.
    Path("lib/components/faces/FaceGroups.svelte"),
    # The folders read for people, the same shape.
    Path("lib/components/suggestions/FolderSuggestions.svelte"),
)


def subscribes(source: str) -> bool:
    """Whether this source re-reads when a share moves: the CALL, not an import, or a component
    declared below and checked in its own right."""
    if "reloadOnLibraryChange(" in source:
        return True
    return any(
        f"<{screen.stem} " in source or f"<{screen.stem}\n" in source
        for screen in SCOPED_COMPONENTS
    )


@pytest.mark.parametrize("wall", SCOPED_WALLS)
def test_a_scoped_wall_re_reads_when_sharing_changes(wall: str) -> None:
    page = ROUTES / wall / "+page.svelte"
    assert page.exists(), f"{wall} has no page; update SCOPED_WALLS"
    assert subscribes(page.read_text(encoding="utf-8")), (
        f"/{wall} lists things whose visibility depends on a grant, and does not re-read when one "
        f"changes. Anything unshared stays on this screen until the page is reloaded. "
        f"Call reloadOnLibraryChange with this screen's own load, or draw a declared component "
        f"that does."
    )


@pytest.mark.parametrize("screen", SCOPED_COMPONENTS, ids=lambda path: path.name)
def test_a_scoped_screen_that_is_not_a_route_re_reads_too(screen: Path) -> None:
    source = (ROUTES.parent / screen).read_text(encoding="utf-8")
    assert "reloadOnLibraryChange(" in source, (
        f"{screen} lists things whose visibility depends on a grant, and does not re-read when one "
        f"changes. Call reloadOnLibraryChange with this screen's own load."
    )


#: The two spellings of one subscription: the rune read inside an effect, and the `whenChanged`
#: helper, which the grid uses so a tag created anywhere does not throw the reader to the top.
WATCHES_THE_SIGNAL = ("libraryChanges.generation", "whenChanged(libraryChanges")


@pytest.mark.parametrize("watcher", WATCHERS)
def test_the_asset_surfaces_watch_the_same_signal(watcher: Path) -> None:
    source = (ROUTES.parent / watcher).read_text(encoding="utf-8")
    assert any(spelling in source for spelling in WATCHES_THE_SIGNAL), (
        f"{watcher} draws assets and does not watch the sharing signal. A file that has just been "
        f"restricted stays on it until the page is reloaded. Read `libraryChanges.generation` in an "
        f"effect, or call `whenChanged(libraryChanges, ...)`."
    )


def test_the_signal_is_bumped_when_a_grant_is_written() -> None:
    """The sharing panel, the only place a grant is made or taken back, bumps the signal."""
    panel = ROUTES.parent / "lib" / "components" / "ShareDialog.svelte"
    source = panel.read_text(encoding="utf-8")
    assert "libraryChanges.changed()" in source, (
        "the sharing panel writes grants and does not announce it, so every screen holding a "
        "scoped list keeps showing what it had until the page is reloaded"
    )
