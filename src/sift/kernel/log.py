# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structured logging, redaction, and timing.

Redaction happens at source rather than on display: a log is copied into a bug report or a
support thread, and by then it is too late for a viewer to decide what to hide.

Credentials (cookies, tokens, passwords, session ids) are never written, in any mode, with no
setting to reveal them. A session cookie in a log is a login somebody can replay.

Names (the account in a home directory, a username, an email) are written whole unless an admin
turns on `Hide personal details in the log` (`logs.hide_personal`): the filesystem is no secret
from the person who owns it, and a log with the names taken out cannot say which folder a fault
was in. `Download log` writes a copy with the names and the secrets taken out (`redact` with
`always_personal`), so the copy attached to a public issue is safe whatever this setting says.
Until the library is open there is no setting to read, so the lines written while Sift starts
hide names (unless `SIFT_LOG_UNREDACTED` says otherwise).

A path is *reduced*, not erased:

    /home/kate/Videos/holiday.mp4   ->   /home/[redacted]/Videos/holiday.mp4

The mount point, the folder layout and the filename survive: none of them identifies anyone and
all of them are what you need to work out what went wrong. URLs, hosts, titles, error text,
timings, counts and ids are not hidden either. They describe what happened rather than who it
happened to, and redaction that removes the answer along with the identity gets switched off.

The scrubber matches on the key and on the value, because paths turn up inside tracebacks, ffmpeg
stderr and downloader output, none of which arrive under a named key.

This module is the only place allowed to touch the standard library's logging primitives.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import re
import socket
import sys
import time
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager, suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TypeVar, cast

import structlog

F = TypeVar("F", bound=Callable[..., Any])

REDACTED = "[redacted]"

# SECRETS are credentials. A session cookie in a log line is a login someone can replay; an API
# token is the account. These are never written, in any mode, and there is no setting to reveal
# them: the value of a log has never once outweighed handing over the key.
_SECRET_KEY_PARTS = (
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "auth_header",
    "session",
    "api_key",
    "apikey",
    "private_key",
    # The random key that encrypts saved site logins. It is derived at login and held only in
    # memory, never logged by the code that handles it: this makes a field carrying it redact by
    # name too, so an accidental log line cannot be the thing that writes it to disk.
    "master_key",
    # Free text an admin wrote about somebody. Not a credential, and here anyway, because the rule
    # this list carries is the one that fits: never written, in any mode, with no setting to reveal
    # it. A note is the most identifying thing in the database after a name, it is never diagnostic,
    # and nothing in the code logs one: this is what keeps that true after the fact.
    "notes",
)

# NAMES say who the operator is, rather than what they were doing; the module's note says when
# they are hidden. A field named for a path has its names stripped and keeps its shape. A field
# that IS a name (`username`, `email`) has nothing worth keeping and goes entirely.
_PERSONAL_KEY_PARTS = (
    "path",
    "dir",
    "folder",
    "email",
    "username",
    "user_name",
    "home",
)

# Deliberately NOT hidden: URLs, remote hosts, titles, error text, stage names, durations,
# counts and ids. They say what happened rather than who it happened to, and a log that cannot
# tell you which URL failed is a log nobody can act on.

# Counts, sizes and types describe without revealing.
_ALLOWED_KEYS = frozenset(
    {
        "file_count",
        "path_count",
        "url_count",
        "query_ms",
        "file_size",
        "file_type",
        "path_exists",
        "path_readable",
        "path_size",
        "session_count",
        # A route template ("/assets/{id}"), not a place on anyone's disk.
        "route",
    }
)

# Secret-shaped values, whatever key they arrive under. Several nets, because a credential wears
# several shapes:
#
#   - A high-entropy run of 32+ characters. Immediately followed by a file extension it is exempted,
#     so a filename (a hash-named `d41...e427e.jpg`, or an ordinary long title) stays readable;
#     the policy is that filenames are visible and a credential is not a filename. A *bare* hex run
#     is NOT exempted: a digest and a raw key are the same characters, so it fails safe (a master key
#     rendered as hex is exactly such a run, and must never reach a log).
#   - A value that follows a credential-shaped key in free text (`token=...`, `Cookie: ...`). This
#     catches the short ones a length rule misses: a session cookie is a credential at any length.
#     The key is kept and only the value goes, so the log still says what was hidden.
#   - A base64 run that carries `=` padding. Standard base64 (the shape of many tokens and signed-URL
#     signatures) uses `+/=`, which the character-run net above deliberately does not, to avoid
#     eating URL and filesystem paths; the padding is the part that is unmistakable and rare in a
#     path, so it is matched on its own.
#
# One residual, stated rather than hidden: a purely alphanumeric high-entropy secret that ends in a
# file extension is indistinguishable from a hash-named file and stays visible. Closing that would
# redact ordinary media filenames, which the policy forbids.
_KEY_LED_SECRET = re.compile(
    r"(?P<keep>\b(?:password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|apikey"
    r"|access[_-]?token|refresh[_-]?token|auth[_-]?token|csrf[_-]?token|sessionid|phpsessid"
    r"|session[_-]?id|token|cookie)\s*[=:]\s*)"
    r"[^\s&;,\"']+",
    re.IGNORECASE,
)
# A password embedded in a URL (`scheme://user:pass@host`). The credential is a secret and goes in
# every mode; the host and path after it are not, and are exactly what a diagnosis needs, so only
# the userinfo is removed and the `@host` is left in place. Matched only when the userinfo carries a
# password (the `:` before the `@`): a bare `user@host` is a name, redacted with the others in
# personal mode rather than as a credential in every mode. Run first, so the credential is gone
# before the high-entropy net below could take a bite out of the middle of it.
_URL_USERINFO = re.compile(r"(?<=://)[^/?#@\s]*:[^/?#@\s]*(?=@)")
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _URL_USERINFO,
    # A whole run only, never the tail of one, and never a file's name: a run followed by dotted
    # parts ending in an extension ("sift-backup-...-0.1.203.zip") names a file, not a secret.
    re.compile(
        r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])"
        r"(?!(?:\.[A-Za-z0-9_\-]+)*\.[A-Za-z0-9]{1,5}(?![A-Za-z0-9_\-]|\.[A-Za-z0-9]))"
    ),
    re.compile(r"(?i)\b(bearer|basic)\s+\S+"),
    _KEY_LED_SECRET,
    re.compile(r"\b[A-Za-z0-9+/]{23,}={1,2}(?![A-Za-z0-9+/=])"),
)

