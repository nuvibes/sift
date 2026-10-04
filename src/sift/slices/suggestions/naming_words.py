# SPDX-License-Identifier: AGPL-3.0-or-later
"""The words a name is read out of: folding, the noise a folder or filename carries, and the
words that are never a name."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.text import clean_stored_text

#: WHICH GENERATION OF THIS READER A CONCLUSION CAME FROM. Bump it when the answer changes.
PARSER_VERSION = 3


#: The longest a proposed name may be.
MAX_NAME = 60


#: The most words a personal name may be.
MAX_WORDS = 4


#: What separates the parts of a filename that was assembled by a tool.
_SEPARATORS = re.compile(r"[\s._\-]+")


#: A run of digits long enough to be a machine's number rather than a person's.
_LONG_NUMBER = re.compile(r"^\d{6,}$")


#: A token with digits and letters mixed and no vowel pattern a word has, like a hash fragment.
_ID_LIKE = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9]{6,}$", re.IGNORECASE)


#: Anything in brackets of any kind.
_BRACKETED = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")


#: A year on its own, and the two ways a date is written into a folder name.
_YEAR = re.compile(r"^(19|20)\d{2}$")


_DATE = re.compile(r"^(19|20)\d{2}([-_.]\d{1,2}){1,2}$")


_DAY_FIRST = re.compile(r"^\d{1,2}[-_.]\d{1,2}[-_.](19|20)?\d{2}$")


#: A resolution written as a number, either way it is spelled.
_RESOLUTION = re.compile(r"^\d{3,4}[pi]$|^\d{3,4}x\d{3,4}$")


#: `part 2`, `pt3`, `vol4`, `disc 1`, `cd2`: the suffix that says this folder is a continuation of
#: the one beside it rather than a different subject.
_PART = re.compile(r"^(part|pt|vol|volume|disc|disk|cd|set)\.?\s*\d+$", re.IGNORECASE)


#: `1st`, `2nd`, `23rd`: a position written in digits, which is nobody's name.
_ORDINAL = re.compile(r"^\d+(st|nd|rd|th)$")


#: The words that describe a file rather than name a subject.
_DATE_WORDS = frozenset(
    {
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "today", "yesterday", "week", "month", "year", "daily", "weekly", "monthly",
    }
)  # fmt: skip


_QUALITY_WORDS = frozenset(
    {
        "4k", "8k", "2k", "uhd", "fhd", "hd", "sd", "hq", "lq", "sq",
        "hdr", "sdr", "dv", "dolbyvision", "10bit", "8bit", "12bit",
        "web", "webdl", "dl", "webrip", "bluray", "brrip", "bdrip", "dvdrip", "hdtv", "hdrip",
        "remux", "proper", "repack", "rip", "raw", "source", "original", "upscaled",
        "fps", "30fps", "60fps", "120fps", "hfr",
    }
)  # fmt: skip


_CODEC_WORDS = frozenset(
    {
        "x264", "x265", "h264", "h265", "hevc", "avc", "av1", "vp9", "vp8", "xvid", "divx",
        "aac", "ac3", "eac3", "dts", "flac", "mp3", "opus", "vorbis", "pcm",
        "mp4", "mkv", "avi", "webm", "mov", "wmv", "flv", "m4v", "ts", "m2ts", "mpg", "mpeg",
        "jpg", "jpeg", "png", "webp", "heic", "avif", "bmp", "tiff", "tif",
        "zip", "rar", "7z", "tar", "gz", "part", "tmp", "temp",
    }
)  # fmt: skip


_PACK_WORDS = frozenset(
    {
        "mega", "megapack", "pack", "packs", "bundle", "archive", "archives",
        "siterip", "rip", "leak", "leaks", "leaked", "onlyfansleaks",
        "full", "complete", "ultimate", "premium", "exclusive", "vip", "free",
        "copy", "copies", "backup", "bak", "new", "old", "final", "latest", "updated",
        "collection", "collections", "compilation", "compilations",
    }
)  # fmt: skip


#: Folders somebody dumps things into, and folders named after what is in them rather than who.
_ARTEFACT_WORDS = frozenset(
    {
        "vlcsnap", "vlc", "snapshot", "snapshots", "videocapture", "capture", "captures",
        "ssnaps", "snaps", "snap", "grab", "grabs", "frame", "frames", "still", "stills",
        "bookmarks", "bookmark", "export", "exports", "output", "untitled", "unnamed",
        "dash", "hls", "segment", "chunk", "part", "clip", "record", "recording",
        "creators", "creator", "models", "model", "library",
        # How somebody labels a shelf rather than what is on it.
        "main", "independent", "personal", "general", "assorted", "various", "mixed", "everything",
    }
)  # fmt: skip


_GENERIC_WORDS = frozenset(
    {
        "downloads", "download", "downloaded", "temp", "tmp", "unsorted", "sorted",
        "misc", "miscellaneous", "other", "others", "random", "stuff", "things", "todo",
        "media", "files", "file", "content", "uploads", "upload", "saved", "save",
        "pics", "pictures", "photos", "photo", "images", "image", "img", "imgs",
        # No separate "films": it is spelled "videos", which the word already at the front of it
        # covers: one entry, not two of the same.
        "videos", "video", "vids", "vid", "clips", "clip", "movies", "movie",
        "gifs", "gif", "webms", "shorts", "reels", "stories", "story", "posts", "post",
        "highlights", "screenshots", "screenshot", "screens", "wallpapers", "wallpaper",
        "favourites", "favorites", "favs", "faves", "best", "top", "hot", "sexy", "nsfw",
        "beach", "pool", "outdoors", "indoor", "selfies", "selfie", "solo", "duo",
        "sets", "shoots", "shoot", "gallery", "galleries", "album", "albums",
        "public", "private", "shared", "inbox", "outbox", "trash", "recycle",
    }
)  # fmt: skip


#: Counting and ordering words.
_NUMBER_WORDS = frozenset(
    {
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
        "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
        "hundred", "thousand",
        "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
        "tenth", "eleventh", "twelfth", "twentieth",
    }
)  # fmt: skip


#: What a file sounds like. `Silent` and `Songs` sort files by their sound, not by who is in them.
_SOUND_WORDS = frozenset(
    {
        "silent", "silence", "mute", "muted", "noaudio", "nosound", "audio", "sound", "sounds",
        "music", "song", "songs", "track", "tracks", "soundtrack", "soundtracks",
    }
)  # fmt: skip


#: Words that say a folder is a SHELF of things rather than somebody's.
_SHELF_WORDS = frozenset(
    {
        "set", "sets", "folder", "folders", "collection", "collections", "compilation",
        "compilations", "series", "batch", "gallery", "galleries", "album", "albums",
        "pack", "packs", "megapack", "bundle", "bundles",
    }
)  # fmt: skip


#: Every word above, as one set. This is what the check reads.
STOP_WORDS: frozenset[str] = (
    _DATE_WORDS
    | _QUALITY_WORDS
    | _CODEC_WORDS
    | _PACK_WORDS
    | _GENERIC_WORDS
    | _ARTEFACT_WORDS
    | _NUMBER_WORDS
    | _SOUND_WORDS
    | _SHELF_WORDS
)


#: Whole folder names that are junk but whose words are not.
STOP_PHRASES: frozenset[str] = frozenset(
    {
        "new folder",
        "camera roll",
        "telegram desktop",
        "whatsapp images",
        "whatsapp video",
        "best of",
        "to sort",
        "not sorted",
        "my stuff",
        "untitled folder",
        "documents",
        "desktop",
        "home",
    }
)


#: Sites a downloader makes a folder for, so a fresh library with no Site rows in it yet still reads
#: `gallery-dl/instagram/harlowquin` correctly.
KNOWN_SITES: frozenset[str] = frozenset(
    {
        "gallery-dl", "gallerydl", "cyberdrop-dl", "cyberdropdl", "yt-dlp", "ytdlp", "youtube-dl",
        "instagram", "ig", "tiktok", "twitter", "x", "reddit", "onlyfans", "fansly", "patreon",
        "youtube", "twitch", "snapchat", "facebook", "tumblr", "pinterest", "flickr", "vsco",
        "coomer", "kemono", "cyberdrop", "bunkr", "gofile", "mega", "erome", "redgifs", "imgur",
        "pornhub", "xhamster", "xvideos", "manyvids", "chaturbate", "stripchat", "myfreecams",
        "telegram", "discord", "mastodon", "bluesky", "threads", "weibo", "bilibili",
        # The middleman services.
        "ssstik", "snaptik", "sssinstagram", "ssstwitter", "snapinsta", "saveinsta", "instasave",
        "storysaver", "bravedown", "fastdl", "savefrom", "igram", "tikmate", "musicaldown",
        "y2mate", "9xbuddy", "dlpanda", "snapsave", "downloadhelper", "savetweetvid", "twdown",
        "toolzin", "imginn", "picuki", "storiesig", "inflact", "cobalt",
    }
)  # fmt: skip


class Segment(StrEnum):
    """What one folder name in a chain turned out to be."""

    JUNK = "junk"
    """A date, a format, an id, a dumping ground. It names nothing and the walk skips it."""

    SITE = "site"
    """A Site. Never a person, whichever level of the tree it sits at."""

    WORD = "word"
    """Something that could name somebody. The deepest one is the claim."""


@dataclass(frozen=True, slots=True)
class Reading:
    """What one folder's chain claims, before any evidence has been asked for."""

    #: The candidate, spelled the way the folder spells it once the noise is off. Empty for none.
    name: str = ""

    #: The site this sits under, if the chain named one.
    site: str = ""

    #: Whether `name` came from a folder sitting directly under `site`.
    is_username: bool = False

    #: How far down the chain the name was found, counting the library's own folder as 0.
    depth: int = -1


