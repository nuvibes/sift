# SPDX-License-Identifier: AGPL-3.0-or-later
"""The downloader, the stash-boxes, site connections, tunnels and capture."""

from __future__ import annotations

from tests.gates.authz.seeds import (
    A_DELETABLE_STASH_BOX,
    A_LINKED_PERSON,
    A_PERSON,
    A_SITE,
    A_STASH_BOX,
    AN_ASSET,
    ART_CREATOR,
    MERGE_FROM,
    MERGE_INTO,
    SITE_MERGE_FROM,
    SITE_MERGE_INTO,
    Case,
    Policy,
)

ROWS: dict[tuple[str, str], Case] = {
    # The downloader, admin permanently: it reaches the internet and holds the site logins.
    ("POST", "/api/downloads"): Case(Policy.ADMIN),
    ("GET", "/api/downloads"): Case(Policy.ADMIN),
    ("GET", "/api/downloads/facets"): Case(Policy.ADMIN, query={"facet": "site"}),
    ("GET", "/api/downloads/glance"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/seen"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/{download_id}/cancel"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/anyway"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    # A site Sift does not take whole answers 400, past the door.
    ("POST", "/api/downloads/bulk-preview"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/bulk"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/links"): Case(Policy.ADMIN),
    ("POST", "/api/downloads/{download_id}/retry"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/first"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/remove"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    # The id resolves to nothing: pause and resume answer 409, and the restore's honest 404 is
    # declared with `answers_anyway`.
    ("POST", "/api/downloads/{download_id}/pause"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/resume"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/downloads/{download_id}/restore"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}, answers_anyway=404
    ),
    ("GET", "/api/supported-sites"): Case(Policy.ADMIN),
    ("GET", "/api/download-tools"): Case(Policy.ADMIN),
    # The one route here that opens a connection out; without a body an admin reaches a 422.
    ("POST", "/api/download-tools/latest"): Case(Policy.ADMIN),
    # Changes what every download on this install produces.
    ("GET", "/api/site-options"): Case(Policy.ADMIN),
    ("PUT", "/api/site-options/{scope}"): Case(Policy.ADMIN, params={"scope": "youtube"}),
    ("DELETE", "/api/site-options/{scope}"): Case(Policy.ADMIN, params={"scope": "youtube"}),
    ("POST", "/api/site-options/preview"): Case(Policy.ADMIN),
    # A name Sift holds a picture for is a name somebody downloaded from.
    ("GET", "/api/creator-art"): Case(Policy.ADMIN),
    ("GET", "/api/creator-art/{username}"): Case(Policy.ADMIN, params={"username": ART_CREATOR}),
    # The names still come through the access layer.
    ("GET", "/api/downloads/{download_id}/files"): Case(
        Policy.ADMIN, params={"download_id": "01HX0000000000000000000009"}
    ),
    # The stash-boxes, reads included: these routes make Sift ask somebody else's service with an
    # admin's own key. Nothing here is destructive: no other route reads a keyless box's row.
    ("GET", "/api/stash-boxes"): Case(Policy.ADMIN),
    ("POST", "/api/stash-boxes"): Case(
        Policy.ADMIN, body={"name": "A box", "endpoint": "https://example.invalid/graphql"}
    ),
    ("PUT", "/api/stash-boxes/{box_id}"): Case(
        Policy.ADMIN, params={"box_id": A_STASH_BOX}, body={"enabled": True}
    ),
    ("DELETE", "/api/stash-boxes/{box_id}"): Case(
        Policy.ADMIN, params={"box_id": A_DELETABLE_STASH_BOX}
    ),
    ("PUT", "/api/stash-boxes/{box_id}/key"): Case(
        Policy.ADMIN, params={"box_id": A_STASH_BOX}, body={"api_key": "not-a-key"}
    ),
    ("DELETE", "/api/stash-boxes/{box_id}/answers"): Case(
        Policy.ADMIN, params={"box_id": A_STASH_BOX}
    ),
    ("POST", "/api/stash-boxes/{box_id}/check"): Case(Policy.ADMIN, params={"box_id": A_STASH_BOX}),
    # Served from Sift's own address because the pages run under `img-src 'self'`.
    ("GET", "/api/stash-boxes/{box_id}/picture"): Case(
        Policy.ADMIN,
        params={"box_id": A_STASH_BOX},
        query={"url": "https://stash-box.invalid/img/1"},
        # `.invalid` resolves nowhere, so an admin is honestly told there is no picture.
        answers_anyway=404,
    ),
    # The address comes from Sift's copy of what the box said; the seeded subject is not linked to
    # the seeded box, so an admin's honest answer is 404.
    ("POST", "/api/stash-boxes/{box_id}/picture/keep"): Case(
        Policy.ADMIN,
        params={"box_id": A_STASH_BOX},
        body={"subject": "person", "local_id": MERGE_FROM},
        answers_anyway=404,
    ),
    # There is no `GET /api/stash-boxes/unasked`: the two rows below are what the screen reads.
    ("POST", "/api/stash-boxes/enrich"): Case(
        Policy.ADMIN,
        body={"subject": "person", "ids": [MERGE_FROM]},
        # Queued, and no box is switched on in this sweep, so nothing is asked of anybody.
    ),
    # No box is switched on in this sweep, so the look-ups answer an empty list.
    ("GET", "/api/stash-boxes/look-up"): Case(Policy.ADMIN, query={"term": "nobody"}),
    ("GET", "/api/stash-boxes/recognise/{asset_id}"): Case(
        Policy.ADMIN, params={"asset_id": AN_ASSET}
    ),
    # Signed-in: no network, no key, Sift's own tables, and every row resolved through the access
    # layer. The totals are unscoped, as the reclaim list's are.
    ("GET", "/api/stash-boxes/linked"): Case(Policy.AUTHENTICATED),
    ("GET", "/api/stash-boxes/undecided"): Case(Policy.AUTHENTICATED),
    # Either answer moves a Site's files for everybody; the seeded Site is no question, so 404.
    ("GET", "/api/stash-boxes/creator-sites"): Case(Policy.ADMIN),
    ("POST", "/api/stash-boxes/creator-sites/{site_id}/username"): Case(
        Policy.ADMIN, params={"site_id": A_SITE}, answers_anyway=404
    ),
    ("POST", "/api/stash-boxes/creator-sites/{site_id}/site"): Case(
        Policy.ADMIN, params={"site_id": A_SITE}, answers_anyway=404
    ),
    # The one stash-box route a guest may call: no network, no key, and the subject is resolved
    # through the access layer first.
    ("GET", "/api/stash-boxes/links/{subject}/{local_id}"): Case(
        Policy.AUTHENTICATED, params={"subject": "person", "local_id": A_PERSON}
    ),
    # Reading is a guest's as the links are; changing it is a decision every pass reads.
    ("GET", "/api/stash-boxes/enrichment/{subject}/{local_id}"): Case(
        Policy.AUTHENTICATED, params={"subject": "person", "local_id": A_PERSON}
    ),
    ("PUT", "/api/stash-boxes/enrichment/{subject}/{local_id}/keep-local"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_PERSON},
        body={"kept_local": True},
    ),
    # Searching, linking and refreshing spend an admin's key; unlinking edits a record.
    ("GET", "/api/stash-boxes/search/{subject}"): Case(
        Policy.ADMIN, params={"subject": "person"}, query={"term": "nobody"}
    ),
    ("PUT", "/api/stash-boxes/links/{subject}/{local_id}/{box_id}"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_LINKED_PERSON, "box_id": A_STASH_BOX},
        body={"remote_id": "not-a-real-id"},
    ),
    ("POST", "/api/stash-boxes/links/{subject}/{local_id}/{box_id}/refresh"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_LINKED_PERSON, "box_id": A_STASH_BOX},
    ),
    ("GET", "/api/stash-boxes/record/{subject}/{local_id}"): Case(
        Policy.ADMIN, params={"subject": "person", "local_id": A_PERSON}
    ),
    ("POST", "/api/stash-boxes/links/{subject}/{local_id}/{box_id}/take"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_LINKED_PERSON, "box_id": A_STASH_BOX},
        body={"keys": []},
    ),
    # Removes a row a later case reads.
    ("DELETE", "/api/stash-boxes/links/{subject}/{local_id}/{box_id}"): Case(
        Policy.ADMIN,
        params={"subject": "person", "local_id": A_LINKED_PERSON, "box_id": A_STASH_BOX},
        destructive=True,
    ),
    # The bulk pass spends an admin's key. Starting a scan answers 409 while the feature is off, as
    # it is on a fresh application; turning it on here would arrange the thing being measured.
    ("POST", "/api/stash-boxes/scan"): Case(Policy.ADMIN, answers_anyway=409),
    ("GET", "/api/stash-boxes/matches"): Case(Policy.ADMIN),
    ("POST", "/api/stash-boxes/matches/apply"): Case(Policy.ADMIN, body={"matches": []}),
    ("POST", "/api/stash-boxes/matches/refuse"): Case(Policy.ADMIN, body={"matches": []}),
    # Folding people who are one: the weighing, then the merge. The merge is destructive and removes
    # one of the pair, so the weighing must be asked first.
    ("POST", "/api/people/weigh-merge"): Case(
        Policy.ADMIN, body={"into": MERGE_INTO, "people": [MERGE_FROM, MERGE_INTO]}
    ),
    ("POST", "/api/people/merge"): Case(
        Policy.ADMIN,
        body={"into": MERGE_INTO, "people": [MERGE_FROM, MERGE_INTO]},
        destructive=True,
    ),
    # The same two acts for SITES. The weighing answers with counts about sites somebody may not
    # see, and a count is a fact about a library.
    ("POST", "/api/sites/weigh-merge"): Case(
        Policy.ADMIN,
        body={"into": SITE_MERGE_INTO, "sites": [SITE_MERGE_FROM, SITE_MERGE_INTO]},
    ),
    ("POST", "/api/sites/merge"): Case(
        Policy.ADMIN,
        body={"into": SITE_MERGE_INTO, "sites": [SITE_MERGE_FROM, SITE_MERGE_INTO]},
        destructive=True,
    ),
    ("GET", "/api/stash-boxes/disagreements"): Case(Policy.ADMIN),
    # A subject nothing is linked to and one that is not there answer the same empty list.
    ("GET", "/api/stash-boxes/disagreements/{subject}/{local_id}"): Case(
        Policy.ADMIN, params={"subject": "person", "local_id": A_PERSON}
    ),
    # The route looks the row up again rather than trusting the body, so on a fresh library an
    # admin's honest answer is 404.
    ("POST", "/api/stash-boxes/disagreements/settle"): Case(
        Policy.ADMIN,
        body={
            "subject": "person",
            "local_id": A_PERSON,
            "key": "name",
            "box_id": A_STASH_BOX,
            "take_theirs": False,
        },
        answers_anyway=404,
    ),
    ("GET", "/api/site-connections"): Case(Policy.ADMIN),
    ("POST", "/api/site-connections"): Case(Policy.ADMIN),
    # The cookies are an admin's and the answer describes them.
    ("POST", "/api/site-connections/preview"): Case(Policy.ADMIN),
    # Reads a sealed secret and sends a request to somebody else's site.
    ("POST", "/api/site-connections/{connection_id}/check"): Case(
        Policy.ADMIN, params={"connection_id": "01HX0000000000000000000009"}
    ),
    ("DELETE", "/api/site-connections/{connection_id}"): Case(
        Policy.ADMIN, params={"connection_id": "01HX0000000000000000000009"}
    ),
    # A tunnel holds a VPN provider's private key; which sites avoid the machine's own address is a
    # picture of what somebody is careful about.
    ("GET", "/api/tunnels"): Case(Policy.ADMIN),
    ("POST", "/api/tunnels"): Case(Policy.ADMIN),
    ("PATCH", "/api/tunnels/{tunnel_id}"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/tunnels/{tunnel_id}/config"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/tunnels/{tunnel_id}/start"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("POST", "/api/tunnels/{tunnel_id}/stop"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("DELETE", "/api/tunnels/{tunnel_id}"): Case(
        Policy.ADMIN, params={"tunnel_id": "01HX0000000000000000000009"}
    ),
    ("GET", "/api/download-routes"): Case(Policy.ADMIN),
    ("PUT", "/api/download-routes/{scope}"): Case(Policy.ADMIN, params={"scope": "reddit"}),
    ("DELETE", "/api/download-routes/{scope}"): Case(Policy.ADMIN, params={"scope": "reddit"}),
    # Adding content decides what sits in the library, and a link reaches the downloader.
    ("POST", "/api/capture/import/file"): Case(Policy.ADMIN),
    ("POST", "/api/capture/import/url"): Case(Policy.ADMIN),
    ("POST", "/api/capture/import/clipboard"): Case(Policy.ADMIN),
}
