# SPDX-License-Identifier: AGPL-3.0-or-later
"""The request and response shapes for the download endpoints.

Three things are load-bearing. Cookies are only accepted, never returned: no response has a field
for them. A download's `status` is the display status the service computed (the ledger state with
the job's blocked or failed state folded in), since "waiting for cookies" is no ledger state. And
every word says cookies, never a login: Sift holds only an exported jar, and "login" would send
people looking for a sign-in form that does not exist.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.wire import Wire

#: What a link can be dropped ON: six named things and the heart. Closed, because the job that files
#: the result reads it back, and an unknown word would be a download that silently goes nowhere.
#: No usernames: a Person is the identity.
AimedAt = Literal["person", "site", "collection", "tag", "photo_set", "song", "favorite"]


class SubmitDownloadRequest(Wire):
    """Ask for a URL to be downloaded, optionally into a particular folder or onto something."""

    url: str = Field(min_length=1, max_length=4096)
    dest_folder_id: str | None = None
    #: What this link was DROPPED on: both halves or neither (the heart carries no id), refused
    #: apart by the route, since either half alone would fail silently later inside a job.
    aimed_kind: AimedAt | None = None
    aimed_id: str | None = None
    #: This paste's own answer to whether a link already downloaded is skipped, kept on its row
    #: without touching the setting; absent follows the setting when it runs. See
    #: `service.PasteChoices`.
    remember: bool | None = None


class SubmittedDownload(Wire):
    """The id of the ledger row a submitted download was recorded as."""

    id: str


#: How many links one paste may QUEUE: the whole-profile cap, a floor under how badly a paste of the
#: wrong thing can go.
MAX_PASTED_LINKS = 500

#: How many lines one paste may carry, much larger: a long paste is taken, the first
#: `MAX_PASTED_LINKS` queued and the rest handed back, rather than refused whole by the validator.
#: Still a ceiling on the request body.
MAX_PASTED_LINES = 5000


class PasteLinksRequest(Wire):
    """Several addresses together, and where they should land.

    Its own route: a paste of many must say what it refused and took, which one ledger row's id
    cannot.
    """

    urls: list[str] = Field(min_length=1, max_length=MAX_PASTED_LINES)
    dest_folder_id: str | None = None
    #: This paste's own answer to whether a link already downloaded is skipped, for every link.
    remember: bool | None = None


class RefusedLink(Wire):
    """One line of a paste that was not queued, and what was wrong with it."""

    url: str
    reason: str


class PastedLinks(Wire):
    """What a paste of several links did.

    One bad line never loses the rest: what could be queued is, and each refused line is named.
    """

    queued: int
    refused: list[RefusedLink] = Field(default_factory=list)
    #: Repeats of an earlier line, counted, not listed: nothing to act on.
    duplicates: int = 0
    #: The links beyond what one go can take, LISTED because they are work still to do: the box
    #: refills with them.
    left_over: list[str] = Field(default_factory=list)


class BulkRequest(Wire):
    """A playlist or channel address, and where its videos should land."""

    url: str = Field(min_length=1, max_length=4096)
    dest_folder_id: str | None = None
    #: This paste's own answer to whether a link already downloaded is skipped, for every item.
    remember: bool | None = None


class BulkPreview(Wire):
    """What a playlist or channel holds, before anything is queued.

    Asked first because taking a creator's whole output is a decision made knowing its size.
    """

    #: The site's display name, so the answer says where this is about to happen.
    site: str
    #: How many will be queued if this goes ahead.
    count: int
    #: Whether the site had more than one paste may take at the same time.
    truncated: bool
    #: How many one paste may take, so the screen states it rather than keeping a stale copy.
    limit: int


class BulkQueued(Wire):
    """How many downloads a confirmed bulk request actually queued."""

    queued: int


class SupportedSite(Wire):
    """One site Sift knows, as the reference table shows it."""

    key: str
    name: str
    #: The addresses that reach it, so somebody can tell whether their mirror is one Sift knows.
    hosts: list[str]
    #: What the site serves: video, images, or both.
    media: list[str]
    #: Whether a whole profile or playlist can be taken in one go.
    bulk: bool
    #: Whether Sift claims this site works, rather than only recognising its addresses (listed so
    #: traffic to it can be given a way out).
    supported: bool = True
    #: Whether downloads from this site have been run and found working: an unchecked claim is
    #: worse than none.
    tested: bool
    #: Whether this Site says WHO posted a file, so `{creator}` can ever fill (not on a file host or
    #: Reddit); the naming builder says so beside the template.
    names_creators: bool
    #: The naming a blank rule stands for: a template, or EMPTY to keep the arriving name (the
    #: catalog's `default_naming`). A stored rule (`SiteOptionItem.naming`) wins, EMPTY included.
    default_naming: str
    #: Every template word a download from this Site can fill, in the order to offer them: the
    #: four every download fills, `creator` where the Site names a person, then the Site's own.
    #: A word not listed here always fills empty on this Site.
    name_words: list[str]
    #: Refusals this site gives in words (an age gate, a region block): what somebody reading this
    #: table is usually looking for.
    walls: list[str] = Field(default=[])
    #: What this Site does with no cookies saved, under Sift's own method: `required`, `partial`
    #: (`cookies_why` says which content needs them) or `not_needed`. The catalog's declaration,
    #: sent so the cookies sheet and the supported list agree; every Site has been run without them.
    cookies: Literal["required", "partial", "not_needed"]
    #: The sentence under that answer, in this Site's own terms.
    cookies_why: str
    #: The need when the Site is pointed at yt-dlp or gallery-dl instead, where it differs.
    cookies_with_a_tool: Literal["required", "partial", "not_needed"] | None = None


class DownloadFolder(Wire):
    """The folder a download goes into, by name and by where it is on disk.

    Both: the name is what the chooser and Folders wall call it; the path tells two of one name
    apart and is what somebody types into a file manager.
    """

    id: str
    name: str
    #: The directory on disk, joined as the import writes into it, from the rows: a list read every
    #: second must not touch the filesystem to label a row.
    path: str


class DownloadItem(Wire):
    """One download as the queue screen sees it. Carries the address; never a hash of one.

    No hash of the address: it is what the ledger dedupes on, and anybody holding a list of links
    could hash them to learn which this Sift was asked for. The address itself is shown: the slice
    is admin-only, read by the person who pasted it.
    """

    id: str
    status: str
    dest_folder_id: str | None
    site: str | None
    #: The username on that site it came from, where the resolver could name one.
    username: str | None
    asset_id: str | None
    error: str | None
    created_at: int
    #: Short and stable, so a screen recognises a kind of failure; never shown as it is.
    error_code: str | None = None
    #: How much the sentence is worth: 3 written about this site from watching it refuse, 2 a
    #: condition with an answer, 1 a status code's standard phrase.
    error_tier: int | None = None
    #: The supported site's stable key for the row's mark; `site` is a renameable name.
    site_key: str | None = None
    #: The way out this download took, in words: Direct, or the name of a tunnel.
    via: str | None = None
    #: The tunnel server's address as it was when the download went, drawn mostly covered as in
    #: Settings; absent for a direct download or one older than the record.
    via_address: str | None = None
    #: The site's name from the address, so an unfinished row is not an anonymous link.
    site_name: str | None = None
    #: What the creator's picture is filed under, for a site whose creators have pages Sift knows.
    #: The address to ask for it is built from this; absent means there is none to ask for.
    creator_scope: str | None = None
    #: The address, exactly as it was pasted. What a link on the row points AT, always.
    url: str | None = None
    #: The address with a signed link's expiry and signature taken off: what the row SAYS, never
    #: what a link points at.
    shown_url: str | None = None
    #: The produced file's name and size, read from the library, so a deleted file leaves a row
    #: that no longer claims to have anything.
    filename: str | None = None
    size_bytes: int | None = None
    #: What the download produced, as the ledger remembers it. Present after the file has been
    #: deleted, which is when `filename` above is not.
    remembered_filename: str | None = None
    #: When it stopped, for a row that has stopped. With `created_at`, this is how long it took.
    finished_at: int | None = None
    #: The site and the person this was filed under, as ids, so the names on the row open their
    #: pages. Absent until a download has succeeded and been attributed.
    site_id: str | None = None
    person_id: str | None = None
    #: How far along, while running only: a stale bar under a finished row would be a lie.
    progress: DownloadProgress | None = None
    #: What the row SAYS about a failure, only where the stored `error` is a status code's standard
    #: phrase or uses a word Sift no longer uses; `error` is usually a sentence about this very
    #: site. A client draws this when present. See `kernel/access/sentences.download_failed`.
    sentence: str | None = None
    #: The job running it: the Activity screen links `/downloads?job=<job_id>` and this page picks
    #: the row out. Absent before it is queued and after the queue prunes it.
    job_id: str | None = None
    #: Place in line from 1, only while waiting: a position, never a time, which would need a rate.
    position: int | None = None
    #: The folder this goes into, named, never "the default folder": by the job's own rule (chosen,
    #: else the Site's, else everything's; `SiteOptionStore.resolve`). A running or landed row names
    #: the folder its job RESOLVED (`record_folder`), so a later setting does not move it. Absent
    #: when no folder exists in the chain or it was removed.
    folder: DownloadFolder | None = None
    #: Files the link offered and those left out for good (size settings, gone from the Site);
    #: absent when none were lost.
    files_offered: int | None = None
    files_left_out: int | None = None
    #: What its tunnel refused after the file landed; absent when nothing was.
    reads_refused: str | None = None


class DownloadProgress(Wire):
    """How far along a running download is.

    Absent, never zero, when unknown: no total draws no bar, a zero total a full one. A gallery
    reports files, which is all its tool can say.
    """

    done_bytes: int
    total_bytes: int | None = None
    #: Whether that total is the tool's running guess rather than a size the site declared.
    total_is_estimated: bool = False
    done_files: int = 0
    total_files: int | None = None
    bytes_per_second: float | None = None
    seconds_left: float | None = None


class QueueSummary(Wire):
    """How the queue as a whole is doing, for the strip above the list.

    One figure for everything running, from a single read, whatever the queue's size.
    """

    running: int
    queued: int
    bytes_per_second: float
    seconds_left: float | None = None
    #: Rows per state over the WHOLE queue, so a chip's number is the queue's, not the page's.
    by_state: dict[str, int] = Field(default_factory=dict)


class DownloadsAtAGlance(Wire):
    """What the Downloads row on the rail says, from the download rows themselves.

    `downloading` turns the glyph (running, plus waiting while not paused). The unseen counts drive
    the dot, red over green. `waiting_for_cookies` is the number beside the row.
    """

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
    #: Every row the queue holds, narrowed by nothing. What the screen's heading counts.
    total: int
    #: How the queue as a whole is doing; an empty queue reports zeroes, never no answer.
    summary: QueueSummary | None = None
    #: How many rows the narrowing keeps: what the pager divides into pages. Equal to `total`
    #: when nothing is narrowed.
    matched: int = 0
    #: Rows per state inside the chosen Site (the whole queue with none): each tab's count.
    counts: dict[str, int] = Field(default_factory=dict)
    #: The Sites the queue holds rows from, inside the chosen state, with how many. The Site menu.
    sites: list[DownloadSiteCount] = Field(default_factory=list)


#: What a site's saved cookies are SHOWN as, decided on the server (`cookie_health.state_of`).
#: `none` is never returned, having no row; it is the client's word for listed sites without
#: cookies, kept in this closed set so nobody invents a fourth.
ConnectionState = Literal["saved", "ending_soon", "expired", "none"]


class SaveConnectionRequest(Wire):
    """Save a site's cookies. They are write-only: taken here, never returned anywhere."""

    site: str = Field(min_length=1, max_length=100)
    cookie: str = Field(min_length=1, max_length=100_000)


