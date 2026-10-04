# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for kernel.config."""

from __future__ import annotations

import re
import stat
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, SecretStr, ValidationError

from sift.kernel import config
from sift.kernel.config import (
    LIBRARIES_MARK,
    MAX_WORKER_CONCURRENCY,
    RETIRED_VARIABLES,
    ConfigError,
    Settings,
    _explain,
    describe_environment,
    ensure_directories,
    get_settings,
    libraries_folder,
    models_folder,
    retired_variables_in_use,
)

pytestmark = pytest.mark.unit


def test_describe_environment_masks_secret_shaped_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret-shaped variable name is masked in the startup description."""
    monkeypatch.setenv("SIFT_DATA_DIR", "/data")
    monkeypatch.setenv("SIFT_API_TOKEN", "supersecretvalue")
    monkeypatch.setenv("SIFT_ADMIN_PASSWORD", "hunter2")

    described = describe_environment()

    assert described["SIFT_DATA_DIR"] == "/data"
    assert described["SIFT_API_TOKEN"] == "[redacted]"
    assert described["SIFT_ADMIN_PASSWORD"] == "[redacted]"
    assert "supersecretvalue" not in str(described)
    assert "hunter2" not in str(described)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "cache_dir": tmp_path / "cache",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- parsing


def test_parses_a_full_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("SIFT_WORKER_CONCURRENCY", "3")
    monkeypatch.setenv("SIFT_LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.data_dir == tmp_path / "d"
    assert settings.worker_concurrency == 3
    assert settings.log_level == "DEBUG"


def test_defaults_need_no_environment(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.worker_concurrency is None  # derived from the hardware
    assert settings.log_level == "INFO"


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("worker_concurrency", 0),
        ("transcode_cache_max_bytes", -1),
        ("port", 70000),
        ("log_level", "LOUD"),
    ],
)
def test_rejects_malformed_values(tmp_path: Path, var: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _settings(tmp_path, **{var: value})


def test_worker_concurrency_has_a_ceiling(tmp_path: Path) -> None:
    """Worker concurrency has a ceiling: an extra zero typed is a startup failure. The ceiling is
    accepted, one past it refused."""
    assert (
        _settings(tmp_path, worker_concurrency=MAX_WORKER_CONCURRENCY).worker_concurrency
        == MAX_WORKER_CONCURRENCY
    )
    with pytest.raises(ValidationError):
        _settings(tmp_path, worker_concurrency=MAX_WORKER_CONCURRENCY + 1)


def test_error_message_names_the_variable_and_says_what_to_do(tmp_path: Path) -> None:
    """The message names the variable and a valid value, for somebody self-hosting."""
    with pytest.raises(ValidationError) as exc:
        _settings(tmp_path, log_level="LOUD")
    message = str(exc.value)
    assert "SIFT_LOG_LEVEL" in message
    assert "INFO" in message


def test_a_typod_variable_fails_the_boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A misspelt SIFT_ variable in the ENVIRONMENT fails the boot: pydantic-settings ignores a
    variable it has no field for, so it would be silently dropped."""
    monkeypatch.setenv("SIFT_CHACE_DIR", str(tmp_path / "typo"))

    with pytest.raises(ValidationError) as exc:
        Settings()

    assert "SIFT_CHACE_DIR" in str(exc.value)
    assert "typo" in str(exc.value).lower()


def test_a_retired_variable_does_not_stop_the_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retired setting still named in the environment does not break the boot: unknown SIFT_ names
    are errors, so it stays recognised and unread."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    for name in RETIRED_VARIABLES:
        monkeypatch.setenv(name, "anything at all")

    Settings()


def test_a_retired_variable_is_reported_so_somebody_can_be_told(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retired name still set is reported, for startup to log: obeyed by nothing, it must say
    so."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    assert retired_variables_in_use() == []

    name = next(iter(RETIRED_VARIABLES))
    monkeypatch.setenv(name, "https://example.invalid")

    assert retired_variables_in_use() == [name]


def test_a_retired_name_is_not_also_a_live_setting() -> None:
    """No retired name is also a live setting."""
    live = {field.alias or f"SIFT_{name.upper()}" for name, field in Settings.model_fields.items()}
    overlap = sorted(live & set(RETIRED_VARIABLES))
    assert not overlap, f"{overlap} is declared as a setting and listed as retired"


def test_unknown_constructor_argument_is_also_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _settings(tmp_path, chace_dir=str(tmp_path / "typo"))


def test_a_shared_dotenv_may_hold_non_sift_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared .env's keys that were never Sift's are ignored, not a boot failure."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SIFT_DATA_DIR=/x\nMEDIA_DIR=/srv/media\nCOMPOSE_PROJECT_NAME=sift\n"
    )
    settings = Settings()
    assert settings.data_dir == Path("/x")


