# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rules that make a photo set without anybody asking for one.

A set can be put together by hand, and that is what a collection is for as well. What makes a photo
set its own kind of thing is that its membership can be DERIVED (it arrived together, so it
belongs together) and these are the ways something arrives together in Sift today:

* **A folder holds pictures and nothing else.** Somebody's library already says these belong
  together; the folder IS the shoot.
* **A ZIP of pictures.** The archive is the shoot, which is how galleries usually arrive. Indexed
  where it lies rather than unpacked. See `sift.kernel.archives`.

The two library rules go through writes that are idempotent on their subject (the folder, or the
archive), so a second pass over either fills in what has arrived since rather than making a second
set beside the first. What a set is keyed BY is the whole of that promise: a rule with no key would
make a new set on every scan.

**Neither rule reads the library through the access layer, and that is correct here.** These run in
a background job, for nobody, exactly as the suggestions pass does: there is no viewer to scope to
and nothing they produce is served. What IS scoped is every screen that later draws the set: the
wall, the count and the pictures all go through the resolver, so a file somebody has hidden is
absent from their view of a set that contains it.
"""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.content import ContentStore
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.photo_sets import MIN_PICTURES
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.slices.photo_sets.service import PhotoSet, PhotoSetService

log = get_logger(__name__)

# How many pictures make a shoot is the kernel's (`kernel.photo_sets.MIN_PICTURES`), so a sentence
# that names the floor reads it without importing this slice.


async def set_from_shoot(
    asset_ids: Sequence[str],
    *,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> str | None:
    """A proposed shoot, once somebody has agreed it is one. The id of the set, or None.

    **The way pictures arrive together that nobody could see from the files.** The folder and
    archive rules read something already written down and this
    reads what the pictures LOOK like: a run of one creator's loose stills that the meaning index
    puts in one place, one light, one outfit. The rule that finds them is somewhere else entirely;
    what reaches here is a grouping a person has pressed yes to.

    The same body as a post's, through `_one_set`, and that is the point of it being here at all
    rather than in the feature that proposes. A second creation path would be free to forget the
    cover, the order, or that `MIN_PICTURES` pictures are the fewest that make a shoot, and it
    would be exercised far less often than this one.

    `origin` is its own word, `shoot`, for the reason the others have theirs: `origin` is what
    lets a later pass tell a set it may refresh from one a person assembled, and a shoot is neither:
    it can be proposed again from the pictures, and it was never read off a folder or an archive.
    The word also carries the provenance onto the row itself (`created_by_via`), so a Photo Set's
    own screen can say a pass proposed it rather than that somebody typed it in.
    """
    made = await _one_set(
        asset_ids,
        name=name,
        origin="shoot",
        url=None,
        content=content,
        service=service,
        why="photo_sets.derived_from_shoot",
    )
    return None if made is None else made.id


async def set_from_stash_library(
    asset_ids: Sequence[str],
    *,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> str | None:
    """A gallery a Stash library kept, made a set of the pictures of it that are here. The id of
    the set, or None.

    The same body as a shoot's, with the import's own pass word as `origin`, so the set says on its
    own page that a Stash library made it rather than that a shoot was found.
    """
    made = await _one_set(
        asset_ids,
        name=name,
        origin=VIA_STASH_LIBRARY,
        url=None,
        content=content,
        service=service,
        why="photo_sets.derived_from_stash_library",
    )
    return None if made is None else made.id


async def set_from_post(
    asset_ids: Sequence[str],
    *,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> PhotoSet | None:
    """One post's pictures, as a set. The SET, or None if this was not one.

    The only one of the five that answers with the row rather than its id, and the reason is the
    record: the pass that asks for this writes a receipt naming the set, and a receipt has to
    snapshot what the set is CALLED. Taking that from the name it asked for would make the line say
    the username, true only for as long as the naming rule here keeps choosing it, and a snapshot
    that is right by coincidence is the kind that goes wrong quietly. So what comes back is what was
    made.

    **Another way pictures arrive together, and the oldest of them: they were posted together.** A
    carousel is one post, and the files of it are sitting in the library already, filed under the
    username their own filenames carry. Which files were in one post is decided by the
    filename reader (`suggestions.naming.one_post`) and never here; what reaches this is a group it
    has already settled.

    The same body as a shoot's, through `_one_set`. That is the whole reason these are short
    functions and not copies: the rule about the fewest pictures, about a mixed
    arrival deriving nothing, about the cover being the first of them, is stated once. A post
    holding a video alongside its pictures therefore makes no set at all, which is the same refusal
    a folder holding a clip and its poster frames gets, for the same reason.

    `origin` is its own word, `filename`, and not `download`: with that word the set would say
    "was made from a download" on its own History and carry `created_by_via = 'download'`, which
    attributes it to a pass that never touched these files.
    Sift did not fetch them; it read their names.

    No `url`. There is no address (the post was recognised from names, not visited), and putting
    a guess at one in `origin_url` would be a link somebody follows to a page that may not be the
    post at all.
    """
    return await _one_set(
        asset_ids,
        name=name,
        origin="filename",
        url=None,
        content=content,
        service=service,
        why="photo_sets.derived_from_post",
    )


async def _one_set(
    asset_ids: Sequence[str],
    *,
    name: str,
    origin: str,
    url: str | None,
    content: ContentStore,
    service: PhotoSetService,
    why: str,
) -> PhotoSet | None:
    """The body both of the sequence rules share: guard, create, fill, cover.

    One copy, because the guards ARE the rule. "`MIN_PICTURES` pictures at least" and "every arrival
    is a still" are what stop a pair of files and a video's poster frames from becoming shoots, and
    a second caller that re-implemented them would be a second place for either to be forgotten.
    """
    if len(asset_ids) < MIN_PICTURES:
        return None
    stills = await content.stills_among(asset_ids)
    if len(stills) != len(asset_ids):
        return None
    photo_set = await service.create(name=name, origin=origin, origin_url=url)
    # Sift worked this grouping out, so the record says Sift rather than borrowing the
    # user whose scan happened to be running, and WHICH of its rules, by the set's own origin
    # word (`shoot`, `filename`, `stash_library`), which is a pass word (`MADE_VIAS`) for exactly this.
    await service.add(photo_set.id, stills, actor=Actor.sift(origin))
    # The first picture, unconditionally: the two guards above mean there are at least MIN_PICTURES
    # of them by the time this line is reached.
    await service.set_cover(photo_set.id, stills[0], actor=Actor.sift(origin))
    log.info(why, photo_set=photo_set.id, pictures=len(stills))
    return photo_set


async def set_from_folder(
    folder_id: str,
    *,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> str | None:
    """A folder of pictures and nothing else, as a set. The id, or None if the folder is not one.

    **The folder's own files, never its subtree.** A shoot with a video sitting in a subfolder
    beside it is still a shoot, and reading the subtree would make the answer depend on how somebody
    happened to nest their library rather than on what is in the folder.

    **One video is enough to refuse.** That is the whole discriminator between a shoot and an
    ordinary folder somebody keeps things in, and a threshold like "mostly pictures" would be a
    number nobody could predict from looking at the screen.

    Idempotent through `from_folder`, which finds the set it made last time and adds only what has
    arrived since, so this runs on every scan of the folder for ever and makes one set.
    """
    media = await content.folder_media(folder_id)
    if media.moving or len(media.still_ids) < MIN_PICTURES:
        return None
    photo_set, added = await service.from_folder(folder_id, name, media.still_ids)
    if added:
        log.info("photo_sets.derived_from_folder", photo_set=photo_set.id, pictures=added)
    return photo_set.id


async def set_from_archive(
    asset_ids: Sequence[str],
    *,
    root_id: str,
    rel_path: str,
    name: str,
    content: ContentStore,
    service: PhotoSetService,
) -> str | None:
    """One archive's pictures, as a set. The id of the set, or None if this was not one.

    **The archive itself is the grouping, so there is no rule to apply beyond the count.** A folder
    has to be interrogated (is there a video in it, is this a shoot or somebody's downloads
    directory) because a folder is a place people put unrelated things. An archive is not: it was
    made, once, by somebody who put exactly these files in it together. That is a far stronger
    statement of belonging than a folder ever makes, and second-guessing it would mean refusing a
    grouping its author already declared.

    Still every arrival must be a still, and that costs nothing here because the scan only indexes
    pictures out of an archive in the first place. Asked anyway, because this function's promise is
    about what it PRODUCES and not about who happens to call it today.

    Ordered as the archive lists them, which is the order somebody arranged the shoot in.

    **Idempotent on the archive.** Created unconditionally, an archive-derived set with no identity
    could not be recognised by the next scan, and every scan would make another beside it. It is
    keyed on the library and the path together, because two libraries can each hold a file of the
    same name.
    """
    if len(asset_ids) < MIN_PICTURES:
        return None
    stills = await content.stills_among(asset_ids)
    if len(stills) != len(asset_ids):
        return None
    photo_set, added = await service.from_archive(root_id, rel_path, name, stills)
    if added:
        log.info("photo_sets.derived_from_archive", photo_set=photo_set.id, pictures=added)
    return photo_set.id