class PreviewConnectionRequest(Wire):
    """Read cookies back to whoever pasted them, without saving anything.

    Its own shape though the fields match: there `cookie` is about to be sealed, here described
    and dropped, and one model would tie the two routes' validation together.
    """

    site: str = Field(min_length=1, max_length=100)
    cookie: str = Field(min_length=1, max_length=100_000)


class CookiePreview(Wire):
    """What Sift understood of pasted cookies, before anything is saved.

    The add form's read-back ("14 cookies for one site, until 2 December"): derived facts only, as
    `SavedConnection` carries, nothing pasted returned and nothing written.
    """

    cookies: int
    domains: list[str]
    #: The first and last expiry: the first alone misrepresents a site with a challenge in front
    #: (see `CookieSummary`).
    expires_at: int | None = None
    expires_last: int | None = None
    #: Whether every dated cookie has passed: worth saying as they are pasted.
    expired: bool = False


class ConnectionCheck(Wire):
    """What one site said when Sift asked it whether the saved cookies still work.

    `said` is written on the server, the one place that knows what was asked.
    """

    accepted: bool
    said: str


class SavedConnection(Wire):
    """A site's saved cookies, and what Sift understood of them.

    Derived facts only, enough to tell a jar saved wrong from right; nothing saved is returned.
    """

    id: str
    cookies: int
    domains: list[str]
    #: The first expiry; absent for a jar of session cookies, which is ordinary.
    expires_at: int | None = None
    #: The last expiry, so a screen describes the spread: a half-hour clearance cookie and a
    #: year-long session cookie have no single expiry.
    expires_last: int | None = None
    #: Whether every cookie that carries a date has already passed it.
    expired: bool = False


