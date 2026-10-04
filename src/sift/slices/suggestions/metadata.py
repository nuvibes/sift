# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two fields this application will read out of a picture, and what they are allowed to say.

The filename pass can read the site's permanent NUMBER for a username out of a name and cannot
read the username itself. A library can hold thousands of files in the dated Instagram shape
under a handful of such numbers, none of which appears anywhere else in the library: not in the
other filename shape, not in `usernames.number`, not in a folder name. So those files sit under no
site at all, and nothing else in Sift could teach it those pairs.

The pictures themselves know. Whatever tool saved them wrote the username into EXIF `Artist` and the
post's address into `ImageDescription`. WHICH tool is not known: the files carry no `Software` tag
that would say (`naming.py` says the same about the filename). Reading those two fields ONCE per
unknown number (a handful of files each, out of band) names the username for every file that
carries it.

## The field list IS the refusal, and it is the whole privacy design

**Nothing here takes what a container happens to offer.** It names the two fields it wants, and it
names them in ffprobe's OWN argument (`-show_entries frame_tags=Artist,ImageDescription`), so
everything else is discarded by the tool and never crosses back into this process. The same tuple
is then used again to filter what did come back, which is belt and braces on purpose: an ffprobe
run that ignored the filter would otherwise be a reader with no list.

