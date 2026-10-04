# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one switch, declared at import so the settings screen draws itself from it.

Global to the install rather than per user: whether Sift groups a library's loose pictures on
its own is a fact about the library, not a personal view of it: two users cannot each have
their own answer to whether a Photo Set exists.

**It starts OFF, and that is the important half.** Making a Photo Set is a write nobody asked for
about pictures nobody looked at, and the rule that proposes one is a guess from what a picture LOOKS
like: tuned to be sound, and not a promise about any particular library. So the ordinary
state of this feature is a card that asks, and the switch is for somebody who has watched it ask
enough times to trust it. With it on, every set is still written with a receipt and is still one
press to take back from the decisions page; what goes away is the asking, not the record.
"""

from __future__ import annotations

from sift.kernel.settings_registry import register_setting

#: Whether a proposed shoot becomes a Photo Set without anybody being asked.
AUTO_FILE_KEY = "shoots.auto_file"


def register() -> None:
    register_setting(
        key=AUTO_FILE_KEY,
        scope="app",
        default=False,
        # Beside the other things a scan files on its own (a folder or an archive becoming a Photo
        # Set, a folder name becoming a person), on Scan's settings page.
        section="Importing",
        label="Create Photo Sets from shoots",
        disclosure=(
            "When off, each suggested shoot waits in Organize under Shoots until you choose Create "
            "Photo Set. When on, every shoot becomes a Photo Set as soon as Sift finds it. Each one "
            "is recorded in History, where you can undo it."
        ),
        help=(
            "A shoot is a run of one creator's photos taken together: the same place, light and "
            "outfit. When on, each one Sift finds becomes a Photo Set."
        ),
    )