#: What the log file is called, inside whatever directory it is put in.
#:
#: Named here once, because `main.py` and `wiring/lifespan.py` (the two places logging is
#: configured) and the route that reads it back must agree on it: three copies of a filename is two
#: chances for a reader to look in the wrong place.
LOG_FILENAME = "sift.log"


def _redact_secret(match: re.Match[str]) -> str:
    """Replace a matched secret with the marker. A pattern that captured a `keep` group (the label
    a `token=` or `cookie:` prefix carries) keeps it, so the log still names what was hidden and
    only the value itself goes."""
    keep = match.groupdict().get("keep")
    return (keep or "") + REDACTED


# The identifying part of a path is a NAME, not the path. `/home/kate/Videos/holiday.mp4` gives
# away exactly one thing ("kate") and everything else in it is useful: the mount point, the
# folder layout, the filename. So only the name is removed:
#
#   /home/kate/Videos/holiday.mp4   ->  /home/[redacted]/Videos/holiday.mp4
#   C:\Users\Kate\Videos\clip.mp4   ->  C:\Users\[redacted]\Videos\clip.mp4
#
# This is also why a filesystem path need not be told from a URL path, though `/health` looks
# exactly like `/home/kate/clip.mp4` to a regex: nothing matches unless it is a home directory
# followed by a name, and a route has neither.
_HOME_SEGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Linux, and BSD's /usr/home.
    re.compile(r"(?i)(?<![\w/])(/home/)([^/\s\"',;]+)"),
    re.compile(r"(?i)(?<![\w/])(/usr/home/)([^/\s\"',;]+)"),
    # macOS.
    re.compile(r"(?i)(?<![\w/])(/Users/)([^/\s\"',;]+)"),
    # macOS mounted volumes. People name their external drives after themselves far more often
    # than they name a folder after themselves, and the name is right there in every path on the
    # disk. The rest of the path survives, so it is still obvious which drive it was.
    re.compile(r"(?i)(?<![\w/])(/Volumes/)([^/\s\"',;]+)"),
    # Windows.
    re.compile(r"(?i)(?<![\w:\\])([A-Za-z]:\\Users\\)([^\\\s\"',;]+)"),
    # THE SAME PATH WITH ITS SEPARATORS ESCAPED, which is not a curiosity: it is what a Windows
    # path looks like by the time a subprocess's output has been through a JSON encoder, and that
    # is the commonest way a path reaches this log at all. `C:\\Users\\ada\\AppData` is matched by
    # NEITHER rule above (the doubled separator breaks the anchor) and would then be eaten by
    # the UNC rule below, which redacts the literal word `Users` and leaves the account name
    # standing.
    re.compile(r"(?i)(?<![\w:\\])([A-Za-z]:\\\\Users\\\\)([^\\\s\"',;]+)"),
    # Windows network shares: \\KATE-PC\media\...: the host is usually the person.
    #
    # `:` AND `]` ARE IN THE LOOKBEHIND, and both are load-bearing.
    #
    # `:` stops this rule reading `C:\\` as a share. A UNC path never follows a drive letter, so
    # excluding one costs nothing, and without it this rule would fire first on an escaped Windows
    # path, take the segment after the drive, and publish the very name the rules above exist to
    # hide.
    #
    # `]` stops the REPLACEMENT becoming a new anchor. `[redacted]` ends in a bracket, so once the
    # rule above has taken the username out of `C:\\Users\\ada\\AppData`, the separator in front of
    # `AppData` is suddenly preceded by a character this lookbehind allowed, and an ordinary
    # folder name would be eaten as though it were a host. That is not a leak, it is the opposite: it
    # loses a fact the policy says to keep.
    # A share whose separators are escaped, and it goes FIRST: ahead of the plain rule below.
    # Written the other way round the plain rule matches the first two of the four backslashes,
    # finds a backslash where it wants a host name, and gives up; the escaped share then goes
    # through unredacted. Order is the whole of it.
    re.compile(r"(?<![\w:\\\]])(\\\\\\\\)([^\\\s\"',;]+)"),
    re.compile(r"(?<![\w:\\\]])(\\\\)([^\\\s\"',;]+)"),
)

