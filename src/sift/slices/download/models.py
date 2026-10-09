# SPDX-License-Identifier: AGPL-3.0-or-later
"""The request and response shapes for the download endpoints.

Cookies are only accepted, never returned: no response has a field for them."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.wire import Wire

#: Closed: an unknown word would be a download that silently goes nowhere.
AimedAt = Literal["person", "site", "collection", "tag", "photo_set", "song", "favorite"]


class SubmitDownloadRequest(Wire):
    """Ask for a URL to be downloaded, optionally into a particular folder or onto something."""

    url: str = Field(min_length=1, max_length=4096)
    dest_folder_id: str | None = None
    #: Both halves or neither: either alone would fail silently inside a job.
    aimed_kind: AimedAt | None = None
    aimed_id: str | None = None
    remember: bool | None = None


class SubmittedDownload(Wire):
    """The id of the ledger row a submitted download was recorded as."""

    id: str


MAX_PASTED_LINKS = 500

#: Lines past `MAX_PASTED_LINKS` are handed back, not refused.
MAX_PASTED_LINES = 5000


class PasteLinksRequest(Wire):
    """Several addresses together; its own route, since a paste names what it refused."""

    urls: list[str] = Field(min_length=1, max_length=MAX_PASTED_LINES)
    dest_folder_id: str | None = None
    remember: bool | None = None


class RefusedLink(Wire):
    """One line of a paste that was not queued, and what was wrong with it."""

    url: str
    reason: str


class PastedLinks(Wire):
    """What a paste of several links did: one bad line never loses the rest."""

    queued: int
    refused: list[RefusedLink] = Field(default_factory=list)
    duplicates: int = 0
    left_over: list[str] = Field(default_factory=list)


class BulkRequest(Wire):
    """A playlist or channel address, and where its videos should land."""

    url: str = Field(min_length=1, max_length=4096)
    dest_folder_id: str | None = None
    remember: bool | None = None


class BulkPreview(Wire):
    """What a playlist or channel holds, asked before anything is queued."""

    site: str
    count: int
    truncated: bool
    limit: int


class BulkQueued(Wire):
    """How many downloads a confirmed bulk request actually queued."""

    queued: int


class SupportedSite(Wire):
    """One site Sift knows, as the reference table shows it."""

    key: str
    name: str
    hosts: list[str]
    media: list[str]
    bulk: bool
    supported: bool = True
    tested: bool
    names_creators: bool
    default_naming: str
    name_words: list[str]
    walls: list[str] = Field(default=[])
    cookies: Literal["required", "partial", "not_needed"]
    cookies_why: str
    cookies_with_a_tool: Literal["required", "partial", "not_needed"] | None = None


class DownloadFolder(Wire):
    """The folder a download goes into, by name and by path on disk."""

    id: str
    name: str
    path: str


class DownloadItem(Wire):
    """One download as the queue screen sees it; the address, never a hash anyone could match."""

    id: str
    status: str
    dest_folder_id: str | None
    site: str | None
    username: str | None
    asset_id: str | None
    error: str | None
    created_at: int
    error_code: str | None = None
    error_tier: int | None = None
    site_key: str | None = None
    via: str | None = None
    via_address: str | None = None
    site_name: str | None = None
    creator_scope: str | None = None
    #: Exactly as pasted: what a link on the row points at.
    url: str | None = None
    #: Signed-link expiry and signature off: what the row says, never a link target.
    shown_url: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    remembered_filename: str | None = None
    finished_at: int | None = None
    site_id: str | None = None
    person_id: str | None = None
    progress: DownloadProgress | None = None
    sentence: str | None = None
    job_id: str | None = None
    position: int | None = None
    #: The folder the job resolved, so a later setting does not move a running row.
    folder: DownloadFolder | None = None
    files_offered: int | None = None
    files_left_out: int | None = None
    reads_refused: str | None = None


class DownloadProgress(Wire):
    """How far along a running download is; absent, never zero, when unknown."""

    done_bytes: int
    total_bytes: int | None = None
    total_is_estimated: bool = False
    done_files: int = 0
    total_files: int | None = None
    bytes_per_second: float | None = None
    seconds_left: float | None = None


class QueueSummary(Wire):
    """How the queue as a whole is doing, for the strip above the list."""

    running: int
    queued: int
    bytes_per_second: float
    seconds_left: float | None = None
    by_state: dict[str, int] = Field(default_factory=dict)


class DownloadsAtAGlance(Wire):
    """What the Downloads row on the rail says, from the download rows themselves."""

    downloading: int
    waiting_for_cookies: int
    landed_unseen: int
    failed_unseen: int


class CreatorsWithArt(Wire):
    """The usernames Sift has a creator picture for. Asked once by a screen full of People."""

    usernames: list[str] = Field(default=[])


class DownloadFile(Wire):
    """One file a download produced, for a row that produced more than one."""

    asset_id: str
    filename: str | None = None
    size_bytes: int | None = None


class DownloadFiles(Wire):
    """Everything one paste put in the library, in the order it landed."""

    files: list[DownloadFile] = Field(default=[])


class DownloadSiteCount(Wire):
    """One Site the queue holds downloads from, and how many: an entry in the Site menu."""

    name: str
    count: int


class DownloadsPage(Wire):
    downloads: list[DownloadItem]
    total: int
    summary: QueueSummary | None = None
    matched: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    sites: list[DownloadSiteCount] = Field(default_factory=list)


#: `none` is never returned: the client's word for a listed Site without Cookies.
ConnectionState = Literal["saved", "ending_soon", "expired", "none"]


class SaveConnectionRequest(Wire):
    """Save a site's cookies. They are write-only: taken here, never returned anywhere."""

    site: str = Field(min_length=1, max_length=100)
    cookie: str = Field(min_length=1, max_length=100_000)


