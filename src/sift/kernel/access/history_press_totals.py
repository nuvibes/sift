# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each press holds, kept as the record changes: per thing it was done with, its acts, those
that recorded no task and those still standing; per thing it was about, its acts and the newest.

Raw, with no vault and no narrowing, so it is a folded line's things exactly when the vault is open.
An act takes its share with it wherever its label moves (an arrival labelled, the smaller press of a
join relabelled), so a join adds two presses' rows; a parting or a move, which the press triggers
(`history_presses`) end by summing their presses again, leaves no newest act behind.
"""

from __future__ import annotations

from typing import Final

from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import LEDGER_QUEUE

# `object_kind` and `object_id` may be NULL (a setting's act), so the objects' key is matched with
# IS and has no unique index: NULLs are distinct to one.
TOTALS_TABLES: Final = (
    """CREATE TABLE IF NOT EXISTS workbench_press_objects (
  press_id    TEXT NOT NULL,
  object_kind TEXT,
  object_id   TEXT,
  name        TEXT,
  acts        INTEGER NOT NULL,
  untold      INTEGER NOT NULL,
  standing    INTEGER NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS ix_workbench_press_objects"
    " ON workbench_press_objects(press_id, object_kind, object_id)",
    """CREATE TABLE IF NOT EXISTS workbench_press_subjects (
  press_id   TEXT NOT NULL,
  kind       TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  name       TEXT,
  acts       INTEGER NOT NULL,
  at         INTEGER NOT NULL,
  latest     TEXT NOT NULL,
  PRIMARY KEY (press_id, kind, subject_id)
) WITHOUT ROWID""",
)

#: Whether the act `{{R}}` still stands: a press's own act not undone.
_STANDS = "({{R}}.queue <> {{LEDGER}} AND {{R}}.reversed_at IS NULL)"
_LEDGER = f"'{LEDGER_QUEUE}'"


def _put(template: str, **parts: str) -> str:
    """`template` with the parts named, leaving any other marker for the statement that uses it."""
    for name, part in parts.items():
        template = template.replace("{{" + name + "}}", part)
    return template


def _of(row: str) -> str:
    return _put(_STANDS, R=row, LEDGER=_LEDGER)


# The larger of two names where either may be NULL, as MAX over rows reads them.
_LARGER_NAME = "COALESCE(MAX({{A}}, {{B}}), {{A}}, {{B}})"

# A press's things summed from its acts, under `{{WHERE}}`.
_SUM_OBJECTS = _put(
    """INSERT INTO workbench_press_objects
       (press_id, object_kind, object_id, name, acts, untold, standing)
SELECT press_id, object_kind, object_id, MAX(object_name), COUNT(*),
       SUM(actor_id IS NULL), SUM({{STANDS}})
  FROM workbench_decisions WHERE {{WHERE}}
 GROUP BY press_id, object_kind, object_id""",
    STANDS=_of("workbench_decisions"),
)
_SUM_SUBJECTS = """INSERT INTO workbench_press_subjects
       (press_id, kind, subject_id, name, acts, at, latest)
SELECT d.press_id, s.kind, s.subject_id, MAX(s.name), COUNT(*), MAX(d.decided_at), MAX(d.id)
  FROM workbench_decisions d CROSS JOIN workbench_decision_subjects s ON s.decision_id = d.id
 WHERE {{WHERE}}
 GROUP BY d.press_id, s.kind, s.subject_id"""

#: The press `{{L}}` summed again from its acts: a parting's two sides, a move's presses.
RESUM = _put(
    """DELETE FROM workbench_press_objects WHERE press_id = {{L}};
{{OBJECTS}};
DELETE FROM workbench_press_subjects WHERE press_id = {{L}};
{{SUBJECTS}}""",
    OBJECTS=_put(_SUM_OBJECTS, WHERE="press_id = {{L}}"),
    SUBJECTS=_put(_SUM_SUBJECTS, WHERE="d.press_id = {{L}}"),
)

#: Every press summed from the record, for a record whose totals are missing or were not kept.
TOTAL_ALL: Final = (
    "DELETE FROM workbench_press_objects",
    splice(_SUM_OBJECTS, WHERE="press_id IS NOT NULL"),
    "DELETE FROM workbench_press_subjects",
    splice(_SUM_SUBJECTS, WHERE="d.press_id IS NOT NULL"),
)

# One thing of press `{{L}}` summed again: the act's object `{{K}}`, `{{I}}`.
_OBJECT_AGAIN = _put(
    """DELETE FROM workbench_press_objects
 WHERE press_id = {{L}} AND object_kind IS {{K}} AND object_id IS {{I}};
{{SUM}}""",
    SUM=_put(
        _SUM_OBJECTS, WHERE="press_id = {{L}} AND object_kind IS {{K}} AND object_id IS {{I}}"
    ),
)
_SUBJECT_AGAIN = _put(
    """DELETE FROM workbench_press_subjects
 WHERE press_id = {{L}} AND kind = {{K}} AND subject_id = {{I}};
{{SUM}}""",
    SUM=_put(_SUM_SUBJECTS, WHERE="d.press_id = {{L}} AND s.kind = {{K}} AND s.subject_id = {{I}}"),
)

