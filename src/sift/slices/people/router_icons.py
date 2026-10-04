# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Site's icon from the installed pack, by its link or by its own name."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from sift.kernel.access import (
    Viewer,
)
from sift.kernel.serving import KEEPABLE, serve_file
from sift.kernel.site_icons import host_of as site_icon_host
from sift.kernel.site_icons import path_of as site_icon_path
from sift.kernel.site_icons import slug_for as site_icon_slug
from sift.slices.auth import current_viewer
from sift.slices.people.router_base import _NOT_FOUND

router = APIRouter(tags=["people"])

# --- the site icons that ship with Sift -----------------------------------------------------


#: The longest host name DNS allows. A host longer than this is not a host, and the query is refused
#: before it is looked at rather than walked label by label.
_LONGEST_HOST = 253


def _pack_slug_for_link(host: str) -> str | None:
    """The shipped logo for the site an ADDRESS is on: its exact host, then each parent domain."""
    try:
        labels = site_icon_host(host).split(".")
    except ValueError:  # an unclosed `[` reads as a broken IPv6 literal: no host, so no logo
        return None
    for start in range(max(len(labels) - 1, 0)):
        found = site_icon_slug(".".join(labels[start:]))
        if found is not None:
            return found
    return None


# Declared BEFORE `/sites/icons/{slug}`, and it has to be: routes are matched in the order they are
# declared, and the slug route would otherwise take `for` as a slug and answer 404.
@router.get("/sites/icons/for")
async def site_icon_for_link(
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    host: Annotated[str, Query(min_length=1, max_length=_LONGEST_HOST)],
) -> Response:
    """The shipped logo for the site a link is on, by the link's host, or 404 where there is none.

    **The host and not the address.**
    """
    del viewer  # signed in is the whole of the check; nothing here is scoped to who is asking.
    slug = _pack_slug_for_link(host)
    path = site_icon_path(slug) if slug is not None else None
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND, headers=KEEPABLE)
    return await serve_file(request, path, media_type="image/png", headers=KEEPABLE)


@router.get("/sites/icons/{slug}")
async def site_icon(
    slug: str,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """One site's logo out of the pack that ships with Sift.

    **The slug is compared against the manifest before it is part of a path**
    """
    del viewer  # signed in is the whole of the check; nothing here is scoped to who is asking.
    path = site_icon_path(slug)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return await serve_file(request, path, media_type="image/png", headers=KEEPABLE)
