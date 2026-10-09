# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tags, people, sites, collections, Photo Sets, songs, Loops, usernames and search."""

from __future__ import annotations

from tests.gates.authz.seeds import (
    A_COLLECTION,
    A_FOLDER,
    A_LINK,
    A_LOOP,
    A_MERGED_SONG,
    A_PERSON,
    A_PHOTO_SET,
    A_SITE,
    A_SONG,
    A_TAG,
    A_USERNAME,
    AN_ALIAS,
    AN_ASSET,
    Case,
    Policy,
)

ROWS: dict[tuple[str, str], Case] = {
    # Tags are shared, and one can carry an access grant; listing is scoped inside.
    ("GET", "/api/tags"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/tags"): Case(Policy.ADMIN),
    ("GET", "/api/tags/{tag_id}"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("GET", "/api/tags/{tag_id}/history"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("PUT", "/api/tags/{tag_id}"): Case(Policy.ADMIN, params={"tag_id": A_TAG}),
    # "DELETE" sorts before "GET", so unmarked it would take the tag before the by-id read.
    ("DELETE", "/api/tags/{tag_id}"): Case(
        Policy.ADMIN, params={"tag_id": A_TAG}, destructive=True
    ),
    # A tag's cover is shared vocabulary, seen by everybody.
    ("PUT", "/api/tags/{tag_id}/cover"): Case(Policy.ADMIN, params={"tag_id": A_TAG}),
    ("POST", "/api/tags/{tag_id}/cover-picture"): Case(Policy.ADMIN, params={"tag_id": A_TAG}),
    # For the reason written on the person's cover.
    ("GET", "/api/tags/{tag_id}/cover"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    # Hiding is the caller's own. Scoped: an unseen tag answers as an unknown one does.
    ("PUT", "/api/tags/{tag_id}/vault"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    # An opinion is one row per user and reaches nobody else; scoped, or a write would confirm the
    # id exists.
    ("PUT", "/api/tags/{tag_id}/favorite"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("PUT", "/api/tags/{tag_id}/rating"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("PUT", "/api/tags/{tag_id}/pin"): Case(Policy.AUTHENTICATED, params={"tag_id": A_TAG}),
    ("POST", "/api/assets/tags"): Case(Policy.ADMIN),
    # A heart and a rating belong to whoever set them; scoped, or a write would confirm the id.
    ("GET", "/api/assets/{asset_id}/tags"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("PUT", "/api/assets/{asset_id}/rating"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("PUT", "/api/assets/{asset_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # The O counter: one row per user, as the heart is. One route for the three acts, a word apart.
    ("PUT", "/api/assets/{asset_id}/o-count"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}, body={"change": "up"}
    ),
    # A pin moves a file on THIS viewer's wall only. `_require_visible` also resolves the id: a
    # state row on an id that names nothing could never be cleaned up.
    ("PUT", "/api/assets/{asset_id}/pin"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}, body={"pinned": True}
    ),
    # Over a SELECTION: an asset the caller may not act on is skipped and counted, hence 200.
    ("POST", "/api/assets/rating"): Case(
        Policy.AUTHENTICATED, body={"asset_ids": [AN_ASSET], "rating": 3}
    ),
    ("POST", "/api/assets/favorite"): Case(
        Policy.AUTHENTICATED, body={"asset_ids": [AN_ASSET], "favorite": True}
    ),
    ("POST", "/api/assets/pin"): Case(
        Policy.AUTHENTICATED, body={"asset_ids": [AN_ASSET], "pinned": True}
    ),
    # People, Usernames and Sites are shared as tags are and can carry an access grant. Reading is
    # scoped inside: a vaulted person is absent from the list, the count and the resolver.
    ("GET", "/api/people"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/people"): Case(Policy.ADMIN),
    ("GET", "/api/people/{person_id}"): Case(Policy.AUTHENTICATED, params={"person_id": A_PERSON}),
    ("PUT", "/api/people/{person_id}"): Case(Policy.ADMIN, params={"person_id": A_PERSON}),
    # Hiding somebody is the viewer's own opinion, as on a site, a tag or a collection.
    ("PUT", "/api/people/{person_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    # Favouriting and rating are the viewer's own; tagging and the cover curate what everyone sees.
    # Without a body those two answer 422, past the door, so their id need not resolve.
    ("PUT", "/api/people/{person_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("PUT", "/api/people/{person_id}/rating"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("PUT", "/api/people/{person_id}/pin"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("GET", "/api/people/{person_id}/tags"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/people/{person_id}/tags"): Case(Policy.ADMIN, params={"person_id": A_PERSON}),
    ("PUT", "/api/people/{person_id}/cover"): Case(Policy.ADMIN, params={"person_id": A_PERSON}),
    # The only route family where a stranger's bytes are the request body; the entity is resolved
    # against the viewer before a byte is read.
    ("POST", "/api/people/{person_id}/cover-picture"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON}
    ),
    # The person's cover: a cover is a fragment of a file, so `serve_cover` reads through the scoped
    # repository and a cover on something unseen is a 404. All six covers answer this way.
    ("GET", "/api/people/{person_id}/cover"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("GET", "/api/people/resolve"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/people/{person_id}/links"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/people/{person_id}/links"): Case(
        Policy.ADMIN,
        params={"person_id": A_PERSON},
    ),
    # Not destructive: it removes one row nothing else names, and the person delete cascades it.
    ("DELETE", "/api/people/{person_id}/links/{link_id}"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON, "link_id": A_LINK}
    ),
    ("GET", "/api/people/{person_id}/history"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    # The sharing half is about USERS, and the kernel leaves it out for anybody but an admin.
    ("GET", "/api/sites/{site_id}/history"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("GET", "/api/people/{person_id}/aliases"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("POST", "/api/people/{person_id}/aliases"): Case(Policy.ADMIN, params={"person_id": A_PERSON}),
    # Details is part of a person's record; writing it is on the person route.
    ("GET", "/api/people/{person_id}/notes"): Case(
        Policy.AUTHENTICATED, params={"person_id": A_PERSON}
    ),
    ("DELETE", "/api/people/{person_id}/aliases/{alias_id}"): Case(
        Policy.ADMIN, params={"person_id": A_PERSON, "alias_id": AN_ALIAS}
    ),
    ("GET", "/api/assets/{asset_id}/people"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("POST", "/api/assets/people"): Case(Policy.ADMIN),
    ("POST", "/api/assets/sites"): Case(Policy.ADMIN),
    # Where a file came from is part of it; taking off a filing that is not there is a 204.
    ("GET", "/api/assets/{asset_id}/filings"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    ("DELETE", "/api/assets/{asset_id}/filings/{username_id}"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET, "username_id": A_USERNAME}
    ),
    # What a field IS, never a value, so there is nothing to scope.
    ("GET", "/api/records/fields"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/collections"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/collections"): Case(Policy.ADMIN),
    ("GET", "/api/collections/{collection_id}"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("GET", "/api/collections/{collection_id}/history"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("GET", "/api/collections/{collection_id}/made-by"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/rating"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/pin"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("GET", "/api/collections/{collection_id}/tags"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("POST", "/api/collections/{collection_id}/tags"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    ("GET", "/api/collections/{collection_id}/items"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("POST", "/api/collections/{collection_id}/items"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    # For the reason written on the person's cover.
    ("GET", "/api/collections/{collection_id}/cover"): Case(
        Policy.AUTHENTICATED, params={"collection_id": A_COLLECTION}
    ),
    ("PUT", "/api/collections/{collection_id}/cover"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    ("POST", "/api/collections/{collection_id}/cover-picture"): Case(
        Policy.ADMIN, params={"collection_id": A_COLLECTION}
    ),
    # --- photo sets: what the library HOLDS is an admin's, what one user THINKS of it is theirs.
    ("GET", "/api/photo-sets"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/photo-sets"): Case(Policy.ADMIN),
    # Deriving a set from a folder writes a grouping over the library.
    ("GET", "/api/photo-sets/{photo_set_id}"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("GET", "/api/photo-sets/{photo_set_id}/history"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("GET", "/api/photo-sets/{photo_set_id}/made-by"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/notes"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    # For the reason written on the person's cover.
    ("GET", "/api/photo-sets/{photo_set_id}/cover"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/cover"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("POST", "/api/photo-sets/{photo_set_id}/cover-picture"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    # The vault, the heart and the stars are one row per user.
    ("PUT", "/api/photo-sets/{photo_set_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/rating"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("PUT", "/api/photo-sets/{photo_set_id}/pin"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("POST", "/api/photo-sets/{photo_set_id}/items"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("GET", "/api/photo-sets/{photo_set_id}/tags"): Case(
        Policy.AUTHENTICATED, params={"photo_set_id": A_PHOTO_SET}
    ),
    ("POST", "/api/photo-sets/{photo_set_id}/tags"): Case(
        Policy.ADMIN, params={"photo_set_id": A_PHOTO_SET}
    ),
    # --- songs: the photo sets' split. No vault grant on a song: its files decide who sees it.
    ("GET", "/api/songs"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/songs/facets"): Case(Policy.AUTHENTICATED, query={"facet": "created"}),
    ("POST", "/api/songs"): Case(Policy.ADMIN),
    ("GET", "/api/songs/{song_id}"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("GET", "/api/songs/{song_id}/history"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("GET", "/api/songs/{song_id}/made-by"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/notes"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("GET", "/api/songs/{song_id}/cover"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/cover"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("POST", "/api/songs/{song_id}/cover-picture"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"song_id": A_SONG}
    ),
    ("PUT", "/api/songs/{song_id}/rating"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/pin"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    ("POST", "/api/songs/{song_id}/files"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("PUT", "/api/songs/{song_id}/vault"): Case(Policy.AUTHENTICATED, params={"song_id": A_SONG}),
    # Shared rows.
    ("PUT", "/api/songs/{song_id}/artists"): Case(Policy.ADMIN, params={"song_id": A_SONG}),
    ("PUT", "/api/artists/{artist_id}"): Case(Policy.ADMIN, params={"artist_id": A_SONG}),
    # Writes nothing, but only an admin may then merge.
    ("POST", "/api/songs/weigh-merge"): Case(
        Policy.ADMIN, body={"into": A_SONG, "songs": [A_MERGED_SONG]}
    ),
    # --- loops. Making one is not an admin's: it points at one file the user can already see.
    # Moving or removing one is its maker's or an admin's; the seeded Loops are ownerless.
    ("GET", "/api/loops"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/loops"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/loops/{loop_id}"): Case(Policy.AUTHENTICATED, params={"loop_id": A_LOOP}),
    # "Yours, or an admin's" is asked inside the handler, which runs only once the body parses.
    ("PUT", "/api/loops/{loop_id}"): Case(Policy.ADMIN, params={"loop_id": A_LOOP}, body={}),
    # The Loop is resolved through the visible set before a picture is looked for.
    ("GET", "/api/loops/{loop_id}/thumb"): Case(Policy.AUTHENTICATED, params={"loop_id": A_LOOP}),
    ("GET", "/api/loops/{loop_id}/tags"): Case(Policy.AUTHENTICATED, params={"loop_id": A_LOOP}),
    # A tag is shared vocabulary, whoever made the Loop.
    ("POST", "/api/loops/{loop_id}/tags"): Case(Policy.ADMIN, params={"loop_id": A_LOOP}),
    # --- tab strip numbers, from the same scoped listing as the wall: a guest gets noughts, not
    # the size of what is kept back.
    ("GET", "/api/related/{kind}/{entity_id}"): Case(
        Policy.AUTHENTICATED, params={"kind": "person", "entity_id": A_PERSON}
    ),
    # The vault. Shutting it is open to everybody, so a lock trigger is never refused; opening it
    # reveals only what that user hid.
    ("GET", "/api/vault"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/vault/unlock"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/vault/lock"): Case(Policy.AUTHENTICATED),
    ("PUT", "/api/assets/{asset_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"asset_id": AN_ASSET}
    ),
    # Hiding is per user; the way IN needs a PIN the seeded users do not hold.
    ("POST", "/api/assets/vault"): Case(
        Policy.AUTHENTICATED, body={"asset_ids": [AN_ASSET], "vault": False}
    ),
    ("PUT", "/api/folders/{folder_id}/vault"): Case(
        Policy.AUTHENTICATED, params={"folder_id": A_FOLDER}
    ),
    # Usernames: reading is scoped in the statement; saying who one belongs to decides whose name
    # is indexed on every file under it.
    ("GET", "/api/usernames"): Case(Policy.AUTHENTICATED),
    ("PUT", "/api/usernames/{username_id}"): Case(Policy.ADMIN, params={"username_id": A_USERNAME}),
    ("POST", "/api/usernames/{username_id}/person"): Case(
        Policy.ADMIN,
        params={"username_id": A_USERNAME},
        body={"person_id": A_PERSON},
    ),
    ("DELETE", "/api/usernames/{username_id}/person"): Case(
        Policy.ADMIN, params={"username_id": A_USERNAME}, destructive=True
    ),
    ("GET", "/api/sites"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/sites"): Case(Policy.ADMIN),
    ("GET", "/api/sites/{site_id}"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}/vault"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    # The person's split: favorite and rating are the viewer's own; tags, cover and details curate.
    ("PUT", "/api/sites/{site_id}/favorite"): Case(
        Policy.AUTHENTICATED, params={"site_id": A_SITE}
    ),
    ("PUT", "/api/sites/{site_id}/rating"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}/pin"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("GET", "/api/sites/{site_id}/tags"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("POST", "/api/sites/{site_id}/tags"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    # For the reason written on the person's cover.
    ("GET", "/api/sites/{site_id}/cover"): Case(Policy.AUTHENTICATED, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}/cover"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    ("POST", "/api/sites/{site_id}/cover-picture"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    ("PUT", "/api/sites/{site_id}/details"): Case(Policy.ADMIN, params={"site_id": A_SITE}),
    # The pack that ships with Sift holds nothing of the library. `onlyfans` is in the pack, so the
    # answer is a 200 worth asserting.
    ("GET", "/api/sites/icons/{slug}"): Case(Policy.AUTHENTICATED, params={"slug": "onlyfans"}),
    ("GET", "/api/sites/icons/for"): Case(Policy.AUTHENTICATED, query={"host": "onlyfans.com"}),
    # Search is scoped inside the server. A search box takes a GUESS, so three channels stay shut
    # for a guest: the results, the COUNT, and the SUGGESTIONS. Results come from the grid's own
    # address. The history is filtered by the asking user, so no id can name another's row.
    ("GET", "/api/search/suggest"): Case(Policy.AUTHENTICATED),
    # Reads no rows; still not a parser left open to anybody.
    ("GET", "/api/search/parse"): Case(Policy.AUTHENTICATED),
    # Reads the same suggesters the dropdown does.
    ("GET", "/api/search/named"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/search/names-now"): Case(Policy.AUTHENTICATED),
    # A guest keeps their own recent searches. A POST, so another site cannot land an entry.
    ("POST", "/api/search/history"): Case(Policy.AUTHENTICATED),
    ("DELETE", "/api/search/history"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/search/opened"): Case(Policy.AUTHENTICATED),
    # The viewer's own; a guessed id reaches no row, so the delete answers 204.
    ("GET", "/api/search/saved"): Case(Policy.AUTHENTICATED),
    ("POST", "/api/search/saved"): Case(Policy.AUTHENTICATED),
    ("DELETE", "/api/search/saved/{saved_id}"): Case(
        Policy.AUTHENTICATED, params={"saved_id": "01HX0000000000000000000091"}
    ),
    # A rename must say whether it happened, and "no such search of yours" confirms nothing else.
    ("PATCH", "/api/search/saved/{saved_id}"): Case(
        Policy.AUTHENTICATED, params={"saved_id": "01HX0000000000000000000091"}
    ),
}
