# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-box switches, and one rule per field they can fill in, generated from the record registry."""

from __future__ import annotations

from sift.kernel.enrichment import DEFAULT_STRATEGY, Strategy, strategy_key
from sift.kernel.records import Subject, fields_of
from sift.kernel.seams import SettingsSeam
from sift.kernel.settings_registry import ReadBy, register_setting
from sift.slices.stash_boxes.known_boxes import KNOWN_BOXES

SECTION = "Stash-boxes"

#: Whether files are matched against the stash-boxes at all: a consent gate for sending fingerprints.
SCAN_KEY = "stash_boxes.scan"
#: Whether NEW files are asked about on their own; `SCAN_KEY` still decides whether at all.
ASK_NEW_FILES_KEY = "stash_boxes.ask_new_files"

#: Whether an exact-hash match applies itself on a run nobody pressed (see `apply_for`).
AUTO_APPLY_KEY = "stash_boxes.apply_certain"

#: Whether a record shows every field a stash-box can fill in; display only, nothing stored changes.
SHOW_EVERY_FIELD_KEY = "records.show_every_field"

#: The kinds an unattended run may create when new, each with its own switch.
INVENTABLE: tuple[Subject, ...] = (Subject.PERSON, Subject.SITE, Subject.TAG)


def invent_key(subject: Subject) -> str:
    """The switch for one kind, built so its key is spelled in one place."""
    return f"stash_boxes.invent.{subject.value}"


_INVENT_LABEL = {
    Subject.PERSON: "people",
    Subject.SITE: "Sites",
    Subject.TAG: "tags",
}


async def may_invent(settings: SettingsSeam) -> frozenset[str]:
    """Which kinds an unattended run may create; the one reader of the three switches."""
    allowed: set[str] = set()
    for subject in INVENTABLE:
        if bool(await settings.get_app(invent_key(subject))):
            allowed.add(subject.value)
    return frozenset(allowed)


def _invent_switches() -> None:
    for subject in INVENTABLE:
        register_setting(
            key=invent_key(subject),
            scope="app",
            default=True,
            section=SECTION,
            # Six words at most, as a settings label gets.
            label=f"Create new {_INVENT_LABEL[subject]}",
            disclosure=(
                "Applies only to automatic lookups and to Auto-enrich. What you confirm yourself "
                "always creates what you select."
            ),
            help=(
                f"When off, {_INVENT_LABEL[subject]} a stash-box names that your library doesn't "
                "have are skipped."
            ),
        )


#: Which stash-box the unattended passes ask, by the box's own word; empty asks every switched-on
#: box. Sift's own passes are not narrowed by it.
AUTO_BOX_KEY = "stash_boxes.auto_box"

_AUTO_BOX_WORDS = ("", *(word for word, _spelling in KNOWN_BOXES))
_AUTO_BOX_LABELS = ("All stash-boxes", *(spelling for _word, spelling in KNOWN_BOXES))


async def auto_box(settings: SettingsSeam) -> str | None:
    """Which box the unattended passes may ask, or None for every switched-on one."""
    held = str(await settings.get_app(AUTO_BOX_KEY) or "").strip().lower()
    return held or None


#: The word a press sends for every switched-on box; an absent word means the setting.
EVERY_BOX = "all"


#: What a press says when every stash-box is off, and when the one it would ask is.
EVERY_BOX_OFF = "Every stash-box is turned off. Turn one on under Stash-boxes."
#: What a press says when enriching itself (`SCAN_KEY`) is off.
ENRICHING_OFF = "Enriching with stash-boxes is turned off. Turn it on under Stash-boxes."
CHOSEN_BOX_OFF = (
    "The stash-box this would ask is turned off. Turn it on, or choose another, under Stash-boxes."
)


async def box_for(settings: SettingsSeam, named: str | None) -> str:
    """Which box a press asks, resolved once at the route: a box's word, `EVERY_BOX`, or the setting."""
    word = str(named or "").strip().lower()
    if word == EVERY_BOX:
        return ""
    if word:
        return word
    return await auto_box(settings) or ""


