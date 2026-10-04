# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers the remote runs on.

None is a setting: they describe what a screen and a command are, and three are limits on what one
signed-in user can make this process hold or do, which a screen must not be able to raise.
"""

from __future__ import annotations

#: How long a screen stays listed after its last report: three missed reports of
#: `REPORT_EVERY_SECONDS` is a screen asleep, frozen or closed; less would drop one on a slow
#: network.
SCREEN_SECONDS = 30.0

#: How often an offered screen reports when nothing changed. The client's copy is held to this one
#: by a test.
REPORT_EVERY_SECONDS = 10.0

#: Screens one user may have listed: far above a person's, far below a script's. Past it the least
#: recently heard goes, which is never a live screen for long.
MOST_SCREENS_PER_USER = 8

#: Commands one user may send in a burst, and the refill rate: each wakes every live connection of
#: theirs early, so this bounds what one user can make them all do. The phone thins drags to a few a
#: second.
COMMAND_BURST = 20
COMMANDS_PER_SECOND = 10.0

#: The same for reports: a dragged volume is a run of changes, so the burst is larger; the rate
#: keeps a tab gone wrong from becoming a stream.
REPORT_BURST = 40
REPORTS_PER_SECOND = 10.0

#: The longest position or length a report or command may name, about eleven days: past any file,
#: and short enough for exact arithmetic.
LONGEST_SECONDS = 1_000_000.0

#: The largest step back or on a command may ask for, in seconds.
LONGEST_STEP_SECONDS = 600.0

#: The most cells a wall may report: a ceiling for the arithmetic; a command naming a cell is held
#: to the number the screen reported.
MOST_CELLS = 64

#: How far a reported position may drift from where playing carried it before the phone is told it
#: moved: under it a heartbeat, over it a seek.
DRIFT_SECONDS = 2.0

#: The longest list a screen may offer (sizes, layouts, presets): a ceiling on one report; a command
#: naming a place is held to the length the screen reported.
MOST_CHOICES = 32

#: The longest a wall's cell may hold one file before it moves on, in seconds: an hour, the
#: ceiling the cell's own timer field takes.
LONGEST_TIMER_SECONDS = 3600
