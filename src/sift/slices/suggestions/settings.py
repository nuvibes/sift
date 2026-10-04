# SPDX-License-Identifier: AGPL-3.0-or-later
"""This pass's switches, declared at import so the settings screen draws itself from them.

Global to the install rather than per-user: what a scan does to the library is a property of the
library, not somebody's preference about their own screen.

**It is on out of the box, and the cost is why it can be.** The rest of this pass reads folders
(what is in them, whose faces run through them), and this half reads NAMES: the rows are already in
hand from the same two queries, and nothing is opened, decoded or downloaded. So it is the cheapest
thing a scan does, and switching it off saves almost nothing. The switch exists for the other
reason: a library whose filenames are meaningless, or whose names happen to read like a downloader's
and are not, gets filings it did not ask for and wants a way to stop them.

**It stops the pass, and undoes nothing.** A filing already written is a row somebody can take back
on the filings screen; a switch that also swept them away would be a preference deleting data, which
is not what a switch on an Importing screen can mean.
"""

from __future__ import annotations

from sift.kernel.settings_registry import register_setting

#: Whether the folder pass runs at all.
#:
#: The master over the key below it. The pass is asked for once a batch of imports has settled and
#: at the end of a walk, and this is the way to say no to it, so a library whose folders are named
#: for something other than the people in them is not proposed names it never wanted, with only
#: the filing screen to take each one back.
#:
#: **It stops the pass and undoes nothing**, the same as the key below: a proposal already made
#: stays on the board and a filing already written stays written.
SCAN_KEY = "suggestions.scan"

FILE_FROM_FILENAMES_KEY = "suggestions.file_from_filenames"

#: Whether a number with no username may be looked up in the PICTURES' own metadata.
#:
#: Under the key above in every sense: with that one off nothing reads a filename at all and this
#: never comes up. It is separate because it is the one thing in this pass that OPENS a file
#: (everything else here is string work over rows already in hand), and because what it opens is
#: metadata, which is a different thing to be asked about than a name. A library whose admin does
#: not want its pictures read still gets every filing a filename can make on its own.
READ_METADATA_KEY = "suggestions.read_metadata"


def register() -> None:
    # RETIRED into the When of the suggestions task (`tasks.suggestions.when`). The key stays as a
    # name the switchboard asks by; the composition root retires it.
    register_setting(
        key=READ_METADATA_KEY,
        scope="app",
        default=True,
        # Importing, beside the switch it sits under and every other switch over what a scan does.
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
        # Importing rather than a screen of this feature's own, beside every other switch over what
        # a scan does: the same reasoning already written beside `semantic.describe_on_import`.
        section="Importing",
        # Short: the gate allows six words of label and thirty-four of help. The reassurance is
        # reference material and belongs in the disclosure.
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
