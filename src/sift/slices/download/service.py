# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download ledger and the sites' saved cookies, and the one entry point capture calls.

The ledger is the `downloads` table, with the hash that lets a re-dropped link be skipped rather
than fetched twice; the saved cookies are `site_connections` and the sealed jars behind them; and
`submit_url` queues a link pasted on the capture screen under the same guards as one dropped on a
folder. One module per act (`service_base`, `service_listing`, `service_controls`,
`service_ledger`, `service_connections`, over `service_views` and `service_sites`); this module is
the door the rest imports.
"""

from __future__ import annotations

from sift.kernel.wiring import URL_IMPORTER, Part
from sift.slices.download.service_connections import ConnectionSecret as ConnectionSecret
from sift.slices.download.service_connections import ConnectionView as ConnectionView
from sift.slices.download.service_connections import DownloadConnections
from sift.slices.download.service_ledger import _SET_FAILED as _SET_FAILED
from sift.slices.download.service_listing import DOWNLOAD_SHOWS as DOWNLOAD_SHOWS
from sift.slices.download.service_listing import DOWNLOAD_SORTS as DOWNLOAD_SORTS
from sift.slices.download.service_listing import DownloadNarrowing as DownloadNarrowing
from sift.slices.download.service_listing import DownloadPage as DownloadPage
from sift.slices.download.service_listing import DownloadShow as DownloadShow
from sift.slices.download.service_listing import DownloadSort as DownloadSort
from sift.slices.download.service_listing import DownloadView as DownloadView
from sift.slices.download.service_listing import FileFacts as FileFacts
from sift.slices.download.service_listing import FilesOf as FilesOf
from sift.slices.download.service_listing import RailFacts as RailFacts
from sift.slices.download.service_sites import _creator_scope_of as _creator_scope_of
from sift.slices.download.service_sites import _shown_url as _shown_url
from sift.slices.download.service_sites import _site_of as _site_of
from sift.slices.download.service_sites import site_home_of as site_home_of
from sift.slices.download.service_sites import site_name_of as site_name_of
from sift.slices.download.service_views import DOWNLOAD as DOWNLOAD
from sift.slices.download.service_views import FOLLOW_THE_SETTINGS as FOLLOW_THE_SETTINGS
from sift.slices.download.service_views import PAUSED_KEY as PAUSED_KEY
from sift.slices.download.service_views import (
    PEOPLE_FROM_USERNAMES_KEY as PEOPLE_FROM_USERNAMES_KEY,
)
from sift.slices.download.service_views import (
    PHOTO_SETS_REMOVED_KEY as PHOTO_SETS_REMOVED_KEY,
)
from sift.slices.download.service_views import REMEMBER_KEY as REMEMBER_KEY
from sift.slices.download.service_views import JobInput as JobInput
from sift.slices.download.service_views import PasteChoices as PasteChoices
from sift.slices.download.service_views import SiteCount as SiteCount
from sift.slices.download.service_views import UsernameFrom as UsernameFrom


class DownloadService(DownloadConnections):
    """The ledger, the sites' cookies, and the URL door. Held on the application, one per database."""


#: The download ledger, under the part the kernel declares as `URL_IMPORTER`, named from it so a
#: rename cannot leave capture looking up a name nothing answers to.
SERVICE: Part[DownloadService] = Part(URL_IMPORTER.name)
