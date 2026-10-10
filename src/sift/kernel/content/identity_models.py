# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the content tables hold, as the rest of the kernel reads it: an asset, its places, what is built from it, and what a pass concluded about it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sift.kernel.db import Row
from sift.kernel.ingress import Kind


class LocationStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"


class DerivativeKind(StrEnum):
    """Everything Sift builds *from* an asset. All of it lives in the cache directory, all of it
    can be deleted, and all of it can be rebuilt from the original."""

    THUMB = "thumb"
    PREVIEW = "preview"
    SPRITE = "sprite"
    RENDITION = "rendition"

    REMUX = "remux"
    """A repaired copy of a file whose audio sits too far from its video to seek through.

    The same streams, copied into a new container so the two are stored together: no decode, no
    re-encode, nothing lost. It is the whole file rather than a piece of one, which makes it the
    largest thing the cache holds, and like everything else here it can be deleted and rebuilt."""


#: The kinds served as a picture, the only ones whose bytes are worth a digest. The two left out
#: are whole files streamed by range, never given a keepable address; including them would make
#: the catch-up pass re-read gigabytes on every run.
PICTURE_KINDS = frozenset({DerivativeKind.THUMB, DerivativeKind.PREVIEW, DerivativeKind.SPRITE})

#: WHICH RECIPE EACH KIND IS BUILT TO. Raised by hand when what a build produces changes; stored on
#: every row (`derivatives.recipe_version`) so a stale row reads as missing to the passes.
#: It is NOT a key in `params`: that string names the file on disk and is in the unique key, so a
#: version there would strand every picture already built. Kernel-side, as a slice cannot be read.
RECIPE_VERSIONS: Mapping[DerivativeKind, int] = {
    DerivativeKind.THUMB: 1,
    DerivativeKind.PREVIEW: 1,
    DerivativeKind.SPRITE: 1,
    DerivativeKind.RENDITION: 1,
    DerivativeKind.REMUX: 1,
}

#: WHICH FILES A KIND IS MADE FOR, where that is not every file that has been read. A still has no
#: hover clip, and a file with no running time has nothing to scrub through. Every statement that
#: asks which files lack a kind carries this condition, and `made_for` is the same rule asked of one
#: file by the builders; a test holds the two in step, so a count can always reach nought.
_MADE_FOR: Mapping[DerivativeKind, str] = {
    DerivativeKind.PREVIEW: "(a.media_type <> 'image')",
    DerivativeKind.SPRITE: "(a.media_type <> 'image' AND COALESCE(a.duration_ms, 0) > 0)",
}

_EVERY_READ_FILE = "(1 = 1)"


def made_for(kind: DerivativeKind, *, media_type: str, duration_ms: int | None) -> bool:
    """Whether this kind is ever made for a file of this type and length. See `_MADE_FOR`."""
    if kind is DerivativeKind.PREVIEW:
        return media_type != Kind.IMAGE
    if kind is DerivativeKind.SPRITE:
        return media_type != Kind.IMAGE and (duration_ms or 0) > 0
    return True


def _made_for_sql(kind: DerivativeKind) -> str:
    """The condition a statement about `a` carries for this kind; always true for most kinds."""
    return _MADE_FOR.get(kind, _EVERY_READ_FILE)


#: The transfer characteristics that mean a picture is HDR: PQ, and HLG. An encode that forces
#: 8-bit 4:2:0 onto either without mapping it first is a grey, washed picture with no sentence.
HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})


