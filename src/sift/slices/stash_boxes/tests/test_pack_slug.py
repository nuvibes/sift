# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which shipped logo a found entry draws, worked out locally. No site is named: the entry under
test is taken from the manifest at run time."""

from __future__ import annotations

import pytest

from sift.kernel.records import FoundRecord, Subject
from sift.kernel.site_icons import every
from sift.slices.stash_boxes.router import _pack_slug


def _one_in_the_pack() -> tuple[str, str]:
    """A slug in the shipped pack and one host it stands for."""
    for entry in every():
        if entry.hosts:
            return entry.slug, sorted(entry.hosts)[0]
    pytest.skip("the pack ships with no entries in this tree")  # pragma: no cover


def _found(subject: Subject, name: str, links: list[str] | None = None) -> FoundRecord:
    return FoundRecord(
        source_id="box-1",
        remote_id="r-1",
        subject=subject,
        name=name,
        fields={} if links is None else {"links": links},
    )


def test_a_found_site_is_matched_to_the_pack_by_its_address() -> None:
    slug, host = _one_in_the_pack()

    assert _pack_slug(_found(Subject.SITE, "Quillhouse", [f"https://{host}/"])) == slug


def test_a_page_on_another_site_never_gives_a_studio_that_sites_logo() -> None:
    """A studio's page on another site never gives the studio that site's logo."""
    _, host = _one_in_the_pack()

    assert _pack_slug(_found(Subject.SITE, "Quillhouse", [f"https://{host}/quillhouse"])) is None


def test_a_found_site_with_no_address_is_matched_by_what_it_is_called() -> None:
    """Most Sites in a library have no address at all, and a name is the weaker key that is left."""
    slug, _ = _one_in_the_pack()
    named = next(one.name for one in every() if one.slug == slug)

    assert _pack_slug(_found(Subject.SITE, named)) == slug


def test_a_link_to_a_database_never_gives_a_site_the_databases_logo() -> None:
    """A box lists a studio's ThePornDB page first among its links. That page is where the studio
    is written about, not the studio, so the pack's next address wins, and a site with only such
    links draws what it always drew."""
    slug, host = _one_in_the_pack()

    assert (
        _pack_slug(
            _found(
                Subject.SITE,
                "Quillhouse",
                ["https://theporndb.net/sites/quillhouse", f"https://{host}/"],
            )
        )
        == slug
    )
    assert (
        _pack_slug(_found(Subject.SITE, "Quillhouse", ["https://stashdb.org/studios/abc"])) is None
    )
    # A database's own front door is still the database, never the studio.
    assert _pack_slug(_found(Subject.SITE, "Quillhouse", ["https://stashdb.org/"])) is None


def test_a_site_the_pack_has_never_heard_of_draws_what_it_always_drew() -> None:
    assert _pack_slug(_found(Subject.SITE, "Quillhouse", ["https://quillhouse.invalid/"])) is None


def test_a_person_is_never_looked_up_in_a_pack_of_site_logos() -> None:
    """Asking it about a performer would match somebody whose name happens to be a registered
    domain: a wrong picture rather than a missing one. The pack is a pack of SITE logos."""
    _, host = _one_in_the_pack()

    assert _pack_slug(_found(Subject.PERSON, "Ada Lumen", [f"https://{host}/"])) is None


def test_a_withheld_entry_names_no_picture() -> None:
    """A site the pack knows but withholds (a link kind such as "Studio") gets no slug: the
    chooser would ask for its picture and be told 404 for every row."""
    assert _pack_slug(_found(Subject.SITE, "Studio", [])) is None