# An act that changes in place, its press and its place kept: what a move does not cover.
_STAYS = (
    "NEW.press_id IS NOT NULL AND OLD.press_id IS NEW.press_id AND OLD.fold_key IS NEW.fold_key"
    " AND OLD.decided_at IS NEW.decided_at AND OLD.id IS NEW.id"
)
_SAME_THING = (
    "OLD.object_kind IS NEW.object_kind AND OLD.object_id IS NEW.object_id"
    " AND OLD.object_name IS NEW.object_name"
)
_LABEL_OF = "(SELECT q.press_id FROM workbench_decisions q WHERE q.id = {{ACT}})"

# The act's share leaving its old press: a row emptied goes. Its newest act is not looked for again,
# since every press an act leaves part of is summed again by the press trigger that parted it.
_LEAVES = splice(
    """UPDATE workbench_press_objects
   SET acts = acts - 1, untold = untold - (OLD.actor_id IS NULL), standing = standing - {{STANDS}}
 WHERE press_id = OLD.press_id AND object_kind IS OLD.object_kind AND object_id IS OLD.object_id;
DELETE FROM workbench_press_objects
 WHERE press_id = OLD.press_id AND object_kind IS OLD.object_kind AND object_id IS OLD.object_id
   AND acts <= 0;
UPDATE workbench_press_subjects SET acts = acts - 1
 WHERE press_id = OLD.press_id AND (kind, subject_id) IN {{ITS}};
DELETE FROM workbench_press_subjects
 WHERE press_id = OLD.press_id AND (kind, subject_id) IN {{ITS}} AND acts <= 0""",
    STANDS=_of("OLD"),
    ITS="(SELECT s.kind, s.subject_id FROM workbench_decision_subjects s WHERE s.decision_id = NEW.id)",
)

# ...and arriving in its new one.
_ARRIVES = splice(
    """UPDATE workbench_press_objects
   SET acts = acts + 1, untold = untold + (NEW.actor_id IS NULL), standing = standing + {{STANDS}},
       name = {{NAME}}
 WHERE press_id = NEW.press_id AND object_kind IS NEW.object_kind AND object_id IS NEW.object_id;
INSERT INTO workbench_press_objects
       (press_id, object_kind, object_id, name, acts, untold, standing)
SELECT NEW.press_id, NEW.object_kind, NEW.object_id, NEW.object_name, 1, NEW.actor_id IS NULL,
       {{STANDS}}
 WHERE NEW.press_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM workbench_press_objects WHERE press_id = NEW.press_id
          AND object_kind IS NEW.object_kind AND object_id IS NEW.object_id);
INSERT INTO workbench_press_subjects (press_id, kind, subject_id, name, acts, at, latest)
SELECT NEW.press_id, s.kind, s.subject_id, s.name, 1, NEW.decided_at, NEW.id
  FROM workbench_decision_subjects s WHERE s.decision_id = NEW.id AND NEW.press_id IS NOT NULL
    ON CONFLICT DO UPDATE SET acts = acts + 1, name = {{SUBJECT_NAME}},
       at = MAX(at, excluded.at), latest = MAX(latest, excluded.latest)""",
    STANDS=_of("NEW"),
    NAME=_put(_LARGER_NAME, A="name", B="NEW.object_name"),
    SUBJECT_NAME=_put(_LARGER_NAME, A="name", B="excluded.name"),
)