class ConnectionItem(Wire):
    """One site's saved cookies as a screen sees it: that they exist, never what they are."""

    id: str
    site: str
    #: The health word as STORED (`saved`, `needs_cookies`), for the diagnostics export; screens
    #: draw `state`, never re-deriving the rule.
    status: str | None
    updated_at: int | None
    #: What the row is shown as, decided on the server from the health and the jar's last expiry.
    state: ConnectionState = "saved"
    #: The jar's first and last expiry as read when saved. One date shown is the LAST: the first is
    #: usually a clearance cookie reissued on the next request.
    expires_at: int | None = None
    expires_last: int | None = None
    #: When Sift last unsealed them for a download or check; absent if never, which is worth seeing.
    last_used_at: int | None = None


class ImportTunnelRequest(Wire):
    """A provider's WireGuard configuration, and the name to know it by.

    Accepted, never returned: it carries the account's private key, so it is sealed on arrival and
    read only to start the tunnel.
    """

    name: str = Field(min_length=1, max_length=80)
    config: str = Field(min_length=1, max_length=65536)


class ReplaceTunnelConfigRequest(Wire):
    """A reissued configuration for a tunnel that already exists."""

    config: str = Field(min_length=1, max_length=65536)


class RenameTunnelRequest(Wire):
    name: str = Field(min_length=1, max_length=80)