class PreviewConnectionRequest(Wire):
    """Read cookies back to whoever pasted them, without saving anything."""

    site: str = Field(min_length=1, max_length=100)
    cookie: str = Field(min_length=1, max_length=100_000)


class CookiePreview(Wire):
    """What Sift understood of pasted cookies, before anything is saved."""

    cookies: int
    domains: list[str]
    expires_at: int | None = None
    expires_last: int | None = None
    expired: bool = False


class ConnectionCheck(Wire):
    """What one site said when asked whether the saved cookies still work."""

    accepted: bool
    said: str


class SavedConnection(Wire):
    """A site's saved cookies as derived facts; nothing saved is returned."""

    id: str
    cookies: int
    domains: list[str]
    expires_at: int | None = None
    expires_last: int | None = None
    expired: bool = False


class ConnectionItem(Wire):
    """One site's saved cookies as a screen sees it: that they exist, never what they are."""

    id: str
    site: str
    status: str | None
    updated_at: int | None
    state: ConnectionState = "saved"
    #: The date shown is the last: the first is usually a reissued clearance cookie.
    expires_at: int | None = None
    expires_last: int | None = None
    last_used_at: int | None = None


class ImportTunnelRequest(Wire):
    """A provider's WireGuard configuration and its name; sealed on arrival, never returned."""

    name: str = Field(min_length=1, max_length=80)
    config: str = Field(min_length=1, max_length=65536)


class ReplaceTunnelConfigRequest(Wire):
    """A reissued configuration for a tunnel that already exists."""

    config: str = Field(min_length=1, max_length=65536)


class RenameTunnelRequest(Wire):
    name: str = Field(min_length=1, max_length=80)


class StopTunnelRequest(Wire):
    """Whether to stop a tunnel now or let the downloads on it finish first."""

    now: bool = False


class ImportedTunnel(Wire):
    id: str


class TunnelItem(Wire):
    """One tunnel as the settings screen sees it; no public key, which names the account."""

    id: str
    name: str
    enabled: bool
    running: bool
    up: bool
    draining: bool
    last_handshake_at: int | None = None
    endpoint: str | None = None
    problem: str | None = None
    can_host: bool | None = None


class RouteRequest(Wire):
    """Where one site's downloads go out: the word for the machine's own address, or a tunnel id."""

    route: str = Field(min_length=1, max_length=64)


class SiteChoice(Wire):
    """A site a route can be set on: the key it is stored under, and its name on screen."""

    key: str
    name: str


class RoutesResponse(Wire):
    """The default route, the sites given their own, and the sites that can be chosen."""

    default: str
    sites: dict[str, str]
    available: list[SiteChoice]


class SiteOptionItem(Wire):
    """What one site does differently, as the settings table shows it."""

    scope: str
    naming: str | None = None
    dest_folder_id: str | None = None
    downloader: str | None = None


class DownloaderChoice(Wire):
    """One tool a Site can be pointed at, and what to call it on the screen."""

    value: str
    label: str
    help: str


class SiteOptionsResponse(Wire):
    """The answers everything follows, the sites given their own, and the template tokens."""

    default: SiteOptionItem
    sites: list[SiteOptionItem]
    tokens: dict[str, str]
    downloaders: list[DownloaderChoice]


class SetSiteOptionsRequest(Wire):
    """What one site (or everything) should do; absent is no opinion, empty keeps the name."""

    naming: str | None = Field(default=None, max_length=200)
    dest_folder_id: str | None = None
    downloader: str | None = Field(default=None, max_length=32)


class NamePreviewRequest(Wire):
    """A template, and optionally the Site to imagine it against."""

    naming: str | None = Field(default=None, max_length=200)
    scope: str | None = Field(default=None, max_length=64)


class NamePreview(Wire):
    """What a template would produce; empty means the file keeps its name."""

    example: str