#: Latin letters that are not an accented form of anything, so decomposing them yields nothing and
#: dropping them silently shortens a name.
_LATIN_STANDINS = {
    "\u00e6": "ae",  # ae ligature
    "\u0153": "oe",  # oe ligature
    "\u00f8": "o",  # o with stroke
    "\u0142": "l",  # l with stroke
    "\u0111": "d",  # d with stroke
    "\u00f0": "d",  # eth
    "\u00fe": "th",  # thorn
    "\u0127": "h",  # h with stroke
    "\u0167": "t",  # t with stroke
    "\u0131": "i",  # dotless i
    "\u014b": "ng",  # eng
}


def _without_latin_accents(decomposed: str) -> str:
    """A decomposed string with the marks dropped from its LATIN letters and nobody else's."""
    kept: list[str] = []
    latin_base = False
    for character in decomposed:
        # By CATEGORY rather than by combining CLASS.
        if unicodedata.category(character).startswith("M"):
            if not latin_base:
                kept.append(character)
            continue
        latin_base = character.isascii()
        kept.append(character)
    return "".join(kept)


def fold(text: str) -> str:
    """A segment reduced to what two spellings of it have in common.

    **A letter is a letter in any script.**
    """
    cleaned = clean_stored_text(text)
    # The overwhelming majority of names are plain ASCII, and that case is two regex passes rather
    # than a walk over the string. The general path below is only paid for by names that need it.
    if cleaned.isascii():
        return " ".join(re.sub(r"[^0-9a-z]+", " ", cleaned.casefold()).split())
    lowered = cleaned.casefold()
    expanded = "".join(_LATIN_STANDINS.get(character, character) for character in lowered)
    stripped = _without_latin_accents(unicodedata.normalize("NFKD", expanded))
    # `isalnum` rather than a range: it is true for a letter or a digit in every script, and false
    # for punctuation, symbols and emoji, which is exactly the line to draw, and a character
    # range could only draw it for English.
    kept = "".join(
        character if character.isalnum() or unicodedata.category(character).startswith("M") else " "
        for character in stripped
    )
    # Put back together.
    return " ".join(unicodedata.normalize("NFC", kept).casefold().split())


