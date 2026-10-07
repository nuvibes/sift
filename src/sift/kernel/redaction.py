# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is taken out of a log line before it is written or shared.

Credentials go always. Names go while `logs.hide_personal` is on, and always from a copy that leaves
the machine (`redacted_line`). A path loses only the name in it: URLs, hosts, error text and counts
stay, since a diagnosis needs them. Standard library only, so it runs where native code may crash.
"""

from __future__ import annotations

import functools
import json
import os
import re
import socket
from pathlib import Path
from typing import Any

REDACTED = "[redacted]"

# Never written, in any mode.
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
    # The key for saved site logins, held only in memory.
    "master_key",
    # An admin's free text about somebody: identifying, never diagnostic.
    "notes",
)

# A path field keeps its shape without the name; a name field goes whole.
_PERSONAL_KEY_PARTS = (
    "path",
    "dir",
    "folder",
    "email",
    "username",
    "user_name",
    "home",
)

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
        # A route template, not a place on disk.
        "route",
    }
)

# The value after a credential's name in free text, at any length; the name stays.
_KEY_LED_SECRET = re.compile(
    r"(?P<keep>\b(?:password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|apikey"
    r"|access[_-]?token|refresh[_-]?token|auth[_-]?token|csrf[_-]?token|sessionid|phpsessid"
    r"|session[_-]?id|token|cookie)\s*[=:]\s*)"
    r"[^\s&;,\"']+",
    re.IGNORECASE,
)
# Only the userinfo of `user:pass@host` goes. First, so the run rule can't split the credential.
_URL_USERINFO = re.compile(r"(?<=://)[^/?#@\s]*:[^/?#@\s]*(?=@)")
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _URL_USERINFO,
    # A cookie header's pairs all go, to its quote or the line's end.
    re.compile(r"(?i)(?P<keep>\b(?:set-)?cookie['\"]?\s*[=:]\s*['\"]?)[^'\"\r\n]+"),
    # A cookies file line, tabs real or escaped: its last field.
    re.compile(
        r"(?P<keep>(?:\t|\\t)(?:TRUE|FALSE)(?:\t|\\t)[^\t\r\n]*?(?:\t|\\t)(?:TRUE|FALSE)"
        r"(?:\t|\\t)\d+(?:\t|\\t)[^\t\r\n\\]+(?:\t|\\t))[^\t\r\n'\"\\]+"
    ),
    # A tunnel's own address, which its provider gave this one account.
    re.compile(r"(?i)(?P<keep>\bAddress\s*=\s*)[0-9a-f.:]+/\d{1,3}(?:\s*,\s*[0-9a-f.:]+/\d{1,3})*"),
    # A run of 32 or more, bare hex too (a digest and a raw key look alike), unless dotted parts
    # ending in an extension make it a file's name (`backup-0.1.203.zip`).
    re.compile(
        r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])"
        r"(?!(?:\.[A-Za-z0-9_\-]+)*\.[A-Za-z0-9]{1,5}(?![A-Za-z0-9_\-]|\.[A-Za-z0-9]))"
    ),
    re.compile(r"(?i)\b(bearer|basic)\s+\S+"),
    _KEY_LED_SECRET,
    re.compile(r"\b[A-Za-z0-9+/]{23,}={1,2}(?![A-Za-z0-9+/=])"),
)


# What every secret rule needs somewhere in a value; a value with none of it skips all eight.
_SECRET_HINT = re.compile(
    r"://|\t|\\t|[A-Za-z0-9_\-]{32,}|[A-Za-z0-9+/]{23,}="
    r"|(?i:cookie|address|bearer|basic|password|passwd|pwd|secret|api[_-]?key|apikey"
    r"|token|sessionid|phpsessid|session[_-]?id)"
)


def _redact_secret(match: re.Match[str]) -> str:
    """The marker after any `keep` label (`token=`), so the log still says what was hidden."""
    keep = match.groupdict().get("keep")
    return (keep or "") + REDACTED


# Only the name after a home root: `/home/[redacted]/Videos/clip.mp4`.
_SPACED_NAME = r"([^\\/\s\"',;:]+(?: [^\\/\s\"',;:]+){1,3})(?=[\\/])"
_NAME_TO_END = r"([^\\/\s\"',;:]+(?:(?: [^\\/\s\"',;:]+){1,3}(?=[\"']|$)| [A-Z][^\"'\r\n]*))"
_HOME_SEGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Spaced names first, ended by a separator, or the rules below leave the surname.
    re.compile(r"(?i)(?<![\w/])(/home/|/usr/home/|/Users/|/Volumes/)" + _SPACED_NAME),
    re.compile(r"(?i)(?<![\w:\\])([A-Za-z]:\\(?:\\)?Users\\(?:\\)?)" + _SPACED_NAME),
    # A name ending its line or quote with no separator after it goes whole, and so does all after a
    # first word whose next starts with a capital: a surname costs the prose behind it.
    re.compile(r"(?<![\w/])((?i:/home/|/usr/home/|/Users/|/Volumes/))" + _NAME_TO_END),
    re.compile(r"(?<![\w:\\])((?i:[A-Za-z]:\\(?:\\)?Users\\(?:\\)?))" + _NAME_TO_END),
    re.compile(r"(?i)(?<![\w/])(/home/)([^/\s\"',;]+)"),
    re.compile(r"(?i)(?<![\w/])(/usr/home/)([^/\s\"',;]+)"),
    re.compile(r"(?i)(?<![\w/])(/Users/)([^/\s\"',;]+)"),
    # People name drives after themselves.
    re.compile(r"(?i)(?<![\w/])(/Volumes/)([^/\s\"',;]+)"),
    re.compile(r"(?i)(?<![\w:\\])([A-Za-z]:\\Users\\)([^\\\s\"',;]+)"),
    # Escaped by a JSON encoder; else the share rule takes `Users` and leaves the name.
    re.compile(r"(?i)(?<![\w:\\])([A-Za-z]:\\\\Users\\\\)([^\\\s\"',;]+)"),
    # Shares, whose host is usually the person. `:` keeps `C:\\` from reading as one; `]` keeps a
    # `[redacted]` from anchoring a new match. The escaped form goes first.
    re.compile(r"(?<![\w:\\\]])(\\\\\\\\)([^\\\s\"',;]+)"),
    re.compile(r"(?<![\w:\\\]])(\\\\)([^\\\s\"',;]+)"),
)

# For a value that is wholly a path, so a name runs to the next separator, spaces and all.
_PATH_SEGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(/home/)([^/\\]+)"),
    re.compile(r"(?i)(/usr/home/)([^/\\]+)"),
    re.compile(r"(?i)(/Users/)([^/\\]+)"),
    re.compile(r"(?i)(/Volumes/)([^/\\]+)"),
    re.compile(r"(?i)([A-Za-z]:\\Users\\)([^/\\]+)"),
    re.compile(r"(?i)([A-Za-z]:\\\\Users\\\\)([^/\\]+)"),
    re.compile(r"(?<![\w:\\\]])(\\\\\\\\)([^/\\]+)"),
    re.compile(r"(?<![\w:\\\]])(\\\\)([^/\\]+)"),
)

# The device that opened Sift, in the web server's request and connection lines; its port stays to
# pair a connection's lines. Any other address is a host a diagnosis needs.
_CLIENT_ADDRESS = re.compile(
    r"(?<!\S)(?:(?:[0-9A-Fa-f]{0,4}:){0,6}\d{1,3}(?:\.\d{1,3}){3}"
    r"|[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}?(?:%\w+)?)"
    r'(?=(?::\d{1,5})? - "(?:WebSocket |[A-Z]+ \S* HTTP/))'
)
# An email has nothing worth keeping.
_PII_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}"),
    _CLIENT_ADDRESS,
)


def _own_username() -> str | None:
    """The account's name, unless short or generic: `media` would eat every `/media/...` path."""
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
    """The machine's name, unless short, generic or a container's hex id."""
    try:
        name = socket.gethostname()
    except OSError:
        return None

    base = name.split(".")[0]
    generic = {"localhost", "sift", "server", "nas", "ubuntu", "debian", "raspberrypi"}
    if len(base) < 4 or base.lower() in generic:
        return None
    if len(base) == 12 and all(c in "0123456789abcdef" for c in base.lower()):
        return None
    return base