# The same roots, for a value that is a path and nothing else. A name here can run to the next
# separator, spaces included, which is the point: real names have spaces in them. `C:\Users\John
# Smith\` and a volume called `Kate's Backup Drive` both leak a surname to the whitespace-bounded
# rules above, and both are perfectly ordinary things to have.
_PATH_SEGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(/home/)([^/\\]+)"),
    re.compile(r"(?i)(/usr/home/)([^/\\]+)"),
    re.compile(r"(?i)(/Users/)([^/\\]+)"),
    re.compile(r"(?i)(/Volumes/)([^/\\]+)"),
    re.compile(r"(?i)([A-Za-z]:\\Users\\)([^/\\]+)"),
    # The escaped form, and the drive letter excluded from the share rule. See the list above for
    # what each of those is for; they are the same two faults, guarded here too.
    re.compile(r"(?i)([A-Za-z]:\\\\Users\\\\)([^/\\]+)"),
    re.compile(r"(?<![\w:\\\]])(\\\\\\\\)([^/\\]+)"),
    re.compile(r"(?<![\w:\\\]])(\\\\)([^/\\]+)"),
)

# Mobile is deliberately absent, and not by oversight. Sift is a server; the phone only ever runs
# the web client, and a browser sends a filename, never a path. Even if one arrived, mobile
# operating systems sandbox by UUID and package id: /var/mobile/Containers/Data/Application/
# <UUID>/ on iOS, /storage/emulated/0/ and /data/data/<package>/ on Android. There is no account
# name in them to remove.

# An email is erased outright: unlike a path, no part of it is worth keeping.
_PII_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (re.compile(r"[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}"),)


def _own_username() -> str | None:
    """The account Sift is running as, if it is worth hiding.

    A home directory is not the only place a username shows up: it appears in a library root
    mounted at /data/kate, in an ffmpeg error, in a traceback. Removing the literal string catches
    those.

    But only when the name is distinctive. Service accounts and container users are called things
    like `root`, `media`, `data`, `abc`, `app`, and blindly substituting one of those would eat
    every `/media/...` path in the log while protecting nobody. That is a live bug in at least one
    tool in this space. Short names are skipped for the same reason.
    """
    try:
        name = Path.home().name
    except (RuntimeError, OSError):
        return None

    generic = {
        "root",
        "user",
        "users",
        "home",
        "admin",
        "administrator",
        "media",
        "data",
        "app",
        "apps",
        "abc",
        "sift",
        "container",
        "nobody",
        "www-data",
        "ubuntu",
        "debian",
        "default",
        "guest",
        "service",
        "srv",
        "opt",
        "var",
        "mnt",
        "run",
    }
    if len(name) < 4 or name.lower() in generic:
        return None
    return name


def _own_hostname() -> str | None:
    """The machine's name, if it is worth hiding.

    A default macOS hostname is literally a person's name (`Kates-MacBook-Pro.local`) and it
    turns up in mDNS errors, tracebacks and network failures rather than in paths, so none of the
    path rules above would ever catch it. Windows defaults are tamer but a renamed machine is often
    `KATE-PC`.

    Skipped when it says nothing: `localhost`, a container's random hex id, or a name so short that
    substituting it would corrupt unrelated text.
    """
    try:
        name = socket.gethostname()
    except OSError:
        return None

    base = name.split(".")[0]
    generic = {"localhost", "sift", "server", "nas", "ubuntu", "debian", "raspberrypi"}
    if len(base) < 4 or base.lower() in generic:
        return None
    # A container id: 12 hex characters, unique per run and meaningless to redact.
    if len(base) == 12 and all(c in "0123456789abcdef" for c in base.lower()):
        return None
    return base


_OS_USERNAME = _own_username()
_HOSTNAME = _own_hostname()


def _hide_own_names(text: str) -> str:
    """The literal account and machine names, wherever they appear.

    The home-directory rules only catch a username inside a home directory. It also shows up in a
    library root mounted at /data/kate, in an ffmpeg error, in a traceback.
    """
    result = text

    if _OS_USERNAME:
        for sep in ("/", "\\"):
            result = result.replace(f"{sep}{_OS_USERNAME}{sep}", f"{sep}{REDACTED}{sep}")
            if result.endswith(f"{sep}{_OS_USERNAME}"):
                result = result[: -len(_OS_USERNAME)] + REDACTED

    if _HOSTNAME:
        result = re.sub(rf"(?i)\b{re.escape(_HOSTNAME)}\b", REDACTED, result)

    return result


def hide_identity(value: str) -> str:
    """Remove names from free text: a traceback, ffmpeg's stderr, a downloader's output.

    Conservative on purpose. A name here runs only to the next whitespace, because the text around
    it is prose: `C:\\Users\\Kate ran the job` must not have the sentence swallowed along with the
    name. Whole-value paths go through `hide_identity_in_path`, which can be exact.
    """
    result = value
    for pattern in _HOME_SEGMENT_PATTERNS:
        result = pattern.sub(lambda m: m.group(1) + REDACTED, result)
    return _hide_own_names(result)