@dataclass(frozen=True, slots=True)
class Asset:
    """A unique piece of content. The columns past `added_at` are filled in by the probe."""

    id: str
    identity: str
    media_type: str
    mime: str | None
    width: int | None
    height: int | None
    duration_ms: int | None
    fps: float | None
    size_bytes: int | None
    container: str | None
    vcodec: str | None
    acodec: str | None
    bit_depth: int | None
    phash: str | None
    videohash: str | None
    original_filename: str | None
    added_at: int
    probed_at: int | None
    interleave_gap: int | None = None
    """Bytes between a moment's video and its audio. None unmeasured, 0 nothing to get wrong."""

    oshash: str | None = None
    """The exact-file hash a public stash-box files this video under. None until it is read."""

    color_transfer: str | None = None
    """How the picture's brightness is encoded, as ffprobe names it: `smpte2084` or
    `arib-std-b67` for an HDR file, `bt709` or nothing for the rest. None until it is read; an
    empty string is a stream that was read and did not say."""

    video_phash: str | None = None
    """The whole-video fingerprint that survives a re-encode. None until it is read.

    Video only. A photograph has `phash` and a GIF has `videohash`; neither can be matched against
    a stash-box, because nobody fingerprints them."""

    title: str | None = None
    """A name for this that is not its filename. Typed in on the file's record."""

    download_url: str | None = None
    """Where this was fetched from, cleaned, seeded from the downloads ledger and editable; the
    ledger's own address stays exactly what was fetched, as a re-dropped link is keyed on it."""

    release_date: str | None = None
    """When what is in this file was published, as an ISO date; NOT `added_at`, when the
    library first saw it."""

    details: str | None = None
    """What this is about, in the words of whoever released it. None until somebody says."""

    production_date: str | None = None
    """When it was filmed, as an ISO date. Usually before `release_date` and sometimes long
    before: a scene shot in one year and put out in the next is ordinary, so they are two facts
    and neither stands in for the other."""

    site_code: str | None = None
    """The reference the Site that released it files it under. Worth keeping because it is what
    somebody searches a stash-box BY when a title and a date are ambiguous."""

    music: str | None = None
    identity_version: int = 0
    """Which generation computed `identity`: 0 the whole-file digest that
    came before, 1 the sampled digest. A version-0 row is waiting to be re-identified; see `adopt_identity`."""
    whole_digest: str | None = None
    """The whole-file digest a version-0 row had before it was re-identified, kept for a pass that
    wants every byte. None for a file that arrived after the sample became the identity."""
    video_duration_ms: int | None = None
    """How long the PICTURE runs, which a file whose sound outlasts it says less than
    `duration_ms`. None until read, zero where the reading names none; either way a sampler falls
    back to the file's length (see `sampling.picture_span`)."""
    fingerprint_version: int | None = None
    """Which generation of `perceptual` took the four fingerprints. None where nobody has; below
    `perceptual.FINGERPRINT_VERSION` where they are owed again."""
    classified_version: int = 0
    """Which generation of the ingress classifier decided `media_type` and `mime`. A row below
    `ingress.CLASSIFIER_VERSION` is waiting for the reclassify pass."""
    still_at_ms: int | None = None
    #: A JPEG a browser draws apart from its stored pixels; None until the head was looked at.
    turn_apart: bool | None = None
    """The moment the tile's still was cut at, chosen by what the frame shows. None for a still
    cut before the choice was made (the first frame, whatever it was), which the black-still
    catch-up measures once; the hover clip starts here so the tile does not jump under a pointer."""

    @property
    def is_hdr(self) -> bool:
        """Whether the picture is HDR, by the transfer characteristics the probe read. Unknown is
        not HDR: mapping an ordinary picture darkens it, so the map is applied only to a file
        that plainly needs it."""
        return (self.color_transfer or "").lower() in HDR_TRANSFERS


@dataclass(frozen=True, slots=True)
class Location:
    """One place an asset's bytes physically sit."""

    id: str
    asset_id: str
    root_id: str
    folder_id: str | None
    rel_path: str
    filename: str
    size_bytes: int | None
    mtime: int | None
    status: LocationStatus
    first_seen_at: int
    last_seen_at: int
    #: Where this picture sits when it is inside an archive: which archive, and what to ask it for.
    #: Both None for an ordinary file, which is what makes `path_of` able to tell them apart without
    #: a flag column that could disagree with them.
    archive_rel_path: str | None = None
    member_path: str | None = None

    @property
    def inside_an_archive(self) -> bool:
        """Whether reading this means opening an archive first: both columns, or it fails as a
        missing file rather than a confusing one."""
        return self.archive_rel_path is not None and self.member_path is not None


@dataclass(frozen=True, slots=True)
class Carrier:
    """One file here carrying an identity somebody asked about (`ContentStore.carriers_of`).

    `kind` is `oshash` or `phash` with `key` the hash as asked, lower case, or `place` with the
    folder and the path the file sits at as this library keeps them."""

    kind: str
    key: str | None
    root_id: str | None
    rel_path: str | None
    asset_id: str


