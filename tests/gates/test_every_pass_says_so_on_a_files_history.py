# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every pass that can run over one file names what says, on that file's History, that it ran.

A pass whose table nothing on the History reads runs in silence. So each pass declares, in
`history_sources.SAID_ON_A_FILE`, the reads or ledger acts that say it ran, its empty answer
included, or stands in `NOT_SAID_ON_A_FILE` with why. The passes are read off the STARTED
application: Run task's products and readings (`importing.ProductRegistry`), job types that run
as a file arrives, count files or carry a press's products, and Enrich and the AcoustID lookup.
Stale declarations fail too. And every line a pass says has a form for a press
(`sentences.had_sift`, from `kernel.presses`), the scan's line naming its three kinds of face.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping

import pytest

from sift.kernel import wiring
from sift.kernel.access import history  # noqa: F401  (registers every History read)
from sift.kernel.access import sentences as say
from sift.kernel.access.history_faces import _faces_found
from sift.kernel.access.history_sources import NOT_SAID_ON_A_FILE, SAID_ON_A_FILE
from sift.kernel.db import registered_point_reads
from sift.kernel.jobs.worker_pool import (
    by_itself_job_types,
    counted_as,
    registered_job_names,
    registered_product_carriers,
)
from sift.kernel.ledger import VERBS
from sift.slices import importing
from sift.slices.music import MUSIC_LOOKUP
from sift.slices.stash_boxes import STASH_SCAN
from sift.testing.authz import booted

pytestmark = [pytest.mark.gate, pytest.mark.unit]

#: How a ledger act is named in a declaration.
_LEDGER = "ledger:"
#: How a History read is named: its registered name starts with this.
_READ = "history."


def unsaid(
    passes: Iterable[str], said: Mapping[str, object], excused: Mapping[str, str]
) -> list[str]:
    """The passes neither declaration names."""
    return sorted({one for one in passes if one not in said and one not in excused})


def stale(
    passes: Collection[str], said: Mapping[str, object], excused: Mapping[str, str]
) -> list[str]:
    """Declarations for a pass the application does not offer or claim."""
    return sorted(name for name in (*said, *excused) if name not in passes)


def unread(
    said: Mapping[str, Iterable[str]], reads: Collection[str], verbs: Collection[str]
) -> list[str]:
    """Every source a declaration names that is no History read and no ledger verb, with its pass."""
    wrong: list[str] = []
    for name, sources in said.items():
        named = tuple(sources)
        if not named:
            wrong.append(f"{name}: names nothing")
        for source in named:
            if source.startswith(_LEDGER):
                if source.removeprefix(_LEDGER) not in verbs:
                    wrong.append(f"{name}: {source}")
            elif not (source.startswith(_READ) and source in reads):
                wrong.append(f"{name}: {source}")
    return sorted(wrong)


def unreasoned(excused: Mapping[str, str]) -> list[str]:
    """The excuses that give no reason."""
    return sorted(name for name, why in excused.items() if not why.strip())


@pytest.fixture(scope="module")
def passes(tmp_path_factory: pytest.TempPathFactory) -> frozenset[str]:
    """Every pass over one file the started application offers or claims.

    Booted as the authorization cases boot theirs: the registries a start fills are handed back
    when it stops, so the next module's boot claims its handlers afresh.
    """
    # Started, not merely built: the products and the handlers are registered in the lifespan.
    with booted(tmp_path_factory.mktemp("passes")) as client:
        products = wiring.part_of_app(client.app, importing.PRODUCTS)  # type: ignore[arg-type]
        offered = {*products.keys(), *(one.key for one in products.readings())}
        names = registered_job_names()
        claimed = {
            *by_itself_job_types(),
            *registered_product_carriers(),
            *(job_type for job_type, name in names.items() if counted_as(job_type) != name),
        }
    return frozenset({*offered, *claimed, STASH_SCAN, MUSIC_LOOKUP})


@pytest.mark.regression
def test_every_pass_over_a_file_names_what_says_it_ran(passes: frozenset[str]) -> None:
    assert len(passes) > 10, f"the started application offered too few passes to be read: {passes}"
    missing = unsaid(passes, SAID_ON_A_FILE, NOT_SAID_ON_A_FILE)
    assert not missing, (
        "these passes over a file name nothing that says on its History that they ran; name the"
        " reads in `history_sources.SAID_ON_A_FILE`, or the reason in `NOT_SAID_ON_A_FILE`: "
        + ", ".join(missing)
    )
    gone = stale(passes, SAID_ON_A_FILE, NOT_SAID_ON_A_FILE)
    assert not gone, "declared for a pass the application does not offer: " + ", ".join(gone)


def test_every_declared_source_is_a_history_read_or_a_ledger_act() -> None:
    wrong = unread(SAID_ON_A_FILE, registered_point_reads(), VERBS)
    assert not wrong, "these name no History read and no ledger verb: " + "; ".join(wrong)
    assert not unreasoned(NOT_SAID_ON_A_FILE), "an excuse with no reason"
    both = sorted(set(SAID_ON_A_FILE) & set(NOT_SAID_ON_A_FILE))
    assert not both, f"declared both said and not said: {both}"


