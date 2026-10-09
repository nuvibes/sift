# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed configuration, read from the environment.

This is the only module that reads the environment. Everything else takes a `Settings` and
reads a field off it, so there is exactly one place to look for what a knob is called, what it
defaults to, and what happens if it is wrong.

Errors here are read by someone who is self-hosting an app, not debugging a program. A
traceback is not an error message: say what is wrong, where, and what to do about it.
"""

from __future__ import annotations

import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def retired_variables_in_use() -> list[str]:
    """Retired names this machine still carries, so startup can say they do nothing.

    Read here rather than anywhere else for the reason at the top of this module: this is the only
    place that reads the environment. Returned rather than logged, because logging is not up yet
    when configuration is built.
    """
    return sorted(name for name in RETIRED_VARIABLES if name in os.environ)


class ConfigError(RuntimeError):
    """Configuration is unusable. Raised at boot, never mid-request."""


class _SharedDotEnvSource(PydanticBaseSettingsSource):
    """The .env file source, made tolerant of keys that are not Sift's own.

    A .env beside a checkout is often shared with other tools, which keep keys there that are not
    settings. Reading the same file, Sift ignores anything that is not one of its SIFT_ settings
    rather than refusing to start over a line another tool put there. A mistyped SIFT_ name is still
    an error: those stay SIFT_-prefixed here and are kept for the strict check, and a SIFT_ name set
    as a real environment variable is always checked.
    """

    def __init__(self, wrapped: PydanticBaseSettingsSource) -> None:
        super().__init__(wrapped.settings_cls)
        self._wrapped = wrapped

    def get_field_value(self, field: Any, field_name: str) -> Any:
        # Never called: __call__ is overridden. Present only to satisfy the abstract base.
        return self._wrapped.get_field_value(field, field_name)  # pragma: no cover

    def __call__(self) -> dict[str, Any]:
        known = set(self.settings_cls.model_fields)
        return {
            key: value
            for key, value in self._wrapped().items()
            if key in known or key.lower().startswith("sift_")
        }


#: The most workers `SIFT_WORKER_CONCURRENCY` may ask for. A backstop, not a knob: no household box
#: has a reason to run more than this many jobs together, and without a ceiling a typo (an extra
#: zero) would try to spawn thousands of worker tasks at boot. Set above the default so nobody who
#: had a real reason to raise it hits the ceiling by accident.
MAX_WORKER_CONCURRENCY = 64


#: Variables older versions read, and what happened to each.
#:
#: A NAME, ONCE USED, IS NEVER FREE AGAIN. Two things go wrong without this list and they pull in
#: opposite directions. Delete the field and the strict check below sees an unrecognised SIFT_
#: variable and refuses to boot, so removing something that did nothing breaks a machine that had
#: it. Keep the field and the variable goes on being accepted, validated and ignored, which is the
#: state that produced this list in the first place.
#:
#: Named here instead: recognised, not read, and said out loud once at startup so whoever wrote
#: that line finds out it is doing nothing. The value is a sentence rather than a marker, because
#: the next reader's question is what to use instead.
#:
#: SIFT_PUBLIC_BASE_URL was declared, documented and validated for as long as it existed and was
#: read by no code in the application: nothing in Sift ever built an absolute link out of it. Every
#: address the client and the playlists use is relative, which is what makes a proxy in front work
#: without being told anything at all.
RETIRED_VARIABLES: dict[str, str] = {
    "SIFT_PUBLIC_BASE_URL": (
        "it never did anything: every address Sift builds is relative, so a proxy in front needs "
        "no telling. Nothing replaces it"
    ),
    "SIFT_SESSION_TTL_SECONDS": (
        "how long a sign-in lasts is a setting now, on the Privacy screen, in days. A number that "
        "can only be reached by editing a file is not a choice on an application somebody installs"
    ),
}


# --- Defaults that differ by platform ------------------------------------------------------------
#
# Sift ships as a native Windows application, where nothing is mounted and Sift must pick
# sensibly. The POSIX values (`/data`, `/media`) are what a checkout on Linux and CI run with.
#
# THIS IS NOT COSMETIC. `/data` is a perfectly valid Windows path: it resolves to `C:\data` on
# the current drive, a folder that already exists on some machines. A Windows run inheriting the
# POSIX defaults would therefore not fail; it would quietly build a second library in a
# directory nobody chose, and `0.0.0.0` would put it on the LAN by default. Both are silent.

_WINDOWS = sys.platform == "win32"


def _windows_app_dir() -> Path:
    r"""`%LOCALAPPDATA%\Sift`: the local profile, deliberately not the roaming one.

    A roaming profile is copied between machines by domain policy and by some backup tools, and a
    media database with its transcode cache is precisely what should never be silently
    synchronised: it is large, it is machine-specific, and half of it is derived.
    """
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Sift"


#: How far up from this file a `vendor/bin` is worth looking for.
#:
#: Bounded, so that a stray directory of that name near the root of a drive can never be mistaken
#: for the application's own. Six is two more than the deepest real layout needs.
_VENDOR_SEARCH_DEPTH = 6


def _vendor_directories() -> list[Path]:
    r"""Everywhere the shipped tools might be, nearest first.

    A walk UP from this file rather than a count of parents. `parents[3]` is the repository root
    from `src/sift/kernel/config.py`, but in the installed application the same file sits three
    levels deeper, inside `runtime/Lib/site-packages/sift/kernel/`, where the same expression names
    `runtime/Lib/vendor/bin`, a folder that does not exist, and every vendored tool would silently
    fall back to a bare name.

    The shell passes explicit paths for ffmpeg, ffprobe, webpinfo and anim_dump, so the tool that
    depends on this is the tunnel client: it is started deep inside the downloader, which has no
    Settings to read, and without the walk no tunnel could start on an installed copy.
    """
    here = Path(__file__).resolve()
    return [parent / "vendor" / "bin" for parent in here.parents[:_VENDOR_SEARCH_DEPTH]]


def vendored_tool(name: str) -> str:
    """The tool the application ships, when it is there; otherwise whatever the machine has.

    PUBLIC, because the tunnel client is found through it too and that one is not a Settings field:
    a tunnel is started deep inside the downloader, which has no Settings to read. A bare name is
    right on Linux, where a package puts the client on PATH, and wrong for a Windows install, where
    nothing puts it anywhere.

    Falling back to the bare name is deliberate and is not a failure: on Linux, and in any checkout
    that has not fetched the vendored tools, the machine's own copy is the right one.
    """
    if not _WINDOWS:
        return name
    for root in _vendor_directories():
        candidate = root / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return name


def vendor_folder() -> Path | None:
    """The folder a Windows pack put its shipped tools in, or None where there is none: Linux,
    and a checkout that has not fetched them.

    Asked of the FOLDER rather than of one tool, so a tool missing from it reads as removed from
    where it was put, which is a different fault from never having been shipped at all.
    """
    if not _WINDOWS:
        return None
    for root in _vendor_directories():
        if root.is_dir():
            return root
    return None


# --- The device's own folder, and the libraries in it --------------------------------------------
#
# A DEVICE RUNS MANY LIBRARIES AND KEEPS ONE COPY OF WHAT IS THE SAME FOR ALL OF THEM. The face,
# Smart Search and watermark models and the graphics-card runtime are files about this device and
# this build of Sift, not about anybody's library: the same model gives the same answer whichever
# library asks. Kept inside each library's data folder they would be hundreds of megabytes a
# feature per library for ever, and a new or duplicated library would start without any of them.
#
# Both rules below are defined here, once, because the kernel's model store needs the second and
# a slice may not be imported by the kernel. The backup slice, which makes libraries, reads them
# from here rather than keeping a copy.

#: The folder new libraries are made in, beside the first library's data folder.
LIBRARIES_FOLDER = "libraries"

#: The mark on that folder (and its list of libraries kept elsewhere). A library whose grandparent
#: holds it is one of the folder's members. Written by the backup slice only.
LIBRARIES_MARK = "sift-libraries.json"

#: The device's one store of models and the graphics-card runtime, beside the libraries.
MODELS_FOLDER = "models"


def libraries_folder(data_dir: Path) -> Path:
    """The folder libraries are made in, found the same way from the first library or any member.

    A data folder at `<folder>/<name>/data` is a member when `<folder>` carries the mark. Anything
    else is a library kept somewhere of its own, and the folder is `libraries` beside its data
    folder, which, for the first library Sift made, is the one place this has always meant.

    Found again from INSIDE, because a member's data folder is `<folder>/<name>/data` and "beside
    the data folder" would be a different place for every library.
    """
    above = data_dir.parent.parent
    if (above / LIBRARIES_MARK).is_file():
        return above
    return data_dir.parent / LIBRARIES_FOLDER


def models_folder(data_dir: Path) -> Path:
    r"""The device's one store of models and the graphics-card runtime: beside the libraries.

    `%LOCALAPPDATA%\Sift\models` on an ordinary install, found from whichever library is running:
    the first one (`...\Sift\data`) and every member of the libraries folder
    (`...\Sift\libraries\<name>\data`) arrive at the same folder, which is what makes "New library"
    and "Duplicate this library" need no models of their own.

    DERIVED FROM THE DATA FOLDER, NOT FROM `%LOCALAPPDATA%`. A copy of Sift started on a data
    folder somewhere else (a test copy of a library, or one somebody moved) keeps its models
    beside that folder, so it can never download over, or delete, the files another running copy
    has loaded.

    INSIDE THE DATA FOLDER WHEN THERE IS NOTHING SIFT OWNS ABOVE IT: a data folder at the root of a
    drive (`D:\data`) or a container's mount (`/data`). A store at `D:\models` or `/models` would be
    a folder on somebody's drive root, or one no volume keeps.
    """
    device = libraries_folder(data_dir).parent
    if device.parent == device:
        return data_dir / MODELS_FOLDER
    return device / MODELS_FOLDER


class Settings(BaseSettings):
    """Every setting Sift has. Prefixed SIFT_ in the environment.

    No secret has a default and none is invented. Site logins are encrypted with a key derived
    from the user's password, so there is no key in the environment at all: nothing for a user
    to generate, save, or lose.
    """

    model_config = SettingsConfigDict(
        env_prefix="SIFT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    release_feed_url: str | None = None
    """Where the release check reads from. The desktop app sets it when it starts the backend;
    unset, the check makes no request."""

    hold_optional_features: bool = False
    """Face recognition, Smart Search and watermark reading read as off for this start only."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Only the .env file source is wrapped. The environment and constructor stay strict, so a
        # mistyped SIFT_ variable there still fails the boot; the shared-file tolerance is scoped to
        # the one place a non-Sift key legitimately appears.
        return (
            init_settings,
            env_settings,
            _SharedDotEnvSource(dotenv_settings),
            file_secret_settings,
        )

    # --- Storage ---------------------------------------------------------------------

    data_dir: Path = _windows_app_dir() / "data" if _WINDOWS else Path("/data")
    """The database. The only directory that must be backed up."""

    cache_dir: Path = _windows_app_dir() / "cache" if _WINDOWS else Path("/cache")
    """Derived files: thumbnails, previews, transcoded segments. Rebuildable, so it is
    deliberately disposable and is never mixed in with the originals."""

    transcode_cache_max_bytes: int = Field(default=5 * 1024**3, gt=0)
    """Ceiling for the transcode cache before the oldest segments are evicted."""

    # Optional overrides. Unset, each derives from data_dir/cache_dir. See the properties
    # below. A user should be able to relocate one directory without restating the others.
    transcode_cache_dir_override: Path | None = Field(
        default=None, alias="SIFT_TRANSCODE_CACHE_DIR"
    )
    quarantine_dir_override: Path | None = Field(default=None, alias="SIFT_QUARANTINE_DIR")

    # --- Behaviour -------------------------------------------------------------------

    cookie_secure: bool | None = None
    """Mark the session cookie Secure, so a browser only ever sends it back over HTTPS.

    Left unset (the default), Sift decides per request: it marks the cookie Secure when the request
    reached it over HTTPS, and does not when it arrived over plain HTTP. That fits both ways Sift is
    normally run without any configuration: on the LAN over http://<host>:5171, where a Secure
    cookie would never come back and login would appear to do nothing, and behind a tunnel or proxy
    that terminates TLS, where the cookie should be Secure because a session cookie is a login
    someone could replay. Set it to true or false only to force one or the other.
    """

    enable_docs: bool = False
    """Serve the interactive API documentation at /docs, /redoc and /openapi.json.

    Off by default. Those pages enumerate every endpoint to anyone who can reach them, and Sift
    is meant to be internet-exposed. Turn it on while developing or debugging an install; leave
    it off in normal use.
    """

    stop_on_stdin_eof: bool = False
    """Shut down cleanly when whoever started Sift lets go of its stdin.

    OFF BY DEFAULT, AND IT HAS TO BE. Stdin is at EOF from the first instant in any container
    started without `-i`, so a Sift that did this unconditionally would exit immediately in the
    published image, on every start, looking exactly like a crash.

    The desktop shell turns it on because it has no other way to ask. Node turns every signal it
    accepts into TerminateProcess on Windows, which stops the process where it stands, so the
    database would never be closed and the write-ahead log would be left unfolded on every quit. A
    pipe is stronger than a signal here as well as more portable: only the process that spawned
    this one holds the other end, so there is no port to reach and no token to leak. See
    `stop_when_the_parent_lets_go` in sift/main.py.
    """

    attention_fake_input_ms: int | None = Field(default=None, ge=0)
    """A stand-in for how long ago the last key press or mouse movement was, in milliseconds.

    For measuring the step-back lever only (`kernel.attention`). Unset (the default), the reading
    is Windows' own. Set, every reading is this number instead, on any platform: 0 is somebody at
    the keyboard for as long as the process runs, 600000 is nobody. It exists because a synthetic
    mouse movement is refused in a remote desktop session whose window is minimized, so a
    measurement cannot always make the real reading move. Sift logs a warning on the first reading
    it replaces, so it cannot be left on unnoticed.
    """

    attention_fake_input_file: Path | None = None
    """A file holding the stand-in above, read afresh on every reading, so it can move while Sift runs.

    For measuring the step-back lever both ways on one running server (`kernel.attention`): write 0
    into it and the pool steps back within a few seconds, write 600000 and it comes back to its full
    count. A file that is missing or holds no whole number reads as nothing to read, which is the
    full count. `attention_fake_input_ms`, when set too, wins. Logged once, like that one.
    """

    shell_url: str | None = None
    """Where the desktop app that started this backend answers the acts only it can do: an address
    on 127.0.0.1 it chose for this launch. An admin on another computer reaches the starting with
    Windows and the firewall rule through it. Unset (a backend run by hand, or in a container),
    the screens say there is no app to ask.
    """

    app_log_dir: Path | None = None
    """The desktop app's own log folder, said by the app that started this backend."""

    shell_token: SecretStr | None = None
    """This launch's secret for asking the desktop app. Made fresh by the app at every launch and
    handed down in the environment, which only the process that spawned this one can set."""

    worker_concurrency: int | None = Field(default=None, gt=0, le=MAX_WORKER_CONCURRENCY)
    """Jobs to run together. Unset means derive it from the hardware actually present.

    Bounded above by MAX_WORKER_CONCURRENCY so a mistyped value cannot try to spawn a runaway number
    of workers at boot; a value over the ceiling is a startup ConfigError, not a degraded run.
    """

    gpu: Literal["auto", "cuda", "rocm"] = "auto"
    """Which graphics acceleration to expect.

    "auto" uses whatever the machine has and is perfectly happy with nothing: the CPU does
    the work, more slowly. "cuda" or "rocm" say a GPU should be here: if none is found, Sift
    says so in plain words at startup and carries on using the CPU, rather than running slow
    for no visible reason. Set it to match the image you started (the plain image needs
    nothing here).
    """

    log_level: str = "INFO"

    log_unredacted: bool = False
    """Show file paths and usernames in the lines written while Sift starts.

    Only those lines: once the library is open, the Logs page's own switch (Hide personal details
    in the log, off unless somebody turns it on) decides, and it is read within seconds of the
    open. The start keeps names hidden so a person who chose hiding is never written whole by
    the lines before the setting could be read; credentials stay hidden either way.
    """

    log_max_bytes: int = 10 * 1024 * 1024
    log_backups: int = 5
    """How much log to keep on disk, and in how many files.

    Sift writes its log to a file under the data directory as well as to the container's output,
    because the two answer different questions. The container's output is only ever the CURRENT
    container: rebuilding the image throws it away, and rebuilding the image is exactly what
    somebody does between noticing a problem and asking about it. The file outlives that.

    The defaults keep about fifty megabytes: enough that yesterday's question can still be
    answered, small enough that nobody has to think about it. Set the size to 0 to write no file
    at all.
    """

    # --- External tools --------------------------------------------------------------

    ffmpeg_path: str = vendored_tool("ffmpeg")
    ffprobe_path: str = vendored_tool("ffprobe")

    # For animated WebP, which ffmpeg can write and has never been able to read. `webpinfo`
    # answers the canvas size and the frame count; `anim_dump` writes the frames out.
    webpinfo_path: str = vendored_tool("webpinfo")
    anim_dump_path: str = vendored_tool("anim_dump")

    # --- Networking ------------------------------------------------------------------

    host: str = "0.0.0.0" if not _WINDOWS else "127.0.0.1"  # noqa: S104 (see the docstring)
    """Which addresses to listen on, and the two answers are different for a good reason.

    In the container, every address: the container's own network is the boundary, the operator
    decides what to publish, and a reverse proxy in front is the normal arrangement.

    On the desktop, this machine only. A desktop application that binds every interface publishes
    somebody's entire media library to their coffee-shop wifi the first time they open it, having
    never been asked. Reaching it from another machine is a real feature (it is what client mode
    is), but it is a decision the person makes, not a default they inherit.
    """

    port: int = Field(default=5171, gt=0, lt=65536)

    cors_allowed_origins: str = ""
    """Browser origins allowed to call the API from a different origin, comma-separated.

    Empty by default, which allows none: the browser's same-origin policy is left in force and no
    cross-origin request is answered. Sift's own frontend is served from the same origin and needs
    nothing here. Set it only when a separate frontend on another origin has to reach the API, e.g.
    a dev server: "https://sift.example.com,http://localhost:5173". Never "*": a wildcard that
    also carries the login cookie would let any site on the internet act as the logged-in user, so
    it is refused outright rather than silently downgraded.
    """

    # --- Derived paths ---------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def transcode_cache_dir(self) -> Path:
        return self.transcode_cache_dir_override or self.cache_dir / "transcode"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def quarantine_dir(self) -> Path:
        return self.quarantine_dir_override or self.data_dir / "quarantine"

    @property
    def models_dir(self) -> Path:
        """The device's store of models and the graphics-card runtime. See `models_folder`.

        A property rather than a field or a variable: it has no value of its own to be told, only
        a place that follows from where the library is, and a second knob for it would be a way
        for two copies of one library to disagree about where their models are.
        """
        return models_folder(self.data_dir)

    @property
    def cors_origins(self) -> tuple[str, ...]:
        """The allowed origins as a list, parsed from the comma-separated setting."""
        return tuple(o.strip() for o in self.cors_allowed_origins.split(",") if o.strip())

    @property
    def managed_dirs(self) -> tuple[Path, ...]:
        """Directories Sift owns and may create. Library roots are not here: those belong to
        the user and Sift only ever reads them."""
        return (
            self.data_dir,
            self.cache_dir,
            self.transcode_cache_dir,
            self.quarantine_dir,
            self.models_dir,
        )

    # --- Validation ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_variables(cls, data: Any) -> Any:
        """A SIFT_ variable that is not a setting is a typo, and must be an error.

        Left alone, pydantic-settings simply does not read a variable it has no field for. So
        `SIFT_CHACE_DIR=/mnt/big` starts cleanly, uses the default, and gives the user a Sift
        that ignores the one line they most carefully edited, with nothing anywhere to say so.
        Failing at boot costs a restart; failing silently costs an afternoon.
        """
        known = {field.alias or f"SIFT_{name.upper()}" for name, field in cls.model_fields.items()}
        # A retired name is recognised so that removing a setting cannot stop a machine that still
        # names it from booting. It is recognised and not read (see `RETIRED_VARIABLES`).
        unknown = sorted(
            k
            for k in os.environ
            if k.startswith("SIFT_") and k not in known and k not in RETIRED_VARIABLES
        )
        if unknown:
            raise ValueError(
                f"{', '.join(unknown)} is not a setting Sift recognizes. Check it for a typo. "
                f"The settings are: {', '.join(sorted(known))}."
                if len(unknown) == 1
                else f"These are not settings Sift recognizes. Check them for typos: "
                f"{', '.join(unknown)}. The settings are: {', '.join(sorted(known))}."
            )
        return data

    @field_validator("log_level", mode="before")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        level = str(value).upper()
        if level not in allowed:
            raise ValueError(
                f"SIFT_LOG_LEVEL is '{value}', which is not a log level. "
                f"Use one of: {', '.join(sorted(allowed))}."
            )
        return level

    @field_validator("gpu", mode="before")
    @classmethod
    def _check_gpu(cls, value: str) -> str:
        allowed = {"auto", "cuda", "rocm"}
        choice = str(value).lower()
        if choice not in allowed:
            raise ValueError(
                f"SIFT_GPU is '{value}', which Sift does not recognize. "
                f"Use one of: {', '.join(sorted(allowed))}."
            )
        return choice

    @field_validator("cors_allowed_origins")
    @classmethod
    def _reject_cors_wildcard(cls, value: str) -> str:
        origins = [o.strip() for o in value.split(",") if o.strip()]
        if "*" in origins:
            raise ValueError(
                "SIFT_CORS_ALLOWED_ORIGINS must not be '*'. A wildcard that also carries the login "
                "cookie would let any website act as the logged-in user. List the exact origins "
                "that need cross-origin access, or leave it empty to allow none."
            )
        return value