def hide_identity_in_path(value: str) -> str:
    """Remove names from a value that is known to be a path in its entirety.

    Because the whole string is one path, a name can be read all the way to the next separator,
    which matters, since real names contain spaces. `C:\\Users\\John Smith\\` and a macOS volume
    called `Kate's Backup Drive` both leak the surname to a whitespace-bounded rule, and both are
    ordinary things for a person to have.
    """
    result = value
    for pattern in _PATH_SEGMENT_PATTERNS:
        result = pattern.sub(lambda m: m.group(1) + REDACTED, result)
    return _hide_own_names(result)


# Set at boot, and then by the library's `logs.hide_personal` (`apply_log_preferences`). Secrets
# stay hidden regardless.
_redact_personal = True


def redacts_personal() -> bool:
    """Whether personal detail is being hidden, so a process started by this one can be set the
    same way."""
    return _redact_personal


def level_name() -> str:
    """The level the pipeline is at, by name, for the same reason."""
    return logging.getLevelName(logging.getLogger().level)


def path_facts(path: str | Path) -> dict[str, object]:
    """The things about a file you cannot tell by looking at its path.

    The path itself is in the log (with the names removed), so extension and depth are already
    visible and are not repeated here. What is not visible is whether the file is actually there,
    whether Sift can read it, and how big it is, and that is usually the whole question.

    `path_readable=False` with `path_exists=True` is the one that saves an afternoon: the file is
    present but the process cannot open it, which is a permissions or ownership problem, not a
    missing file.
    """
    p = Path(path)
    try:
        exists = p.exists()
        readable = os.access(p, os.R_OK) if exists else False
        size = p.stat().st_size if exists and p.is_file() else None
    except OSError:
        exists = readable = False
        size = None

    return {
        "path_exists": exists,
        "path_readable": readable,
        "path_size": size,
    }


def hashed(value: object) -> str:
    """A stable, opaque handle for a sensitive value.

    Two log lines about the same URL carry the same handle and can be correlated; neither
    reveals the URL. Truncated, because the goal is correlation within one log file, not
    cryptographic commitment, and a full digest is just noise to read past.
    """
    digest = hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()
    return digest[:12]


def _key_is_secret(key: str) -> bool:
    lowered = key.lower()
    if lowered in _ALLOWED_KEYS:
        return False
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _key_is_personal(key: str) -> bool:
    lowered = key.lower()
    if lowered in _ALLOWED_KEYS:
        return False
    return any(part in lowered for part in _PERSONAL_KEY_PARTS)


_PATHLIKE_KEY_PARTS = ("path", "dir", "folder", "home")


def _key_is_pathlike(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _PATHLIKE_KEY_PARTS)


def redact(value: Any, _key: str = "", *, always_personal: bool = False) -> Any:
    """Hide credentials always, and personal identifiers unless an admin has opted out.

    Also the entry point for subprocess output: the downloaders write their destination path to
    stdout, and that stdout is logged verbatim when a job fails.

    `always_personal` forces the personal reduction on even when an admin has turned it off for
    logs. It exists for text that does not merely get logged but gets *persisted*: a stored job
    error rides out of the machine in a backup or a diagnostics export, where an admin's choice
    to see names in their own live log stream does not travel with it. Credentials are hidden
    either way; this only concerns names.
    """
    personal = _redact_personal or always_personal

    if _key and _key_is_secret(_key):
        return REDACTED

    if _key and personal and _key_is_personal(_key):
        # A path keeps its shape and loses its names; a username or email field is nothing but a
        # name, so there is nothing to keep. A path handed in as a Path object rather than a str is
        # reduced the same way, not blanked: it is the same value wearing a different type.
        if _key_is_pathlike(_key) and isinstance(value, str | os.PathLike):
            return hide_identity_in_path(str(value))
        return REDACTED

    if isinstance(value, str):
        result = value
        for pattern in _SECRET_VALUE_PATTERNS:
            result = pattern.sub(_redact_secret, result)
        if personal:
            # Applied to all text, not just fields that look like paths. Paths turn up inside
            # tracebacks, ffmpeg stderr and downloader output, none of which arrive under a
            # helpfully-named key.
            result = hide_identity(result)
            for pattern in _PII_VALUE_PATTERNS:
                result = pattern.sub(REDACTED, result)
        return result

    if isinstance(value, dict):
        return {k: redact(v, str(k), always_personal=always_personal) for k, v in value.items()}

    if isinstance(value, list | tuple | set):
        rendered = [redact(item, always_personal=always_personal) for item in value]
        return set(rendered) if isinstance(value, set) else type(value)(rendered)

    if isinstance(value, Exception):
        # An exception's message routinely quotes the path that caused it.
        return redact(str(value), always_personal=always_personal)

    return value


