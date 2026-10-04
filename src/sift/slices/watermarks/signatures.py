# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding whether a line of read text is a site's mark, and whose.

The reader hands back whatever it saw, which on a real frame is a caption, a timestamp, a logo's
letters and, sometimes, the thing this feature is for. This is the part that says which.

**A site address is matched by distance, not by equality, and the username beside it is not.** A
recogniser reading white-on-white letters at a steep angle gets a character wrong now and then, so
demanding `onlyfans.com` exactly would throw away marks a person can plainly read. But the same
tolerance applied to the USERNAME would attribute a file to the wrong username, and there is no
symmetry between those two mistakes: a site read one letter out is a site that does not exist and
is refused by the matcher; a username read one letter out is very often a DIFFERENT REAL PERSON.

**Only an exact address decides anything.** Most marks read exactly, and the fuzzy ones usually come
with a legible username, so the fuzzy reads are real marks and the temptation is to file on them
too. Two things say not to. The rule is that an exact read files the file and anything else is
recorded; and the two addresses this knows are close enough to each other's fragments to collide:
`fans.com`, which is what a corner crop makes of `onlyfans.com`, is two edits from `fansly.com`. A
rule that filed on distance two would file some OnlyFans marks under Fansly, silently, and the
record of what was actually read is a far better thing to show a person than a filing they have to
notice is wrong.

So a fuzzy read is kept as a READ and never as a filing. It shows on the file with the text that
was read, which is the one thing that makes it correctable.

**Four kinds of thing are recognised, and the KIND is what says what may be written.** A site's
own address says where this copy came off, which is a filing. The other three say it less
directly, and two of them are allowed to write less:

- a **notice**: `DMCA PROTECTED CONTENT`, the band a distributor lays over a copy it re-hosts.
  **It FILES the file, under the site in `NOTICES` beside the phrase, and writes no tag.** The
  band's letters name no site, but the band is a signature rather than an anonymous stamp, and the
  distributor that draws this one re-hosts OnlyFans material. So a copy wearing it came off
  OnlyFans, and a tag would record a fact nothing could act on when the fact is actionable: it is
  a filing: the site alone, no username, one decision per file that undoes like every other. See
  `NOTICES` for why the site is written down as data rather than compiled into the matcher, and
  for the honest size of the evidence.
- a **channel**: `t.me/<name>`, the address of a Telegram channel a copy was mirrored through. A
  channel is not a site Sift files under and its name is not a person's, so this is a tag.
- a **username**: a bare `@name` with no address anywhere on the frame. A
  username on its own does not say which site it belongs to, so it is attributed only where the
  LIBRARY answers that: the name must match a username exactly, and that username must exist on
  exactly one site.

**All three are exact or nothing, and that is not timidity.** The distance rule above exists
because an address read a letter out is still plainly that address and nothing else on earth. None
of the three has that property: `dmca protected content` misread is a phrase nobody wrote, a
channel name a letter out is a DIFFERENT channel, and a username a letter out was already refused
for the marks that carry an address beside them.

**A tag does not compete with a filing, so the two are read separately.** `best_mark` picks the ONE
reading kept against the file (there is one row per file, because a frame carries one mark) and
a site's address outranks everything, so the file that carries both an address and a distributor's
band shows the address. `tags_in` is asked of the same lines and answers with every channel found
on any of them: a mirror's address is a fact about the copy standing beside the filing rather than
a rival claim about where it came from, and a file may truthfully carry both.

**A notice is not one of those.** It files, and two filings for one copy is not a thing a filing
means, so when a frame carries both a band and an exact address, the address wins and the band
decides nothing. That is the right way round: the address was read off THIS copy and says where it
came off; the band says the same thing by inference from who draws it. Direct evidence over
inference, and where the address is `onlyfans.com` the two agree anyway.