def ensure_directories(settings: Settings) -> None:
    """Create the directories Sift owns, and prove each one is writable.

    Checked at boot rather than on first use, so a read-only cache volume is a startup failure
    with a clear cause instead of a thumbnail that silently never appears.

    Writability is tested by actually writing, not by inspecting permission bits: the bits can
    say yes while the filesystem is read-only, the disk is full, or a network share's own
    permissions disagree.
    """
    for directory in settings.managed_dirs:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                f"Sift cannot create the directory {directory} ({exc.strerror}).\n"
                "Check that the drive or network folder it's on is connected, and that you're "
                "allowed to write to it."
            ) from exc

        try:
            with tempfile.NamedTemporaryFile(dir=directory):
                pass
        except OSError as exc:
            raise ConfigError(
                f"Sift can see the directory {directory} but cannot write to it "
                f"({exc.strerror}).\n"
                "It is most likely mounted read-only, out of space, or owned by a different "
                "user than the one Sift runs as."
            ) from exc


def _explain(error: ValidationError) -> str:
    """Turn a validator's report into something a self-hoster can act on.

    Pydantic's default rendering leads with its own vocabulary: "1 validation error for
    Settings", "[type=value_error, input_value=...]", which is noise to someone whose actual
    problem is a mistyped line in a file. Keep the sentence the validator wrote, drop the rest.
    """
    lines = []
    for err in error.errors():
        message = err["msg"].removeprefix("Value error, ")

        if not err["loc"]:
            # A model-level check. It already knows which variables it is talking about.
            lines.append(f"  - {message}")
            continue

        location = err["loc"][0]
        variable = str(location)
        if not variable.startswith("SIFT_"):
            variable = f"SIFT_{variable}".upper()

        if err["type"] == "extra_forbidden":
            message = f"{variable} is not a setting Sift recognizes. Check it for a typo."
        elif not message.startswith("SIFT_"):
            message = f"{variable}: {message}"

        lines.append(f"  - {message}")

    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The settings, loaded once: a second parse could disagree with the first."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigError(
            "Sift could not start because a setting is wrong.\n\n"
            f"{_explain(exc)}\n\n"
            "These come from your .env file, or from the environment Sift was started in."
        ) from exc


_ENV_REDACTED = "[redacted]"
_SECRET_ENV_MARKERS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "PWD",
    "CREDENTIAL",
    "PASSPHRASE",
)


def _is_secret_env_name(name: str) -> bool:
    """Whether an environment variable name looks like it carries a secret."""
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


def describe_environment() -> dict[str, str]:
    """The SIFT_ variables actually set, for a startup log line.

    No secret lives in Sift's environment by design: secrets are wrapped in the database, not
    passed in as environment. A secret-shaped name is masked anyway, so the day a secret-carrying
    knob is added it does not land in the log by default rather than only after someone remembers
    to.
    """
    return {
        k: (_ENV_REDACTED if _is_secret_env_name(k) else v)
        for k, v in sorted(os.environ.items())
        if k.startswith("SIFT_")
    }
