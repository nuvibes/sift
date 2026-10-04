# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rule that reads a stash-box studio as one creator's username, a Site, or a question.

Two signs: the studio's name (or an alias) credited on its scenes, as a share of them; and a store
page of hers among its links. Both make a username; neither, or a store with nobody of its name
credited, keep a Site; anything between is a question. Every case below is one row of that.
"""

from __future__ import annotations

from sift.kernel.access.creator_studios import ASK_FROM, HERS, Verdict, fold, read_studio
from sift.kernel.urls import is_a_creator_store

HER = "Esme Wrenfield"
STORE = "https://www.manyvids.com/Profile/1001/Esme-Wrenfield/Store/Videos/"
SECOND_STORE = "https://www.clips4sale.com/studio/1002/esmewrenfield-clips"
SOCIAL = "https://x.com/esmewrenfield"
ABOUT_HER = "https://www.iafd.com/person.rme/id=1/esme-wrenfield"


def _scenes(hers: int, theirs: int) -> list[list[str]]:
    return [[HER] for _ in range(hers)] + [["Wren Halloway"] for _ in range(theirs)]


def test_a_creators_own_store_with_her_in_every_scene_is_her_username() -> None:
    reading = read_studio(HER, [], [SOCIAL, STORE], _scenes(10, 0))

    assert reading.verdict is Verdict.USERNAME
    assert reading.home is not None
    assert (reading.home.site, reading.home.handle) == ("ManyVids", "Esme-Wrenfield")
    assert reading.store


def test_her_name_in_four_of_five_scenes_is_still_hers_and_three_of_five_is_asked() -> None:
    assert HERS == 0.8
    assert read_studio(HER, [], [STORE], _scenes(4, 1)).verdict is Verdict.USERNAME
    assert read_studio(HER, [], [STORE], _scenes(3, 2)).verdict is Verdict.ASK


def test_a_studio_selling_on_a_store_with_nobody_of_its_name_credited_stays_a_site() -> None:
    """A producer with a Clips4Sale page is a producer: the store alone decides nothing."""
    reading = read_studio("Another Studio", [], [SECOND_STORE], _scenes(0, 12))

    assert reading.verdict is Verdict.SITE
    assert reading.home is None
    assert reading.others == 1


def test_her_name_with_no_store_but_a_page_of_hers_is_asked_and_no_page_at_all_is_a_site() -> None:
    assert ASK_FROM == 0.5
    asked = read_studio(HER, [], [SOCIAL], _scenes(1, 1))
    assert asked.verdict is Verdict.ASK
    assert asked.home is not None and asked.home.site == "X"
    assert not asked.store
    assert read_studio(HER, [], [SOCIAL], _scenes(1, 2)).verdict is Verdict.SITE
    assert read_studio(HER, [], [], _scenes(5, 0)).verdict is Verdict.SITE


def test_an_alias_counts_as_her_name_and_spelling_is_folded() -> None:
    reading = read_studio("esmewrenfield clips", ["esme-wrenfield"], [STORE], _scenes(3, 0))

    assert reading.verdict is Verdict.USERNAME
    assert fold("Esme  Wrenfield's") == fold("esme wrenfields")


def test_the_store_named_after_her_is_chosen_over_the_first() -> None:
    reading = read_studio(HER, [], [SECOND_STORE, STORE], _scenes(2, 0))

    assert reading.home is not None
    assert reading.home.site == "ManyVids"


def test_a_studio_with_no_scenes_read_is_a_site() -> None:
    assert read_studio(HER, [], [STORE], []).verdict is Verdict.SITE


def test_one_scene_crediting_her_from_a_store_is_hers_and_one_without_her_is_a_site() -> None:
    """How the lookup reads each scene as it arrives."""
    assert read_studio(HER, [], [STORE], [[HER, "Wren Halloway"]]).verdict is Verdict.USERNAME
    assert read_studio(HER, [], [STORE], [["Wren Halloway"]]).verdict is Verdict.SITE


def test_the_sites_own_address_and_a_page_about_her_are_never_her_home() -> None:
    """A Site called ManyVids linking manyvids.com is that Site; a reference page is about her."""
    assert read_studio("ManyVids", [], [STORE], [["ManyVids"]]).verdict is Verdict.SITE
    assert read_studio(HER, [], [ABOUT_HER], _scenes(5, 0)).home is None


def test_a_store_is_matched_by_its_host() -> None:
    assert is_a_creator_store(STORE)
    assert is_a_creator_store("https://onlyfans.com/esmewrenfield")
    assert not is_a_creator_store(SOCIAL)
    assert not is_a_creator_store("https://notmanyvids.com/x")
