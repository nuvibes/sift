# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each Site's saved cookies: read back, sealed, checked against the Site, forgotten."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from sift.kernel.access import Viewer
from sift.kernel.log import get_logger
from sift.kernel.tunnels import (
    EgressRouter,
    TunnelError,
)
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.download.models import (
    ConnectionCheck,
    ConnectionItem,
    CookiePreview,
    PreviewConnectionRequest,
    SaveConnectionRequest,
    SavedConnection,
)
from sift.slices.download.router_parts import _egress, _refused, _service
from sift.slices.download.service import (
    DownloadService,
    site_name_of,
)
from sift.slices.download.sources import cookie_health
from sift.slices.download.sources.cookies import CookieInvalid, header_for, understand
from sift.slices.download.sources.net import guarded_session
from sift.slices.download.sources.sites import catalog

router = APIRouter()

log = get_logger(__name__)


@router.get("/site-connections")
async def list_connections(
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[ConnectionItem]:
    """Every Site with saved cookies: that they exist and their state, never what they are."""
    connections = await service.list_connections()
    return [
        ConnectionItem(
            id=connection.id,
            site=connection.site,
            status=connection.status,
            updated_at=connection.updated_at,
            # `cookie_health` sits below the models, so the wire narrows the plain word here.
            state=connection.state,  # type: ignore[arg-type]
            expires_at=connection.expires_at,
            expires_last=connection.expires_last,
            last_used_at=connection.last_used_at,
        )
        for connection in connections
    ]


@router.post("/site-connections/preview", dependencies=[Depends(csrf_protect)])
async def preview_connection(
    body: PreviewConnectionRequest,
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CookiePreview:
    """Read pasted cookies back before Save, by the same reading Save uses; nothing is stored."""
    record = catalog.by_site(body.site)
    try:
        _, summary = await understand(
            body.cookie, domain=record.hosts[0] if record is not None else None
        )
    except CookieInvalid as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return CookiePreview(
        cookies=summary.count,
        domains=list(summary.domains),
        expires_at=summary.expires_at,
        expires_last=summary.expires_last,
        expired=summary.expired,
    )


@router.post(
    "/site-connections",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_protect)],
)
async def save_connection(
    body: SaveConnectionRequest,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> SavedConnection:
    """Save a Site's cookies, read first and sealed under the master key."""
    if key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved cookies and tunnels are locked. Enter your password in the box at the top "
            "of this page to unlock them, then save the cookies again.",
        )
    record = catalog.by_site(body.site)
    try:
        readable, summary = await understand(
            body.cookie, domain=record.hosts[0] if record is not None else None
        )
    except CookieInvalid as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    connection_id = await service.save_connection(
        site=body.site,
        cookie=readable,
        master_key=key,
        by=viewer.id,
        expires_at=summary.expires_at,
        expires_last=summary.expires_last,
    )
    return SavedConnection(
        id=connection_id,
        cookies=summary.count,
        domains=list(summary.domains),
        expires_at=summary.expires_at,
        expires_last=summary.expires_last,
        expired=summary.expired,
    )


#: When each Site was last checked, by Site key: in memory, as a restart costs one request.
_CHECKED_AT: dict[str, float] = {}

#: A jar of cookies does not recover in under a minute.
_CHECK_EVERY_SECONDS = 60.0


def forget_checks() -> None:
    """Forget every check's timing, as a fresh process starts: for tests."""
    _CHECKED_AT.clear()


@router.post("/site-connections/{connection_id}/check", dependencies=[Depends(csrf_protect)])
async def check_connection(
    connection_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    router_: Annotated[EgressRouter, Depends(_egress)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> ConnectionCheck:
    """Ask one Site now, the way its downloads go out, whether it still accepts its cookies."""
    if key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved cookies and tunnels are locked. Enter your password in the box at the top "
            "of this page to unlock them, then check again.",
        )
    saved = await service.connection_by_id(connection_id)
    if saved is None or saved.secret_id is None or saved.site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There are no saved cookies to check.")

    record = catalog.by_site(saved.site)
    if record is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Sift has no record of {saved.site}, so it has no address to ask.",
        )
    site_key = record.key
    now = time.monotonic()
    last = _CHECKED_AT.get(site_key)
    if last is not None and now - last < _CHECK_EVERY_SECONDS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Checked a moment ago. Try again in a minute.",
        )

    jar = await service.open_cookie(saved.secret_id, key)
    if jar is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Sift could not read the cookies saved for {saved.site}. Add them again.",
        )
    host = record.hosts[0]
    try:
        header = header_for(jar, host)
    except CookieInvalid as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not header:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"None of the saved cookies are for {saved.site}. Export them again from that site.",
        )

    _CHECKED_AT[site_key] = now
    called = site_name_of(f"https://{host}/") or saved.site
    accepted = await _still_accepted(router_, f"https://{host}/", header)
    if accepted is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Sift could not reach {saved.site} just now. Try the check again in a little while.",
        )
    if accepted:
        # Released too, or the killswitch would keep the cookies off however often they work.
        await service.record_login_health(saved.site, cookie_health.SAVED)
        cookie_health.clear_for_site(saved.site)
        return ConnectionCheck(accepted=True, said=f"{called} still accepts these cookies")
    await service.record_login_health(saved.site, cookie_health.NEEDS_COOKIES)
    return ConnectionCheck(
        accepted=False,
        said=(
            f"{called} turned these cookies away. Sign in again in your browser and export a "
            "fresh file."
        ),
    )


async def _still_accepted(router_: EgressRouter, url: str, header: str) -> bool | None:
    """Whether the Site served a page to these cookies (401 and 403 are a no); None if unreached."""
    try:
        async with (
            router_.route_for(url) as proxy,
            guarded_session(proxy=proxy) as session,
            session.get(url, headers={"Cookie": header}) as response,
        ):
            return response.status not in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )
    except TunnelError as exc:
        raise _refused(exc) from exc
    except Exception as exc:
        # Nothing a network does here says anything about the cookies.
        log.info("download.cookie_check_unreachable", detail=str(exc))
        return None


@router.delete(
    "/site-connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_connection(
    connection_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Forget a Site's saved cookies and the sealed jar behind them; idempotent."""
    await service.delete_connection(connection_id, by=viewer.id)
