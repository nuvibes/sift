# SPDX-License-Identifier: AGPL-3.0-or-later
"""An undo takes back every table its decision wrote, or says why one is left.

A decision on the Organize board writes a receipt, and pressing Undo on it hands the receipt back to
the area that wrote it (`Reverser.reverse`). What that reverse puts back is written by hand, one
area at a time, and nothing else holds it to what the decision did. A username join whose undo
put back three of its five writes would leave the alias on the person and the search index
untold, so the search box would go on answering the person for the username after the undo said
it was done. An undo that leaves part of its decision behind reads as done and is not.

**So this is read from the code, both halves, the way the other write gates read statements.** For
every reverser that declares itself reversible, the accept path (the functions that take the
decision and write its receipt) and the reverse path are walked from named roots: every statement
each one runs, through every call it makes within the modules named for it, down to the SQL. The
tables the accept writes must all be tables the reverse writes, and the search index counts as a
table: an accept that tells the index must have a reverse that tells it too.

## What is excused, and how

A write that SHOULD outlive its undo is named in `LEFT_ON_PURPOSE` with the reason: a person the
decision created (somebody may have edited them since), a site a download may have attached to
since. Every entry is a decision somebody took; an entry nobody needs any more fails the build, so
the list cannot quietly cover a new gap later. A gap that is a gap and is not yet put back is named
in `OWED` instead, which is a debt and not an excuse. A reverser under whose name nothing writes a
receipt is in `NO_RECEIPT`, with where its decisions are written.

The unit is a TABLE, and that is the honest limit of a static reading: an undo that writes a table
its decision wrote is credited with it whether or not it puts back the same rows. The rows are the
tests' business, beside each reverser.

## The limit of it, said plainly

Calls are followed by NAME within the modules a reverser names for itself, so two functions of one
name in those modules are both followed. That over-reads, and on the reverse side an over-read is a
table the reverse is credited with and may not write. The roots and the modules are kept narrow for
that reason, and the known positive below (`test_a_reverse_that_loses_a_table_is_caught`) takes a
real table out of a real reverse and watches this fail.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"

#: What telling the search index looks like, at whichever level a writer reaches it. Counted as a
#: table of its own, because a decision that changed what a file is found by has an undo that has to
#: change it back, and a row put back with the index left answering the old words is half an undo.
INDEX = "(the search index)"
_INDEX_CALLS = frozenset({"touched", "touched_many", "_touched", "renamed", "index_assets"})

#: A write to a table, in any of the forms Sift writes: insert (with or without OR), update (never
#: the `DO UPDATE` of an upsert, whose table is the insert's), delete, and replace.
_WRITE = re.compile(
    r"\b(?:insert\s+(?:or\s+\w+\s+)?into|(?<!do\s)update(?!\s+set\b)|delete\s+from|replace\s+into)"
    r"\s+([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
_TABLE = re.compile(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)")


@dataclass(frozen=True)
class Decision:
    """One kind of decision: where it is taken, where it is taken back, and what to read.

    `accept` and `reverse` are roots, `module:Qualified.name`, relative to `sift.`. `within` is
    every module whose functions a call may be followed into; the roots' own modules are always in.
    """

    accept: tuple[str, ...]
    reverse: tuple[str, ...]
    within: tuple[str, ...] = ()


#: The modules a decision's writes reach through, named once: the kernel's catalog (attributions,
#: filings, people made on the caller's connection) and the faces feature's store and mixins.
_CATALOG = tuple(
    f"kernel.access.catalog.{one}"
    for one in (
        "carried",
        "enrichment_runs",
        "filed_from",
        "made",
        "makers",
        "numbers",
        "people",
        "pictures",
        "posts",
        "refusals",
        "take_back",
        "unread",
        "usernames",
    )
)
#: The stash-box service, one module per act (`slices/stash_boxes/service.py` is their door).
_STASH_SERVICE = tuple(
    f"slices.stash_boxes.{one}"
    for one in ("configured", "asking", "linking", "matches", "scanning", "ledger")
)
_FACES = tuple(
    f"slices.faces.{one}"
    for one in (
        "store",
        "evidence",
        "service_base",
        "service_decisions",
        "service_grouping",
        "service_identified",
        "service_matching",
        "service_may_be",
        "service_pictures",
        "service_references",
        "service_runs",
        "service_scanning",
        "service_starters",
    )
)

#: Every reverser that says `reversible = True`, by `module:Class`, and its decision.
#:
#: A reverser missing from here fails `test_every_reversible_reverser_is_read`: a new kind of
#: decision with an Undo is exactly the kind this is about.
_PEOPLE = tuple(
    f"slices.people.{one}"
    for one in (
        "service_base",
        "service_files",
        "service_merges",
        "service_people",
        "service_sites",
        "service_usernames",
    )
)
_SUGGESTIONS = tuple(
    f"slices.suggestions.{one}"
    for one in (
        "store",
        "service_base",
        "service_confirm",
        "service_filenames",
        "service_folders",
        "service_pass",
        "service_pictures",
        "service_screen",
        "service_undo",
    )
)

#: The content store, one module per part of it.
_IDENTITY = tuple(
    f"kernel.content.{name}"
    for name in (
        "identity",
        "identity_arrivals",
        "identity_counts",
        "identity_derivatives",
        "identity_fields",
        "identity_models",
        "identity_paths",
        "identity_places",
        "identity_probes",
        "identity_store",
        "identity_verdicts",
    )
)

DECISIONS: dict[str, Decision] = {
    "slices.dedup.queue:DedupQueue": Decision(
        accept=(
            "slices.dedup.service:DedupService.dismiss_group",
            "slices.dedup.service:DedupService.settle_group",
        ),
        reverse=("slices.dedup.queue:DedupQueue.reverse",),
        within=("slices.dedup.service",),
    ),
    "slices.dedup.queue:CarriedAttributions": Decision(
        accept=("slices.dedup.service:DedupService.carry",),
        reverse=("slices.dedup.queue:CarriedAttributions.reverse",),
        within=("slices.dedup.service", *_CATALOG),
    ),
    "slices.faces.queue:IgnoredRecords": Decision(
        accept=("slices.faces.service_decisions:DecisionsMixin.ignore",),
        reverse=("slices.faces.queue:IgnoredRecords.reverse",),
        within=_FACES,
    ),
    "slices.faces.queue:IdentifiedRecords": Decision(
        accept=(
            "slices.faces.service_decisions:DecisionsMixin.confirm_many",
            "slices.faces.service_may_be:MayBeMixin.confirm_groups",
            "slices.faces.service_may_be:MayBeMixin.name_groups",
            "slices.faces.service_may_be:MayBeMixin.refuse_groups",
            "slices.faces.service_runs:RunsMixin.confirm_look_alikes",
            "slices.faces.service_runs:RunsMixin.confirm_matches",
            "slices.faces.service_runs:RunsMixin._refuse_run",
            "slices.faces.service_matching:MatchingMixin.rematch",
        ),
        reverse=("slices.faces.queue:IdentifiedRecords.reverse",),
        within=_FACES,
    ),
    "slices.faces.jobs:StarterRecords": Decision(
        accept=(
            "slices.faces.service_starters:StartersMixin.file_starters",
            "slices.faces.service_starters:StartersMixin.record_starters",
        ),
        reverse=("slices.faces.jobs:StarterRecords.reverse",),
        within=_FACES,
    ),
    "slices.faces.service_fingerprints:FingerprintRecords": Decision(
        accept=(
            "slices.faces.service_fingerprints:FingerprintsMixin.recognize_from_fingerprints",
            "slices.faces.service_fingerprints:FingerprintsMixin.make_person_from_entry",
        ),
        reverse=("slices.faces.service_fingerprints:FingerprintRecords.reverse",),
        within=(
            *_FACES,
            "slices.faces.service_fingerprints",
            "slices.faces.store_references",
            "slices.faces.store_people",
            *_CATALOG,
        ),
    ),
    "slices.music.names:SharedNameReceipts": Decision(
        accept=("slices.music.names:SongNames.spread",),
        reverse=("slices.music.names:SharedNameReceipts.reverse",),
        within=("slices.music.store", *_IDENTITY),
    ),
    "slices.organize.batch:BatchRenameReceipts": Decision(
        accept=("slices.organize.batch:BatchRenamer.carry_out",),
        reverse=("slices.organize.batch:BatchRenameReceipts.reverse",),
        within=("slices.organize.service", *_IDENTITY),
    ),
    # The settings the benchmark of a device set, as Sift's own save; taken back as the person's own
    # save through the same door, value by value, where the value is still what Sift set.
    "slices.performance.benchmark:BenchmarkReceipts": Decision(
        accept=("slices.settings_hub.service:SettingsService.apply_as_sift",),
        reverse=("slices.performance.benchmark:BenchmarkReceipts.reverse",),
        within=("slices.settings_hub.service",),
    ),
    "slices.people.queue:UsernameQueue": Decision(
        accept=("slices.people.service_usernames:UsernamesMixin.attach_username",),
        reverse=("slices.people.queue:UsernameQueue.reverse",),
        within=_PEOPLE,
    ),
    "slices.shoots.queue:ShootQueue": Decision(
        accept=(
            "slices.shoots.service:ShootService._make",
            "slices.shoots.service:ShootService.name_the_rest",
        ),
        reverse=("slices.shoots.queue:ShootQueue.reverse",),
        within=(
            "slices.shoots.service",
            "slices.shoots.store",
            "slices.photo_sets.service",
            *_CATALOG,
        ),
    ),
    # A Site a stash-box made, filed as a username or kept as a Site: by a person on the stash-box
    # page, or by the catalog's own repair; every move is put back from its receipt.
    "slices.stash_boxes.queue:StudioQueue": Decision(
        accept=(
            "slices.stash_boxes.studios:CreatorStudios.turn",
            "slices.stash_boxes.studios:CreatorStudios.keep",
            "kernel.access.creator_studios:repair",
        ),
        reverse=("slices.stash_boxes.queue:StudioQueue.reverse",),
        within=("slices.stash_boxes.studios", "kernel.access.creator_studios"),
    ),
    "slices.stash_boxes.queue:TaggerQueue": Decision(
        accept=("slices.stash_boxes.router_matches:apply_matches",),
        reverse=("slices.stash_boxes.queue:TaggerQueue.reverse",),
        within=(*_STASH_SERVICE, *_CATALOG),
    ),
    "slices.stash_boxes.reconcile:ReconcileReceipts": Decision(
        accept=("slices.stash_boxes.reconcile:Reconciler.settle",),
        reverse=("slices.stash_boxes.reconcile:ReconcileReceipts.reverse",),
        within=(
            *_STASH_SERVICE,
            "slices.stash_boxes.enrich",
            "kernel.enrichment",
            *_CATALOG,
        ),
    ),
    "slices.suggestions.queue:FolderQueue": Decision(
        accept=(
            "slices.suggestions.service_confirm:ConfirmMixin.confirm",
            "slices.suggestions.service_confirm:ConfirmMixin.reject",
        ),
        reverse=("slices.suggestions.queue:FolderQueue.reverse",),
        within=(*_SUGGESTIONS, *_CATALOG),
    ),
    "slices.suggestions.queue:FiledFromFilenamesQueue": Decision(
        accept=(
            "slices.suggestions.service_filenames:FilenameRuleMixin._file_under",
            "slices.suggestions.service_filenames:FilenameRuleMixin._set_from_post",
            "slices.suggestions.service_undo:UndoMixin.take_back_username",
            "slices.suggestions.service_pictures:MetadataRuleMixin._learn_number",
        ),
        reverse=("slices.suggestions.queue:FiledFromFilenamesQueue.reverse",),
        within=(*_SUGGESTIONS, *_CATALOG),
    ),
    "slices.suggestions.queue:FiledQueue": Decision(
        accept=(
            "slices.suggestions.service_folders:FolderRuleMixin._attribute",
            "slices.suggestions.service_undo:UndoMixin.take_back_folder",
        ),
        reverse=("slices.suggestions.queue:FiledQueue.reverse",),
        within=(*_SUGGESTIONS, *_CATALOG),
    ),
    "slices.watermarks.queue:WatermarkFilings": Decision(
        accept=("slices.watermarks.service:WatermarkService._settle",),
        reverse=("slices.watermarks.queue:WatermarkFilings.reverse",),
        within=("slices.watermarks.service", "slices.watermarks.store", *_CATALOG),
    ),
}

#: Reversers that declare Undo and under whose name nothing writes a receipt, so there is no
#: decision of theirs to read. Each says where its decisions are written instead.
NO_RECEIPT: dict[str, str] = {
    "slices.faces.queue:SuggestionsQueue": (
        "its answers are written as receipts of `identified`, and that reverser takes them back"
    ),
    "slices.faces.queue:DisagreementsQueue": (
        "its answers are written as receipts of `identified`, and that reverser takes them back"
    ),
    "slices.faces.queue:PeopleKnownQueue": (
        "its answers are written as receipts of `identified`, and that reverser takes them back"
    ),
    "slices.faces.queue:ToNameQueue": (
        "a group set aside is written under `ignored`; only a receipt from before that name"
        " reaches this reverse, which brings the group back exactly as `IgnoredRecords` does"
    ),
    "slices.faces.queue:SetAsideQueue": (
        "a group set aside is written under `ignored`; only a receipt from before that name"
        " reaches this reverse, which brings the group back exactly as `IgnoredRecords` does"
    ),
}

_SITE_STAYS = (
    "the site and its poster-unknown username are left standing: a download or a stash-box can"
    " attach to them within minutes, and an empty one is removed on its own page"
)

#: Tables a decision writes that its undo deliberately leaves, per reverser, with the reason.
LEFT_ON_PURPOSE: dict[str, dict[str, str]] = {
    "slices.people.queue:UsernameQueue": {
        "people": (
            "a person the join CREATED stays: somebody may have edited them since, and an undo"
            " that deletes a row somebody has worked on is worse than a person with no files"
        ),
    },
    "slices.stash_boxes.queue:TaggerQueue": {
        "enrichment_runs": (
            "what the stash-box answers wrote stays, and so does the run that wrote it: the undo"
            " puts the QUESTION back (a field filled in may have been corrected since), and the"
            " run is the record of a pass that happened"
        ),
    },
    "slices.suggestions.queue:FolderQueue": {"sites": _SITE_STAYS, "site_links": _SITE_STAYS},
    "slices.suggestions.queue:FiledFromFilenamesQueue": {
        "sites": _SITE_STAYS,
        "site_links": _SITE_STAYS,
        "usernames": (
            "the username a filing made is left where it is, for the reason the site is; a number"
            " learned from the pictures' metadata is a fact read off the files, not a filing"
        ),
    },
    "slices.watermarks.queue:WatermarkFilings": {
        "sites": _SITE_STAYS,
        "site_links": _SITE_STAYS,
        "usernames": "the username may be somebody's own, and a filing is all the reading decided",
        "tags": (
            "a watermark tag is a fact about the copy that stands whether or not the frame also"
            " carried an address worth filing on"
        ),
        "asset_tags": (
            "a watermark tag is a fact about the copy that stands whether or not the frame also"
            " carried an address worth filing on"
        ),
        "watermark_scans": (
            "the scan is the pass's memory that this file was read, not part of the filing it"
            " decided"
        ),
    },
}

#: WHAT AN UNDO CARRIES ALONG that the table rule cannot see, per reverser: the name of each function
#: its reverse must reach, with the reason. A consequence of an Undo that writes a table the reverse
#: already writes for its own faces (`face_tracks` here) is credited by the table whether or not it
#: is reached, so the reach itself is what is held. The Undo of a naming takes back the recognitions
#: that rested on the pictures it removed, and where it removed her last one, Sift's own questions
#: about her, which rest on nothing then either.
CARRIED_ALONG: dict[str, dict[str, str]] = {
    "slices.faces.queue:IdentifiedRecords": {
        "take_back_recognitions": (
            "a recognition whose every picture went with the Undo rests on nothing and goes with it"
        ),
        "return_questions": (
            "an Undo that leaves her no picture returns the questions Sift asked about her to"
            " nobody, since they rest on nothing"
        ),
    },
}
#: Gaps that are gaps: tables a decision writes and its undo SHOULD put back and does not yet. An
#: entry here is not an excuse; it is the list the next change to that reverser takes off.
OWED: dict[str, dict[str, str]] = {}


# --- reading the tree ------------------------------------------------------------------------------


def _tables() -> frozenset[str]:
    """Every table the schema declares. A word after UPDATE that is not one is not a write."""
    found: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        found.update(_TABLE.findall(path.read_text(encoding="utf-8")))
    return frozenset(found)


TABLES = _tables()


def _written_by(text: str) -> set[str]:
    return {one.lower() for one in _WRITE.findall(text) if one.lower() in TABLES}


def _strings_in(node: ast.AST) -> Iterator[str]:
    for piece in ast.walk(node):
        if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
            yield piece.value


def _docstrings(node: ast.AST) -> set[int]:
    """The ids of the docstring constants under a node, which are prose and never SQL."""
    found: set[int] = set()
    for piece in ast.walk(node):
        if isinstance(piece, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = piece.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _defined_statements(tree: ast.Module) -> dict[str, set[str]]:
    """Every module-level name holding a write, and the tables it writes.

    A bare string, a mapping or a sequence of them, or an f-string or a sum: every string inside the
    value is read, so a statement held in a dict by kind is seen the way a bare one is.
    """
    found: dict[str, set[str]] = {}
    for node in tree.body:
        targets: list[str] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = [one.id for one in node.targets if isinstance(one, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets, value = [node.target.id], node.value
        if value is None:
            continue
        written = set().union(*(_written_by(one) for one in _strings_in(value)))
        if written:
            for target in targets:
                found[target] = written
    return found


@dataclass
class Module:
    name: str
    tree: ast.Module
    #: Module-level names holding a write, and the tables each one writes.
    statements: dict[str, set[str]] = field(default_factory=dict)
    #: Every function, by qualified name (`Class.method` or `function`).
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)


class Tree:
    """The source, parsed on demand, with a way to stand one file's text in for another's.

    The override is what lets the known positive take a table out of a real reverse without
    touching the file on disk.
    """

    def __init__(self, overrides: Mapping[str, str] | None = None) -> None:
        self._overrides = dict(overrides or {})
        self._modules: dict[str, Module | None] = {}
        self._defined: dict[str, dict[str, set[str]]] = {}

    def module(self, name: str) -> Module | None:
        if name not in self._modules:
            self._modules[name] = self._read(name)
        return self._modules[name]

    def _text(self, name: str) -> str | None:
        relative = Path(*name.split("."))
        for candidate in (relative.with_suffix(".py"), relative / "__init__.py"):
            path = SOURCE / candidate
            if path.is_file():
                return self._overrides.get(name) or path.read_text(encoding="utf-8")
        return None

    def _read(self, name: str) -> Module | None:
        text = self._text(name)
        return None if text is None else self._parse(name, text)

    def _local(self, name: str) -> dict[str, set[str]]:
        """The statements a module DEFINES, without following its imports (which may be a cycle)."""
        if name not in self._defined:
            text = self._text(name)
            self._defined[name] = {} if text is None else _defined_statements(ast.parse(text))
        return self._defined[name]

    def _parse(self, name: str, text: str) -> Module:
        tree = ast.parse(text)
        module = Module(name=name, tree=tree, statements=_defined_statements(tree))
        # A statement imported from a sibling module is that module's, by the name it is imported
        # as: the shape `repository.store` writes `repository.folders`' statements in.
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if not node.module.startswith("sift."):
                    continue
                defined = self._local(node.module.removeprefix("sift."))
                for alias in node.names:
                    if alias.name in defined:
                        module.statements[alias.asname or alias.name] = defined[alias.name]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                module.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                for inner in node.body:
                    if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef):
                        module.functions[f"{node.name}.{inner.name}"] = inner
        return module


#: The methods of Python's own containers and strings. A call by one of these names is almost
#: always `seen.add(...)` or `found.update(...)`, and following it by name lands on whichever Sift
#: method shares the word (`StashBoxService.add` makes a stash-box), which would credit a decision
#: with writing a table it never goes near. Not followed, and that is a blind spot worth naming: a
#: Sift method with one of these names is not read through.
_CONTAINER_METHODS = frozenset(
    {
        "add",
        "append",
        "clear",
        "copy",
        "discard",
        "extend",
        "get",
        "insert",
        "items",
        "join",
        "keys",
        "pop",
        "remove",
        "replace",
        "setdefault",
        "sort",
        "split",
        "strip",
        "update",
        "values",
    }
)


def _called(node: ast.AST) -> Iterator[str]:
    """The last name of everything a function calls: `a.b.c(...)` is `c`."""
    for piece in ast.walk(node):
        if isinstance(piece, ast.Call):
            if isinstance(piece.func, ast.Name):
                yield piece.func.id
            elif (
                isinstance(piece.func, ast.Attribute) and piece.func.attr not in _CONTAINER_METHODS
            ):
                yield piece.func.attr


def writes(tree: Tree, roots: tuple[str, ...], within: tuple[str, ...]) -> set[str]:
    """Every table these roots write, following calls by name into the modules named."""
    modules = {root.split(":")[0] for root in roots} | set(within)
    by_name: dict[str, list[tuple[Module, ast.AST]]] = {}
    for name in sorted(modules):
        module = tree.module(name)
        assert module is not None, f"{name} is not a module under src/sift"
        for qualified, defined in module.functions.items():
            by_name.setdefault(qualified.rsplit(".", 1)[-1], []).append((module, defined))

    found: set[str] = set()
    seen: set[int] = set()
    todo: list[tuple[Module, ast.AST]] = []
    for root in roots:
        name, qualified = root.split(":")
        module = tree.module(name)
        assert module is not None and qualified in module.functions, f"{root} is not in the tree"
        todo.append((module, module.functions[qualified]))
    while todo:
        module, function = todo.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        prose = _docstrings(function)
        for piece in ast.walk(function):
            if isinstance(piece, ast.Name) and piece.id in module.statements:
                found |= module.statements[piece.id]
            elif (
                isinstance(piece, ast.Constant)
                and isinstance(piece.value, str)
                and id(piece) not in prose
            ):
                found |= _written_by(piece.value)
        for called in _called(function):
            if called in _INDEX_CALLS:
                found.add(INDEX)
            todo.extend(by_name.get(called, ()))
    return found


def reached(tree: Tree, roots: tuple[str, ...], within: tuple[str, ...]) -> set[str]:
    """The name of every function these roots reach, following calls by name as `writes` does."""
    modules = {root.split(":")[0] for root in roots} | set(within)
    by_name: dict[str, list[ast.AST]] = {}
    for name in sorted(modules):
        module = tree.module(name)
        assert module is not None, f"{name} is not a module under src/sift"
        for qualified, defined in module.functions.items():
            by_name.setdefault(qualified.rsplit(".", 1)[-1], []).append(defined)
    found: set[str] = set()
    todo: list[ast.AST] = []
    for root in roots:
        name, qualified = root.split(":")
        module = tree.module(name)
        assert module is not None and qualified in module.functions, f"{root} is not in the tree"
        todo.append(module.functions[qualified])
    seen: set[int] = set()
    while todo:
        function = todo.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        for called in _called(function):
            if called in by_name:
                found.add(called)
                todo.extend(by_name[called])
    return found


def gaps(tree: Tree, key: str, decision: Decision) -> set[str]:
    """What the accept writes that the reverse neither writes nor is excused from."""
    accepted = writes(tree, decision.accept, decision.within)
    reversed_ = writes(tree, decision.reverse, decision.within)
    return accepted - reversed_ - set(LEFT_ON_PURPOSE.get(key, {})) - set(OWED.get(key, {}))


# --- the rule --------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(DECISIONS))
def test_a_reverse_writes_every_table_its_decision_wrote(key: str) -> None:
    missing = gaps(Tree(), key, DECISIONS[key])

    assert not missing, (
        f"\n{key}: its decision writes these and its Undo leaves them as they are:\n  "
        + "\n  ".join(sorted(missing))
        + "\nPut them back in the reverse, from what the receipt recorded (the accept writes into"
        "\nits payload what it made), or name them in LEFT_ON_PURPOSE with the reason they stay.\n"
    )


def test_every_reversible_reverser_is_read() -> None:
    """A reverser that offers Undo and is missing here is a decision nobody is holding to it."""
    declared: set[str] = set()
    for path in sorted(SOURCE.rglob("*.py")):
        if "/tests/" in path.as_posix():
            continue
        module = path.relative_to(SOURCE).as_posix().removesuffix(".py").replace("/", ".")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            names = {
                inner.name
                for inner in node.body
                if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            reversible = any(
                isinstance(inner, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "reversible" for t in inner.targets)
                and isinstance(inner.value, ast.Constant)
                and inner.value.value is True
                for inner in node.body
            )
            if reversible and "reverse" in names:
                declared.add(f"{module}:{node.name}")

    named = set(DECISIONS) | set(NO_RECEIPT)
    assert declared - named == set(), (
        "\nThese offer Undo and nothing reads what their Undo puts back against what their"
        "\ndecision wrote. Name their accept and reverse in DECISIONS.\n  "
        + "\n  ".join(sorted(declared - named))
    )
    assert named - declared == set(), (
        "\nThese are read here and are not reversible reversers any more:\n  "
        + "\n  ".join(sorted(named - declared))
    )
    assert not set(DECISIONS) & set(NO_RECEIPT), "a reverser is read and excused at the same time"


def test_nothing_is_left_on_purpose_that_the_reverse_puts_back() -> None:
    """An excuse for a table the reverse writes after all, or for a reverser that has gone, would
    quietly cover the next gap of that name."""
    stale: list[str] = []
    for key, left in [*LEFT_ON_PURPOSE.items(), *OWED.items()]:
        decision = DECISIONS.get(key)
        if decision is None:
            stale.append(f"{key} (not a reverser here)")
            continue
        tree = Tree()
        accepted = writes(tree, decision.accept, decision.within)
        reversed_ = writes(tree, decision.reverse, decision.within)
        stale += [f"{key}: {one}" for one in left if one not in accepted or one in reversed_]

    assert not stale, "\n  ".join(["these are excused and need not be:", *stale])


@pytest.mark.parametrize("key", sorted(CARRIED_ALONG))
def test_a_reverse_reaches_what_its_undo_carries_along(key: str) -> None:
    decision = DECISIONS[key]
    missing = set(CARRIED_ALONG[key]) - reached(Tree(), decision.reverse, decision.within)
    assert not missing, (
        f"\n{key}: its Undo no longer reaches these, so what they take back stays:\n  "
        + "\n  ".join(sorted(missing))
    )


def test_every_reason_is_a_reason() -> None:
    for key, left in [*LEFT_ON_PURPOSE.items(), *OWED.items()]:
        for table, reason in left.items():
            assert len(reason.split()) >= 8, f"{key}: {table} is left without a reason"
        assert not set(LEFT_ON_PURPOSE.get(key, {})) & set(OWED.get(key, {})), (
            f"{key}: a table is both left on purpose and owed"
        )
    for key, reason in NO_RECEIPT.items():
        assert len(reason.split()) >= 8, f"{key} writes no receipt, and says nothing about why"


# --- the known positives ---------------------------------------------------------------------------


def test_the_walk_finds_the_writes_it_is_about() -> None:
    """The username join, read both ways: a walk that found nothing would pass for ever."""
    decision = DECISIONS["slices.people.queue:UsernameQueue"]
    tree = Tree()

    accepted = writes(tree, decision.accept, decision.within)
    reversed_ = writes(tree, decision.reverse, decision.within)

    assert {"usernames", "asset_people", "people_aliases", "people"} <= accepted
    assert {"usernames", "asset_people", "people_aliases", INDEX} <= reversed_


def test_a_reverse_that_loses_a_table_is_caught() -> None:
    """The username undo with its alias removal taken out, the fault this gate was written for."""
    path = SOURCE / "slices" / "people" / "service_usernames.py"
    text = path.read_text(encoding="utf-8")
    call = "await self._remove_alias_on(connection, *alias, alias_holder, actor=actor)"
    assert text.count(call) == 1, "the known positive no longer names a real line"
    tree = Tree({"slices.people.service_usernames": text.replace(call, "pass")})

    key = "slices.people.queue:UsernameQueue"
    assert gaps(tree, key, DECISIONS[key]) == {"people_aliases"}


def test_an_undo_that_drops_what_it_carries_along_is_caught() -> None:
    """The naming's Undo with the return of her questions taken out: its table is still written for
    the faces, so only the reach can see it go."""
    path = SOURCE / "slices" / "faces" / "queue.py"
    text = path.read_text(encoding="utf-8")
    call = "await self._service.return_questions(person_id, leaving=own)"
    assert text.count(call) == 1, "the known positive no longer names a real line"
    tree = Tree({"slices.faces.queue": text.replace(call, "False")})

    key = "slices.faces.queue:IdentifiedRecords"
    decision = DECISIONS[key]
    assert "return_questions" not in reached(tree, decision.reverse, decision.within)
    assert gaps(tree, key, decision) == set(), "the table rule alone cannot see it"


def test_the_forms_of_a_write_are_seen() -> None:
    """An upsert's table is its insert's, and the `SET` of its update is not a table."""
    assert _written_by(
        "INSERT INTO people_aliases (id) VALUES (?) ON CONFLICT(id) DO UPDATE SET id = excluded.id"
    ) == {"people_aliases"}
    assert _written_by("INSERT OR IGNORE INTO asset_people (asset_id) VALUES (?)") == {
        "asset_people"
    }
    assert _written_by("UPDATE usernames SET person_id = ? WHERE id = ?") == {"usernames"}
    assert _written_by("DELETE FROM asset_people WHERE person_id = ?") == {"asset_people"}
    assert _written_by("SELECT * FROM people") == set()
