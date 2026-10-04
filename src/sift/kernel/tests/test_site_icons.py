# SPDX-License-Identifier: AGPL-3.0-or-later
"""The site logos that ship with Sift: the pack agrees with itself, and the lookup cannot over-reach.

Two different things are checked here and they fail in different ways.

**The PACK** is data that was fetched once by a script and committed. Nothing re-derives it, so the
only thing that can be wrong with it is that it disagrees with itself: a manifest entry with no
picture, a picture nothing names, two entries fighting over one host. Each of those is silent: a
Site simply goes on being drawn as a letter, or is drawn as somebody else's logo, and there is
no failure anywhere to notice.

**The LOOKUP** is code, and the thing it must never do is over-reach. A slug arrives off the wire on
the icon route, so it must never become part of a path unless the manifest already named it; a host
arrives off a row, so it must match its own site and nothing that merely looks like it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from sift.kernel import site_icons

pytestmark = pytest.mark.regression


@pytest.fixture(autouse=True)
def _forget_the_cached_pack() -> None:
    """The readers cache, because the pack cannot change while Sift runs. A test changes it.

    Cleared before every case rather than after, so a case that fails part-way cannot leave a
    poisoned cache for the next one: the failure mode where a suite's second failure is an
    artefact of its first, and nobody can tell which is real.
    """
    site_icons.every.cache_clear()
    site_icons._by_host.cache_clear()
    site_icons._by_name.cache_clear()
    site_icons._slugs.cache_clear()
    site_icons._by_slug.cache_clear()


def _pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, icons: list[dict[str, object]]) -> None:
    """Stand a pack of invented sites up in a temporary directory and point the readers at it."""
    (tmp_path / "icons").mkdir(exist_ok=True)
    for entry in icons:
        (tmp_path / "icons" / f"{entry['slug']}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "manifest.json").write_text(json.dumps({"icons": icons}), encoding="utf-8")
    monkeypatch.setattr(site_icons, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(site_icons, "ICONS_DIR", tmp_path / "icons")


A_PACK: list[dict[str, object]] = [
    {"slug": "quillhouse", "name": "Quillhouse", "hosts": ["quillhouse.example", "quill.example"]},
    {"slug": "marrowvale", "name": "Marrowvale Studios", "hosts": ["marrowvale.example"]},
]


class TestThePackAgreesWithItself:
    """What ships is data, so the only fault it can have is an internal disagreement."""

    def test_every_entry_has_its_picture_and_every_picture_has_its_entry(self) -> None:
        """Either half missing is invisible in use: a Site is drawn as a letter, exactly as it
        was before the pack existed, and a picture nothing names is bytes in the repository that
        nothing can ever serve."""
        named = {icon.slug for icon in site_icons.every()}
        on_disk = {one.stem for one in site_icons.ICONS_DIR.glob("*.png")}

        assert named - on_disk == set(), "the manifest names pictures that are not there"
        assert on_disk - named == set(), "pictures are shipped that the manifest does not name"

    def test_no_two_entries_claim_one_host_or_one_slug(self) -> None:
        """A host claimed twice is decided by whichever entry the build script wrote first, which
        is an arbitrary answer to "whose logo is this", and a slug twice is one file overwriting
        the other while both entries go on naming it."""
        hosts = [host for icon in site_icons.every() for host in icon.hosts]
        slugs = [icon.slug for icon in site_icons.every()]

        assert len(hosts) == len(set(hosts)), "two entries claim one host"
        assert len(slugs) == len(set(slugs)), "two entries claim one slug"

    def test_every_host_is_a_bare_lower_case_host(self) -> None:
        """The lookup compares exactly, so a host written as a URL or in capitals is an entry that
        can never match anything, and nothing would say so."""
        for icon in site_icons.every():
            for host in icon.hosts:
                assert host == host.lower(), f"{icon.slug}: {host} is not lower case"
                assert "/" not in host and ":" not in host, f"{icon.slug}: {host} is not a host"
                assert not host.startswith("www."), f"{icon.slug}: {host} still carries www."


def _withheld_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, withheld: list[dict[str, object]]
) -> None:
    """`A_PACK`, with these entries withheld, and a file LEFT ON THE DISK for each of them, which
    is the stale state a hand edit or an interrupted build could leave behind."""
    _pack(tmp_path, monkeypatch, A_PACK)
    manifest = tmp_path / "manifest.json"
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["withheld"] = withheld
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    for entry in withheld:
        (tmp_path / "icons" / f"{entry['slug']}.png").write_bytes(b"\x89PNG\r\n\x1a\n")


A_WITHHELD: list[dict[str, object]] = [
    {"slug": "larkspur", "name": "Larkspur Studios", "hosts": ["larkspur.example"]},
    {"slug": "kind-studio", "name": "Studio", "hosts": [], "aliases": ["Studios"]},
]


class TestAWithheldSiteHasNoPicture:
    """A site the pack KNOWS and ships no picture for answers exactly as a site with no logo does:
    a tile draws its first letter, a link its plain link glyph. Its hosts and names stay known."""

    def test_no_picture_no_tone_no_token_by_address_or_by_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _withheld_pack(tmp_path, monkeypatch, A_WITHHELD)

        for address, name in (("larkspur.example", None), (None, "Larkspur Studios")):
            assert site_icons.icon_for(address, name) is None
            assert site_icons.tone_for(address, name) is None
            assert site_icons.icon_token(address, name) is None
        assert site_icons.icon_for(None, "Studio") is None

    def test_a_file_left_on_the_disk_is_never_served(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The allowlist is the SHIPPED entries: a withheld slug off the wire finds no path even
        with its old picture still sitting in the directory."""
        _withheld_pack(tmp_path, monkeypatch, A_WITHHELD)

        assert (tmp_path / "icons" / "larkspur.png").is_file()
        assert site_icons.path_of("larkspur") is None
        assert site_icons.path_of("kind-studio") is None

    def test_which_site_a_link_is_on_is_still_answered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`slug_for` says WHICH site; `path_of` says whether it has a picture. A withheld site is
        still that site."""
        _withheld_pack(tmp_path, monkeypatch, A_WITHHELD)

        assert site_icons.slug_for("https://larkspur.example/tour") == "larkspur"
        assert site_icons.slug_for_name("studios") == "kind-studio"

    def test_a_withheld_name_is_not_matched_to_a_site_that_shares_it_as_an_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row called "Studio" is the link kind. Without the kind in the index, a shipped site
        that happens to list the word as an alias would draw its logo beside it."""
        _pack(tmp_path, monkeypatch, [*A_PACK[:1], {**A_PACK[1], "aliases": ["Studio"]}])
        raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        raw["withheld"] = A_WITHHELD
        (tmp_path / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")

        assert site_icons.icon_for(None, "Studio") is None


#: What the shipped pack must hold back, by slug, with the reason it gives. A real person's picture
#: does not ship in a public repository; a drawn figure is not a mark that names a site; a link kind
#: is not a site. A rebuild that brought one back as a picture (or as a generic glyph) is
#: refused here.
WITHHELD = {
    **dict.fromkeys(
        (
            "harmony",
            "latexlolanoir",
            "legendarylootz",
            "missvikkilynn",
            "rebeccalordproductions",
            "lifestylefemdom",
            "pornopedia",
            "theartofblowjob",
            "thehotmeangirl",
        ),
        "photograph",
    ),
    **dict.fromkeys(
        (
            "suicidegirls",
            "hotoldermale",
            "vrlatina",
            "thebestporn",
            "diapergirls",
            "corbinfisher",
        ),
        "mascot",
    ),
    **dict.fromkeys(
        (
            "kind-home",
            "kind-official-website",
            "kind-studio",
            "kind-studio-profile",
            "kind-modeling-agency",
            "kind-artist-website",
            "kind-link",
            "kind-other",
        ),
        "link kind",
    ),
}


class TestTheShippedPackWithholds:
    """The pack is a set of MARKS; what is not one ships no picture at all."""

    @pytest.mark.parametrize(("slug", "reason"), sorted(WITHHELD.items()))
    def test_the_entry_is_withheld_with_its_reason_and_no_picture(
        self, slug: str, reason: str
    ) -> None:
        raw = json.loads(site_icons.MANIFEST.read_text(encoding="utf-8"))
        held_back = {one["slug"]: one for one in raw.get("withheld", [])}

        assert slug in held_back, f"{slug} is not withheld"
        assert held_back[slug]["reason"] == reason
        assert held_back[slug].get("why"), f"{slug} does not say why"
        assert slug not in {one["slug"] for one in raw["icons"]}, f"{slug} ships a picture again"
        assert not (site_icons.ICONS_DIR / f"{slug}.png").exists(), f"{slug}.png still ships"
        assert site_icons.path_of(slug) is None

    def test_no_slug_or_host_is_both_shipped_and_withheld(self) -> None:
        """One site on both lists is two answers to "does it have a picture", decided by which
        list the index read first."""
        shipped = site_icons.every()
        held_back = site_icons.withheld()

        assert {one.slug for one in shipped} & {one.slug for one in held_back} == set()
        assert {h for one in shipped for h in one.hosts} & {
            h for one in held_back for h in one.hosts
        } == set()

    def test_a_withheld_site_is_still_known_by_its_host(self) -> None:
        for icon in site_icons.withheld():
            for host in icon.hosts:
                assert site_icons.slug_for(host) == icon.slug
                assert site_icons.icon_for(host, icon.name) is None


class TestASiteWhoseOwnIconsAreNotItsMark:
    """Babepedia's icons are photographs and UViU's manifest serves its network's mark; each is
    drawn with its own wordmark instead: never a glyph, never the site's own icons again."""

    @pytest.mark.parametrize(("slug", "picture"), [("babepedia", "picked"), ("uviu", "page logo")])
    def test_the_mark_is_the_wordmark_not_the_sites_own_icon(self, slug: str, picture: str) -> None:
        raw = json.loads(site_icons.MANIFEST.read_text(encoding="utf-8"))
        entry = next(one for one in raw["icons"] if one["slug"] == slug)

        assert entry["picture"] == picture, f"{slug} is drawn from {entry['picture']!r} again"


TONED_PACK: list[dict[str, object]] = [
    {"slug": "quillhouse", "name": "Quillhouse", "hosts": ["quillhouse.example"], "tone": "light"},
    {"slug": "marrowvale", "name": "Marrowvale", "hosts": ["marrowvale.example"], "tone": "dark"},
    {"slug": "sunsetter", "name": "Sunsetter", "hosts": ["sunsetter.example"], "tone": "colour"},
    {"slug": "larkspur", "name": "Larkspur Studios", "hosts": ["larkspur.example"]},
    {"slug": "nightjar", "name": "Nightjar Media", "hosts": ["nightjar.example"], "tone": "sepia"},
]


class TestTheToneOfAMark:
    """`tone_for` answers for the picture `icon_for` finds, by the same keys in the same order."""

    def test_each_recorded_tone_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pack(tmp_path, monkeypatch, TONED_PACK)

        assert site_icons.tone_for("https://quillhouse.example/") == "light"
        assert site_icons.tone_for(None, "Marrowvale") == "dark"
        assert site_icons.tone_for("sunsetter.example") == "colour"

    def test_a_missing_or_unknown_tone_reads_as_colour(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`colour` is the answer that changes nothing about how a mark is drawn, so it is the one
        a pack written before tones existed (or with a word nobody taught this) gets."""
        _pack(tmp_path, monkeypatch, TONED_PACK)

        assert site_icons.tone_for("larkspur.example") == "colour"
        assert site_icons.tone_for("nightjar.example") == "colour"

    def test_the_address_wins_over_the_name_exactly_as_the_picture_does(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tone from one entry beside a picture from another would put a mark on the wrong
        ground, so the two are read through one match."""
        _pack(tmp_path, monkeypatch, TONED_PACK)

        found = site_icons.icon_for("marrowvale.example", "Quillhouse")
        assert found is not None and found.stem == "marrowvale"
        assert site_icons.tone_for("marrowvale.example", "Quillhouse") == "dark"

    def test_no_picture_is_no_tone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _pack(tmp_path, monkeypatch, TONED_PACK)
        (tmp_path / "icons" / "quillhouse.png").unlink()

        assert site_icons.tone_for("unknown.example", "Nobody") is None
        assert site_icons.tone_for("quillhouse.example") is None, "a tone for a missing picture"

    def test_the_shipped_pack_records_one_of_the_three_words_for_every_mark(self) -> None:
        raw = json.loads(site_icons.MANIFEST.read_text(encoding="utf-8"))

        for one in raw["icons"]:
            assert one.get("tone") in site_icons.TONES, f"{one['slug']}: tone {one.get('tone')!r}"


class TestFindingOne:
    """By host, by name, and the answers it must refuse to give."""

    def test_a_host_finds_its_own_site(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.slug_for("https://quillhouse.example/tour") == "quillhouse"
        assert site_icons.slug_for("https://www.quillhouse.example/") == "quillhouse"
        assert site_icons.slug_for("quillhouse.example") == "quillhouse"

    def test_a_second_domain_finds_the_same_site(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One site, two addresses, one logo. The manifest lists both rather than the lookup
        guessing that two hosts sharing a word are one site."""
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.slug_for("https://quill.example/") == "quillhouse"

    def test_a_look_alike_and_a_subdomain_find_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The match is an exact host, deliberately, and this is what that buys: nothing here can
        be talked into drawing one site's logo beside another site's name. A subdomain finding
        nothing is the accepted cost, and it is the safe direction: a letter rather than a lie."""
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.slug_for("https://notquillhouse.example/") is None
        assert site_icons.slug_for("https://quillhouse.example.attacker.test/") is None
        assert site_icons.slug_for("https://members.quillhouse.example/") is None

    def test_a_name_finds_a_site_with_no_address(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case that carries the pack. A Site made by a download has a name and nothing
        else, so a host lookup alone would miss the sites a library has most of."""
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.icon_for(None, "Marrowvale Studios") is not None
        assert site_icons.icon_for("", "  marrowvale studios ") is not None
        assert site_icons.icon_for(None, "no site by that name") is None

    def test_an_address_is_preferred_to_a_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two sites can share a name and no two share a host, so the stronger key is asked first."""
        _pack(tmp_path, monkeypatch, A_PACK)

        found = site_icons.icon_for("https://quillhouse.example/", "Marrowvale Studios")

        assert found is not None
        assert found.name == "quillhouse.png"

    def test_a_page_on_another_site_is_not_the_rows_address(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stash-box can file a studio's links with its page on a database site first, so the
        row's address is that page, and if the address won, the row would wear the DATABASE's
        logo. A page is a page ON a site; the name decides."""
        _pack(tmp_path, monkeypatch, A_PACK)

        found = site_icons.icon_for(
            "https://quillhouse.example/sites/marrowvale", "Marrowvale Studios"
        )

        assert found is not None and found.stem == "marrowvale"
        assert (
            site_icons.tone_for("https://quillhouse.example/sites/x", "Marrowvale Studios")
            is not None
        )

    def test_a_page_with_no_name_to_go_by_draws_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rather than the host's logo, which is exactly the lie above when the name is unknown."""
        _pack(tmp_path, monkeypatch, A_PACK)

        assert (
            site_icons.icon_for("https://quillhouse.example/sites/nobody", "nobody by that name")
            is None
        )
        assert site_icons.icon_token("https://quillhouse.example/sites/nobody") is None

    def test_a_root_address_is_still_the_rows_address(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The front door, with or without a query string, is still the site's own address, and a
        LINK on a page still finds the site it is on (`slug_for` answers that other question)."""
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.is_a_sites_own("https://quillhouse.example/?ref=feed")
        assert site_icons.is_a_sites_own("quillhouse.example")
        assert not site_icons.is_a_sites_own("https://quillhouse.example/tour")
        found = site_icons.icon_for("https://quillhouse.example/?ref=feed", "Marrowvale Studios")
        assert found is not None and found.stem == "quillhouse"
        assert site_icons.slug_for("https://quillhouse.example/tour") == "quillhouse"

    def test_an_empty_address_and_an_empty_name_find_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.host_of("   ") == ""
        assert site_icons.slug_for("") is None
        assert site_icons.slug_for_name("   ") is None
        assert site_icons.icon_for(None, None) is None


class TestASiteThatWasCalledSomethingElse:
    """A rename leaves rows behind under the old word. What the pack does about it is `_by_name`
    for a site that merely spells its name two ways, and a second ENTRY where the old name is a
    different mark. See the last case here."""

    def test_an_old_name_finds_the_picture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case this exists for: one site, one picture, and rows under either word."""
        _pack(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "quillhouse",
                    "name": "Quillhouse",
                    "hosts": ["quillhouse.example", "quillhaus.example"],
                    "aliases": ["Quillhaus"],
                }
            ],
        )

        assert site_icons.slug_for_name("Quillhouse") == "quillhouse"
        assert site_icons.slug_for_name("quillhaus") == "quillhouse"
        # And by either domain, which is the half that has always worked.
        assert site_icons.slug_for("https://quillhaus.example/pictures") == "quillhouse"

    def test_a_name_beats_another_entrys_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A site's own name is what it calls itself today, so it wins over somebody's old one."""
        _pack(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "quillhouse",
                    "name": "Quillhouse",
                    "hosts": ["quillhouse.example"],
                    "aliases": ["Marrowvale Studios"],
                },
                {
                    "slug": "marrowvale",
                    "name": "Marrowvale Studios",
                    "hosts": ["marrowvale.example"],
                },
            ],
        )

        assert site_icons.slug_for_name("Marrowvale Studios") == "marrowvale"

    def test_an_entry_with_no_aliases_reads_as_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The key is left out of most entries, and a missing key is not an empty one by accident."""
        _pack(tmp_path, monkeypatch, A_PACK)

        assert all(icon.aliases == () for icon in site_icons.every())

    def test_the_shipped_pack_draws_a_renamed_site_under_each_of_its_names(self) -> None:
        """THE PACK ITSELF, not an invented one.

        `Twitter` is an alias of X: X is one site with two addresses and one picture, and a Site
        row still called Twitter is that site under its old name. The pack holds no entry of its
        own for the old name (no bird), so the name index cannot beat the alias with it.

        The ROWS are never folded by this: `slices/people/site_merge.py` is the only way two Sites
        become one; a picture lookup only decides which mark a row is drawn with.
        """
        by_slug = {icon.slug: icon for icon in site_icons.every()}

        assert by_slug["x"].name == "X"
        assert set(by_slug["x"].hosts) == {"x.com", "twitter.com"}
        assert "Twitter" in by_slug["x"].aliases
        assert "twitter" not in by_slug
        assert all(icon.name.strip().lower() != "twitter" for icon in site_icons.withheld())

        assert site_icons.slug_for_name("X") == "x"
        assert site_icons.slug_for_name("Twitter") == "x"
        assert site_icons.icon_for(None, "Twitter") == site_icons.icon_for("https://x.com/", "X")


class TestTheWordsTheStashBoxesUse:
    """THE PACK ITSELF: a Site made from a stash-box link wears the box's word for the link KIND.

    StashDB, FansDB and PMVStash each name a link by what it points at: "reddit user", "PMV Haven
    video", "x.com (twitter)", and a Site row made from one carries that word. Each must draw the
    site it is on. And the words that are kinds rather than sites ("Home", "Studio") are KNOWN as
    kinds and draw no picture: a tile draws the row's first letter and a link its plain link
    glyph.
    """

    @pytest.mark.parametrize(
        ("word", "slug"),
        [
            ("reddit user", "reddit"),
            ("subreddit", "reddit"),
            ("PMV Haven video", "pmvhaven"),
            ("Eporner profile", "eporner"),
            ("x.com (twitter)", "x"),
            ("Discord Server", "discord"),
        ],
    )
    def test_a_box_word_finds_its_site(self, word: str, slug: str) -> None:
        assert site_icons.slug_for_name(word) == slug
        assert site_icons.path_of(slug) is not None

    @pytest.mark.parametrize(
        ("word", "slug"),
        [
            ("Home", "kind-home"),
            ("Official Website", "kind-official-website"),
            ("Studio Profile", "kind-studio-profile"),
            ("link", "kind-link"),
        ],
    )
    def test_a_box_word_for_a_kind_is_known_and_draws_nothing(self, word: str, slug: str) -> None:
        assert site_icons.slug_for_name(word) == slug
        assert site_icons.path_of(slug) is None
        assert site_icons.icon_for(None, word) is None

    def test_the_old_word_for_x_draws_x(self) -> None:
        """`Twitter` alone is X's old name, so it draws X's mark like the boxes' spellings do."""
        assert site_icons.slug_for_name("Twitter") == "x"


class TestWhatTheRouteMayAskFor:
    """A slug arrives off the wire, so the manifest is the allowlist and not a starting point."""

    def test_a_slug_the_manifest_does_not_name_is_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.path_of("nobody") is None

    @pytest.mark.parametrize(
        "asked",
        [
            "../../../../etc/passwd",
            "..\\..\\manifest",
            "/etc/shadow",
            "quillhouse/../../manifest",
        ],
    )
    def test_a_slug_that_tries_to_leave_the_pack_is_nothing(
        self, asked: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refused by never becoming a path at all, rather than by being turned into one and then
        measured: there is no instant here at which a stranger's string is joined to a directory."""
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.path_of(asked) is None

    def test_a_picture_the_manifest_does_not_name_is_not_served(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The manifest is the allowlist, so a file it does not name is not reachable, which is
        what makes the check a check rather than a spelling of `is_file`. A rebuild that dropped a
        site leaves its picture behind, and a picture nothing declares is a picture nobody can say
        where came from."""
        _pack(tmp_path, monkeypatch, A_PACK)
        (tmp_path / "icons" / "leftover.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        assert (tmp_path / "icons" / "leftover.png").is_file()
        assert site_icons.path_of("leftover") is None

    def test_a_named_slug_whose_picture_is_gone_is_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A half-copied install. The answer is a miss, which every caller already draws a letter
        for, rather than a path that is handed to a file response and fails there."""
        _pack(tmp_path, monkeypatch, A_PACK)
        (tmp_path / "icons" / "quillhouse.png").unlink()

        assert site_icons.path_of("quillhouse") is None


class TestAPackThatIsNotThere:
    """An install with no manifest is a Sift that looks like the one before this feature."""

    def test_a_missing_manifest_is_an_empty_pack(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(site_icons, "MANIFEST", tmp_path / "gone.json")

        assert site_icons.every() == ()
        assert site_icons.icon_for("https://quillhouse.example/") is None

    def test_a_manifest_that_is_not_a_pack_is_an_empty_pack(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refusing to start over a file that can be re-fetched would trade a wall of letters for
        an application that does not open."""
        (tmp_path / "manifest.json").write_text("[not json", encoding="utf-8")
        monkeypatch.setattr(site_icons, "MANIFEST", tmp_path / "manifest.json")

        assert site_icons.every() == ()

    def test_an_entry_missing_its_slug_or_its_hosts_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One bad line does not cost the other nine hundred."""
        (tmp_path / "manifest.json").write_text(
            json.dumps(
                {
                    "icons": [
                        "not an entry",
                        {"name": "No Slug", "hosts": ["nowhere.example"]},
                        {"slug": "nohosts", "name": "No Hosts"},
                        {"slug": "good", "hosts": ["good.example"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(site_icons, "MANIFEST", tmp_path / "manifest.json")

        assert [icon.slug for icon in site_icons.every()] == ["good"]
        assert site_icons.every()[0].name == "good", "a nameless entry is named for its slug"

    def test_a_manifest_holding_something_that_is_not_a_list_is_an_empty_pack(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({"icons": 7}), encoding="utf-8")
        monkeypatch.setattr(site_icons, "MANIFEST", tmp_path / "manifest.json")

        assert site_icons.every() == ()


class TestNamesFromAPackThatIsNotThere:
    """The names are read from the same file as the pictures, and fail the same way: to nothing."""

    @pytest.mark.parametrize("contents", [None, "[not json"])
    def test_a_missing_or_garbled_manifest_names_no_site_and_no_label(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contents: str | None
    ) -> None:
        manifest = tmp_path / "manifest.json"
        if contents is not None:
            manifest.write_text(contents, encoding="utf-8")
        monkeypatch.setattr(site_icons, "MANIFEST", manifest)

        assert site_icons.name_for("https://quillhouse.example/esme") is None
        assert site_icons.is_a_label("Studio Profile") is False

    def test_an_entry_that_cannot_name_a_site_costs_only_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A line that is not an entry, one with no name, and a missing site short of its host or
        its name are each skipped; the good entry beside them is still read."""
        (tmp_path / "manifest.json").write_text(
            json.dumps(
                {
                    "icons": [
                        "not an entry",
                        {"slug": "nameless", "name": "  ", "hosts": ["nameless.example"]},
                        {"slug": "good", "name": "Good", "hosts": ["good.example"]},
                    ],
                    "missing": [
                        "not an entry",
                        {"host": "hostonly.example"},
                        {"name": "a name only"},
                        {"host": "tidepool.example", "name": "Tidepool"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(site_icons, "MANIFEST", tmp_path / "manifest.json")

        assert site_icons.name_for("https://good.example/") == "Good"
        assert site_icons.name_for("https://tidepool.example/") == "Tidepool"
        assert site_icons.name_for("https://nameless.example/") is None
        assert site_icons.name_for("https://hostonly.example/") is None


class TestAPicturesToken:
    """`token_of`: the name a shipped picture is served under, so a browser may keep it."""

    def test_the_token_is_the_release_and_a_digest_of_the_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bytes because a new picture for the same site must not be answered by the old one
        a browser kept; the release because a release may change how the same file is served."""
        monkeypatch.setattr(site_icons, "app_version", lambda: "9.9.9")
        one, two = tmp_path / "one.png", tmp_path / "two.png"
        one.write_bytes(b"\x89PNG one")
        two.write_bytes(b"\x89PNG two")

        token = site_icons.token_of(one)

        assert token.startswith("9.9.9-")
        assert len(token.removeprefix("9.9.9-")) == 16
        assert token != site_icons.token_of(two)

    def test_a_build_with_no_version_is_named_as_a_dev_build(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(site_icons, "app_version", lambda: "")
        picture = tmp_path / "dev.png"
        picture.write_bytes(b"\x89PNG dev")

        assert site_icons.token_of(picture).startswith("dev-")

    def test_a_picture_that_cannot_be_read_gets_a_token_nothing_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A listing is never failed over one unreadable logo; the file route answers it as a miss."""
        monkeypatch.setattr(site_icons, "app_version", lambda: "9.9.9")

        assert site_icons.token_of(tmp_path / "gone.png") == "9.9.9-unread"

    def test_a_site_the_pack_draws_is_named_by_its_pictures_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pack(tmp_path, monkeypatch, A_PACK)

        assert site_icons.icon_token("https://quillhouse.example/") == site_icons.token_of(
            tmp_path / "icons" / "quillhouse.png"
        )
        assert site_icons.icon_token("https://nowhere.example/") is None


class TestWhatASiteIsCalled:
    """`name_for` and `is_a_label`: the question a stash-box's addresses are filed by.

    A box names the site beside each address with its word for the KIND of link ("Reddit User",
    "Studio Profile"), and a Site made from that word is a label, not a site. The address's own
    host is the fact, and this is Sift's name for it.
    """

    _MANIFEST: ClassVar[dict[str, object]] = {
        "icons": [
            {
                "slug": "quillhouse",
                "name": "Quillhouse",
                "hosts": ["quillhouse.example"],
                "aliases": ["Quillhouse profile", "Quill House"],
            },
            # A site known only by its name, sharing a word another entry has as an alias.
            {"slug": "marrow", "name": "Marrow", "hosts": []},
            {"slug": "vale", "name": "Vale", "hosts": ["vale.example"], "aliases": ["Marrow"]},
        ],
        "withheld": [
            {
                "slug": "lantern",
                "name": "Lantern",
                "hosts": ["lantern.example"],
                "reason": "mascot",
            },
            {
                "slug": "kind-studio-profile",
                "name": "Studio Profile",
                "hosts": [],
                "aliases": ["Studio page"],
                "reason": "link kind",
            },
        ],
        "missing": [{"host": "tidepool.example", "name": "Tidepool"}],
    }

    @pytest.fixture(autouse=True)
    def _a_pack(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps(self._MANIFEST), encoding="utf-8")
        monkeypatch.setattr(site_icons, "MANIFEST", tmp_path / "manifest.json")

    @pytest.mark.parametrize(
        ("address", "name"),
        [
            ("https://quillhouse.example/u/esme", "Quillhouse"),
            ("https://www.quillhouse.example/u/esme", "Quillhouse"),
            # A page on a subdomain is that site: a name is not a picture (see `name_for`).
            ("https://profiles.quillhouse.example/esme", "Quillhouse"),
            # A withheld site is a site; a site with no picture at all is one too.
            ("https://lantern.example/esme", "Lantern"),
            ("https://tidepool.example/esme", "Tidepool"),
        ],
    )
    def test_a_host_is_named_the_way_the_pack_names_its_site(self, address: str, name: str) -> None:
        assert site_icons.name_for(address) == name

    @pytest.mark.parametrize(
        "address",
        [
            # A name that merely ENDS the same way is another site.
            "https://notquillhouse.example/esme",
            "https://esme.example.test/",
            # A last label alone is nothing.
            "https://example/esme",
            "",
        ],
    )
    def test_a_host_the_pack_does_not_know_has_no_name(self, address: str) -> None:
        assert site_icons.name_for(address) is None

    @pytest.mark.parametrize(
        ("name", "label"),
        [
            # The link kinds, under any spelling they carry.
            ("Studio Profile", True),
            ("studio page", True),
            # An alias that is no site's own name: a box's word for a known site.
            ("Quillhouse profile", True),
            ("Quill House", True),
            # A site's own name, even where another entry lists it as an alias, is a site.
            ("Marrow", False),
            ("Quillhouse", False),
            ("Lantern", False),
            ("Tidepool", False),
            # A word the pack has never heard of is nobody's label.
            ("Clipvault", False),
            ("", False),
        ],
    )
    def test_a_label_is_a_box_word_and_never_a_sites_name(self, name: str, label: bool) -> None:
        assert site_icons.is_a_label(name) is label


class TestTheShippedPackNamesTheBoxWords:
    """The real pack: three words stash-boxes file links under are labels, and the sites their
    addresses are on have names. Twitter is X's old name, so it reads as a label for X."""

    @pytest.mark.parametrize(
        "word",
        # `Twitter` is X's old name, an alias of X and no site's own name now, so an address a
        # box files under it is filed under X, the name Sift gives the host.
        ["Reddit User", "Studio Profile", "Modeling Agency", "Home", "Eporner profile", "Twitter"],
    )
    def test_a_box_word_is_a_label(self, word: str) -> None:
        assert site_icons.is_a_label(word)

    @pytest.mark.parametrize("word", ["Reddit", "X", "Kink"])
    def test_a_sites_own_name_is_not(self, word: str) -> None:
        assert not site_icons.is_a_label(word)

    @pytest.mark.parametrize(
        ("address", "name"),
        [
            ("https://www.reddit.com/user/esmewren", "Reddit"),
            ("https://www.kink.com/model/esmewren", "Kink"),
            ("https://www.eporner.com/profile/esmewren/", "Eporner"),
        ],
    )
    def test_their_hosts_are_named(self, address: str, name: str) -> None:
        assert site_icons.name_for(address) == name


class TestAnIndexOfSites:
    """A database of sites links every studio it lists, so its address is never the studio's."""

    @pytest.mark.parametrize(
        ("address", "index"),
        [
            ("https://stashdb.org/studios/abc", True),
            ("https://www.iafd.com/studio.rme", True),
            ("https://en.wikipedia.org/wiki/Quillhouse", True),
            ("https://notwikipedia.org/", False),
            ("https://quillhouse.example/", False),
            ("not an address", False),
            ("", False),
        ],
    )
    def test_an_index_is_known_by_its_host_or_a_subdomain_and_nothing_like_it(
        self, address: str, index: bool
    ) -> None:
        assert site_icons.is_index_host(address) is index


class TestAPackThatAlreadyDrawsASiteWell:
    """Asked before a stash-box is asked for a picture: a box's logo of a site the pack draws at
    full quality is a request nobody would see the answer to."""

    def test_only_a_high_quality_picture_that_is_there_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pack(
            tmp_path,
            monkeypatch,
            [
                {"slug": "quillhouse", "name": "Quillhouse", "hosts": ["quillhouse.example"],
                 "quality": "high"},
                {"slug": "marrowvale", "name": "Marrowvale Studios",
                 "hosts": ["marrowvale.example"], "quality": "low"},
                {"slug": "fernleaf", "name": "Fernleaf", "hosts": ["fernleaf.example"],
                 "quality": "high"},
            ],
        )  # fmt: skip
        (tmp_path / "icons" / "fernleaf.png").unlink()

        assert site_icons.ships_a_good_one("https://quillhouse.example/")
        assert site_icons.ships_a_good_one(None, "Quillhouse")
        assert not site_icons.ships_a_good_one("https://marrowvale.example/"), "an enlarged icon"
        assert not site_icons.ships_a_good_one(None, "Fernleaf"), "a picture that is not there"
        assert not site_icons.ships_a_good_one("https://unknown.example/", "an unknown studio")