@dataclass(frozen=True, slots=True)
class Derivative:
    """Something built from an asset and kept in the cache."""

    id: str
    asset_id: str
    kind: DerivativeKind
    rel_cache_path: str
    params: str
    size_bytes: int | None
    created_at: int

    content_hash: str | None = None
    """A short digest of this file's own bytes, or None when nothing has read them.

    It is what lets an address name the picture rather than the asset, so a browser may keep a copy
    without asking whether it is still current. None until the catch-up pass reaches a derivative
    built before this existed, and permanently None for one whose file cannot be read."""


class VerdictProduct(StrEnum):
    """What a verdict is about: the products a Build makes, the read that comes before them, and
    the identity a row is brought forward to. The first four are the Build's own product keys."""

    PROBE = "probe"
    #: One per picture rather than one for all three: each is its own product on the
    #: Generate row, with its own count and its own retry, so a file that has no frame to cut for
    #: a sprite is not also refused a thumbnail it could have had.
    THUMBNAILS = "thumbnails"
    PREVIEWS = "previews"
    SPRITES = "sprites"
    FINGERPRINTS = "fingerprints"
    FACES = "faces"
    MEANING = "meaning"
    #: The watermark read's own key, which the watermarks feature writes its verdicts under. Named
    #: here so the one table of what a product can give up on (`left_out:` in the query language)
    #: lists every product the Importing pane can say "left out" about.
    WATERMARKS = "watermarks"
    IDENTITY = "identity"


#: Which verdict a picture of each kind is filed under. Written out rather than derived from the
#: names, so a kind added without a product fails here rather than at the first verdict.
_PICTURE_VERDICTS: dict[DerivativeKind, VerdictProduct] = {
    DerivativeKind.THUMB: VerdictProduct.THUMBNAILS,
    DerivativeKind.PREVIEW: VerdictProduct.PREVIEWS,
    DerivativeKind.SPRITE: VerdictProduct.SPRITES,
}


def picture_verdict(kind: DerivativeKind) -> VerdictProduct:
    """The product a verdict about this kind of picture is filed under."""
    return _PICTURE_VERDICTS[kind]


@dataclass(frozen=True, slots=True)
class Verdict:
    """What a feature decided it could not make for a file, and why. See `file_verdicts`."""

    asset_id: str
    product: str
    code: str
    reason: str
    transient: bool
    at: int


def _verdict_from_row(row: Row) -> Verdict:
    return Verdict(
        asset_id=str(row["asset_id"]),
        product=str(row["product"]),
        code=str(row["code"]),
        reason=str(row["reason"]),
        transient=bool(row["transient"]),
        at=int(row["at"]),
    )


@dataclass(frozen=True, slots=True)
class Within:
    """Which files a piece of work is WANTED for at all, as a condition on the assets row `a`.

    Built only by `wanted_outside`, which owns the statement; a feature never writes SQL against
    `assets` or `asset_locations`. Carried by a `Lack` so the count of what is lacking and the
    count of what is wanted (a bar's numerator and its denominator) apply the same rule.
    """

    condition: str
    params: tuple[Any, ...] = ()


#: Where a walk of the library has reached: the last file handed out, as (added_at, id).
PageKey = tuple[int, str]


@dataclass(frozen=True, slots=True)
class LibraryPage:
    """One page of a walk of the library: the files on it, and where the next page starts."""

    ids: list[str]
    last: PageKey | None


@dataclass(frozen=True, slots=True)
class Lack:
    """One thing a file can lack, as a condition on the assets row, for `ContentStore.count_lacking`.

    `condition` is a boolean SQL expression over the alias `a` (the assets row), and is a
    constant of the feature that owns the table it looks in, written out like every other
    statement here: `NOT EXISTS (SELECT 1 FROM its_table t WHERE t.asset_id = a.id AND ...)`.
    Only `params` carries anything decided at run time, bound to the condition's `?` in order.
    """

    condition: str
    params: tuple[Any, ...] = ()
    product: str | None = None
    """The product a verdict about this is filed under (see `file_verdicts`). A term with one
    leaves out every file the feature has already said it cannot make this for."""
    within: Within | None = None
    """Which files want it at all, where a folder refuses it (see `wanted_outside`). A file only
    in a folder that said no to this work is not lacking it: nothing is going to make it there."""


