# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the log does, as two things somebody can choose.

## Why these are here and not beside the logger

`kernel/log.py` sets logging up before there is a database to ask, which is the only order that
works: the boot lines have to be written with something. So the environment decides what the logger
starts as, and these decide what it becomes once the library is open. Keeping the declarations apart
from `log.py` keeps that split visible: a reader of `log.py` sees a function that is handed two
values, and a reader of this file sees where the two values come from after boot.

## Why detail is two words rather than five levels

`DEBUG`, `INFO`, `WARNING`, `ERROR` and `CRITICAL` are a programmer's vocabulary and three of them
are indistinguishable to the person running the application: below WARNING nothing is hidden that
anybody would miss, and above INFO the log stops recording what happened. The real question is "is
something wrong and do you need the detail", so there are two answers, and the environment
variable is still there for anybody who genuinely wants CRITICAL.
"""

from __future__ import annotations

from pathlib import Path

from sift.kernel.settings_registry import register_setting

#: The ordinary log: what happened, and anything that went wrong.
NORMAL = "normal"
#: Everything the ordinary log has, plus the detail that is only worth reading when chasing a fault.
DETAILED = "detailed"

DETAIL_KEY = "logs.detail"
KEEP_MB_KEY = "logs.keep_mb"
HIDE_PERSONAL_KEY = "logs.hide_personal"

#: A megabyte, so the stored number and the byte cap are one conversion in one place.
BYTES_PER_MB = 1024 * 1024


def detailed_from(value: object) -> bool:
    """Whether the stored answer means the detailed log. Anything unrecognised means the ordinary
    one: a log that quietly became enormous because a value could not be read is the worse failure
    of the two."""
    return value == DETAILED


def hide_personal_from(value: object) -> bool:
    """Whether the stored answer asks for personal detail to be hidden as lines are written. Only a
    stored True does: the log is written whole unless somebody chose otherwise."""
    return value is True


#: What the log may take up in total, when nothing has been stored.
#:
#: Sized by how long it holds, not picked: at ordinary use and NORMAL detail, sixty megabytes of
#: log can span less than a day, and a log that holds less than a day is a log that has thrown away
#: whatever went wrong by the time somebody comes to read it, which is the only thing it is for.
#:
#: A gigabyte is roughly a fortnight at that rate. On an application whose cache defaults to four,
#: that is a proportionate amount of disk to spend on being able to answer "what happened last
#: week".
DEFAULT_KEEP_MB = 1024


def keep_bytes_from(value: object) -> int:
    """The stored megabytes as bytes: ALL of it, file and backups together.

    A separate function from the validator on purpose: the validator says what may be STORED, and
    this says what to do with whatever is in the database, including a value written by a version
    that allowed something this one does not.
    """
    if not isinstance(value, str | int | float):
        return DEFAULT_KEEP_MB * BYTES_PER_MB
    try:
        megabytes = int(value)
    except ValueError:
        return DEFAULT_KEEP_MB * BYTES_PER_MB
    return max(1, megabytes) * BYTES_PER_MB


def per_file_bytes(total_bytes: int, backups: int) -> int:
    """How large ONE file may get, given what the whole log may take.

    THE SETTING IS THE TOTAL, AND THIS IS WHY THERE IS A FUNCTION FOR IT. The rotating handler is
    given a per-FILE cap and keeps `backups` more beside it, so a setting handed straight to it
    means `backups + 1` times as much disk as the number on the screen says: "Space for the log"
    meaning a sixth of it. A label that lies about a number is worse than no label.

    Divided rather than the backups being dropped: several files is what makes a log rotate at all,
    and rotating is what stops one enormous file that has to be opened whole to be read.
    """
    return max(BYTES_PER_MB, total_bytes // max(1, backups + 1))


def fit_within(log_file: Path, backups: int, total_bytes: int) -> int:
    """Delete the oldest rotated files until the log and its copies fit in `total_bytes`. Returns
    how many bytes were freed.

    ## Why this exists: lowering the setting has to lower the log

    The rotating handler only ever looks at ONE file. Lowering the setting lowers the size at which
    the current file rotates, and nothing else, so every older file written under the old, larger
    setting stays exactly as large as it was until enough new rotations push it off the end, and
    "Maximum log size" is exceeded, by as much as double, for as long as that takes. A number on a
    screen that says "never grows past this" has to be true now.

    ## Whole files, newest kept first

    The current file is counted and never touched: the handler holds it open, and it rotates on the
    next line anyway once it is past the per-file size. Then `.1`, `.2`, ... in the order they were
    written backwards, each kept while it still fits in what is left and DELETED once it does not,
    together with everything older. Deleting whole files is the handler's own unit (it deletes the
    oldest file when it rotates) and it costs a stat and an unlink, never a read, so it is safe to
    call on every rotation and every change of the setting. Cutting a file down to the part that
    fits was the alternative and was not taken: it is a copy of up to the whole setting's worth of
    log to keep a few hours of lines the setting has just said are not wanted.

    Files past `backups` (left by a larger `SIFT_LOG_BACKUPS` on an earlier run) are not the
    handler's any more and are never rotated away, so they are counted and deleted by the same rule.

    A file that cannot be removed is left and skipped: a log that could not be trimmed is not a
    reason for anything that writes a log line to fail.
    """
    try:
        room = total_bytes - log_file.stat().st_size
    except OSError:
        room = total_bytes
    freed = 0
    number = 1
    while True:
        older = log_file.with_name(f"{log_file.name}.{number}")
        try:
            size = older.stat().st_size
        except OSError:
            # A gap ends the walk once the handler's own files are behind it. Rotation numbers
            # contiguously, so a missing `.N` inside the kept range is a file somebody removed,
            # and past it there is nothing this handler wrote.
            if number > backups:
                break
            number += 1
            continue
        if size <= room and number <= backups:
            room -= size
        else:
            room = 0
            try:
                older.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash (Sift's own log file)
                freed += size
            except OSError:
                pass
        number += 1
    return freed


# Declared at import, the way every slice declares its own: the settings screen is generated from
# what has been registered, and a registration that waits for somebody to call it is a row that is
# missing from the screen until they do.
register_setting(
    key=DETAIL_KEY,
    scope="app",
    default=NORMAL,
    choices=(NORMAL, DETAILED),
    choice_labels=("Normal", "Detailed"),
    section="Logs",
    label="Log detail",
    help=(
        "Normal records what happened. Detailed also records each step, and fills the log faster."
    ),
)

# On the Logs page rather than under a privacy heading, because what it changes is what this log
# records. The secrets rule in `kernel/log.py` holds whatever this says.
register_setting(
    key=HIDE_PERSONAL_KEY,
    scope="app",
    default=False,
    section="Logs",
    label="Hide personal details in the log",
    help=(
        "Hides the names in file paths, usernames and email addresses as lines are written. "
        "Passwords, cookies and keys are always hidden."
    ),
    default_since="0.1.218",
)

register_setting(
    key=KEEP_MB_KEY,
    scope="app",
    default=DEFAULT_KEEP_MB,
    # Below about a hundred the log stops covering a day of ordinary use, which is the point of
    # keeping one. Above ten gigabytes it is no longer a log.
    minimum=16,
    maximum=10_000,
    unit="MB",
    section="Logs",
    label="Maximum log size",
    disclosure=(
        "The default holds about two weeks at normal detail, counting the current file and the "
        "older ones kept in the same folder. The log never grows past this size."
    ),
    help=("The most disk space the log may use. Sift deletes the oldest entries as it fills."),
)