#: The totals' own triggers: an act's label moving, a subject arriving, changing or going, and an
#: act undone, redone or renamed where it stands.
TOTALS_TRIGGERS: Final = (
    (
        "workbench_totals_act_moves",
        "CREATE TRIGGER IF NOT EXISTS workbench_totals_act_moves"
        " AFTER UPDATE OF press_id ON workbench_decisions"
        f" WHEN OLD.press_id IS NOT NEW.press_id BEGIN {_LEAVES}; {_ARRIVES}; END",
    ),
    (
        "workbench_totals_subject_arrives",
        splice(
            "CREATE TRIGGER IF NOT EXISTS workbench_totals_subject_arrives"
            " AFTER INSERT ON workbench_decision_subjects BEGIN"
            " INSERT INTO workbench_press_subjects"
            " (press_id, kind, subject_id, name, acts, at, latest)"
            " SELECT d.press_id, NEW.kind, NEW.subject_id, NEW.name, 1, d.decided_at, d.id"
            " FROM workbench_decisions d WHERE d.id = NEW.decision_id AND d.press_id IS NOT NULL"
            " ON CONFLICT DO UPDATE SET acts = acts + 1, name = {{NAME}},"
            " at = MAX(at, excluded.at), latest = MAX(latest, excluded.latest); END",
            NAME=splice(_LARGER_NAME, A="name", B="excluded.name"),
        ),
    ),
    (
        "workbench_totals_subject_changes",
        "CREATE TRIGGER IF NOT EXISTS workbench_totals_subject_changes"
        " AFTER UPDATE ON workbench_decision_subjects BEGIN "
        + splice(
            _SUBJECT_AGAIN,
            L=splice(_LABEL_OF, ACT="OLD.decision_id"),
            K="OLD.kind",
            I="OLD.subject_id",
        )
        + "; "
        + splice(
            _SUBJECT_AGAIN,
            L=splice(_LABEL_OF, ACT="NEW.decision_id"),
            K="NEW.kind",
            I="NEW.subject_id",
        )
        + "; END",
    ),
    (
        "workbench_totals_subject_goes",
        "CREATE TRIGGER IF NOT EXISTS workbench_totals_subject_goes"
        " AFTER DELETE ON workbench_decision_subjects BEGIN "
        + splice(
            _SUBJECT_AGAIN,
            L=splice(_LABEL_OF, ACT="OLD.decision_id"),
            K="OLD.kind",
            I="OLD.subject_id",
        )
        + "; END",
    ),
    (
        "workbench_totals_act_stands",
        splice(
            "CREATE TRIGGER IF NOT EXISTS workbench_totals_act_stands"
            " AFTER UPDATE OF queue, actor_id, reversed_at ON workbench_decisions"
            " WHEN {{STAYS}} AND {{SAME_THING}} BEGIN"
            " UPDATE workbench_press_objects"
            " SET untold = untold - (OLD.actor_id IS NULL) + (NEW.actor_id IS NULL),"
            " standing = standing - {{WAS}} + {{IS}}"
            " WHERE press_id = NEW.press_id AND object_kind IS NEW.object_kind"
            " AND object_id IS NEW.object_id; END",
            STAYS=_STAYS,
            SAME_THING=_SAME_THING,
            WAS=_of("OLD"),
            IS=_of("NEW"),
        ),
    ),
    (
        "workbench_totals_act_renamed",
        "CREATE TRIGGER IF NOT EXISTS workbench_totals_act_renamed"
        " AFTER UPDATE OF object_kind, object_id, object_name ON workbench_decisions"
        f" WHEN {_STAYS} AND NOT ({_SAME_THING}) BEGIN "
        + splice(_OBJECT_AGAIN, L="NEW.press_id", K="OLD.object_kind", I="OLD.object_id")
        + "; "
        + splice(_OBJECT_AGAIN, L="NEW.press_id", K="NEW.object_kind", I="NEW.object_id")
        + "; END",
    ),
)

#: The same triggers dropped, for a test that rebuilds them.
TOTALS_DROPS: Final = tuple(f"DROP TRIGGER {name}" for name, _ddl in TOTALS_TRIGGERS)

#: Totals that differ from the record summed again, either way round. Empty is the only right answer.
TOTALS_DIFFERENCES = splice(
    """
WITH objects AS (
  SELECT press_id, object_kind, object_id, MAX(object_name) AS name, COUNT(*) AS acts,
         SUM(actor_id IS NULL) AS untold, SUM({{STANDS}}) AS standing
    FROM workbench_decisions WHERE press_id IS NOT NULL
   GROUP BY press_id, object_kind, object_id
), subjects AS (
  SELECT d.press_id, s.kind, s.subject_id, MAX(s.name) AS name, COUNT(*) AS acts,
         MAX(d.decided_at) AS at, MAX(d.id) AS latest
    FROM workbench_decisions d CROSS JOIN workbench_decision_subjects s ON s.decision_id = d.id
   WHERE d.press_id IS NOT NULL
   GROUP BY d.press_id, s.kind, s.subject_id
), kept_objects AS (
  SELECT press_id, object_kind, object_id, name, acts, untold, standing FROM workbench_press_objects
), kept_subjects AS (
  SELECT press_id, kind, subject_id, name, acts, at, latest FROM workbench_press_subjects
)
SELECT 'object' AS part, press_id FROM (SELECT * FROM objects EXCEPT SELECT * FROM kept_objects)
UNION ALL
SELECT 'object', press_id FROM (SELECT * FROM kept_objects EXCEPT SELECT * FROM objects)
UNION ALL
SELECT 'subject', press_id FROM (SELECT * FROM subjects EXCEPT SELECT * FROM kept_subjects)
UNION ALL
SELECT 'subject', press_id FROM (SELECT * FROM kept_subjects EXCEPT SELECT * FROM subjects)
""",
    STANDS=_of("workbench_decisions"),
)
