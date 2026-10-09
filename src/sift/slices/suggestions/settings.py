# SPDX-License-Identifier: AGPL-3.0-or-later
"""This pass's switches, global to the install; switching one off stops the pass and undoes
nothing."""

from __future__ import annotations

from sift.kernel.settings_registry import register_setting

#: The master over the key below; on by default since reading names opens no file.
SCAN_KEY = "suggestions.scan"

FILE_FROM_FILENAMES_KEY = "suggestions.file_from_filenames"

#: Separate because it is the one part of the pass that opens a file.
READ_METADATA_KEY = "suggestions.read_metadata"


def register() -> None:
    # Retired into the suggestions task's When; the key stays as a name the switchboard asks by.
    register_setting(
        key=READ_METADATA_KEY,
        scope="app",
        default=True,
        section="Importing",
        label="Find usernames in photo details",
        disclosure=(
            "Sift checks only two fields, Artist and ImageDescription, in a few of that username's "
            "photos. It checks them only when no username in your library matches the ID. It never reads a "
            "location or anything else in the file. When off, those files wait until you add the "
            "ID to a username yourself, and nothing already added is undone."
        ),
        help=(
            "When a file is named with a Site's ID for a username, Sift looks in the photo's "
            "details for the name."
        ),
    )
    register_setting(
        key=FILE_FROM_FILENAMES_KEY,
        scope="app",
        default=True,
        section="Importing",
        # The gate allows six words of label and thirty-four of help.
        label="Add usernames from filenames",
        disclosure=(
            "When off, Sift ignores filenames, and Sites and usernames already added stay. It uses "
            "only the name, so it adds almost no time to a scan, and it never adds People."
        ),
        help=(
            "When a filename shows the Site and username a file came from, as downloaders name "
            "them, Sift adds that Site and username to the file."
        ),
    )