That matters because the thing being refused is really found in files: some videos carry
`location` / `location-eng` (a phone's coordinates written into an MP4), and asking a photograph
for all its tags can return `GPSLatitudeRef` beside the two fields this wants. A reader that
took the tag dictionary whole would be carrying somebody's coordinates into a database that has no
column for them and no reason to want one.

## What a reading has to prove before it names anything

Two things:

* **The `Artist` is the username.** Every number whose files are pictures carries one, and every
  picture of a number carries the same one.
* **The `ImageDescription` is an address on the site the SHAPE already named.** It reads
  `https://www.instagram.com/...`, which corroborates the site the filename signature declares
  without being asked to name the username, and that is all it can do in most cases, because the
  address is a POST address (`instagram.com/p/<code>/`) and a post address names no username.

  **The description's address is NOT required to name the same username**: most carry post
  addresses, and held to that rule nearly every number would stay unnamed. So the address
  corroborates the SITE always, and is held to the username in the one shape that carries one (a
  profile address).

The count of agreeing pictures is TWO. That names most numbers and nearly all of their files; three
would name fewer, and one would let a single stray file invent a username.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from sift.kernel.config import Settings
from sift.kernel.media import FFmpegError, run_json
from sift.kernel.subprocess import Priority
from sift.kernel.urls import INSTAGRAM_WORDS

#: THE ONLY TWO FIELDS THIS APPLICATION READS OUT OF A PICTURE, and the list is the refusal.
#:
#: Frozen, named, and used in two places that must not disagree: it is the argument handed to
#: ffprobe, so nothing else is ever returned, AND the allow-list `understand` filters on, so nothing
#: else could be kept if it were. Everything else a container carries (a phone's coordinates, a
#: camera's serial, an editor's advertisement) is out of reach by construction rather than by
#: somebody remembering not to look at it.
#:
#: `Artist` is the username. `ImageDescription` is the address the picture was saved
#: from, and it is read for ONE thing: whether it is on the site the filename's shape already named.
FIELDS: Final[tuple[str, str]] = ("Artist", "ImageDescription")

#: The host an address has to be on for it to corroborate a site. One row, because one site's
#: shapes are all the filename reader knows (see `naming.SIGNATURES`). A site that is not in here
#: corroborates nothing and so learns nothing, which is the honest direction: the reading is only
#: as good as the agreement between two independent facts, and there is no agreement to have.
SITE_ADDRESSES: Final[Mapping[str, str]] = {"Instagram": "instagram.com"}

#: The first segment of an address that is NOT a profile's. A post address names no username, which
#: is the ordinary case.
#:
#: The route words as well as the post words: `instagram.com/explore/` is one segment long exactly
#: as a profile is, and read as a username it names one called "explore". The words are the
#: kernel's (`kernel/urls.py`), the same list the download's own reader refuses; catalog v57 refiles
#: usernames like `p` and `stories` made before that.
_NOT_A_USERNAME: Final[frozenset[str]] = INSTAGRAM_WORDS

#: How many of a number's pictures must agree before it names a username. See the module note for
#: what each value reaches.
AGREEING_PICTURES: Final[int] = 2

#: A username, as a site spells one. Anything else in the `Artist` field is a sentence or a camera's
#: idea of a name, and this is the same bar an address's own segment is held to.
_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")

#: How long one picture gets. It is one seek to the first frame of a still, and a share that has
#: gone away must not hold a pass open: the number is not named on screen anywhere.
READ_TIMEOUT_SECONDS: Final[float] = 20.0


@dataclass(frozen=True, slots=True)
class Fields:
    """What one picture said, in the two fields and nothing else."""

    artist: str = ""
    description: str = ""


def probe_args(path: Path, *, settings: Settings) -> list[str]:
    """Ask one picture for exactly two of its fields.

    The first frame only, the way the orientation reader asks: the fields are on the picture, and
    reading further is reading the whole file to answer a question the first frame has answered.

    `-show_entries frame_tags=<the two>` is the refusal made by the tool rather than by this
    process (see `FIELDS`).
    """
    return [
        settings.ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-read_intervals",
        "%+#1",
        "-show_entries",
        f"frame_tags={','.join(FIELDS)}",
        "-of",
        "json",
        str(path),
    ]


def understand(payload: Mapping[str, Any]) -> Fields:
    """The two fields out of what ffprobe said, and nothing else out of anything.

    Keyed on `FIELDS` rather than on whatever came back, which is the second half of the refusal:
    a build of ffmpeg that ignored `-show_entries`, or a later edit that dropped the argument,
    cannot turn this into a reader of everything.
    """
    frames = payload.get("frames") or []
    if not frames or not isinstance(frames[0], Mapping):
        return Fields()
    tags = frames[0].get("tags") or {}
    if not isinstance(tags, Mapping):
        return Fields()
    kept = {name: str(tags.get(name) or "").strip() for name in FIELDS}
    return Fields(artist=kept["Artist"], description=kept["ImageDescription"])


async def read_fields(path: Path, *, settings: Settings) -> Fields:
    """One picture's two fields. Empty for anything that could not be read.

    A file that is gone, a share that is offline, a container ffprobe will not open: all of them are
    "this picture teaches nothing", which is already a case this has to handle: most pictures
    carry none of these fields at all. Raising would turn a dead share into a failed scan.
    """
    try:
        payload = await run_json(
            probe_args(path, settings=settings),
            time_limit=READ_TIMEOUT_SECONDS,
            # Nobody is waiting for this. It runs inside a pass, behind whatever somebody has open.
            priority=Priority.BACKGROUND,
            reads=path,
        )
    except FFmpegError:
        return Fields()
    return understand(payload)


def _username_of(text: str) -> str:
    """A username as this reader compares them: trimmed, without a leading `@`, case folded."""
    return text.strip().lstrip("@").casefold()


def username_in_address(address: str) -> str | None:
    """The username a site's address names, where it names one at all.

    A PROFILE address (`instagram.com/quillmoss`) names one; a POST address
    (`instagram.com/p/<code>/`) does not, and that is not a failure to parse: it is what the
    address is. None means "this address says nothing about who", which the caller reads as no
    disagreement rather than as a disagreement.
    """
    parts = [one for one in urlsplit(address).path.split("/") if one]
    if len(parts) != 1 or parts[0].casefold() in _NOT_A_USERNAME:
        return None
    return parts[0] if _USERNAME.match(parts[0]) else None


def names_the_site(address: str, site: str) -> bool:
    """Whether this address is on the site the filename's shape already named.

    The host and never the text: `instagram.com` has to BE the host or a subdomain of it, so a
    caption that happens to mention the word, or a shortener's address whose path contains it,
    corroborates nothing.
    """
    host = SITE_ADDRESSES.get(site)
    if host is None:
        return False
    where = urlsplit(address)
    if where.scheme not in ("http", "https"):
        return False
    name = where.hostname or ""
    return name == host or name.endswith(f".{host}")


def agreed_name(readings: Sequence[Fields], site: str) -> tuple[str, int] | None:
    """The username these pictures agree on, and how many of them said it. None where they do not.

    **Every picture that said anything has to say the same thing.** Not a majority and not the
    commonest answer: what is being decided is a permanent column on a username, filled once and
    never corrected, and a disagreement between two of a username's own pictures is exactly the
    case where a guess is worst. One dissenter teaches nothing, and the files stay unfiled until
    somebody types the number in.

    A picture that carried no `Artist` is not a dissenter: most pictures in any library carry
    nothing at all, and counting silence as disagreement would refuse every number.

    The site's own address is the corroboration and it is required of every picture that speaks:
    an `Artist` with no address beside it is one field agreeing with itself.
    """
    said: dict[str, str] = {}
    spoke = 0
    for reading in readings:
        if not reading.artist or not _USERNAME.match(reading.artist.strip().lstrip("@")):
            continue
        if not names_the_site(reading.description, site):
            continue
        named = username_in_address(reading.description)
        if named is not None and _username_of(named) != _username_of(reading.artist):
            # The address carries a username and it is a different one. Two of the file's own fields
            # disagreeing is the strongest possible reason not to write anything down.
            return None
        spoke += 1
        said.setdefault(_username_of(reading.artist), reading.artist.strip().lstrip("@"))
        if len(said) > 1:
            return None
    if spoke < AGREEING_PICTURES or not said:
        return None
    return next(iter(said.values())), spoke
