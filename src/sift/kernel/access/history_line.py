# SPDX-License-Identifier: AGPL-3.0-or-later
"""One History line: what it says, who did it, what it names, and where it sits in a thread.

Every thread (a file's, a person's, an entity's) is built from these shapes and put in order by
`ordered`, so a line means the same thing on every page that draws it and a client draws its mark
from one closed list of kinds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.access import sentences as say
from sift.kernel.access.sentences import Line, Piece


class Actor(StrEnum):
    """Who did a thing, as far as the row that recorded it can say.

    Five, and the fifth is the honest one. `SOMEBODY` is a person, and the row does not say which:
    a tag put on a file, a person named in it, a share made on it. The tables record what was
    decided and not who decided it, and answering "You" would be a guess that is wrong on any
    install with more than one user. It is not a placeholder for a missing column; it is what
    those rows actually know.

    `SIFT` is a pass that ran on its own. `STASH_BOX` is a named service somewhere else, which is
    kept apart from `SIFT` for the reason the enriched marks are: "a stash-box did this" and "Sift
    worked this out" are different claims, and only one of them can be checked by asking somebody
    else.
    """

    YOU = "you"
    ANOTHER_USER = "another_user"
    SOMEBODY = "somebody"
    SIFT = "sift"
    STASH_BOX = "stash_box"


@dataclass(frozen=True, slots=True)
class Undo:
    """What would take an event back, as the kind of thing it is and its id.

    Two fields rather than one id, because there are two different doors (a move is undone
    through the organizer, a decision through the workbench), and a client holding only an id
    would have to work out which from the event's kind. That is a second mapping to keep in step
    with this one, in a language that cannot check it.
    """

    #: `move` or `decision`. Both doors are live; see the module docstring of `history`.
    kind: str
    id: str


#: What a sentence can NAME, as a closed list of the kinds that have somewhere to go.
#:
#: Closed and small, for the reason the decision subjects are: a client draws a link from this word,
#: and two reads writing `photo_set` and `photoset` for the same thing would be a link that quietly
#: goes nowhere on one screen and works on another.
#:
#: `asset` is one of them. Naming a file from an unscoped read would say it exists to somebody who
#: may not be shown it, so `history_of_asset` is handed the access layer and resolves each file it
#: is about to name through it, exactly as `made_from` and `produced_for` in the editing feature do:
#: a file this user may not be shown is left out of the sentence rather than named in it.
#:
#: `face_pile` is the eighth and is the same kind of address a folder is: a group of faces Sift has
#: not put a name to has no entity page, it has a place in the organizer, and that place is what
#: somebody wants when a history says a face was found here and does not say whose.
LINK_KINDS = (
    # THE TWO THAT ARE NOT THINGS BUT SETS. A number in a
    # sentence ("4,000 files", "300 faces") names no row anywhere, and it is the run of the
    # sentence somebody most wants to press: it is the only way to see what was counted. So it is a
    # link like any other, with `href` carrying the filtered wall it stands for and `id` naming the
    # subject the count is about. A client that has no drawing for these two reads the same
    # sentence with the number as plain words, which is what every unknown kind does.
    "files",
    "faces",
    # A FIELD OF THE RECORD, for the fold under a box's line: "FansDB filled in 10
    # details" opens to the ten, named. A field has no page, so a client draws it as a chip with its
    # name and nothing to press, which is what every kind without a page already is. See
    # `box_filled_in`.
    "field",
    "person",
    "site",
    "tag",
    "collection",
    "photo_set",
    # ONE PIECE OF MUSIC: its page is the Music page's row, `/songs/<id>`.
    "song",
    "folder",
    "asset",
    "face_pile",
    # A USERNAME: one person's name on one site. It has NO page of its own: a line naming one goes
    # to its person, or where nobody is said for it, to the files posted under it
    # (`sentences.username_opens`, the one rule every link to a username is built by). A client
    # drawing a link of this kind with no address on it falls back to the second, which exists for
    # every username.
    #
    # !! IT IS DELIBERATELY NOT IN `sentences.LINKED_KINDS`, which is the table that lets a LEDGER
    # line draw a link by kind alone. Its address depends on whether a person is said for it, which
    # the kind cannot say, so it is linkable by a read that looks that up (the ledger router's
    # `_username_hrefs`) and by nothing else.
    "username",
    # A ROW ON THE DOWNLOADS QUEUE: a download that never landed produced no file, so the row is
    # what a line names, and its place is the queue with that row picked out.
    "download",
)


@dataclass(frozen=True, slots=True)
class Link:
    """One thing a line names, and where it lives: as a detail group's entry, a receipt's declared
    way somewhere, and the read-off view of a line's named pieces (`Event.links`).

    A client never looks for these inside a sentence's words: that search would link every "d"
    in "Added to d". A line is `sentences.Piece`s, each thing placed where it sits by its builder; a
    `Link` is what a thing is when it is listed rather than said.
    """

    #: One of `LINK_KINDS`.
    kind: str
    #: Whatever that kind's page is addressed by: its id, a folder's included (`in:` takes a
    #: folder's id, and a path names nothing for a library folder's own, the empty path).
    id: str
    #: Exactly as it appears in `what`.
    name: str
    #: WHERE THIS ONE GOES, where the kind's own page is not the answer.
    #:
    #: Empty on nearly every link, and that is the ordinary case: a person's name goes to the
    #: person's page, which the client already knows how to build from the kind and the id. What
    #: needs this is a NUMBER ("4,000 files", "300 faces"), which is not a thing with a page but
    #: a SET, and the only place that knows which set it counted is the read that counted it.
    #:
    #: A screen in the client rather than an address of the API's, the same space a workbench
    #: queue's `opens` lives in and for the reason written there. `sentences.py` is the one place
    #: that spells them; nothing else in the kernel builds a path.
    href: str | None = None
    #: THE THING THIS NAMES HAS GONE, so the words are drawn and never linked.
    #:
    #: Not the same answer as an empty `href`, which means the opposite: that the kind's own page
    #: IS where this goes, and the client builds that address from the kind and the id. A deleted
    #: file needs a third answer, because its kind HAS a page and this one is not on it: a link
    #: there lands on "no such file", which reads as a broken screen rather than as a library that
    #: has moved on. Struck through and plain, the same rule a download whose file has gone follows.
    gone: bool = False


@dataclass(frozen=True, slots=True)
class Detail:
    """ONE GROUP OF THINGS A FOLDED LINE STANDS FOR, and the words the sentence counted them in.

    A line that stands for a whole press says how MANY ("StashDB recognized this file and wrote 19
    people, the site and 7 tags"), and the things themselves are listed under it for the row to
    open to. Listed as one column they are the count made useless again: twenty-six names with
    nothing saying which of them are the people and which the tags. So they arrive already cut the
    way the sentence cut them, one group per phrase, in the sentence's order.

    `words` IS THE PHRASE and not a second way of saying it. It is the same string the sentence was
    built from (see `_by_the_box`, where the line and the groups come out of one loop), so a
    heading over a group and the count in the line above it cannot come to disagree. A client
    composing "19 people" for itself, out of the group's length and a noun of its own, would be the
    second vocabulary the sentences exist to avoid; and the one it would already differ on is a
    filing, which the fold counts as "the site" where the walls call that row a Site.

    A GROUP WITH NOTHING TO LIST IS NOT ONE. A filing whose site has been deleted is still an act
    the sentence counts, and it has no page to offer (`usernames.site_id` is `ON DELETE SET NULL`),
    so the phrase stays in the line and no group is made for it.
    """

    #: One of `LINK_KINDS`. Every link in the group wears it: a group is one ACT, and an act writes
    #: one kind of row.
    kind: str
    #: Exactly as it appears in `what`: "19 people", "the site", "7 tags".
    words: str
    #: The things themselves, in the order they were written. Never empty; see the docstring.
    links: tuple[Link, ...]


#: One kept answer to a stash-box's disagreement: the record's kind, its id, the box and the field:
#: the key of one `stash_box_kept` row, and of one `vocabulary.RECEIPT_KEPT` entry.
KeptAnswer = tuple[str, str, str, str]

#: One face somebody answered for: the person, the file the face is in, and the answer
#: (`vocabulary.FACE_SAID_YES` or `FACE_SAID_NO`): the key of one `vocabulary.RECEIPT_FACES`
#: entry, and of one face a face's own line counts. See `one_line_per_face_answer`.
FaceAnswer = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened to the subject, said in the app's own voice.

    `what` is a finished sentence rather than a template with holes in it. It is written here, once
    per kind, because the words are the same words the rest of Sift uses for the same act, and a
    client assembling them would be a second vocabulary, drifting quietly out of step with the
    first the day somebody renames something.

    IT CARRIES NO FULL STOP. These are rows in a thread, one fact each, next to a time and a name
    (the register of a caption rather than of prose), and a column of stops down the right of short
    lines is punctuation doing no work.

    `at` is seconds since the epoch, and None means the row predates the column that records the
    moment. Drawn as "before this was recorded" rather than as a date, and sorted oldest, because
    inventing a time for it would put a made-up date on a screen somebody is reading to find out
    what really happened.
    """

    at: int | None
    actor: Actor
    #: The user, or the stash-box. None when there is nothing to name, and None for another
    #: user when the viewer is not an admin (see `_Who`).
    actor_name: str | None
    #: A small closed vocabulary; see `KINDS`.
    kind: str
    #: THE LINE, as pieces: plain words and the things they name, each where it sits. `what` and
    #: `links` below are read off these, so there is one line and two ways of looking at it.
    pieces: Line
    undo: Undo | None = None
    #: Whether this has already been taken back. An event that was reversed keeps its place in the
    #: order: a history that quietly loses its reversals reads as though nothing ever happened.
    reversed: bool = False
    #: THE THINGS THIS ONE LINE STANDS FOR, where it stands for more than one act.
    #:
    #: Empty on every line that is only itself. A line that folded a source's whole press (the
    #: seventeen people a stash-box named in one go) says how MANY in its sentence and lists them
    #: here, so the pane stays short and nothing is lost: a row with a `detail` opens.
    #:
    #: GROUPED, one per phrase the sentence counted, rather than run together into a single list.
    #: See `Detail`: a box that wrote people, a site and tags in one press is three groups, and a
    #: flat list of all of them is a column of names with nothing saying which is which.
    #:
    #: Links rather than names, and that is the difference from `links` beside it. `links` is what
    #: the SENTENCE names, and a client finds each one by looking for its name in `what`, so a
    #: name that is not in the sentence cannot go in there without that search being able to match
    #: it somewhere it does not belong. These are drawn as a list of their own and never searched
    #: for, so a tag called `file` cannot come to underline a word in the sentence above it.
    detail: tuple[Detail, ...] = ()
    #: WHAT did it, in the `enriched:` filter's own four words (`stash`, `faces`, `folder` or
    #: `filename`), and None where the row cannot say.
    #:
    #: It exists because the KIND cannot answer it. `named`, `tagged` and `filed` each happen three
    #: ways, and the actor only separates a stash-box from Sift: a folder read and a face match are
    #: both `SIFT`, and they are the two a person most wants told apart. The words are the filter's
    #: rather than new ones, so a mark on a row and a row in the Browse column cannot disagree about
    #: what they mean.
    via: str | None = None
    #: WHICH VERB MADE A COPY (`trim`, `compress`, `gif` and the rest of `_COPY_VERBS`), exactly
    #: as `produced_files.operation` spells it, and None on every kind that is not a copy. One
    #: other line carries it: the arrival of a tag Sift made for a copy (`via` `produced`), where it
    #: is the act that made the copy as `tags.created_by_act` spells it (`compress`, `edit`).
    #:
    #: A field of its own rather than a second meaning for `via`, which answers "which of the four
    #: enrichment passes did this" in the `enriched:` filter's own closed vocabulary. The two
    #: questions are different questions: one names a PASS, one names a VERB, and a client reading
    #: one word for both would draw the filter's mark for a copy the day somebody adds an enrichment
    #: pass that shares a name. The sentence already says the verb in words; this is the same word
    #: unbroken, so a mark can be drawn from it without reading English.
    how: str | None = None
    #: WHETHER SOMEBODY PRESSED IT, on the one kind where that is a separate fact: an enrichment.
    #:
    #: True where a person agreed to an answer, False where a run nobody was watching wrote it, and
    #: None where the row does not say: every enrichment applied before Sift wrote this down.
    #:
    #: A field of its own rather than a reading of `actor`, and the reason is that the actor is
    #: already right and already says something else: a stash-box wrote the fields either way, so
    #: `STASH_BOX` is the honest answer to who, and "did I do this or did the machine" is a second
    #: question with a second answer. Nothing else in this database records it (an applied match
    #: and a hand-confirmed one leave identical rows), which is why it is stored rather than
    #: inferred. See `enrichment_runs`.
    by_hand: bool | None = None
    #: WHICH FIELDS A STASH-BOX FILLED IN, in the words somebody reads.
    #:
    #: Empty on every line but an `enriched` one, and empty on an `enriched` line whose run predates
    #: version 52 of the catalog. NOT on the wire: like `by_hand` beside it, this is carried from
    #: the read to the fold, where it becomes part of the sentence the fold writes. See
    #: `_by_the_box`.
    #:
    #: These are the fields the file's own rows CANNOT account for: its title, its details, the
    #: dates. The people, the tags and the filing are counted off the rows themselves, which is a
    #: better source than any stored list: a row carries the box's word and can be counted at the
    #: moment somebody reads it, while a list written at the time can only say what was true then.
    #: So the sentence takes each half from wherever it is truest; see the module header of
    #: `history_folds`.
    wrote: tuple[str, ...] = ()
    #: HOW SURE SIFT WAS OF A STASH-BOX'S MATCH (`certain`, `likely` or `unsure`, its own reading,
    #: stored beside the match in `asset_stash_box_matches.grade`), on an `enriched` line only.
    #: Carried to the fold, which says it in the line (`sentences.recognized`).
    grade: str | None = None
    #: WHERE THE THING A LINE IS ABOUT LIVES OUTSIDE SIFT, as the words of the way there and the
    #: address: "Open on PMVStash" and the box's own page for the scene it matched. None on every
    #: line with nowhere outside to go, and on a box whose pages Sift does not know
    #: (`kernel.urls.scene_page`).
    away: tuple[str, str] | None = None
    #: HOUSEKEEPING Sift does to every file that arrives: its pictures generated, its meaning
    #: indexed for Smart Search, a look for a watermark that found none, a stash-box that had never
    #: heard of it. Not on the wire: the file's tab folds each sitting of these into one line, "Sift
    #: processed this file", that opens to each step (`_one_processed_line`).
    routine: bool = False
    #: WHETHER THIS DOWNLOAD BROUGHT THE FILE IN, on a `downloaded` line. Not on the wire. Its line
    #: then names the file's arrival too, and the arrival is not drawn again
    #: (`_one_line_per_download`).
    landed: bool = False
    #: THE RECEIPT THIS LINE BELONGS TO, where the record says one does.
    #:
    #: A decision carries its OWN id here, and an attribution written by that same press carries the
    #: same id, so "these two lines are one act" is a fact read off the ledger rather than a
    #: comparison of two English sentences. See `_one_line_per_act`, which is the whole reason it
    #: exists, and `_receipt_of_object`, which is how a link row finds its receipt.
    #:
    #: Null on every line nothing can be taken back for, and null on an attribution whose receipt
    #: was written before its area went through the ledger: those rows carry no verb and no object,
    #: so there is nothing to match them on and the two lines both stand. That is the honest
    #: failure: a fold that fires on half of them would make the pane look arbitrary instead of
    #: repetitive.
    #:
    #: On the wire as well, deliberately: a client that wants to show a line and its Undo together,
    #: or to group a press, reads an id instead of comparing sentences.
    receipt: str | None = None
    #: WHEN A LINE THAT STANDS FOR A RUN BEGAN, where it stands for one.
    #:
    #: `at` is the LAST of the run, which is where the line sorts and what "when" has always meant
    #: on a row. A day's downloads folded into one line happened across the day, and the row says
    #: that span ("2:02 PM to 6:40 PM") rather than a single moment that was only the last of
    #: them. None on every line that is one act, which is nearly all of them.
    since: int | None = None
    #: WHICH STASH-BOX this line is, by its row, on a box's `enriched` line and None on every other.
    #: NOT on the wire: the box is named in words and has no page. Carried so the link table's
    #: line and the ledger's presses are matched by which box they are, not by what it is called;
    #: see `runs_not_drawn`.
    box_id: str | None = None
    #: THE ROW'S OWN SOURCE WORD on a naming, a tagging or a filing (`asset_people.source` and its
    #: siblings), and None everywhere else. NOT on the wire. Carried so a press is grouped, and
    #: said, by the word the row stores rather than by the filter's `via`, which has no word for a
    #: username or a copy. See `_said_by`.
    source: str | None = None
    #: THE STASH-BOX ANSWERS A RECEIPT KEPT, on a decision's line: each the `KeptAnswer` it names
    #: and how (`vocabulary.KEPT_BY_THE_PRESS` or `KEPT_BY_TAKING_ANOTHER`), read off the key the
    #: writer declares (`vocabulary.RECEIPT_KEPT`). NOT on the wire. See `one_line_per_kept`.
    kept: tuple[tuple[KeptAnswer, str], ...] = ()
    #: WHICH KEPT ANSWER a `kept_mine` line is, and None on every other. NOT on the wire. Carried
    #: so the line meets the receipt that recorded the same press. See `one_line_per_kept`.
    answer: KeptAnswer | None = None
    #: THE FACES A LINE IS ABOUT, each a `FaceAnswer`: on a receipt, the faces it declares it
    #: answered (`vocabulary.RECEIPT_FACES`); on a face's own line ("A face here was agreed to be
    #: ...", "3 faces were confirmed as them"), the faces that line counts. NOT on the wire. See
    #: `one_line_per_face_answer`.
    faces: tuple[FaceAnswer, ...] = ()
    #: WHETHER A DECISION'S LINE IS ITS STORED TITLE, as against the words its area put together
    #: from what it recorded (`worded.decided_said`). NOT on the wire. See `one_line_per_kept`.
    stored_words: bool = False
    #: What else a decision wrote that its line does not say, drawn under the line ("A new person
    #: added, 1 other folder with that name answered."). Empty on every other line.
    more: str = ""

    @property
    def what(self) -> str:
        """The line as words, shut: every piece's text in order."""
        return say.text_of(self.pieces)

    @property
    def links(self) -> tuple[Link, ...]:
        """Every thing the line names, as links, in the order the line names them."""
        return tuple(link_of_piece(one) for one in say.things_in(self.pieces))


def link_of_piece(one: Piece) -> Link:
    """A named piece as the `Link` a detail group or a fold carries."""
    return Link(kind=one.kind or "", id=one.id or "", name=one.text, href=one.href, gone=one.gone)


def piece_of(link: Link) -> Piece:
    """A `Link` as the piece a line places where the thing sits."""
    return say.thing(link.kind, link.id, link.name, href=link.href, gone=link.gone)


def by_of(actor: Actor, name: str | None) -> str | None:
    """Who did it, as the first word of a line: see `sentences.by_word`."""
    return say.by_word(actor.value, name)


#: The four words the `enriched:` filter takes, which are the four a History line answers `via`
#: with.
#:
#: Written down beside `KINDS` for the same reason: a screen draws a mark from one of them, and a
#: word appearing in a reply that nothing drew is how a mark comes to be missing on one row and not
#: on another. They are the filter's own words rather than a set invented here (see
#: `repository/assets.py`, where the same four are what `enriched:` matches on).
VIAS = (
    "stash",
    "faces",
    "folder",
    "filename",
    "metadata",
    "watermark",
    "facial_fingerprints",
)


#: Every kind an event can be, which is the whole vocabulary a client draws marks for.
#:
#: Written down rather than left implicit in the reads, so a screen can be checked against it
#: and so adding one is a deliberate act with a mark to choose rather than a string appearing in a
#: response nobody drew.
KINDS = (
    "added",
    "moved",
    "renamed",
    "undone",
    "decided",
    "named",
    "tagged",
    "filed",
    "enriched",
    # The other half: a box was asked and had never heard of this file. Its own kind rather than an
    # `enriched` with an empty sentence, because what it reports is the opposite outcome and a pane
    # that drew both under one mark would say a box recognised a file it has never seen. See
    # `_ASKED`.
    # The same kind for the other answers a service somebody else runs can give that wrote nothing to
    # the file: a stash-box match still waiting to be checked, and AcoustID asked about its music.
    "asked",
    "face_run",
    # The third face act, beside the two a PERSON takes. `confirmed` and `rejected` are answers
    # somebody gave; this is the one Sift reached on its own, above the line at which an appearance
    # is attached without being asked. Its own kind rather than a `named` carrying `via="faces"`;
    # see `face_matches_of_asset` for why.
    "matched",
    "confirmed",
    "rejected",
    "copied_from",
    # The same row read from the other end: what was made OUT of this file. Said here rather than
    # as a strip of its own under the player, which would ask two more requests per file for one
    # line of provenance. A history is where "what happened to this" is answered; a second surface
    # answering half of it is the copy that drifts.
    "copied_into",
    "shared",
    # Seven sources the tables record. Each is its own kind rather than a shade of an existing one,
    # for the reason `asked` is: a client draws a mark from this word, and two acts under one mark
    # is a mark that says something untrue about one of them.
    #: A watermark was read off the picture, or looked for and not found, or refused.
    "watermark",
    #: Sift fetched the file from somewhere.
    "downloaded",
    #: Sift built what it needs to show the file, or read what it is about.
    "ready",
    #: A pass gave up on the file: a picture it could not generate, a look it could not take. Its own
    #: kind for the rule this list keeps: the mark `ready` wears says the work was done.
    "left_out",
    #: An appearance was taken off the file, or set aside.
    "face_off",
    #: Somebody said a person is not in this file, for good.
    "ruled_out",
    #: The file is concealed from the user reading this.
    "hidden",
    #: A stash-box disagreed about a field and the answer was to keep what was here.
    "kept_mine",
    #: What Sift has learnt to recognize somebody by. A PERSON's thread only (see
    #: `history_person.py`, where it is read).
    "taught",
    # THE EIGHT THE EVENT LEDGER RECORDS, each an act that leaves no other row: a link taken off, a
    # field edited, a rename, a delete, a merge, a refusal to let something leave the machine and
    # the taking back of one, a fingerprint pass, and the moment something was shown again. Their
    # own kinds for the reason `asked` has one: a client draws a mark from this word, and two acts
    # under one mark is a mark that says something untrue about one of them: "Hidden" and "Shown
    # again" are opposite outcomes of one switch.
    #: A person, a tag, a site, a shelf or a Photo Set taken off something.
    "removed",
    #: Something concealed and then shown again.
    "revealed",
    #: A record field, a note or a setting written.
    "edited",
    #: Something removed from the library for good.
    "deleted",
    #: Two people or two sites folded into one.
    "merged",
    #: A refusal to let a file or an entity be sent outside the machine, and the taking back of one.
    "kept_local",
    "allowed",
    #: "Do not swap" put on a file or an entity, and taken off.
    "kept_from_swaps",
    "allowed_in_swaps",
    #: Sift read the file and wrote down what it looks like.
    "scanned",
    #: A DOWNLOAD THAT GAVE UP. Its own kind rather than a shade of `downloaded`, for the rule this
    #: list keeps: a client draws a mark from this
    #: word, and a failure wearing the arrow that means "Sift fetched this" is a mark saying
    #: something untrue. The one that succeeded already has `downloaded` above.
    "download_failed",
    #: A COPY SAVED TO SOMEBODY'S DEVICE: the `saved` event
    #: `browse.record_save` writes. Its own kind because no other mark says it: `downloaded` is
    #: Sift fetching a file IN, and this is a copy going OUT.
    "saved",
    #: A THEATER WALL SENT to another of the same person's devices: the `wall_sent` event older
    #: lines carry, one per send, naming the files that were in the cells; the sends are retired.
    "wall_sent",
    # SEVEN MORE LEDGER ACTS WITH MARKS OF THEIR OWN. Without them each would fall through
    # `_EVENT_KINDS` to `decided` and wear the Organize tray with "Settled at the workbench, and it
    # can be taken back": a pause, a cancel and a finished task are none of those things.
    #: A download stopped for now, and started again.
    "paused",
    "resumed",
    #: The three things somebody does with a Site's cookies.
    "cookies_saved",
    "cookies_replaced",
    "cookies_forgotten",
    #: A download stopped for good by a person.
    "canceled",
    #: A task over the library that finished.
    "ran",
    #: A PASS SOMEBODY PRESSED OVER ONE FILE that no pass line says: an older look whose answer
    #: a newer one replaced, or one that found the work already done (`kernel.presses`).
    "pressed",
    #: A SONG SIFT NAMED: from the page a file was downloaded from, from AcoustID's answer to its
    #: fingerprint, or from another file that shares its song (the spread).
    "song_named",
    #: A SWAP WITH ANOTHER SIFT began and ended: two kinds, because they are the two ends of one
    #: session and a mark that said "a swap" on both would not say which. Feed-only: the session
    #: has no page, and the files it imported say so on their own arrival line (`added_by`).
    "swap_started",
    "swap_ended",
    #: A BACKUP RESTORED as this library: the `restored` event `backup.service` writes. Feed-only:
    #: the backup file has no page.
    "restored",
    #: A LIBRARY CREATED FROM A SIFT DATABASE FILE on the Database Switcher: the `adopted` event
    #: `backup.libraries` writes as the new library's first line. Feed-only: the file has no page.
    "adopted",
    #: AN ACT ON THE COMPUTER RUNNING SIFT asked from a window (`kernel/machine_acts.py`), one kind
    #: per act and per end of a switch, each with its own mark. Feed-only: the computer has no page.
    "sharing_turned_on",
    "sharing_turned_off",
    "start_with_windows_on",
    "start_with_windows_off",
    "firewall_opened",
    "storage_moved",
    "update_started",
    "library_opened",
    "restarted",
)

#: WHICH OF TWO ACTS IN THE SAME SECOND CAME FIRST: the cause before what it caused. Every kind has
#: a place here, so a new kind is placed on purpose rather than falling to the end.
CAUSE_ORDER: tuple[tuple[str, ...], ...] = (
    ("downloaded", "download_failed", "paused", "resumed", "canceled", "swap_started"),
    ("added", "copied_from"),
    ("filed",),
    ("renamed", "moved"),
    (
        "named",
        "tagged",
        "song_named",
        "decided",
        "edited",
        "removed",
        "merged",
        "cookies_saved",
        "cookies_replaced",
        "cookies_forgotten",
    ),
    ("ready", "left_out", "watermark", "scanned", "ran", "pressed", "restored", "adopted"),
    (
        "sharing_turned_on",
        "sharing_turned_off",
        "start_with_windows_on",
        "start_with_windows_off",
        "firewall_opened",
        "storage_moved",
        "update_started",
        "library_opened",
        "restarted",
    ),
    ("asked", "enriched", "kept_mine"),
    ("face_run",),
    ("matched",),
    ("confirmed", "rejected", "face_off", "ruled_out", "taught"),
    (
        "shared",
        "copied_into",
        "saved",
        "wall_sent",
        "kept_local",
        "allowed",
        "kept_from_swaps",
        "allowed_in_swaps",
        "swap_ended",
    ),
    ("hidden", "revealed", "deleted"),
    ("undone",),
)
CAUSE_RANK: Mapping[str, int] = {
    kind: rank for rank, kinds in enumerate(CAUSE_ORDER) for kind in kinds
}


def ordered(events: Iterable[Event]) -> list[Event]:
    """A thread in the order its acts happened: by moment, a tie by cause before effect, and the
    rows with no moment first. Every History thread is put in order here and nowhere else."""
    return sorted(
        events,
        key=lambda event: (
            event.at is not None,
            event.at or 0,
            CAUSE_RANK.get(event.kind, len(CAUSE_ORDER)),
        ),
    )


#: How many events come back when the caller does not say, and the most it will ever answer with.
#:
#: The cap keeps the NEWEST, and that is the choice worth writing down. A file with six hundred
#: events is a file somebody has been working on, and what they are looking for is what happened
#: recently, so the oldest are the ones to drop. The rows with no time at all are therefore the
#: first to go, which is right for the same reason: they are the ones that can say least.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