#: How far apart two lengths may be and still be the same release, in seconds.
DURATION_KEY = "stash_boxes.duration_tolerance_s"
DURATION_DEFAULT_S = 10

STRATEGIES = tuple(one.value for one in Strategy)
STRATEGY_LABELS = (
    "Leave alone",
    "Fill in what is missing",
    "Replace what is there",
)

#: Subjects with per-field rules; no stash-box says anything about a collection or a photo set.
ENRICHED: tuple[Subject, ...] = (Subject.PERSON, Subject.SITE, Subject.TAG, Subject.ASSET)

_CALLED = {
    Subject.PERSON: "a person",
    Subject.SITE: "a Site",
    Subject.TAG: "a tag",
    Subject.ASSET: "a file",
}


def _switches() -> None:
    register_setting(
        key=SCAN_KEY,
        scope="app",
        default=False,
        section=SECTION,
        label="Enrich your library with stash-boxes",
        disclosure=(
            "A stash-box is a shared database of details about adult videos and the people in them. "
            "Sift sends each file's fingerprint and adds the details of any match to your library. "
            "Your files are never sent."
        ),
        help=(
            "Sift looks up your files on the stash-boxes you add, using your API key for each, and "
            "fills in their details."
        ),
        # Not asked at first run: nothing runs until a box is added.
    )
    # The schedule is `tasks.enrichment.when`; this key stays as the name the sweep asks by.
    register_setting(
        key=AUTO_APPLY_KEY,
        scope="app",
        default=True,
        section=SECTION,
        label="Confirm exact matches automatically",
        disclosure=(
            "Running Auto-enrich yourself always confirms exact matches, because you asked for it. "
            "This decides only for lookups Sift starts by itself, such as for new files. Other "
            "matches wait in Organize either way."
        ),
        help=(
            "When an automatic lookup finds an exact match, Sift adds the details right away. "
            "Other matches wait in Organize."
        ),
    )
    _record_switches()


def _record_switches() -> None:
    register_setting(
        key=SHOW_EVERY_FIELD_KEY,
        # Per user: it stores and requests nothing.
        scope="user",
        # A stored value stands; only one never stored follows the default.
        default=True,
        section="Appearance",
        label="Show every field on a record",
        # Read by the browser only.
        read_by=ReadBy.CLIENT,
        disclosure=(
            "The extra fields are ones only a stash-box fills in, like eye color and height. "
            "Nothing is saved or deleted either way."
        ),
        help="When off, a record shows only its main fields.",
        default_since="0.1.218",
    )
    register_setting(
        key=AUTO_BOX_KEY,
        scope="app",
        default="",
        section=SECTION,
        choices=_AUTO_BOX_WORDS,
        choice_labels=_AUTO_BOX_LABELS,
        label="Auto-enrich using",
        disclosure=(
            "Every stash-box you add stays on, and you can still search any of them yourself."
        ),
        help=(
            "The stash-box Sift's automatic lookups use, and the one Auto-enrich uses when you don't "
            "choose one."
        ),
    )
    register_setting(
        key=DURATION_KEY,
        scope="app",
        default=DURATION_DEFAULT_S,
        section=SECTION,
        label="Allow lengths to differ by",
        minimum=0,
        maximum=600,
        unit="sec",
        disclosure=(
            "Sift ignores a match whose length differs by more than this, so a different cut of "
            "the same video isn't taken for yours."
        ),
        help="Two copies of the same video run for about the same time.",
    )


def _rules() -> None:
    for subject in ENRICHED:
        for one in fields_of(subject):
            if not one.imported:
                continue
            register_setting(
                key=strategy_key(subject, one.key),
                scope="app",
                default=DEFAULT_STRATEGY.value,
                section=SECTION,
                # The field's own name: the subject is the group heading above it.
                label=one.label,
                choices=STRATEGIES,
                choice_labels=STRATEGY_LABELS,
                # One short line the screen does not draw; `SharedQuestion` explains them all.
                help=f"What Sift does when a stash-box has this for {_CALLED[subject]}.",
            )


_switches()
_invent_switches()
_rules()