def _is_noise_token(token: str) -> bool:
    """Whether one word of a name is the machine's rather than a person's."""
    if token in STOP_WORDS:
        return True
    if _YEAR.match(token) or _LONG_NUMBER.match(token):
        return True
    if _RESOLUTION.match(token) or _ORDINAL.match(token):
        return True
    return bool(_ID_LIKE.match(token))


def _written_words(text: str) -> list[tuple[str, str]]:
    """Each word of `text` as it is written, beside its folded form."""
    if text.isascii():
        return [(word, word.casefold()) for word in re.findall(r"[0-9A-Za-z]+", text)]
    words: list[tuple[str, str]] = []
    current: list[str] = []
    for character in f"{text} ":
        if character.isalnum() or unicodedata.category(character).startswith("M"):
            current.append(character)
            continue
        if current:
            written = "".join(current)
            words.append((written, fold(written)))
            current = []
    return words


def _is_noise_word(folded: str) -> bool:
    """Whether a word, folded, is noise. A word that folds to several tokens is noise only if every
    one of them is."""
    return all(_is_noise_token(token) for token in folded.split())


def strip_noise(name: str) -> str:
    """A folder or file name with everything that describes the file taken off it."""
    words = _written_words(_BRACKETED.sub(" ", clean_stored_text(name)))
    while words and _YEAR.match(words[0][1]):
        words = words[1:]
    for position, (_written, folded) in enumerate(words):
        if _YEAR.match(folded):
            words = words[:position]
            break
    kept = [word for word in words if not _is_noise_word(word[1])]
    without_sites = [word for word in kept if word[1] not in KNOWN_SITES]
    return " ".join(written for written, _folded in (without_sites or kept))