_OS_USERNAME = _own_username()
_HOSTNAME = _own_hostname()
_hostname_pattern: tuple[str, re.Pattern[str]] | None = None


def _hostname_matcher(name: str) -> re.Pattern[str]:
    """The machine's name as a word, compiled once per name (a test may set another)."""
    global _hostname_pattern
    if _hostname_pattern is None or _hostname_pattern[0] != name:
        _hostname_pattern = (name, re.compile(rf"(?i)\b{re.escape(name)}\b"))
    return _hostname_pattern[1]


def _hide_own_names(text: str) -> str:
    """The account and machine names wherever they appear: `/data/kate`, an ffmpeg error."""
    result = text

    if _OS_USERNAME and _OS_USERNAME in result:
        for sep in ("/", "\\"):
            result = result.replace(f"{sep}{_OS_USERNAME}{sep}", f"{sep}{REDACTED}{sep}")
            if result.endswith(f"{sep}{_OS_USERNAME}"):
                result = result[: -len(_OS_USERNAME)] + REDACTED

    if _HOSTNAME and _HOSTNAME.lower() in result.lower():
        result = _hostname_matcher(_HOSTNAME).sub(REDACTED, result)

    return result


# What every home-segment and path rule needs somewhere in the text, in a decoded value or in a
# JSON line (whose backslashes are doubled: a single one is still inside the pair).
_HOME_HINTS = ("/home/", "/users/", "/volumes/", "users\\", "\\\\")


