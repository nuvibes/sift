# SPDX-License-Identifier: AGPL-3.0-or-later
"""A setting's line on History: its values in its own control's words, and who changed it.

Beside `test_sentences.py`, whose table holds every line once; these read the setting line's values
and its folded form word for word, and the line an update writes for a default it changed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sift.kernel.access import sentences as say


def _feed(verb: str, **rest: object) -> say.Line:
    """One ledger act in the Settings feed, which names everything."""
    by = str(rest.pop("by", say.YOU))
    return say.feed_line(verb, by=by, **rest).pieces  # type: ignore[arg-type]


def test_a_setting_says_its_values_in_its_own_controls_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Said "from 40% to 70%", not "from 40 to 70": the unit, the menu's word for a choice
    and the word for an automatic zero, all read off the setting's declaration."""
    from sift.kernel import settings_registry

    declared = {
        "t.volume": SimpleNamespace(
            unit="%", choices=None, choice_labels=None, automatic_label=None, names_a_tunnel=False
        ),
        "t.device": SimpleNamespace(
            unit=None,
            choices=("cpu", "nvidia"),
            choice_labels=("CPU", "GPU"),
            automatic_label=None,
            names_a_tunnel=False,
        ),
        "t.workers": SimpleNamespace(
            unit=None,
            choices=None,
            choice_labels=None,
            automatic_label="Automatic",
            names_a_tunnel=False,
        ),
    }
    for key, one in declared.items():
        monkeypatch.setitem(settings_registry._REGISTRY, key, one)

    def changed(key: str, name: str, before: str, after: str) -> str:
        line = _feed(
            "edited",
            subjects=[("setting", say.Piece(name))],
            payload={"key": key, "before": before, "after": after},
        )
        return say.text_of(line)

    assert changed("t.volume", "Starting volume", "40", "70") == (
        "You changed Starting volume from 40% to 70%"
    )
    assert changed("t.device", "Run recognition on", '"cpu"', '"nvidia"') == (
        "You changed Run recognition on from CPU to GPU"
    )
    assert changed("t.workers", "Tasks at the same time", "4", "0") == (
        "You changed Tasks at the same time from 4 to Automatic"
    )
    # A key nothing declares says the stored value, as before.
    assert changed("t.unknown", "Other", "4", "5") == "You changed Other from 4 to 5"
    # A value that is not plain is said in the words the writer kept for it: a Site's name
    # template put back to following the default says so, with the template it had.
    cleared = _feed(
        "edited",
        by="Sift",
        subjects=[("setting", say.Piece("Instagram's name template"))],
        payload={
            "key": "site_options.instagram.naming",
            "before": '"{site}{creator}{name}"',
            "after": "null",
            "after_said": "the default",
        },
    )
    assert say.text_of(cleared) == (
        "Sift changed Instagram's name template from {site}{creator}{name} to the default"
    )


def test_a_tunnel_setting_says_the_tunnels_name_and_never_its_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.kernel import settings_registry

    monkeypatch.setitem(
        settings_registry._REGISTRY,
        "t.route",
        SimpleNamespace(
            unit=None, choices=None, choice_labels=None, automatic_label=None, names_a_tunnel=True
        ),
    )

    def changed(**payload: object) -> str:
        line = _feed(
            "edited",
            subjects=[("setting", say.Piece("Connect to AcoustID through"))],
            payload={"key": "t.route", "before": "null", "after": '"tun-7c1e"', **payload},
        )
        return say.text_of(line)

    assert changed() == "You changed Connect to AcoustID through"
    assert changed(before_said="Direct", after_said="Home VPN") == (
        "You changed Connect to AcoustID through from Direct to Home VPN"
    )


def test_a_sites_setting_given_and_removed_over_a_sitting_says_it_was_left_where_it_was() -> None:
    """A Site's name template and folder are recorded in words (`before_said`, `after_said`). Given
    and removed within a sitting, the folded line says what a toggle moved back says, never a change
    that changed nothing."""

    def folded(name: str, first: dict[str, object], payload: dict[str, object]) -> str:
        return say.text_of(
            say.feed_folded(
                "edited",
                by="You",
                acts=2,
                subjects=[("setting", say.Piece(name))],
                first=first,
                payload=payload,
            ).pieces
        )

    naming = "site_options.tiktok.naming"
    assert folded(
        "TikTok's name template",
        {"key": naming, "before": None, "after": "{name}", "before_said": "Sift's name"},
        {"key": naming, "before": "{name}", "after": None, "after_said": "Sift's name"},
    ) == ("You changed TikTok's name template 2 times and left it at Sift's name")
    folder = "site_options.tiktok.dest_folder_id"
    unset = "the default downloads folder"
    assert folded(
        "TikTok's download folder",
        {"key": folder, "before": None, "after": None, "before_said": unset},
        {"key": folder, "before": None, "after": None, "after_said": unset},
    ) == (f"You changed TikTok's download folder 2 times and left it at {unset}")
    # One that did move still says where it started and where it was left.
    assert folded(
        "TikTok's download folder",
        {"key": folder, "before": None, "after": None, "before_said": unset},
        {"key": folder, "before": None, "after": None, "after_said": "Holiday"},
    ) == (f"You changed TikTok's download folder from {unset} to Holiday")


def test_a_default_an_update_changed_is_said_as_that_update_folded_or_not() -> None:
    """The first start of a release writes it (`settings_hub.defaults`). The actor is the update by
    its release, never Sift alone, on the plain line and on a press the feed folds."""
    payload: dict[str, object] = {"key": "k", "after": "true", "update_to": "0.1.218"}
    plain = _feed(
        "edited",
        by="Sift",
        task="update",
        subjects=[("setting", say.Piece("Faces"))],
        payload=payload,
    )
    assert say.text_of(plain) == "The update to 0.1.218 changed Faces to on"
    folded = say.feed_folded(
        "edited",
        by="Sift",
        acts=2,
        subjects=[("setting", say.Piece("Faces"))],
        first={"key": "k", "before": "false", "update_to": "0.1.218"},
        payload=payload,
    )
    assert say.text_of(folded.pieces) == "The update to 0.1.218 changed Faces from off to on"
