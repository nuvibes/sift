# SPDX-License-Identifier: AGPL-3.0-or-later
"""The preference registry: what it accepts at declaration, and what it refuses.

Every check here fails at import time in real use (a feature declares its settings when its module
loads), so a mistake is a broken boot, loud and immediate, not a wrong value discovered later.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.settings_registry import (
    SECTIONS,
    Scope,
    SettingError,
    folder_words_of,
    get_registered,
    get_removed,
    get_retired,
    label_of,
    register_setting,
    registered_settings,
    remove_setting,
    retire_setting,
    retired_settings,
)

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("clean_settings_registry")]


def _register(**overrides: object) -> None:
    kwargs: dict[str, object] = {
        "key": "test.example",
        "scope": "user",
        "default": "a",
        "choices": ["a", "b"],
        "choice_labels": ["A", "B"],
        "section": "Playback",
        "label": "Example",
        "help": "An example setting.",
    }
    kwargs.update(overrides)
    register_setting(**kwargs)  # type: ignore[arg-type]


def test_a_registered_setting_is_readable_back() -> None:
    _register(
        key="playback.loop_mode",
        default="loop_one",
        choices=["loop_one", "once"],
        choice_labels=["Repeat", "Stop"],
    )
    setting = get_registered("playback.loop_mode")
    assert setting is not None
    assert setting.scope is Scope.USER
    assert setting.default == "loop_one"
    assert setting.choices == ("loop_one", "once")
    assert "playback.loop_mode" in registered_settings()


def test_registering_the_same_key_twice_fails_loudly() -> None:
    """Two features each claiming one key is the drift a single registry exists to prevent."""
    _register(key="library.trash_mode")
    with pytest.raises(SettingError, match="registered twice"):
        _register(key="library.trash_mode")


def test_a_label_is_required() -> None:
    with pytest.raises(SettingError, match="needs a label"):
        _register(label="   ")


def test_help_text_is_required() -> None:
    with pytest.raises(SettingError, match="needs help text"):
        _register(help="")


def test_an_unknown_section_is_refused() -> None:
    with pytest.raises(SettingError, match="not one of"):
        _register(section="Nonsense")


def test_every_section_name_is_accepted() -> None:
    for index, section in enumerate(SECTIONS):
        _register(key=f"test.section{index}", section=section)


def test_an_invalid_scope_is_refused() -> None:
    with pytest.raises(SettingError, match="expected 'user' or 'app'"):
        _register(scope="everyone")


# --- validators -------------------------------------------------------------------------------


def test_a_choice_validator_accepts_a_listed_value_and_rejects_others() -> None:
    _register(key="test.choice", default="a", choices=["a", "b"], choice_labels=["A", "B"])
    setting = get_registered("test.choice")
    assert setting is not None
    assert setting.validate("b") == "b"
    with pytest.raises(SettingError, match="must be one of"):
        setting.validate("c")


def test_a_boolean_setting_accepts_only_booleans() -> None:
    _register(key="test.flag", default=False, choices=None, choice_labels=None)
    setting = get_registered("test.flag")
    assert setting is not None
    assert setting.validate(True) is True
    with pytest.raises(SettingError, match="true or false"):
        setting.validate("true")


def test_a_boolean_is_not_read_as_an_integer() -> None:
    """bool is a subclass of int in Python: an integer validator would accept True. The default here
    is a bool, so the boolean validator is chosen, and a number is refused."""
    _register(key="test.flag2", default=True, choices=None, choice_labels=None)
    setting = get_registered("test.flag2")
    assert setting is not None
    with pytest.raises(SettingError, match="true or false"):
        setting.validate(1)


def test_an_integer_range_is_enforced() -> None:
    _register(key="test.count", default=5, choices=None, choice_labels=None, minimum=1, maximum=10)
    setting = get_registered("test.count")
    assert setting is not None
    assert setting.validate(1) == 1
    assert setting.validate(10) == 10
    with pytest.raises(SettingError, match="at least 1"):
        setting.validate(0)
    with pytest.raises(SettingError, match="at most 10"):
        setting.validate(11)


def test_an_integer_setting_rejects_a_boolean() -> None:
    _register(key="test.count2", default=5, choices=None, choice_labels=None, minimum=0, maximum=10)
    setting = get_registered("test.count2")
    assert setting is not None
    with pytest.raises(SettingError, match="whole number"):
        setting.validate(True)


def test_a_string_setting_rejects_a_non_string() -> None:
    _register(key="test.text", default="", choices=None, choice_labels=None)
    setting = get_registered("test.text")
    assert setting is not None
    assert setting.validate("hello") == "hello"
    with pytest.raises(SettingError, match="expected text"):
        setting.validate(3)


def test_choices_with_no_names_are_refused() -> None:
    """A menu whose options have no names shows the stored words: `nvidia`, `fully_gone`.

    Refused at the declaration rather than left to the screen, so the failure is at the line that
    caused it and the app does not start. That is deliberate: the alternative is a pane that draws
    perfectly and puts an internal word in front of somebody, which nothing downstream can tell
    apart from a name that was chosen.
    """
    with pytest.raises(SettingError, match="needs a name for each"):
        _register(key="test.unnamed", default="a", choices=["a", "b"], choice_labels=None)


def test_a_choice_whose_name_is_blank_is_refused() -> None:
    """The same rule one step in. A list of names with a gap in it is a menu with a blank row."""
    with pytest.raises(SettingError, match="blank name"):
        _register(key="test.blank", default="a", choices=["a", "b"], choice_labels=["A", "  "])


def test_a_name_for_every_choice_means_the_same_number_of_them() -> None:
    with pytest.raises(SettingError, match="2 choices and 1 names"):
        _register(key="test.short", default="a", choices=["a", "b"], choice_labels=["A"])


def test_names_for_choices_that_do_not_exist_are_refused() -> None:
    """The other way round, and it is the shape a setting takes on its way to becoming a menu:
    somebody writes the names first and never adds the values."""
    with pytest.raises(SettingError, match="names choices it does not have"):
        _register(key="test.nochoices", default="a", choices=None, choice_labels=["A", "B"])


def test_a_default_that_fails_its_own_validator_is_refused() -> None:
    """A default outside its own choices would surface only when a user reset the value, so it is
    caught at declaration instead."""
    with pytest.raises(SettingError, match="default its validator rejects"):
        _register(key="test.bad", default="z", choices=["a", "b"], choice_labels=["A", "B"])


def test_a_default_of_an_uninferrable_type_needs_an_explicit_validator() -> None:
    with pytest.raises(SettingError, match="cannot infer"):
        _register(key="test.weird", default=[1, 2, 3], choices=None, choice_labels=None)


def test_an_explicit_validator_overrides_inference() -> None:
    def only_short(value: object) -> object:
        if isinstance(value, str) and len(value) <= 3:
            return value
        raise SettingError("too long")

    _register(
        key="test.custom", default="ok", choices=None, choice_labels=None, validator=only_short
    )
    setting = get_registered("test.custom")
    assert setting is not None
    assert setting.validate("abc") == "abc"
    with pytest.raises(SettingError, match="too long"):
        setting.validate("abcd")


def test_metadata_carries_what_a_screen_renders_and_not_the_validator() -> None:
    _register(key="test.meta", default="a", choices=["a", "b"], choice_labels=["A", "B"])
    setting = get_registered("test.meta")
    assert setting is not None
    meta = setting.metadata()
    assert meta["key"] == "test.meta"
    assert meta["choices"] == ["a", "b"]
    assert meta["label"] == "Example"
    assert "validate" not in meta


def test_metadata_carries_every_optional_part_a_screen_can_draw() -> None:
    """Each optional field is its own branch, and a missing one must be ABSENT rather than null.

    The screen decides what to draw from whether a key is there: a number with a `minimum` gets a
    floor on its box, one without gets a plain field. A null carried across the wire is a key that
    is there, so the box would grow a floor of nothing and refuse everything typed into it.
    """
    _register(
        key="test.every-part",
        default=3,
        choices=None,
        choice_labels=None,
        disclosure="advanced",
        minimum=1,
        maximum=9,
        unit="seconds",
        automatic_label="Automatic",
    )
    setting = get_registered("test.every-part")
    assert setting is not None

    meta = setting.metadata()

    assert meta["disclosure"] == "advanced"
    assert meta["minimum"] == 1
    assert meta["maximum"] == 9
    assert meta["unit"] == "seconds"
    # The word a screen draws in place of the zero that means "work it out". It is declared here
    # rather than held as a list of keys on the client for the same reason `choice_labels` is.
    assert meta["automatic_label"] == "Automatic"
    assert "choices" not in meta
    assert "choice_labels" not in meta


def test_metadata_leaves_out_every_part_that_was_not_declared() -> None:
    """The other side of the same rule, and the one a screen reads as "draw a plain box"."""
    _register(key="test.plain", default="a", choices=None, choice_labels=None)
    setting = get_registered("test.plain")
    assert setting is not None

    meta = setting.metadata()

    for absent in (
        "disclosure",
        "choices",
        "choice_labels",
        "minimum",
        "maximum",
        "unit",
        "automatic_label",
    ):
        assert absent not in meta, absent
    # And what every setting carries, whatever else it does not.
    assert meta["key"] == "test.plain"
    assert meta["section"] == "Playback"


# --- retiring a key into others, and removing one outright ----------------------------------------


def _same(values: tuple[Any, ...]) -> Any:
    return values[0]


def _spread(value: Any, values: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(value for _ in values)


def _retire(key: str = "test.old", **overrides: Any) -> None:
    kwargs: dict[str, Any] = {
        "into": ["test.new"],
        "read": _same,
        "write": _spread,
        "why": "answered by the new key now",
    }
    kwargs.update(overrides)
    retire_setting(key, **kwargs)


def test_a_retired_key_is_answered_by_its_successors_and_says_why() -> None:
    _retire(into=["test.new", "test.newer"])

    retired = get_retired("test.old")
    assert retired is not None
    assert retired.into == ("test.new", "test.newer")
    assert retired.read(("a", "b")) == "a"
    assert retired.write("c", ("a", "b")) == ("c", "c")
    assert set(retired_settings()) == {"test.old"}
    assert get_retired("test.never") is None


def test_a_key_still_declared_cannot_be_retired() -> None:
    """Retiring a key means removing its declaration in the same change; both at once would give
    one key two answers."""
    _register(key="test.old")
    with pytest.raises(SettingError, match="still registered"):
        _retire()


@pytest.mark.parametrize(
    ("overrides", "refusal"),
    [
        ({"into": []}, "retired into nothing"),
        ({"why": "  "}, "without saying why"),
    ],
)
def test_a_retirement_names_what_answers_it_and_why(
    overrides: dict[str, Any], refusal: str
) -> None:
    with pytest.raises(SettingError, match=refusal):
        _retire(**overrides)
    assert get_retired("test.old") is None


def test_a_key_is_retired_once() -> None:
    _retire()
    with pytest.raises(SettingError, match="retired twice"):
        _retire()


def test_a_retired_key_cannot_be_declared_again() -> None:
    """Its stored values are read through the successors; a second declaration would answer the
    same key a second way."""
    _retire(into=["test.new", "test.newer"])
    with pytest.raises(SettingError, match=r"retired into test\.new, test\.newer"):
        _register(key="test.old")


def test_a_removed_key_keeps_its_last_label_and_cannot_come_back() -> None:
    """Every change somebody made to it is a record that still has to be named."""
    remove_setting("test.gone", label="Offer to measure this machine", why="it changed nothing")

    removed = get_removed("test.gone")
    assert removed is not None and removed.label == "Offer to measure this machine"
    assert get_removed("test.never") is None
    with pytest.raises(SettingError, match="removed and cannot be declared again"):
        _register(key="test.gone")
    with pytest.raises(SettingError, match="removed twice"):
        remove_setting("test.gone", label="Again", why="again")


def test_a_key_still_declared_or_retired_cannot_be_removed() -> None:
    _register(key="test.live")
    _retire()
    for key in ("test.live", "test.old"):
        with pytest.raises(SettingError, match="still declared"):
            remove_setting(key, label="Gone", why="gone")


@pytest.mark.parametrize(("label", "why"), [("", "gone"), ("Gone", " ")])
def test_a_removal_says_what_it_was_called_and_why(label: str, why: str) -> None:
    with pytest.raises(SettingError, match="without its label or without saying why"):
        remove_setting("test.gone", label=label, why=why)
    assert get_removed("test.gone") is None


def test_a_key_is_named_by_its_own_label_or_by_what_it_was_retired_into() -> None:
    """A folder's answer can be stored under an old key, and a screen still has to name it: by
    the settings it became, listed the way a sentence lists things, and by nothing when none of
    them is declared yet."""
    for key, label in (("test.a", "Faces"), ("test.b", "Tags"), ("test.c", "Scenes")):
        _register(key=key, label=label)
    _retire("test.one", into=["test.a", "test.missing"])
    _retire("test.three", into=["test.a", "test.b", "test.c"])
    _retire("test.nothing", into=["test.missing"])

    assert label_of("test.a") == "Faces"
    assert label_of("test.one") == "Faces"
    assert label_of("test.three") == "Faces, Tags and Scenes"
    assert label_of("test.nothing") is None
    assert label_of("test.never") is None


def test_a_retired_key_is_named_on_a_folders_page_by_its_folder_words() -> None:
    """Where a retirement declares what a folder's answer does, a folder's page says that and its
    help; anywhere it declared nothing, the key's label and no help."""
    _register(key="test.task", label="Generate")
    _retire(
        "test.arrive",
        into=["test.task"],
        folder_label="Generate as files arrive",
        folder_help="Only on the way in.",
    )
    _retire("test.plain", into=["test.task"])

    assert folder_words_of("test.arrive") == ("Generate as files arrive", "Only on the way in.")
    assert folder_words_of("test.plain") == ("Generate", None)
    assert folder_words_of("test.task") == ("Generate", None)