def test_a_typod_sift_key_in_the_dotenv_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misspelt SIFT_ name keeps its prefix, so it is still caught in a shared .env."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SIFT_CHACE_DIR=/typo\n")
    with pytest.raises(ValidationError):
        Settings()


def test_cors_origins_parse_from_a_comma_separated_list(tmp_path: Path) -> None:
    settings = _settings(tmp_path, cors_allowed_origins="https://a.example, https://b.example ,")
    assert settings.cors_origins == ("https://a.example", "https://b.example")


def test_cors_origins_are_empty_by_default(tmp_path: Path) -> None:
    """Empty means no cross-origin access."""
    assert _settings(tmp_path).cors_origins == ()


def test_a_cors_wildcard_is_refused(tmp_path: Path) -> None:
    """A CORS wildcard with the login cookie would let any site act as the user: refused at
    config."""
    with pytest.raises(ValidationError) as exc:
        _settings(tmp_path, cors_allowed_origins="https://a.example,*")
    assert "SIFT_CORS_ALLOWED_ORIGINS" in str(exc.value)


#: Secrets the application mints for itself each launch and never stores, each with why.
MINTED_PER_LAUNCH = {
    "shell_token": (
        "the desktop app makes it fresh at every launch and hands it to the server it spawns, so"
        " a request on the loopback can prove it came from that launch; it dies with the launch"
    ),
}


def test_no_secret_is_defined_anywhere() -> None:
    """No secret is defined with a default; Site logins are sealed under a key from the password. A
    per-launch secret in `MINTED_PER_LAUNCH` is a `SecretStr` with no default."""
    suspicious = re.compile(r"secret|password|key|token|credential", re.IGNORECASE)
    named = [f for f in Settings.model_fields if suspicious.search(f)]
    unexcused = [f for f in named if f not in MINTED_PER_LAUNCH]
    assert unexcused == [], f"config declares a credential-shaped setting: {unexcused}"
    for field in MINTED_PER_LAUNCH:
        declared = Settings.model_fields[field]
        assert declared.annotation == SecretStr | None, f"{field} must never print"
        assert declared.default is None, f"{field} must have no default"


# --- derived paths


def test_derived_paths_follow_their_parents(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.transcode_cache_dir == tmp_path / "cache" / "transcode"
    assert settings.quarantine_dir == tmp_path / "data" / "quarantine"


def test_a_derived_path_can_be_overridden_alone(tmp_path: Path) -> None:
    """A derived path can be relocated alone."""
    fast = tmp_path / "nvme" / "seg"
    settings = _settings(tmp_path, SIFT_TRANSCODE_CACHE_DIR=fast)
    assert settings.transcode_cache_dir == fast
    assert settings.quarantine_dir == tmp_path / "data" / "quarantine"


# --- boot-time directory checks


def test_ensure_directories_creates_them(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    ensure_directories(settings)
    for directory in settings.managed_dirs:
        assert directory.is_dir()


POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "chmod does not make a directory unwritable on Windows: the mode bits are accepted and "
        "then ignored, so the premise of the test simply is not true there. The behaviour these "
        "two prove is covered on every platform by the pair below, which raise the same OSError at "
        "the same seam without asking the filesystem to enforce anything."
    ),
)


