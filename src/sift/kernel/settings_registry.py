# SPDX-License-Identifier: AGPL-3.0-or-later
"""The preference registry: what settings exist, and how each one is validated.

Sift has a preference for almost every behavioural choice: how near-duplicates are handled on
import, whether a delete goes to a bin or is final, which mode the vault conceals in, how a clip
loops. If each feature stored its own preference its own way there would be a dozen little settings
systems and no single screen that could show them all.

So there is one registry, and it lives here rather than in the feature that renders the settings
screen. A feature declares a preference at import time with `register_setting`, and the screen is
generated from what has been declared. This is the same shape as the schema registry in `db`: the
registration point is the kernel's, so any feature can reach it without importing another feature,
while the values themselves and the screen that edits them belong to the settings feature.

Nothing here touches the database. This module only records what a setting *is*: its default, how
to validate a new value, and the human-facing label and help the screen shows. Reading and writing
the stored value is the settings feature's job.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sift.kernel.version import release_of

#: A validator normalizes a submitted value or raises. It returns the value to store, which lets it
#: coerce as well as check, though the ones built below only check.
Validator = Callable[[Any], Any]

#: A refusal answers "may this be chosen HERE, on this machine, in this installation" with the
#: reason it may not, or None.
#:
#: SEPARATE FROM THE VALIDATOR, AND THE SEPARATION IS THE WHOLE POINT. A validator answers "is this
#: a value this setting can hold", which is a fact about the setting and is true wherever it is
#: asked, so it is run on the way out of the database as well as on the way in, and a stored value
#: it rejects is quietly replaced by the default. A refusal answers something about the MACHINE, and
#: running that on a read would rewrite somebody's stored choice into one they never made.
#:
#: An availability check used as the validator of a device setting would turn a stored "nvidia"
#: into "cpu" on the way back, so recognition, which is supposed to REFUSE to run on a device that
#: is not there rather than quietly run slowly on the processor, would silently run slowly on the
#: processor.
Refusal = Callable[[Any], str | None]


class Scope(StrEnum):
    """Who a setting belongs to.

    A USER setting is per-user: every user has their own, and changing it moves nobody else's.
    An APP setting is global to the instance and only an admin may change it: a capability the
    whole install shares, not a personal preference.
    """

    USER = "user"
    APP = "app"


# The settings screen's sections, in the order they appear. A setting names one of these, and a
# setting that names anything else is a typo caught at registration rather than a blank row on a
# screen. The sections exist before the settings that fill them: the screen shows them all, and the
# ones nothing has registered into yet are simply empty.
SECTIONS: tuple[str, ...] = (
    "Library",
    # What is done to a file on the way in, in the three groups a person thinks in: reading it,
    # drawing it, and working out what is in it. It sits beside Folders because that pair is the
    # whole of "where the files are and what happens to them": one door for one question.
    "Importing",
    # What the editor produces AND what compression produces. Two headings, one section: two
    # questions a person asks in the same breath, not two rail slots of one and four numbers.
    "Editing",
    "Downloads",
    "Sites and Tunnels",
    "Playback",
    "Theater",
    "Identify",
    "Smart Search",
    "Watermarks",
    # The stash-boxes and the rules for what each may fill in: a section of their own rather than
    # beside network sharing, Site logins and tunnels. A door should answer one question.
    "Stash-boxes",
    "Music",
    "Performance",
    # Everything Sift does on a clock, in one place, rather than under the feature that owns the
    # work (the backup schedule under Backup, the quarantine timer under Maintenance), so "what
    # runs without me, and when" has one screen that answers it. What is declared here is drawn
    # here and nowhere else; the panes the work belongs to point at it.
    "Scheduled tasks",
    # Housekeeping an admin does to the library rather than to a preference: what the duplicate
    # scan shows, what has been quarantined, what can be tidied away. Like Connections above, the
    # pane holds hand-built controls beside its declared settings.
    "Maintenance",
    # What Sift writes down about what it did, how much of it, and reading the end of the file.
    # Not inside Maintenance, which is a pane about duplicates and orphaned files: a log is looked
    # for BY NAME on the day something has gone wrong.
    "Logs",
    "Privacy and Security",
    "Appearance",
    "Backup",
    "Updates",
    "About",
)


class SettingError(ValueError):
    """A setting was declared or written wrongly. The message is meant to be read."""


class ReadBy(StrEnum):
    """Which side of the application acts on a setting's value.

    Not where it is shown: every setting is shown. Where the behaviour it controls lives.
    """

    SERVER = "server"
    CLIENT = "client"


# NO FIRST-RUN QUESTIONS. A first run is a user and an installation method, and nothing else: a run
# of screens between somebody and the library they installed Sift to look at is too many, however
# well each one argues for itself.
#
# **A question is answered in Settings, or it stays at the default it ships with.** That is safe
# rather than a loss: the settings
# store holds a row only where somebody wrote one, every setting is drawn on a screen somebody can
# search for, and a consent gate that nobody has answered is off. Nothing is decided on somebody's
# behalf by not asking.


@dataclass(frozen=True, slots=True)
class Setting:
    """One declared preference.

    Everything the settings screen needs to render a control and everything the API needs to
    validate a new value. The label and help are plain language on purpose: a screen full of
    `snake_case` keys is unusable by the person a self-hosted app is for.
    """

    key: str
    scope: Scope
    default: Any
    validate: Validator
    #: Why this value may not be chosen on this machine, run ONLY when somebody is choosing it.
    #: See `Refusal` for why this is not the validator.
    refuse: Refusal | None
    section: str
    label: str
    help: str
    #: Reference material (licence terms, download sizes, measured figures) read once and then
    #: in the way forever. The screen puts this behind a "more" affordance instead of between the
    #: reader and the next control, which is only possible because it arrives separated from the
    #: help rather than joined onto the end of it.
    disclosure: str | None = None
    #: For a fixed set of options, the options, so the screen can draw a menu instead of a text
    #: box, and so a caller can see them without running a value through the validator to find out.
    choices: tuple[Any, ...] | None = None
    #: What each of those options is called on screen, in the same order. Declared here beside the
    #: values rather than kept by the screen, because a stored value is a word like `nvidia` and the
    #: reader is shown "Graphics card": a mapping the screen would otherwise have to hold as a
    #: second list, which drifts the first time a choice is added and nobody edits both.
    choice_labels: tuple[str, ...] | None = None
    #: Inclusive bounds for a numeric setting, for the same reason: the screen can constrain input.
    minimum: int | None = None
    maximum: int | None = None
    #: The unit the number is in, shown inside the control rather than tacked onto the label. Also
    #: what tells the screen a value is a proportion: a percentage is the one thing drawn as a
    #: slider, so the shape of the control follows from what the setting is rather than from a
    #: judgement made once per setting.
    unit: str | None = None
    #: What ZERO is called where a bare 0 would be misread: a count where 0 means Sift decides (how
    #: many jobs run at once, how many downloads, previews, folder scans, recognition threads), or a
    #: limit where 0 means none (a size floor, a lock timer, a keep-for). A box reading `0` says the
    #: opposite of what those mean, and nobody types a zero into a count on purpose, so the number on
    #: screen was only ever readable to whoever wrote it. `tests/gates/test_a_number_names_its_zero.py`
    #: holds every whole-number setting that accepts 0 to a word, or to its list of real amounts.
    #:
    #: Declared here rather than listed in the screen for the reason `choice_labels` is: a list of
    #: five keys held on the client is a list that goes stale the first time a sixth is added and
    #: nobody edits both. The screen shows this word in place of the zero, and clearing the box
    #: means it.
    automatic_label: str | None = None
    #: Which side actually ACTS on this, as opposed to drawing a control for it.
    #:
    #: Declared rather than worked out, because no checker can tell the two apart. A screen names
    #: every setting it renders, so "the key appears in the client" is true of every setting there
    #: is, so a setting can be declared, drawn, stored, and read by no code anywhere. Saying which
    #: side is supposed to read it is what makes "and nothing does" a checkable claim.
    read_by: ReadBy = None  # type: ignore[assignment]
    #: Whether changing this changes WHAT THE USER MAY SEE, as opposed to how something looks.
    #:
    #: Almost nothing is. A setting moving rings `settings`, and the handful of screens that read
    #: a preference listen for it: the player, the lock triggers, the wall, Downloads. The GRID
    #: does not, and must not: it would re-read every list in the application every time anybody
    #: nudged a slider, which is most of the cost of polling for nothing.
    #:
    #: But a few settings decide what a scoped read RETURNS, and for those `settings` is the wrong
    #: bell. `vault.concealment` is the one that exists today: switching it from "Show a locked
    #: tile" to "Show nothing" is exactly the change `library` already means (something hidden or
    #: revealed), which the grid listens for.
    #:
    #: Declared here rather than as a list of keys held by the hub, for the reason `read_by` is
    #: declared: a list somewhere else is a list that goes stale the first time a second setting
    #: needs it and nobody edits both. The server reads the mode on every request; what this adds
    #: is TELLING the browser, or placeholders would sit on the screen until somebody navigated.
    changes_visibility: bool = False
    #: The release whose update last changed `default` (`"0.1.218"`), or None. Give it whenever a
    #: default changes: the first start of that release says so on History (`settings_hub.defaults`).
    default_since: str | None = None

    def metadata(self) -> dict[str, Any]:
        """The parts the settings screen renders from. The validator is not among them: it is a
        function, it does not cross the wire, and the screen never needs it."""
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


# --- validators -------------------------------------------------------------------------------
#
# Built from what a registration declares (a default, maybe choices, maybe a range) so the common
# cases need no hand-written validator. Each raises SettingError on a value it will not accept, and
# the message says what was wanted: it reaches the user through a 422.
#
# A value arrives as parsed JSON, so the Python types below are the JSON types: a JSON boolean is a
# bool, a JSON number an int, a JSON string a str. `bool` is checked before `int` on purpose:
# `bool` is a subclass of `int` in Python, so an unchecked int validator would accept True.


def _boolean_validator() -> Validator:
    def validate(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        raise SettingError("expected true or false")

    return validate


def _choice_validator(choices: Sequence[Any]) -> Validator:
    allowed = tuple(choices)

    def validate(value: Any) -> Any:
        # Type-strict, not a plain `in`: Python makes `True == 1`, so a bare membership test would
        # let a boolean satisfy a numeric choice and the reverse. A choice matches only a value of
        # its own type, the same care `_integer_validator` takes when it refuses a bool as an int.
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
    """Pick a validator from what the registration declared.

    Choices win: a fixed set of options is a fixed set whatever the values look like. Otherwise the
    type of the default decides, because the default is itself a valid value and so names the type
    the setting holds.
    """
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


# --- the registry -----------------------------------------------------------------------------


_REGISTRY: dict[str, Setting] = {}


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
) -> None:
    """Declare a preference. Called once, at import time, by the feature that owns it.

    `scope` is `"user"` (per-user) or `"app"` (global, admin-only). Give `choices` for a menu, a
    `minimum`/`maximum` for a bounded number, or a `validator` for anything else; with none of them
    the type of `default` decides. `label` and `help` are shown on the settings screen and must be
    plain language: they are required, and empty ones are refused, because the screen is generated
    from them and a blank one is a defect the person running Sift would have to read the source to
    understand.

    `choices` requires `choice_labels`, one for each, and that is why the screen never holds a
    second list saying what `nvidia` is called. `unit` is the suffix a number is shown with, and a
    unit of `%` is also what makes the screen draw a slider instead of a box, so the control
    follows from the declaration rather than from a decision taken again per setting.

    Registering the same key twice raises. Two features each believing they own `playback.loop_mode`
    is exactly the drift a single registry exists to prevent, so it fails at import rather than
    letting the second silently win.

    `refuse` is for a value that is perfectly valid and cannot be honoured HERE: a graphics card
    on a machine that has none. It runs only when somebody is choosing the value, never when one is
    read back, because a check about the machine has no business rewriting what is stored. See
    `Refusal`.

    `read_by` says which side acts on the value: the server by default, or `CLIENT` for the few
    that are genuinely the browser's alone: how big the tiles are, whether motion is reduced.
    It is checked, so a server setting nothing on the server reads is a failure rather than a
    control that quietly does nothing.

    There is no `setup` argument: the first run asks no questions (see the note above `Setting`),
    and a setting is answered on the settings screen or stays at its default.
    """
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

    # A menu whose options have no names shows the stored words: `nvidia`, `fully_gone`. Refused
    # here rather than left to the screen, so the failure is at the declaration that caused it.
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

    validate = validator or _build_validator(default, choices, minimum, maximum)

    # A default that its own validator rejects is a contradiction that would surface only when a
    # user reset the value, so it is caught here at declaration instead.
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


# --- retired settings ----------------------------------------------------------------------------
#
# A setting whose question is now answered by another one. Not deleted outright, and the reason is
# everything that still names it: a folder's own answer stored under the old key, a caller written
# before the change, a link somebody saved to the row. A retired key is READ through the setting
# that replaced it and WRITTEN into it, so there is one stored value and the old spelling is only a
# way of asking about it, never a second copy that could disagree.
#
# It is not drawn anywhere: the screens draw the setting it was retired into. The settings route
# lists each retired key beside its successor so an old address can be sent to the new row.


@dataclass(frozen=True, slots=True)
class Retired:
    """One key that is now a reading of other settings, and how to translate each way."""

    key: str
    #: The settings that hold the answer now, in the order `read` and `write` take them.
    into: tuple[str, ...]
    #: Their current values, in that order, turned into the answer the old key would have given.
    read: Callable[[tuple[Any, ...]], Any]
    #: A value written to the old key, and their current values, turned into their new values.
    write: Callable[[Any, tuple[Any, ...]], tuple[Any, ...]]
    #: Why it was retired, for a reader of the source and the log. Not shown on any screen.
    why: str
    #: What the key is called on a folder's own settings, where a folder's answer is still stored
    #: under it and decides less than the setting it became: a task's When says when the task runs,
    #: and a folder's answer only whether a file ARRIVING there is worked on. None where the
    #: successors' labels already say what a folder's answer does.
    folder_label: str | None = None
    #: What that answer does, said under it on the folder's page. None for nothing to add.
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
    """Say that `key` is answered by `into` now. Called at import, where the successor is declared.

    `folder_label` and `folder_help` are what a folder's own answer under the old key is called
    and what it does (`Retired.folder_label`), declared here so the words live beside the key.

    Refused for a key that is still registered (retiring it means removing its declaration, in the
    same change) and for one retired twice. The successors need not be registered yet (import
    order decides that) and are checked when a value is read.
    """
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


# REMOVED SETTINGS: a key taken out with nothing to read it through. A retired key (above) is
# answered by its successors; a removed one is answered by nothing: its control went because it
# changed nothing. But the record still names it: every change somebody made to it is a ledger row
# whose snapshot holds the label of the day it was written, which can be a word this application no
# longer says ("Offer to measure this machine"). So a removed
# key declares what it was last called, and History names it that way and says it is gone.
#
# Only its LABEL and why: its values meant something to a control that no longer exists, and a
# line reading one back ("left it at done") would be the machine talking.


@dataclass(frozen=True, slots=True)
class Removed:
    """One key taken out with no successor: what it was last called, and why it went."""

    key: str
    #: Its label on the day it went, in today's words.
    label: str
    #: For a reader of the source. Not shown on any screen.
    why: str


_REMOVED: dict[str, Removed] = {}


def remove_setting(key: str, *, label: str, why: str) -> None:
    """Say that `key` was taken out with nothing in its place. Called at import, in the module that
    declared it. Refused for a key still registered or retired, and for one removed twice."""
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
    """What a key is called on screen: its own label, or (for a retired key) what it became.

    A retired key has no row and no label of its own, and a screen can still have to name one: a
    folder's own answer is stored under the old key, because that is the key the gates read. It is
    called by the settings it was retired into, in their order, listed the way a sentence lists
    things, so the Identify group's old master reads as the three tasks it now switches, which is
    exactly what answering it for a folder does. None for a key nothing declared, and for a retired
    key none of whose successors is declared yet.
    """
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
    """What a key is called on a folder's own settings, and the help under it.

    A retired key's folder words where it declared some (`retire_setting`), because there the
    folder's answer decides less than the setting the key became; otherwise `label_of` and no help.
    """
    retired = _RETIRED.get(key)
    if retired is not None and retired.folder_label is not None:
        return retired.folder_label, retired.folder_help
    return label_of(key), None
