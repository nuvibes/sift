# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every phrasing a recap card can lead with, by kind.

A phrasing is a headline of two to five words and a context of one or two short sentences. Its
`story` is the condition its figures must tell (`recaps_voice_facts`); one with no story fits any
figures that fill its slots. A slot is a word in braces, filled from the card's figures.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Phrasing:
    id: str
    headline: str
    context: str
    story: str = ""


def _kind(name: str, *lines: tuple[str, str, str, str]) -> tuple[Phrasing, ...]:
    return tuple(
        Phrasing(f"{name}.{one}", head, context, story) for one, story, head, context in lines
    )


#: The stories told before any other a card's figures tell: the reader against their own usual.
FIRST: Mapping[str, frozenset[str]] = {
    "sift_did": frozenset({"day_quiet", "quiet", "big"}),
    "theater": frozenset({"more"}),
    "when": frozenset({"day_near", "day_far", "day_same", "moved"}),
}


LINES: Mapping[str, tuple[Phrasing, ...]] = {
    "headline": _kind(
        "headline",
        ("cover", "", "Your {title}", "{total} viewed. Most of it went on {lead}."),
        ("here", "", "Here's your {noun}", "{total} of viewing, {lead_share} of it on {lead}."),
        ("theater", "theater", "A Theater {noun}", "{theater} on the wall, {rest} off it."),
        ("video", "video", "A video kind of {noun}", "{video} of videos, {image} of pictures."),
        ("pictures", "pictures", "A picture {noun}", "{image} of pictures. Videos got {video}."),
        ("even", "even", "Half and half", "{a} of {a_kind}, {b} of {b_kind}. Too close to call."),
        ("long", "long", "Quite a {noun}", "{total} viewed. That's {long}."),
    ),
    "compared": _kind(
        "compared",
        (
            "day_big",
            "day_big",
            "A big {weekday}",
            "{now} of viewing. Your usual is closer to {usual}.",
        ),
        ("day_up", "day_up", "A little extra", "{now} of viewing, {diff} past your usual."),
        (
            "day_usual",
            "day_usual",
            "Right on your usual",
            "{now} of viewing. Most days look like this.",
        ),
        (
            "day_easy",
            "day_easy",
            "An easy {weekday}",
            "{now} of viewing. Your usual day brings {usual}.",
        ),
        (
            "day_quiet",
            "day_quiet",
            "A quiet {weekday}",
            "{now} of viewing. Some days are for other things.",
        ),
        ("big", "big", "Up, and then some", "{now} viewed, {times} as much as {before}."),
        ("up", "up", "Up on {before}", "{diff} more, {now} in all."),
        ("usual", "usual", "Steady as you go", "{now} viewed, much like {before}."),
        ("easy", "easy", "A lighter {noun}", "{now} viewed, against {usual} {before_when}."),
        (
            "quiet",
            "quiet",
            "A breather after {before}",
            "{now} viewed, a calmer pace than {before}.",
        ),
    ),
    "top_person": _kind(
        "top_person",
        (
            "mostly",
            "mostly",
            "Mostly {person_head}",
            "{share} of everything you viewed. Nobody else came close.",
        ),
        ("owned", "owned", "{person_head}'s {noun}", "{time} together, {share} of your viewing."),
        ("long", "long", "{person_head}, on repeat", "{time} of viewing. That's about {films}."),
        ("you_and", "", "You and {person_head}", "{time} together {when}."),
        ("front", "", "Front and center", "{person}, with {time} of viewing."),
    ),
    "top_five": _kind(
        "top_five",
        (
            "runaway",
            "runaway",
            "{first_head} by a mile",
            "{t1} of viewing. {second} came next with {t2}.",
        ),
        ("close", "close", "A close race", "{first} edged out {second} by {gap}."),
        ("spread", "spread", "Plenty to go around", "{n} people, none far behind the top."),
        ("front_row", "", "The front row", "{n} people, {total} between them."),
        ("top", "", "Your top {n}", "{first} led the way with {t1}."),
    ),
    "top_site": _kind(
        "top_site",
        ("mostly", "mostly", "Mostly {site_head}", "{share} of your viewing came from there."),
        ("little", "little", "A little {site_head}", "{time} of viewing was enough for the top."),
        ("led", "", "{site_head} led the way", "{time} of viewing, {share} of the {unit}."),
        ("of_the", "", "Your Site of the {unit}", "{site}, with {time} of viewing."),
        ("on_top", "", "{site_head} on top", "{time} of what you viewed came from there."),
    ),
    "top_tag": _kind(
        "top_tag",
        ("all", "mostly", "{tag_head} all the way", "{share} of your viewing carried this tag."),
        ("dash", "little", "A dash of {tag_head}", "{time} was enough to lead your tags."),
        ("kind_of", "", "A {tag_head} kind of {noun}", "{time} of viewing wore that tag."),
        ("above", "", "{tag_head}, above the rest", "{time}, {share} of your {unit}."),
        ("tagged", "", "Tagged and viewed", "{tag} led your tags with {time}."),
    ),
    "top_song": _kind(
        "top_song",
        ("stuck", "long", "Stuck in your head", "{song}, behind {time} of viewing."),
        ("for_the", "little", "A song for the {unit}", "{time} with {song} playing."),
        ("soundtrack", "", "Your soundtrack: {song_head}", "{time} of viewing set to it."),
        ("song_of", "", "The song of the {unit}", "{song} played under {time} of viewing."),
        ("scored", "", "Your {noun}, scored", "{song} topped your songs with {time}."),
    ),
    "first_last": _kind(
        "first_last",
        ("early", "early", "Up early", "First file at {first_at}, last at {last_at}."),
        ("late", "late", "Night owl", "Last file at {last_at}. The first opened at {first_at}."),
        (
            "both",
            "both",
            "Early bird and night owl",
            "First at {first_at}, last at {last_at}. Both, then.",
        ),
        (
            "long",
            "long",
            "A {span_hours}-hour day",
            "That's how far your first file was from your last.",
        ),
        ("short", "short", "Short and sweet", "First file to last in {gap}."),
        ("bookends", "", "The bookends", "{first_file} at {first_at}. {last_file} at {last_at}."),
        (
            "from_to",
            "",
            "{first_head} to {last_head}",
            "Your first file and your last, {gap} apart.",
        ),
    ),
    "when": _kind(
        "when",
        (
            "near",
            "day_near",
            "{hour} was your hour",
            "Your {part}s usually peak closer to {usual_bare}.",
        ),
        (
            "far",
            "day_far",
            "A change of hour",
            "{hour} led the way. Most days peak closer to {usual_hour}.",
        ),
        ("same", "day_same", "Right on schedule", "{hour} again, your usual peak."),
        (
            "moved",
            "moved",
            "A new favorite hour",
            "{hour} took over from {usual_hour}, {before}'s peak.",
        ),
        ("late", "late", "After midnight", "Your busiest hour started at {hour}."),
        ("morning", "morning", "A morning person", "{hour} beat every other hour {when}."),
        ("prime", "", "Prime time: {hour}", "That hour alone held {share} of your {unit}."),
        (
            "slot",
            "",
            "{weekday_head} at {hour}",
            "{weekday_share} of your {unit} fell on {weekday_plain}.",
        ),
    ),
    "theater": _kind(
        "theater",
        ("films", "films", "{time_head} in Theater", "That's about {films}."),
        ("night", "night", "Theater night", "{time} on the wall. {films}, give or take."),
        (
            "encore",
            "more",
            "You stayed for the encore",
            "{diff} more than {than}.",
        ),
        (
            "double",
            "double",
            "Almost a double feature",
            "{time} in Theater. {left} more and it's two films.",
        ),
        ("short", "short", "A short show", "{time} on the wall {when}."),
        ("big_screen", "", "The big screen", "You gave Theater {time} {when}."),
    ),
    "sift_did": _kind(
        "sift_did",
        (
            "day_quiet",
            "day_quiet",
            "{n} files imported",
            "A quiet day for the library. Your usual {weekday} brings {usual}.",
        ),
        (
            "quiet",
            "quiet",
            "{n} new files",
            "A quiet {unit} for the library. {before} brought {usual}.",
        ),
        ("big", "big", "A big import {unit}", "{n} files imported, {times} {than}."),
        ("small", "small", "Small batch", "{n} new files. Fewer to sort, more time to look."),
        ("huge", "huge", "The library grew", "{n} new files {when}. Plenty to look through."),
        (
            "faces",
            "faces",
            "Faces, put to names",
            "{faces} faces named {when}. People keeps growing.",
        ),
        ("organized", "organized", "Tidy work", "{decided} answers on Organize {when}."),
        ("filed", "filed", "Filed away", "{filed} files filed {when}."),
        ("ready", "", "Fresh in the library", "{n} files imported {when}, ready to view."),
    ),
    "rated": _kind(
        "rated",
        ("opinions", "", "You had opinions", "{rated} rated {when}."),
        ("stars", "stars", "Stars handed out", "{starred} starred {when}."),
        ("both", "both", "Rated and starred", "{rated} rated, {starred_n} starred."),
        ("many", "many", "The critic is in", "{rated_n} ratings {when}. That's {per_day} a day."),
        ("few", "few", "A careful eye", "{rated} rated, each one on purpose."),
    ),
    "o": _kind(
        "o",
        ("count", "", "Keeping count", "{presses} {when}."),
        ("moved", "", "The O counter moved", "{n} presses, all yours."),
        ("one", "one", "One and done", "A single O press {when}."),
        ("many", "many", "{n} and counting", "That's {per_day} a day {when}."),
        ("tally", "", "Another tally mark", "{presses} on the counter {when}."),
    ),
    "closing": _kind(
        "closing",
        ("that_was", "", "That was {span}", "See you {next}."),
        ("same_time", "", "Same time {next}?", "Your next recap is on its way."),
        ("wrap", "", "And that's a wrap", "{span}, every number yours."),
        ("quite", "year_long", "Quite a year", "{total} viewed. That's {days}, end to end."),
        ("numbers", "year", "Your year in numbers", "Every figure here is yours alone."),
    ),
    "top_file": _kind(
        "top_file",
        ("repeat", "repeat", "On repeat", "{views} of the same {kind_word}."),
        ("stuck", "stuck", "This one stuck", "You came back to it {more} more times."),
        ("twice", "twice", "Twice is nice", "Two views, and the top spot."),
        ("named", "", "Your file of the {unit}", "{file}, viewed {once}."),
        ("another", "", "Worth another look", "{views} for this {kind_word} {when}."),
    ),
    "new_favourite": _kind(
        "new_favourite",
        ("new", "", "A new favorite", "Viewed {once} the day you found it."),
        ("love", "", "Love at first view", "{views} on day one."),
        ("classic", "", "Instant classic", "{file}, {once} on its first day."),
        ("hooked", "hooked", "Hooked from the start", "{views} on the first day alone."),
        ("keeper", "", "A keeper", "Found {when}, viewed {once} straight away."),
    ),
    "rediscovered": _kind(
        "rediscovered",
        ("long_time", "", "Long time, no see", "{days} away, then back {when}."),
        ("welcome", "", "Welcome back", "Your first view of this one in {days}."),
        ("archive", "archive", "Back from the archive", "More than {years} since your last view."),
        ("dusted", "dusted", "Dusted off", "{months} since you last viewed it."),
        ("still_good", "", "Still good", "{file}, back after {days}."),
    ),
    "session": _kind(
        "session",
        ("deep", "deep", "{pages_n} pages deep", "{time} from open to close."),
        ("long", "long", "The long haul", "One visit, {time} from start to finish."),
        ("short", "short", "Quick and to the point", "Your longest visit ran {time}."),
        ("open", "open", "A visit that kept going", "{time} from opening Sift to closing it."),
        ("longest", "", "Your longest visit", "{time}, {pages} along the way."),
    ),
    "downloads": _kind(
        "downloads",
        ("haul", "", "The haul", "{downloads} finished {when}."),
        ("big", "big", "A big haul", "{n} downloads, {per_day} a day."),
        ("one", "one", "One for the library", "A single download finished {when}."),
        ("new", "", "New in the library", "{downloads} done {when}, ready to view."),
        ("sites", "", "Freshly downloaded", "{n} new files from your Sites {when}."),
    ),
    "theater_files": _kind(
        "theater_files",
        ("wall", "", "Wall to wall", "{files} took a turn on the wall."),
        ("packed", "packed", "A packed wall", "{files}, {per_day} a day."),
        ("lineup", "", "Theater's lineup", "{files} in the lineup {when}."),
        ("small", "small", "A short lineup", "{files}, each given its moment."),
        ("rounds", "rounds", "Round after round", "{files} cycled through Theater."),
    ),
    "alongside": _kind(
        "alongside",
        ("in_step", "", "{a} and {b}, in step", "Over {n}, the two rose and fell together."),
        ("pattern", "", "A pattern of yours", "On the {units} {a} rose, so did {b}."),
        ("rising", "", "Rising together", "When {a} went up, {b} followed."),
        ("as_one", "", "These two go together", "{a} and {b} moved as one over {n}."),
        ("kind", "", "Two of a kind", "{a} and {b} kept pace over {n}."),
    ),
    "mosaic": _kind(
        "mosaic",
        ("top", "", "Your top {n} files", "{v1} for the first, {v2} for the next."),
        ("runaway", "runaway", "One clear favorite", "{v1} for the top file. The next had {v2}."),
        ("reel", "", "The highlight reel", "{n} files, {total} between them."),
        ("rotation", "", "On heavy rotation", "{total} across your top {n}."),
        ("close", "close", "Neck and neck", "{v1} and {v2}, side by side."),
    ),
    "before_after": _kind(
        "before_after",
        ("focus", "changed", "A change of focus", "{m1} leaned toward {k1}. {m2}, toward {k2}."),
        ("from_to", "changed", "From {k1} to {k2}", "{m1} to {m2}, your taste moved."),
        ("steady", "same", "Steady taste", "Mostly {k} in {m1}, and still in {m2}."),
        ("still", "same", "Still {k}", "{m1} and {m2} agreed on that."),
        ("true", "same", "True to {k}", "The year opened and closed on {k}."),
    ),
    "race": _kind(
        "race",
        (
            "every",
            "every",
            "{person_head}, every month",
            "The most-viewed person in all {of} months.",
        ),
        ("most", "most", "{person_head}'s year", "Out in front for {m} of {of} months."),
        ("varied", "varied", "Changing places", "{leaders} different people led a month."),
        ("race", "", "The race for the top", "{person} led {m} of {of_months}."),
        ("monthly", "", "Month by month", "{person} came out ahead in {m_months}."),
    ),
    "heatmap": _kind(
        "heatmap",
        ("days", "", "{days}", "{share} of your days {when} had viewing."),
        ("brightest", "", "Your brightest square", "{busiest}, with {busiest_time} viewed."),
        (
            "streak",
            "streak",
            "{streak} in a row",
            "From {streak_from} to {streak_to}, never a day off.",
        ),
        (
            "shape",
            "shape",
            "Your year, day by day",
            "Busiest in {best_month}, quietest in {quiet_month}.",
        ),
        (
            "column",
            "column",
            "{weekday}s run deep",
            "Your busiest column, {weekday_share} of the year.",
        ),
    ),
}