@POSIX_ONLY
def test_ensure_directories_fails_loudly_on_a_read_only_cache(tmp_path: Path) -> None:
    """A read-only cache stops the boot rather than silently producing no thumbnails."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(stat.S_IRUSR | stat.S_IXUSR)
    settings = _settings(tmp_path, cache_dir=cache)

    try:
        with pytest.raises(ConfigError, match="cannot write"):
            ensure_directories(settings)
    finally:
        cache.chmod(stat.S_IRWXU)


def test_an_undirectable_write_stops_the_boot_on_any_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory Sift can see and not write to stops the boot with a sentence naming it, however
    the OSError arose."""
    settings = _settings(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("sift.kernel.config.tempfile.NamedTemporaryFile", refuse)

    with pytest.raises(ConfigError, match="cannot write"):
        ensure_directories(settings)


def test_an_uncreatable_directory_stops_the_boot_on_any_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing at `mkdir` gets its own message: the drive or network folder is not connected."""
    settings = _settings(tmp_path)
    real_mkdir = Path.mkdir

    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        if self == settings.cache_dir:
            raise OSError(13, "Permission denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", refuse)

    with pytest.raises(ConfigError, match="cannot create the directory"):
        ensure_directories(settings)


def test_read_only_check_survives_permissive_permission_bits(tmp_path: Path) -> None:
    """Writability is proven by writing: permission bits can say yes on a read-only or full disk."""
    settings = _settings(tmp_path)
    ensure_directories(settings)
    probe = settings.cache_dir / "probe"
    probe.write_text("ok")
    assert probe.read_text() == "ok"


# --- the .env.example contract


def _documented_vars(env_example: Path) -> set[str]:
    """Every SIFT_ variable named in .env.example, commented-out optional ones included."""
    pattern = re.compile(r"^\s*#?\s*(SIFT_[A-Z0-9_]+)\s*=", re.MULTILINE)
    return set(pattern.findall(env_example.read_text()))


def _settings_vars() -> set[str]:
    names = set()
    for name, field in Settings.model_fields.items():
        names.add(field.alias or f"SIFT_{name.upper()}")
    return names


def test_env_example_documents_every_setting(repo_root: Path) -> None:
    """.env.example documents every setting: it is a self-hoster's only documentation."""
    documented = _documented_vars(repo_root / ".env.example")
    declared = _settings_vars()

    undocumented = declared - documented
    assert not undocumented, f"settings missing from .env.example: {sorted(undocumented)}"

    stale = documented - declared
    assert not stale, f".env.example documents settings that no longer exist: {sorted(stale)}"


def test_env_example_parses(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped .env.example loads."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text((repo_root / ".env.example").read_text())

    settings = Settings()

    # Every line is commented out, so the copy changes nothing: each value is its default.
    assert settings.data_dir == Settings.model_fields["data_dir"].default
    assert settings.log_level == "INFO"


def test_env_example_contains_no_real_value(repo_root: Path) -> None:
    """It ships in the repo, so it holds no credential or real host."""
    text = (repo_root / ".env.example").read_text()
    assert not re.search(
        r"^\s*SIFT_.*(SECRET|PASSWORD|TOKEN|KEY)\s*=\s*\S", text, re.MULTILINE | re.IGNORECASE
    )


def test_a_gpu_nobody_recognizes_is_refused_and_says_which_are_not(tmp_path: Path) -> None:
    """An unknown GPU word is refused with all three allowed ones named: it would otherwise mean no
    acceleration with nothing saying why."""
    with pytest.raises(ValidationError) as exc:
        _settings(tmp_path, gpu="nvidia")

    message = str(exc.value)
    assert "SIFT_GPU" in message
    for allowed in ("auto", "cuda", "rocm"):
        assert allowed in message


def test_a_gpu_choice_is_read_whatever_its_capitals(tmp_path: Path) -> None:
    assert _settings(tmp_path, gpu="CUDA").gpu == "cuda"


@POSIX_ONLY
def test_a_directory_that_cannot_be_made_stops_the_boot_and_says_where_to_look(
    tmp_path: Path,
) -> None:
    """A folder on a drive that is not there fails at `mkdir`, with the message to connect it."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    settings = _settings(tmp_path, cache_dir=blocked / "cache")

    try:
        with pytest.raises(ConfigError, match="cannot create the directory"):
            ensure_directories(settings)
    finally:
        blocked.chmod(stat.S_IRWXU)


def test_a_directory_the_machine_refuses_to_make_stops_the_boot_on_any_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same branch where Windows ignores the permission bits: the refusal is provoked
    directly."""
    settings = _settings(tmp_path, cache_dir=tmp_path / "blocked" / "cache")
    real = Path.mkdir

    def refuse(self: Path, **kwargs: object) -> None:
        if "blocked" in self.parts:
            raise PermissionError(13, "Permission denied")
        real(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", refuse)

    with pytest.raises(ConfigError, match="cannot create the directory"):
        ensure_directories(settings)


def test_the_boot_message_keeps_the_validators_sentence_and_drops_the_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configuration error is one sentence, without pydantic's framing."""
    get_settings.cache_clear()
    monkeypatch.setenv("SIFT_LOG_LEVEL", "LOUD")

    try:
        with pytest.raises(ConfigError) as exc:
            get_settings()
    finally:
        get_settings.cache_clear()

    message = str(exc.value)
    assert "SIFT_LOG_LEVEL" in message
    assert "started in" in message
    assert "validation error for Settings" not in message
    assert "input_value" not in message


def test_the_boot_message_names_a_typo_as_a_typo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variable Sift has no field for is not silent."""
    get_settings.cache_clear()
    monkeypatch.setenv("SIFT_CHACE_DIR", str(tmp_path / "typo"))

    try:
        with pytest.raises(ConfigError) as exc:
            get_settings()
    finally:
        get_settings.cache_clear()

    assert "SIFT_CHACE_DIR is not a setting Sift recognizes" in str(exc.value)


def test_a_refusal_that_names_no_variable_is_reported_as_it_was_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-wide check's sentence is carried through with no field name prefixed."""
    get_settings.cache_clear()
    monkeypatch.setenv("SIFT_CORS_ALLOWED_ORIGINS", "*")

    try:
        with pytest.raises(ConfigError) as exc:
            get_settings()
    finally:
        get_settings.cache_clear()

    message = str(exc.value)
    assert "must not be '*'" in message
    # Not re-prefixed: the validator's sentence already leads with the variable's name.
    assert "SIFT_CORS_ALLOWED_ORIGINS: SIFT_CORS_ALLOWED_ORIGINS" not in message


def test_a_value_of_the_wrong_type_is_reported_with_the_variable_in_front_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic writes this one, not Sift, so the sentence arrives with no variable on it.

    "Input should be a valid integer" on its own names nothing, and a file with twenty lines in it
    gives the reader no way to tell which. The field name is turned back into the variable they
    actually typed and put in front.
    """
    get_settings.cache_clear()
    monkeypatch.setenv("SIFT_LOG_MAX_BYTES", "lots")

    try:
        with pytest.raises(ConfigError) as exc:
            get_settings()
    finally:
        get_settings.cache_clear()

    assert "SIFT_LOG_MAX_BYTES: Input should be a valid integer" in str(exc.value)


def test_an_unknown_keyword_is_explained_as_a_typo_rather_than_as_extra_input(
    tmp_path: Path,
) -> None:
    """The other way an unknown name arrives, and the only one that reaches this branch.

    A stray SIFT_ variable in the environment is refused by the model check before pydantic sees
    it. An unknown keyword handed to the constructor is not: it comes back as "Extra inputs are
    not permitted", which says nothing about what to do.
    """
    with pytest.raises(ValidationError) as exc:
        _settings(tmp_path, chace_dir=str(tmp_path / "typo"))

    assert "SIFT_CHACE_DIR is not a setting Sift recognizes" in _explain(exc.value)


def test_a_report_that_already_names_the_variable_is_not_prefixed_twice() -> None:
    """Guarding against the library changing its mind about what it puts in `loc`.

    Pydantic reports the FIELD name today, so the variable is reconstructed from it. Were it ever
    to report the alias instead (which is the variable's real name), prefixing it again would
    print SIFT_SIFT_THING and read as a fault in Sift rather than in the value.

    Driven through a throwaway model rather than through Settings, because Settings cannot produce
    that shape today. What is under test is the helper's tolerance, and the only honest way to show
    tolerance is to hand it the thing it tolerates.
    """

    class Aliased(BaseModel):
        thing: int = Field(alias="SIFT_THING")

    with pytest.raises(ValidationError) as exc:
        Aliased(SIFT_THING="lots")  # type: ignore[arg-type]

    explained = _explain(exc.value)

    assert "SIFT_THING" in explained
    assert "SIFT_SIFT_THING" not in explained


def test_a_platform_that_ships_no_vendored_tools_uses_the_bare_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where nothing is vendored, the machine's own tool is the tool.

    The image apt-installs ffmpeg onto PATH and there is nothing beside the application to prefer,
    so the honest answer is the name. Driven through the flag rather than skipped, because one of
    the two answers is unreachable on whichever machine the suite is running on.
    """
    monkeypatch.setattr(config, "_WINDOWS", False)

    assert config.vendored_tool("ffmpeg") == "ffmpeg"


def test_a_platform_that_ships_them_prefers_the_one_beside_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trap this closes: a machine with an unrelated ffmpeg on PATH from another project, and
    a test run against a different ffmpeg than the one Sift ships."""
    monkeypatch.setattr(config, "_WINDOWS", True)

    chosen = config.vendored_tool("ffmpeg")

    vendored = Path(config.__file__).resolve().parents[3] / "vendor" / "bin" / "ffmpeg.exe"
    assert chosen == (str(vendored) if vendored.exists() else "ffmpeg")


def test_the_installed_layout_is_searched_and_not_counted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The installed layout, where a fixed count of parents goes wrong.

    Installed, this module lives at `runtime/Lib/site-packages/sift/kernel/config.py`: three
    levels deeper than in a checkout. Counting a fixed number of parents would name
    `runtime/Lib/vendor/bin`, which does not exist, so every vendored tool would fall back to a
    bare name and no tunnel could start on any installed copy.
    """
    monkeypatch.setattr(config, "_WINDOWS", True)
    root = tmp_path / "resources"
    deep = root / "runtime" / "Lib" / "site-packages" / "sift" / "kernel" / "config.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("", encoding="utf-8")
    beside = root / "vendor" / "bin"
    beside.mkdir(parents=True)
    (beside / "wireproxy.exe").write_bytes(b"")
    monkeypatch.setattr(config, "__file__", str(deep))

    assert config.vendored_tool("wireproxy") == str(beside / "wireproxy.exe")


def test_a_tool_that_is_nowhere_is_still_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A folder that exists without the tool in it is not an answer: the search goes on.

    Without this the first `vendor/bin` found would end the search, and a partly-populated one
    would shadow the real one further up.
    """
    monkeypatch.setattr(config, "_WINDOWS", True)
    empty = tmp_path / "here" / "vendor" / "bin"
    empty.mkdir(parents=True)
    deep = tmp_path / "here" / "sift" / "kernel" / "config.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "__file__", str(deep))

    assert config.vendored_tool("nothing-ships-this") == "nothing-ships-this"


def test_the_shipped_tools_folder_is_found_where_a_pack_put_it_and_nowhere_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The folder itself, so a tool missing from it reads as removed rather than never shipped:
    the installed layout's folder on Windows, none where nothing was put, and none on a platform
    that ships no tools."""
    root = tmp_path / "resources"
    deep = root / "runtime" / "Lib" / "site-packages" / "sift" / "kernel" / "config.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "__file__", str(deep))
    monkeypatch.setattr(config, "_WINDOWS", True)

    assert config.vendor_folder() is None
    beside = root / "vendor" / "bin"
    beside.mkdir(parents=True)
    assert config.vendor_folder() == beside

    monkeypatch.setattr(config, "_WINDOWS", False)
    assert config.vendor_folder() is None


# --- where the device's store of models is --------------------------------------------------------


def test_the_first_library_and_every_member_share_one_store(tmp_path: Path) -> None:
    """What makes a new or duplicated library need no models of its own."""
    device = tmp_path / "Sift"
    first = device / "data"
    folder = libraries_folder(first)
    folder.mkdir(parents=True)
    (folder / LIBRARIES_MARK).write_bytes(b"{}")
    member = folder / "Second" / "data"

    assert models_folder(first) == device / "models"
    assert models_folder(member) == device / "models"


def test_a_copy_started_on_another_folder_keeps_its_own_store(tmp_path: Path) -> None:
    """A test copy of a library must never download over, or delete, what the real one loaded."""
    assert models_folder(tmp_path / "copy" / "data") == tmp_path / "copy" / "models"
    assert models_folder(tmp_path / "copy" / "data") != models_folder(tmp_path / "real" / "data")


def test_a_data_folder_with_nothing_above_it_keeps_the_store_inside(tmp_path: Path) -> None:
    """A drive root or a container's `/data`: never a folder on somebody's drive root."""
    root = Path(tmp_path.anchor)
    assert models_folder(root / "data") == root / "data" / "models"