def _may_hold_a_home(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _HOME_HINTS)


def hide_identity(value: str) -> str:
    """Names out of free text, each ending at whitespace unless a separator ends it."""
    result = value
    if _may_hold_a_home(result):
        for pattern in _HOME_SEGMENT_PATTERNS:
            result = pattern.sub(lambda m: m.group(1) + REDACTED, result)
    return _hide_own_names(result)


def hide_identity_in_path(value: str) -> str:
    """Names out of a value that is wholly a path."""
    result = value
    if _may_hold_a_home(result):
        for pattern in _PATH_SEGMENT_PATTERNS:
            result = pattern.sub(lambda m: m.group(1) + REDACTED, result)
    return _hide_own_names(result)


@functools.lru_cache(maxsize=4096)
def _key_is_secret(key: str) -> bool:
    lowered = key.lower()
    if lowered in _ALLOWED_KEYS:
        return False
    return any(part in lowered for part in _SECRET_KEY_PARTS)


@functools.lru_cache(maxsize=4096)
def _key_is_personal(key: str) -> bool:
    lowered = key.lower()
    if lowered in _ALLOWED_KEYS:
        return False
    return any(part in lowered for part in _PERSONAL_KEY_PARTS)


_PATHLIKE_KEY_PARTS = ("path", "dir", "folder", "home")


def _key_is_pathlike(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _PATHLIKE_KEY_PARTS)


def _redact_text(value: str, personal: bool) -> str:
    """Free text: the secret rules where a hint of one is present, then names and addresses."""
    result = value
    if _SECRET_HINT.search(result) is not None:
        for pattern in _SECRET_VALUE_PATTERNS:
            result = pattern.sub(_redact_secret, result)
    if personal:
        # Paths arrive inside tracebacks and tool output, under no key.
        result = hide_identity(result)
        if "@" in result or ' - "' in result:
            for pattern in _PII_VALUE_PATTERNS:
                result = pattern.sub(REDACTED, result)
    return result


def redact(value: Any, _key: str = "", *, personal: bool) -> Any:
    """Credentials always, and personal identifiers when `personal` says so."""
    if _key and _key_is_secret(_key):
        return REDACTED

    if _key and personal and _key_is_personal(_key):
        # A Path object is reduced like a str: the same value.
        if _key_is_pathlike(_key) and isinstance(value, str | os.PathLike):
            return hide_identity_in_path(str(value))
        return REDACTED

    if isinstance(value, str):
        return _redact_text(value, personal)

    if isinstance(value, dict):
        return {k: redact(v, str(k), personal=personal) for k, v in value.items()}

    if isinstance(value, list | tuple | set):
        rendered = [redact(item, personal=personal) for item in value]
        return set(rendered) if isinstance(value, set) else type(value)(rendered)

    if isinstance(value, Exception):
        return redact(str(value), personal=personal)

    return value


def redacted_line(raw: str) -> str:
    """One line fit to share: a record field by field (a key naming a secret hides its value), any
    other line as text."""
    try:
        record: Any = json.loads(raw)
    except ValueError:
        record = None
    if not isinstance(record, dict):
        return str(redact(raw, personal=True))
    return json.dumps(redact(record, personal=True), ensure_ascii=False)