**None of this costs a frame, a crop or a detection pass.** All four kinds are read out of the
lines the two crops already produced, so widening what is recognised is arithmetic over a few
hundred short strings and the per-file cost is the decoder's and the models', unchanged: matching
all four kinds is a small fraction of what the pass costs a file.

The three kinds beyond a site's address are rare. What matters is that they read cleanly when they
are there and that nothing else on a frame is mistaken for one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: What a mark IS, which is the whole of what decides what it may write.
#:
#: One word per thing, and these are the words `watermark_reads.kind` stores and the words the line
#: on a file's own screen is phrased from, so the column, the matcher and the sentence a person
#: reads cannot come to mean different things.
SITE = "site"
NOTICE = "notice"
CHANNEL = "channel"
USERNAME = "username"

#: The site addresses a mark can carry, and what Sift calls the site those marks came off.
#:
#: Two, and a third has to be seen on real files before it earns a line: the standard the
#: file-name reader is held to. Each is a full address because that is what the mark draws: a bare
#: site name would match half the captions in a library.
SIGNATURES: tuple[tuple[str, str], ...] = (
    ("OnlyFans", "onlyfans.com"),
    ("Fansly", "fansly.com"),
)

#: The distributor's bands: the words a person reads, the run of symbols a tidied line holds, and
#: **the site a copy wearing that band came off**.
#:
#: The first two are written down because tidying throws away the spacing and the case, and the
#: phrase is drawn on a frame in half a dozen spacings: one file can be read twice, as
#: `DMCAPROTECTEDCONTENT` and as `DMCA PROTECTEDCONTENT`, from the two crops. Matching the tidied
#: form covers every one of them and showing the written form is what a person can actually read
#: back.
#:
#: **The third is DATA on purpose.** Which site a band means is a property of the distributor that
#: draws it, not of the phrase: the same words drawn by somebody re-hosting something else would
#: mean somewhere else. Compiling `OnlyFans` into the matcher would make that a fact about DMCA
#: notices in general, which it is not. As a column it is a row to change and a row to add, and the
#: shape says out loud that the site belongs to the band rather than to the phrase.
#:
#: **What decides this is knowing which distributor lays this band and what it re-hosts**, not how
#: often the band is met: it is rare. A second band (a copyright line, an aggregator's own notice)
#: earns its row the way a site does: by being met on real files first, with its own site beside
#: it.
NOTICES: tuple[tuple[str, str, str], ...] = (
    ("DMCA PROTECTED CONTENT", "dmcaprotectedcontent", "OnlyFans"),
)

#: The address a mirrored copy carries, and it is deliberately matched WHOLE and in context.
#:
#: Three characters is short enough to fall inside ordinary words once the tidying has closed the
#: gaps up: `chat.me/x` contains `t.me/x` and is not a channel. So a reading of this has to start
#: the line or follow something that is not a letter or a digit. See `_channel_in`.
CHANNEL_HOST = "t.me"

#: What a kind that decides nothing about a site puts on the file, and the ONLY thing it puts there.
#:
#: The name is the tag a person sees on the file and on the tag wall, so it is written the way the
#: rest of the library writes a tag rather than the way the mark shouts it.
#:
#: **One entry, not two.** A notice files under a site instead and writes nothing here. A tag an
#: older reading wrote for one is taken back off the files that wear it by the v3 step in
#: `schema.py`, which is the only place that name still appears.
TAG_OF = {
    CHANNEL: "Telegram mirror",
}

#: What this module decided with, recorded on every reading it produces.
#:
#: Everything above and below it is the matcher: which addresses are known, which bands, what counts
#: as a channel, how far a read may sit from an address, how a username is picked out. A change to
#: any of it changes what the same letters mean, and without this there would be no way to ask
#: which readings were decided by the older rule, so an improvement to the matcher would re-decide
#: nothing and a library would keep its old answers for ever.
#:
#: Bumped whenever a change here would read the same text differently: a site added or removed, a
#: band added, the distance or the tail length moved, the username pattern widened. NOT bumped for a
#: comment, a rename, or anything that cannot change an answer.
#:
#: It is deliberately not the model's revision. That says what produced the LETTERS and lives in
#: `weights.REVISION`; this says what was made of them. A better matcher re-decides stored text and
#: opens no file, where a new model has to look at every frame again: two different jobs, so two
#: different numbers.
MATCHER_VERSION = 1

