# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed configuration, read from the environment, by the only module that reads it.

Errors here are read by somebody self-hosting: say what is wrong, where, and what to do."""

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
    """Retired names this machine still sets, returned for startup to report."""
    return sorted(name for name in RETIRED_VARIABLES if name in os.environ)


class ConfigError(RuntimeError):
    """Configuration is unusable. Raised at boot, never mid-request."""


class _SharedDotEnvSource(PydanticBaseSettingsSource):
    """The dotenv source, ignoring keys not SIFT_ settings, as other tools share the file."""

    def __init__(self, wrapped: PydanticBaseSettingsSource) -> None:
        super().__init__(wrapped.settings_cls)
        self._wrapped = wrapped

    def get_field_value(self, field: Any, field_name: str) -> Any:
        # Never called, as __call__ is overridden; present for the abstract base.
        return self._wrapped.get_field_value(field, field_name)  # pragma: no cover

    def __call__(self) -> dict[str, Any]:
        known = set(self.settings_cls.model_fields)
        return {
            key: value
            for key, value in self._wrapped().items()
            if key in known or key.lower().startswith("sift_")
        }


#: A backstop against a typo spawning thousands of workers at boot, above any real need.
MAX_WORKER_CONCURRENCY = 64


#: Variables older versions read, and what replaced each: recognised so a machine setting one
#: still boots, never read, and named once at startup.
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


# Windows defaults differ: the POSIX `/data` is a valid Windows path, and a run inheriting it
# would silently build a library there and listen on the LAN.

_WINDOWS = sys.platform == "win32"


def _windows_app_dir() -> Path:
    r"""`%LOCALAPPDATA%\Sift`, never roaming: a media database must not be synchronised."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Sift"


#: Bounded, so a stray `vendor/bin` near a drive's root is never taken for the application's.
_VENDOR_SEARCH_DEPTH = 6


def _vendor_directories() -> list[Path]:
    r"""Everywhere the shipped tools might be, nearest first, walking up from this file."""
    here = Path(__file__).resolve()
    return [parent / "vendor" / "bin" for parent in here.parents[:_VENDOR_SEARCH_DEPTH]]


def vendored_tool(name: str) -> str:
    """The tool the application ships when it is there; otherwise the machine's own by name."""
    if not _WINDOWS:
        return name
    for root in _vendor_directories():
        candidate = root / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return name


def vendor_folder() -> Path | None:
    """The folder a Windows pack put its tools in, or None, so a missing tool reads as removed."""
    if not _WINDOWS:
        return None
    for root in _vendor_directories():
        if root.is_dir():
            return root
    return None


# One store per device for what every library shares (models, the GPU runtime), defined here
# as the kernel's model store needs it.

#: The folder new libraries are made in, beside the first library's data folder.
LIBRARIES_FOLDER = "libraries"

#: The mark naming a libraries folder; written by the backup slice only.
LIBRARIES_MARK = "sift-libraries.json"

#: The device's one store of models and the graphics-card runtime, beside the libraries.
MODELS_FOLDER = "models"


def libraries_folder(data_dir: Path) -> Path:
    """The folder libraries are made in, found alike from the first library or any member."""
    above = data_dir.parent.parent
    if (above / LIBRARIES_MARK).is_file():
        return above
    return data_dir.parent / LIBRARIES_FOLDER


def models_folder(data_dir: Path) -> Path:
    r"""The device's one model store beside the libraries, derived from the data folder.

    Inside the data folder when nothing Sift owns sits above it, as at a drive's root."""
    device = libraries_folder(data_dir).parent
    if device.parent == device:
        return data_dir / MODELS_FOLDER
    return device / MODELS_FOLDER


class Settings(BaseSettings):
    """Every setting Sift has, prefixed SIFT_; no secret has a default or lives here."""

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
        # Only the dotenv source is tolerant; a mistyped SIFT_ variable elsewhere still fails.
        return (
            init_settings,
            env_settings,
            _SharedDotEnvSource(dotenv_settings),
            file_secret_settings,
        )

    data_dir: Path = _windows_app_dir() / "data" if _WINDOWS else Path("/data")
    """The database. The only directory that must be backed up."""

    cache_dir: Path = _windows_app_dir() / "cache" if _WINDOWS else Path("/cache")
    """Derived files: thumbnails, previews, transcoded segments. Rebuildable, so it is
    deliberately disposable and is never mixed in with the originals."""

    transcode_cache_max_bytes: int = Field(default=5 * 1024**3, gt=0)
    """Ceiling for the transcode cache before the oldest segments are evicted."""

    # Optional overrides; unset, each derives from data_dir or cache_dir.
    transcode_cache_dir_override: Path | None = Field(
        default=None, alias="SIFT_TRANSCODE_CACHE_DIR"
    )
    quarantine_dir_override: Path | None = Field(default=None, alias="SIFT_QUARANTINE_DIR")

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

    ffmpeg_path: str = vendored_tool("ffmpeg")
    ffprobe_path: str = vendored_tool("ffprobe")

    # Animated WebP's two readers, as ffmpeg cannot read one.
    webpinfo_path: str = vendored_tool("webpinfo")
    anim_dump_path: str = vendored_tool("anim_dump")

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
        """The device's model store, derived rather than set, so library copies agree on it."""
        return models_folder(self.data_dir)

    @property
    def cors_origins(self) -> tuple[str, ...]:
        """The allowed origins as a list, parsed from the comma-separated setting."""
        return tuple(o.strip() for o in self.cors_allowed_origins.split(",") if o.strip())

    @property
    def managed_dirs(self) -> tuple[Path, ...]:
        """Directories Sift owns and may create; library roots are the user's and only read."""
        return (
            self.data_dir,
            self.cache_dir,
            self.transcode_cache_dir,
            self.quarantine_dir,
            self.models_dir,
        )

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_variables(cls, data: Any) -> Any:
        """A SIFT_ variable that is not a setting is a typo, so it fails the boot."""
        known = {field.alias or f"SIFT_{name.upper()}" for name, field in cls.model_fields.items()}
        # A retired name is recognised but never read (see `RETIRED_VARIABLES`).
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
    """Create the directories Sift owns and prove each writable by writing, at boot."""
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
    """A validator's report as the sentences it wrote, without pydantic's own vocabulary."""
    lines = []
    for err in error.errors():
        message = err["msg"].removeprefix("Value error, ")

        if not err["loc"]:
            # A model-level check, which already names its variables.
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
    """The SIFT_ variables set, for a startup line, any secret-shaped name masked."""
    return {
        k: (_ENV_REDACTED if _is_secret_env_name(k) else v)
        for k, v in sorted(os.environ.items())
        if k.startswith("SIFT_")
    }