class StopTunnelRequest(Wire):
    """Whether to stop a tunnel now or let the downloads on it finish first.

    Draining by default: a started transfer cannot be re-routed. Now is for when the traffic itself
    is the reason.
    """

    now: bool = False


class ImportedTunnel(Wire):
    id: str


class TunnelItem(Wire):
    """One tunnel as the settings screen sees it.

    No public key: it names the provider account and nobody acts on it. The server's address is
    carried (admin-only, mostly covered until asked): it tells a tunnel to the wrong country apart.
    `enabled` is what was asked for, `up` that the far end answered recently; the gap between them
    is worth seeing.
    """

    id: str
    name: str
    enabled: bool
    running: bool
    up: bool
    draining: bool
    last_handshake_at: int | None = None
    #: The address of the server on the far side, without its port. None while nothing has answered.
    endpoint: str | None = None
    #: Why the last start failed, in words (a port held by another program is named), or null.
    problem: str | None = None
    #: Whether a swap can be hosted on it, as last found: true, false (no port from the provider),
    #: or null if untried. Never the port or the address, which are the swap session's.
    can_host: bool | None = None


class RouteRequest(Wire):
    """Where one site's downloads go out: the word for the machine's own address, or a tunnel id."""

    route: str = Field(min_length=1, max_length=64)


class SiteChoice(Wire):
    """A site a route can be set on: the key it is stored under, and its name on screen."""

    key: str
    name: str


class RoutesResponse(Wire):
    """The default every site follows, the sites given one of their own, and what can be chosen.

    The sites come back with the routes, or a screen could show only the ones already set.
    """

    default: str
    sites: dict[str, str]
    available: list[SiteChoice]


class SiteOptionItem(Wire):
    """What one site does differently, as the settings table shows it."""

    scope: str
    naming: str | None = None
    dest_folder_id: str | None = None
    #: The tool chosen for this site, or None to use whatever Sift would have used.
    downloader: str | None = None


class DownloaderChoice(Wire):
    """One tool a Site can be pointed at, and what to call it on the screen.

    From the server, since which tools ship is a fact about Sift; a copy in the browser would
    offer a tool after it had gone.
    """

    #: What the server stores; empty string is "no opinion", a chooser's first option.
    value: str
    label: str
    #: One line under the name. What the tool is good at, in the words somebody choosing would use.
    help: str


class SiteOptionsResponse(Wire):
    """The answers everything follows, the sites given their own, and the tokens a template may use.

    The tokens come back with the values, each explained: a template box with no list of what goes
    in it cannot be filled.
    """

    default: SiteOptionItem
    sites: list[SiteOptionItem]
    tokens: dict[str, str]
    #: The tools a Site may be pointed at, in the order the screen should offer them.
    downloaders: list[DownloaderChoice]


class SetSiteOptionsRequest(Wire):
    """What one site (or everything) should do. Absent means "no opinion", which is not the same
    as an empty template: that is the deliberate choice to keep the name the fetcher gave it."""

    naming: str | None = Field(default=None, max_length=200)
    dest_folder_id: str | None = None
    #: A tool instead of the catalog's, or None; checked against what Sift ships, since a stored
    #: name nothing runs reads as ignored.
    downloader: str | None = Field(default=None, max_length=32)


class NamePreviewRequest(Wire):
    """A template, and optionally the Site to imagine it against.

    Its own shape: a preview asks about one field, and a write body would make it say something
    about a folder and a downloader.
    """

    naming: str | None = Field(default=None, max_length=200)
    #: A site key whose real facts the example uses, or None for everything's: `{creator}` is empty
    #: on most Sites, which a preview must show.
    scope: str | None = Field(default=None, max_length=64)


class NamePreview(Wire):
    """What a template would produce, so it can be checked at the setting rather than days later.

    Empty means the file would keep its name, which the screen shows.
    """

    example: str