#: How far a window may sit from an address and still be called a reading of it. The publisher of
#: the models has nothing to say about this; a read one or two edits out still carries a plausible
#: username beside it, and only an exact read files anything (see the module docstring).
MAX_DISTANCE = 2

#: The symbols kept when a read line is tidied for matching. Everything else (spaces, quotes, the
#: marks a recogniser sprinkles into a blurred edge) is dropped rather than replaced, so a mark
#: read as `only fans .com` still matches.
_KEPT = re.compile(r"[^a-z0-9./_-]")

#: What a username is allowed to be made of, and how long it may run.
#:
#: Deliberately NOT the same set as the line above: a hyphen survives tidying because a URL can
#: carry one, and it is not part of a username on either site this knows. Widening it would let
#: the tail of a caption run into the username.
_USERNAME = re.compile(r"[a-z0-9_.]{3,30}")

#: A username drawn on its own, with the `@` a person writes in front of it.
#:
#: The same symbols `_USERNAME` allows, for the same reason, and read off the line BEFORE it is
#: tidied. See `_username_in` for why this one cannot share the tidied text.
_AT_USERNAME = re.compile(r"@([a-z0-9_.]{3,30})")

#: The shortest tail of an address that is taken as a reading of it with the front cut off.
#:
#: **The reason this rule exists.** A corner crop slicing the mark in half leaves `fans.com/` and a
#: legible username. Compared by edits alone that string sits FOUR from `onlyfans.com`, which it is
#: plainly a reading of, and TWO from `fansly.com`, which it is not: so a tolerant rule would
#: confidently assign OnlyFans marks to the wrong site. An exact eight-character tail belongs to one
#: address and not the other, which is far stronger evidence than two edits over the same length,
#: and that is why it is ranked ahead.
#:
#: Eight, because that is where the two addresses stop sharing a tail with room to spare (they
#: differ from five characters in) and because eight exact characters is the case seen. A
#: tail is only read as an address when a username follows it: a lone fragment in a caption is not
#: evidence of anything.
MIN_TAIL = 8

#: A username this short or shorter is attributed only on an exact match to one that already exists.
#: Reads of six characters or fewer are usually a corner crop cutting the mark in half rather than
#: short usernames, and a one-edit tolerance on a five-letter word reaches a great many real
#: words.
SHORT_USERNAME = 6


@dataclass(frozen=True, slots=True)
class Mark:
    """One mark found in one line of read text, and what it carried."""

    #: Which of the four this is, and so what it may write. See `SITE` and the three beside it.
    kind: str
    #: What Sift calls the site, and **the whole of what says this mark may be filed on**: a caller
    #: files under this and does nothing where it is None. A `SITE` takes it from `SIGNATURES` and a
    #: `NOTICE` from the third column of `NOTICES`: the band names no site in its letters but the
    #: distributor that draws it re-hosts one. None for a
    #: `CHANNEL`, which is not a site Sift files under, and for a `USERNAME`, which is a name with
    #: no site attached and is looked up in the library instead.
    site: str | None
    #: The address itself, as written in `SIGNATURES` rather than as read. None where the kind has
    #: no address: a notice is a phrase and a bare username is a name.
    host: str | None
    #: The username read after the address, or None where the mark carried none.
    username: str | None
    #: How many edits the read address sat from the real one. Zero is what files anything.
    distance: int
    #: What is shown to a person and kept on the file: the address and the username as READ.
    text: str
    #: Whether this was read as an address with its front cut off rather than as edits. Never
    #: exact, and ranked ahead of an edit reading that is no closer. See `MIN_TAIL`.
    truncated: bool = False

    @property
    def exact(self) -> bool:
        return self.distance == 0