def _redaction_processor(
    _logger: object, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Applied to every event, so nothing reaches a handler unscrubbed."""
    return {k: redact(v, str(k)) for k, v in event_dict.items()}


#: The level the process was STARTED at, which is what `SIFT_LOG_LEVEL` says.
#:
#: A module-level record rather than a re-read of the environment, because the environment is not
#: the only caller: the tests configure a level directly, and a re-read would answer about a
#: variable that had nothing to do with the logger actually running. INFO until something says
#: otherwise, which is what `configure_logging` defaults to.
_boot_level = logging.INFO


def apply_log_preferences(
    *, detailed: bool, per_file_bytes: int, hide_personal: bool | None = None
) -> None:
    """Move the running logger onto what the settings say, without a restart.

    ## Why this is not simply `configure_logging` again

    Because logging is set up before there is a database to ask. The environment decides what the
    boot lines are written with (there is nowhere else the answer could come from at that moment)
    and the stored preference takes over once the library is open. Tearing the handlers down and
    rebuilding them would close and reopen the file mid-run and lose whatever a rotation was part
    way through; the two things somebody is actually choosing are a level and a cap, and both are
    attributes of objects that are already there.

    A file handler that was never attached (no log file, or a boot value of zero) stays absent.
    Making one here would put a file on disk somebody never asked for, from a screen about how
    large it may get.

    PER FILE, not the total: the setting is what the whole log may take, and `log_settings` divides
    it by the number of files that will exist. The handler has only ever understood one file.

    `hide_personal` is the library's `Hide personal details in the log`, taken from the next line
    written. None leaves the answer as it is.
    """
    global _redact_personal
    if hide_personal is not None:
        _redact_personal = hide_personal
    root = logging.getLogger()
    # NEVER LOUDER THAN THE MACHINE WAS TOLD TO BE.
    #
    # Not `logging.INFO` flat, which would make `SIFT_LOG_LEVEL` a setting that holds until the
    # database opens and is then thrown away: a server started at WARNING would go back to INFO a
    # second later, with nothing said.
    #
    # The switch stays what it says it is. `Detailed` is a request for MORE, so it still takes the
    # level down to DEBUG; turning it off returns to whatever the environment asked for, and where
    # that is louder than INFO (a boot at DEBUG) that level is kept exactly.
    root.setLevel(logging.DEBUG if detailed else max(_boot_level, logging.INFO))
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler):
            # `maxBytes` is read on every emit, so this takes effect on the next line written.
            lowered = per_file_bytes < handler.maxBytes
            handler.maxBytes = per_file_bytes
            # A lowered size trims the OLDER files at once: the handler only ever looks at the one
            # it writes, so a 27 MB rotated file would have kept the total over the setting until
            # five more rotations pushed it out.
            if lowered:
                from sift.kernel.log_settings import fit_within

                handler.acquire()
                try:
                    fit_within(
                        Path(handler.baseFilename),
                        handler.backupCount,
                        per_file_bytes * (handler.backupCount + 1),
                    )
                finally:
                    handler.release()


class _CappedRotatingFileHandler(RotatingFileHandler):
    """A rotating handler whose files, taken together, never exceed the setting.

    The standard handler rotates by the size of the one file it writes and keeps `backupCount`
    older ones whatever their size, so a setting lowered while big rotated files exist is exceeded
    for as long as they last. Every rollover here ends by trimming the oldest files until the whole
    set fits in `maxBytes * (backupCount + 1)`, the total the setting stands for.
    """

    def doRollover(self) -> None:
        super().doRollover()
        from sift.kernel.log_settings import fit_within

        fit_within(
            Path(self.baseFilename), self.backupCount, self.maxBytes * (self.backupCount + 1)
        )


def configure_logging(
    level: str = "INFO",
    *,
    redact_personal: bool = True,
    log_file: Path | None = None,
    max_bytes: int = 0,
    backups: int = 0,
) -> None:
    """Install the logging pipeline. Called once, at boot.

    EVERY log record goes through the scrubber, including records from libraries that know
    nothing about it. That is not belt-and-braces, it is the requirement: uvicorn's access log
    prints the raw request line, and a Sift URL can carry a library path. Redacting only Sift's
    own logger while the web server writes the same data to the same file, one line below, would
    be pure theatre.

    `redact_personal=False` reveals paths and usernames: an admin's own filesystem, which was
    never hidden from them anywhere else. Secrets stay hidden either way.

    A `log_file` gets the same lines as the stream, through the same formatter, so the two cannot
    disagree about what happened or about what is scrubbed out of it. It exists because the stream
    goes to the container's output and dies with the container: an image rebuilt after a problem
    takes the record of that problem with it. A file under the data directory survives that, and it
    is capped and rotated so it can never be the reason a disk fills up.
    """
    global _redact_personal
    _redact_personal = redact_personal

    level_name = level.upper()

    # Runs on Sift's events and on records forwarded from the standard library alike.
    #
    # `format_exc_info` renders an exception to a traceback *string* under an `exception` key,
    # and it sits BEFORE the scrubber on purpose: a traceback names the path that caused the
    # failure, and left as an `exc_info` tuple it would be turned into text later, by the
    # formatter, downstream of the one processor that redacts. Stringifying it here puts it in
    # front of the scrubber like every other field. `format_exc_info`, not `dict_tracebacks`,
    # which would also capture each frame's local variables, and a local is exactly where a
    # password or an unwrapped key would be sitting when the thing blew up. A traceback string is
    # everything an operator needs and nothing a redactor cannot reduce.
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        _redaction_processor,
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # `foreign_pre_chain` is what puts third-party records through the scrubber.
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [handler]

    if log_file is not None and max_bytes > 0:
        # A log that cannot be written is not a reason to refuse to boot. A read-only data
        # directory, a full disk and a permission the image does not have all end up here, and in
        # every one of them the right answer is to carry on with the stream and say so once.
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            rotating = _CappedRotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
            rotating.setFormatter(formatter)
            handlers.append(rotating)
        except OSError as exc:
            # Printed rather than logged: the logger is what is being set up here, and a handler
            # that could not be attached cannot carry the news that it could not be attached.
            print(f"sift: could not open the log file {log_file}: {exc}", file=sys.stderr)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level_name)
    # Kept so the stored preference cannot make the log LOUDER than this. See
    # `apply_log_preferences`, which reads it.
    global _boot_level
    _boot_level = logging.getLevelNamesMapping().get(level_name, logging.INFO)

    # uvicorn installs its own handlers on these; leaving them attached would emit a second,
    # unscrubbed copy of every line straight past the formatter above.
    for name in ("uvicorn", "uvicorn.error"):
        third_party = logging.getLogger(name)
        third_party.handlers = []
        third_party.propagate = True

    # The access log is silenced HERE, and `access_log=False` at the call to uvicorn is not what
    # does it.
    #
    # **uvicorn IGNORES that flag.** It decides whether to log a request with
    # `self.access_log = self.access_logger.hasHandlers()` (the flag is never read on this path)
    # and `hasHandlers()` walks up the tree. So the two lines above, applied to `uvicorn.access`,
    # would RE-ENABLE it: the handlers cleared, propagation left on, the logger reaches the root
    # handlers installed a few lines up, and uvicorn logs every request after all.
    #
    # Why that matters: Sift's own middleware records each request with the route TEMPLATE
    # (`/assets/{asset_id}/thumb`), precisely so an id never lands in a file somebody pastes into a
    # bug report. uvicorn's line is the raw request target (`/api/assets/<the id>/thumb`), which
    # beside it would defeat the redaction rather than duplicate it.
    #
    # `propagate = False` rather than a level or a filter: it is the one setting that makes
    # `hasHandlers()` false, which is the actual question being asked.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

    if not redact_personal:
        # Said plainly and at every boot. The person who turned this on six months ago is not
        # the person pasting a log into an issue today, even when they are.
        get_logger(__name__).warning(
            "log.unredacted",
            detail=(
                "Logs now contain file paths and usernames. They are no longer safe to share, "
                "post in a bug report, or attach to a support request. Unset SIFT_LOG_UNREDACTED "
                "to turn this back off."
            ),
        )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """The logger. Events are named, with structured fields:

        log.info("download.started", asset_id=asset_id, host=hashed(url))

    Log the event, not the payload.
    """
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


# --- Security events ---------------------------------------------------------------------

_security_log = None


def security_event(kind: str, **fields: Any) -> None:
    """Record something a security investigation would need: failed logins, lockouts, access
    denials, quarantined uploads, rejected outbound requests.

    Kept to one shape so the whole class is greppable and no slice invents its own. Redaction
    and auditability are not in tension here: the answer to both is to log *that* it happened,
    who to, and when, and never *what* the content was. An access denial is worth recording; the
    path they were denied is not, and recording it would put the very thing they could not see
    into a file that is easier to leak than the database.
    """
    global _security_log
    if _security_log is None:
        _security_log = get_logger("sift.security")
    _security_log.warning(f"security.{kind}", **fields)


def user_safe_message(error: Exception) -> str:
    """What an error is allowed to say to someone who is not an admin.

    Error text leaks: a stack trace names paths, a database error quotes the query, a downloader
    error quotes the URL. Guests get a generic sentence and the detail goes to the log, where
    only the operator reads it.
    """
    if isinstance(error, PermissionError):
        return "You do not have access to that."
    if isinstance(error, FileNotFoundError):
        return "That is no longer available."
    if isinstance(error, TimeoutError):
        return "That took too long. Try again."
    return "Something went wrong. If it keeps happening, ask the person running this Sift."


# --- Timing ------------------------------------------------------------------------------


class Timing:
    """What a timed block can tell its own record, while it is still running.

    A block that has to WAIT for something before it can start (a connection out of a pool, a
    lock) spends that wait inside the timed region, so the record would blame the work for time
    the work never had: an eighteen-second queue for a database connection logged as an
    eighteen-second query of a statement that takes milliseconds on its own.

    So a block that queues says when its wait ended, and the record then carries three numbers that
    cannot be confused with one another: how long it waited, how long it ran, and the total.

    It can also report **how much it moved**, which is the other thing a duration alone cannot say.
    A read that hands back three thousand rows and one that hands back one look identical in a
    timing record and are not remotely the same piece of work: a row leaving SQLite for Python costs
    a turn of the interpreter, so the row count is what decides whether a query survives the machine
    being busy: the same scan returning thousands of rows can cost milliseconds on an idle machine
    and over a minute beside two busy threads, while COUNTing it stays cheap.
    """

    __slots__ = ("_acquired", "_measured", "_started", "ran_ms")

    def __init__(self, started: float) -> None:
        self._started = started
        self._acquired: float | None = None
        self._measured: dict[str, Any] = {}
        #: What the block was judged on, once it has ended: the time it RAN, which is the total
        #: where nothing declared a wait. Readable after the block, and it is there so a caller
        #: can learn what its own work usually costs without timing it a second time: two clock
        #: reads are cheap, but a second measurement of the same block is a second number that can
        #: disagree with the one in the record.
        self.ran_ms = 0.0

    def measured(self, **facts: Any) -> None:
        """Something the block learned about its own work, for the record it will emit.

        Merged rather than replaced, so two calls both land and neither has to know about the other.
        """
        self._measured.update(facts)

    def acquired(self) -> None:
        """Whatever this block was waiting for, it has it now.

        Called once, from inside the block, the moment the wait is over. Called twice, the first
        answer stands: the wait ended when it ended, and a second call is a nested acquisition that
        is part of the work rather than part of the queue.
        """
        if self._acquired is None:
            self._acquired = time.perf_counter()

    def _split(self, ended: float) -> tuple[float, dict[str, float]]:
        """The total, and the two numbers that only exist when a wait was declared."""
        total_ms = round((ended - self._started) * 1000, 2)
        if self._acquired is None:
            self.ran_ms = total_ms
            return total_ms, {}
        waited_ms = round((self._acquired - self._started) * 1000, 2)
        self.ran_ms = round(total_ms - waited_ms, 2)
        return total_ms, {
            "waited_ms": waited_ms,
            "ran_ms": self.ran_ms,
        }


#: Where the record asks how backlogged the event loop is, in seconds. Filled in at boot.
#:
#: Empty by default so nothing depends on it having been wired: with no source the records carry
#: no backlog reading.
_backlog_source: Callable[[], float] | None = None

#: Above this much backlog, a slow reading is not evidence about the work.
#:
#: When the loop's ready queue takes this long to drain, everything waiting on it is late by about
#: that much, including the moment a finished query's result is handed back. The reading is then
#: mostly a measure of the queue, and escalating it names the wrong component.
#:
#: A lookup costing a fraction of a millisecond can be recorded at seconds while the queue drains,
#: and read as a slow query; tuning it would achieve nothing at all.
BACKLOGGED_SECONDS = 0.25


def set_loop_backlog(source: Callable[[], float] | None) -> None:
    """Tell the records where to ask how backlogged the loop is. Called once, at boot."""
    global _backlog_source
    _backlog_source = source


#: Where a finished record is also handed for the "what is costing the time" report. Filled at boot.
#:
#: A seam rather than an import: the record keeps no state of its own and the thing that does lives
#: with the other watches, which already import this module.
_work_sink: Callable[[str, float], None] | None = None


def set_work_sink(sink: Callable[[str, float], None] | None) -> None:
    """Tell the records where to report what they measured. Called once, at boot."""
    global _work_sink
    _work_sink = sink


#: A second reader of the same measurements, beside the one above: the ledger, which files each
#: stage's time against the run it belongs to. Two names rather than one list because the two
#: are set at different moments of the boot and cleared at different moments of the shutdown.
_stage_sink: Callable[[str, float], None] | None = None


def set_stage_sink(sink: Callable[[str, float], None] | None) -> None:
    """Tell the records where the ledger is, or that there is none."""
    global _stage_sink
    _stage_sink = sink


def _report_work(stage: str, milliseconds: float) -> None:
    # A diagnostic must never be the reason a request fails.
    if _work_sink is not None:
        with suppress(Exception):
            _work_sink(stage, milliseconds)
    if _stage_sink is not None:
        with suppress(Exception):
            _stage_sink(stage, milliseconds)


#: How many rows one read may hand back before the record is escalated to a warning.
#:
#: Not a page size and not a limit on what the application may do: it is the line above which a
#: read stops being a read and becomes a latency risk that only shows up under load. A page of the
#: grid is 200; the sweeps that legitimately walk the library go through the lane and are counted
#: there too, which is the point: if something is above this on a request path, it wants finding.
WIDE_READ_ROWS = 500

#: Where a finished record reports how many rows it moved. Filled at boot, like `_work_sink`.
_rows_sink: Callable[[str, int, str | None], None] | None = None


def set_rows_sink(sink: Callable[[str, int, str | None], None] | None) -> None:
    """Tell the records where to report how wide their reads were. Called once, at boot."""
    global _rows_sink
    _rows_sink = sink


def _report_rows(stage: str, rows: int, sql: str | None) -> None:
    if _rows_sink is None:
        return
    # A diagnostic must never be the reason a request fails.
    with suppress(Exception):
        _rows_sink(stage, rows, sql)


#: The standard-library number behind each level name this module uses, so the hook can ask whether
#: a record would survive before it pays to build one. A dict rather than `getattr(logging, name)`:
#: the names here are the ones `timing_hook` accepts, and a typo should be a KeyError in a test
#: rather than an AttributeError in production at the moment something slow finally happens.
#: The logger every timing record is written to. Named once so the hook's level check and the hook's
#: writer can never end up asking about two different loggers.
_TIMING_LOGGER = "sift.timing"

_LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _loop_backlog_ms() -> float | None:
    """How far behind the loop is right now, in milliseconds, or None when nothing is watching."""
    if _backlog_source is None:
        return None
    try:
        return round(_backlog_source() * 1000, 1)
    except Exception:
        # A diagnostic must never be the reason a request fails.
        return None


@contextmanager
def timing_hook(
    stage: str,
    *,
    level: str = "info",
    slow_ms: float | None = None,
    sql: str | None = None,
    **fields: Any,
) -> Iterator[Timing]:
    """Time a block and emit a structured record.

    Instrumentation goes in from the start, not when something turns out to be slow. Retrofitting
    it means editing every slice at exactly the moment the answer is needed, and the code that
    matters is always the code nobody instrumented.

    Recorded on failure too: the duration before something gave up is usually the interesting
    number.

    `level` is the level a normal completion is logged at; it defaults to info. A caller that
    fires many times a request (the per-statement
    database timings) passes `level="debug"` so it is off by default and one
    `SIFT_LOG_LEVEL=DEBUG` away when a query needs chasing, rather than a wall of SQL in the
    ordinary log. `slow_ms` escalates a run past that many milliseconds to warning regardless, so a
    genuinely slow one still shows even when the stage is quiet; and a run that *failed* is
    escalated the same way when the stage was demoted, because a failed query hidden at debug is a
    failure nobody sees.

    **A block that queues should say so**. See `Timing.acquired`. `slow_ms` is then measured
    against the time it RAN rather than the time it took, so a fast statement that waited a long
    while for a connection is never escalated as a slow statement. What that wait means is a
    question about the pool, and the pool has its own watch; answering it here would blame
    queries that measure single-digit milliseconds.

    **`sql` is named rather than left among the fields, and it never reaches the record.** It is
    what the wide-read report groups by; written into every escalated line it would fill the log
    with thousands of warnings each carrying forty lines of SQL. A statement is identified in the
    log by its NAME, which the database layer passes beside it, and the text goes only to the
    report on the health screen where somebody has asked to see it.
    """
    log = get_logger(_TIMING_LOGGER)
    started = time.perf_counter()
    timing = Timing(started)
    failed = False
    try:
        yield timing
    except BaseException:
        failed = True
        raise
    finally:
        elapsed_ms, split = timing._split(time.perf_counter())
        # What `slow_ms` judges: the work, whenever the work can be told apart from the wait.
        judged_ms = split.get("ran_ms", elapsed_ms)
        # And how far behind the loop was, because that is the second wait hiding inside `ran_ms`.
        # A finished statement's result is handed back through the loop like everything else, so a
        # drained-queue time of seconds lands in the reading and reads as a slow statement.
        backlog_ms = _loop_backlog_ms()
        behind = backlog_ms is not None and backlog_ms > BACKLOGGED_SECONDS * 1000
        if backlog_ms is not None:
            split = {**split, "loop_backlog_ms": backlog_ms}
        chosen = level
        if (slow_ms is not None and not failed and judged_ms > slow_ms and not behind) or (
            failed and level == "debug"
        ):
            chosen = "warning"
        _report_work(stage, elapsed_ms)
        measured = timing._measured
        rows = measured.get("rows")
        if isinstance(rows, int):
            _report_rows(stage, rows, sql)
            # A read wide enough to be a latency problem whatever it costs today. Escalated on the
            # count rather than on the clock, because the clock only says so once the machine is
            # busy, by which time it is somebody reporting the application is unusable, not a log
            # line. See `WIDE_READ_ROWS`.
            if rows >= WIDE_READ_ROWS and not failed:
                chosen = "warning"
        # Nothing above this line may be skipped: the health readings and the escalation to warning
        # are what this exists for, and they are the same whether or not anybody is reading the log.
        # Everything BELOW it is the record itself, and building one that is about to be discarded
        # is the most-repeated waste in the application.
        #
        # **Sift's logger is not a filtering one.** `configure_logging` sets
        # `wrapper_class=structlog.stdlib.BoundLogger`, which runs the whole processor chain,
        # including the redaction scrubber, and only then hands a record to the standard library,
        # which drops it for being below the level. So a `debug` call at INFO is not free and is not
        # nearly free: about **0.04 ms**, against **0.0001 ms** for the two clock reads that are the
        # actual measurement. Every database statement carries one, so a scan making a hundred
        # thousand reads would pay four seconds for records nobody would ever see.
        #
        # Asked of `chosen` rather than of `level`, and that ordering is load-bearing: a statement
        # escalated to warning by `slow_ms` or by `WIDE_READ_ROWS` must still be written even though
        # its ordinary level is off. Guarding on `level` would silence exactly the lines worth
        # having.
        #
        # Asked of the STANDARD LIBRARY logger rather than of the structlog one, and that is not a
        # detail either. structlog hands the record to the standard library, which is what actually
        # applies the level, so this asks the thing that decides. The bound logger cannot be
        # asked: before `configure_logging` runs, structlog's default wrapper is a filtering one
        # with no `isEnabledFor` at all, so calling it there is an AttributeError rather than a
        # false, which a test run as a subset would meet, where a whole-file run configures
        # logging in an earlier test and leaves it configured.
        #
        # Written as a condition and never as an early `return`. This is a `finally` block, and a
        # `return` in one DISCARDS the exception on its way out, so the version of this that reads
        # more nicely would silently swallow every database error the hook is wrapped around (Ruff
        # B012).
        if logging.getLogger(_TIMING_LOGGER).isEnabledFor(_LOG_LEVELS[chosen]):
            getattr(log, chosen)(
                "timing",
                stage=stage,
                duration_ms=elapsed_ms,
                failed=failed,
                **split,
                **measured,
                **fields,
            )


def timed(stage: str) -> Callable[[F], F]:
    """Decorator form of `timing_hook`. Works on sync and async callables."""

    def decorate(fn: F) -> F:
        import inspect

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with timing_hook(stage):
                    return await fn(*args, **kwargs)

            return cast(F, async_wrapper)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with timing_hook(stage):
                return fn(*args, **kwargs)

        return cast(F, wrapper)

    return decorate
