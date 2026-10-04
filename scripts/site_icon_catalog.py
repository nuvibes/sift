# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the site-icon build knows BY HAND: names, joins, and where a better picture lives.

A MAINTAINER'S HELPER, imported only by `scripts/build_site_icons.py`. Everything the crawl can find
for itself (a site's own head, its web manifest, a stash-box's list of sites and studios) it
finds for itself. What is here is the residue that no crawl can decide: which two hosts are one
site, which words a library row may wear for a site, which vector on Wikimedia Commons is the
brand's own mark, and which entries ship NO picture (a photograph, a mascot, a link KIND).

**Every entry in the picture tables was fetched and LOOKED AT before it was written down.** A title
or a slug that stops resolving costs nothing but a fall-through to the next source, and the
`--report` names the source each icon actually came from, so a rotted entry is visible there.
"""

from __future__ import annotations

from collections.abc import Iterable

#: HOSTS THAT ARE ONE SITE. The second host is folded into the entry the first one names.
#:
#: The stash-boxes file some sites under two addresses (StashDB lists Telegram at `telegram.org`
#: and FansDB at `t.me`), and the pack refuses two entries claiming one name, so without this a
#: library row called `Telegram` would draw whichever entry the build happened to write first.
HOST_JOINS: dict[str, str] = {
    "t.me": "telegram.org",
    "threads.net": "threads.com",
    "msha.ke": "milkshake.app",
    "justforfans.app": "justfor.fans",
    "legacy.peach.com": "peach.com",
    "web.archive.org": "archive.org",
}

#: Sites no crawl layer reaches, named by hand: `(host, name, other names it answers to)`.
#:
#: The Internet Archive is here because every layer DROPS `archive.org` on purpose (a studio whose
#: address is an archived page is a studio that no longer exists), and a Username link to the
#: archive is still a link to a site with a logo.
NAMED_SITES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("archive.org", "Internet Archive", ("Wayback Machine",)),
    # SITES A STUDIO COULD CLAIM. A studio whose "home" is its page on one of these sites would
    # give the site's host the studio's name and logo (`build_site_icons.is_own_address` refuses
    # that). Each is a site that links in a library point at, so it is named here, as itself.
    ("amarotic.com", "Amarotic", ()),
    ("aventertainments.com", "AV Entertainment", ()),
    ("hotmovies.com", "HotMovies", ()),
    ("pissvids.com", "PissVids", ()),
    ("pornbox.com", "Pornbox", ()),
    ("povr.com", "POVR", ()),
    ("r18.com", "R18", ()),
    ("vrporn.com", "VRPorn", ()),
)

#: WHAT A SITE IS CALLED, where the first box to name it named a LINK KIND instead.
#:
#: PMVStash files Eporner as "Eporner profile" and "Eporner video", and whichever arrives first
#: would otherwise become the site's name in the pack. The kind words stay as aliases.
NAMES: dict[str, str] = {
    "discord.com": "Discord",
    "eporner.com": "Eporner",
    "discuss.eroscripts.com": "EroScripts",
    "hypnotube.com": "Hypnotube",
    "iwara.tv": "iwara",
    "milovana.com": "Milovana",
    "porntrex.com": "PornTrex",
    "sexlikereal.com": "SexLikeReal",
    "simpcity.su": "SimpCity",
    "spankbang.com": "SpankBang",
    "pmvhaven.com": "PMV Haven",
    "empornium.is": "Empornium",
}

#: WORDS A LIBRARY ROW MAY WEAR FOR A SITE, by the host of the entry they belong to.
#:
#: The stash-boxes each name a link KIND rather than a site ("Eporner video", "PMV Haven creator",
#: "reddit user"), and a Site made from one of those links carries that word. The box's own names
#: arrive by themselves (the merge keeps every name a layer used as an alias); these are the ones
#: the boxes spell differently from each other or not at all.
ALIASES: dict[str, tuple[str, ...]] = {
    "discord.com": ("Discord", "Discord Server"),
    "reddit.com": ("Reddit", "Reddit User", "reddit user", "reddit post", "subreddit"),
    # `Twitter` is X's old name for the same site, so a Site row still called Twitter draws X's
    # mark: one site, two addresses, one picture. The rows themselves are never folded here
    # (`slices/people/site_merge.py` is the only way two Sites become one).
    "x.com": ("Twitter", "x.com (twitter)", "X / Twitter", "Twitter (X)", "X (Twitter)"),
    "pmvhaven.com": ("PMV Haven", "PMV Haven creator", "PMV Haven profile", "PMV Haven video"),
    "eporner.com": ("Eporner", "Eporner profile", "Eporner video"),
    "hypnotube.com": ("Hypnotube", "Hypnotube profile", "Hypnotube video"),
    "iwara.tv": ("iwara", "iwara.tv", "iwara.tv profile", "iwara.tv video"),
    "pornhub.com": ("Pornhub profile", "Pornhub video"),
    "spankbang.com": ("Spankbang", "SpankBang", "Spankbang profile", "Spankbang video"),
    "sexlikereal.com": (
        "SexLikeReal",
        "SexLikeReal performer profile",
        "SexLikeReal scene",
        "SexLikeReal studio",
    ),
    "simpcity.su": ("SimpCity", "SimpCity member", "SimpCity thread"),
    "xhamster.com": ("xHamster page", "xHamster video"),
    "xvideos.com": ("XVideos profile", "XVideos video"),
    "porntrex.com": ("PornTrex", "PornTrex member page", "PornTrex video"),
    "milovana.com": ("Milovana", "Milovana Profile", "Milovana Release Thread"),
    "empornium.is": ("Empornium", "Empornium user page"),
    "discuss.eroscripts.com": ("EroScripts", "Eroscripts", "Eroscripts user"),
    "milkshake.app": ("Milkshake", "Milkshake App"),
    "justfor.fans": ("Just for Fans", "JustForFans", "JustFor.Fans"),
    "peach.com": ("Peach", "Peach (legacy)"),
    "hoo.be": ("Hoo", "hoo.be"),
    "lnk.bio": ("LnkBio", "Lnk.Bio"),
    "link.me": ("LinkMe", "Linkme"),
    "dmm.co.jp": ("DMM / FANZA", "DMM", "FANZA"),
    "seesaawiki.jp": ("Seesaawiki", "Seesaawiki / NHPedia", "NHPedia", "Sougouwiki"),
    "stashdb.org": ("StashDB", "StashDB performer"),
    "fansdb.cc": ("FansDB", "FansDB performer"),
    "pmvstash.org": ("PMV Stash", "PMVStash"),
    "javstash.org": ("JAVStash", "JAV Stash"),
    "theporndb.net": ("ThePornDB", "ThePornDB performer", "TPDB"),
    "iafd.com": ("IAFD", "IAFD profile"),
    "bsky.app": ("Bluesky", "BlueSky"),
    "telegram.org": ("Telegram",),
    "threads.com": ("Threads",),
    "stripchat.com": ("Stripchat",),
    "myfreecams.com": ("MyFreeCams", "MFC"),
    "share.myfreecams.com": ("MFC Share",),
}

#: VECTORS ON WIKIMEDIA COMMONS THAT ARE THE BRAND'S OWN MARK, by host (or by slug for an entry
#: with no host). Looked at, each one.
#:
#: Only MARKS, never a wordmark: at the 96 pixels a tile draws a logo at, a word six times wider than
#: it is tall is a grey line. Commons has the wordmark for several sites here (OnlyFans, Reddit,
#: Kick, DeviantArt) and they are deliberately left out: the site's own icon or Simple Icons'
#: glyph is the better picture of those.
COMMONS: dict[str, str] = {
    "instagram.com": "Instagram logo 2016.svg",
    "facebook.com": "2023 Facebook icon.svg",
    "youtube.com": "YouTube full-color icon (2017).svg",
    "patreon.com": "Patreon logo.svg",
    "telegram.org": "Telegram logo.svg",
    "threads.com": "Threads (app) logo.svg",
    "tiktok.com": "Tiktok icon.svg",
    "twitch.tv": "Twitch Glitch Logo Purple.svg",
    "bsky.app": "Bluesky Logo.svg",
    "wikipedia.org": "Wikipedia-logo-v2.svg",
    "imdb.com": "IMDB Logo 2016.svg",
    "themoviedb.org": "Tmdb.new.logo.svg",
    # The two national film databases whose own mark IS a flag, drawn at 16 pixels in their
    # favicons. The flag itself, as a vector (both looked at).
    "bgafd.co.uk": "Flag of the United Kingdom.svg",
    "egafd.com": "Flag of Europe.svg",
}

#: SITES WHOSE PLATE COMES OFF WHATEVER ITS COLOUR, by host.
#:
#: A saturated square behind a mark is normally the brand's own tile and is kept (see
#: `site_icon_art.remove_plate`). CamSoda's largest icon is its ring on a light-blue square, and on
#: a Sites tile that reads as a background behind the logo, not as the logo. Decided by looking,
#: per brand, never by a colour rule:
#: a colour rule wide enough to catch this blue would also strip Snapchat's yellow.
PLATE_OFF: frozenset[str] = frozenset({"camsoda.com"})

#: A SITE'S OWN ICONS THAT ARE NOT ITS MARK, by host, each with what they are instead.
#:
#: For these the build never takes a picture off the site's own head, manifest or conventional
#: addresses (the source that is right for nearly every other site) because what is served there
#: is SOMEBODY ELSE'S mark or a photograph. The site's logo then comes from `PICKED`, the page
#: header's own logo, a stash-box, or nowhere: a letter is a better answer than another brand's
#: logo.
NOT_ITS_MARK: dict[str, str] = {
    # uviu.com's web manifest names a 512-pixel Pornhub mark (the network it belongs to) while
    # its own page draws the UViU wordmark in its header.
    "uviu.com": "its web manifest serves its network's mark, not the UViU wordmark",
    # Every icon Babepedia declares is a photograph of a person; its wordmark is in `PICKED`.
    "babepedia.com": "its own icons are photographs of a person",
}

#: PICTURES CHOSEN BY HAND, by host (or by slug for an entry with no host): `(address, what it is)`.
#:
#: Tried FIRST, before anything the crawl finds. Each is here because the crawl's own answer was
#: wrong or soft and a better picture of the SAME brand exists where no rule can find it: a page
#: header's logo behind a challenge page, a flag on Wikimedia Commons, a parent brand's mark. **Every
#: address was fetched and LOOKED AT before it was written down**, and a rotted one costs only a
#: fall-through: the entry then gets what the crawl finds, which `--contact-sheet` shows.
PICKED: dict[str, tuple[str, str]] = {
    # Its page answers a crawler with a challenge; the logo file it draws does not.
    "babepedia.com": (
        "https://www.babepedia.com/images/logo2020-trans.png",
        "the site's own wordmark, 200 x 100, from its page",
    ),
    # Its only big picture is `og:image`, a screenshot of phones; its own mark is this 96.
    "magic.ly": ("https://magic.ly/favicon.png", "the site's own 96 px mark"),
    # A 192-pixel mark beside the declared 64 that nothing machine-readable points at.
    "reddit.com": (
        "https://www.redditstatic.com/shreddit/assets/favicon/192x192.png",
        "the site's 192 px favicon, not declared",
    ),
}

#: Sites whose own picture is a PHOTOGRAPH OF A REAL PERSON (a creator's avatar as the icon, a
#: model on the studio's logo). A real person's picture never ships in this repository, so these
#: are WITHHELD: no picture ships for them at all, whatever their site or stash-box answers with,
#: and a Site row under one is drawn as its first letter. Found by looking at the tiles, one by
#: one; add a host here the moment another is seen.
#:
#: Not the generic website glyph: a glyph is not the site's logo, so a Sites tile wearing one would
#: say "this is a website" where the letter says which site. A site with no logo of its own is drawn
#: the way any site without one is.
PHOTOGRAPHS: frozenset[str] = frozenset(
    {
        # babepedia.com is NOT on this list: its icons are photographs (see `NOT_ITS_MARK`), but its
        # own page draws a wordmark, which is in `PICKED`. It stays out of the site's own icons
        # either way.
        "harmony.fan",
        "latexlolanoir.com",
        "legendarylootz.com",
        "lifestylefemdom.com",
        # From the contact sheet: a posterised photograph of the site's model, and a drawn likeness
        # of the studio's namesake. A likeness of a real person is a face either way.
        "missvikkilynn.com",
        "rebeccalordproductions.com",
        "pornopedia.com",
        "theartofblowjob.com",
        "thehotmeangirl.com",
    }
)

#: Sites whose mark is a DRAWN FIGURE (a pin-up, a cartoon, a silhouette) rather than a mark
#: that says which site it is. Withheld exactly as `PHOTOGRAPHS` is (no picture ships; the tile
#: draws the site's first letter): a figure at 96 pixels reads as a picture of somebody, not as a
#: site.
MASCOTS: frozenset[str] = frozenset(
    {
        "corbinfisher.com",
        "diapergirls.com",
        "hotoldermale.com",
        "suicidegirls.com",
        "thebestporn.com",
        "vrlatina.com",
    }
)

#: WHY AN ENTRY IS WITHHELD, in the words the manifest records under `withheld`: the reason key and
#: the sentence a reader of the manifest gets. Three reasons, and each is decided here by a list,
#: never by looking at pixels at build time: a rule that could withhold a site could also quietly
#: stop withholding one.
WITHHELD_WHY: dict[str, str] = {
    "photograph": (
        "the site's own icon is a photograph or likeness of a real person, which this public "
        "repository does not ship; the site is drawn as its first letter"
    ),
    "mascot": (
        "the site's mark is a drawn figure rather than a mark that names the site; the site is "
        "drawn as its first letter"
    ),
    "link kind": (
        "a kind of link, not a site: there is no logo to ship, so a row called this is drawn as "
        "its first letter and a link as the plain link glyph"
    ),
}


def withheld_reason(hosts: Iterable[str], *, kind: bool = False) -> str | None:
    """The `WITHHELD_WHY` key an entry is withheld for, or None when its picture ships.

    Every host the entry claims is asked, not only the one it is filed under: a site that arrives
    under a second domain is the same site with the same picture. `kind` is the entry being one of
    `LINK_KINDS`, which have no host to go by.
    """
    if kind:
        return "link kind"
    claimed = set(hosts)
    if claimed & PHOTOGRAPHS:
        return "photograph"
    if claimed & MASCOTS:
        return "mascot"
    return None


#: SIMPLE ICONS SLUGS, by host, where the brand's title there is not the site's name here.
#:
#: Simple Icons (simpleicons.org, CC0) is the LAST vector source: a one-colour glyph, drawn here in
#: the brand colour Simple Icons records. A brand whose title matches the site's name exactly is
#: found without an entry; these are the ones spelled differently.
SIMPLE_ICONS: dict[str, str] = {
    "x.com": "x",
    "bsky.app": "bluesky",
    "discord.com": "discord",
    "onlyfans.com": "onlyfans",
    "reddit.com": "reddit",
    "linktr.ee": "linktree",
    "tumblr.com": "tumblr",
    "snapchat.com": "snapchat",
    "myspace.com": "myspace",
    "wikidata.org": "wikidata",
    "rumble.com": "rumble",
    "kick.com": "kick",
    "carrd.co": "carrd",
    "deviantart.com": "deviantart",
    "boosty.to": "boosty",
}

#: The Simple Icons release the glyphs are read from. PINNED, so a rebuild draws the same mark
#: until somebody moves this on purpose.
SIMPLE_ICONS_VERSION = "16.32.0"

#: LINK KINDS THAT ARE NOT SITES: `(slug, name, other names)`.
#:
#: StashDB, FansDB and PMVStash each file a person's own home page, a studio's page or an agency's
#: under a KIND rather than a site ("Home", "Official Website", "Studio"), and a Site row in a
#: library can end up called exactly that. There is no logo for a kind, so each is WITHHELD
#: (`WITHHELD_WHY["link kind"]`): the manifest knows the words, so a row called one of them is
#: recognised as a kind rather than matched to some site that happens to share the word, and no
#: picture ships. No hosts: a kind has no address. Found by name.
#:
#: No grey line glyph of what it is (a house, a globe, a chain link): a glyph is not a logo, and a
#: Sites tile wearing one reads as a site nobody has heard of. The client already draws the chain
#: link beside a link it has no logo for, so a copy of it in the pack would be a second answer.
LINK_KINDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("kind-home", "Home", ("Home page", "Homepage")),
    ("kind-official-website", "Official Website", ("Website", "Official Site", "Web site")),
    ("kind-studio", "Studio", ()),
    ("kind-studio-profile", "Studio Profile", ()),
    ("kind-modeling-agency", "Modeling Agency", ("Modelling Agency", "Agency")),
    ("kind-artist-website", "Artist website", ("Artist Website", "Artist site")),
    ("kind-link", "link", ("Link", "Links")),
    ("kind-other", "Other", ()),
)