def is_date_like(name: str) -> bool:
    """Whether a whole folder name is a date and nothing else."""
    raw = clean_stored_text(name).strip()
    if _DATE.match(raw) or _DAY_FIRST.match(raw) or _YEAR.match(raw):
        return True
    folded = fold(raw)
    if not folded:
        return False
    tokens = folded.split()
    # `2023 07`, `july 2023`, `14`. Every token is a number or a month, and there is at least one.
    return all(token in _DATE_WORDS or token.isdigit() for token in tokens)


def is_stop_folder(name: str) -> bool:
    """Whether a whole folder name contributes nothing to who somebody is."""
    # Brackets come off first here too.
    folded = fold(_BRACKETED.sub(" ", clean_stored_text(name)))
    if not folded:
        return True
    if folded in STOP_PHRASES:
        return True
    if is_date_like(name):
        return True
    if _PART.match(folded):
        return True
    tokens = folded.split()
    return all(_is_noise_token(token) for token in tokens)


#: Words a sentence is built from.
_SENTENCE_WORDS = frozenset(
    {
        "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "from", "into", "onto",
        "by", "as", "is", "was", "are", "were", "be", "been", "my", "your", "his", "her", "its",
        "our", "their", "that", "this", "these", "those", "who", "what", "when", "where", "why",
        "how", "need", "needs", "want", "wants", "get", "gets", "got", "all", "some", "any",
        "more", "less", "here", "there", "them", "they", "it", "so", "but", "or", "if", "then",
    }
)  # fmt: skip


def reads_like_a_name(name: str) -> bool:
    """Whether what is left after the noise could be somebody's name or a username."""
    stripped = strip_noise(name)
    if not stripped or len(stripped) > MAX_NAME:
        return False
    tokens = fold(stripped).split()
    if not tokens or len(tokens) > MAX_WORDS:
        return False
    if all(token.isdigit() for token in tokens):
        return False
    # One word left beside a shelf word is the shelf's theme, not somebody (see `_SHELF_WORDS`).
    if len(tokens) == 1 and any(
        token in _SHELF_WORDS for token in fold(_BRACKETED.sub(" ", name)).split()
    ):
        return False
    # A sentence rather than a name.
    if sum(1 for token in tokens if token in _SENTENCE_WORDS) > 1:
        return False
    # `Nadia and Sarah` is two people, and two people are two claims.
    return "and" not in tokens


def classify(name: str, *, sites: Iterable[str] = ()) -> Segment:
    """What one folder name in a chain is."""
    if is_stop_folder(name):
        return Segment.JUNK
    folded = fold(name)
    known = {fold(site) for site in sites} | KNOWN_SITES
    if folded in known or folded.replace(" ", "") in known:
        return Segment.SITE
    # A domain-shaped name is its first label.
    if "." in name and folded.split() and folded.split()[0] in known:
        return Segment.SITE
    return Segment.WORD
