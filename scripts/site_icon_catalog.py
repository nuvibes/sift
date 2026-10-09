# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the site-icon build knows by hand; every picture entry was fetched and looked at."""

from __future__ import annotations

from collections.abc import Iterable

#: Hosts that are one site: the pack refuses two entries claiming one name.
HOST_JOINS: dict[str, str] = {
    "t.me": "telegram.org",
    "threads.net": "threads.com",
    "msha.ke": "milkshake.app",
    "justforfans.app": "justfor.fans",
    "legacy.peach.com": "peach.com",
    "web.archive.org": "archive.org",
}

#: Sites no crawl layer reaches: `(host, name, other names it answers to)`.
NAMED_SITES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("archive.org", "Internet Archive", ("Wayback Machine",)),
    # Sites a studio could claim as its home; named here so the studio never takes their logo.
    ("amarotic.com", "Amarotic", ()),
    ("aventertainments.com", "AV Entertainment", ()),
    ("hotmovies.com", "HotMovies", ()),
    ("pissvids.com", "PissVids", ()),
    ("pornbox.com", "Pornbox", ()),
    ("povr.com", "POVR", ()),
    ("r18.com", "R18", ()),
    ("vrporn.com", "VRPorn", ()),
)

#: What a site is called where the first box to name it named a link kind instead.
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

#: Words a library row may wear for a site, by host, where the boxes spell them differently.
ALIASES: dict[str, tuple[str, ...]] = {
    "discord.com": ("Discord", "Discord Server"),
    "reddit.com": ("Reddit", "Reddit User", "reddit user", "reddit post", "subreddit"),
    # `Twitter` is X's old name for the same site, so a row still called Twitter draws X's mark.
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

#: Vectors on Wikimedia Commons that are the brand's own mark; marks only, never a wordmark.
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
    # National film databases whose own mark is a flag.
    "bgafd.co.uk": "Flag of the United Kingdom.svg",
    "egafd.com": "Flag of Europe.svg",
}

#: Sites whose plate comes off whatever its colour, decided per brand by looking.
PLATE_OFF: frozenset[str] = frozenset({"camsoda.com"})

#: A site's own icons that are not its mark (another brand's, or a photograph), by host.
NOT_ITS_MARK: dict[str, str] = {
    "uviu.com": "its web manifest serves its network's mark, not the UViU wordmark",
    "babepedia.com": "its own icons are photographs of a person",
}

#: Pictures chosen by hand, tried first: `(address, what it is)`.
PICKED: dict[str, tuple[str, str]] = {
    "babepedia.com": (
        "https://www.babepedia.com/images/logo2020-trans.png",
        "the site's own wordmark, 200 x 100, from its page",
    ),
    "magic.ly": ("https://magic.ly/favicon.png", "the site's own 96 px mark"),
    "reddit.com": (
        "https://www.redditstatic.com/shreddit/assets/favicon/192x192.png",
        "the site's 192 px favicon, not declared",
    ),
}

#: Withheld: a real person's picture never ships here; such a site draws its first letter.
PHOTOGRAPHS: frozenset[str] = frozenset(
    {
        "harmony.fan",
        "latexlolanoir.com",
        "legendarylootz.com",
        "lifestylefemdom.com",
        "missvikkilynn.com",
        "rebeccalordproductions.com",
        "pornopedia.com",
        "theartofblowjob.com",
        "thehotmeangirl.com",
    }
)

#: Withheld like `PHOTOGRAPHS`: a drawn figure reads as somebody, not as a site.
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

#: Why an entry is withheld, decided by a list, never by pixels at build time.
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
    """The `WITHHELD_WHY` key an entry is withheld for under any of its hosts, or None."""
    if kind:
        return "link kind"
    claimed = set(hosts)
    if claimed & PHOTOGRAPHS:
        return "photograph"
    if claimed & MASCOTS:
        return "mascot"
    return None


#: Simple Icons slugs by host, where the brand's title there is not the site's name here.
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

#: Pinned, so a rebuild draws the same mark until somebody moves it on purpose.
SIMPLE_ICONS_VERSION = "16.32.0"

#: Link kinds that are not sites: withheld, recognised by name, and given no picture.
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