def _tidied(line: str) -> tuple[str, set[int]]:
    """The tidied line, and where a GAP was closed up to make it.

    **Both halves are needed and they pull opposite ways.** The address has to be matched with the
    gaps taken out, because a recogniser reading a small mark puts them in where there are none:
    `only fans .com` is one of the commonest readings of the mark and it has to match. The USERNAME
    has to stop at a gap, because a username never contains one and the strip it was read from can
    hold more than the mark: two marks on the same row of a frame come back as one line, and
    without this the second one runs into the first one's username and the pair attributes nothing.

    So the gaps are closed AND remembered, which is the only way to have both.
    """
    kept: list[str] = []
    breaks: set[int] = set()
    gap = False
    for character in line.lower():
        if _KEPT.match(character):
            # A symbol no address is made of. Dropped, and the fact that something WAS here is
            # what the break records.
            gap = True
            continue
        if gap and kept:
            breaks.add(len(kept))
        gap = False
        kept.append(character)
    return "".join(kept), breaks


def distance_to(word: str, target: str, *, ceiling: int) -> int:
    """How many edits turn one short word into another, giving up once past a ceiling.

    The ceiling is what makes this cheap enough to ask of every username on a site: a read name is
    compared against every one of them, and the great majority differ in their first few letters.
    """
    if word == target:
        return 0
    if abs(len(word) - len(target)) > ceiling:
        return ceiling + 1
    previous = list(range(len(target) + 1))
    for index, left in enumerate(word, 1):
        row = [index]
        for column, right in enumerate(target, 1):
            row.append(
                min(
                    previous[column] + 1,
                    row[column - 1] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        if min(row) > ceiling:
            return ceiling + 1
        previous = row
    return previous[-1]


def _username_after(text: str, breaks: set[int], at: int) -> str | None:
    """The username written straight after the address, or None.

    Cut at the first gap the tidying closed up. See `_tidied`. A username is one run of symbols
    with nothing between them; anything past a space was something else on the same line.
    """
    if at >= len(text) or text[at] != "/":
        return None
    start = at + 1
    stop = min((where for where in breaks if where > start), default=len(text))
    found = _USERNAME.match(text[start:stop])
    if found is None:
        return None
    return found.group(0).strip(".") or None


def mark_in(line: str) -> Mark | None:
    """The best mark of any kind in one line of read text, or None where there is none.

    A site's address is looked for first and outranks everything. See `_rank` for the order and
    why it is that way round.
    """
    text, breaks = _tidied(line)
    best = _site_in(text, breaks)
    for found in (_notice_in(text), _channel_in(text, breaks), _username_in(line)):
        if found is not None and _better(found, best):
            best = found
    return best


def _site_in(text: str, breaks: set[int]) -> Mark | None:
    """The best site address in one tidied line, or None where there is none.

    Every window of about the right length is compared against every address, and the closest
    reading wins; a tie goes to the one that came with a username, because a mark with a username is
    the reading that carries information and a bare address is what a caption can accidentally be.
    """
    best: Mark | None = None
    for site, host in SIGNATURES:
        for length in range(len(host) - MAX_DISTANCE, len(host) + MAX_DISTANCE + 1):
            for start in range(0, max(0, len(text) - length) + 1):
                window = text[start : start + length]
                apart = distance_to(window, host, ceiling=MAX_DISTANCE)
                if apart > MAX_DISTANCE:
                    continue
                username = _username_after(text, breaks, start + length)
                found = Mark(
                    kind=SITE,
                    site=site,
                    host=host,
                    username=username,
                    distance=apart,
                    text=window if username is None else f"{window}/{username}",
                )
                if _better(found, best):
                    best = found
        if best is None or not best.exact:
            cut = _tail_in(text, breaks, site, host)
            if cut is not None and _better(cut, best):
                best = cut
    return best


#: Which kind of mark is the better thing to keep against a file, lowest first.
#:
#: **The KIND is compared before the distance, and that is the one ordering worth arguing with.** A
#: site's address read a letter or two out loses to nothing and beats an exact distributor's band,
#: even though the band is certain and the address is not.
#:
#: "Nothing is lost by ranking a notice second" is not the reason: a notice writes no tag, it files,
#: so losing the ranking is the whole of its effect being dropped (a channel does still tag through
#: `tags_in` whether or not it wins here).
#:
#: It is the right way round, for a reason that has to carry that weight on its own: the two
#: are not answers to the same question. An address is read off THIS copy and says where the copy
#: came off; a band says the same thing by inference from the distributor who draws it. Direct
#: evidence outranks inference, and a copy is filed under one site rather than two, so the frame
#: carrying an exact `onlyfans.com` and a band is filed once, under OnlyFans, which is what both of
#: them said anyway.
#:
#: A bare username is last because "alone" is the whole of what makes it evidence: `@name` beside
#: an address is that address's username, and `@name` beside nothing is the only case this kind
#: covers.
_KIND_ORDER = {SITE: 0, NOTICE: 1, CHANNEL: 2, USERNAME: 3}


def _rank(mark: Mark) -> tuple[int, int, int, int]:
    """How good a reading is, lowest first: the kind, then closer, then a cut-off tail over
    arbitrary edits, then one that came with a username."""
    return (
        _KIND_ORDER[mark.kind],
        mark.distance,
        0 if mark.truncated else 1,
        0 if mark.username else 1,
    )


def _better(found: Mark, best: Mark | None) -> bool:
    return best is None or _rank(found) < _rank(best)


def _tail_in(text: str, breaks: set[int], site: str, host: str) -> Mark | None:
    """The address read with its front cut off, or None. Longest tail wins."""
    for length in range(len(host) - 1, MIN_TAIL - 1, -1):
        tail = host[-length:]
        at = text.find(tail)
        while at != -1:
            username = _username_after(text, breaks, at + length)
            if username is not None:
                return Mark(
                    kind=SITE,
                    site=site,
                    host=host,
                    username=username,
                    distance=MAX_DISTANCE,
                    text=f"{tail}/{username}",
                    truncated=True,
                )
            at = text.find(tail, at + 1)
    return None


def _notice_in(text: str) -> Mark | None:
    """A distributor's band in one tidied line, or None.

    A plain containment test and no tolerance at all. The phrase is twenty characters long with the
    spacing already taken out, so a reading of it either is the phrase or is not one, and unlike
    an address there is no second phrase nearby for a near-miss to be confused with, which is what
    the distance rule was bought to solve.

    The site comes back on the mark, and `host` does not: the band carries a site the way a
    signature carries a name, not the way an address does. There is nothing on the picture to show
    a person as the address that was read, so the text stays the phrase itself.
    """
    for written, tidied, site in NOTICES:
        if tidied in text:
            return Mark(kind=NOTICE, site=site, host=None, username=None, distance=0, text=written)
    return None


def _channel_in(text: str, breaks: set[int]) -> Mark | None:
    """A Telegram channel's address in one tidied line, or None.

    The channel's name is read the way a site's username is, by `_username_after`, so one rule decides
    what a name may contain and where it stops. Without a name after it there is no mark: a bare
    fragment is evidence of nothing, exactly as `MIN_TAIL` says of a cut-off address.

    **And the address has to START something, which takes all three of the tests below.** `t.me` is
    three characters and lands inside ordinary words once the gaps are closed up: `chat.me/x` holds
    it and is not a channel. So a reading of it has to begin the line, or follow a symbol no
    address is made of (`https://t.me/x`), or sit where the tidying CLOSED A GAP, and that last
    one is not an extra: `visit t.me/x` becomes `visitt.me/x`, where the character in front of the
    mark is a letter belonging to the word before it. Without the break it is thrown away, and a
    caption with the mark at the end of it is the ordinary case.
    """
    at = text.find(CHANNEL_HOST)
    while at != -1:
        if at == 0 or at in breaks or not text[at - 1].isalnum():
            username = _username_after(text, breaks, at + len(CHANNEL_HOST))
            if username is not None:
                return Mark(
                    kind=CHANNEL,
                    site=None,
                    host=CHANNEL_HOST,
                    username=username,
                    distance=0,
                    text=f"{CHANNEL_HOST}/{username}",
                )
        at = text.find(CHANNEL_HOST, at + 1)
    return None


def _username_in(line: str) -> Mark | None:
    """A bare `@name` in one line as it was READ, or None.

    **Read off the raw line rather than the tidied one, and that is not an oversight.** `@` is not
    a symbol any address is made of, so tidying drops it, and it has to keep dropping it, because
    a mark drawn as `onlyfans.com/@name` is read correctly today only because the `@` disappears
    before `_username_after` looks for the name. Putting `@` into `_KEPT` to find this kind would
    break that one, so this kind gets its own look at the line instead.

    The first one wins: a band carrying two usernames and no address is two claims about the copy
    and neither is corroborated, and the ranking makes sure this only decides anything when the
    frame carried nothing better.
    """
    found = _AT_USERNAME.search(line.lower())
    if found is None:
        return None
    username = found.group(1).strip(".")
    if len(username) < 3:
        return None
    return Mark(
        kind=USERNAME, site=None, host=None, username=username, distance=0, text=f"@{username}"
    )


def best_mark(lines: list[str]) -> Mark | None:
    """The best mark across every line read from one file."""
    best: Mark | None = None
    for line in lines:
        found = mark_in(line)
        if found is not None and _better(found, best):
            best = found
    return best


def tags_in(lines: list[str]) -> list[str]:
    """Every tag the marks on one file's frame ask for, in the order `TAG_OF` declares them.

    Asked separately from `best_mark` because a tag is not a rival claim. See the note at the top
    of this file. A copy can carry a mirror's address AND the address it was taken from, and the
    honest record of it says both: the site in the one reading kept against the file, the mirror as
    a tag beside it.

    **Only a channel is asked for.** A notice files under a site instead, through the one reading
    `best_mark` keeps, so asking for it here would be asking a second time for something already
    decided. The answer is still built through `TAG_OF` rather than returning the one name directly:
    `TAG_OF` is what says which kinds tag, a list of one is the shape every caller already handles,
    and a second tagging kind is a row there rather than a rewrite here.
    """
    kinds = set()
    for line in lines:
        text, breaks = _tidied(line)
        found = _channel_in(text, breaks)
        if found is not None:
            kinds.add(found.kind)
    return [TAG_OF[kind] for kind in TAG_OF if kind in kinds]


def nearest_username(name: str, known: list[str]) -> str | None:
    """The username on this site the read name points at, or None where there is none.

    Exact first, then one edit, and one edit only for a name long enough for that to mean something.
    See `SHORT_USERNAME`. Nothing further: a read two edits from a username is as likely to be a
    different username as a misreading of that one, and a string with nothing certain to attach to
    is recorded rather than acted on.
    """
    folded = name.lower()
    for candidate in known:
        if candidate.lower() == folded:
            return candidate
    if len(folded) <= SHORT_USERNAME:
        return None
    nearest: str | None = None
    for candidate in known:
        if distance_to(folded, candidate.lower(), ceiling=1) <= 1:
            if nearest is not None:
                # Two usernames one edit away is not an attribution, it is a coin toss. Neither.
                return None
            nearest = candidate
    return nearest
