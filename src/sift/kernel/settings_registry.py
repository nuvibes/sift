# SPDX-License-Identifier: AGPL-3.0-or-later
"""The preference registry: what settings exist, and how each one is validated.

Features declare at import; the settings feature stores values and draws the screen from this."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sift.kernel.version import release_of

#: Normalizes a submitted value or raises; returns the value to store.
Validator = Callable[[Any], Any]

#: Whether a value may be chosen on this machine, with the reason, run only when choosing: a
#: validator also runs on reads, and a machine check there would rewrite a stored choice.
Refusal = Callable[[Any], str | None]


class Scope(StrEnum):
    """Who a setting belongs to: each user (USER), or the whole install, admin-only (APP)."""

    USER = "user"
    APP = "app"


# The screen's sections in order; naming any other is a typo caught at registration.
SECTIONS: tuple[str, ...] = (
    "Library",
    "Importing",
    "Editing",
    "Sites and Tunnels",
    "Downloads",
    "Playback",
    "Theater",
    "Identify",
    "Smart Search",
    "Watermarks",
    "Stash-boxes",
    "Music",
    "Performance",
    # Everything on a clock, so "what runs without me, and when" has one screen.
    "Scheduled tasks",
    "Maintenance",
    "Logs",
    "Privacy and Security",
    "Insights",
    "Appearance",
    "Backup",
    "Updates",
    "About",
)


class SettingError(ValueError):
    """A setting was declared or written wrongly. The message is meant to be read."""


class ReadBy(StrEnum):
    """Which side acts on a setting's value; every setting is shown either way."""

    SERVER = "server"
    CLIENT = "client"


# No first-run questions: every question is answered in Settings or stays at its safe default.


@dataclass(frozen=True, slots=True)
class Setting:
    """One declared preference: what the screen draws and what the API validates."""

    key: str
    scope: Scope
    default: Any
    validate: Validator
    #: Run only when somebody chooses a value; see `Refusal`.
    refuse: Refusal | None
    section: str
    label: str
    help: str
    #: Reference material the screen puts behind a "more" control, kept apart from the help.
    disclosure: str | None = None
    #: For a fixed set of options, the options, so the screen can draw a menu.
    choices: tuple[Any, ...] | None = None
    #: What each option is called on screen, beside the values so the two never drift.
    choice_labels: tuple[str, ...] | None = None
    #: Inclusive bounds, so the screen can constrain input.
    minimum: int | None = None
    maximum: int | None = None
    #: The unit, shown in the control; `%` draws a slider.
    unit: str | None = None
    #: What zero is called where a bare 0 would be misread; a gate holds every count to one.
    automatic_label: str | None = None
    #: Which side acts on it, declared so "nothing reads it" is a checkable claim.
    read_by: ReadBy = None  # type: ignore[assignment]
    #: Whether a change alters what a scoped read returns, so it rings `library`, not `settings`.
    changes_visibility: bool = False
    #: The release whose update last changed `default`, so its first start says so on History.
    default_since: str | None = None
    names_a_tunnel: bool = False

    def metadata(self) -> dict[str, Any]:
        """The parts the settings screen renders from; the validator never crosses the wire."""
        meta: dict[str, Any] = {
            "key": self.key,
            "scope": self.scope.value,
            "default": self.default,
            "section": self.section,
            "label": self.label,
            "help": self.help,
        }
        if self.disclosure is not None:
            meta["disclosure"] = self.disclosure
        if self.choices is not None:
            meta["choices"] = list(self.choices)
        if self.choice_labels is not None:
            meta["choice_labels"] = list(self.choice_labels)
        if self.minimum is not None:
            meta["minimum"] = self.minimum
        if self.maximum is not None:
            meta["maximum"] = self.maximum
        if self.unit is not None:
            meta["unit"] = self.unit
        if self.automatic_label is not None:
            meta["automatic_label"] = self.automatic_label
        return meta


# Validators built from what a registration declares; each raises SettingError with what was
# wanted. `bool` is checked before `int`, as a bool is an int in Python.