def test_a_made_up_pass_with_nothing_said_is_refused() -> None:
    """The known positive: a product nobody declared, a source that names nothing, a pass gone."""
    said = {"thumbnails": ("history.derivatives",)}
    assert unsaid({"thumbnails", "invented_pass"}, said, {}) == ["invented_pass"]
    assert unsaid({"invented_pass"}, said, {"invented_pass": "a reason"}) == []
    assert stale({"faces"}, said, {}) == ["thumbnails"]
    reads = {"history.derivatives"}
    assert unread({"music": ("history.nothing_here",)}, reads, {"scanned"}) == [
        "music: history.nothing_here"
    ]
    assert unread({"music": ("ledger:hummed",)}, reads, {"scanned"}) == ["music: ledger:hummed"]
    assert unread({"music": ("downloads",)}, {"downloads"}, set()) == ["music: downloads"]
    assert unread({"music": ()}, reads, set()) == ["music: names nothing"]
    assert unread(said, reads, set()) == []
    assert unreasoned({"invented_pass": "  "}) == ["invented_pass"]


#: THE PRESSED FORM OF EVERY SOURCE'S LINE, by the sentence function History says it with, handed a
#: presser's word. A source a declaration names that is in neither table below fails the gate.
PRESSED_LINES: Mapping[str, Callable[[str], say.Line]] = {
    "history.derivatives": lambda by: say.made_ready(["thumb"], by=by),
    "history.left_out": lambda by: say.could_not("faces", again=False, by=by),
    "ledger:scanned": lambda by: say.fingerprinted_by(by, empty=False),
    "history.music_fingerprint": lambda by: say.music_fingerprinted(empty=False, by=by),
    "history.music_lookup": lambda by: say.acoustid_answered("nothing", by=by),
    "history.face_scan": lambda by: say.looked_for_faces(0, (), by=by),
    "ledger:face_run": lambda by: say.looked_for_faces(0, (), by=by),
    "history.indexed": lambda by: say.read_its_meaning(by=by),
    "history.watermark_scan": lambda by: say.watermark_none(by=by),
    "history.watermark_read": lambda by: say.watermark_found(
        "site", "quillmark", "Quillhouse", None, filed=True, by=by
    ),
    "history.details_read": lambda by: say.details_read_again(by=by),
    "history.asked": lambda by: say.asked_and_found_nothing("Quillbox", by=by),
    "history.asked_waiting": lambda by: say.asked_and_waiting("Quillbox", by=by),
}

#: The sources whose line is not a pass's own, and why a press does not reword it.
NOT_PRESSED_LINES: Mapping[str, str] = {
    "history.enriched": "the stash-box's own act on the file, said by the box, with whether a"
    " person applied its answer",
    "ledger:song_named": "the song's own line, said by the service that named it",
}


def unpressed(
    said: Mapping[str, Iterable[str]],
    pressed: Mapping[str, object],
    excused: Mapping[str, str],
) -> list[str]:
    """Every source a declaration names with no pressed form and no reason."""
    named = {source for sources in said.values() for source in sources}
    return sorted(one for one in named if one not in pressed and one not in excused)


def test_every_line_a_pass_says_has_a_form_for_a_press() -> None:
    missing = unpressed(SAID_ON_A_FILE, PRESSED_LINES, NOT_PRESSED_LINES)
    assert not missing, (
        "these lines say a pass ran and have no form that names who pressed it; give the sentence"
        " function a `by` (`sentences.had_sift`) and name it in PRESSED_LINES: "
        + ", ".join(missing)
    )
    for source, line in PRESSED_LINES.items():
        words = say.text_of(line("Wren Halloway"))
        assert words.startswith("Wren Halloway had Sift "), f"{source}: {words}"
    assert not unreasoned(NOT_PRESSED_LINES), "an excuse with no reason"


def test_a_made_up_line_with_no_pressed_form_is_refused() -> None:
    """The known positive: a source nobody gave a pressed form."""
    said = {"faces": ("history.face_scan", "history.invented")}
    assert unpressed(said, {"history.face_scan": None}, {}) == ["history.invented"]
    assert unpressed(said, {"history.face_scan": None}, {"history.invented": "a reason"}) == []


def test_the_scan_line_says_each_of_the_three_kinds_of_face() -> None:
    """Named first, then the faces Sift only suggests (a way to the person and a way to the
    question), then the faces nobody has named (a way to their group); a person both named and
    suggested is said once, as named."""
    rows = [
        {"person_id": None, "name": None, "pile_id": "pile-1", "attribution": None},
        {"person_id": "p-2", "name": "Ada Lumen", "pile_id": None, "attribution": "suggested"},
        {"person_id": "p-1", "name": "Neve Alder", "pile_id": None, "attribution": "matched"},
        {"person_id": "p-1", "name": "Neve Alder", "pile_id": None, "attribution": "suggested"},
    ]
    line = _faces_found(rows)  # type: ignore[arg-type]
    assert say.text_of(line) == "Neve Alder, a face that may be Ada Lumen and a face"
    # The way to the question and the way to the person are two links with words between them,
    # never side by side, where they would draw as one.
    assert [one.text for one in line[2:5]] == ["a face", " that may be ", "Ada Lumen"]
    assert [(one.kind, one.id, one.href) for one in say.things_in(line)] == [
        ("person", "p-1", None),
        ("faces", "p-2", "/organize/known-people/p-2?show=suggested"),
        ("person", "p-2", None),
        ("face_pile", "pile-1", None),
    ]
    # To a reader the question is not for, a suggestion is a face nobody has named.
    assert say.text_of(_faces_found(rows, asks=False)) == "Neve Alder and 2 faces"  # type: ignore[arg-type]