#: Which reading of a kept probe answer this is. Raised by hand when what is stored changes shape
#: (another argument to the tool, a different stripping rule), so a later reader can tell what
#: it is looking at without guessing from the contents.
#:
#: 2: a still's reading holds its first frame, which is where its turn is read from, so a picture
#: read under 1 may have its width and height the wrong way round (see `keep_probe`).
#: 3: a JPEG is read as a browser turns it, by its first Exif block (`kernel.jpeg_turn`); only a
#: JPEG photograph read under 2 is looked at again, and only one read the other way is read again.
PROBE_VERSION = 3

#: The reading every file but a JPEG photograph is current at.
PROBE_VERSION_NOT_A_JPEG = 2


@dataclass(frozen=True, slots=True)
class ProbeKeep:
    """The tool's whole answer about one file, ready to be stored.

    Built by whoever ran the tool, because only that caller knows which tool it was and what it
    said. The store's job is to put it in the same transaction as the fields read out of it.
    """

    body: bytes
    """The answer, compressed, with every key naming a place already taken out of it."""

    tool: str
    """Which program said it, in that program's own words: its version line."""

    version: int = PROBE_VERSION


@dataclass(frozen=True, slots=True)
class Ingested:
    """What an ingest turned out to be; `asset_is_new` is False for bytes already held, which a
    caller could not ask afterwards without a race."""

    asset: Asset
    location: Location
    asset_is_new: bool


def _flag_or_none(row: Row, key: str) -> bool | None:
    """A nullable 0/1 column as a flag; None where the column is absent or NULL."""
    # `in row` would search the VALUES: a Row iterates them, and only `keys()` names columns.
    value = row[key] if key in row.keys() else None  # noqa: SIM118
    return None if value is None else bool(value)


def asset_from_row(row: Row) -> Asset:
    return Asset(
        id=row["id"],
        identity=row["identity"],
        media_type=row["media_type"],
        mime=row["mime"],
        width=row["width"],
        height=row["height"],
        duration_ms=row["duration_ms"],
        fps=row["fps"],
        size_bytes=row["size_bytes"],
        container=row["container"],
        vcodec=row["vcodec"],
        acodec=row["acodec"],
        bit_depth=row["bit_depth"],
        phash=row["phash"],
        videohash=row["videohash"],
        original_filename=row["original_filename"],
        added_at=row["added_at"],
        probed_at=row["probed_at"],
        interleave_gap=row["interleave_gap"],
        oshash=row["oshash"],
        video_phash=row["video_phash"],
        color_transfer=row["color_transfer"],
        title=row["title"],
        download_url=row["download_url"],
        release_date=row["release_date"],
        details=row["details"],
        production_date=row["production_date"],
        site_code=row["site_code"],
        music=row["music"],
        identity_version=row["identity_version"],
        whole_digest=row["whole_digest"],
        video_duration_ms=row["video_duration_ms"],
        fingerprint_version=row["fingerprint_version"],
        classified_version=row["classified_version"],
        still_at_ms=row["still_at_ms"],
        turn_apart=_flag_or_none(row, "turn_apart"),
    )


def location_from_row(row: Row) -> Location:
    return Location(
        id=row["id"],
        asset_id=row["asset_id"],
        root_id=row["root_id"],
        folder_id=row["folder_id"],
        rel_path=row["rel_path"],
        filename=row["filename"],
        size_bytes=row["size_bytes"],
        mtime=row["mtime"],
        status=LocationStatus(row["status"]),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        archive_rel_path=row["archive_rel_path"],
        member_path=row["member_path"],
    )


def derivative_from_row(row: Row) -> Derivative:
    return Derivative(
        id=row["id"],
        asset_id=row["asset_id"],
        kind=DerivativeKind(row["kind"]),
        rel_cache_path=row["rel_cache_path"],
        params=row["params"],
        size_bytes=row["size_bytes"],
        created_at=row["created_at"],
        content_hash=row["content_hash"],
    )


_DELETE_ASSET = "DELETE FROM assets WHERE id = ? RETURNING id"


@dataclass(frozen=True, slots=True)
class Lacking:
    """What `ContentStore.count_lacking` answers: a count per term, in the order the terms were
    given, and how many files at least one of the ticked terms is true of."""

    each: tuple[int, ...]
    files: int


@dataclass(frozen=True, slots=True)
class FolderMedia:
    """One folder's own contents, as facts rather than a verdict: how many pictures make a set
    is the caller's rule."""

    still_ids: list[str]
    moving: int