def _boolean_validator() -> Validator:
    def validate(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        raise SettingError("expected true or false")

    return validate


def _choice_validator(choices: Sequence[Any]) -> Validator:
    allowed = tuple(choices)

    def validate(value: Any) -> Any:
        # Type-strict, as `True == 1` would let a boolean satisfy a numeric choice.
        if any(type(value) is type(choice) and value == choice for choice in allowed):
            return value
        raise SettingError(f"must be one of: {', '.join(str(choice) for choice in allowed)}")

    return validate


def _integer_validator(minimum: int | None, maximum: int | None) -> Validator:
    def validate(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingError("expected a whole number")
        if minimum is not None and value < minimum:
            raise SettingError(f"must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise SettingError(f"must be at most {maximum}")
        return int(value)

    return validate


def _text_validator() -> Validator:
    def validate(value: Any) -> str:
        if isinstance(value, str):
            return value
        raise SettingError("expected text")

    return validate


def _build_validator(
    default: Any,
    choices: Sequence[Any] | None,
    minimum: int | None,
    maximum: int | None,
) -> Validator:
    """Pick a validator: choices win, else the default's type names what the setting holds."""
    if choices is not None:
        return _choice_validator(choices)
    if isinstance(default, bool):
        return _boolean_validator()
    if isinstance(default, int):
        return _integer_validator(minimum, maximum)
    if isinstance(default, str):
        return _text_validator()
    raise SettingError(
        f"cannot infer how to validate a setting whose default is {type(default).__name__}; "
        "pass choices, a numeric default, a string default, or an explicit validator"
    )


_REGISTRY: dict[str, Setting] = {}


def _check_choices(
    key: str, choices: Sequence[Any] | None, choice_labels: Sequence[str] | None
) -> None:
    """Each choice needs a name, refused here rather than shown as the stored word (`nvidia`)."""
    if choices is not None:
        if choice_labels is None:
            raise SettingError(f"setting {key!r} has choices and needs a name for each of them")
        if len(tuple(choice_labels)) != len(tuple(choices)):
            raise SettingError(
                f"setting {key!r} has {len(tuple(choices))} choices and "
                f"{len(tuple(choice_labels))} names for them"
            )
        if any(not one.strip() for one in choice_labels):
            raise SettingError(f"setting {key!r} has a choice with a blank name")
    elif choice_labels is not None:
        raise SettingError(f"setting {key!r} names choices it does not have")


def register_setting(
    *,
    key: str,
    scope: str,
    default: Any,
    section: str,
    label: str,
    help: str,
    disclosure: str | None = None,
    choices: Sequence[Any] | None = None,
    choice_labels: Sequence[str] | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    unit: str | None = None,
    automatic_label: str | None = None,
    validator: Validator | None = None,
    refuse: Refusal | None = None,
    read_by: ReadBy = ReadBy.SERVER,
    changes_visibility: bool = False,
    default_since: str | None = None,
    names_a_tunnel: bool = False,
) -> None:
    """Declare a preference, once, at import, by the feature that owns it."""
    if key in _REGISTRY:
        raise SettingError(f"setting {key!r} is registered twice")
    if key in _RETIRED:
        raise SettingError(
            f"setting {key!r} was retired into {', '.join(_RETIRED[key].into)} and cannot be "
            "declared again"
        )
    if key in _REMOVED:
        raise SettingError(f"setting {key!r} was removed and cannot be declared again")

    try:
        resolved_scope = Scope(scope)
    except ValueError as exc:
        raise SettingError(
            f"setting {key!r} has scope {scope!r}; expected 'user' or 'app'"
        ) from exc

    if section not in SECTIONS:
        raise SettingError(
            f"setting {key!r} names section {section!r}, which is not one of: {', '.join(SECTIONS)}"
        )
    if not label.strip():
        raise SettingError(f"setting {key!r} needs a label")
    if not help.strip():
        raise SettingError(f"setting {key!r} needs help text")

    _check_choices(key, choices, choice_labels)
    validate = validator or _build_validator(default, choices, minimum, maximum)

    # A default its own validator rejects would surface only on a reset, so it is caught now.
    try:
        validate(default)
    except SettingError as exc:
        raise SettingError(f"setting {key!r} has a default its validator rejects: {exc}") from exc

    _REGISTRY[key] = Setting(
        key=key,
        scope=resolved_scope,
        default=default,
        validate=validate,
        refuse=refuse,
        section=section,
        label=label,
        help=help,
        disclosure=disclosure,
        choices=tuple(choices) if choices is not None else None,
        choice_labels=tuple(choice_labels) if choice_labels is not None else None,
        minimum=minimum,
        maximum=maximum,
        unit=unit,
        automatic_label=automatic_label,
        read_by=read_by,
        changes_visibility=changes_visibility,
        default_since=_a_release(key, default_since),
        names_a_tunnel=names_a_tunnel,
    )


def _a_release(key: str, since: str | None) -> str | None:
    if since is not None and release_of(since) is None:
        raise SettingError(f"setting {key!r} says its default changed in {since!r}, not a release")
    return since


def registered_settings() -> dict[str, Setting]:
    """The registry, copied. Callers read it; nothing mutates it through here."""
    return dict(_REGISTRY)


def get_registered(key: str) -> Setting | None:
    """The declaration for one key, or None if nothing has registered it."""
    return _REGISTRY.get(key)


# A retired key is read through and written into the settings that replaced it, so there is
# one stored value; it is drawn nowhere.


@dataclass(frozen=True, slots=True)
class Retired:
    key: str
    #: The settings that hold the answer now, in the order `read` and `write` take them.
    into: tuple[str, ...]
    #: Their values turned into the answer the old key would have given.
    read: Callable[[tuple[Any, ...]], Any]
    #: A value written to the old key turned into their new values.
    write: Callable[[Any, tuple[Any, ...]], tuple[Any, ...]]
    #: For a reader of the source and the log; never shown.
    why: str
    #: The key's name on a folder's own settings, where it decides less than its successor.
    folder_label: str | None = None
    folder_help: str | None = None


_RETIRED: dict[str, Retired] = {}


def retire_setting(
    key: str,
    *,
    into: Sequence[str],
    read: Callable[[tuple[Any, ...]], Any],
    write: Callable[[Any, tuple[Any, ...]], tuple[Any, ...]],
    why: str,
    folder_label: str | None = None,
    folder_help: str | None = None,
) -> None:
    """Say that `key` is answered by `into` now; refused if still registered or retired twice."""
    targets = tuple(into)
    if key in _REGISTRY:
        raise SettingError(f"setting {key!r} is still registered and cannot be retired")
    if key in _RETIRED:
        raise SettingError(f"setting {key!r} is retired twice")
    if not targets:
        raise SettingError(f"setting {key!r} is retired into nothing")
    if not why.strip():
        raise SettingError(f"setting {key!r} is retired without saying why")
    _RETIRED[key] = Retired(
        key=key,
        into=targets,
        read=read,
        write=write,
        why=why,
        folder_label=folder_label,
        folder_help=folder_help,
    )


def get_retired(key: str) -> Retired | None:
    """The retirement of one key, or None if it is not retired."""
    return _RETIRED.get(key)


def retired_settings() -> dict[str, Retired]:
    """Every retired key, copied."""
    return dict(_RETIRED)


# A removed key has no successor; it keeps its last label so History can still name it.


@dataclass(frozen=True, slots=True)
class Removed:
    """One key taken out with no successor: what it was last called, and why it went."""

    key: str
    label: str
    why: str


_REMOVED: dict[str, Removed] = {}


def remove_setting(key: str, *, label: str, why: str) -> None:
    """Say that `key` was taken out with nothing in its place, once, at import."""
    if key in _REGISTRY or key in _RETIRED:
        raise SettingError(f"setting {key!r} is still declared and cannot be removed")
    if key in _REMOVED:
        raise SettingError(f"setting {key!r} is removed twice")
    if not label.strip() or not why.strip():
        raise SettingError(f"setting {key!r} is removed without its label or without saying why")
    _REMOVED[key] = Removed(key=key, label=label, why=why)


def get_removed(key: str) -> Removed | None:
    """The removal of one key, or None if it was not removed."""
    return _REMOVED.get(key)


def label_of(key: str) -> str | None:
    """What a key is called on screen: its label, or for a retired key what it became."""
    setting = _REGISTRY.get(key)
    if setting is not None:
        return setting.label
    retired = _RETIRED.get(key)
    if retired is None:
        return None
    labels = [_REGISTRY[one].label for one in retired.into if one in _REGISTRY]
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def folder_words_of(key: str) -> tuple[str | None, str | None]:
    """What a key is called on a folder's own settings, and the help under it."""
    retired = _RETIRED.get(key)
    if retired is not None and retired.folder_label is not None:
        return retired.folder_label, retired.folder_help
    return label_of(key), None
