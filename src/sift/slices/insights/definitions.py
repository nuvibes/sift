# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each figure on Insights counts, one sentence by its label, said one press away from it."""

from __future__ import annotations

from collections.abc import Mapping

from sift.kernel.access.sentences import Line, said

#: WHAT EACH FIGURE COUNTS, one sentence by the figure's label, said one press away from the figure
#: (`models.Figure.defines`). `test_statements` walks every label the page and the recaps draw.
DEFINITIONS: Mapping[str, str] = {
    "Viewed": (
        "Time files were in front of you, counted once each reached the view threshold. Theater "
        "counts each hour a wall played once, whatever the cells."
    ),
    "Viewed before": "Time viewed in the period before, counted the same way.",
    "Visits": (
        "One for each view outside Theater that reached the view threshold, and one for each "
        "Theater wall that played."
    ),
    "First opened at": "When you first started viewing that day, after half an hour away or more.",
    "Daily average": "Time viewed, divided by the days Sift has counted so far in the period.",
    "Files opened": "Files you opened outside Theater, each counted once.",
    "Files you came back to": "Files you opened three times or more outside Theater.",
    "Photo Sets looked through": "Photo Sets with at least one file you viewed.",
    "In Theater": (
        "Each hour a Theater wall played counts once, however many cells played. A wall with "
        "nothing playing counts nothing."
    ),
    "Theater visits": "Theater walls that played. A wall with nothing playing isn't counted.",
    "Files in Theater": "Files a Theater wall played, counted once a day.",
    "Times you opened Sift": "Each time you started viewing after half an hour away or more.",
    "Earliest start": "The earliest time you started viewing, after half an hour away or more.",
    "Latest finish": (
        "The latest time you stopped viewing. Past midnight it stays with the day the evening "
        "began."
    ),
    "Rated": "Files whose rating you changed, each counted once.",
    "Starred": "Files you starred, each counted once.",
    "O count": "Each press of O that raised the O count.",
    "Questions answered": "Answers you gave on Organize. An answer you undid isn't counted.",
    "Answered": "Answers you gave on Organize. An answer you undid isn't counted.",
    "Faces named": "Faces you named on Organize, or matches you agreed with.",
    "Files filed": "Files you filed, each counted once.",
    "Imported": "Files added to the library, counted on the day they were imported.",
    "Files imported": "Files added to the library, counted on the day they were imported.",
    "Deleted": "Files deleted from the library that you could see.",
    "Tasks": (
        "Time Sift's tasks worked, added across every worker, so one day can hold more than 24 "
        "hours."
    ),
    "Faces found": "Faces Sift found in the files.",
    "Files fingerprinted": "Files Sift fingerprinted.",
    "Views": "Times you opened the file outside Theater and it reached the view threshold.",
    "Views that day": "Times you viewed the file on the first day you opened it.",
    "Days away": "Days between your last view of the file and this one.",
    "Longest visit": (
        "From opening Sift to closing it on one device, including time the window was left open."
    ),
    "Downloaded": "The size of the downloads that finished.",
}


def definition(label: str) -> Line | None:
    """What a figure of this label counts, or None for a label with no definition."""
    words = DEFINITIONS.get(label)
    return None if words is None else said(words)
